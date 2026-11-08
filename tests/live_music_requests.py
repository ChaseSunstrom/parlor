"""Music requests against a real Lidarr: starts a throwaway Lidarr (Docker, with the internet for its metadata),
requests an album through Parlor's own route, and lets the ledger follow it. Lidarr has no indexers, so it finds
nothing; six hours on (the clock is moved, not waited), the ledger hands it over: Lidarr stops looking and nothing of
it is left in Lidarr's queue. music-fndr's download is played by a stand-in that writes what music-fndr would (one
tagged file per track of the plain album, at the right lengths) into the staging folder Parlor gave it; then Parlor
tags the files with the album's ids and Lidarr imports them, told which track each file is, and the request ends imported, with the album watched
again. The container and its folders are removed afterwards.
   python tests/live_music_requests.py [MUSICBRAINZ_RELEASE_GROUP]   (needs Docker, ffmpeg and the internet)"""
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import arr
import core
import music
import musicreq
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TPE2, TRCK
from yeerr_test_env import H, signed_in

ALBUM = sys.argv[1] if len(sys.argv) > 1 else "b1392450-e666-3926-a536-22c65f834433"  # OK Computer
ROOT = Path(tempfile.mkdtemp(prefix="live-music-requests-"))
DATA = ROOT / "data"
NAME = "parlor-live-music-requests-lidarr"
for d in ("config", "data/music", "data/downloads/parlor-music"):
    (ROOT / d).mkdir(parents=True)
subprocess.run(["chmod", "-R", "a+rwX", str(ROOT)], check=True)
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
checks = {}


def lidarr(method, path, **kw):
    return arr.call("lidarr", method, path, timeout=120, **kw)


def wait(what, check, seconds=300, every=3):
    end = time.time() + seconds
    while time.time() < end:
        try:
            if got := check():
                return got
        except arr.ArrError:
            pass
        time.sleep(every)
    raise SystemExit(f"timed out waiting for {what}")


