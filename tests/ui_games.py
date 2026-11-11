"""Games in a browser: the Games page (Steam linked, shelves with art, retro tiles, the PC card) on a computer and a
phone; a member signing in to Steam by QR code with no key (a stand-in Steam approves it), on both; then a retro game
played in Parlor: EmulatorJS (its files fetched once from its CDN and checked against the
pinned sha256s) runs a tiny NES program made here, which paints the screen green and writes its battery save. The
save goes up to Parlor and comes back on a second device; a save state does too.

Run from app/: ../.venv/bin/python ../tests/ui_games.py   (needs the network once, for EmulatorJS; prints RESULT: ok)"""
import base64
import json
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import core
import games
import uvicorn
from nes_rom import nes_rom
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-games-"))
OUT.mkdir(parents=True, exist_ok=True)
GAMES_DIR = env.TMP / "games"




(GAMES_DIR / "roms" / "nes").mkdir(parents=True, exist_ok=True)
(GAMES_DIR / "roms" / "nes" / "Pixel Green.nes").write_bytes(nes_rom())
(GAMES_DIR / "roms" / "gba").mkdir(exist_ok=True)
(GAMES_DIR / "roms" / "gba" / "Tiny Quest.gba").write_bytes(b"\0" * 192)
games.GAMES = GAMES_DIR

STEAM_ID, KEY = "76561197960287930", "0123456789ABCDEF0123456789ABCDEF"
SAMPLE = [(620, "Portal 2", 1500, 90), (440, "Team Fortress 2", 30, 0), (570, "Dota 2", 9000, 0), (730, "Counter-Strike 2", 4000, 30)]


def token(kind):
    """shaped like one of Steam's: three parts, the middle one saying whose it is"""
    return "eyJhbGciOiJFZERTQSJ9." + base64.urlsafe_b64encode(json.dumps({"sub": STEAM_ID, "kind": kind, "exp": int(time.time()) + 86400}).encode()).decode().rstrip("=") + ".c2ln"


SIGNIN = {"approved": False, "polls": 0}  # the stand-in's phone: it approves when the check says so


class Steam(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):  # Steam's sign-in service: no key
        call = urlparse(self.path).path.split("/")[2]
        sent = json.loads(parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())["input_json"][0])
        SIGNIN["polls"] += call == "PollAuthSessionStatus"
        body = json.dumps({"response": {
            "BeginAuthSessionViaQR": {"client_id": "4242", "request_id": "cmVxdWVzdA==", "challenge_url": "https://s.team/q/1/4242", "interval": 2},
            "PollAuthSessionStatus": {"refresh_token": token("refresh"), "account_name": "pats_login"} if SIGNIN["approved"] else {"had_remote_interaction": SIGNIN["polls"] > 1},
            "GenerateAccessTokenForApp": {"access_token": token("access")} if sent.get("refresh_token", "").startswith("eyJ") else {},
        }[call]}).encode()
        self.send_response(200)
        for k, v in (("Content-Type", "application/json"), ("Content-Length", str(len(body))), ("x-eresult", "1")):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u, q = urlparse(self.path), {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        if u.path.startswith("/profiles/"):
            body, kind = b"<profile><steamID><![CDATA[Pat]]></steamID></profile>", "text/xml"
            self.send_response(200)
        elif u.path.startswith("/img/"):
            body, kind = b"", "image/jpeg"  # no art: the card shows the name
            self.send_response(404)
        else:
            kind = "application/json"
            body = json.dumps({"response": {
                "/ISteamUser/GetPlayerSummaries/v2/": {"players": [{"personaname": "Pat", "communityvisibilitystate": 3}]},
                "/IPlayerService/GetOwnedGames/v1/": {"game_count": len(SAMPLE), "games": [{"appid": a, "name": n, "playtime_forever": m} for a, n, m, _ in SAMPLE]},
                "/IPlayerService/GetRecentlyPlayedGames/v1/": {"games": [{"appid": a, "name": n, "playtime_2weeks": r, "playtime_forever": m} for a, n, m, r in SAMPLE if r]},
            }.get(u.path, {})}).encode()
            self.send_response(200 if q.get("key") == KEY or (q.get("access_token", "").startswith("eyJ") and u.path.startswith("/IPlayerService/")) else 403)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


steam_server = ThreadingHTTPServer(("127.0.0.1", 0), Steam)
threading.Thread(target=steam_server.serve_forever, daemon=True).start()
games.STEAM_API = f"http://127.0.0.1:{steam_server.server_port}"
games.STEAM_ART = f"http://127.0.0.1:{steam_server.server_port}/img/{{appid}}/{{kind}}.jpg"
games.STEAM_PROFILE = f"http://127.0.0.1:{steam_server.server_port}/profiles/{{sid}}/?xml=1"

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
BASE = f"http://127.0.0.1:{PORT}"
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)
CHROME = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux*/chrome"))), None)
checks, errors = {}, []


