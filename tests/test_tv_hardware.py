"""TV hardware from Parlor's remote (app/tvhw.py): power, input and volume reach a TV set over its own protocol, each
driver against a fake of that protocol made here (Roku ECP over HTTP, LG webOS over a WebSocket with its pairing
prompt and client-key, Samsung over a WebSocket with its token, Android TV through a fake adb, HDMI-CEC through a
fake cec-client). And the safety around it: only admins add sets; an address must be on the LAN's private ranges,
every address a name resolves to, checked again before each connection, which goes to that address and not the name;
keys and tokens stay in the database, out of the API and the log; a member presses only the sets they were given.

Run: .venv/bin/python -m unittest discover -s tests -p 'test_tv_hardware*.py'"""
import http.server
import ipaddress
import json
import logging
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)
from yeerr_test_env import H, signed_in

import core
import tv
import tvhw
from websockets.sync.server import serve

LOCAL = {ipaddress.ip_network("127.0.0.1/32")}  # the fakes listen here; nothing else on loopback is let in
REAL_GETADDRINFO = socket.getaddrinfo
TMP = Path(tempfile.mkdtemp())


def resolves(names):
    """getaddrinfo with made-up names: {"den-tv.lan": ["192.168.1.40"]}; anything else goes to the real one"""
    calls = []

    def fake(host, *a, **kw):
        if host in names:
            calls.append(host)
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in names[host]]
        return REAL_GETADDRINFO(host, *a, **kw)

    return mock.patch("socket.getaddrinfo", fake), calls


class Roku(http.server.ThreadingHTTPServer):
    """Roku's External Control Protocol: POST /keypress/<Key>, 200 with no body"""

    def __init__(self, status=200, location=""):
        self.seen, self.status, self.location = [], status, location

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(h):
                self.seen.append(h.path)
                h.send_response(self.status)
                if self.location:
                    h.send_header("Location", self.location)
                h.send_header("Content-Length", "0")
                h.end_headers()

            do_GET = do_POST

            def log_message(h, *a):
                pass

        super().__init__(("127.0.0.1", 0), Handler)
        self.port = self.server_address[1]
        threading.Thread(target=self.serve_forever, daemon=True).start()


class Lg:
    """LG webOS: register (a prompt on the TV, then a client-key), then ssap:// requests"""
    KEY = "lg-client-key-5f1c9e"

    def __init__(self, accepts=True):
        self.accepts, self.registered_with, self.prompts, self.seen = accepts, [], 0, []
        self.server = serve(self.handle, "127.0.0.1", 0)
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def handle(self, ws):
        for raw in ws:
            m = json.loads(raw)
            if m["type"] == "register":
                key = m["payload"].get("client-key")
                self.registered_with.append(key)
                if key != self.KEY:
                    self.prompts += 1
                    ws.send(json.dumps({"type": "response", "id": m["id"], "payload": {"pairingType": "PROMPT", "returnValue": True}}))
                    if not self.accepts:
                        continue
                ws.send(json.dumps({"type": "registered", "id": m["id"], "payload": {"client-key": self.KEY}}))
            else:
                self.seen.append((m["uri"], m.get("payload")))
                ws.send(json.dumps({"type": "response", "id": m["id"], "payload": {"returnValue": True, "mute": False}}))


class Samsung:
    """Samsung Tizen: the token goes in the address; the TV gives one once someone accepts on its screen"""
    TOKEN = "71840266"

    def __init__(self, accepts=True):
        self.accepts, self.tokens, self.seen = accepts, [], []
        self.server = serve(self.handle, "127.0.0.1", 0)
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def handle(self, ws):
        url = urlsplit(ws.request.path)
        token = parse_qs(url.query).get("token", [""])[0]
        self.tokens.append(token)
        if url.path != "/api/v2/channels/samsung.remote.control" or (token != self.TOKEN and not self.accepts):
            ws.send(json.dumps({"event": "ms.channel.unauthorized"}))
            return
        ws.send(json.dumps({"event": "ms.channel.connect", "data": {} if token == self.TOKEN else {"token": self.TOKEN}}))
        for raw in ws:
            self.seen.append(json.loads(raw))


