"""The VPN from Settings: a WireGuard file uploaded after the stack is running, checked and saved;
nothing gets out until there is one, unless you turn the VPN off (which needs confirming).
Gluetun's front door (app/gluetun-front.sh) runs here with a fake iptables and a fake Gluetun."""
import os
import shutil
import signal
import stat
import subprocess
import time
import unittest
from unittest import mock

from yeerr_test_env import H, TMP, signed_in

import stack

GOOD = """[Interface]
PrivateKey = cHJpdmF0ZWtleXByaXZhdGVrZXlwcml2YXRla2V5cHI=
Address = 10.64.0.2/32
DNS = 10.64.0.1

[Peer]
PublicKey = cHVibGlja2V5cHVibGlja2V5cHVibGlja2V5cHVibGk=
AllowedIPs = 0.0.0.0/0
Endpoint = 203.0.113.9:51820
"""


class Check(unittest.TestCase):
    def test_a_good_file_is_kept_as_it_is(self):
        self.assertEqual(stack.check_wireguard(GOOD), GOOD)

    def test_a_host_name_becomes_the_address_gluetun_needs(self):
        with mock.patch.object(stack.socket, "getaddrinfo", return_value=[(2, 2, 17, "", ("198.51.100.7", 51820))]):
            out = stack.check_wireguard(GOOD.replace("203.0.113.9", "vpn.example.net"))
        self.assertIn("Endpoint = 198.51.100.7:51820\n", out)

    def test_whats_wrong_is_said_plainly(self):
        for text, words in ((GOOD.replace("PrivateKey", "Secret"), "no PrivateKey in [Interface]"),
                            (GOOD.replace("Endpoint = 203.0.113.9:51820\n", ""), "no Endpoint in [Peer]"),
                            (GOOD + "\n[Peer]\nPublicKey = x\nEndpoint = 192.0.2.1:1\n", "one [Peer]"),
                            ("hello", "isn't a WireGuard file"),
                            (GOOD.replace(":51820", ""), "should look like"),
                            (GOOD.replace(":51820", ":0"), "should look like"),
                            (GOOD.replace(":51820", ":99999"), "should look like"),
                            (GOOD.replace(":51820", ":５１８２０"), "should look like"),
                            (GOOD.replace("cHJpdmF0ZWtleXByaXZhdGVrZXlwcml2YXRla2V5cHI=", "secret"), "PrivateKey isn't a WireGuard key"),
                            (GOOD.replace("cHVibGlja2V5cHVibGlja2V5cHVibGlja2V5cHVibGk=", "c2hvcnQ="), "PublicKey isn't a WireGuard key"),
                            (GOOD.replace("10.64.0.2/32", "home"), "Address in [Interface] should be like")):
            with self.assertRaises(ValueError) as e:
                stack.check_wireguard(text)
            self.assertIn(words, str(e.exception))
        with mock.patch.object(stack.socket, "getaddrinfo", side_effect=OSError("no such host")), self.assertRaises(ValueError) as e:
            stack.check_wireguard(GOOD.replace("203.0.113.9", "nowhere.invalid"))
        self.assertIn("Couldn't look up nowhere.invalid", str(e.exception))


