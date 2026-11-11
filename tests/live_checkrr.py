"""Checkrr for real: the pinned image, started the way the compose starts it, on the config Parlor writes, beside a
real Radarr. A film Radarr imported whole and that then went bad on disk (its bytes overwritten) is found by a scan,
deleted through Radarr and searched for again; with replacing off it is only listed; a good film, a good song and a
subtitle are left alone; and Checkrr restarts by itself when Parlor rewrites its config (first with no *arr, as on a first start, then with Radarr's key).
   python tests/live_checkrr.py      (needs Docker, ffmpeg and the internet: Radarr looks the films up; a few minutes)"""
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

import requests

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import stack

REPO = Path(__file__).resolve().parent.parent
TAG = f"parlor-checkrr-{secrets.token_hex(3)}"
ROOT = Path(tempfile.mkdtemp(prefix="parlor-checkrr-"))
COMPOSE = (REPO / "docker-compose.yml").read_text()
BLOCK = re.search(r"^  checkrr:.*\n((?:    .*\n|\n)+)", COMPOSE, re.M)[0]
IMAGE = re.search(r"image: (aetaric/checkrr\S+)", BLOCK)[1]
START = re.search(r"^      - '(was=.*)'$", BLOCK, re.M)[1].replace("$$", "$")  # the compose's own start line
RADARR = re.search(r"image: (\S*radarr\S*)", COMPOSE)[1]
WHO = f"{os.getuid()}:{os.getgid()}"
BAD, LISTED, GOOD = 603, 605, 604  # three films Radarr can look up
checks = {}