def device(b, width=1280, height=860, name="admin"):
    ctx = b.new_context(viewport={"width": width, "height": height}, color_scheme="dark", is_mobile=width < 500, has_touch=width < 500)
    p = ctx.new_page()
    p.on("pageerror", lambda e: errors.append(str(e)[:200]))
    p.goto(BASE + "/")
    p.wait_for_selector("#gateForm")
    p.fill("#gName", name)
    p.fill("#gPw", "correct horse")
    p.click("#gateForm .primary")
    p.wait_for_selector("#gateForm", state="detached", timeout=30000)
    return p


def green(p) -> float:
    """the share of the game's screen that's the program's green, from a screenshot (a WebGL canvas reads back empty)"""
    shot = p.locator("#game canvas").first.screenshot()
    return p.evaluate("""async (b64) => {
      const img = new Image(); img.src = 'data:image/png;base64,' + b64; await img.decode();
      const s = document.createElement('canvas'); s.width = 64; s.height = 48;
      const g = s.getContext('2d'); g.drawImage(img, 0, 0, 64, 48);
      const d = g.getImageData(0, 0, 64, 48).data; let n = 0;
      for (let i = 0; i < d.length; i += 4) if (d[i + 1] > 150 && d[i] < 150 && d[i + 2] < 150) n++;
      return n / (64 * 48);
    }""", base64.b64encode(shot).decode())


