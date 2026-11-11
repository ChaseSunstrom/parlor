"""The source-audio qualities with a real song: music-fndr (as Parlor runs it) downloads one track from YouTube as
"m4a" and as "opus", and the files hold YouTube's own AAC and Opus streams, not re-encodes (no audio-encoder stamp
from ffmpeg), decode cleanly from start to end, carry their tags, and a throwaway Jellyfin scans both into a music
library with the right title, artist and length. Then the mp3 already there counts as there for the other qualities.
   python tests/live_music_quality.py      (needs the internet, ffmpeg and Docker; a few minutes)"""
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

import requests

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder; the working directory is its music folder)

import music

TRACK = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"  # a song that's been on YouTube since 2009
TAG = f"parlor-quality-{secrets.token_hex(3)}"
NAME, PASSWORD = "qualityadmin", secrets.token_urlsafe(12)  # pragma: allowlist secret (a random login for a throwaway container)
checks, files = {}, {}


def probe(path):
    """(codec, kbps, seconds, the audio stream's own encoder stamp) as ffmpeg reads the file"""
    said = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    audio = re.search(r"Stream #0:\d+.*?: Audio: (\w+)", said)
    if not audio:
        raise SystemExit(f"FAIL: ffmpeg found no audio in {path}: {said.strip()[-300:]}")
    h, m, sec = re.search(r"Duration: (\d+):(\d+):([\d.]+)", said).groups()
    after = said[audio.end():]
    stamp = re.search(r"^\s+encoder\s*: (.+)$", after, re.M | re.I)
    return audio[1], int(re.search(r"bitrate: (\d+) kb/s", said)[1]), int(h) * 3600 + int(m) * 60 + float(sec), stamp[1].strip() if stamp else ""


music.SpotifyClient.init(client_id="", client_secret="", no_cache=True)
song = music.spotify(lambda: music.parse_query([TRACK], threads=1))[0]
for quality, codec in (("m4a", "aac"), ("opus", "opus")):
    music.set_quality(quality, keep=False)
    got = music.make_downloader().download_multiple_songs([song])
    path = Path(got[0][1]).resolve() if got and got[0][1] else None  # absolute: ffmpeg here may run from elsewhere
    checks[f"{quality}: the song downloads as .{quality}"] = bool(path and path.exists() and path.suffix == f".{quality}")
    if not path or not path.exists():
        continue
    files[quality] = path
    found, kbps, seconds, encoder = probe(path)
    print(f"  {path.name}: {found}, {kbps} kbps, {seconds:.0f} s, {path.stat().st_size // 1024} KB, audio encoder stamp: {encoder or 'none'}")
    checks[f"{quality}: it's YouTube's {codec} stream, not a re-encode"] = found == codec and not encoder.startswith("Lavc")
    bad = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True).stderr.strip()
    checks[f"{quality}: it decodes cleanly from start to end"] = not bad and abs(seconds - song.duration) < 20
    tags = music.read_tags(path)
    checks[f"{quality}: it has its tags (and so counts as there)"] = tags.get("name") == song.name and (tags.get("artists") or [""])[0] == song.artists[0]

# what's already there counts as there, whatever the quality now is
by_id, by_name = music.library_songs()
music.set_quality("mp3-320", keep=False)
checks["a song you have in another format isn't fetched again"] = bool(music.copy_we_have(song, by_id, by_name)) and len(files) == 2

# Jellyfin scans both into a music library
if len(files) == 2:
    try:
        subprocess.run(["docker", "run", "-d", "--name", TAG, "-p", "127.0.0.1::8096", "-v", f"{Path.cwd()}:/music:ro", "jellyfin/jellyfin:latest"], check=True, capture_output=True)
        port = subprocess.run(["docker", "port", TAG, "8096"], capture_output=True, text=True, check=True).stdout.splitlines()[0].rsplit(":", 1)[1]
        JF = f"http://127.0.0.1:{port}"

        def wait(what, fn, seconds=240):
            end = time.time() + seconds
            while True:
                try:
                    got = fn()
                    if got:
                        return got
                except requests.RequestException:
                    pass
                if time.time() > end:
                    raise SystemExit(f"FAIL: {what}")
                time.sleep(2)

        wait("Jellyfin didn't come up", lambda: requests.get(f"{JF}/System/Info/Public", timeout=5).json().get("StartupWizardCompleted") is False)
        for method, path, body in (("POST", "/Startup/Configuration", {"UICulture": "en-US", "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en"}),
                                   ("GET", "/Startup/User", None), ("POST", "/Startup/User", {"Name": NAME, "Password": PASSWORD}),
                                   ("POST", "/Startup/RemoteAccess", {"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False}), ("POST", "/Startup/Complete", None)):
            wait(f"Jellyfin {path}", lambda: requests.request(method, JF + path, json=body, timeout=20).ok, 60)
        auth = 'MediaBrowser Client="parlor-test", Device="test", DeviceId="quality", Version="1"'
        token = requests.post(f"{JF}/Users/AuthenticateByName", json={"Username": NAME, "Pw": PASSWORD}, headers={"Authorization": auth}, timeout=30).json()["AccessToken"]
        H = {"Authorization": f'{auth}, Token="{token}"'}
        requests.post(f"{JF}/Library/VirtualFolders", params={"name": "Music", "collectionType": "music", "paths": "/music", "refreshLibrary": "true"},
                      json={"LibraryOptions": {}}, headers=H, timeout=60).raise_for_status()
        seen = []

        def scanned():
            seen[:] = requests.get(f"{JF}/Items", params={"Recursive": "true", "IncludeItemTypes": "Audio", "Fields": "MediaSources,Path"}, headers=H, timeout=30).json()["Items"]
            return seen if len(seen) == 2 and all(i.get("RunTimeTicks") and i.get("Artists") for i in seen) else None  # scanned and probed

        try:
            items = wait("Jellyfin didn't scan both files", scanned, 600)
        except SystemExit:
            print("  Jellyfin has:", [i.get("Path") for i in seen], requests.get(f"{JF}/Library/VirtualFolders", headers=H, timeout=30).text[:300],
                  subprocess.run(["docker", "exec", TAG, "ls", "-la", "/music"], capture_output=True, text=True).stdout[-400:])
            raise
        for it in items:
            print(f"  Jellyfin: {it['Name']} by {', '.join(it.get('Artists') or [])}, {it['RunTimeTicks'] // 10_000_000} s, {it['MediaSources'][0]['Container']}")
        checks["Jellyfin scans both with title, artist and length"] = (
            {Path(i["Path"]).suffix for i in items} == {".m4a", ".opus"}
            and all(i["Name"] == song.name and song.artists[0] in (i.get("Artists") or []) and abs(i["RunTimeTicks"] / 1e7 - song.duration) < 20 for i in items))
    finally:
        subprocess.run(["docker", "rm", "-fv", TAG], capture_output=True)

for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
ok = len(checks) == 10 and all(checks.values())
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
