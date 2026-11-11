"""Asking for games in a real browser: Request → Games shows rows from a stand-in Steam, the search finds a game, its
page has one Request button; a member's request waits for an admin, who approves it, picks a release from the list
(no link or key in the page) and sees it downloaded; Your requests lists it; a stack with no Questarr login says so.
Steam and Questarr are stand-ins and the covers are painted here, so nothing leaves this machine. Three widths:
   python tests/ui_gamereq.py [OUT_DIR]   (needs Playwright and its Chromium)"""
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import core
import gamelookup
import gamereq
import stack
import uvicorn
from playwright.sync_api import sync_playwright
from test_gamelookup import NOW, Steam
from test_gamereq import Questarr, release

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-gamereq-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-game-req-pass-42!"  # pragma: allowlist secret (made up)
COVER = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 2 3'><defs><linearGradient id='g' x2='1' y2='1'><stop stop-color='#6b5cff'/>"
         "<stop offset='1' stop-color='#ff7a59'/></linearGradient></defs><rect width='2' height='3' fill='url(#g)'/></svg>")
q = Questarr()
q.releases = [release("Doom-HARBOUR", known=None), release("Doom [Some Repack]", seeders=3, kind="repack", known="repack"), release("Doom Update 4", part="update", kind="update")]
own = {"password": q.password}
for obj, name, value in ((gamelookup, "steam", Steam()), (gamelookup, "_now", lambda: NOW), (gamereq.requests, "request", q), (gamereq, "start", lambda: None),
                         (stack, "_questarr_own", lambda: own["password"]), (stack, "questarr_url", lambda: "http://questarr:5000")):
    mock.patch.object(obj, name, value).start()
core.db()
auth.create_user("host", PASSWORD, True)
auth.create_user("gamer", PASSWORD, False)

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
BASE = f"http://127.0.0.1:{PORT}"
SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
errors, checks, outside = [], {}, []


def someone(b, name, width=1366, height=1000, **kw):
    ctx = b.new_context(viewport={"width": width, "height": height}, color_scheme="dark", **kw)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    page.route("https://**/*", lambda r: (r.fulfill(content_type="image/svg+xml", body=COVER) if "steamstatic.com" in r.request.url
                                          else r.continue_() if "//fonts.g" in r.request.url  # the page's typeface
                                          else (outside.append(r.request.url) if "#/gamereq" in r.request.frame.url or "#/requests" in r.request.frame.url else None, r.abort())))  # Home has shelves of its own
    page.goto(BASE + "/")
    page.wait_for_selector("#gateForm")
    page.fill("#gName", name)
    page.fill("#gPw", PASSWORD)
    page.click("#gateForm .primary")
    page.wait_for_selector("#gateForm", state="detached", timeout=30000)
    return page


