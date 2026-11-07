"""Imported first by every test: a throwaway data, video and music folder, then the app."""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))
TMP = Path(tempfile.mkdtemp(prefix="yeerr-test-"))
for name in ("data", "videos", "music"):
    (TMP / name).mkdir()
os.environ.update(DATA_DIR=str(TMP / "data"), VIDEO_DIR=str(TMP / "videos"), MUSIC_DIR=str(TMP / "music"),
                  SEERR_URL="", SEERR_API_KEY="", JELLYFIN_URL="", OPEN_SIGNUP="0", UPDATE_HOURS="0")
os.chdir(TMP / "music")  # music-fndr writes relative to the working directory

from fastapi.testclient import TestClient  # noqa: E402

import app as yeerr  # noqa: E402
import core  # noqa: E402

H = {"X-Requested-With": "yeerr"}


def client():
    return TestClient(yeerr.app)


def signed_in(name, password="correct horse", admin=False):
    """A client signed in as `name`, creating the account (the first one is the admin)."""
    c = client()
    if not core.one("SELECT 1 AS x FROM users LIMIT 1"):  # first run: "admin" is always the first account
        assert c.post("/api/auth/setup", json={"name": "admin", "password": "correct horse"}, headers=H).status_code == 200
        if name == "admin":
            return c
        c = client()
    if not core.one("SELECT 1 AS x FROM users WHERE name = ?", name):
        import auth

        auth.create_user(name, password, admin)
    r = c.post("/api/auth/login", json={"name": name, "password": password}, headers=H)
    assert r.status_code == 200, r.text
    return c