def tool(name, body):
    """a fake command-line tool: it appends its argv, HOME and stdin to <name>.log, then runs `body`"""
    path, log = TMP / name, TMP / f"{name}.log"
    path.write_text(f"#!{sys.executable}\nimport json, os, sys\nstdin = '' if sys.stdin.isatty() else sys.stdin.read()\n"
                    f"open({str(log)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'home': os.environ.get('HOME'), 'stdin': stdin,"
                    f" 'env': sorted(os.environ)}}) + '\\n')\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    log.write_text("")
    return str(path), lambda: [json.loads(line) for line in log.read_text().splitlines()]


class Logs(logging.Handler):
    """everything logged, at every level, while a test runs"""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(f"{record.name} {record.getMessage()} {record.exc_text or ''} {record.args!r}")

    def __enter__(self):
        root = logging.getLogger()
        self.level = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(self)
        return self

    def __exit__(self, *a):
        logging.getLogger().removeHandler(self)
        logging.getLogger().setLevel(self.level)


class Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = signed_in("admin")
        cls.member = signed_in("hwpat")  # a name no other test file uses: they share one database in a full run
        cls.pat = core.one("SELECT id FROM users WHERE name = 'hwpat'")["id"]

    def setUp(self):
        tv._tries.clear()
        core.db().execute("DELETE FROM tv_sets")
        for p in (mock.patch.object(tvhw, "ALSO", LOCAL), mock.patch.object(tvhw, "TLS", False), mock.patch.object(tvhw, "TIMEOUT", 2)):
            p.start()
            self.addCleanup(p.stop)

    def port(self, kind, port):
        p = mock.patch.dict(tvhw.PORTS, {kind: port})
        p.start()
        self.addCleanup(p.stop)

    def add(self, kind, host="127.0.0.1", who=None, **more):
        r = (who or self.admin).post("/api/tv/sets", json={"kind": kind, "name": "Den", "host": host, **more}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def press(self, sid, key, who=None):
        return (who or self.admin).post(f"/api/tv/hw/{sid}/press", json={"key": key}, headers=H)

    def pair(self, sid):
        return self.admin.post(f"/api/tv/sets/{sid}/test", headers=H)

    def secret(self, sid):
        return core.one("SELECT secret FROM tv_sets WHERE id = ?", sid)["secret"]


class Address(unittest.TestCase):
    """the check itself, with nothing let in for tests"""

    def test_the_lans_private_ranges_pass(self):
        for ip in ("10.0.0.7", "172.16.4.2", "172.31.255.254", "192.168.1.40", "fd12:3456:789a::5"):
            self.assertEqual(tvhw.checked_ip(ip), ip)

    def test_inside_docker_the_containers_own_networks_are_refused(self):
        """the other containers and Docker's gateway are private addresses too, but they're no TV"""
        with tempfile.TemporaryDirectory() as d:
            route, six, inside = Path(d, "route"), Path(d, "if_inet6"), Path(d, "dockerenv")
            route.write_text("Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
                             "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"      # default via 172.18.0.1
                             "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"      # 172.18.0.0/16, attached
                             "eth1\t0001A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n")     # 192.168.1.0/24, attached (the LAN)
            six.write_text("fd00000000000000000000000000dead 02 40 00 80 eth0\nfe800000000000000000000000000001 02 40 20 80 eth0\n")
            where = mock.patch.multiple(tvhw, ROUTES=str(route), ROUTES6=str(six), INSIDE=str(inside))
            with where, mock.patch.object(tvhw, "LAN", [ipaddress.ip_network("192.168.0.0/16")]):
                for ip in ("172.18.0.1", "172.18.0.5", "192.168.1.40", "fd00::5"):  # outside a container nothing is Docker's
                    self.assertEqual(tvhw.checked_ip(ip), ip)
                inside.touch()
                for ip in ("172.18.0.1", "172.18.0.5", "172.18.255.254", "fd00::5"):
                    with self.assertRaises(tvhw.Refused, msg=ip) as no:
                        tvhw.checked_ip(ip)
                    self.assertIn("containers", no.exception.detail)
                for ip in ("172.19.0.5", "10.0.0.7", "192.168.1.40", "fd12::5"):  # the LAN (in LAN_SUBNET) even when attached: host networking
                    self.assertEqual(tvhw.checked_ip(ip), ip)
                route.unlink()  # can't tell which networks are Docker's: every private address but LAN_SUBNET's is refused
                with self.assertRaises(tvhw.Refused):
                    tvhw.checked_ip("172.19.0.5")
                self.assertEqual(tvhw.checked_ip("192.168.1.40"), "192.168.1.40")

    def test_everything_else_is_refused(self):
        for ip in ("127.0.0.1", "127.8.8.8", "::1", "169.254.169.254", "169.254.1.1", "fe80::1", "224.0.0.251", "ff02::1", "0.0.0.0", "::",
                   "8.8.8.8", "172.32.0.1", "192.169.0.1", "100.64.0.1", "::ffff:127.0.0.1", "::ffff:192.168.1.40", "2001:4860:4860::8888",
                   "2130706433", "0x7f.1", "localhost"):
            with self.assertRaises(tvhw.Refused, msg=ip):
                tvhw.checked_ip(ip)

    def test_an_address_is_a_name_or_a_number_and_nothing_more(self):
        for host in ("", "http://192.168.1.40", "192.168.1.40/x", "192.168.1.40:22", "a@192.168.1.40", "192.168.1.40 x", "-oProxy", "x" * 300, "a\nb"):
            with self.assertRaises(tvhw.Refused, msg=host):
                tvhw.checked_ip(host)

    def test_a_name_is_resolved_once_and_every_address_it_has_is_checked(self):
        patch, calls = resolves({"den-tv.lan": ["192.168.1.40"], "sly.lan": ["192.168.1.40", "169.254.169.254"], "six.lan": ["192.168.1.40", "::1"]})
        with patch:
            self.assertEqual(tvhw.checked_ip("den-tv.lan"), "192.168.1.40")
            self.assertEqual(calls, ["den-tv.lan"])
            for name in ("sly.lan", "six.lan"):
                with self.assertRaises(tvhw.Refused):
                    tvhw.checked_ip(name)

    def test_a_name_that_is_not_there_is_refused_plainly(self):
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror("nope")), self.assertRaises(tvhw.Refused) as e:
            tvhw.checked_ip("gone.lan")
        self.assertIn("find", str(e.exception))


class RokuAndTheRules(Case):
    def setUp(self):
        super().setUp()
        self.roku = Roku()
        self.port("roku", self.roku.port)

    def test_the_buttons_reach_a_roku(self):
        sid = self.add("roku")
        for key in ("power", "volup", "voldown", "mute", "hdmi2"):
            self.assertEqual(self.press(sid, key).status_code, 200, key)
        self.assertEqual(self.roku.seen, ["/keypress/Power", "/keypress/VolumeUp", "/keypress/VolumeDown", "/keypress/VolumeMute", "/keypress/InputHDMI2"])

    def test_only_an_admin_adds_changes_tests_or_removes_a_set(self):
        sid = self.add("roku")
        m = self.member
        self.assertEqual(m.post("/api/tv/sets", json={"kind": "roku", "name": "Mine", "host": "192.168.1.9"}, headers=H).status_code, 403)
        self.assertEqual(m.get("/api/tv/sets").status_code, 403)
        self.assertEqual(m.put(f"/api/tv/sets/{sid}/users", json={"users": [self.pat]}, headers=H).status_code, 403)
        self.assertEqual(m.post(f"/api/tv/sets/{sid}/test", headers=H).status_code, 403)
        self.assertEqual(m.delete(f"/api/tv/sets/{sid}", headers=H).status_code, 403)
        self.assertEqual(len(self.admin.get("/api/tv/sets").json()["sets"]), 1)
        self.assertEqual(self.admin.delete(f"/api/tv/sets/{sid}", headers=H).status_code, 200)
        self.assertEqual(self.admin.get("/api/tv/sets").json()["sets"], [])

    def test_an_address_off_the_lan_is_refused_when_adding(self):
        tvhw.ALSO = set()  # as Parlor runs: the tests' own 127.0.0.1 isn't let in either (setUp's patch puts it back)
        for host in ("8.8.8.8", "127.0.0.1", "127.0.0.2", "169.254.169.254", "localhost", "http://192.168.1.4/", ""):
            r = self.admin.post("/api/tv/sets", json={"kind": "roku", "name": "Den", "host": host}, headers=H)
            self.assertEqual(r.status_code, 400, host)
        self.assertEqual(self.admin.post("/api/tv/sets", json={"kind": "betamax", "name": "Den", "host": "192.168.1.4"}, headers=H).status_code, 422)
        self.assertEqual(core.rows("SELECT id FROM tv_sets"), [])

    def test_a_member_presses_only_the_sets_they_were_given(self):
        sid = self.add("roku")
        self.assertEqual(self.member.get("/api/tv/hw").json()["sets"], [])
        self.assertEqual(self.press(sid, "volup", self.member).status_code, 404)
        self.assertEqual(self.roku.seen, [])
        self.assertEqual(self.admin.put(f"/api/tv/sets/{sid}/users", json={"users": [self.pat]}, headers=H).status_code, 200)
        mine = self.member.get("/api/tv/hw").json()["sets"]
        self.assertEqual([(s["id"], s["name"], s["kind"]) for s in mine], [(sid, "Den", "roku")])
        self.assertIn("power", mine[0]["keys"])
        self.assertNotIn("host", mine[0], "a member has no use for the address")
        self.assertEqual(self.press(sid, "volup", self.member).status_code, 200)
        self.assertEqual(self.admin.get("/api/tv/sets").json()["sets"][0]["users"], [self.pat])
        self.assertEqual(self.admin.put(f"/api/tv/sets/{sid}/users", json={"users": []}, headers=H).status_code, 200)
        self.assertEqual(self.press(sid, "volup", self.member).status_code, 404)
        self.assertEqual(self.roku.seen, ["/keypress/VolumeUp"])
        self.assertEqual(self.admin.put(f"/api/tv/sets/{sid}/users", json={"users": [99999]}, headers=H).status_code, 404)

    def test_only_the_buttons_on_the_list(self):
        sid = self.add("roku")
        self.assertEqual(self.press(sid, "Home/../../query/apps").status_code, 422)
        self.assertEqual(self.press(sid, "input").status_code, 400, "a Roku has no next-input key")
        self.assertEqual(self.press(99999, "power").status_code, 404)
        self.assertEqual(self.roku.seen, [])

    def test_the_name_is_checked_again_on_every_press_and_the_press_goes_to_that_address(self):
        patch, calls = resolves({"den-tv.lan": ["127.0.0.1"]})
        with patch:
            sid = self.add("roku", host="den-tv.lan")
            self.assertEqual(self.press(sid, "power").status_code, 200)  # the name exists nowhere: only the checked address can have worked
            self.assertEqual(calls, ["den-tv.lan"] * 2)
        moved, _ = resolves({"den-tv.lan": ["127.0.0.1", "169.254.169.254"]})  # the name now points somewhere else as well
        with moved:
            r = self.press(sid, "power")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.roku.seen, ["/keypress/Power"])

    def test_a_redirect_is_not_followed(self):
        other = Roku()
        self.roku.status, self.roku.location = 307, f"http://127.0.0.1:{other.port}/keypress/Power"
        self.assertEqual(self.press(self.add("roku"), "power").status_code, 502)
        self.assertEqual(other.seen, [])

    def test_a_roku_that_refuses_says_which_setting(self):
        self.roku.status = 403
        r = self.press(self.add("roku"), "power")
        self.assertEqual(r.status_code, 502)
        self.assertIn("Control by mobile apps", r.json()["detail"])

    def test_a_set_that_never_answers_gives_up_in_time(self):
        with socket.socket() as dead, mock.patch.object(tvhw, "TIMEOUT", 0.3):
            dead.bind(("127.0.0.1", 0))
            dead.listen(1)  # takes the connection, says nothing
            self.port("roku", dead.getsockname()[1])
            began = time.time()
            r = self.press(self.add("roku"), "power")
        self.assertEqual(r.status_code, 502)
        self.assertLess(time.time() - began, 3)

    def test_presses_are_rate_limited(self):
        sid = self.add("roku")
        with mock.patch.dict(tv.LIMITS, {"hw": 3}):
            self.assertEqual([self.press(sid, "volup").status_code for _ in range(4)], [200, 200, 200, 429])
        self.assertEqual(len(self.roku.seen), 3)

    def test_one_press_at_a_time_on_a_set(self):
        sid = self.add("roku")
        with tvhw._busy[sid]:
            with mock.patch.object(tvhw, "TIMEOUT", 0.2):
                self.assertEqual(self.press(sid, "volup").status_code, 429)
        self.assertEqual(self.press(sid, "volup").status_code, 200)

    def test_removing_an_account_takes_its_access_with_it(self):
        sid = self.add("roku")
        gone = signed_in("sam")
        uid = core.one("SELECT id FROM users WHERE name = 'sam'")["id"]
        self.admin.put(f"/api/tv/sets/{sid}/users", json={"users": [uid, self.pat]}, headers=H)
        core.db().execute("DELETE FROM users WHERE id = ?", (uid,))
        self.assertEqual(self.admin.get("/api/tv/sets").json()["sets"][0]["users"], [self.pat])
        self.assertIn(self.press(sid, "volup", gone).status_code, (401, 404))


