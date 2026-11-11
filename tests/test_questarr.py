"""Questarr in the stack: Parlor's setup makes its login, connects it to qBittorrent and Prowlarr's indexers and gives
it the games folder. It needs no IGDB keys: the compose sends its game lookups to Parlor (test_gamelookup.py)."""
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
        first = self.steps(self.run_all(password=None))  # nothing typed: Parlor makes Questarr's login itself
        self.assertEqual({k: v[0] for k, v in first.items()}, {"connections": "done", "indexers": "done"}, first)
        k = self.fake.quest
        self.assertEqual(k["user"], ("parlor", core.setting("questarr_password")), "a login of Parlor's own")
        self.assertRegex(k["user"][1], r"^(?=.*[A-Za-z])(?=.*\d)\S{24,}$", "long, random, and what Questarr's password rule takes")
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

    def test_wiring_leaves_a_folder_and_a_download_client_of_yours_alone(self):
        k = self.fake.quest
        k.update(root="/mnt/games", downloaders=[{"id": "9", "type": "qbittorrent", "url": "http://mine", "port": 1}])
        got = self.steps(self.run_all(password=None))
        self.assertEqual(got["connections"][0], "ok", got)
        self.assertEqual((k["root"], len(k["downloaders"])), ("/mnt/games", 1))

    def test_wiring_an_older_questarr_whose_login_is_yours_takes_your_password_once(self):
        self.fake.quest["user"] = ("admin", "correct horse")  # as Parlor's setup made it before it had a login of its own
        got = self.steps(self.run_all(password=None))["connections"]
        self.assertEqual(got[0], "skipped")
        self.assertIn("your password once", got[1])
        self.assertEqual(self.steps(self.run_all())["connections"][0], "done")
        self.assertEqual(core.setting("questarr_key"), "qsr_0")
        self.assertEqual(self.fake.quest["user"], ("admin", "correct horse"), "your login stays as it is")
        self.assertFalse(core.setting("questarr_password"), "and Parlor has no login of its own to show")

    def test_wiring_says_when_questarr_has_another_login(self):
        self.fake.quest["user"] = ("someone", "else")
        got = self.steps(self.run_all())["connections"]
        self.assertEqual(got[0], "failed")
        self.assertIn("its own login", got[1])

    def test_wiring_a_key_questarr_forgot_is_made_again_with_nothing_typed(self):
        self.run_all(password=None)
        login = self.fake.quest["user"]
        core.set_setting("questarr_key", "qsr_gone")
        got = self.steps(self.run_all(password=None))["connections"]
        self.assertEqual(got[0], "failed")
        self.assertFalse(core.setting("questarr_key"), "the dead key is dropped")
        self.assertEqual(self.steps(self.run_all(password=None))["connections"][0], "ok")
        self.assertEqual((core.setting("questarr_key"), self.fake.quest["user"]), ("qsr_1", login), "a new key, the same login")
        # Questarr's data started over: the same login is made there again
        self.fake.quest.update(user=None, keys=[], downloaders=[], root="/data")
        self.run_all(password=None)
        self.assertEqual(self.steps(self.run_all(password=None))["connections"][0], "done")
        self.assertEqual(self.fake.quest["user"], login)

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

    def test_wiring_in_the_compose_sends_its_game_lookups_to_parlor(self):
        text = (ROOT / "docker-compose.yml").read_text()
        block = re.search(r"^  questarr:.*\n(?:    .*\n)+", text, re.M)[0]
        for line in ("<<: *env", "IGDB_CLIENT_ID: parlor", "IGDB_CLIENT_SECRET: parlor", "NODE_OPTIONS: --require /app/data/parlor-lookup.cjs", "condition: service_completed_successfully"):
            self.assertIn(line, block)  # (setup first: it puts that file and the key into ./questarr/config)
        parlor = re.search(r"^  parlor:.*\n(?:(?:    .*)?\n)+?(?=  \w+:)", text, re.M)[0]
        self.assertIn("./questarr/config:/stack/questarr:ro", parlor, "Parlor reads the key Questarr shows, and can't change Questarr's files")
        lookup = (ROOT / "app" / "questarr-lookup.cjs").read_text()
        self.assertIn('"http://parlor:8800/lookup/games"', lookup)
        self.assertIn('"/app/data/parlor-lookup.key"', lookup)


class NoKeys(Base):
    def setUp(self):
        super().setUp()
        with mock.patch.object(stack, "start"):  # the very first account would start a setup run of its own, as "admin"
            self.me = signed_in("questadmin", admin=True)

    def test_nokeys_the_row_asks_for_nothing_and_the_old_route_is_gone(self):
        stack.run("questadmin", "correct horse")
        row = next(a for a in self.me.get("/api/stack").json()["apps"] if a["app"] == "questarr")
        self.assertEqual((row["up"], row["key"]), (True, True))
        self.assertNotIn("needs", row)
        r = self.me.post("/api/stack/questarr/igdb", json={"client_id": "a" * 30, "client_secret": "b" * 30, "password": "correct horse"}, headers=H)
        self.assertIn(r.status_code, (404, 405))
        self.assertFalse([c for c in self.fake.calls if "igdb" in str(c).lower() or c[1].endswith("/api/config")], "and Questarr is never asked about IGDB")

    def test_nokeys_another_admins_setup_run_doesnt_need_questarrs_login(self):
        stack.run("questadmin", "correct horse")
        import auth

        auth.create_user("secondadmin", "another horse", True)
        got = {r["step"]: r["status"] for r in stack.run("secondadmin", "another horse") if r["app"] == "questarr"}
        self.assertEqual(got, {"connections": "ok", "indexers": "ok"}, "it's wired already, under Parlor's key")

    def test_nokeys_an_admin_can_read_the_login_parlor_made_and_nobody_else(self):
        self.assertEqual(self.me.get("/api/stack/questarr/login").status_code, 404, "before setup there is none")
        stack.run("questadmin", None)
        row = next(a for a in self.me.get("/api/stack").json()["apps"] if a["app"] == "questarr")
        self.assertIs(row["login"], True)
        r = self.me.get("/api/stack/questarr/login")
        self.assertEqual(r.json(), {"name": "parlor", "password": core.setting("questarr_password")})
        self.assertEqual(self.fake.quest["user"], ("parlor", r.json()["password"]))
        self.assertIn("no-store", r.headers.get("cache-control", ""))
        self.assertEqual(signed_in("questmember").get("/api/stack/questarr/login").status_code, 403)
        # a login Questarr never took (someone made its first account while setup ran) isn't shown as its login
        core.db().execute("DELETE FROM settings WHERE key = 'questarr_key'")
        self.fake.quest["user"] = ("someone", "else")
        stack.run("questadmin", None)
        self.assertIs(next(a for a in self.me.get("/api/stack").json()["apps"] if a["app"] == "questarr")["login"], False)
        self.assertEqual(self.me.get("/api/stack/questarr/login").status_code, 404)
        from yeerr_test_env import client

        self.assertEqual(client().get("/api/stack/questarr/login").status_code, 401)

