"""Whole sets against a real Seerr: starts a throwaway Jellyfin and Seerr in Docker (Seerr signs in through Jellyfin
and reads TMDB over the internet), points Parlor at it and checks that every well-known franchise, studio and network
bulk.py starts from exists there under a name, that a collection, a studio, a network and a keyword list with Seerr's
filters, and that Request all missing asks Seerr for exactly the missing films of a collection, as the person, once.
   python tests/live_collections.py      (needs Docker and the internet; a few minutes the first time)"""
import secrets
import subprocess
import sys
import time

import requests

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import bulk
import core
from yeerr_test_env import H, signed_in

TAG = f"parlor-sets-{secrets.token_hex(3)}"
NAME, PASSWORD = "setsadmin", secrets.token_urlsafe(12)  # pragma: allowlist secret (a random login for throwaway containers)
AVENGERS, MARVEL, HBO, MCU = 86311, 420, 49, 180547


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
        except requests.RequestException:
            pass
        if time.time() > end:
            raise SystemExit(f"FAIL: {what} didn't come up")
        time.sleep(2)


checks = {}
try:
    sh("docker", "network", "create", TAG)
    sh("docker", "run", "-d", "--name", f"{TAG}-jf", "--network", TAG, "-p", "127.0.0.1::8096", "jellyfin/jellyfin:latest")
    sh("docker", "run", "-d", "--name", f"{TAG}-seerr", "--network", TAG, "-p", "127.0.0.1::5055", "ghcr.io/seerr-team/seerr:latest")
    JF, SEERR = f"http://127.0.0.1:{port(f'{TAG}-jf', 8096)}", f"http://127.0.0.1:{port(f'{TAG}-seerr', 5055)}"

    # Jellyfin's first-run wizard, then Seerr signs in through it and is initialized
    wait("Jellyfin", lambda: requests.get(f"{JF}/System/Info/Public", timeout=5).json().get("StartupWizardCompleted") is False)
    for method, path, body in (("POST", "/Startup/Configuration", {"UICulture": "en-US", "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en"}),
                               ("GET", "/Startup/User", None), ("POST", "/Startup/User", {"Name": NAME, "Password": PASSWORD}),
                               ("POST", "/Startup/RemoteAccess", {"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False}), ("POST", "/Startup/Complete", None)):
        wait(f"Jellyfin {path}", lambda: requests.request(method, JF + path, json=body, timeout=20).ok, 60)
    wait("Seerr", lambda: requests.get(f"{SEERR}/api/v1/status", timeout=5).ok)
    s = requests.Session()
    r = s.post(f"{SEERR}/api/v1/auth/jellyfin", timeout=60, json={"username": NAME, "password": PASSWORD, "hostname": f"{TAG}-jf", "port": 8096, "useSsl": False,
                                                                  "urlBase": "", "email": "sets@example.test", "serverType": 2})
    r.raise_for_status()
    seerr_user = r.json()["id"]
    s.post(f"{SEERR}/api/v1/settings/initialize", timeout=30).raise_for_status()
    key = s.get(f"{SEERR}/api/v1/settings/main", timeout=30).json()["apiKey"]

    core.db()
    core.set_setting("seerr_url", SEERR)
    core.set_setting("seerr_key", key)
    me = signed_in("setsadmin")
    core.db().execute("UPDATE users SET seerr_id = ? WHERE name = 'setsadmin'", (seerr_user,))

    # every set Parlor starts from is a real one with a name (printed, to read them)
    for label, kind, ids in (("franchises", "collection", bulk.FRANCHISES), ("studios", "studio", bulk.STUDIOS), ("networks", "network", bulk.NETWORKS)):
        names = {}
        for i in ids:
            try:
                names[i] = bulk.about(kind, i)["name"]
            except Exception as exc:  # noqa: BLE001  (the check is that none does)
                names[i] = ""
                print(f"  ! {kind} {i}: {getattr(exc, 'detail', exc)}")
        print(f"  {label}: " + ", ".join(f"{i}={n}" for i, n in names.items()))
        checks[f"every one of the {len(ids)} {label} exists in Seerr"] = all(names.values())
    home = me.get("/api/seerr/sets").json()
    checks["the start page lists them with pictures"] = (len(home["franchises"]) == len(bulk.FRANCHISES) and all(f["poster"] for f in home["franchises"])
                                                         and sum(bool(x["logo"]) for x in home["studios"] + home["networks"]) >= 25)

    got = me.get(f"/api/seerr/sets/collection/{AVENGERS}").json()
    checks["a collection lists its films"] = got["name"] == "The Avengers Collection" and got["total"] == 4 and all(i["status"] == "unknown" for i in got["items"])
    got = me.get(f"/api/seerr/sets/collection/{AVENGERS}?year_from=2015&year_to=2018").json()
    checks["a collection narrows by year"] = sorted(i["year"] for i in got["items"]) == ["2015", "2018"]
    got = me.get(f"/api/seerr/sets/studio/{MARVEL}?year_from=2010&year_to=2012&rating=7").json()
    checks["a studio narrows by year and rating, in Seerr"] = (got["name"] == "Marvel Studios" and 0 < got["total"] < 10
                                                               and all("2010" <= i["year"] <= "2012" and i["rating"] >= 7 for i in got["items"]))
    got = me.get(f"/api/seerr/sets/network/{HBO}").json()
    checks["a network lists shows, 20 a page"] = got["name"] == "HBO" and got["media"] == "tv" and len(got["items"]) == 20 and got["pages"] > 5
    films, shows = me.get(f"/api/seerr/sets/keyword/{MCU}").json(), me.get(f"/api/seerr/sets/keyword/{MCU}?media=tv").json()
    checks["a keyword lists films or shows"] = films["total"] > 20 and shows["total"] > 5 and shows["items"][0]["type"] == "tv"
    found = me.get("/api/seerr/sets/search?q=marvel").json()
    checks["search finds studios and keywords"] = any(x["id"] == MARVEL for x in found["studios"]) and len(found["keywords"]) > 0
    film = me.get("/api/seerr/movie/24428").json()  # the first Avengers film
    checks["a film names its collection, studio and keywords"] = (film["collection"] == {"id": AVENGERS, "name": "The Avengers Collection"}
                                                                  and any(x["id"] == MARVEL for x in film["studios"]) and len(film["keywords"]) > 3)

    # request one film the ordinary way, then all that's missing: the other three, once, as this person
    first = me.get(f"/api/seerr/sets/collection/{AVENGERS}").json()["items"][0]
    assert me.post("/api/seerr/request", json={"type": "movie", "id": first["id"]}, headers=H).status_code == 200
    plan = me.post("/api/seerr/sets/plan", json={"kind": "collection", "id": AVENGERS}, headers=H).json()
    checks["the plan is the three films not yet requested"] = plan["count"] == 3 and plan["left_out"] == {"requested": 1} and first["id"] not in [t["id"] for t in plan["titles"]]
    job = me.post("/api/seerr/sets/request", json={"plan": plan["plan"]}, headers=H).json()
    bulk.PACE = 0.25
    bulk.run(job["id"])
    job = me.get("/api/seerr/sets/jobs").json()["jobs"][0]
    checks["the job requested all three"] = (job["status"], job["requested"], job["failed"], job["skipped"]) == ("done", 3, 0, 0)
    asked = requests.get(f"{SEERR}/api/v1/request", params={"take": 50}, headers={"X-Api-Key": key}, timeout=30).json()["results"]
    checks["Seerr has four requests, each film once, by this person"] = (
        sorted(q["media"]["tmdbId"] for q in asked) == sorted(i["id"] for i in me.get(f"/api/seerr/sets/collection/{AVENGERS}").json()["items"])
        and {q["requestedBy"]["id"] for q in asked} == {seerr_user})
    again = me.post("/api/seerr/sets/plan", json={"kind": "collection", "id": AVENGERS}, headers=H).json()
    checks["afterwards nothing is missing"] = again["count"] == 0 and sum(again["left_out"].values()) == 4
finally:
    subprocess.run(["docker", "rm", "-fv", f"{TAG}-jf", f"{TAG}-seerr"], capture_output=True)
    subprocess.run(["docker", "network", "rm", TAG], capture_output=True)

for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
ok = len(checks) == 15 and all(checks.values())
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