class LgWebos(Case):
    def start(self, **kw):
        self.lg = Lg(**kw)
        self.port("webos", self.lg.port)
        return self.add("webos")

    def test_pairing_asks_on_the_tv_and_keeps_the_key_where_only_the_database_has_it(self):
        sid = self.start()
        self.assertEqual(self.press(sid, "volup").status_code, 409, "not paired yet")
        with Logs() as logs:
            r = self.pair(sid)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self.lg.prompts, 1)
            self.assertEqual(self.secret(sid), Lg.KEY)
            self.assertEqual(self.press(sid, "volup").status_code, 200)
            said = r.text + self.admin.get("/api/tv/sets").text + self.admin.get("/api/tv/hw").text
        self.assertTrue(self.admin.get("/api/tv/sets").json()["sets"][0]["paired"])
        self.assertNotIn(Lg.KEY, said)
        self.assertNotIn("secret", said)
        self.assertNotIn(Lg.KEY, "\n".join(logs.lines))
        self.assertEqual(self.lg.registered_with, [None, Lg.KEY])
        self.assertEqual(self.lg.prompts, 1, "a press with the key asks nothing on the TV")

    def test_the_buttons_reach_an_lg(self):
        sid = self.start()
        self.pair(sid)
        for key in ("volup", "voldown", "mute", "hdmi2", "power"):
            self.assertEqual(self.press(sid, key).status_code, 200, key)
        self.assertEqual(self.lg.seen, [("ssap://audio/volumeUp", None), ("ssap://audio/volumeDown", None), ("ssap://audio/getStatus", None),
                                        ("ssap://audio/setMute", {"mute": True}), ("ssap://tv/switchInput", {"inputId": "HDMI_2"}),
                                        ("ssap://system/turnOff", None)])

    def test_nobody_accepting_on_the_tv_ends_the_wait(self):
        sid = self.start(accepts=False)
        with mock.patch.object(tvhw, "PAIR_WAIT", 0.4):
            r = self.pair(sid)
        self.assertEqual(r.status_code, 502)
        self.assertIn("Accept", r.json()["detail"])
        self.assertEqual(self.secret(sid), "")

    def test_a_tv_that_forgot_the_key_is_not_asked_from_a_button(self):
        sid = self.start(accepts=False)
        core.db().execute("UPDATE tv_sets SET secret = 'an-old-key' WHERE id = ?", (sid,))
        with mock.patch.object(tvhw, "TIMEOUT", 0.4):
            r = self.press(sid, "volup")
        self.assertEqual(r.status_code, 409)
        self.assertNotIn("an-old-key", r.text)
        self.assertEqual(self.lg.seen, [])

    def test_power_wakes_a_set_that_is_off_over_the_lan(self):
        with socket.socket() as dead, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wol:
            dead.bind(("127.0.0.1", 0))  # bound and not listening: connections are refused, as by a TV in standby
            wol.bind(("127.0.0.1", 0))
            wol.settimeout(3)
            self.port("webos", dead.getsockname()[1])
            sid = self.add("webos", mac="AA:bb:CC:00:11:22")
            core.db().execute("UPDATE tv_sets SET secret = 'k' WHERE id = ?", (sid,))
            with mock.patch.object(tvhw, "WOL_PORT", wol.getsockname()[1]):
                self.assertEqual(self.press(sid, "power").status_code, 200)
                self.assertEqual(wol.recv(200), b"\xff" * 6 + bytes.fromhex("aabbcc001122") * 16)
                self.assertEqual(self.press(sid, "volup").status_code, 502, "only power wakes it")
        r = self.admin.post("/api/tv/sets", json={"kind": "webos", "name": "Den", "host": "127.0.0.1", "mac": "not a mac"}, headers=H)
        self.assertEqual(r.status_code, 422)


