"""Live check of Gluetun's front door (app/gluetun-front.sh) with the real Gluetun image and a real
WireGuard server in another container:
  no file    -> qBittorrent's network reaches the home network, not the internet
  off switch -> reaches the internet directly; back on -> blocked again
  a file     -> Gluetun starts, the tunnel connects, traffic goes through the server
  tunnel cut -> blocked (kill switch); Gluetun killed -> closed, restarted
  new file   -> Gluetun restarts on it and connects again
and a watcher in qBittorrent's place sees no leak at any moment from the first file on (the VPN
server drops one site, so reaching that site at all means traffic went around the tunnel)
Exit 0 when all of that holds. Files are saved with stack.save_wireguard, as yeerr's upload does.
Needs Docker, the WireGuard kernel module and internet; run it by hand: python tests/live_vpn.py"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="parlor-vpn-"))
os.environ.setdefault("DATA_DIR", str(TMP / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import stack  # noqa: E402

RUN = TMP / f"vpn-{int(time.time())}"
G, NET = RUN / "gluetun", RUN.name
GLUETUN, SERVER, WEB, WATCH = f"{NET}-gluetun", f"{NET}-wgserver", f"{NET}-web", f"{NET}-watch"


def sh(*cmd, check=False):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"{' '.join(cmd)}\n{r.stdout}{r.stderr}")
    return r


def ex(name, script, check=True):
    return sh("docker", "exec", name, "sh", "-c", script, check=check).stdout.strip()


def ip_of(name):
    return sh("docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name, check=True).stdout.strip()


SITE = socket.gethostbyname("example.com")  # reachable only directly: the VPN server drops it (addresses: no DNS needed)
OUT = next(ip for ip in (socket.gethostbyname(h) for h in ("www.debian.org", "github.com", "www.kernel.org")) if ip != SITE)


def probe():
    """What a container in Gluetun's network (qBittorrent's place) can reach."""
    r = sh("docker", "run", "--rm", "--network", f"container:{GLUETUN}", "alpine:3.20", "sh", "-c",
           f"nc -z -w4 {SITE} 80 && echo direct || echo no-direct; nc -z -w4 {OUT} 80 && echo out || echo no-out; "
           f"wget -q -T 3 -O - http://{ip_of(WEB)}/ 2>/dev/null | grep -q home && echo home || echo no-home")
    return set(r.stdout.split())


def wait_state(*want, limit=60):
    for _ in range(limit * 2):
        if stack.vpn_state(G)["state"] in want:
            return True
        time.sleep(0.5)
    print("  state stuck at", stack.vpn_state(G)["state"])
    return False


def received():
    out = ex(SERVER, "wg show wg0 transfer", check=False).split()
    return int(out[1]) if len(out) > 1 else 0


results = {}
try:
    sh("docker", "network", "create", NET, check=True)
    subnet = sh("docker", "network", "inspect", "-f", "{{range .IPAM.Config}}{{.Subnet}}{{end}}", NET, check=True).stdout.strip()
    sh("docker", "run", "-d", "--name", WEB, "--network", NET, "alpine:3.20", "sh", "-c",
       "while :; do printf 'HTTP/1.0 200 OK\r\n\r\nhome\n' | nc -l -p 80; done", check=True)
    sh("docker", "run", "-d", "--name", SERVER, "--network", NET, "--cap-add", "NET_ADMIN", "--sysctl", "net.ipv4.ip_forward=1",
       "alpine:3.20", "sleep", "infinity", check=True)
    ex(SERVER, "apk add -q wireguard-tools iptables dnsmasq")
    s_priv, c_priv = ex(SERVER, "wg genkey"), ex(SERVER, "wg genkey")
    s_pub, c_pub = ex(SERVER, f"echo {s_priv} | wg pubkey"), ex(SERVER, f"echo {c_priv} | wg pubkey")
    ex(SERVER, f"echo {s_priv} > /s.key && ip link add wg0 type wireguard && wg set wg0 listen-port 51820 private-key /s.key "
               f"peer {c_pub} allowed-ips 10.66.0.2/32 && ip addr add 10.66.0.1/24 dev wg0 && ip link set wg0 up && "
               f"iptables -t nat -A POSTROUTING -s 10.66.0.0/24 -o eth0 -j MASQUERADE && iptables -I FORWARD -d {SITE} -j DROP && "
               # this network allows no outside DNS: the server answers for the tunnel, like a VPN provider's does
               "dnsmasq --listen-address=10.66.0.1 --bind-interfaces --no-resolv --server=127.0.0.11")
    conf = (f"[Interface]\nPrivateKey = {c_priv}\nAddress = 10.66.0.2/32\n\n"
            f"[Peer]\nPublicKey = {s_pub}\nAllowedIPs = 0.0.0.0/0\nEndpoint = {ip_of(SERVER)}:51820\n")

    G.mkdir(parents=True)
    stack.install_vpn_script(RUN)
    # the "home network" here is the test network; the containers' range is the rest of 172.16/12
    sh("docker", "run", "-d", "--name", GLUETUN, "--network", NET, "--cap-add", "NET_ADMIN", "--device", "/dev/net/tun",
       "-e", "VPN_SERVICE_PROVIDER=custom", "-e", "VPN_TYPE=wireguard", "-e", f"FIREWALL_OUTBOUND_SUBNETS={subnet}",
       # no ICMP and no outside DNS here: health checks by TCP to an address, DNS through the server
       "-e", "DNS_UPSTREAM_RESOLVER_TYPE=plain", "-e", "DNS_UPSTREAM_PLAIN_ADDRESSES=10.66.0.1:53", "-e", "BLOCK_MALICIOUS=off",
       "-e", f"HEALTH_TARGET_ADDRESSES={OUT}:80", "-e", "HEALTH_SMALL_CHECK_TYPE=dns",
       "-v", f"{G}:/gluetun", "--entrypoint", "/bin/sh", "qmcgaw/gluetun:latest", "/gluetun/front-door.sh", check=True)

    results["waits without a file"] = wait_state("waiting", limit=20)
    p = probe()
    results["blocked: home yes, internet no"] = p == {"home", "no-direct", "no-out"}
    stack.vpn_off(True, G)
    results["off"] = wait_state("off", limit=10) and probe() == {"home", "direct", "out"}
    stack.vpn_off(False, G)
    results["back on: blocked again"] = wait_state("waiting", limit=10) and probe() == {"home", "no-direct", "no-out"}
    sh("docker", "run", "-d", "--name", WATCH, "--network", f"container:{GLUETUN}", "alpine:3.20", "sh", "-c",
       f"while :; do nc -z -w1 {SITE} 80 && echo LEAK $(date +%T); sleep 0.2; done", check=True)

    stack.save_wireguard(conf, G)
    results["a file starts the tunnel"] = wait_state("connected", limit=90)
    before = received()
    results["traffic goes through the server"] = probe() == {"home", "no-direct", "out"} and received() > before
    ex(SERVER, "ip link set wg0 down")
    time.sleep(3)
    results["tunnel cut: nothing gets out"] = probe() == {"home", "no-direct", "no-out"}
    ex(SERVER, "ip link set wg0 up")
    wait_state("connected", limit=90)
    ex(GLUETUN, "for p in $(pidof gluetun-entrypoint); do [ \"$(cut -d' ' -f4 /proc/$p/stat)\" = 1 ] && kill -9 $p; done", check=False)
    results["Gluetun killed: closed, then started again"] = wait_state("restarting", limit=15) and wait_state("connected", limit=90)

    starts = sh("docker", "logs", GLUETUN).stdout.count("parlor-vpn: starting")
    stack.save_wireguard(conf.replace("[Peer]", "MTU = 1380\n\n[Peer]"), G)
    time.sleep(8)
    results["a new file restarts Gluetun"] = sh("docker", "logs", GLUETUN).stdout.count("parlor-vpn: starting") == starts + 1
    results["and it connects again"] = wait_state("connected", limit=90) and "out" in probe()
    leaks = sh("docker", "logs", WATCH).stdout.split("\n")
    results["no leak at any moment"] = not any(ln.startswith("LEAK") for ln in leaks)
finally:
    print(sh("docker", "logs", "--tail", "15", GLUETUN).stdout[-2500:])
    for n in (WATCH, GLUETUN, SERVER, WEB):
        sh("docker", "rm", "-f", n)
    sh("docker", "network", "rm", NET)
for k, v in results.items():
    print(("✓ " if v else "✗ ") + k)
ok = len(results) == 11 and all(results.values())
print("RESULT:", "ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
