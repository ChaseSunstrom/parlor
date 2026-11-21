# Development

[← Parlor's README](../README.md)

How to run Parlor from source, run its tests, and where things are.

## Running it and testing

```sh
uv venv -p 3.12 .venv && VIRTUAL_ENV=.venv uv pip install "spotdl~=4.5.2" "spotapi~=1.2.8" "segno~=1.6" "httpx<0.28" -U "yt-dlp[default]"
mkdir -p dev/music && cd dev/music && DATA_DIR=../data VIDEO_DIR=../videos ../../.venv/bin/uvicorn --app-dir ../../app app:app --reload --port 8800
cd app && ../.venv/bin/python -m unittest discover -s ../tests   # unit tests, no network
python3 tests/smoke.py http://127.0.0.1:8800                     # live check against a running Parlor
```

## Where things are

| File | What it is |
|---|---|
| `app/app.py` | Puts it together. The next five sit beside it in `app/`. |
| `auth.py` | Accounts and settings. |
| `yt.py` | YouTube. |
| `seerr.py` | Seerr. |
| `bulk.py` | Whole collections through Seerr. |
| `core.py` | The database and sessions. |
| `app/music.py` | music-fndr's backend, copied by `scripts/vendor-music.sh ../music-fndr`. Change it in music-fndr, then copy it again. |
| `app/static/` | The whole UI, with no build step. |
| `js/music.js` | music-fndr's page code (in `app/static/`). |
| `tv.py`, `js/tv.js`, `js/remote.js` | TVs: pairing, the event stream and commands (`tv.py`); Parlor on the TV, where everything that changes it goes through `tvDo` (`tv.js`); the phone's side, where every command goes through `tvSend` (`remote.js`). `tvhw.py` and `cast.py` work the TV set itself. |
| `js/tvhome.js` | What a TV shows: Home (the hero and the rows), the full-screen player's strip and options, search with a keyboard on the screen, and the idle clock. Each screen's keys are asked first, through `tvScreenKey`. |
| `tests/ui_tv.py` | Drives the TV page with arrows, OK and Back in a real browser, and a phone's remote with `--remote`; saves screenshots. |
| `tests/perf_tv.py` | Measures what the TV page costs to draw at 4K on a slow graphics chip. `PERF_CSS='…'` adds a rule for one run, to see what it costs. |
| [DESIGN.md](../DESIGN.md) | Describes the look. |
| `desktop/` | The desktop app (Electron). |
| `constraints.txt` | Pins every Python package in the image except yt-dlp (which updates itself on purpose), so a rebuild gives the same ones. |

### The desktop app

- `main.js` serves the bundled page as `parlor://app`.
- It passes the page's requests on to the server with a cookie jar the page can't read.
- It runs mpv and RetroArch.
- `cd app && ../.venv/bin/python ../tests/live_desktop.py` runs the packaged app in Docker against a real mpv and a real RetroArch (build it first).

### Moving the pinned packages on

`constraints.txt` pins them. To move them on:

1. Drop `-c /tmp/constraints.txt` from the Dockerfile.
2. Run `docker compose build --no-cache parlor`.
3. Run the checks.
4. Run `docker run --rm --entrypoint pip parlor:latest freeze | grep -v -i -e '^yt-dlp' -e '@ file'` into `constraints.txt` (keeping its header).
5. Put the `-c` back.