class SamsungTizen(Case):
    def start(self, **kw):
        self.tv = Samsung(**kw)
        self.port("tizen", self.tv.port)
        return self.add("tizen")

    def test_pairing_keeps_the_token_and_sends_it_from_then_on_without_logging_it(self):
        sid = self.start()
        with Logs() as logs:
            r = self.pair(sid)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self.secret(sid), Samsung.TOKEN)
            self.assertEqual(self.press(sid, "volup").status_code, 200)
            said = r.text + self.admin.get("/api/tv/sets").text + self.admin.get("/api/tv/hw").text
        self.assertEqual(self.tv.tokens, ["", Samsung.TOKEN])
        self.assertNotIn(Samsung.TOKEN, said)
        self.assertNotIn(Samsung.TOKEN, "\n".join(logs.lines))

    def test_the_buttons_reach_a_samsung(self):
        sid = self.start()
        self.pair(sid)
        for key in ("power", "volup", "voldown", "mute", "input", "hdmi3"):
            self.assertEqual(self.press(sid, key).status_code, 200, key)
        for _ in range(40):
            if len(self.tv.seen) == 6:
                break
            time.sleep(0.05)
        self.assertEqual([m["params"]["DataOfCmd"] for m in self.tv.seen], ["KEY_POWER", "KEY_VOLUP", "KEY_VOLDOWN", "KEY_MUTE", "KEY_SOURCE", "KEY_HDMI3"])
        self.assertEqual({(m["method"], m["params"]["Cmd"], m["params"]["TypeOfRemote"]) for m in self.tv.seen}, {("ms.remote.control", "Click", "SendRemoteKey")})

    def test_a_samsung_that_says_no(self):
        sid = self.start(accepts=False)
        r = self.pair(sid)
        self.assertEqual(r.status_code, 502)
        self.assertIn("Allow", r.json()["detail"])
        self.assertEqual(self.press(sid, "volup").status_code, 409)
        self.assertEqual(self.tv.seen, [])