try:
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    # the data folder at the same path inside, so the staging folder's path means the same thing to both
    subprocess.run(["docker", "run", "-d", "--name", NAME, "-e", "PUID=1000", "-e", "PGID=1000", "-p", f"127.0.0.1:{PORT}:8686",
                    "-v", f"{ROOT / 'config'}:/config", "-v", f"{DATA}:{DATA}", "lscr.io/linuxserver/lidarr:latest"], check=True, capture_output=True)
    key = wait("Lidarr's API key", lambda: (ROOT / "config/config.xml").exists() and (re.search(r"<ApiKey>(\w+)</ApiKey>", (ROOT / "config/config.xml").read_text()) or [0, 0])[1])
    core.set_setting("lidarr_url", f"http://127.0.0.1:{PORT}")
    core.set_setting("lidarr_key", key)
    wait("Lidarr to answer", lambda: lidarr("GET", "/system/status"), 180)
    lidarr("POST", "/rootfolder", json={"name": "Music", "path": str(DATA / "music"), "defaultQualityProfileId": 1, "defaultMetadataProfileId": 1,
                                        "defaultMonitorOption": "all", "defaultNewItemMonitorOption": "all", "defaultTags": []})
    me = signed_in("live-listener")
    with mock.patch.object(musicreq, "STAGING", DATA / "downloads" / "parlor-music"):
        r = me.post("/api/arr/music/request", json={"type": "album", "id": ALBUM}, headers=H)
        assert r.status_code == 200, r.text
        rid = core.one("SELECT id FROM music_requests ORDER BY id DESC LIMIT 1")["id"]
        album_id = core.one("SELECT album FROM music_requests WHERE id = ?", rid)["album"]
        album = wait("the album's releases", lambda: (a := lidarr("GET", f"/album/{album_id}")).get("releases") and a, 300)
        musicreq.tick(time.time() + 60)
        checks["the request is followed (searching)"] = core.one("SELECT state FROM music_requests WHERE id = ?", rid)["state"] == "searching"
        checks["an album Lidarr stopped watching is watched again"] = wait("monitoring", lambda: lidarr("GET", f"/album/{album_id}")["monitored"], 60)

        # Lidarr finds nothing (no indexers): six hours later it's handed over to YouTube
        release = min((r for r in album["releases"] if r.get("trackCount")), key=lambda r: (r["trackCount"], r["id"]))  # the plain album
        tracks = lidarr("GET", "/track", params={"albumReleaseId": release["id"]})
        artist, title = album["artist"]["artistName"], album["title"]
        jobs = []

        def queue_download(kind, id, title=None, subtitle=None, image=None, output=None):
            jobs.append({"job": "live1", "id": id, "output": output, "status": "downloading", "songs": {}})
            return jobs[-1]

        spotify = {"albums": [{"id": "6dVIqQ8qmQ5GBnJ9shOYGE", "name": title, "artists": [{"name": artist}], "year": album["releaseDate"][:4], "image": None}]}
        with mock.patch.object(music, "search", lambda q: spotify), mock.patch.object(music, "album", lambda sid: {"total": len(tracks)}), \
                mock.patch.object(music, "queue_download", queue_download), mock.patch.object(music, "jobs", jobs):
            musicreq.tick(time.time() + 6 * 3600 + 120)
            row = core.one("SELECT * FROM music_requests WHERE id = ?", rid)
            after = lidarr("GET", f"/album/{album_id}")
            left = lidarr("GET", "/queue/details", params={"albumIds": album_id})
            checks["after 6 h with nothing found it goes to YouTube, Lidarr stopped and its queue empty"] = (
                row["state"] == "fetching" and not after["monitored"] and left == [] and jobs and jobs[0]["output"].startswith(str(DATA / "downloads" / "parlor-music")))
            # music-fndr's part, played by a stand-in: one tagged file per track, where the job's template says
            for t in tracks:
                n = int(t["absoluteTrackNumber"])
                path = Path(jobs[0]["output"].replace("{album-artist}", artist).replace("{album}", title).replace("{track-number}", f"{n:02d}")
                            .replace("{title}", re.sub(r'[\\/:*?"<>|]', "_", t["title"])).replace("{output-ext}", "mp3"))
                path.parent.mkdir(parents=True, exist_ok=True)
                secs = max(1, round((t.get("duration") or 180000) / 1000))
                subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={secs}", "-ac", "1", "-b:a", "32k", str(path)], check=True)
                tags = ID3()
                for frame in (TIT2(encoding=3, text=t["title"]), TPE1(encoding=3, text=artist), TPE2(encoding=3, text=artist), TALB(encoding=3, text=title),
                              TRCK(encoding=3, text=f"{n}/{len(tracks)}")):
                    tags.add(frame)
                tags.save(path)
            subprocess.run(["chmod", "-R", "a+rwX", str(DATA / "downloads")], check=True)
            jobs[0].update(status="done", songs={t["id"]: {"status": "done"} for t in tracks})
            musicreq.tick(time.time() + 6 * 3600 + 900)
            checks["Parlor tags the files and asks Lidarr to import them"] = core.one("SELECT state FROM music_requests WHERE id = ?", rid)["state"] == "importing"

            def landed():
                musicreq.tick(time.time() + 6 * 3600 + 3600)
                return core.one("SELECT state FROM music_requests WHERE id = ?", rid)["state"] in ("imported", "failed")
            wait("the import", landed, 300, every=10)
            row = core.one("SELECT * FROM music_requests WHERE id = ?", rid)
            final = lidarr("GET", f"/album/{album_id}")
            print("ledger:", row["state"], "|", row["why"], "| Lidarr:", final["statistics"])
            checks["Lidarr imported it and the request ends imported"] = row["state"] == "imported" and final["statistics"]["trackFileCount"] >= len(tracks)
            checks["the album is watched again, so Lidarr can find a better copy later"] = final["monitored"]
            left = sorted(str(p.relative_to(DATA)) for p in (DATA / "downloads" / "parlor-music").rglob("*"))
            print("left in staging:", left[:10])
            checks["the staging folder is cleaned up"] = not (DATA / "downloads" / "parlor-music" / f"request-{rid}").exists()
finally:
    logs = subprocess.run(["docker", "logs", "--tail", "60", NAME], capture_output=True, text=True).stdout
    print("\n".join(line for line in logs.splitlines() if re.search(r"Import|reject|Reject|Unable|match|Warn|Error", line))[-2500:])
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    subprocess.run(["rm", "-rf", str(ROOT)])
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
ok = len(checks) == 7 and all(checks.values())
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