class Api(unittest.TestCase):
    def setUp(self):
        self.root = TMP / "vpn-stack"
        shutil.rmtree(self.root, ignore_errors=True)
        (self.root / "gluetun").mkdir(parents=True)
        for p in (mock.patch.object(stack, "STACK", self.root), mock.patch.dict("os.environ", {"STACK": "1"})):
            p.start()
            self.addCleanup(p.stop)
        self.g = self.root / "gluetun"
        self.c = signed_in("admin")

    def test_upload_replace_remove(self):
        (self.g / "yeerr-vpn.state").write_text("waiting\n")
        r = self.c.get("/api/stack/vpn").json()
        self.assertEqual((r["state"], r["file"], r["off"]), ("waiting", False, False))
        self.assertIn("not the internet", r["words"])
        r = self.c.put("/api/stack/vpn", json={"conf": GOOD}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        conf = self.g / "wireguard" / "wg0.conf"
        self.assertEqual(conf.read_text(), GOOD)
        self.assertEqual(stat.S_IMODE(conf.stat().st_mode), 0o600, "the private key is readable by root only")
        self.assertEqual(r.json()["endpoint"], "203.0.113.9:51820")
        self.assertNotIn("PrivateKey", self.c.get("/api/stack/vpn").text, "keys never leave the server")
        self.c.put("/api/stack/vpn", json={"conf": GOOD.replace("203.0.113.9", "192.0.2.4")}, headers=H)
        self.assertIn("192.0.2.4", conf.read_text(), "a new file replaces the old one")
        self.assertEqual(self.c.put("/api/stack/vpn", json={"conf": "nope"}, headers=H).status_code, 422)
        self.assertIn("192.0.2.4", conf.read_text(), "a bad file leaves the good one alone")
        self.assertFalse(self.c.delete("/api/stack/vpn", headers=H).json()["file"])
        self.assertFalse(conf.exists())

    def test_off_needs_confirming_and_a_new_file_turns_it_back_on(self):
        r = self.c.put("/api/stack/vpn/off", json={"off": True}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertFalse((self.g / "yeerr-vpn-off").exists())
        self.assertTrue(self.c.put("/api/stack/vpn/off", json={"off": True, "confirm": True}, headers=H).json()["off"])
        self.assertFalse(self.c.put("/api/stack/vpn/off", json={"off": False}, headers=H).json()["off"], "turning it back on needs no confirming")
        self.c.put("/api/stack/vpn/off", json={"off": True, "confirm": True}, headers=H)
        self.assertFalse(self.c.put("/api/stack/vpn", json={"conf": GOOD}, headers=H).json()["off"], "uploading a file means you want the VPN")

    def test_what_the_page_says(self):
        (self.g / "wireguard").mkdir()
        (self.g / "wireguard" / "wg0.conf").write_bytes(b"[Peer]\nEndpoint\n\xff\xfe junk\n")
        state = self.g / "yeerr-vpn.state"
        state.write_text("broken: no firewall (is NET_ADMIN given?)\n")
        r = self.c.get("/api/stack/vpn").json()
        self.assertEqual((r["endpoint"], r["words"]), (None, "Can't run the VPN: no firewall (is NET_ADMIN given?)"), "a mangled file doesn't break the page")
        state.write_text("connected\n")
        os.utime(state, (time.time() - 600, time.time() - 600))
        r = self.c.get("/api/stack/vpn").json()
        self.assertEqual(r["state"], "stopped")
        self.assertIn("isn't running", r["words"], "a state nobody refreshes isn't believed")

    def test_admins_only_and_only_in_the_stack(self):
        member = signed_in("member-vpn")
        self.assertEqual(member.get("/api/stack/vpn").status_code, 403)
        self.assertEqual(member.put("/api/stack/vpn", json={"conf": GOOD}, headers=H).status_code, 403)
        shutil.rmtree(self.g)
        self.assertEqual(self.c.put("/api/stack/vpn", json={"conf": GOOD}, headers=H).status_code, 409)

    def test_setup_puts_the_front_door_in_place_and_moves_an_old_file(self):
        root = TMP / "vpn-init"
        shutil.rmtree(root, ignore_errors=True)
        (root / "gluetun").mkdir(parents=True)
        (root / "gluetun" / "wg0.conf").write_text(GOOD)
        done = stack.init_files(root)
        self.assertIn("gluetun: front door in place", done)
        self.assertEqual((root / "gluetun" / "wireguard" / "wg0.conf").read_text(), GOOD, "the old compose's file moves where Gluetun reads it")
        self.assertEqual((root / "gluetun" / "yeerr-vpn.sh").read_text(), stack.VPN_SCRIPT.read_text())
        self.assertNotIn("gluetun: front door in place", stack.init_files(root), "a second run changes nothing")


@unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
class FrontDoor(unittest.TestCase):
    """The script itself, with iptables and Gluetun faked: what it lets through, and when it (re)starts Gluetun."""

    entry = 'trap "exit 0" TERM\nwhile :; do sleep 0.1; done\n'  # a Gluetun that runs until stopped
    iptables = ""

    def setUp(self):
        self.d = TMP / "front"
        shutil.rmtree(self.d, ignore_errors=True)
        (self.d / "bin").mkdir(parents=True)
        (self.d / "g" / "wireguard").mkdir(parents=True)
        self.log = self.d / "iptables.log"
        fake_ipt = f'#!/bin/sh\necho "$(basename "$0") $*" >> {self.log}\n{self.iptables}'
        for name in ("iptables", "ip6tables"):
            (self.d / "bin" / name).write_text(fake_ipt)
        (self.d / "bin" / "ip").write_text(f'#!/bin/sh\necho "ip $*" >> {self.log}\nexit 1\n')  # nothing left to remove
        # a Gluetun that notes each start and says healthy when asked
        (self.d / "entry").write_text(f'#!/bin/sh\n[ "$1" = healthcheck ] && exit 0\necho start >> {self.d}/starts\n' + self.entry)
        for f in [*(self.d / "bin").iterdir(), self.d / "entry"]:
            f.chmod(0o755)
        env = {**os.environ, "PATH": f"{self.d / 'bin'}:{os.environ['PATH']}", "YEERR_VPN_DIR": str(self.d / "g"),
               "GLUETUN_ENTRYPOINT": str(self.d / "entry"), "YEERR_VPN_TICK": "0.1", "YEERR_VPN_BACKOFF": "0.2", "FIREWALL_OUTBOUND_SUBNETS": "192.168.7.0/24,172.16.0.0/12"}
        (self.d / "g" / "yeerr-vpn.state").write_text("connected\n")  # left from before a restart
        self.p = subprocess.Popen(["sh", str(stack.VPN_SCRIPT)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop)

    def stop(self):
        if self.p.poll() is None:
            self.p.send_signal(signal.SIGTERM)
            self.p.wait(5)

    def until(self, state, limit=5.0):
        end = time.time() + limit
        while time.time() < end:
            if stack.vpn_state(self.d / "g")["state"] == state:
                return True
            time.sleep(0.05)
        self.fail(f"never got to {state}; it's {stack.vpn_state(self.d / 'g')['state']}")

    def starts(self):
        f = self.d / "starts"
        return len(f.read_text().split()) if f.exists() else 0

    def test_blocked_until_a_file_then_gluetun_restarts_when_it_changes(self):
        self.until("waiting")
        rules = self.log.read_text()
        self.assertIn("iptables -P OUTPUT DROP", rules)
        self.assertIn("ip6tables -P OUTPUT DROP", rules)
        self.assertIn("iptables -A OUTPUT -d 192.168.7.0/24 -j ACCEPT", rules, "your home network still reaches qBittorrent")
        self.assertLess(rules.index("-P OUTPUT DROP"), rules.index("-F"), "closed before the old rules are cleared")
        self.assertEqual(self.starts(), 0)
        stack.save_wireguard(GOOD, self.d / "g")
        self.until("connected")
        self.assertEqual(self.starts(), 1)
        stack.save_wireguard(GOOD.replace("203.0.113.9", "192.0.2.4"), self.d / "g")
        end = time.time() + 5
        while self.starts() < 2 and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(self.starts(), 2, "a new file restarts Gluetun")
        (self.d / "g" / "wireguard" / "wg0.conf").unlink()
        self.until("waiting")

    def test_off_opens_and_on_closes_again(self):
        self.until("waiting")
        stack.vpn_off(True, self.d / "g")
        self.until("off")
        self.assertTrue(self.log.read_text().rstrip().endswith("-P FORWARD ACCEPT"))
        self.log.write_text("")
        stack.vpn_off(False, self.d / "g")
        self.until("waiting")
        self.assertIn("iptables -P OUTPUT DROP", self.log.read_text())

    def test_an_old_state_isnt_believed_before_the_firewall_is_up(self):
        self.until("waiting")
        self.assertLess(self.log.read_text().index("-P OUTPUT DROP"), 1_000_000)

    def test_stops_when_told(self):
        self.until("waiting")
        self.p.send_signal(signal.SIGTERM)
        self.assertEqual(self.p.wait(3), 0)


class FrontDoorCrash(FrontDoor):
    """A Gluetun that dies the first time: closed at once, started again."""
    entry = '[ "$(wc -l < "$(dirname "$0")/starts")" -eq 1 ] && { sleep 0.3; exit 1; }\ntrap "exit 0" TERM\nwhile :; do sleep 0.1; done\n'

    def test_blocked_until_a_file_then_gluetun_restarts_when_it_changes(self):
        self.until("waiting")
        stack.save_wireguard(GOOD, self.d / "g")
        end = time.time() + 5
        while self.starts() < 2 and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(self.starts(), 2, "started again after it died")
        rules = self.log.read_text().split("\n")
        drops = [i for i, line in enumerate(rules) if line == "iptables -P OUTPUT DROP"]
        self.assertGreaterEqual(len(drops), 3, "closed at start, and again when it died, before the restart")
        self.assertIn("ip -4 rule del table 51820", rules, "a killed Gluetun's routing rule is cleared, or the next one can't start")
        self.assertIn("ip link del tun0", rules)
        self.until("connected")

    test_off_opens_and_on_closes_again = test_stops_when_told = test_an_old_state_isnt_believed_before_the_firewall_is_up = None


class NoFirewall(FrontDoor):
    iptables = "exit 1\n"

    def test_it_stops_rather_than_run_open(self):
        self.assertEqual(self.p.wait(5), 1)
        self.assertEqual(stack.vpn_state(self.d / "g")["state"], "broken: no firewall (is NET_ADMIN given?)")

    test_blocked_until_a_file_then_gluetun_restarts_when_it_changes = test_off_opens_and_on_closes_again = None
    test_stops_when_told = test_an_old_state_isnt_believed_before_the_firewall_is_up = None


if __name__ == "__main__":
    unittest.main()
