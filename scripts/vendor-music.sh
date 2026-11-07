#!/bin/sh
# Copy music-fndr's backend into yeerr as app/music.py, with the three changes
# that let it run mounted inside yeerr. Run it again to pick up music-fndr updates:
#   scripts/vendor-music.sh ../music-fndr
set -eu
src="${1:?usage: scripts/vendor-music.sh PATH-TO-music-fndr}"
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/app/music.py"
python3 - "$src/app.py" "$out" <<'PY'
import sys
from pathlib import Path

src, out = Path(sys.argv[1]).read_text(), Path(sys.argv[2])
edits = [
    # 1. yeerr's UI sends its own header (yeerr's guard checks it before this app sees the request)
    ('!= "music-findr":', 'not in ("music-findr", "yeerr"):'),
    # 2. yeerr starts the worker itself; a mounted app's startup hook doesn't run
    ('@app.on_event("startup")\ndef start_worker():', 'def start_worker():'),
    # 3. yeerr serves the UI; music-fndr's own page isn't mounted
    ('app.mount(\n    "/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui"\n)\n', ''),
    ("from fastapi.staticfiles import StaticFiles\n", ""),
]
for old, new in edits:
    if src.count(old) != 1:
        sys.exit(f"vendor-music: expected one {old!r} in music-fndr's app.py; update this script")
    src = src.replace(old, new)
header = '"""music-fndr (github.com/ChaseSunstrom/music-fndr), vendored by scripts/vendor-music.sh. Edit it there."""\n\n'
out.write_text((header + src).rstrip() + "\n")
print(f"wrote {out}")
PY
