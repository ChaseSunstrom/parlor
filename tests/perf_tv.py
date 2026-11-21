"""How much Parlor's TV page costs to draw on a slow TV: a real Chromium at 3840×2160 with a software GPU (SwiftShader)
and the page's thread slowed down, a TV paired the usual way, then the frame times (the gaps between the page's
animation frames) while it sits on Home, while the focus walks along a row, down the rows and back up them; and how
long a press takes from the key to the frame that shows it. A film library of 40 comes from the Jellyfin stand-in.
   cd app && ../.venv/bin/python ../tests/perf_tv.py [--cpu 4] [--size 3840x2160] [--budget MS] [OUT_DIR]
Prints one line per scene; with --budget it fails when a scene's median frame is slower than that."""
import os
import socket
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("JF_STANDIN_FILMS", "40")
import yeerr_test_env as env  # noqa: E402,F401  (a throwaway data folder, then the app)

import auth  # noqa: E402
import core  # noqa: E402
import httpx  # noqa: E402
import jf_standin as jfs  # noqa: E402
import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from yeerr_test_env import H  # noqa: E402


def opt(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


CPU, SIZE, BUDGET = float(opt("--cpu", 4)), opt("--size", "3840x2160"), float(opt("--budget", 0))
rest = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and not sys.argv[i - 1].startswith("--")]
OUT = Path(rest[0] if rest else tempfile.mkdtemp(prefix="perf-tv-"))
OUT.mkdir(parents=True, exist_ok=True)
W, Hh = (int(x) for x in SIZE.split("x"))
PASSWORD = "Perf-tv-pass-42!"  # pragma: allowlist secret (made up)
core.db()
auth.create_user("boss", PASSWORD, True)
jfs.point(core)
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
BASE = f"http://127.0.0.1:{PORT}"

CHROME = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux*/chrome"), reverse=True)), None)  # the whole browser: it composites like a TV's

FRAMES = """(ms) => new Promise((done) => { const gaps = []; let last = performance.now(); const end = last + ms;
  const tick = (now) => { gaps.push(now - last); last = now; now < end ? requestAnimationFrame(tick) : done(gaps); };
  requestAnimationFrame(tick); })"""
PRESS = """(key) => new Promise((done) => { const t = performance.now();
  document.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  requestAnimationFrame(() => requestAnimationFrame((now) => done(now - t))); })"""  # the frame after the one that drew it


def line(name, gaps):
    gaps = sorted(gaps[1:]) or [0]
    med, p95 = statistics.median(gaps), gaps[int(len(gaps) * 0.95) - 1]
    print(f"  {name:34} median {med:6.1f} ms   p95 {p95:6.1f} ms   worst {gaps[-1]:6.1f} ms   {len(gaps)} frames, {sum(g > 50 for g in gaps)} over 50 ms")
    return med


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=str(CHROME) if CHROME else None, args=["--headless=new", "--autoplay-policy=no-user-gesture-required", "--use-angle=swiftshader", "--ignore-gpu-blocklist", "--enable-gpu-rasterization"])
    ctx = b.new_context(viewport={"width": W, "height": Hh}, color_scheme="dark")
    screen = ctx.new_page()
    errors = []
    screen.on("pageerror", lambda e: errors.append(str(e)))
    screen.goto(BASE + "/tv")
    screen.wait_for_selector("#tvCode", timeout=30000)
    code = screen.inner_text("#tvCode").replace("\n", "").replace(" ", "")
    owner = httpx.Client(base_url=BASE, headers=H, timeout=10)
    assert owner.post("/api/auth/login", json={"name": "boss", "password": PASSWORD}).status_code == 200
    assert owner.post("/api/tv/pair", json={"code": code, "name": "Living room"}).status_code == 200
    screen.wait_for_function("ME && tvMe && document.body.classList.contains('tv')", timeout=30000)
    if os.environ.get("PERF_CSS"):  # try a rule's cost without editing the stylesheet
        screen.add_style_tag(content=os.environ["PERF_CSS"])
    screen.wait_for_timeout(4000)  # shelves and pictures in
    cdp = ctx.new_cdp_session(screen)
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": CPU})
    gpu = b.new_browser_cdp_session().send("SystemInfo.getInfo")["gpu"]["devices"]
    print(f"Chromium {b.version} at {W}×{Hh}, page thread slowed {CPU:g}×, GPU: {gpu[0]['deviceString'] if gpu else 'none'}")
    meds = {}
    screen.screenshot(path=str(OUT / "perf-home.png"))
    meds["Home, nothing pressed (3 s)"] = line("Home, nothing pressed (3 s)", screen.evaluate(FRAMES, 3000))
    for scene, key, n in (("focus along a row (12 presses)", "ArrowRight", 12), ("focus down the rows (8 presses)", "ArrowDown", 8),
                           ("focus back up the rows (8 presses)", "ArrowUp", 8)):
        took, gaps = [], []
        for _ in range(n):
            took.append(screen.evaluate(PRESS, key))
            gaps += screen.evaluate(FRAMES, 250)[1:]
        meds[scene] = line(scene, [0] + gaps)
        took.sort()
        print(f"  {'  a press to its frame':34} median {statistics.median(took):6.1f} ms   worst {took[-1]:6.1f} ms")
    screen.screenshot(path=str(OUT / "perf-after.png"))
    b.close()
server.should_exit = True
print("page errors:", errors or "none")
slow = {k: v for k, v in meds.items() if BUDGET and v > BUDGET}
print("RESULT:", "ok" if not slow and not errors else f"FAIL: slower than {BUDGET:g} ms: {slow or errors}")
sys.exit(1 if slow or errors else 0)
