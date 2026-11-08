"""Encryption at rest: Parlor's database (SQLCipher) and the files it keeps beside it (YouTube cookies,
the download list) are unreadable without the key, kept in its own folder; an old plain database is
converted once, safely; and the data folder is Parlor's alone."""
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yeerr_test_env as env
from yeerr_test_env import H, signed_in

import core
import security
import stack
import vault
import yt

PLAIN = b"SQLite format 3\x00"


class Place(unittest.TestCase):
    """A fresh data and key folder for each test, and core pointed at it."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="vault-"))
        self.data, self.keys = self.root / "data", self.root / "key"
        self.data.mkdir()
        for p in (mock.patch.object(core, "DATA", self.data), mock.patch.object(vault, "KEY_DIR", self.keys),
                  mock.patch.dict(os.environ, {"ENCRYPT": "1"})):
            p.start()
            self.addCleanup(p.stop)
        vault.forget()
        self.addCleanup(vault.forget)
        self.addCleanup(lambda: core._local.__dict__.clear())

    def fresh(self):
        """What the next start sees: no open connection, no remembered key."""
        core._local.__dict__.clear()
        core._ready.clear()
        vault.forget()
        return core.db()


class Database(Place):
    def test_a_new_database_is_encrypted_and_needs_its_key(self):
        core.db().execute("INSERT INTO settings(key, value) VALUES ('a', 'secret value')")
        db = self.data / "yeerr.db"
        self.assertNotEqual(db.read_bytes()[:16], PLAIN)
        self.assertNotIn(b"secret value", db.read_bytes())
        self.assertEqual(self.fresh().execute("SELECT value FROM settings WHERE key = 'a'").fetchone()[0], "secret value")
        self.assertEqual(stat.S_IMODE((self.keys / "db.key").stat().st_mode), 0o600)
        with self.assertRaises(sqlite3.DatabaseError):  # plain sqlite can't read it
            sqlite3.connect(db).execute("SELECT * FROM settings").fetchall()
        (self.keys / "db.key").write_text("ab" * 32)
        with self.assertRaises(vault.WrongKey):
            self.fresh()
        (self.keys / "db.key").unlink()
        with self.assertRaisesRegex(vault.WrongKey, "isn't in"):
            self.fresh()  # a lost key is said plainly, and no new key is made over its place
        self.assertFalse((self.keys / "db.key").exists())

    def test_an_old_plain_database_is_converted_once_with_a_backup(self):
        db = self.data / "yeerr.db"
        with mock.patch.dict(os.environ, {"ENCRYPT": "0"}):
            self.fresh().execute("INSERT INTO settings(key, value) VALUES ('kept', 'yes')")
            core._local.db.close()
        self.assertEqual(db.read_bytes()[:16], PLAIN)
        conn = self.fresh()
        self.assertEqual(conn.execute("SELECT value FROM settings WHERE key = 'kept'").fetchone()[0], "yes")
        self.assertNotEqual(db.read_bytes()[:16], PLAIN)
        backup = self.data / "yeerr.db.plain-backup"
        self.assertEqual(backup.read_bytes()[:16], PLAIN, "the plain copy stays until the next good start")
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.fresh()
        self.assertFalse(backup.exists(), "a good start with the encrypted one removes it")

    def test_an_interrupted_conversion_leaves_the_original(self):
        db = self.data / "yeerr.db"
        with mock.patch.dict(os.environ, {"ENCRYPT": "0"}):
            self.fresh().execute("INSERT INTO settings(key, value) VALUES ('kept', 'yes')")
            core._local.db.close()
        with mock.patch.object(vault, "_check", side_effect=RuntimeError("power cut")):
            with self.assertRaises(RuntimeError):
                self.fresh()
        self.assertEqual(db.read_bytes()[:16], PLAIN, "untouched")
        self.assertFalse((self.data / "yeerr.db.encrypting").exists())
        self.assertEqual(self.fresh().execute("SELECT value FROM settings WHERE key = 'kept'").fetchone()[0], "yes")

    def test_encrypt_0_keeps_it_plain(self):
        with mock.patch.dict(os.environ, {"ENCRYPT": "0"}):
            self.fresh().execute("INSERT INTO settings(key, value) VALUES ('a', 'b')")
        self.assertEqual((self.data / "yeerr.db").read_bytes()[:16], PLAIN)
        self.assertFalse((self.keys / "db.key").exists())

    def test_encrypt_0_later_still_opens_an_encrypted_database(self):
        self.fresh().execute("INSERT INTO settings(key, value) VALUES ('a', 'b')")
        with mock.patch.dict(os.environ, {"ENCRYPT": "0"}):
            self.assertEqual(self.fresh().execute("SELECT value FROM settings WHERE key = 'a'").fetchone()[0], "b")


class Files(Place):
    def test_files_beside_it_are_sealed_and_old_plain_ones_still_read(self):
        p = self.data / "x.json"
        vault.write(p, b'{"a": 1}')
        self.assertNotIn(b'"a"', p.read_bytes())
        self.assertEqual(vault.read(p), b'{"a": 1}')
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        p.write_bytes(b"plain from before")
        self.assertEqual(vault.read(p), b"plain from before")
        vault.write(p, b"again")
        raw = bytearray(p.read_bytes())
        raw[-1] ^= 1
        p.write_bytes(bytes(raw))
        with self.assertRaises(vault.WrongKey):
            vault.read(p)

    def test_youtube_cookies_and_the_download_list(self):
        c = signed_in("cookie-owner")
        cookie = ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecretcookie"
        self.assertEqual(c.put("/api/me/youtube-cookies", json={"text": cookie}, headers=H).status_code, 200)
        uid = c.get("/api/me").json()["id"]
        self.assertNotIn(b"secretcookie", yt.cookie_path(uid).read_bytes())
        opts = yt._opts({}, uid)
        try:
            self.assertIn("secretcookie", Path(opts["cookiefile"]).read_text(), "yt-dlp gets a plain copy for the call")
            self.assertEqual(stat.S_IMODE(Path(opts["cookiefile"]).stat().st_mode), 0o600)
        finally:
            Path(opts["cookiefile"]).unlink()
        with yt._jobs_lock:
            yt.jobs[:] = [{"job": "j1", "id": "kkkkkkkkkkk", "title": "Private title", "status": "done"}]
        yt.save_jobs()
        self.assertNotIn(b"Private title", yt.STATE().read_bytes())
        yt.jobs.clear()
        yt.restore_jobs()
        self.assertEqual(yt.jobs[0]["title"], "Private title")
        yt.jobs.clear()


class AtRest(Place):
    def test_the_data_folder_and_its_files_are_parlors_alone(self):
        (self.data / "cookies").mkdir()
        (self.data / "cookies" / "1.txt").write_text("x")
        os.chmod(self.data, 0o755)
        core.db()
        vault.lock_down(self.data)
        self.assertEqual(stat.S_IMODE(self.data.stat().st_mode), 0o700)
        for name in ("yeerr.db", "cookies/1.txt"):
            self.assertEqual(stat.S_IMODE((self.data / name).stat().st_mode), 0o600, name)
        self.assertEqual(stat.S_IMODE((self.data / "cookies").stat().st_mode), 0o700)

    def test_a_folder_it_cant_change_is_left_as_it_is(self):
        with mock.patch.object(vault.os, "chmod", side_effect=PermissionError("read-only")):
            vault.lock_down(self.data)  # no exception

    def test_the_stacks_seerr_key_is_read_from_seerr_not_copied(self):
        stack_dir = self.root / "stack"
        (stack_dir / "seerr").mkdir(parents=True)
        (stack_dir / "seerr" / "settings.json").write_text(json.dumps({"main": {"apiKey": "disk-key"}}))
        core.set_setting("seerr_key", "disk-key")  # what an earlier version stored
        with mock.patch.object(stack, "STACK", stack_dir), mock.patch.object(stack, "ours", lambda app: True):
            stack.read_keys_from_disk()
            self.assertEqual(core.setting("seerr_key"), "disk-key")
            self.assertIsNone(core.one("SELECT value FROM settings WHERE key = 'seerr_key'"), "the copy is gone")
            core.READERS.clear()  # a restart: it still knows to read it there
            stack.read_keys_from_disk()
            self.assertEqual(core.setting("seerr_key"), "disk-key")
        core.READERS.pop("seerr_key", None)
        core.db().execute("DELETE FROM settings WHERE key = 'seerr_key_on_disk'")
        # a Seerr whose settings.json holds a key it doesn't use (newer Seerrs keep it elsewhere): the working one stays
        core.set_setting("seerr_key", "working-key")
        with mock.patch.object(stack, "STACK", stack_dir), mock.patch.object(stack, "ours", lambda app: True):
            stack.read_keys_from_disk()
            self.assertEqual(core.setting("seerr_key"), "working-key")
            self.assertNotIn("seerr_key", core.READERS)
        core.set_setting("seerr_key", "elsewhere-key")  # a Seerr elsewhere: only Parlor has its key
        with mock.patch.object(stack, "STACK", stack_dir), mock.patch.object(stack, "ours", lambda app: False):
            stack.read_keys_from_disk()
            self.assertEqual(core.setting("seerr_key"), "elsewhere-key")
        core.READERS.pop("seerr_key", None)


class LockAtBoot(Place):
    """With a passphrase, the key is kept sealed by it: Parlor starts locked and opens only for it."""

    def setUp(self):
        super().setUp()
        self.addCleanup(security._tries.clear)
        self.admin = signed_in("admin")
        core.db()

    def test_lock_unlock_and_turn_it_off(self):
        wrong = self.admin.put("/api/vault/passphrase", json={"password": "nope", "passphrase": "a long passphrase"}, headers=H)
        self.assertEqual(wrong.status_code, 403, "your own password first")
        short = self.admin.put("/api/vault/passphrase", json={"password": "correct horse", "passphrase": "short"}, headers=H)
        self.assertEqual(short.status_code, 422)
        r = self.admin.put("/api/vault/passphrase", json={"password": "correct horse", "passphrase": "a long passphrase"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        raw = vault.key().hex()
        self.assertNotIn(raw, (self.keys / "db.key").read_text(), "the key isn't on disk in the clear")
        vault.forget()  # a restart
        core._local.__dict__.clear()
        self.assertTrue(vault.locked())
        self.assertEqual(self.admin.get("/api/me").status_code, 423, "nothing answers while locked")
        self.assertEqual(self.admin.get("/api/vault/state").json()["locked"], True)
        started = []
        with mock.patch.object(security, "on_unlock", lambda: started.append(1)):
            self.assertEqual(self.admin.post("/api/vault/unlock", json={"passphrase": "wrong one"}, headers=H).status_code, 403)
            self.assertEqual(self.admin.post("/api/vault/unlock", json={"passphrase": "a long passphrase"}, headers=H).status_code, 200)
        self.assertEqual(started, [1], "the start that waited for the key goes on")
        self.assertEqual(vault.key().hex(), raw)
        self.assertEqual(self.admin.get("/api/me").status_code, 200)
        off = self.admin.put("/api/vault/passphrase", json={"password": "correct horse", "passphrase": ""}, headers=H)
        self.assertEqual(off.status_code, 200)
        self.assertEqual((self.keys / "db.key").read_text().strip(), raw, "back to a plain key file")

    def test_one_unlock_at_a_time(self):
        self.admin.put("/api/vault/passphrase", json={"password": "correct horse", "passphrase": "a long passphrase"}, headers=H)
        vault.forget()
        with security._trying:  # another attempt is running
            r = self.admin.post("/api/vault/unlock", json={"passphrase": "a long passphrase"}, headers=H)
        self.assertEqual(r.status_code, 429, "no queue of attempts, each costing 128 MB")
        with mock.patch.object(security, "on_unlock", lambda: None):
            self.assertEqual(self.admin.post("/api/vault/unlock", json={"passphrase": "a long passphrase"}, headers=H).status_code, 200)

    def test_lost_key_is_never_replaced_once_the_database_is_open(self):
        (self.keys / "db.key").unlink()
        vault._key = b""
        with self.assertRaises(vault.WrongKey):
            vault.key()
        self.assertFalse((self.keys / "db.key").exists())

    def test_unlock_attempts_are_slowed(self):
        self.admin.put("/api/vault/passphrase", json={"password": "correct horse", "passphrase": "a long passphrase"}, headers=H)
        vault.forget()
        codes = [self.admin.post("/api/vault/unlock", json={"passphrase": f"guess {i}"}, headers=H).status_code for i in range(7)]
        self.assertEqual(codes[:5], [403] * 5)
        self.assertEqual(codes[5:], [429, 429], "after five wrong ones in a minute, it waits")
        security._tries.clear()
        with mock.patch.object(security, "on_unlock", lambda: None):
            self.admin.post("/api/vault/unlock", json={"passphrase": "a long passphrase"}, headers=H)

    def test_key_backup_needs_an_admin_and_their_password(self):
        member = signed_in("plain-member")
        self.assertEqual(member.post("/api/vault/key", json={"password": "correct horse"}, headers=H).status_code, 403)
        self.assertEqual(self.admin.post("/api/vault/key", json={"password": "nope"}, headers=H).status_code, 403)
        r = self.admin.post("/api/vault/key", json={"password": "correct horse"}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.json()["key"], vault.key().hex())
        state = self.admin.get("/api/vault/state").json()
        self.assertEqual((state["encrypted"], state["key_apart"], state["lock_at_boot"]), (True, True, False))


if __name__ == "__main__":
    unittest.main()
