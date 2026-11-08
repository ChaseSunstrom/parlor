"""Parlor's Android app on Android (an emulator in Docker, with KVM): installs dist/parlor.apk (run scripts/build-android.sh
first) and drives the app's own WebView over its debugging port. The app opens on its address screen and takes this
computer's Parlor (plain http, as on a home network); sign-in goes through the app; an album from the music folder shows
its covers and plays, its stream passed on by the app, seeking included; it keeps playing behind the home screen,
with a media notification; a song saved for offline lands in the app's
own storage; then, with Parlor stopped, the app opens on Saved for offline and the saved song plays from the phone.
Screenshots of each step; the emulator is removed afterwards.
   python tests/live_android.py [OUT_DIR]   (needs Docker, /dev/kvm and ffmpeg; the emulator and its Android 14 system image
   are kept in the parlor-android-emu volume)"""
import base64
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import urlopen

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import core
import uvicorn
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="live-android-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Live-android-pass-42!"  # pragma: allowlist secret (made up)
NAME = "parlor-live-android"
CONSOLE = 5584  # the emulator's console port (and adb's 5585)
APK = ROOT / "dist" / "parlor.apk"
if not APK.exists():
    raise SystemExit("run scripts/build-android.sh first")


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


COVER = Path(tempfile.mkdtemp(prefix="live-android-cover-")) / "cover.jpg"
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x2f6fa8:s=300x300", "-frames:v", "1", str(COVER)], check=True)
for n, (title, hz) in enumerate([("Dawn", 330), ("Pier", 392), ("Gale", 440)], 1):
    folder = Path.cwd() / "The Gulls" / "Harbor"
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration=14", "-i", str(COVER), "-map", "0:a", "-map", "1:v",
                    "-c:a", "libmp3lame", "-b:a", "96k", "-c:v", "copy", "-disposition:v", "attached_pic", "-id3v2_version", "3", "-metadata:s:v", "title=Cover",
                    "-metadata", f"title={title}", "-metadata", "artist=The Gulls", "-metadata", "album_artist=The Gulls", "-metadata", "album=Harbor",
                    "-metadata", f"track={n}", str(folder / f"{n:02d} - {title}.mp3")], check=True)
    assert (folder / f"{n:02d} - {title}.mp3").stat().st_size > 100_000, "a whole song, not a fragment"
core.db()
auth.create_user("phone", PASSWORD, False)
PORT, DEV = port(), port()
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
serving = threading.Thread(target=server.run, daemon=True)
serving.start()
while not server.started:
    time.sleep(0.1)

# the emulator shares this computer's network, so the phone reaches this Parlor at 10.0.2.2 (the emulator's name for it)
BOOT = f"""
# the emulator and Android 14's system image live in a volume, fetched the first time (about 1.5 GB)
if [ ! -x /sdk-emu/emulator/emulator ]; then
  yes | sdkmanager --sdk_root=/sdk-emu --licenses >/dev/null || true
  sdkmanager --sdk_root=/sdk-emu emulator "system-images;android-34;google_apis;x86_64" >/dev/null
fi
# the emulator wants the build image's SDK (platform-tools) and the volume's emulator and system image in one place
ln -sfn /sdk-emu/system-images /opt/android-sdk/system-images
ln -sfn /sdk-emu/emulator /opt/android-sdk/emulator
export ANDROID_HOME=/opt/android-sdk ANDROID_SDK_ROOT=/opt/android-sdk ANDROID_AVD_HOME=/tmp/avd HOME=/tmp
mkdir -p /tmp/avd/p.avd
printf 'avd.ini.encoding=UTF-8\\npath=/tmp/avd/p.avd\\ntarget=android-34\\n' > /tmp/avd/p.ini
printf '%s\\n' image.sysdir.1=system-images/android-34/google_apis/x86_64/ abi.type=x86_64 hw.cpu.arch=x86_64 hw.cpu.ncore=4 \\
  hw.ramSize=4096 hw.lcd.width=1080 hw.lcd.height=2340 hw.lcd.density=440 hw.keyboard=yes hw.gpu.enabled=yes \\
  hw.gpu.mode=swiftshader_indirect disk.dataPartition.size=4G > /tmp/avd/p.avd/config.ini
/opt/android-sdk/emulator/emulator -avd p -no-window -no-audio -no-boot-anim -no-snapshot -gpu swiftshader_indirect -accel on \\
  -ports {CONSOLE},{CONSOLE + 1} > /tmp/emu.log 2>&1 &
export ANDROID_SERIAL=emulator-{CONSOLE}
adb wait-for-device
until [ "$(adb shell getprop sys.boot_completed | tr -d '\\r')" = 1 ]; do sleep 2; done
echo booted
adb install -r /dist/parlor.apk
adb shell am start -n org.parlor.app/.MainActivity
pid=
for i in $(seq 90); do pid=$(adb shell pidof org.parlor.app | tr -d '\\r' || true); [ -n "$pid" ] && break; sleep 1; done
for i in $(seq 60); do adb shell cat /proc/net/unix 2>/dev/null | grep "webview_devtools_remote_$pid" >/dev/null && break; sleep 1; done
adb forward tcp:{DEV} localabstract:webview_devtools_remote_$pid
echo READY
tail -f /dev/null
"""
checks, errors = {}, []



