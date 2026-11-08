"""Games → From your PC, the parts Parlor owns: the bridge's pages, API and WebSocket pass through only for people
signed in with Games, only from this site, only after an admin has opened it once, and always saying who's asking
in Parlor's own header (never the browser's). Against a stand-in bridge that echoes what it was sent."""
import socket
import threading
import time
import unittest
from unittest import mock

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import core
import games
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from starlette.websockets import WebSocketDisconnect
from yeerr_test_env import client, signed_in

bridge = FastAPI()


@bridge.api_route("/games/stream/{path:path}", methods=["GET", "POST", "DELETE"])
async def echo(path: str, request: Request):
    return {"path": path, "method": request.method, "who": request.headers.get("x-parlor-user"), "query": str(request.query_params),
            "cookie": request.headers.get("cookie"), "body": (await request.body()).decode()}


@bridge.websocket("/games/stream/{path:path}")
async def echo_ws(path: str, ws: WebSocket):
    await ws.accept()
    await ws.send_text(f"who={ws.headers.get('x-parlor-user')} path={path}")
    try:
        while True:
            await ws.send_text("echo:" + await ws.receive_text())
    except WebSocketDisconnect:
        pass


with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(bridge, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)
SAME = {"Origin": "http://testserver"}


class Stream(unittest.TestCase):
    def setUp(self):
        self.patch = mock.patch.object(games, "STREAM", f"http://127.0.0.1:{PORT}")
        self.patch.start()
        core.set_setting("games_stream_admin", "0")
        self.admin, self.member = signed_in("admin"), signed_in("streamer")

    def tearDown(self):
        self.patch.stop()

    def test_an_admin_first_then_everyone_with_games(self):
        r = self.member.get("/games/stream/")
        self.assertEqual(r.status_code, 409, "before an admin has opened it")
        self.assertFalse(self.member.get("/api/games").json()["stream_ready"])
        self.assertEqual(self.admin.get("/games/stream/").json()["who"], f"parlor-{core.one('SELECT id FROM users WHERE name = ?', 'admin')['id']}")
        self.assertTrue(self.member.get("/api/games").json()["stream_ready"])
        self.assertEqual(self.member.get("/games/stream/api/hosts").status_code, 200)
        self.assertEqual(client().get("/games/stream/").status_code, 401, "signed out")
        core.db().execute("UPDATE users SET blocked = 'games' WHERE name = 'streamer'")
        try:
            self.assertEqual(self.member.get("/games/stream/").status_code, 403, "without Games")
        finally:
            core.db().execute("UPDATE users SET blocked = '' WHERE name = 'streamer'")

    def test_a_bridge_that_isnt_answering_doesnt_count_as_set_up(self):
        with mock.patch.object(games, "STREAM", "http://127.0.0.1:9"):
            self.assertEqual(self.admin.get("/games/stream/").status_code, 502)
        self.assertFalse(self.member.get("/api/games").json()["stream_ready"])
        with mock.patch.object(games, "STREAM", ""):
            self.assertEqual(self.admin.get("/games/stream/").status_code, 409, "the profile is off")

    def test_who_is_asking_is_parlors_to_say(self):
        self.admin.get("/games/stream/")
        mid = core.one("SELECT id FROM users WHERE name = 'streamer'")["id"]
        got = self.member.get("/games/stream/api/user?x=1", headers={"X-Parlor-User": "parlor-1"}).json()
        self.assertEqual(got["who"], f"parlor-{mid}", "the browser's header is replaced")
        self.assertEqual(got["query"], "x=1")
        self.assertIsNone(got["cookie"], "Parlor's session never reaches the bridge")

    def test_changes_come_from_this_site_only_and_paths_stay_put(self):
        self.admin.get("/games/stream/")
        self.assertEqual(self.member.post("/games/stream/api/host", json={"address": "pc"}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.member.post("/games/stream/api/host", json={"address": "pc"}).status_code, 403, "no Origin at all")
        ok = self.member.post("/games/stream/api/host", json={"address": "pc"}, headers=SAME)
        self.assertEqual((ok.status_code, ok.json()["method"]), (200, "POST"))
        self.assertEqual(self.member.post("/games/stream/api/host", content=b"x" * 1_000_001, headers=SAME).status_code, 413)
        for bad in ("api/%2e%2e/x", "api/a%3Fb", "api/a@b", "api/a%25b", "..%2Fx"):
            self.assertEqual(self.member.get(f"/games/stream/{bad}").status_code, 404, bad)

    def test_the_websocket_checks_the_same_and_passes_both_ways(self):
        self.admin.get("/games/stream/")
        with self.member.websocket_connect("/games/stream/api/host/stream", headers=SAME) as ws:
            self.assertTrue(ws.receive_text().startswith("who=parlor-"))
            ws.send_text("hi")
            self.assertEqual(ws.receive_text(), "echo:hi")
        for who, headers in ((client(), SAME), (self.member, {"Origin": "https://evil.example"})):
            with self.assertRaises(WebSocketDisconnect):
                with who.websocket_connect("/games/stream/api/host/stream", headers=headers) as ws:
                    ws.receive_text()


if __name__ == "__main__":
    unittest.main()
