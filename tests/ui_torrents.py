"""Torrents page and /qbit/ in a real browser against a live stack (python tests/live_stack.py --keep first):
   python tests/ui_torrents.py PROJECT OUT_DIR   (needs Playwright and its Chromium)"""
import os, subprocess, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

P, OUT = sys.argv[1], Path(sys.argv[2])
OUT.mkdir(exist_ok=True)
if P.startswith("http"):  # a yeerr already running (a dev server): its address
    BASE = P
else:
    port = subprocess.run(["docker", "port", f"{P}-yeerr-1", "8800"], capture_output=True, text=True).stdout.split(":")[-1].strip()
    BASE = f"http://127.0.0.1:{port}"
NAME, PASSWORD = os.environ.get("YEERR_NAME", "stackadmin"), os.environ.get("YEERR_PASSWORD", "Live-setup-pass-42!")  # pragma: allowlist secret
MAGNET = "magnet:?xt=urn:btih:dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c&dn=Big+Buck+Bunny"  # Blender's film, CC-BY
SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
errors, checks = [], {}
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, color_scheme="dark")
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    page.goto(BASE + "/"); page.wait_for_selector(".gate form")
    page.fill("#gName", NAME); page.fill("#gPw", PASSWORD); page.click(".gate .primary")
    page.wait_for_selector(".home h1", timeout=30000)
    page.goto(BASE + "/#/torrents"); page.wait_for_selector("#torAdd")
    page.fill("#torMagnet", MAGNET); page.click("#torAdd button")
    page.wait_for_selector("#torList .torrent", timeout=30000)
    time.sleep(6)
    checks["listed"] = "Big Buck Bunny" in page.inner_text("#torList") or "big buck bunny" in page.inner_text("#torList").lower()
    page.screenshot(path=str(OUT / "torrents-desk.png"))
    page.click("#torList .torrent [data-t=pause]")
    time.sleep(4)
    checks["paused"] = "Resume" in page.inner_text("#torList")
    page.click("#torList .torrent summary"); time.sleep(0.5)
    page.screenshot(path=str(OUT / "torrents-menu.png"))
    page.click("#torList .torrent [data-t=purge]")
    time.sleep(4)
    checks["removed"] = not page.query_selector("#torList .torrent")
    q = ctx.new_page()
    q.on("pageerror", lambda e: errors.append("qbit: " + str(e)))
    r = q.goto(BASE + "/qbit/")
    time.sleep(5)
    checks["qbit page"] = r.status == 200 and "qBittorrent" in q.title()
    codes = q.evaluate("async () => [await fetch('api/v2/transfer/toggleSpeedLimitsMode', {method: 'POST'}), await fetch('api/v2/transfer/toggleSpeedLimitsMode', {method: 'POST'})].map(r => r.status)")
    checks["its own page can change things"] = codes == [200, 200]
    q.screenshot(path=str(OUT / "qbit-page.png"))
    phone = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="light", storage_state=ctx.storage_state()).new_page()
    phone.goto(BASE + "/#/torrents"); phone.wait_for_selector("#torList"); time.sleep(3)
    phone.screenshot(path=str(OUT / "torrents-phone.png"), full_page=True)
    b.close()
for k, v in checks.items():
    print(("✓ " if v else "✗ ") + k)
print("errors:", errors)
sys.exit(0 if all(checks.values()) and not [e for e in errors if not e.startswith("qbit")] else 1)
