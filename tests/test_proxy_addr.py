"""Behind a reverse proxy: when FORWARDED_ALLOW_IPS names the proxy, Parlor counts each person by their own address
(X-Forwarded-For) for the limits on guessing sign-ins and TV codes; from anyone else the header is ignored, so it
can't be used to dodge them. Parlor served by uvicorn here, as in the image."""
import socket
import threading
import time
import unittest

import httpx

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import tv
import uvicorn
from yeerr_test_env import H


def serve(trusted):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=port, log_level="warning", forwarded_allow_ips=trusted))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", server


class Proxy(unittest.TestCase):
    def setUp(self):
        tv._tries.clear()

    def codes(self, base, who, n):
        return [httpx.post(f"{base}/api/tv/code", headers={**H, "X-Forwarded-For": who}).status_code for _ in range(n)]

    def test_behind_a_trusted_proxy_each_person_has_their_own_limit(self):
        base, server = serve("127.0.0.1")  # the proxy is on this machine
        try:
            self.assertEqual(self.codes(base, "203.0.113.5", tv.LIMITS["code"] + 1)[-1], 429, "one person past the limit waits")
            self.assertEqual(self.codes(base, "203.0.113.6", 1), [200], "someone else behind the same proxy doesn't")
        finally:
            server.should_exit = True

    def test_from_anyone_else_the_header_changes_nothing(self):
        base, server = serve("192.0.2.1")  # a proxy that isn't the one asking
        try:
            self.codes(base, "203.0.113.5", tv.LIMITS["code"])
            self.assertEqual(self.codes(base, "203.0.113.99", 1), [429], "a made-up address doesn't buy more tries")
        finally:
            server.should_exit = True


if __name__ == "__main__":
    unittest.main()