try:
    env.signed_in("admin")
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=str(CHROME) if CHROME else None, args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        p = device(b)
        # the Games page: an admin adds the Steam key, links a profile; shelves, retro tiles, the PC card
        p.goto(BASE + "/#/games")
        p.wait_for_selector("#g-steam .gsetup", timeout=20000)
        checks["with no Steam linked, the page leads with the games there are; Steam is one line to open"] = (
            p.eval_on_selector_all(".gsec", "els => els.map(e => e.id)") == ["g-retro", "g-pc", "g-steam"] and not p.locator("#steamKey").is_visible())
        p.click("#g-steam .gsetup > summary")
        checks["opened, it offers the sign-in with Steam's app first, and the key as the other way"] = (
            p.locator("#steamQr [data-steam-qr]").is_visible() and not p.locator("#steamKey").is_visible() and "Or with a Steam Web API key" in p.inner_text(".gkey"))
        p.click(".gkey > summary")
        p.fill("#steamKeyIn", KEY)
        p.click("#steamKey .primary")
        p.wait_for_selector("#steamLink", timeout=10000)  # Steam's own page now, the profile box open
        p.wait_for_selector("#steamKey", state="detached")
        p.fill("#steamProfile", f"https://steamcommunity.com/profiles/{STEAM_ID}")
        p.click("#steamLink .primary")
        p.wait_for_selector("#g-steam .gshelf .gcard", timeout=10000)
        checks["once linked, Steam comes first"] = p.eval_on_selector_all(".gsec", "els => els.map(e => e.id)") == ["g-steam", "g-retro", "g-pc"]
        names = p.eval_on_selector_all("#steamGrid .gname", "els => els.map(e => e.textContent)")
        recent = p.eval_on_selector_all("#g-steam .gshelf .gname", "els => els.map(e => e.textContent)")
        checks["Steam: linked by profile address, most played first, recently played above"] = names[:2] == ["Dota 2", "Counter-Strike 2"] and recent == ["Portal 2", "Counter-Strike 2"]
        tiles = p.eval_on_selector_all("#g-retro .gcard.retro .gname", "els => els.map(e => e.textContent)")
        checks["Retro: the server's games by system"] = sorted(tiles) == ["Pixel Green", "Tiny Quest"]
        checks["the PC card says how it's turned on"] = "docs/games.md" in p.inner_text("#g-pc")
        p.wait_for_timeout(400)
        p.screenshot(path=str(OUT / "games.png"), full_page=True)
        p.goto(BASE + "/#/games/steam")
        p.wait_for_selector("#steamFind")
        p.fill("#steamFind", "port")
        checks["the Steam page finds a game"] = p.eval_on_selector_all("#steamGrid .gname", "els => els.map(e => e.textContent)") == ["Portal 2"]
        phone = device(b, 390, 844)
        phone.goto(BASE + "/#/games")
        phone.wait_for_selector("#g-retro .gcard", timeout=20000)
        checks["on a phone: no sideways scroll"] = phone.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        phone.screenshot(path=str(OUT / "games-phone.png"), full_page=True)
        # no key anywhere: a member signs in to Steam by QR code, on a computer and on a phone
        core.set_setting("steam_key", "")
        auth.create_user("pal", "correct horse", False)
        pal = device(b, name="pal")
        pal.goto(BASE + "/#/games/steam")
        pal.click("#steamQr [data-steam-qr]", timeout=20000)
        pal.wait_for_selector("#steamQr .gqr svg path", timeout=10000)
        box = pal.locator("#steamQr .gqr").bounding_box()
        white = pal.evaluate("getComputedStyle(document.querySelector('.gqr')).backgroundColor")
        checks["a member with no key anywhere gets a QR code: square, black on white, with a link that opens Steam's app"] = (
            abs(box["width"] - box["height"]) < 2 and box["width"] >= 180 and white == "rgb(255, 255, 255)"
            and pal.get_attribute("#steamQr a", "href") == "https://s.team/q/1/4242" and "Waiting for your phone" in pal.inner_text("#steamQrSay"))
        pal.wait_for_function("document.querySelector('#steamQrSay')?.textContent.includes('Approve it')", timeout=15000)
        pal.screenshot(path=str(OUT / "steam-qr.png"), full_page=True)
        small = device(b, 390, 844, name="pal")
        small.goto(BASE + "/#/games/steam")
        small.click("#steamQr [data-steam-qr]", timeout=20000)
        small.wait_for_selector("#steamQr .gqr svg path", timeout=10000)
        checks["on a phone the code and its link fit the screen"] = small.evaluate("document.documentElement.scrollWidth <= innerWidth + 1") and small.locator("#steamQr a").is_visible()
        small.screenshot(path=str(OUT / "steam-qr-phone.png"), full_page=True)
        SIGNIN["approved"] = True  # the phone says yes
        small.wait_for_selector("#g-steam .gshelf .gcard", timeout=20000)
        names = small.eval_on_selector_all("#steamGrid .gname", "els => els.map(e => e.textContent)")
        html = small.content() + json.dumps(small.evaluate("fetch('/api/games/steam').then((r) => r.json())"))
        checks["approved on the phone, the page goes to their games by itself; no sign-in is in the page"] = (
            names[:2] == ["Dota 2", "Counter-Strike 2"] and "· Pat" in small.inner_text("#g-steam") and "eyJ" not in html and "pats_login" not in html and "cmVxdWVzdA" not in html)
        small.screenshot(path=str(OUT / "steam-signed-in-phone.png"), full_page=True)
        # play: the tile opens the game's page; EmulatorJS runs the program
        p.goto(BASE + "/#/games/retro")
        p.wait_for_selector("#g-retro .gcard.retro", timeout=20000)
        p.click("text=Pixel Green")
        p.wait_for_url("**/api/games/play/nes/**", timeout=10000)
        p.wait_for_function("window.retroReady === true", timeout=120000)
        p.wait_for_function("(() => { const c = document.querySelector('#game canvas'); return !!c; })()", timeout=30000)
        for _ in range(40):
            if green(p) > 0.5:
                break
            p.wait_for_timeout(250)
        share = green(p)
        print(f"green on the game's screen: {share:.0%}")
        p.screenshot(path=str(OUT / "play.png"))
        checks["a NES game plays in Parlor (EmulatorJS, pinned files)"] = share > 0.5
        # its battery save goes up to Parlor
        p.evaluate("retroKeep()")
        uid = core.one("SELECT id FROM users WHERE name = 'admin'")["id"]
        saved = None
        for _ in range(40):
            r = p.evaluate("fetch(JSON.parse(document.getElementById('retro').textContent).save + '/sram').then(r => r.ok ? r.arrayBuffer().then(b => Array.from(new Uint8Array(b).slice(0, 2))) : null)")
            if r:
                saved = r
                break
            p.wait_for_timeout(250)
        checks["the game's own save goes up to Parlor"] = saved == [0x50, 0x4C]
        # a save state too
        p.evaluate("EJS_emulator.callEvent('saveState', { state: EJS_emulator.gameManager.getState() })")
        p.wait_for_timeout(1500)
        state_len = p.evaluate("fetch(JSON.parse(document.getElementById('retro').textContent).save + '/state').then(r => r.ok ? r.arrayBuffer().then(b => b.byteLength) : 0)")
        checks["a save state goes up to Parlor"] = state_len > 1000
        # another device: the save comes back into the core, and the state loads
        other = device(b)
        other.goto(p.url)
        other.wait_for_function("window.retroReady === true", timeout=120000)
        back = other.evaluate("(() => { const gm = EJS_emulator.gameManager; const f = gm.FS.readFile(gm.getSaveFilePath()); return Array.from(f.slice(0, 2)); })()")
        other.evaluate("() => { window.loadedState = 0; const gm = EJS_emulator.gameManager, l = gm.loadState.bind(gm); gm.loadState = (s) => { window.loadedState = s.length; l(s); }; }")
        other.evaluate("EJS_onLoadState()")
        other.wait_for_function("window.loadedState > 0", timeout=10000)
        checks["on another device the save is back and the state loads"] = back == [0x50, 0x4C]
        # a device that can't read the save never writes over it
        third = device(b)
        puts = []
        third.route("**/save/**/sram", lambda route: route.fulfill(status=503) if route.request.method == "GET" else (puts.append(1), route.continue_())[1])
        third.goto(p.url)
        third.wait_for_function("window.EJS_emulator && EJS_emulator.started", timeout=120000)
        third.wait_for_timeout(12000)
        third.evaluate("retroKeep()")
        third.wait_for_timeout(500)
        checks["a save that couldn't be read is never written over"] = not puts
        b.close()
except Exception as exc:  # report what was reached
    import traceback

    errors.append(f"{type(exc).__name__}: {str(exc)[:200]} at line {traceback.extract_tb(exc.__traceback__)[-1].lineno}")
finally:
    server.should_exit = True

for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
print("screenshots:", OUT)
ok = len(checks) == 16 and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
