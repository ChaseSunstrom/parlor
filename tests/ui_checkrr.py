"""Checkrr's row in Settings → Services in a real browser, against the fake stack from test_stack.py: it shows the
last scan, a link to Checkrr's own page and the Replace broken files switch; switching it off rewrites Checkrr's
config to only list, and a config of your own hides the switch. Screenshots at desktop and phone width:
   python tests/ui_checkrr.py [OUT_DIR]   (needs Playwright and its Chromium)"""
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

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-checkrr-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-check-pass-42!"  # pragma: allowlist secret (made up)
core.db()
auth.create_user("boss", PASSWORD, True)
fake = fakes.FakeStack()
fake.scans = [{"Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 3600)),
               "Data": {"filesChecked": 1210, "radarrSubmissions": 1, "sonarrSubmissions": 0, "lidarrSubmissions": 0, "unknownFileCount": 2}}]
for p in (mock.patch.object(stack.requests, "request", fake), mock.patch.object(arr.requests, "request", fake), mock.patch.object(arr, "STACK", fakes.stack_dir()),
          mock.patch.object(stack, "STACK", fakes.stack_dir()), mock.patch.object(stack, "DATA", fakes.TMP / "media"), mock.patch.dict(os.environ, {"STACK": "1"}),
          mock.patch.object(stack, "INDEXERS", ["yts", "eztv"])):
    p.start()
stack.checkrr_file().unlink(missing_ok=True)
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
    page.wait_for_selector("#checkrrReplace", timeout=15000)
    row = page.locator(".svc", has_text="Checkrr")
    said = row.inner_text()
    checks["the row has the last scan and what it did"] = "last scan 3 hours ago: 1210 files, 2 broken, 1 replaced" in said
    checks["it links to Checkrr's own page on this server"] = row.locator("a", has_text="broken files").get_attribute("href") == "http://127.0.0.1:8585/"
    checks["replacing is on, as set up"] = page.is_checked("#checkrrReplace") and "replaces broken files" in said
    row.scroll_into_view_if_needed()
    page.screenshot(path=str(OUT / "checkrr-row.png"))
    with page.expect_response(lambda r: r.url.endswith("/api/stack/checkrr"), timeout=15000):
        page.click("#checkrrReplace")
    page.wait_for_function("() => [...document.querySelectorAll('.toast')].some((e) => e.textContent.includes('only lists'))", timeout=8000)
    text = stack.checkrr_file().read_text()
    checks["switching it off makes Checkrr only list"] = text.count("\n    process: false") == 3 and "\n    process: true" not in text
    checks["no API key is in the page"] = not any(k in page.content() for k in fakes.KEYS.values())
    phone = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="light", is_mobile=True, has_touch=True, storage_state=ctx.storage_state()).new_page()
    phone.on("pageerror", lambda e: errors.append(f"phone: {e}"))
    phone.goto(BASE + "/#/settings")
    phone.wait_for_selector("#checkrrReplace", timeout=15000)
    phone.locator("#checkrrReplace").scroll_into_view_if_needed()
    checks["on a phone the row fits and the switch stayed off"] = (phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
                                                                and not phone.is_checked("#checkrrReplace"))
    phone.screenshot(path=str(OUT / "checkrr-row-phone.png"))
    stack.checkrr_file().write_text("checkrr: {}\n")  # someone took the config over
    phone.reload()
    phone.wait_for_selector(".svc:has-text('Checkrr')", timeout=15000)
    phone.wait_for_function("() => document.querySelector('.svc-list')?.textContent.includes('your own config')", timeout=15000)
    checks["a config of your own hides the switch"] = phone.locator("#checkrrReplace").count() == 0
    b.close()
server.should_exit = True
stack.checkrr_file().unlink(missing_ok=True)
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
print(f"screenshots: checkrr-row.png, checkrr-row-phone.png in {OUT}")
ok = len(checks) == 7 and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
