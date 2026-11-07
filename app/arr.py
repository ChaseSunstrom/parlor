"""The *arr apps (Sonarr, Radarr, Lidarr, Prowlarr): where they are, their API keys, and calls to them.

In yeerr's compose the apps run beside it under their service names, and their config
folders are mounted read-only under STACK, so the API keys are read from each app's
config.xml and nobody copies them by hand. Settings (or SONARR_URL / SONARR_API_KEY...)
point at apps that live somewhere else."""

import os
import re
from pathlib import Path

import requests

from core import setting

STACK = Path(os.environ.get("STACK_DIR", "/stack"))  # the apps' config folders, read-only
APPS = {
    "sonarr": {"label": "Sonarr", "port": 8989, "api": "v3", "root": "/data/shows", "category": ("tvCategory", "tv-sonarr")},
    "radarr": {"label": "Radarr", "port": 7878, "api": "v3", "root": "/data/movies", "category": ("movieCategory", "radarr")},
    "lidarr": {"label": "Lidarr", "port": 8686, "api": "v1", "root": "/data/music", "category": ("musicCategory", "lidarr")},
    "prowlarr": {"label": "Prowlarr", "port": 9696, "api": "v1"},
}
_keys: dict = {}  # app -> (config.xml mtime, key)


class ArrError(Exception):
    """A plain-words failure talking to an app."""


def url(app) -> str:
    return (setting(f"{app}_url") or os.environ.get(f"{app.upper()}_URL") or f"http://{app}:{APPS[app]['port']}").rstrip("/")


def config_file(app) -> Path:
    return STACK / app / "config.xml"


def key(app) -> str:
    """The app's API key: from Settings or the environment, else from its own config.xml."""
    given = setting(f"{app}_key") or os.environ.get(f"{app.upper()}_API_KEY", "")
    if given:
        return given
    path = config_file(app)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    if _keys.get(app, (None,))[0] != mtime:
        found = re.search(r"<ApiKey>\s*([0-9A-Za-z]{20,64})\s*</ApiKey>", path.read_text(errors="replace"))
        _keys[app] = (mtime, found[1] if found else "")
    return _keys[app][1]


def call(app, method, path, timeout=30, **kw):
    """One API call; errors come back as ArrError in plain words."""
    k = key(app)
    if not k:
        raise ArrError(f"{APPS[app]['label']}'s API key wasn't found (no {config_file(app)} and none in Settings)")
    try:
        r = requests.request(method, f"{url(app)}/api/{APPS[app]['api']}{path}", headers={"X-Api-Key": k}, timeout=timeout, **kw)
    except requests.RequestException as exc:
        raise ArrError(f"{APPS[app]['label']} didn't answer at {url(app)} ({type(exc).__name__})") from exc
    if r.status_code == 401:
        raise ArrError(f"{APPS[app]['label']} refused the API key")
    if not r.ok:
        raise ArrError(f"{APPS[app]['label']} said {r.status_code}: {problem(r)}")
    try:
        return r.json() if r.content else None
    except ValueError as exc:  # a proxy's HTML page, say
        raise ArrError(f"{APPS[app]['label']} answered with something that isn't JSON") from exc


def problem(r) -> str:
    """The first error message in an *arr's error reply."""
    try:
        body = r.json()
    except ValueError:
        return r.text[:200]
    if isinstance(body, list) and body and isinstance(body[0], dict):
        return body[0].get("errorMessage") or str(body[0])[:200]
    if isinstance(body, dict):
        return body.get("message") or body.get("error") or str(body)[:200]
    return str(body)[:200]


def fill(schema: dict, **values) -> dict:
    """An *arr schema object with some of its fields set by name."""
    for f in schema.get("fields", []):
        if f["name"] in values:
            f["value"] = values[f["name"]]
    return schema
