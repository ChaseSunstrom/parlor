"""Private items: the file format (chunked AES-GCM, tamper-evident), sealed boxes, the person's key lifecycle
(sign-in, restart, password change, an admin's reset, the recovery code) and the item routes (make private, list,
play with ranges, share, unshare with a new key, back to the library); nobody else, admins included, can open one."""
import hashlib
import io
import json
import os
import time
import unittest

import yeerr_test_env as env
from yeerr_test_env import H, signed_in

import core
import private
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

KEY, PREFIX = bytes(range(32)), b"\x01\x02\x03\x04\x05\x06\x07"


def sealed(data, key=KEY):
    out = io.BytesIO()
    private.encrypt(io.BytesIO(data), out, key, PREFIX)
    return out.getvalue()


class Format(unittest.TestCase):
    def setUp(self):
        self.path = env.TMP / "fmt.bin"

    def write(self, blob):
        self.path.write_bytes(blob)
        return self.path

    def test_known_answer(self):
        """The layout stays readable by later versions: this exact output for this key, prefix and plaintext."""
        blob = sealed(b"parlor" * 20000)
        self.assertEqual(blob[:private.HEADER], private.MAGIC + b"\x01\x00\x01\x00\x00" + PREFIX)
        self.assertEqual(len(blob), private.HEADER + 120000 + 2 * private.TAG)
        self.assertEqual(hashlib.sha256(blob).hexdigest(), "519453827ea179d5b3aff1749ec429d26d203bf75f0b826b953eb55d3b82d438")
        self.assertEqual(sealed(b"x"), sealed(b"x"), "deterministic given the prefix")

    def test_round_trip_and_ranges(self):
        for data in (b"", b"a", os.urandom(private.CHUNK), os.urandom(private.CHUNK * 3 + 123)):
            p = self.write(sealed(data))
            self.assertEqual(private.size_of(p), len(data))
            self.assertEqual(b"".join(private.read_range(p, KEY)), data)
            if len(data) > private.CHUNK:
                for start, end in ((0, 10), (private.CHUNK - 5, private.CHUNK + 5), (len(data) - 7, len(data) - 1), (1000, 2 * private.CHUNK + 50)):
                    self.assertEqual(b"".join(private.read_range(p, KEY, start, end)), data[start:end + 1], (start, end))

    def test_tampering_is_caught(self):
        data = os.urandom(private.CHUNK * 3)
        blob = sealed(data)
        step = private.CHUNK + private.TAG
        cut = self.write(blob[:private.HEADER + 2 * step])  # the last chunk dropped
        with self.assertRaises(private.Broken):
            b"".join(private.read_range(cut, KEY))
        swapped = blob[:private.HEADER] + blob[private.HEADER + step:private.HEADER + 2 * step] + blob[private.HEADER:private.HEADER + step] + blob[private.HEADER + 2 * step:]
        with self.assertRaises(private.Broken):
            b"".join(private.read_range(self.write(swapped), KEY))
        other = io.BytesIO()
        private.encrypt(io.BytesIO(data), other, KEY, b"\x09" * 7)
        with self.assertRaises(private.Broken):  # another file's header
            b"".join(private.read_range(self.write(other.getvalue()[:private.HEADER] + blob[private.HEADER:]), KEY))
        with self.assertRaises(private.Broken):
            b"".join(private.read_range(self.write(blob), bytes(32)))

    def test_sealed_boxes(self):
        me, them = X25519PrivateKey.generate(), X25519PrivateKey.generate()
        box = private.seal_to(private._pub(me), b"file key")
        self.assertEqual(private.open_box(me, box), b"file key")
        with self.assertRaises(private.Broken):
            private.open_box(them, box)


def video(name="Private one", body=b"video bytes " * 20000, owner="priv-owner"):
    """A downloaded video in the library, as yt.run_download leaves it."""
    vid = hashlib.md5(name.encode()).hexdigest()[:11].replace("a", "A")
    rel = f"Channel/{name} [{vid}].mp4"
    path = core.VIDEOS / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    path.with_suffix(".jpg").write_bytes(b"thumb")
    item = json.dumps({"id": vid, "title": name, "channel": "Channel", "duration": 61})
    core.db().execute("INSERT OR REPLACE INTO videos(id, item, path, size, quality, user_id, added) VALUES (?, ?, ?, ?, ?, (SELECT id FROM users WHERE name = ?), ?)",
                      (vid, item, rel, len(body), "720", owner, time.time()))
    return vid, path, body


