"""Questarr in the stack: Parlor's setup makes its login, connects it to qBittorrent and Prowlarr's indexers and gives
it the games folder; the IGDB keys are asked for once in Settings -> Services."""
import re
from pathlib import Path
from unittest import mock

from yeerr_test_env import H, TMP, signed_in

import core
import stack
from test_stack import KEYS, Base

ROOT = Path(__file__).resolve().parent.parent


class Wiring(Base):
    def steps(self, results):
        return {step: (r["status"], r["detail"]) for (app, step), r in results.items() if app == "questarr"}

    def test_wiring_a_fresh_stack_and_a_second_run_changes_nothing(self):
        first = self.steps(self.run_all())
        self.assertEqual({k: v[0] for k, v in first.items()}, {"connections": "done", "indexers": "done"}, first)
        k = self.fake.quest
        self.assertEqual(k["user"], ("admin", "correct horse"), "your Parlor name and password are its login")
        self.assertEqual(core.setting("questarr_key"), "qsr_0")
        d = k["downloaders"][0]
        self.assertEqual((d["type"], d["url"], d["port"], d["category"], d["downloadPath"], d["enabled"]),
                         ("qbittorrent", "http://gluetun", 8080, "games", "/data/downloads/games", True))
        self.assertNotIn("password", {x for x, v in d.items() if v}, "qBittorrent takes the containers without a login")
        self.assertEqual(k["root"], "/data/games")
        self.assertTrue((TMP / "media" / "games").is_dir())
        self.assertEqual(len(k["indexers"]), len(self.fake.indexers))
        self.assertTrue(k["indexers"] and all(i["url"].startswith("http://prowlarr:9696/") for i in k["indexers"]))
        before = len(self.fake.writes())
        again = self.steps(self.run_all(password=None))  # the key is kept: no password needed again
        self.assertEqual({k: v[0] for k, v in again.items()}, {"connections": "ok", "indexers": "ok"}, again)
        self.assertEqual((len(k["downloaders"]), len(k["keys"])), (1, 1))
        wrote = [c for c in self.fake.writes()[before:] if c[1] == "questarr"]
        self.assertEqual(wrote, [])
        # an indexer added to Prowlarr later reaches Questarr on the next run
        self.fake.indexers.append({**self.fake.indexers[0], "id": 99, "name": "Later"})
        self.assertEqual(self.steps(self.run_all(password=None))["indexers"], ("done", "5 from Prowlarr"))
        self.assertIn("http://prowlarr:9696/99/api", [i["url"] for i in k["indexers"]])

    def test_wiring_an_extra_feed_of_yours_isnt_a_reason_to_sync_again(self):
        self.run_all()
        self.fake.quest["indexers"].append({"id": "x", "name": "Mine", "url": "http://prowlarr:9696/777/api"})
        before = len(self.fake.writes())
        self.assertEqual(self.steps(self.run_all(password=None))["indexers"][0], "ok")
        self.assertEqual(self.fake.writes()[before:], [])

    def test_wiring_a_qbittorrent_over_https_gets_port_443(self):
        core.set_setting("qbittorrent_url", "https://gluetun")
        self.addCleanup(lambda: core.db().execute("DELETE FROM settings WHERE key = 'qbittorrent_url'"))
        self.run_all()
        d = self.fake.quest["downloaders"][0]
        self.assertEqual((d["url"], d["port"], d["useSsl"]), ("https://gluetun", 443, True))

    def test_wiring_a_questarr_sync_that_timed_out_at_start_is_tried_again(self):
        results = [{"app": "questarr", "step": "indexers", "status": "failed", "detail": "Questarr said 502: starting"}]
        self.assertTrue(stack.worth_retrying(results))
        self.assertFalse(stack.worth_retrying([{**results[0], "app": "prowlarr"}]), "Prowlarr's public sites come and go: not a reason")

    def test_wiring_waits_for_the_password_and_leaves_yours_alone(self):
        self.assertEqual(self.steps(self.run_all(password=None))["connections"][0], "skipped")
        self.assertIsNone(self.fake.quest["user"])
        # a library folder and a download client you set yourself stay
        k = self.fake.quest
        k.update(user=("admin", "correct horse"), root="/mnt/games", downloaders=[{"id": "9", "type": "qbittorrent", "url": "http://mine", "port": 1}])
        got = self.steps(self.run_all())
        self.assertEqual(got["connections"][0], "ok", got)
        self.assertEqual((k["root"], len(k["downloaders"])), ("/mnt/games", 1))

    def test_wiring_says_when_questarr_has_another_login_or_forgot_the_key(self):
        self.fake.quest["user"] = ("someone", "else")
        got = self.steps(self.run_all())["connections"]
        self.assertEqual(got[0], "failed")
        self.assertIn("its own login", got[1])
        self.fake.quest["user"] = ("admin", "correct horse")
        core.set_setting("questarr_key", "qsr_gone")
        got = self.steps(self.run_all(password=None))["connections"]
        self.assertEqual(got[0], "failed")
        self.assertIn("password", got[1])
        self.assertFalse(core.setting("questarr_key"), "the dead key is dropped, so the next run with a password makes a new one")

    def test_wiring_leaves_a_questarr_of_your_own_alone(self):
        core.set_setting("questarr_url", "http://games.example.test:5000")
        got = self.steps(self.run_all())
        self.assertTrue(all(v[0] == "ok" and "left alone" in v[1] for v in got.values()), got)
        self.assertFalse([c for c in self.fake.calls if "games.example.test" in c[1]])

    def test_wiring_in_the_compose_is_pinned_by_digest_with_its_folders(self):
        text = (ROOT / "docker-compose.yml").read_text()
        block = re.search(r"^  questarr:.*\n(?:    .*\n)+", text, re.M)[0]
        self.assertRegex(block, r"image: ghcr\.io/doezer/questarr:v[\d.]+@sha256:[0-9a-f]{64}\b")
        for line in ("./questarr/config:/app/data", "${GAMES:-${DATA:-/data}}:/data${GAMES:+/games}", "${DOWNLOADS:-${DATA:-/data}}:/data${DOWNLOADS:+/downloads}", '"5000:5000"'):
            self.assertIn(line, block)
        self.assertIn("/questarr/", (ROOT / ".gitignore").read_text().split())
        self.assertIn("questarr", stack.CONFIG_DIRS, "setup makes its folder, owned by the stack's user")


