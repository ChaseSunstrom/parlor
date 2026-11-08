"""The remote and casting, over real HTTP (Parlor served by uvicorn here): a remote sees its account's TVs come and go
and what they play; its commands reach the TV quickly (p95 measured under 300 ms) and in order, from two remotes at
once; nobody else can drive or watch a TV; an offline TV says so; unpairing or signing out ends the streams; an admin
sees and drives every TV only when asking for everyone's (Settings → TVs)."""
import json
import queue
import socket
import threading
import time
import unittest
from unittest import mock

import httpx

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import core
import tv
import uvicorn
from yeerr_test_env import H

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
BASE = f"http://127.0.0.1:{PORT}"
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)


def session(name, admin=False):
    import auth

    if not core.one("SELECT 1 AS x FROM users WHERE name = ?", name):
        auth.create_user(name, "correct horse", admin)
    c = httpx.Client(base_url=BASE, headers=H, timeout=10)
    assert c.post("/api/auth/login", json={"name": name, "password": "correct horse"}).status_code == 200
    return c


def paired_tv(owner, name="Den"):
    t = httpx.Client(base_url=BASE, headers=H, timeout=10)
    got = t.post("/api/tv/code").json()
    assert owner.post("/api/tv/pair", json={"code": got["code"], "name": name}).status_code == 200
    assert t.post("/api/tv/claim", json={"claim": got["claim"]}).json()["paired"]
    return t, t.get("/api/tv/me").json()["tv"]["id"]