class Items(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")
        self.owner = signed_in("priv-owner")
        self.friend = signed_in("priv-friend")
        for c in (self.owner, self.friend):
            if not c.get("/api/private/state").json()["on"]:
                self.code = c.post("/api/private/enable", json={"password": "correct horse"}, headers=H).json()["recovery_code"]

    def make(self, name):
        vid, path, body = video(name)
        return self.make_vid(vid), path, body

    def make_vid(self, vid):
        r = self.owner.post(f"/api/yt/library/{vid}/private", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def test_a_private_video_only_its_people_can_open(self):
        iid, path, body = self.make("Only mine")
        self.assertFalse(path.exists(), "the plain file is gone")
        self.assertFalse(path.with_suffix(".jpg").exists())
        enc = private.folder() / iid
        self.assertNotIn(b"video bytes", enc.read_bytes())
        self.assertTrue((private.folder() / ".ignore").exists(), "Jellyfin skips the folder")
        self.assertIsNone(core.one("SELECT 1 AS x FROM videos WHERE path LIKE '%Only mine%'"), "gone from the shared library")
        mine = self.owner.get("/api/private").json()["items"]
        self.assertEqual([(i["title"], i["mine"]) for i in mine if i["id"] == iid], [("Only mine", True)])
        r = self.owner.get(f"/api/private/{iid}/file", headers={"Range": "bytes=100-199"})
        self.assertEqual((r.status_code, r.content), (206, body[100:200]))
        self.assertEqual(self.owner.get(f"/api/private/{iid}/file").content, body)
        for other in (self.friend, self.admin):
            self.assertEqual(other.get(f"/api/private/{iid}/file").status_code, 404, "not even an admin")
            self.assertNotIn(iid, [i["id"] for i in other.get("/api/private").json()["items"]])

    def test_share_then_unshare_with_a_new_key(self):
        iid, _, body = self.make("Shared one")
        self.assertEqual(self.owner.post(f"/api/private/{iid}/share", json={"name": "priv-friend"}, headers=H).status_code, 200)
        self.assertEqual(self.friend.get(f"/api/private/{iid}/file").content, body)
        self.assertEqual(self.friend.post(f"/api/private/{iid}/share", json={"name": "admin"}, headers=H).status_code, 403, "only the owner shares")
        old_box = core.one("SELECT wrapped FROM private_shares WHERE item_id = ? AND user_id = (SELECT id FROM users WHERE name = 'priv-friend')", iid)["wrapped"]
        before = (private.folder() / iid).read_bytes()
        self.assertEqual(self.owner.delete(f"/api/private/{iid}/share/priv-friend", headers=H).status_code, 200)
        self.assertEqual(self.friend.get(f"/api/private/{iid}/file").status_code, 404)
        now = private.folder() / core.one("SELECT file FROM private_items WHERE id = ?", iid)["file"]
        self.assertNotEqual(now.read_bytes()[private.HEADER:], before[private.HEADER:], "re-encrypted")
        self.assertFalse((private.folder() / iid).exists(), "the old copy is gone")
        friend_priv = next(k for _, (u, k) in private._held.items() if u == core.one("SELECT id FROM users WHERE name = 'priv-friend'")["id"])
        old_key = private.open_box(friend_priv, old_box)
        with self.assertRaises(private.Broken):  # the key they had opens nothing now
            b"".join(private.read_range(now, old_key))
        self.assertEqual(self.owner.get(f"/api/private/{iid}/file").content, body)

    def test_what_others_can_learn(self):
        """A sharee doesn't see who else has it; names can't be probed; the shared download list forgets it."""
        import yt
        vid, _, _ = video("Quiet one")
        with yt._jobs_lock:
            yt.jobs.insert(0, {"job": "j-quiet", "id": vid, "kind": "video", "status": "done", "title": "Quiet one"})
        iid = self.make_vid(vid)
        self.assertNotIn("j-quiet", [j["job"] for j in self.friend.get("/api/yt/downloads").json()])
        self.assertEqual(self.owner.post(f"/api/yt/library/{vid}/private", headers=H).status_code, 404, "a second click finds nothing")
        nobody = self.owner.post(f"/api/private/{iid}/share", json={"name": "no-such-person"}, headers=H)
        signed_in("priv-keyless")
        keyless = self.owner.post(f"/api/private/{iid}/share", json={"name": "priv-keyless"}, headers=H)
        self.assertEqual((nobody.status_code, keyless.status_code), (404, 404))
        self.assertEqual(nobody.json()["detail"].replace("no-such-person", "X"), keyless.json()["detail"].replace("priv-keyless", "X"))
        self.assertEqual(self.owner.delete(f"/api/private/{iid}/share/priv-friend", headers=H).status_code, 404, "never shared: no re-key")
        self.owner.post(f"/api/private/{iid}/share", json={"name": "priv-friend"}, headers=H)
        seen = next(i for i in self.friend.get("/api/private").json()["items"] if i["id"] == iid)
        self.assertEqual((seen["mine"], seen["shared_with"]), (False, []))
        self.assertEqual(next(i for i in self.owner.get("/api/private").json()["items"] if i["id"] == iid)["shared_with"], ["priv-friend"])

    def test_an_empty_file(self):
        vid, _, _ = video("Empty one", body=b"")
        iid = self.make_vid(vid)
        r = self.owner.get(f"/api/private/{iid}/file", headers={"Range": "bytes=0-"})
        self.assertEqual((r.status_code, r.content), (200, b""))

    def test_unshare_cut_off_midway_loses_nothing(self):
        """A crash while re-keying (here: sealing the new key fails) leaves the old file and keys as they were."""
        iid, _, body = self.make("Crash safe")
        self.owner.post(f"/api/private/{iid}/share", json={"name": "priv-friend"}, headers=H)
        real, private.seal_to = private.seal_to, lambda *a: (_ for _ in ()).throw(RuntimeError("power cut"))
        try:
            with self.assertRaises(RuntimeError):
                self.owner.delete(f"/api/private/{iid}/share/priv-friend", headers=H)
        finally:
            private.seal_to = real
        self.assertEqual(self.owner.get(f"/api/private/{iid}/file").content, body)
        self.assertEqual(self.friend.get(f"/api/private/{iid}/file").content, body, "still shared: nothing changed")
        private.sweep()  # at start: the half-written new copy goes
        kept = {r["file"] for r in core.rows("SELECT file FROM private_items")}
        self.assertEqual({f.name for f in private.folder().iterdir()} - {".ignore"}, kept)

    def test_deleting_an_account_deletes_its_private_files(self):
        gone = signed_in("priv-gone")
        gone.post("/api/private/enable", json={"password": "correct horse"}, headers=H)
        vid, _, _ = video("Theirs", owner="priv-gone")
        iid = gone.post(f"/api/yt/library/{vid}/private", headers=H).json()["id"]
        f = private.folder() / core.one("SELECT file FROM private_items WHERE id = ?", iid)["file"]
        self.assertTrue(f.exists())
        uid = gone.get("/api/me").json()["id"]
        self.assertEqual(self.admin.delete(f"/api/users/{uid}", headers=H).status_code, 200)
        self.assertFalse(f.exists(), "nobody could ever open it again: it's deleted, not left behind")

    def test_back_to_the_library(self):
        iid, path, body = self.make("Changed my mind")
        r = self.owner.post(f"/api/private/{iid}/unprivate", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(path.read_bytes(), body)
        self.assertFalse((private.folder() / iid).exists())
        self.assertIsNotNone(core.one("SELECT 1 AS x FROM videos WHERE path LIKE '%Changed my mind%'"))


class Keys(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")
        core.db().execute("UPDATE users SET pw = ? WHERE name = 'priv-keys'", (core.hash_password("first password"),))  # each test starts here
        self.person = signed_in("priv-keys", "first password")
        self.pid = self.person.get("/api/me").json()["id"]
        core.db().execute("DELETE FROM person_keys WHERE user_id = ?", (self.pid,))

    def test_lifecycle(self):
        self.assertEqual(self.person.post("/api/private/enable", json={"password": "wrong"}, headers=H).status_code, 403)
        code = self.person.post("/api/private/enable", json={"password": "first password"}, headers=H).json()["recovery_code"]
        self.assertEqual(self.person.get("/api/private/state").json(), {"on": True, "open": True})
        private._held.clear()  # a restart
        self.assertEqual(self.person.get("/api/private").status_code, 423)
        self.assertEqual(self.person.post("/api/private/unlock", json={"password": "first password"}, headers=H).json()["open"], True)
        # a password change re-seals it
        self.person.post("/api/me/password", json={"old": "first password", "new": "second password"}, headers=H)
        private._held.clear()
        again = env.client()
        again.post("/api/auth/login", json={"name": "priv-keys", "password": "second password"}, headers=H)
        self.assertTrue(again.get("/api/private/state").json()["open"], "signing in opens it")
        # an admin's reset can't open it: the recovery code can
        self.admin.patch(f"/api/users/{self.pid}", json={"password": "third password"}, headers=H)
        private._held.clear()
        third = env.client()
        third.post("/api/auth/login", json={"name": "priv-keys", "password": "third password"}, headers=H)
        self.assertFalse(third.get("/api/private/state").json()["open"])
        self.assertEqual(third.post("/api/private/unlock", json={"password": "third password"}, headers=H).status_code, 409)
        self.assertEqual(third.post("/api/private/recover", json={"code": "0000-0000", "password": "third password"}, headers=H).status_code, 403)
        r = third.post("/api/private/recover", json={"code": code, "password": "third password"}, headers=H)
        self.assertEqual((r.status_code, r.json()["open"]), (200, True))
        private._held.clear()
        fourth = env.client()
        fourth.post("/api/auth/login", json={"name": "priv-keys", "password": "third password"}, headers=H)
        self.assertTrue(fourth.get("/api/private/state").json()["open"], "sealed by the new password from now on")

    def test_signing_out_drops_the_key(self):
        self.person.post("/api/private/enable", json={"password": "first password"}, headers=H)
        self.assertTrue(self.person.get("/api/private/state").json()["open"])
        mine = core._digest(self.person.cookies.get(core.COOKIE))
        self.assertIn(mine, private._held)
        self.person.post("/api/auth/logout", headers=H)
        self.assertNotIn(mine, private._held, "this session's key is gone from memory")

    def test_signing_out_everywhere_drops_every_key(self):
        self.person.post("/api/private/enable", json={"password": "first password"}, headers=H)
        other = signed_in("priv-keys", "first password")
        held = lambda: [u for u, _ in private._held.values() if u == self.pid]  # noqa: E731
        self.assertGreaterEqual(len(held()), 2)
        other.post("/api/auth/logout-everywhere", headers=H)
        self.assertEqual(held(), [])


if __name__ == "__main__":
    unittest.main()
