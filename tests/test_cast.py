"""A film from the library on a TV set's own player (app/cast.py), against a stand-in DLNA player made here: it answers
the unicast SSDP search, serves its description, and takes AVTransport actions. And the safety around it: the link
the TV fetches the film by works without a sign-in but only for that film and only while it lasts, it stays out of
the log, the Jellyfin key never goes to the TV, and a TV whose answers point at another address is not followed.

Run: .venv/bin/python -m unittest discover -s tests -p 'test_cast*.py'"""
import http.server
import ipaddress
import logging
import re
import socket
import threading
import time
import unittest
from unittest import mock
from xml.sax.saxutils import unescape

from requests.structures import CaseInsensitiveDict

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)
from yeerr_test_env import H, client, signed_in

import cast
import core
import jf
import tv
import tvhw
import yt

LOCAL = {ipaddress.ip_network("127.0.0.1/32")}
FILM, SHOW, SONG = "a" * 32, "b" * 32, "c" * 32
KEY = "jellyfin-key-9c1f"
BODY = bytes(range(256)) * 40  # the "film": 10240 bytes
AVT = "urn:schemas-upnp-org:service:AVTransport:1"


class Player(http.server.ThreadingHTTPServer):
    """a DLNA renderer: SSDP on a UDP port, its description and its AVTransport control on a TCP one"""

    def __init__(self, location=None, control="/ctl/avt", answers=True, refuses=(), root=False, code=701):
        self.actions, self.state, self.at, self.uri, self.meta, self.refuses, self.searches = [], "NO_MEDIA_PRESENT", 0, "", "", list(refuses), []
        me = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def send(h, status, text):
                body = text.encode()
                h.send_response(status)
                h.send_header("Content-Type", "text/xml")
                h.send_header("Content-Length", str(len(body)))
                h.end_headers()
                h.wfile.write(body)

            def do_GET(h):
                if h.path != "/desc.xml" and not (root and h.path == "/"):
                    return h.send(404, "")
                h.send(200, f"""<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device><friendlyName>Den</friendlyName><serviceList>
                    <service><serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType><controlURL>/ctl/rc</controlURL></service>
                    <service><serviceType>{AVT}</serviceType><controlURL>{control}</controlURL></service></serviceList></device></root>""")

            def do_POST(h):
                text = h.rfile.read(int(h.headers["Content-Length"])).decode()
                action = h.headers["SOAPACTION"].strip('"').split("#")[1]
                arg = lambda name: unescape((re.search(rf"<{name}>(.*?)</{name}>", text, re.S) or [None, ""])[1], {"&quot;": '"'})  # noqa: E731
                me.actions.append((h.path, action))
                if action in me.refuses:
                    me.refuses.remove(action)
                    return h.send(500, f"<s:Envelope><s:Body><s:Fault><detail><UPnPError><errorCode>{code}</errorCode></UPnPError></detail></s:Fault></s:Body></s:Envelope>")
                out = ""
                if action == "SetAVTransportURI":
                    me.uri, me.meta, me.state, me.at = arg("CurrentURI"), arg("CurrentURIMetaData"), "STOPPED", 0
                elif action == "Play":
                    me.state = "PLAYING"
                elif action == "Pause":
                    me.state = "PAUSED_PLAYBACK"
                elif action == "Stop":
                    me.state = "STOPPED"
                elif action == "Seek":
                    hh, mm, ss = arg("Target").split(":")
                    me.at = int(hh) * 3600 + int(mm) * 60 + int(ss)
                elif action == "GetTransportInfo":
                    out = f"<CurrentTransportState>{me.state}</CurrentTransportState>"
                elif action == "GetPositionInfo":
                    out = f"<TrackDuration>1:40:00</TrackDuration><RelTime>{cast._clock(me.at)}</RelTime>"
                h.send(200, f'<s:Envelope><s:Body><u:{action}Response xmlns:u="{AVT}">{out}</u:{action}Response></s:Body></s:Envelope>')

            def log_message(h, *a):
                pass

        super().__init__(("127.0.0.1", 0), Handler)
        self.port = self.server_address[1]
        self.location = location or f"http://127.0.0.1:{self.port}/desc.xml"
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.ssdp = self.udp.getsockname()[1]
        threading.Thread(target=self.serve_forever, daemon=True).start()
        if answers:
            threading.Thread(target=self.hear, daemon=True).start()

    def hear(self):
        while True:
            try:
                data, frm = self.udp.recvfrom(4096)
            except OSError:
                return
            self.searches.append(data.decode())
            self.udp.sendto(f"HTTP/1.1 200 OK\r\nST: {AVT}\r\nLOCATION: {self.location}\r\n\r\n".encode(), frm)

    def did(self):
        return [a for _, a in self.actions]