class AndroidAdb(Case):
    def start(self, body="print('connected to x')"):
        adb, self.ran = tool("adb", "if sys.argv[1] == 'keygen':\n    open(sys.argv[2], 'w').write('PRIVATE-ADB-KEY')\n    sys.exit(0)\n" + body)
        for p in (mock.patch.object(tvhw, "ADB", adb), mock.patch.object(tvhw, "_adb_home", None)):
            p.start()
            self.addCleanup(p.stop)
        self.port("adb", 5599)
        core.db().execute("DELETE FROM settings WHERE key = 'tv_adb_key'")
        return self.add("adb")

    def test_the_buttons_are_key_numbers_in_adbs_argv(self):
        sid = self.start()
        for key in ("power", "volup", "voldown", "mute", "input", "hdmi1"):
            self.assertEqual(self.press(sid, key).status_code, 200, key)
        ran = [r["argv"] for r in self.ran() if r["argv"][0] != "keygen"]
        self.assertEqual(ran[:2], [["connect", "127.0.0.1:5599"], ["-s", "127.0.0.1:5599", "shell", "input", "keyevent", "26"]])
        self.assertEqual([a[-1] for a in ran[1::2]], ["26", "24", "25", "164", "178", "243"])
        self.assertTrue(all(a[:5] == ["-s", "127.0.0.1:5599", "shell", "input", "keyevent"] and a[5].isdigit() and len(a) == 6 for a in ran[1::2]))

    def test_adbs_own_key_is_kept_in_the_database_and_given_back_after_a_restart(self):
        sid = self.start()
        self.assertEqual(self.press(sid, "volup").status_code, 200)
        self.assertEqual(core.setting("tv_adb_key"), "PRIVATE-ADB-KEY")
        home = self.ran()[-1]["home"]
        self.assertNotIn(str(core.DATA), home, "not beside the database, in the clear")
        self.assertEqual(stat.S_IMODE(os.stat(home).st_mode), 0o700)
        with mock.patch.object(tvhw, "_adb_home", None):  # Parlor restarted: a new folder, the same key
            self.assertEqual(self.press(sid, "volup").status_code, 200)
        again = self.ran()[-1]["home"]
        self.assertNotEqual(again, home)
        self.assertEqual((Path(again) / ".android" / "adbkey").read_text(), "PRIVATE-ADB-KEY")
        self.assertEqual([r["argv"][0] for r in self.ran()].count("keygen"), 1)
        self.assertNotIn("PRIVATE-ADB-KEY", self.admin.get("/api/tv/sets").text)
        self.assertNotIn("PARLOR_SESSION", " ".join(self.ran()[-1]["env"]))

    def test_a_tv_that_has_not_allowed_debugging_says_so(self):
        sid = self.start("print('failed to authenticate to 127.0.0.1:5599')")
        r = self.pair(sid)
        self.assertEqual(r.status_code, 502)
        self.assertIn("Allow", r.json()["detail"])
        self.assertNotIn("keyevent", json.dumps(self.ran()))

    def test_without_adb_on_the_server_it_says_so(self):
        sid = self.start()
        with mock.patch.object(tvhw, "ADB", str(TMP / "no-such-adb")):
            r = self.press(sid, "volup")
        self.assertEqual(r.status_code, 502)
        self.assertIn("adb isn't installed", r.json()["detail"])

    def test_an_adb_that_hangs_is_stopped(self):
        sid = self.start("import time\ntime.sleep(30)")
        with mock.patch.object(tvhw, "TIMEOUT", 0.5):
            began = time.time()
            self.assertEqual(self.press(sid, "volup").status_code, 502)
        self.assertLess(time.time() - began, 5)

    def test_leftovers_removing_an_adb_set_disconnects_its_address(self):
        sid = self.start()
        self.assertEqual(self.press(sid, "volup").status_code, 200)
        self.assertEqual(self.admin.delete(f"/api/tv/sets/{sid}", headers=H).status_code, 200)
        self.assertEqual(self.ran()[-1]["argv"], ["disconnect", "127.0.0.1:5599"])

    def test_leftovers_a_failing_disconnect_never_fails_the_remove(self):
        for body in ("if sys.argv[1] == 'disconnect':\n    sys.exit(1)\nprint('connected to x')", "import time\ntime.sleep(30)"):
            sid = self.start(body)
            with mock.patch.object(tvhw, "TIMEOUT", 0.3):
                self.assertEqual(self.admin.delete(f"/api/tv/sets/{sid}", headers=H).status_code, 200)
            self.assertIsNone(core.one("SELECT id FROM tv_sets WHERE id = ?", sid))
        sid = self.start()
        with mock.patch.object(tvhw, "ADB", str(TMP / "no-such-adb")):  # adb gone, and the Remove still works
            self.assertEqual(self.admin.delete(f"/api/tv/sets/{sid}", headers=H).status_code, 200)


