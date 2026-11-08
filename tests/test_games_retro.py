"""Games → Retro: ROMs under GAMES/roms/<system>/ are listed by system (with art beside them), served only from
inside that folder, and played in Parlor with EmulatorJS, whose files come from its CDN once and only when their
sha256 matches the pinned list. Saves and save states are each person's own, sealed on the server. The play page
alone gets cross-origin isolation, and only for the systems whose cores need it."""
import os
import shutil
import threading
import unittest
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import core
import games
from yeerr_test_env import H, TMP, client, signed_in

ROOT = TMP / "games"
ROM = b"NES\x1a" + bytes(range(256)) * 64
CDN_FILES = {"loader.js": b"// loader", "cores/fceumm-wasm.data": b"core bytes" * 100, "bad.js": b"tampered"}
PINNED = {"loader.js": sha256(CDN_FILES["loader.js"]).hexdigest(), "cores/fceumm-wasm.data": sha256(CDN_FILES["cores/fceumm-wasm.data"]).hexdigest(),
          "bad.js": sha256(b"what it should be").hexdigest(), "missing.js": "0" * 64}
FETCHED: list = []


class CDN(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.lstrip("/")
        FETCHED.append(path)
        body = CDN_FILES.get(path)
        self.send_response(200 if body else 404)
        self.send_header("Content-Length", str(len(body or b"")))
        self.end_headers()
        self.wfile.write(body or b"")


SERVER = ThreadingHTTPServer(("127.0.0.1", 0), CDN)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()


def make_library():
    shutil.rmtree(ROOT, ignore_errors=True)
    nes, gba, odd = ROOT / "roms" / "nes", ROOT / "roms" / "gba", ROOT / "roms" / "toaster"
    for d in (nes, gba, odd):
        d.mkdir(parents=True)
    (nes / "Gruniozerca.nes").write_bytes(ROM)
    (nes / "Gruniozerca.png").write_bytes(b"\x89PNG art")
    (nes / "notes.txt").write_text("not a game")
    (nes / ".hidden.nes").write_bytes(ROM)
    (gba / "Tiny Quest.gba").write_bytes(b"gba" * 10)
    (odd / "x.bin").write_bytes(b"?")  # a folder no emulator plays
    (ROOT / "secret.nes").write_bytes(b"outside the system folders")
    os.symlink("/etc/hostname", nes / "escape.nes")  # a link out of the folder
    os.symlink(str(ROOT.parent), ROOT / "roms" / "snes")  # a system folder that's a link out
    dos = ROOT / "roms" / "dos"
    dos.mkdir()
    (dos / "Keen.zip").write_bytes(b"PK\x03\x04")


class Retro(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_library()

    def setUp(self):
        self.patches = [mock.patch.object(games, "GAMES", ROOT), mock.patch.object(games, "EJS_CDN", f"http://127.0.0.1:{SERVER.server_port}/"),
                        mock.patch.object(games, "EJS_FILES", PINNED)]
        for p in self.patches:
            p.start()
        FETCHED.clear()
        self.me = signed_in("retro")

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_roms_are_listed_by_system_with_art(self):
        got = self.me.get("/api/games/retro").json()
        systems = {s["id"]: s for s in got["systems"]}
        self.assertEqual(sorted(systems), ["dos", "gba", "nes"], "known systems only; a linked-out folder isn't one")
        self.assertEqual(systems["nes"]["name"], "NES")
        nes = systems["nes"]["games"]
        self.assertEqual([g["name"] for g in nes], ["Gruniozerca"], "no text files, hidden files or links out")
        self.assertEqual(nes[0]["art"], "/api/games/retro/art/nes/Gruniozerca.png")
        self.assertEqual(nes[0]["play"], "/api/games/play/nes/Gruniozerca.nes")
        self.assertIsNone(systems["gba"]["games"][0]["art"])
        self.assertEqual(self.me.get(nes[0]["art"]).content, b"\x89PNG art")
        self.assertEqual(self.me.get("/api/games/retro/art/nes/a%00b.png").status_code, 404)
        self.assertEqual(self.me.get("/api/games/retro/art/nes/..%2F..%2Fsecret.png").status_code, 404)

    def test_a_rom_is_served_from_inside_the_folder_only(self):
        r = self.me.get("/api/games/retro/rom/nes/Gruniozerca.nes")
        self.assertEqual((r.status_code, r.content), (200, ROM))
        part = self.me.get("/api/games/retro/rom/nes/Gruniozerca.nes", headers={"Range": "bytes=4-7"})
        self.assertEqual((part.status_code, part.content), (206, ROM[4:8]))
        for bad in ("nes/escape.nes", "nes/..%2F..%2Fsecret.nes", "nes/%2e%2e/secret.nes", "nes/notes.txt", "nes/.hidden.nes",
                    "..%2Fsecret.nes/x", "snes/anything.sfc", "toaster/x.bin", "nes/%2Fetc%2Fhostname", "nes/missing.nes"):
            self.assertEqual(self.me.get(f"/api/games/retro/rom/{bad}").status_code, 404, bad)

    def test_saves_and_states_are_each_persons_own_and_sealed(self):
        base = "/api/games/retro/save/nes/Gruniozerca.nes"
        self.assertEqual(self.me.get(f"{base}/sram").status_code, 404, "nothing saved yet")
        for slot, data in (("sram", b"battery save"), ("state", b"\x00state" * 1000)):
            r = self.me.put(f"{base}/{slot}", content=data, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self.me.get(f"{base}/{slot}").content, data, "back on another device")
        stored = list((Path(core.DATA) / "games-saves").rglob("*"))
        self.assertTrue(any(p.is_file() for p in stored))
        self.assertFalse(any(b"battery save" in p.read_bytes() for p in stored if p.is_file()), "sealed on disk")
        other = signed_in("retro2")
        self.assertEqual(other.get(f"{base}/sram").status_code, 404, "someone else's saves aren't theirs")
        self.assertEqual(self.me.put(f"{base}/slot9", content=b"x", headers=H).status_code, 422)
        self.assertEqual(self.me.put("/api/games/retro/save/nes/missing.nes/sram", content=b"x", headers=H).status_code, 404)
        self.assertEqual(self.me.put("/api/games/retro/save/nes/escape.nes/sram", content=b"x", headers=H).status_code, 404)
        with mock.patch.object(games, "SAVE_MAX", 100):
            self.assertEqual(self.me.put(f"{base}/state", content=b"x" * 101, headers=H).status_code, 413)
        self.assertEqual(self.me.put(f"{base}/sram", content=b"x", headers={}).status_code, 403, "changes need the header")

    def test_emulator_files_come_once_and_only_as_pinned(self):
        r = self.me.get("/api/games/emulator/cores/fceumm-wasm.data")
        self.assertEqual((r.status_code, r.content), (200, CDN_FILES["cores/fceumm-wasm.data"]))
        self.me.get("/api/games/emulator/cores/fceumm-wasm.data")
        self.assertEqual(FETCHED, ["cores/fceumm-wasm.data"], "fetched once, then kept")
        self.assertEqual(self.me.get("/api/games/emulator/bad.js").status_code, 502, "a file that doesn't match its sha256 is refused")
        self.assertEqual(self.me.get("/api/games/emulator/bad.js").status_code, 502, "and never kept")
        self.assertEqual(self.me.get("/api/games/emulator/missing.js").status_code, 502)
        self.assertEqual(self.me.get("/api/games/emulator/not-pinned.js").status_code, 404, "only the pinned list")
        self.assertEqual(self.me.get("/api/games/emulator/..%2F..%2Fsecret").status_code, 404)

    def test_the_play_page_and_cross_origin_isolation(self):
        r = self.me.get("/api/games/play/nes/Gruniozerca.nes")
        self.assertEqual(r.status_code, 200)
        self.assertIn('"core": "nes"', r.text)
        self.assertIn("/api/games/retro/rom/nes/Gruniozerca.nes", r.text)
        self.assertNotIn("cross-origin-opener-policy", r.headers, "only where a core needs it")
        dos = self.me.get("/api/games/play/dos/Keen.zip")
        self.assertEqual(dos.headers["cross-origin-opener-policy"], "same-origin")
        self.assertEqual(dos.headers["cross-origin-embedder-policy"], "require-corp")
        self.assertIn('"threads": true', dos.text)
        self.assertNotIn("cross-origin-opener-policy", self.me.get("/api/games/retro").headers, "nothing else is isolated")
        self.assertEqual(self.me.get("/api/games/play/nes/escape.nes").status_code, 404)
        named = ROOT / "roms" / "nes" / "<img src=x onerror=alert(1)>.nes"
        named.write_bytes(ROM)
        try:
            page = self.me.get("/api/games/play/nes/%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E.nes").text
            self.assertIn("onerror", page)
            self.assertNotIn("<img", page, "a file's name is never markup")
        finally:
            named.unlink()

    def test_only_people_with_games(self):
        uid = core.one("SELECT id FROM users WHERE name = 'retro'")["id"]
        core.db().execute("UPDATE users SET blocked = 'games' WHERE id = ?", (uid,))
        try:
            for path in ("/api/games/retro", "/api/games/retro/rom/nes/Gruniozerca.nes", "/api/games/play/nes/Gruniozerca.nes"):
                self.assertEqual(self.me.get(path).status_code, 403, path)
        finally:
            core.db().execute("UPDATE users SET blocked = '' WHERE id = ?", (uid,))
        self.assertEqual(client().get("/api/games/retro").status_code, 401)


if __name__ == "__main__":
    unittest.main()