class Igdb(Base):
    ID, SECRET = "a" * 30, "b" * 30  # pragma: allowlist secret (made up)

    def setUp(self):
        super().setUp()
        with mock.patch.object(stack, "start"):  # the very first account would start a setup run of its own, as "admin"
            self.me = signed_in("questadmin", admin=True)

    def row(self):
        return next(a for a in self.me.get("/api/stack").json()["apps"] if a["app"] == "questarr")

    def give(self, **over):
        return self.me.post("/api/stack/questarr/igdb", json={"client_id": self.ID, "client_secret": self.SECRET, "password": "correct horse", **over}, headers=H)

    def test_igdb_keys_are_asked_for_once_and_until_then_it_needs_them(self):
        self.assertEqual(self.give().status_code, 409, "before setup there's no Questarr login to give them to")
        stack.run("questadmin", "correct horse")
        row = self.row()
        self.assertEqual((row["up"], row["key"], row["needs"]), (True, True, "IGDB keys"))
        self.assertEqual(self.give(password="wrong").status_code, 403)
        r = self.give(client_id="short")
        self.assertEqual(r.status_code, 400)
        self.assertIn("doesn't look valid", r.json()["detail"])
        self.assertIsNone(self.fake.quest["igdb"])
        self.assertEqual(self.give().status_code, 200)
        self.assertEqual(self.fake.quest["igdb"], (self.ID, self.SECRET))
        self.assertIsNone(self.row()["needs"])
        stored = " ".join(str(r["value"]) for r in core.rows("SELECT value FROM settings"))
        self.assertNotIn(self.SECRET, stored, "Questarr keeps them; Parlor keeps no copy")

    def test_igdb_keys_given_in_questarr_itself_are_noticed_at_setup(self):
        self.fake.quest.update(user=("questadmin", "correct horse"), igdb=("x", "y"))
        stack.run("questadmin", "correct horse")
        self.assertIsNone(self.row()["needs"])
        # and later too: taken away there, a run with the password sees it; a run without one changes nothing
        self.fake.quest["igdb"] = None
        stack.run("questadmin", None)
        self.assertIsNone(self.row()["needs"])
        stack.run("questadmin", "correct horse")
        self.assertEqual(self.row()["needs"], "IGDB keys")

    def test_igdb_from_another_admin_says_whose_login_questarr_has(self):
        stack.run("questadmin", "correct horse")
        import auth

        auth.create_user("secondadmin", "another horse", True)
        other = signed_in("secondadmin", password="another horse")
        r = other.post("/api/stack/questarr/igdb", json={"client_id": self.ID, "client_secret": self.SECRET, "password": "another horse"}, headers=H)
        self.assertEqual(r.status_code, 409)
        self.assertIn("questadmin", r.json()["detail"])
        self.assertIsNone(self.fake.quest["igdb"])
        # a setup run by that other admin doesn't fail on Questarr's login either: it's wired already
        got = {r["step"]: r["status"] for r in stack.run("secondadmin", "another horse") if r["app"] == "questarr"}
        self.assertEqual(got, {"connections": "ok", "indexers": "ok"})

    def test_igdb_is_for_admins(self):
        member = signed_in("questmember")
        self.assertEqual(member.post("/api/stack/questarr/igdb", json={"client_id": self.ID, "client_secret": self.SECRET, "password": "x"}, headers=H).status_code, 403)