class Answer:
    def __init__(self, status=200, body=b"", headers=None, data=None):
        self.status_code, self.body, self.headers, self.data, self.closed = status, body, CaseInsensitiveDict(headers or {}), data, False

    def json(self):
        return self.data

    def iter_content(self, n):
        yield self.body

    def close(self):
        self.closed = True


class Jellyfin:
    """the few Jellyfin answers a film on a TV needs; every call is kept"""

    def __init__(self):
        self.calls, self.saves = [], []

    def __call__(self, method, url, headers=None, params=None, json=None, **kw):
        path = url.split("//", 1)[1].split("/", 1)[1]
        self.calls.append({"method": method, "path": path, "headers": headers or {}, "params": params})
        if path == "System/Info":
            return Answer(data={"Version": "10.10.0", "ServerName": "stand-in"})
        if path == "Users":
            return Answer(data=[{"Id": "f" * 32, "Name": "family", "Policy": {}}])
        if path == f"Items/{FILM}":
            return Answer(data={"Id": FILM, "Name": "Stalker <1979> & co", "Type": "Movie", "MediaType": "Video", "RunTimeTicks": 6000 * jf.T,
                                "UserData": {"PlaybackPositionTicks": 600 * jf.T}, "MediaSources": [{"Id": FILM, "Container": "mkv,webm"}]})
        if path == f"Items/{SHOW}":
            return Answer(data={"Id": SHOW, "Name": "Pilot", "Type": "Episode", "MediaType": "Video", "SeriesName": "Twin Peaks", "ParentIndexNumber": 1,
                                "IndexNumber": 1, "UserData": {}, "MediaSources": [{"Id": SHOW, "Container": "mp4"}]})
        if path == f"Items/{SONG}":
            return Answer(data={"Id": SONG, "Name": "A song", "Type": "Audio", "MediaType": "Audio", "MediaSources": [{"Id": SONG, "Container": "flac"}]})
        if path == f"Videos/{FILM}/stream":
            rng = (headers or {}).get("Range")
            if rng:
                a, b = re.fullmatch(r"bytes=(\d+)-(\d*)", rng).groups()
                a, b = int(a), int(b) if b else len(BODY) - 1
                return Answer(206, BODY[a:b + 1], {"Content-Range": f"bytes {a}-{b}/{len(BODY)}", "Content-Length": str(b - a + 1), "Content-Type": "video/x-matroska"})
            return Answer(200, BODY, {"Content-Length": str(len(BODY)), "Content-Type": "video/x-matroska"})
        if path.endswith("/UserData"):
            self.saves.append((path, params, json))
            return Answer(data={})
        return Answer(404)


