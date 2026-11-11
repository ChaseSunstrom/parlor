"""Access control in a real browser: Parlor runs here with a faked Jellyfin. An admin opens Settings → People →
Access for a member, closes Watch and Downloads and limits Jellyfin to one library; the member's menus then lack
Watch, the server refuses its API, and Jellyfin was told. No page errors; screenshots at desktop and phone width:
   python tests/ui_access.py [OUT_DIR]   (needs Playwright and its Chromium beside the app's packages)"""
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
import jf
import uvicorn
from playwright.sync_api import sync_playwright
from test_access import FakeJellyfin

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-access-"))
OUT.mkdir(parents=True, exist_ok=True)
ADMIN, MEMBER, PASSWORD = "uiadmin", "uimember", "Ui-access-pass-42!"  # pragma: allowlist secret (made up)
fake = FakeJellyfin()


def requests(method, url, **kw):  # Jellyfin is faked; anything else Parlor asks for (YouTube, Seerr) is down
    if "jellyfin:8096" in url:
        return fake(method, url, **kw)
    raise jf.requests.ConnectionError("not in this test")


mock.patch.object(jf.requests, "request", requests).start()
core.db()
core.set_setting("jellyfin_url", "http://jellyfin:8096")
core.set_setting("jellyfin_key", "k")
core.set_setting("jellyfin_user", "default")
auth.create_user(ADMIN, PASSWORD, True)
member_id = auth.create_user(MEMBER, PASSWORD, False)

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
BASE = f"http://127.0.0.1:{PORT}"
SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
errors, checks = [], {}


def sign_in(page, name):
    page.goto(BASE + "/")
    page.wait_for_selector("#gateForm")
    page.fill("#gName", name)
    page.fill("#gPw", PASSWORD)
    page.click("#gateForm .primary")
    page.wait_for_selector("#gateForm", state="detached", timeout=30000)


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    for label, size in (("desk", {"width": 1280, "height": 900}), ("phone", {"width": 390, "height": 844})):
        core.db().execute("UPDATE users SET blocked = '', libraries = NULL WHERE id = ?", (member_id,))
        ctx = b.new_context(viewport=size, color_scheme="dark")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        sign_in(page, ADMIN)
        page.goto(BASE + "/#/settings")
        row = f"[data-user='{member_id}']"
        page.wait_for_selector(f"{row} [data-access]", timeout=30000)
        page.click(f"{row} [data-access]")
        page.wait_for_selector(".access-row form", timeout=30000)
        page.uncheck(".access-row [name=section][value=watch]")
        page.uncheck(".access-row [name=section][value=downloads]")
        page.uncheck(".access-row [name=all]")
        page.check(f".access-row [name=lib][value='{'f' * 32}']")
        page.locator(".access-row").scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / f"editor-{label}.png"))
        page.click(".access-row .primary")
        page.wait_for_selector(".access-row", state="detached", timeout=30000)
        page.wait_for_selector(f"{row}:has-text('1 library')", timeout=30000)
        row_text = page.inner_text(row)
        checks[f"{label}: the People list says what's closed"] = "no downloads, watch" in row_text and "1 library" in row_text
        ctx.close()
        ctx = b.new_context(viewport=size, color_scheme="dark")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        sign_in(page, MEMBER)
        page.goto(BASE + "/#/media")
        time.sleep(1)
        nav = page.inner_text("#side") if label == "desk" else page.inner_text(".nav")
        checks[f"{label}: Watch is gone from the menu"] = "Watch" not in nav and "Request" in nav
        status = page.evaluate("fetch('/api/me/later').then((r) => r.status)")
        checks[f"{label}: the server refuses Watch"] = status == 403
        page.screenshot(path=str(OUT / f"member-{label}.png"))
        ctx.close()
    b.close()
server.should_exit = True
own = core.one("SELECT jf_user, jf_own, libraries FROM users WHERE id = ?", member_id)
checks["Jellyfin got the limit on the member's own user"] = bool(own["jf_own"]) and fake.policies.get(own["jf_user"], {}).get("EnabledFolders") == ["f" * 32]
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = len(checks) == 7 and all(checks.values()) and not errors and len(shots) == 4
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
