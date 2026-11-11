"""Questarr's row in Settings → Services in a real browser, against the fake stack from test_stack.py: after setup it
shows its steps and asks for nothing: no IGDB keys, no form, and the old route for them is gone. Screenshots at desktop
and phone width:
   python tests/ui_questarr.py [OUT_DIR]   (needs Playwright and its Chromium)"""
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import arr
import auth
import core
import stack
import test_stack as fakes
import uvicorn
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-questarr-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-quest-pass-42!"  # pragma: allowlist secret (made up)
core.db()
auth.create_user("boss", PASSWORD, True)
fake = fakes.FakeStack()
for p in (mock.patch.object(stack.requests, "request", fake), mock.patch.object(arr.requests, "request", fake), mock.patch.object(arr, "STACK", fakes.stack_dir()),
          mock.patch.object(stack, "STACK", fakes.stack_dir()), mock.patch.object(stack, "DATA", fakes.TMP / "media"), mock.patch.dict(os.environ, {"STACK": "1"}),
          mock.patch.object(stack, "INDEXERS", ["yts", "eztv"])):
    p.start()
stack.state.update(results=stack.run("boss", PASSWORD), finished=time.time(), started=time.time() - 5)
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

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, color_scheme="dark")
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/")
    page.wait_for_selector("#gateForm")
    page.fill("#gName", "boss")
    page.fill("#gPw", PASSWORD)
    page.click("#gateForm .primary")
    page.wait_for_selector("#gateForm", state="detached", timeout=30000)
    page.goto(BASE + "/#/settings")
    page.wait_for_selector(".svc", timeout=15000)
    row = page.locator(".svc", has_text="Questarr")
    said = row.inner_text()
    checks["Questarr's row shows its setup steps"] = "connections" in said and "from Prowlarr" in said
    checks["it asks for no keys: no IGDB, no form"] = "IGDB" not in page.content() and "needs" not in said and page.locator("#igdbForm").count() == 0 and row.locator("input").count() == 0
    checks["the old route for the keys is gone"] = page.evaluate(
        """() => fetch("/api/stack/questarr/igdb", {method: "POST", headers: {"X-Requested-With": "parlor", "Content-Type": "application/json"}, body: "{}"}).then((r) => r.status)""") in (404, 405)
    row.scroll_into_view_if_needed()
    page.screenshot(path=str(OUT / "questarr-row.png"))
    phone = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="light", is_mobile=True, has_touch=True, storage_state=ctx.storage_state()).new_page()
    phone.on("pageerror", lambda e: errors.append(f"phone: {e}"))
    phone.goto(BASE + "/#/settings")
    phone.wait_for_selector(".svc", timeout=15000)
    phone.locator(".svc", has_text="Questarr").scroll_into_view_if_needed()
    checks["on a phone the row fits the screen"] = phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    phone.screenshot(path=str(OUT / "questarr-row-phone.png"))
    b.close()
server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
print(f"screenshots: questarr-row.png, questarr-row-phone.png in {OUT}")
ok = len(checks) == 4 and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