class Leftovers(Case):
    """small review leftovers: adb disconnect on Remove, a time limit on the name lookup, a logged failed press"""

    def test_leftovers_a_name_lookup_that_hangs_is_refused_in_seconds(self):
        stuck = threading.Event()
        self.addCleanup(stuck.set)
        with mock.patch("socket.getaddrinfo", lambda *a, **k: stuck.wait(30)), mock.patch.object(tvhw, "RESOLVE_WAIT", 0.3):
            began = time.time()
            with self.assertRaises(tvhw.Refused) as e:
                tvhw.checked_ip("slow.lan")
        self.assertLess(time.time() - began, 3)
        self.assertIn("too long", e.exception.detail)

    def test_leftovers_a_failed_press_is_logged_with_the_set_and_never_its_secret(self):
        sid = self.add("tizen")
        core.db().execute("UPDATE tv_sets SET secret = 'TOKEN-1234' WHERE id = ?", (sid,))
        boom = ValueError("wss://127.0.0.1:8002/?token=TOKEN-1234")
        with mock.patch.dict(tvhw.DRIVERS, {"tizen": mock.Mock(side_effect=boom)}), self.assertLogs("parlor.tvhw", "WARNING") as got:
            self.assertEqual(self.press(sid, "volup").status_code, 502)
        text = "\n".join(got.output)
        self.assertIn(f"TV set {sid} (tizen)", text)
        self.assertIn("ValueError", text)
        self.assertNotIn("TOKEN-1234", text)