class Tab:
    """the app's WebView over the Chrome DevTools Protocol (Playwright can't attach to a WebView's browser)"""

    def __init__(self):
        self.n = 0
        self.attach()

    def attach(self):
        """the app's page; again when the WebView swaps it (as it does while the app starts)"""
        url = next(t["webSocketDebuggerUrl"] for t in json.load(urlopen(f"http://127.0.0.1:{DEV}/json")) if t["url"].startswith("https://localhost"))
        self.ws = connect(url, max_size=None, open_timeout=30)
        self.call("Runtime.enable")

    def call(self, method, again=True, **params):
        self.n += 1
        try:
            self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
            return self.answer(method)
        except ConnectionClosed:
            if not again:
                raise
            time.sleep(1)
            self.attach()
            return self.call(method, again=False, **params)

    def answer(self, method):
        while True:
            m = json.loads(self.ws.recv(timeout=120))
            if m.get("method") == "Runtime.exceptionThrown":
                errors.append(m["params"]["exceptionDetails"].get("exception", {}).get("description", "?")[:300])
            elif m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(f"{expr[:80]}: {r['exceptionDetails'].get('exception', {}).get('description', r['exceptionDetails'])}")
        return r["result"].get("value")

    def wait(self, expr, seconds=30):
        end = time.time() + seconds
        while time.time() < end:
            try:
                if self.js(f"!!({expr})"):
                    return
            except RuntimeError:
                pass  # the page is between loads
            time.sleep(0.4)
        self.shot("timed-out")
        state = self.js("[dock.audio.currentTime, dock.audio.paused, dock.audio.readyState, dock.audio.networkState, dock.audio.error?.message, dock.audio.src]")
        raise SystemExit(f"timed out waiting for {expr}; the player: {state}")

    def click(self, sel):
        self.js(f"document.querySelector({json.dumps(sel)}).click()")

    def tap(self, sel):
        """a real touch, for what needs one (starting to play)"""
        x, y = self.js(f"(r => [r.x + r.width / 2, r.y + r.height / 2])(document.querySelector({json.dumps(sel)}).getBoundingClientRect())")
        for kind in ("touchStart", "touchEnd"):
            self.call("Input.dispatchTouchEvent", type=kind, touchPoints=[] if kind == "touchEnd" else [{"x": x, "y": y}])

    def fill(self, sel, value):
        self.js(f"(e => {{ e.value = {json.dumps(value)}; e.dispatchEvent(new Event('input', {{ bubbles: true }})); }})(document.querySelector({json.dumps(sel)}))")

    def shot(self, name):
        time.sleep(0.6)
        (OUT / f"{name}.png").write_bytes(base64.b64decode(self.call("Page.captureScreenshot")["data"]))


def adb(*args):
    return subprocess.run(["docker", "exec", "-e", f"ANDROID_SERIAL=emulator-{CONSOLE}", NAME, "adb", *args], capture_output=True, text=True).stdout