class Logs(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(f"{record.name} {record.getMessage()} {record.args!r}")


class Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = signed_in("admin")
        cls.member = signed_in("castpat")
        cls.pat = core.one("SELECT id FROM users WHERE name = 'castpat'")["id"]

    def setUp(self):
        tv._tries.clear()
        core.db().execute("DELETE FROM tv_sets")
        old = {k: core.setting(k) for k in ("jellyfin_url", "jellyfin_key", "jellyfin_user", "tv_address")}
        self.addCleanup(lambda: [core.set_setting(k, v) for k, v in old.items()])
        for k, v in (("jellyfin_url", "http://jellyfin.test:8096"), ("jellyfin_key", KEY), ("jellyfin_user", "family"), ("tv_address", "")):
            core.set_setting(k, v)
        jf._users.data.clear()
        cast._links.clear()
        cast._on.clear()
        cast._found.clear()
        self.jf = Jellyfin()
        for p in (mock.patch.object(cast, "PORTS", ()), mock.patch.object(tvhw, "ALSO", LOCAL), mock.patch.object(tvhw, "TIMEOUT", 2), mock.patch.object(cast, "SEEK_WAIT", 0),
                  mock.patch.object(cast, "SEARCH_WAIT", 0.5), mock.patch.object(jf.requests, "request", self.jf)):
            p.start()
            self.addCleanup(p.stop)

    def player(self, **kw):
        p = Player(**kw)
        self.addCleanup(p.shutdown)
        self.addCleanup(p.udp.close)
        patch = mock.patch.object(cast, "SSDP_PORT", p.ssdp)
        patch.start()
        self.addCleanup(patch.stop)
        r = self.admin.post("/api/tv/sets", json={"kind": "webos", "name": "Den", "host": "127.0.0.1"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return p, r.json()["id"]

    def play(self, sid, iid=FILM, who=None, host="192.168.1.20:8800", **body):
        return (who or self.admin).post(f"/api/tv/hw/{sid}/play", json={"id": iid, **body}, headers={**H, "Host": host})

    def do(self, sid, **body):
        return self.admin.post(f"/api/tv/hw/{sid}/playing", json=body, headers=H)

    def now(self, sid):
        return self.admin.get(f"/api/tv/hw/{sid}/playing").json()["playing"]


class Plays(Case):
    def test_a_film_is_set_played_and_taken_to_where_it_was_left(self):
        p, sid = self.player()
        r = self.play(sid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["title"], "Stalker <1979> & co")
        self.assertEqual(p.did(), ["SetAVTransportURI", "Play", "Seek"])
        self.assertEqual({path for path, _ in p.actions}, {"/ctl/avt"})  # the AVTransport service's address, not the other service's
        self.assertRegex(p.uri, r"^http://192\.168\.1\.20:8800/cast/[\w-]{40,}/video\.mkv$")
        self.assertEqual(p.at, 600)  # Jellyfin's saved place
        self.assertIn("<dc:title>Stalker &lt;1979&gt; &amp; co</dc:title>", p.meta)
        self.assertIn(f'protocolInfo="http-get:*:video/x-matroska:{cast.DLNA}"', p.meta)
        self.assertIn(p.uri, unescape(p.meta))
        self.assertIn("ST: " + AVT, p.searches[0])
        self.assertTrue(self.admin.get("/api/tv/hw").json()["sets"][0]["plays"])

    def test_from_the_start_and_an_episodes_name(self):
        p, sid = self.player()
        self.assertEqual(self.play(sid, start=0).status_code, 200)
        self.assertEqual(p.did(), ["SetAVTransportURI", "Play"])
        self.assertEqual(self.play(sid, SHOW).json()["title"], "Twin Peaks · S1:E1 · Pilot")
        self.assertTrue(p.uri.endswith("/video.mp4"))

    def test_a_tv_that_wont_seek_yet_is_asked_again_and_the_film_plays_anyway(self):
        p, sid = self.player(refuses=["Seek", "Seek"])
        self.assertEqual(self.play(sid).status_code, 200)
        self.assertEqual(p.did(), ["SetAVTransportURI", "Play", "Seek", "Seek", "Seek"])
        self.assertEqual(p.at, 600)
        p2, sid2 = self.player(refuses=["Seek"] * 9)
        self.assertEqual(self.play(sid2).status_code, 200)  # it never lets Parlor seek: from the start, not an error
        self.assertEqual(p2.did().count("Seek"), cast.SEEK_TRIES)

    def test_pause_play_seek_and_stop_and_what_is_playing(self):
        p, sid = self.player()
        self.assertIsNone(self.now(sid))
        self.assertEqual(self.do(sid, do="pause").status_code, 409)  # nothing from Parlor is on it
        self.play(sid)
        self.assertEqual(self.now(sid), {"title": "Stalker <1979> & co", "state": "playing", "at": 600, "length": 6000})
        self.assertEqual(self.do(sid, do="pause").status_code, 200)
        self.assertEqual(self.now(sid)["state"], "paused")
        self.assertEqual(self.do(sid, do="play").status_code, 200)
        self.assertEqual(self.do(sid, do="seek", by=30).status_code, 200)
        self.assertEqual(p.at, 630)
        self.assertEqual(self.do(sid, do="seek", by=-9999).status_code, 200)
        self.assertEqual(p.at, 0)
        self.assertEqual(self.do(sid, do="seek", to=3725).status_code, 200)
        self.assertEqual(p.at, 3725)
        self.assertEqual(self.do(sid, do="stop").status_code, 200)
        self.assertEqual(p.state, "STOPPED")
        self.assertIsNone(self.now(sid))
        self.assertEqual(self.do(sid, do="eject").status_code, 422)

    def test_where_it_got_to_is_saved_and_watched_at_the_end(self):
        p, sid = self.player()
        self.play(sid)
        self.now(sid)
        self.assertEqual(self.jf.saves, [(f"UserItems/{FILM}/UserData", {"userId": "f" * 32}, {"PlaybackPositionTicks": 600 * jf.T})])
        p.at = 700
        self.now(sid)
        self.assertEqual(len(self.jf.saves), 1)  # not on every look
        p.at, p.state = 5900, "PLAYING"
        with mock.patch.object(cast, "SAVE_EVERY", 0):
            self.now(sid)
        p.state = "STOPPED"  # the film ended on the TV
        self.assertIsNone(self.now(sid))
        self.assertEqual(self.jf.saves[-1][2], {"Played": True, "PlaybackPositionTicks": 0})
        self.assertEqual(cast._links, {})

    def test_a_film_starting_is_not_taken_for_one_that_ended(self):
        p, sid = self.player()
        self.play(sid, start=0)
        p.state = "STOPPED"  # some TVs say so until the first bytes arrive
        self.assertEqual(self.now(sid)["state"], "stopped")
        self.assertEqual(len(cast._links), 1)

    def test_a_film_the_tv_never_starts_is_said_and_its_link_ends(self):
        p, sid = self.player()
        self.play(sid)
        p.state = "STOPPED"
        self.assertEqual(self.now(sid)["state"], "stopped")  # still starting
        with mock.patch.object(cast, "NO_START", 0):
            r = self.admin.get(f"/api/tv/hw/{sid}/playing").json()
        self.assertIsNone(r["playing"])
        self.assertIn("didn't start", r["said"])
        self.assertEqual(cast._links, {})

    def test_an_old_films_end_leaves_a_newer_films_link(self):
        p, sid = self.player()
        self.play(sid)
        old = cast._links[cast._on[sid]]
        self.play(sid)
        cast._forget(sid, old)
        self.assertEqual(len(cast._links), 1)
        self.assertIn(cast._on[sid], cast._links)

    def test_only_a_video_and_only_a_set_that_plays(self):
        p, sid = self.player()
        r = self.play(sid, SONG)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.play(sid, "d" * 32).status_code, 404)  # Jellyfin has no such thing (or this person can't see it)
        self.assertEqual(self.play(sid, "../etc").status_code, 422)
        self.assertEqual(p.actions, [])
        self.assertEqual(cast._links, {})
        roku = self.admin.post("/api/tv/sets", json={"kind": "roku", "name": "Roku", "host": "127.0.0.1"}, headers=H).json()["id"]
        self.assertEqual(self.play(roku).status_code, 400)
        self.assertFalse(next(s for s in self.admin.get("/api/tv/hw").json()["sets"] if s["id"] == roku)["plays"])

    def test_a_member_plays_only_on_a_set_they_were_given(self):
        p, sid = self.player()
        self.assertEqual(self.play(sid, who=self.member).status_code, 404)
        self.assertEqual(self.member.get(f"/api/tv/hw/{sid}/playing").status_code, 404)
        self.assertEqual(self.member.post(f"/api/tv/hw/{sid}/playing", json={"do": "stop"}, headers=H).status_code, 404)
        self.admin.put(f"/api/tv/sets/{sid}/users", json={"users": [self.pat]}, headers=H)
        self.assertEqual(self.play(sid, who=self.member).status_code, 200)
        self.assertEqual(client().post(f"/api/tv/hw/{sid}/play", json={"id": FILM}, headers=H).status_code, 401)
        # kept out of the Library, a person can't play it on a TV either
        self.admin.put(f"/api/users/{self.pat}/access", json={"blocked": ["library"]}, headers=H)
        self.addCleanup(lambda: self.admin.put(f"/api/users/{self.pat}/access", json={"blocked": []}, headers=H))
        self.assertEqual(self.play(sid, who=self.member).status_code, 403)


class Link(Case):
    """what the TV fetches the film by"""

    def link(self):
        p, sid = self.player()
        self.play(sid)
        return p, sid, p.uri.split("192.168.1.20:8800", 1)[1]

    def test_it_serves_the_film_with_no_sign_in_and_byte_ranges(self):
        p, sid, path = self.link()
        tvset = client()  # a TV: no session
        r = tvset.get(path)
        self.assertEqual((r.status_code, r.content), (200, BODY))
        self.assertEqual(r.headers["content-type"], "video/x-matroska")
        self.assertEqual(r.headers["contentfeatures.dlna.org"], cast.DLNA)
        self.assertEqual(r.headers["accept-ranges"], "bytes")
        r = tvset.get(path, headers={"Range": "bytes=100-199"})
        self.assertEqual((r.status_code, r.content, r.headers["content-range"]), (206, BODY[100:200], f"bytes 100-199/{len(BODY)}"))
        r = tvset.head(path)
        self.assertEqual((r.status_code, r.content, r.headers["content-length"]), (200, b"", str(len(BODY))))
        call = self.jf.calls[-1]
        self.assertEqual((call["path"], call["params"]), (f"Videos/{FILM}/stream", {"static": "true", "mediaSourceId": FILM}))

    def test_the_jellyfin_key_never_goes_to_the_tv(self):
        p, sid, path = self.link()
        r = client().get(path)
        self.assertNotIn(KEY, p.uri + p.meta + str(dict(r.headers)))
        self.assertIn(KEY, self.jf.calls[-1]["headers"]["Authorization"])  # it went to Jellyfin, from Parlor

    def test_it_is_for_that_film_only_and_ends(self):
        p, sid, path = self.link()
        token = path.split("/")[2]
        tvset = client()
        for other in (path.replace(token, "x" * 43), path.replace(token, token[:-1]), f"/cast/{token}", "/cast//video.mkv"):
            self.assertIn(tvset.get(other).status_code, (404, 405), other)
        self.assertEqual(tvset.get(path.replace("video.mkv", f"../../api/jf/stream/Videos/{SHOW}/stream")).status_code, 401)  # that's the signed-in stream, and a TV isn't
        self.assertEqual(tvset.post(path).status_code, 405)
        self.play(sid, SHOW)  # something else on the same set: the old link is gone
        self.assertEqual(tvset.get(path).status_code, 404)
        path = p.uri.split("192.168.1.20:8800", 1)[1]
        self.do(sid, do="stop")
        self.assertEqual(tvset.get(path).status_code, 404)
        self.play(sid)
        path = p.uri.split("192.168.1.20:8800", 1)[1]
        with mock.patch.object(cast.time, "time", lambda: time.monotonic() + cast.LIFE + 10**10):
            self.assertEqual(tvset.get(path).status_code, 404)  # past its life

    def test_the_links_name_stays_out_of_the_log(self):
        logs = Logs()
        root = logging.getLogger()
        level = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(logs)
        access = logging.getLogger("uvicorn.access")  # a server run earlier in the suite stops it reaching the root
        was = access.level, access.propagate, access.disabled
        access.setLevel(logging.INFO)
        access.propagate, access.disabled = False, False
        access.addHandler(logs)
        self.addCleanup(lambda: (root.removeHandler(logs), root.setLevel(level), access.removeHandler(logs), access.setLevel(was[0]),
                                 setattr(access, "propagate", was[1]), setattr(access, "disabled", was[2])))
        p, sid, path = self.link()
        token = path.split("/")[2]
        client().get(path)
        self.now(sid)
        self.do(sid, do="stop")
        logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "192.168.1.40:5", "GET", path, "1.1", 200)  # what uvicorn writes
        self.assertTrue(any("uvicorn.access" in line for line in logs.lines))
        self.assertFalse([line for line in logs.lines if token in line and not line.startswith("httpx ")])  # httpx is this test's own client
        self.assertNotIn(token, self.admin.get(f"/api/tv/hw/{sid}/playing").text + self.admin.get("/api/tv/hw").text)

    def test_the_address_the_tv_gets_is_the_one_set_or_the_one_asked_by(self):
        p, sid = self.player()
        for host in ("localhost:8800", "127.0.0.1:8800", "93.184.216.34", "evil.example.test"):
            self.assertEqual(self.play(sid, host=host).status_code, 409, host)
        self.assertEqual(p.actions, [])
        self.assertEqual(cast._links, {})
        r = self.admin.put("/api/tv/address", json={"address": " http://192.168.1.20:8800/ "}, headers=H)
        self.assertEqual((r.status_code, r.json()), (200, {"address": "http://192.168.1.20:8800"}))
        self.assertEqual(self.admin.get("/api/tv/sets").json()["address"], "http://192.168.1.20:8800")
        self.assertEqual(self.play(sid, host="parlor.example.test").status_code, 200)  # opened by a name a TV can't use
        self.assertTrue(p.uri.startswith("http://192.168.1.20:8800/cast/"))
        for bad in ("http://93.184.216.34", "http://localhost:8800", "ftp://192.168.1.20", "http://192.168.1.20:8800/x", "http://a@192.168.1.20",
                    "http://192.168.1.20:99999", "192.168.1.20:8800", "http://192.168.1.20/?x=1"):
            self.assertEqual(self.admin.put("/api/tv/address", json={"address": bad}, headers=H).status_code, 400, bad)
        self.assertEqual(self.member.put("/api/tv/address", json={"address": ""}, headers=H).status_code, 403)
        self.assertEqual(self.admin.put("/api/tv/address", json={"address": ""}, headers=H).json(), {"address": ""})


class Finding(Case):
    """the TV's own answers can't send Parlor anywhere but the TV"""

    def test_a_tv_that_doesnt_answer_is_said_plainly(self):
        p, sid = self.player(answers=False)
        r = self.play(sid)
        self.assertEqual(r.status_code, 502)
        self.assertIn("network player", r.json()["detail"])
        self.assertEqual(cast._links, {})

    def test_a_tv_that_doesnt_answer_the_search_is_found_by_its_open_ports(self):
        p, sid = self.player(answers=False, root=True)  # an LG across network segments: silent, its player described at a port's root
        with socket.socket() as shut:
            shut.bind(("127.0.0.1", 0))  # a closed port, and one that isn't a description
            with mock.patch.object(cast, "PORTS", (shut.getsockname()[1], p.ssdp, p.server_port)):
                self.assertEqual(self.play(sid).status_code, 200)
        self.assertEqual(p.did()[:2], ["SetAVTransportURI", "Play"])

    def test_a_description_at_another_address_is_not_fetched(self):
        other = Player(answers=False)
        self.addCleanup(other.shutdown)
        self.addCleanup(other.udp.close)
        seen = []
        other.RequestHandlerClass.do_GET = lambda h: seen.append(h.path) or h.send(200, "")
        for location in (f"http://localhost:{other.port}/desc.xml", f"http://127.0.0.2:{other.port}/desc.xml", f"https://127.0.0.1:{other.port}/desc.xml",
                         "http://127.0.0.1/desc.xml", f"http://u@127.0.0.1:{other.port}/desc.xml", "http://93.184.216.34:80/desc.xml", "file:///etc/passwd"):
            p, sid = self.player(location=location)
            self.assertEqual(self.play(sid).status_code, 502, location)
        self.assertEqual(seen, [])

    def test_a_control_address_elsewhere_is_not_used(self):
        for control in ("http://127.0.0.2:1/ctl", "http://93.184.216.34:80/ctl", "//evil.example.test:80/ctl", "https://127.0.0.1:1/ctl"):
            p, sid = self.player(control=control)
            self.assertEqual(self.play(sid).status_code, 502, control)
            self.assertEqual(p.actions, [])

    def test_an_answer_from_another_address_is_not_heard(self):
        p, sid = self.player(answers=False)
        real = socket.socket.recvfrom

        def spoofed(sock, n):
            data, frm = real(sock, n)
            return data, ("127.0.0.9", frm[1])

        threading.Thread(target=p.hear, daemon=True).start()
        with mock.patch.object(socket.socket, "recvfrom", spoofed):
            found = cast._search("127.0.0.1")
        self.assertEqual(found, [])

    def test_a_refusal_from_the_player_is_said_with_its_number(self):
        p, sid = self.player(refuses=["SetAVTransportURI"] * 2)
        r = self.play(sid)
        self.assertEqual(r.status_code, 502)
        self.assertIn("701", r.json()["detail"])
        self.assertEqual(p.did(), ["SetAVTransportURI", "Stop", "SetAVTransportURI"])
        self.assertEqual(cast._links, {})

    def test_a_tv_still_loading_the_last_film_is_stopped_and_sent_this_one(self):
        p, sid = self.player(refuses=["SetAVTransportURI"])  # 701: what a real LG answers while it loads something else
        self.assertEqual(self.play(sid, start=0).status_code, 200)
        self.assertEqual(p.did(), ["SetAVTransportURI", "Stop", "SetAVTransportURI", "Play"])

    def test_a_tv_that_cant_fetch_the_link_is_told_where_it_tried(self):
        p, sid = self.player(refuses=["SetAVTransportURI"], code=716)  # what a real LG answers when a firewall is in its way
        r = self.play(sid)
        self.assertEqual(r.status_code, 502)
        self.assertIn("couldn't fetch it from http://192.168.1.20:8800", r.json()["detail"])
        self.assertNotIn("/cast/", r.json()["detail"])

    def test_a_huge_description_is_not_read_whole(self):
        p, sid = self.player()
        p.RequestHandlerClass.do_GET = lambda h: h.send(200, "<root>" + "x" * (cast.MAX_TEXT + 10) + "</root>")
        self.assertEqual(self.play(sid).status_code, 502)

VID = "castvideo01"
INFO = {"id": VID, "title": "A <talk> & more", "channel": "A channel", "channel_id": "UC" + "x" * 22, "duration": 600, "view_count": 5, "formats": [
    {"format_id": "137", "protocol": "https", "url": "https://yt.test/137", "vcodec": "avc1.64", "acodec": "none", "height": 1080, "ext": "mp4"},
    {"format_id": "18", "protocol": "https", "url": "https://yt.test/18", "vcodec": "avc1.42", "acodec": "mp4a.40", "height": 360, "ext": "mp4",
     "http_headers": {"User-Agent": "yt"}}]}


class Watch(Case):
    """a Watch (YouTube) video on a set's own player: the downloaded file, else YouTube's one-file stream"""

    def setUp(self):
        super().setUp()
        self.asked = []
        core.db().execute("DELETE FROM history")
        core.db().execute("DELETE FROM videos")

        def get(url, headers=None, **kw):  # YouTube: only ranges, a piece at a time
            self.asked.append((url, headers["Range"]))
            a, b = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", headers["Range"]).groups())
            if a >= len(BODY):
                return Answer(416)
            b = min(b, len(BODY) - 1)
            return Answer(206, BODY[a:b + 1], {"Content-Range": f"bytes {a}-{b}/{len(BODY)}", "Content-Length": str(b - a + 1)})

        for p in (mock.patch.object(yt, "video_info", lambda vid, user_id=None, fresh=False: INFO), mock.patch.object(yt.requests, "get", get),
                  mock.patch.object(yt, "CHUNK", 4000)):
            p.start()
            self.addCleanup(p.stop)

    def test_a_video_plays_from_youtubes_one_file_stream_whole_however_youtube_cuts_it(self):
        p, sid = self.player()
        core.db().execute("INSERT INTO history(user_id, video_id, item, position, watched) VALUES (?, ?, '{}', 90, 1)",
                          (core.one("SELECT id FROM users WHERE name = 'admin'")["id"], VID))
        r = self.play(sid, VID, kind="yt")
        self.assertEqual((r.status_code, r.json()["title"]), (200, "A <talk> & more"))
        self.assertIn("360p", r.json()["note"])  # the small one: said, with the way to the full one
        self.assertEqual((p.did()[:3], p.at), (["SetAVTransportURI", "Play", "Seek"], 90))  # from where it was left
        self.assertTrue(p.uri.endswith("/video.mp4"))
        path = p.uri.split("192.168.1.20:8800", 1)[1]
        tvset = client()  # a TV: no session
        r = tvset.get(path)
        self.assertEqual((r.status_code, r.content, r.headers["content-length"], r.headers["content-type"]), (200, BODY, str(len(BODY)), "video/mp4"))
        self.assertEqual([x[1] for x in self.asked], ["bytes=0-3999", "bytes=4000-7999", "bytes=8000-10239"])
        self.assertEqual(self.asked[0][0], "https://yt.test/18")  # the stream with sound in it
        r = tvset.get(path, headers={"Range": "bytes=100-5099"})
        self.assertEqual((r.status_code, r.content, r.headers["content-range"]), (206, BODY[100:5100], f"bytes 100-5099/{len(BODY)}"))
        r = tvset.get(path, headers={"Range": "bytes=10000-"})
        self.assertEqual((r.status_code, r.content, r.headers["content-range"]), (206, BODY[10000:], f"bytes 10000-10239/{len(BODY)}"))
        self.assertEqual(tvset.head(path).headers["content-length"], str(len(BODY)))
        self.assertEqual(tvset.get(path, headers={"Range": "bytes=99999-"}).status_code, 416)
        r = tvset.get(path, headers={"Range": "bytes=-240"})  # the last bytes: a player reading an MP4's index
        self.assertEqual((r.status_code, r.content, r.headers["content-range"]), (206, BODY[-240:], f"bytes 10000-10239/{len(BODY)}"))
        r = tvset.get(path, headers={"Range": "bytes=0-1,5-9"})  # a range this can't read: the whole video, said as such
        self.assertEqual((r.status_code, r.content, "content-range" in r.headers), (200, BODY, False))
        # where it got to goes to the Watch history, not Jellyfin
        p.at = 240
        self.assertEqual(self.now(sid)["title"], "A <talk> & more")
        self.assertEqual(core.one("SELECT position FROM history WHERE video_id = ?", VID)["position"], 240)
        self.assertEqual(self.jf.saves, [])
        self.assertEqual(self.play(sid, VID, kind="yt", start=0).status_code, 200)  # from the start
        self.assertNotIn("Seek", p.did()[-2:])

    def test_a_downloaded_video_plays_from_its_file(self):
        p, sid = self.player()
        (core.VIDEOS / "talk.mkv").write_bytes(BODY)
        self.addCleanup((core.VIDEOS / "talk.mkv").unlink)
        core.db().execute("INSERT INTO videos(id, item, path, added) VALUES (?, '{}', 'talk.mkv', 1)", (VID,))
        r = self.play(sid, VID, kind="yt")
        self.assertEqual((r.status_code, r.json()["note"]), (200, ""))
        self.assertTrue(p.uri.endswith("/video.mkv"))
        path = p.uri.split("192.168.1.20:8800", 1)[1]
        r = client().get(path, headers={"Range": "bytes=10-19"})
        self.assertEqual((r.status_code, r.content, r.headers["content-type"], r.headers["contentfeatures.dlna.org"]),
                         (206, BODY[10:20], "video/x-matroska", cast.DLNA))
        self.assertEqual(self.asked, [])  # YouTube isn't asked

    def test_what_cant_play_is_said_and_who_may(self):
        p, sid = self.player()
        with mock.patch.object(yt, "video_info", lambda *a, **k: {**INFO, "formats": INFO["formats"][:1]}):  # picture and sound apart only
            r = self.play(sid, VID, kind="yt")
        self.assertEqual(r.status_code, 409)
        self.assertIn("downloaded", r.json()["detail"])
        with mock.patch.object(yt, "video_info", lambda *a, **k: {**INFO, "formats": [], "live_status": "is_live"}):
            self.assertIn("live stream", self.play(sid, VID, kind="yt").json()["detail"])
        self.assertEqual(self.play(sid, VID).status_code, 422)  # a Watch id isn't a Library item
        self.assertEqual(self.play(sid, FILM, kind="yt").status_code, 422)
        self.admin.put(f"/api/tv/sets/{sid}/users", json={"users": [self.pat]}, headers=H)
        self.admin.put(f"/api/users/{self.pat}/access", json={"blocked": ["watch"]}, headers=H)
        self.addCleanup(lambda: self.admin.put(f"/api/users/{self.pat}/access", json={"blocked": []}, headers=H))
        self.assertEqual(self.play(sid, VID, kind="yt", who=self.member).status_code, 403)
        self.assertEqual(self.play(sid, who=self.member).status_code, 200)  # the Library is still theirs

if __name__ == "__main__":
    unittest.main()