class HdmiCec(Case):
    def start(self, power="on"):
        cec, self.ran = tool("cec-client", f"print('opening a connection to the CEC adapter...')\nif stdin.strip() == 'pow 0':\n    print('power status: {power}')")
        p = mock.patch.object(tvhw, "CEC", cec)
        p.start()
        self.addCleanup(p.stop)
        return self.add("cec", host="")

    def test_the_buttons_are_lines_from_a_fixed_list_on_cec_clients_stdin(self):
        sid = self.start()
        for key in ("volup", "voldown", "mute", "hdmi2", "power"):
            self.assertEqual(self.press(sid, key).status_code, 200, key)
        self.assertEqual([r["stdin"] for r in self.ran()], ["volup\n", "voldown\n", "mute\n", "tx 1F:82:20:00\n", "pow 0\n", "standby 0\n"])
        self.assertEqual({tuple(r["argv"]) for r in self.ran()}, {("-s", "-d", "1")})

    def test_power_switches_on_a_tv_in_standby(self):
        sid = self.start(power="standby")
        self.assertEqual(self.press(sid, "power").status_code, 200)
        self.assertEqual([r["stdin"] for r in self.ran()], ["pow 0\n", "on 0\n"])

    def test_a_cec_set_has_no_address_and_no_next_input_key(self):
        sid = self.start()
        self.assertEqual(self.press(sid, "input").status_code, 400)
        r = self.admin.post("/api/tv/sets", json={"kind": "cec", "name": "Den", "host": "192.168.1.4"}, headers=H)
        self.assertEqual(r.status_code, 400)

    def test_without_cec_client_on_the_server_it_says_so(self):
        sid = self.start()
        with mock.patch.object(tvhw, "CEC", str(TMP / "no-such-cec")):
            r = self.pair(sid)
        self.assertEqual(r.status_code, 502)
        self.assertIn("cec-client isn't installed", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