try:
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", NAME, "--network", "host", "--device", "/dev/kvm", "--cpus", "4",
                    "-v", "parlor-android-emu:/sdk-emu", "-v", f"{APK.parent}:/dist:ro", "parlor-android-emu:1", "bash", "-euo", "pipefail", "-c", BOOT],
                   check=True, capture_output=True)
    started = time.time()
    while "READY" not in (logs := subprocess.run(["docker", "logs", NAME], capture_output=True, text=True).stdout):
        if time.time() - started > 900 or subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", NAME], capture_output=True, text=True).stdout.strip() != "true":
            raise SystemExit("the emulator didn't come up:\n" + logs[-2000:] + subprocess.run(["docker", "exec", NAME, "tail", "-20", "/tmp/emu.log"],
                                                                                                 capture_output=True, text=True).stdout)
        time.sleep(3)
    print(f"emulator up in {time.time() - started:.0f} s")

    tab = Tab()

    # the address screen; then this computer's Parlor, plain http
    tab.wait("document.querySelector('#serverForm')", 60)
    checks["the app opens on its address screen"] = True
    tab.shot("1-address")
    tab.fill("#serverUrl", "10.0.2.2:1")  # nothing there: it says so
    tab.click("#serverForm .primary")
    tab.wait("document.querySelector('#serverErr')?.textContent.length > 0")
    refused = tab.js("document.querySelector('#serverErr').textContent")
    tab.fill("#serverUrl", f"http://10.0.2.2:{PORT}")
    tab.click("#serverForm .primary")
    tab.wait("document.querySelector('#gateForm')")
    tab.fill("#gName", "phone")
    tab.fill("#gPw", PASSWORD)
    tab.click("#gateForm .primary")
    tab.wait("!document.querySelector('#gateForm') && ME && ME.name === 'phone'")
    tab.shot("2-home")
    checks["it takes this computer's Parlor (plain http) and signs in through the app"] = "Nothing answered" in refused and tab.js("SERVER") == f"http://10.0.2.2:{PORT}"

    # an album from the music folder: its cover and the stream come through the app; seeking works
    tab.js("location.hash = '#/library/The%20Gulls/Harbor'")
    tab.wait("document.querySelector('.jtracks [data-local-more]')")
    tab.tap(".hero [data-local-play]")
    tab.wait("!dock.audio.paused && dock.audio.currentTime > 0.5")
    tab.js("dock.audio.currentTime = 9")
    tab.wait("dock.audio.currentTime > 9.3 && !dock.audio.paused", 15)
    tab.wait("document.querySelector('.hero img')?.naturalWidth > 0", 15)
    tab.shot("3-album")
    checks["an album shows its cover and plays through the app, seeking included"] = tab.js("dock.audio.src").startswith("https://localhost/api/jf/music/")

    # Home: it keeps playing in the background, with a media notification
    tab.js("dock.audio.currentTime = 2")  # well before the end, so it's still this song after the wait
    time.sleep(0.5)
    at = tab.js("dock.audio.currentTime")
    adb("shell", "input", "keyevent", "KEYCODE_HOME")
    time.sleep(4)
    note = adb("shell", "dumpsys", "notification", "--noredact")
    moved = tab.js("[dock.audio.paused, dock.audio.currentTime]")
    adb("shell", "am", "start", "-n", "org.parlor.app/.MainActivity")
    print(f"in the background: paused={moved[0]}, {at:.1f} s -> {moved[1]:.1f} s; media notification: {'org.parlor.app' in note and 'Dawn' in note}")
    checks["Home: it keeps playing, with a media notification for the lock screen"] = not moved[0] and moved[1] > at + 2 and "org.parlor.app" in note and "Dawn" in note

    # save a song for offline: it lands in the app's own storage
    tab.js("queueShow(false)")
    tab.click(".jtracks [data-local-more='1']")
    tab.wait("document.querySelector('#queueMenu [data-qm=save]')", 5)
    tab.click("#queueMenu [data-qm=save]")
    tab.wait("offMine().length === 1 && (offMine()[0].done || offMine()[0].error)", 60)
    saved = tab.js("offMine()[0]")
    print("saved:", {k: saved.get(k) for k in ("title", "done", "error", "size")})
    checks["a song saved for offline lands in the app's storage"] = bool(saved["done"]) and saved["size"] == (Path.cwd() / "The Gulls/Harbor/02 - Pier.mp3").stat().st_size

    # Parlor stopped: the app opens on Saved for offline and the song plays from the phone
    tab.js("dock.audio.pause()")
    server.should_exit = True
    serving.join(15)
    tab.call("Page.reload")
    time.sleep(2)
    tab.wait("[...document.querySelectorAll('h1')].some((h) => h.textContent.includes(\"You're offline\"))", 60)
    tab.tap(".offalbum [data-off-play]")
    tab.wait("dock.audio.src.startsWith('blob:') && !dock.audio.paused && dock.audio.currentTime > 0.5")
    tab.shot("4-offline")
    checks["with Parlor stopped, the app opens on Saved for offline and the saved song plays from the phone"] = tab.js("dock.queue[dock.at].title") == "Pier"
finally:
    if os.environ.get("HOLD"):  # keep the emulator and Parlor up to look around (the DevTools port is printed)
        print(f"holding: DevTools at http://127.0.0.1:{DEV}/json, Parlor at http://127.0.0.1:{PORT}", flush=True)
        time.sleep(int(os.environ["HOLD"]))
    crash = adb("logcat", "-d", "-b", "crash")
    log = [x for x in adb("logcat", "-d", "*:W").splitlines() if re.search(r"parlor|Capacitor|MediaSession|AndroidRuntime|chromium.*(Uncaught|Error)", x) and "simple_" not in x]
    if crash.strip() or log:
        print("logcat:\n" + "\n".join((crash.splitlines() + log)[-25:]))
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
print("screenshots:", ", ".join(sorted(x.name for x in OUT.glob("*.png"))))
ok = len(checks) == 6 and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
