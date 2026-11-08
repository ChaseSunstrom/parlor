"""Settings → Security in a real browser: Parlor runs here on a throwaway data folder with encryption on.
An admin downloads the key (it matches), turns on lock at boot, Parlor "restarts" (its key forgotten),
the page shows the unlock screen, a wrong passphrase is refused, the right one opens everything again.
No page errors; screenshots at desktop and phone width:
   python tests/ui_vault.py [OUT_DIR]   (needs Playwright and its Chromium beside the app's packages)"""
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["ENCRYPT"] = "1"
import yeerr_test_env as env  # noqa: E402,F401  (a throwaway data folder, then the app)

import auth  # noqa: E402
import core  # noqa: E402
import uvicorn  # noqa: E402
import vault  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-vault-"))
OUT.mkdir(parents=True, exist_ok=True)
NAME, PASSWORD, PASSPHRASE = "uiadmin", "Ui-vault-pass-42!", "a long and lovely passphrase"  # pragma: allowlist secret (made up)
vault.KEY_DIR = env.TMP / "key"  # apart from the data folder, as the compose has it
vault.forget()
core.db()
auth.create_user(NAME, PASSWORD, True)

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
BASE = f"http://127.0.0.1:{PORT}"


def restart():
    """What a restart does to the key: forgotten, so a sealed one waits for the passphrase."""
    vault.forget()
    core._local.__dict__.clear()


SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
errors, checks = [], {}
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    for label, size in (("desk", {"width": 1280, "height": 900}), ("phone", {"width": 390, "height": 844})):
        ctx = b.new_context(viewport=size, color_scheme="dark", accept_downloads=True)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(BASE + "/")
        page.wait_for_selector("#gateForm")
        page.fill("#gName", NAME)
        page.fill("#gPw", PASSWORD)
        page.click("#gateForm .primary")
        page.wait_for_selector("#gateForm", state="detached", timeout=30000)
        page.goto(BASE + "/#/settings")
        page.wait_for_selector("#security .panel", timeout=30000)
        checks[f"{label}: Security says the data is encrypted and the key kept apart"] = (
            "encrypted at rest" in page.inner_text("#security") and "kept apart" in page.inner_text("#security"))
        page.locator("#security").scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / f"security-{label}.png"))
        page.fill("#keyPw", PASSWORD)
        with page.expect_download() as dl:
            page.click("#keyForm button")
        checks[f"{label}: the key downloads and is the one in use"] = Path(dl.value.path()).read_text().strip() == vault.key().hex()
        page.fill("#lockPw", PASSWORD)
        page.fill("#lockPass", PASSPHRASE)
        page.click("#lockForm button")
        page.wait_for_selector("#lockForm button:has-text('Turn lock at boot off')", timeout=30000)
        restart()
        page.goto(BASE + "/#/")
        page.reload()
        page.wait_for_selector("#unlockForm", timeout=30000)
        time.sleep(0.6)  # the lock screen fades in
        page.screenshot(path=str(OUT / f"locked-{label}.png"))
        page.fill("#uPass", "not the passphrase")
        page.click("#unlockForm .primary")
        page.wait_for_selector("#uErr:has-text('passphrase')", timeout=30000)
        checks[f"{label}: a wrong passphrase is refused"] = vault.locked()
        page.fill("#uPass", PASSPHRASE)
        page.click("#unlockForm .primary")
        page.wait_for_selector("#unlockForm", state="detached", timeout=30000)
        checks[f"{label}: the right one opens Parlor again"] = not vault.locked() and not page.query_selector("#unlockForm")
        page.goto(BASE + "/#/settings")
        page.wait_for_selector("#lockForm", timeout=30000)
        page.fill("#lockPw", PASSWORD)
        page.click("#lockForm button")
        page.wait_for_selector("#lockPass", timeout=30000)
        checks[f"{label}: lock at boot turns off again"] = not vault.lock_at_boot()
        ctx.close()
    b.close()
server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = len(checks) == 10 and all(checks.values()) and not errors and len(shots) == 4
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
