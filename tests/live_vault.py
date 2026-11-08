"""Live check of encryption at rest in the real image: an install made by a Parlor from before
(plain database) is upgraded to this one; the database is encrypted on the first start, the account
still signs in, the plain copy is kept until the next start and then removed, and the key file is
Parlor's alone. Containers and folders are removed at the end. Needs Docker:
python tests/live_vault.py OLD_IMAGE   (an image from before encryption, e.g. parlor:latest)"""
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
OLD, NEW = (sys.argv[1] if len(sys.argv) > 1 else "parlor:latest"), "parlor:vault-test"
HOME = Path(tempfile.mkdtemp(prefix="parlor-vault-"))
NAME, PASSWORD = "vaultadmin", "Live-vault-pass-42!"  # pragma: allowlist secret (a made-up password for a throwaway container)
BOX = f"parlor-vault-{int(time.time())}"
with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
BASE, H = f"http://127.0.0.1:{PORT}", {"X-Requested-With": "parlor"}
PLAIN = b"SQLite format 3\x00"


def sh(*cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"{' '.join(cmd[:5])}… failed:\n{r.stdout[-500:]}\n{r.stderr[-1500:]}")
    return r.stdout.strip()


def up(image):
    sh("docker", "run", "-d", "--name", BOX, "-p", f"127.0.0.1:{PORT}:8800", "-e", "DATA_DIR=/config", "-e", "KEY_DIR=/key",
       "-e", "UPDATE_HOURS=0", "-v", f"{HOME / 'config'}:/config", "-v", f"{HOME / 'key'}:/key", image)
    end = time.time() + 180
    while time.time() < end:
        try:
            if requests.get(f"{BASE}/api/health", timeout=3).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    print(sh("docker", "logs", "--tail", "30", BOX, check=False))
    sys.exit(f"FAIL: {image} didn't start")


def down():
    sh("docker", "rm", "-f", BOX, check=False)


def signs_in():
    return requests.post(f"{BASE}/api/auth/login", json={"name": NAME, "password": PASSWORD}, headers=H, timeout=10).status_code == 200


def inside(path):  # read as root in a throwaway container: the files belong to Parlor
    return subprocess.run(["docker", "run", "--rm", "-v", f"{HOME}:/h", "--entrypoint", "sh", NEW, "-c", f"cat /h/{path}"],
                          capture_output=True).stdout


checks = {}
sh("nice", "-n", "19", "docker", "build", "-q", "-t", NEW, str(REPO))
try:
    up(OLD)
    assert requests.post(f"{BASE}/api/auth/setup", json={"name": NAME, "password": PASSWORD}, headers=H, timeout=30).ok
    down()
    checks["the old Parlor wrote a plain database"] = inside("config/yeerr.db")[:16] == PLAIN
    up(NEW)
    checks["the account still signs in after the upgrade"] = signs_in()
    db = inside("config/yeerr.db")
    checks["the database is encrypted"] = bool(db) and db[:16] != PLAIN and NAME.encode() not in db
    checks["the plain copy is kept for now"] = inside("config/yeerr.db.plain-backup")[:16] == PLAIN
    mode = sh("docker", "run", "--rm", "-v", f"{HOME}:/h", "--entrypoint", "stat", NEW, "-c", "%a", "/h/key/db.key", "/h/config")
    checks["the key and the data folder are Parlor's alone"] = mode.split() == ["600", "700"]
    down()
    up(NEW)
    checks["the next start removes the plain copy"] = not inside("config/yeerr.db.plain-backup")
    checks["and still signs in"] = signs_in()
finally:
    down()
    subprocess.run(["docker", "run", "--rm", "-v", f"{HOME}:/h", "--entrypoint", "rm", NEW, "-rf", "/h/config", "/h/key"], capture_output=True)
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
ok = len(checks) == 7 and all(checks.values())
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