def opened(page, where, sel):
    page.goto("about:blank")  # a whole load each time: the same address again would leave the old page standing
    page.goto(BASE + "/" + where)
    page.wait_for_selector(sel, timeout=30000)
    page.wait_for_timeout(400)
    return page.inner_text("main")


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    gamer = someone(b, "gamer")
    text = opened(gamer, "#/gamereq", ".pcard[data-game]")
    checks["Request → Games shows Steam's rows: popular, new, coming soon"] = all(w in text for w in ("Popular now", "New and popular", "Coming soon", "Doom", "Fresh Quest", "Soon Quest"))
    checks["adult-only games and things that aren't games stay out"] = "After Dark" not in text and "Console of Steam" not in text and "Add-on" not in text
    checks["Games is a page of Request"] = gamer.get_attribute("#side a[href='#/gamereq']", "aria-current") == "page" and [
        t.strip() for t in gamer.locator("main .subnav a").all_inner_texts()] == ["Films & shows", "Music", "Games", "Your requests"]
    checks["a member isn't offered the admin's switch"] = gamer.locator("#gameAuto").count() == 0
    shot(gamer, "browse-desk")
    gamer.fill("#q", "doom")
    gamer.press("#q", "Enter")
    gamer.wait_for_selector(".pgrid .pcard[data-game]", timeout=30000)
    checks["the search box finds games by title"] = "#/gamereq/search/doom" in gamer.url and sorted(gamer.locator(".pgrid .pcard .t").all_inner_texts()) == ["Doom", "Doom Eternal"]
    text = opened(gamer, "#/gamereq/game/10", ".hero [data-ask-game]")
    checks["a game's page: what it is, who made it, its pictures, a link to Steam"] = all(w in text for w in ("Doom", "About Doom.", "By Makers", "Screenshots", "On Steam")) and gamer.locator(".gshots img").count() == 1
    shot(gamer, "game-desk")
    gamer.click(".hero [data-ask-game]")
    gamer.wait_for_selector(".greqs li", timeout=30000)
    text = gamer.inner_text("main")
    checks["a member's request waits for an admin, and says so"] = "Waiting for an admin" in text and not q.ledger and gamer.locator("[data-ask-game]").count() == 0
    text = opened(gamer, "#/requests", "#gameReqs .greqs li")
    checks["Your requests lists the game under Games; a member can cancel, not approve"] = ("Games" in gamer.inner_text("#gameReqs h2") and "Doom" in text
                                                                                            and gamer.locator("[data-greq=approve]").count() == 0 and gamer.locator("[data-greq=cancel]").count() == 1)

    host = someone(b, "host")
    host.wait_for_selector("#stats .stat-card[href='#/requests']", timeout=30000)
    checks["Home tells the admin a request is waiting for approval"] = "1 request waiting for approval" in host.inner_text("#stats").replace("\n", " ")
    opened(host, "#/requests/games", "#gameReqs [data-greq=approve]")
    host.click("[data-greq=approve]")
    host.wait_for_selector(".mreq.searching", timeout=30000)
    gamereq.tick()  # into Questarr …
    gamereq.tick()  # … and its indexers searched
    text = opened(host, "#/requests/games", ".gpicks .mpick")
    html = host.content().lower()
    checks["the admin approves; Questarr gets the request by the game's Steam id"] = [r["app"] for r in q.ledger.values()] == [10]
    checks["no release passes every check, so the admin picks: the usable ones, best first"] = ("Pick a release" in text and host.locator(".gpicks .mpick b").all_inner_texts() == ["Doom-HARBOUR", "Doom [Some Repack]"]
                                                                                              and "Harbour Index" in text and "20 seeders" in text)
    checks["no download link and no key reaches the page"] = "apikey" not in html and "prowlarr" not in html
    shot(host, "pick-desk")
    host.click(".gpicks .mpick >> nth=0")
    host.wait_for_selector(".mreq.downloading", timeout=30000)
    checks["picking one starts that download in Questarr"] = len(q.grabs) == 1 and q.grabs[0]["title"] == "Doom-HARBOUR" and "Doom-HARBOUR" in host.inner_text("#gameReqs")
    for r in q.ledger.values():
        r["status"] = "available"
    gamereq.tick(time.time() + 120)
    text = opened(host, "#/gamereq/game/10", ".greqs li")
    checks["and when Questarr has it, the game's page says Downloaded"] = "Downloaded" in text and host.locator(".hero [data-ask-game]").count() == 0
    opened(host, "#/gamereq", "#gameAuto")
    was = host.is_checked("#gameAuto")
    host.click("#gameAuto")
    host.wait_for_function("document.querySelector('#toast').textContent.includes('waits for your pick')", timeout=30000)
    checks["an admin can switch off taking releases unseen"] = was and core.db().execute("SELECT value FROM settings WHERE key = 'games_auto_pick'").fetchone()[0] in ("0", "false", "")
    shot(host, "browse-admin-desk")

    own["password"] = ""  # a stack set up before Parlor made Questarr's login
    text = opened(host, "#/gamereq", ".pcard[data-game]")
    checks["no Questarr login: the page says what to do, and still shows the games"] = "Questarr isn't set up yet" in text and "Set up again" in text and "Fresh Quest" in text
    text = opened(host, "#/gamereq/game/20", ".hero")
    checks["and a game's page offers no Request button"] = host.locator("[data-ask-game]").count() == 0 and "once Questarr is set up" in text
    own["password"] = q.password

    for label, width, height, mobile in (("tablet", 820, 1180, False), ("phone", 390, 844, True)):
        pg = someone(b, "gamer", width, height, is_mobile=mobile)
        for name, where, sel in (("browse", "#/gamereq", ".pcard[data-game]"), ("game", "#/gamereq/game/20", ".hero [data-ask-game]"), ("requests", "#/requests", "#gameReqs .greqs li")):
            opened(pg, where, sel)
            shot(pg, f"{name}-{label}")
            checks[f"{label}: {name} has no sideways scroll"] = pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    b.close()
server.should_exit = True
checks["the game pages ask the internet for nothing but the typeface"] = not outside
print("asked outside:", outside or "nothing")
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = len(checks) == 24 and all(checks.values()) and not errors and len(shots) == 10
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
