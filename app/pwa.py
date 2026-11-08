"""Parlor as an installable app: serves the service worker (static/sw.js) with the files that make up the app and a
version that changes whenever one of them does, so devices pick up a new Parlor; OFFLINE=off makes every device's
worker remove itself and what it kept."""
import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

STATIC = Path(__file__).parent / "static"
ICONS = ["logo.svg", "icon-192.png", "icon-512.png", "icon-maskable.png", "icon-180.png"]
router = APIRouter()


def shell() -> list:
    """the page, its styles and scripts (in index.html's order) and the icons"""
    scripts = re.findall(r'<script src="(js/[\w-]+\.js)"', (STATIC / "index.html").read_text())
    return ["./", "app.css", "manifest.webmanifest", *ICONS, *scripts, "js/offline-worker.js"]


@lru_cache(1)
def worker() -> str:
    files = shell()
    digest = hashlib.sha256()
    for f in ["index.html", "sw.js", *files[1:]]:
        digest.update((STATIC / f).read_bytes())
    off = os.environ.get("OFFLINE", "").strip().lower() in ("off", "0", "false", "no")
    return ((STATIC / "sw.js").read_text().replace('"__VERSION__"', json.dumps(digest.hexdigest()[:12]))
            .replace("__SHELL__", json.dumps(files)).replace("__OFF__", json.dumps(off)))


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    return Response(worker(), media_type="text/javascript", headers={"Cache-Control": "no-cache"})