class Listen:
    """an event stream read on its own thread: next() gives each message, None once it has ended"""

    def __init__(self, c, query=""):
        self.q, self.done = queue.Queue(), threading.Event()
        threading.Thread(target=self.run, args=(c, query), daemon=True).start()

    def run(self, c, query):
        try:
            with c.stream("GET", "/api/tv/events" + query, timeout=None) as r:
                self.status = r.status_code
                for line in r.iter_lines():
                    if self.done.is_set():
                        return
                    if line.startswith("data: "):
                        self.q.put((time.perf_counter(), json.loads(line[6:])))
        except httpx.HTTPError:
            pass
        self.q.put((time.perf_counter(), None))

    def next(self, kind=None, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            try:
                at, msg = self.q.get(timeout=max(0.01, end - time.time()))
            except queue.Empty:
                break
            if msg is None or kind is None or msg.get("type") == kind:
                return (at, msg) if kind == "timed" else msg
        raise AssertionError(f"no {kind or 'message'} within {timeout} s")

    def close(self):
        self.done.set()


class Remote(unittest.TestCase):
    def setUp(self):
        tv._tries.clear()
        core.db().execute("DELETE FROM tvs")
        self.owner = session("remoteowner")

    def test_a_remote_drives_its_tv_quickly_and_both_remotes_see_the_state(self):
        t, tid = paired_tv(self.owner)
        one, two = Listen(self.owner), Listen(session("remoteowner"))
        self.assertEqual([x["id"] for x in one.next("tvs")["tvs"]], [tid])
        two.next("tvs")
        screen = Listen(t)
        self.assertEqual(one.next("online")["tv"], tid, "a remote sees the TV come on")
        two.next("online")
        # each press, timed from the POST to the TV hearing it
        gaps = []
        for i in range(60):
            sent = time.perf_counter()
            r = self.owner.post(f"/api/tv/{tid}/do", json={"cmd": "key", "key": ["up", "down", "left", "right"][i % 4]})
            self.assertEqual(r.status_code, 200, r.text)
            msg = screen.next("do")
            gaps.append(time.perf_counter() - sent)
            self.assertEqual(msg["key"], ["up", "down", "left", "right"][i % 4], "in order")
        p95 = sorted(gaps)[int(len(gaps) * 0.95)]
        print(f"key to TV: p95 {p95 * 1000:.1f} ms, worst {max(gaps) * 1000:.1f} ms")
        self.assertLess(p95, 0.3)
        # a second remote's command arrives too, then the TV says what it's doing and both remotes hear it
        other = session("remoteowner")
        other.post(f"/api/tv/{tid}/do", json={"cmd": "seek", "by": 10})
        self.assertEqual(screen.next("do")["by"], 10)
        self.assertEqual(t.post("/api/tv/state", json={"state": {"title": "Night Train", "playing": True, "at": 12}}).status_code, 200)
        self.assertEqual(one.next("state")["state"]["title"], "Night Train")
        self.assertEqual(self.owner.get("/api/tv").json()["tvs"][0]["state"]["at"], 12, "where it is in the film, not the server's clock")
        self.assertEqual(two.next("state")["state"]["title"], "Night Train")
        self.assertTrue(self.owner.get("/api/tv").json()["tvs"][0]["online"])
        # the TV goes away: remotes hear it, and commands say it's off
        screen.close()
        t.close()
        one.next("offline", timeout=tv.PING + 5)
        r = self.owner.post(f"/api/tv/{tid}/do", json={"cmd": "toggle"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Den", r.json()["detail"])
        for x in (one, two):
            x.close()

    def test_nobody_else_drives_or_watches_it_but_an_admin_from_settings(self):
        t, tid = paired_tv(self.owner)
        screen = Listen(t)
        stranger = session("remotestranger")
        theirs = Listen(stranger)
        self.assertEqual(theirs.next("tvs")["tvs"], [], "someone else's TV isn't listed")
        self.assertEqual(stranger.post(f"/api/tv/{tid}/do", json={"cmd": "toggle"}).status_code, 404)
        self.assertEqual(Listen(stranger, "?everyone=1").next("tvs")["tvs"], [], "asking for everyone's isn't enough")
        boss = session("remoteboss", admin=True)
        mine_only = Listen(boss)
        self.assertEqual(mine_only.next("tvs")["tvs"], [], "an admin's own remote lists their own TVs")
        everyone = Listen(boss, "?everyone=1")
        self.assertEqual([x["id"] for x in everyone.next("tvs")["tvs"]], [tid], "Settings → TVs lists every TV")
        self.assertEqual(boss.post(f"/api/tv/{tid}/do", json={"cmd": "pause"}).status_code, 404, "an admin's own remote can't drive others' TVs")
        self.assertEqual(boss.delete(f"/api/tv/{tid}").status_code, 404, "nor unpair them")
        self.assertEqual(boss.post(f"/api/tv/{tid}/do?everyone=true", json={"cmd": "pause"}).status_code, 200, "Settings → TVs can")
        self.assertEqual(screen.next("do")["cmd"], "pause")
        t.post("/api/tv/state", json={"state": {"title": "Secret"}})
        self.assertEqual(everyone.next("state")["state"]["title"], "Secret")
        with self.assertRaises(AssertionError):
            theirs.next("state", timeout=1)
        for x in (screen, theirs, mine_only, everyone):
            x.close()

    def test_what_a_command_may_say(self):
        t, tid = paired_tv(self.owner)
        Listen(t)
        time.sleep(0.3)
        for bad in ({"cmd": "key", "key": "sideways"}, {"cmd": "explode"}, {"cmd": "cast"}, {"cmd": "type", "text": "x" * 201},
                    {"cmd": "cast", "cast": {"kind": "jf", "id": "x" * 300}}, {"cmd": "volume", "to": -1}):
            self.assertEqual(self.owner.post(f"/api/tv/{tid}/do", json=bad).status_code, 422, bad)
        self.assertEqual(self.owner.post("/api/tv/state", json={"state": {}}).status_code, 403, "only a TV says what it plays")
        self.assertEqual(t.post("/api/tv/state", json={"state": {"x": "y" * 5000}}).status_code, 413)
        self.assertEqual(httpx.post(f"{BASE}/api/tv/{tid}/do", json={"cmd": "toggle"}, cookies=self.owner.cookies).status_code, 403,
                         "commands need the header other sites can't send")

    def test_a_tv_that_goes_quiet_shows_as_off(self):
        with mock.patch.object(tv, "PING", 0.5):
            t, tid = paired_tv(self.owner)
            remote = Listen(self.owner)
            remote.next("tvs")
            screen = Listen(t)  # listening, but never saying it's there (switched off at the wall, say)
            remote.next("online")
            self.assertEqual(remote.next("offline", timeout=5)["tv"], tid, "quiet for three pings: off")
            self.assertIsNone(screen.next(timeout=5), "and its stream is ended")
            self.assertEqual(self.owner.post(f"/api/tv/{tid}/do", json={"cmd": "toggle"}).status_code, 409)
            remote.close()

    def test_a_demoted_admin_stops_hearing_every_tv(self):
        with mock.patch.object(tv, "PING", 0.5):
            boss = session("remotedemoted", admin=True)
            everyone = Listen(boss, "?everyone=1")
            everyone.next("tvs")
            core.db().execute("UPDATE users SET admin = 0 WHERE name = 'remotedemoted'")
            self.assertIsNone(everyone.next(timeout=5), "no longer an admin: the stream of everyone's TVs ends")

    def test_unpairing_or_signing_out_ends_the_streams(self):
        t, tid = paired_tv(self.owner)
        remote = Listen(self.owner)
        remote.next("tvs")  # listening before the TV comes on, so it hears that
        screen = Listen(t)
        remote.next("online")
        self.assertEqual(self.owner.delete(f"/api/tv/{tid}").status_code, 200)
        self.assertIsNone(screen.next(), "the TV's stream ends at once")
        self.assertEqual(remote.next("gone")["tv"], tid)
        with mock.patch.object(tv, "PING", 0.5):
            t2, _ = paired_tv(self.owner)
            remote2 = Listen(self.owner)
            remote2.next("tvs")
            screen2 = Listen(t2)
            remote2.next("online")
            self.owner.post("/api/auth/logout-everywhere")
            self.assertIsNone(screen2.next(timeout=5), "signed out everywhere: the TV stops hearing")
            self.assertIsNone(remote2.next(timeout=5), "and so do the remotes")


if __name__ == "__main__":
    unittest.main()