def sh(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def port(name, inner):
    return sh("docker", "port", name, str(inner)).splitlines()[0].rsplit(":", 1)[1]


def wait(what, fn, seconds=240):
    end = time.time() + seconds
    while True:
        try:
            got = fn()
            if got:
                return got
        except (requests.RequestException, OSError, ValueError, TypeError):
            pass
        if time.time() > end:
            raise SystemExit(f"FAIL: {what}")
        time.sleep(2)


def clip(path, audio_only=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    video = [] if audio_only else ["-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=24"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *video, "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(path)], check=True)


for d in ("config", "radarr", "movies", "shows", "music"):
    (ROOT / d).mkdir()
conf = ROOT / "config" / "checkrr.yaml"
try:
    sh("docker", "network", "create", TAG)
    sh("docker", "run", "-d", "--name", f"{TAG}-radarr", "--network", TAG, "--network-alias", "radarr", "-p", "127.0.0.1::7878",
       "-e", f"PUID={os.getuid()}", "-e", f"PGID={os.getgid()}", "-v", f"{ROOT / 'radarr'}:/config", "-v", f"{ROOT / 'movies'}:/data/movies", RADARR)
    # a first start: the config setup's one-shot writes, with no *arr yet
    conf.write_text(stack.checkrr_config([], True))
    sh("docker", "run", "-d", "--name", f"{TAG}-checkrr", "--network", TAG, "--restart", "unless-stopped", "--user", WHO, "-p", "127.0.0.1::8585",
       "-v", f"{ROOT / 'config'}:/config", *[x for k in ("movies", "shows", "music") for x in ("-v", f"{ROOT / k}:/data/{k}:ro")],
       "--entrypoint", "/bin/sh", IMAGE, "-c", START)
    ck = lambda: f"http://127.0.0.1:{port(f'{TAG}-checkrr', 8585)}"  # noqa: E731  (the port changes when it restarts)
    checks["Checkrr starts on the config of a first start"] = bool(wait("Checkrr didn't start", lambda: requests.get(ck() + "/api/schedule", timeout=5).ok, 90))

    # Radarr: its key, a root folder, two films, each with a file it imports whole
    key = wait("Radarr wrote no API key", lambda: re.search(r"<ApiKey>(\w+)</ApiKey>", (ROOT / "radarr" / "config.xml").read_text())[1])
    R, RH = f"http://127.0.0.1:{port(f'{TAG}-radarr', 7878)}/api/v3", {"X-Api-Key": key}
    wait("Radarr didn't come up", lambda: requests.get(f"{R}/system/status", headers=RH, timeout=5).ok)
    requests.post(f"{R}/rootfolder", headers=RH, json={"path": "/data/movies"}, timeout=30).raise_for_status()
    profile = requests.get(f"{R}/qualityprofile", headers=RH, timeout=30).json()[0]["id"]
    films = {}
    for tmdb in (BAD, LISTED, GOOD):
        found = wait(f"Radarr couldn't look up film {tmdb}", lambda: requests.get(f"{R}/movie/lookup/tmdb", params={"tmdbId": tmdb}, headers=RH, timeout=60).json(), 120)
        r = requests.post(f"{R}/movie", headers=RH, timeout=60, json={**found, "rootFolderPath": "/data/movies", "qualityProfileId": profile, "monitored": True,
                                                                       "addOptions": {"searchForMovie": False}})
        r.raise_for_status()
        m = r.json()
        name = Path(m["path"]).name
        clip(ROOT / "movies" / name / f"{name} Bluray-1080p.mkv")
        films[tmdb] = {"id": m["id"], "file": ROOT / "movies" / name / f"{name} Bluray-1080p.mkv"}
    (films[GOOD]["file"].with_suffix(".srt")).write_text("1\n00:00:00,000 --> 00:00:02,000\nhello\n")
    clip(ROOT / "music" / "Artist" / "Album" / "01 - Song.flac", audio_only=True)
    requests.post(f"{R}/command", headers=RH, json={"name": "RefreshMovie", "movieIds": [f["id"] for f in films.values()]}, timeout=30).raise_for_status()
    has = lambda tmdb: requests.get(f"{R}/movie/{films[tmdb]['id']}", headers=RH, timeout=30).json()["hasFile"]  # noqa: E731
    wait("Radarr didn't import the two films", lambda: has(BAD) and has(LISTED) and has(GOOD), 180)

    # setup's step: the same file, now with Radarr and its key; Checkrr must restart on it by itself
    def write(replace):
        started = sh("docker", "inspect", "-f", "{{.State.StartedAt}}", f"{TAG}-checkrr")
        stack._write_private(conf, stack.checkrr_config([("radarr", "radarr", 7878, key, "/data/movies")], replace))
        wait("Checkrr didn't restart on the new config", lambda: sh("docker", "inspect", "-f", "{{.State.StartedAt}}", f"{TAG}-checkrr") != started
             and requests.get(ck() + "/api/schedule", timeout=5).ok, 120)

    write(True)
    checks["Checkrr restarts by itself when its config changes"] = True

    def scan():
        requests.post(ck() + "/api/run", timeout=30).raise_for_status()
        time.sleep(3)
        wait("the scan didn't finish", lambda: requests.get(ck() + "/api/stats/current", timeout=10).json()["running"] is False, 300)
        return requests.get(ck() + "/api/stats/current", timeout=10).json()

    first = scan()
    print(f"  a scan of good files: {first}")
    checks["a scan of good files replaces and lists nothing"] = (first["filesChecked"] >= 3 and first["radarrSubmissions"] == 0 and first["unknownFileCount"] == 0
                                                             and not requests.get(ck() + "/api/files/bad", timeout=10).json())

    # report only (the switch in Settings → Services, off): a film that goes bad is listed and nothing is deleted
    write(False)
    films[LISTED]["file"].write_bytes(secrets.token_bytes(300_000))
    only = scan()
    print(f"  a report-only scan after one film went bad: {only}")
    listed = [x["Path"] for x in requests.get(ck() + "/api/files/bad", timeout=10).json()]
    checks["report only: the broken film is listed, and Radarr keeps its file"] = (only["radarrSubmissions"] == 0 and any(films[LISTED]["file"].name in x for x in listed)
                                                                               and films[LISTED]["file"].exists() and has(LISTED))
    write(True)

    # the film goes bad on disk
    films[BAD]["file"].write_bytes(secrets.token_bytes(300_000))
    second = scan()
    print(f"  a scan after one film went bad: {second}")
    checks["the broken film is handed to Radarr"] = second["radarrSubmissions"] >= 1
    print(f"  the film listed while report only, after replacing came back on: file {'still there' if films[LISTED]['file'].exists() else 'deleted by Radarr'}")
    wait("Radarr still has the broken film's file", lambda: not has(BAD), 120)
    checks["Radarr deleted the broken file"] = not films[BAD]["file"].exists()
    names = wait("Radarr got no search", lambda: [c["name"] for c in requests.get(f"{R}/command", headers=RH, timeout=30).json() if c["name"] == "MoviesSearch"], 60)
    checks["Radarr was told to search for a new copy"] = bool(names)
    checks["the good film, the song and the subtitle are still there"] = (has(GOOD) and films[GOOD]["file"].exists() and films[GOOD]["file"].with_suffix(".srt").exists()
                                                                      and (ROOT / "music" / "Artist" / "Album" / "01 - Song.flac").exists())
    with mock.patch.object(stack, "checkrr_url", ck):  # what Settings → Services shows, read from the real thing
        last = stack.checkrr_last_scan()
    print(f"  Parlor reads the last scan as: {last}")
    checks["Parlor reads the last scan: what was broken and replaced"] = bool(last) and last["replaced"] >= 1 and last["broken"] >= last["replaced"] and last["checked"] >= 4 and re.match(r"\d{4}-\d\d-\d\dT", last["when"]) is not None
    began = time.time()
    sh("docker", "stop", f"{TAG}-checkrr")
    checks["docker stop reaches Checkrr at once"] = time.time() - began < 8
finally:
    subprocess.run(["docker", "rm", "-fv", f"{TAG}-radarr", f"{TAG}-checkrr"], capture_output=True)
    subprocess.run(["docker", "network", "rm", TAG], capture_output=True)

for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
ok = len(checks) == 10 and all(checks.values())
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
