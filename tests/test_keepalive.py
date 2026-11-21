"""A long answer on a connection that was just used is not cut short: uvicorn starts a request that arrived while
the one before was finishing with its keep-alive timer still running, and when that goes off (5 seconds, 1 here) it
closed the connection in the middle of the answer: an event stream to a TV, a film. app.py closes idle ones only."""
import socket
import threading
import time
import unittest

import yeerr_test_env  # noqa: F401  (importing the app is what sets the server right)
import uvicorn


async def tiny(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    if scope["path"] == "/slow":  # 2.5 seconds of answer, a piece every quarter second
        import asyncio
        for n in range(10):
            await send({"type": "http.response.body", "body": b"piece%d\n" % n, "more_body": True})
            await asyncio.sleep(0.25)
    await send({"type": "http.response.body", "body": b"end\n"})


class KeepAlive(unittest.TestCase):
    def test_a_long_answer_right_after_another_arrives_whole(self):
        server = uvicorn.Server(uvicorn.Config(tiny, host="127.0.0.1", port=0, log_level="critical", timeout_keep_alive=1, http="h11"))
        threading.Thread(target=server.run, daemon=True).start()
        while not server.started:
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
            s.sendall(b"GET /quick HTTP/1.1\r\nHost: x\r\n\r\nGET /slow HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")  # together: the second waits its turn
            got = b""
            while chunk := s.recv(65536):
                got += chunk
        server.should_exit = True
        self.assertIn(b"piece9", got, "the slow answer was cut off")
        self.assertEqual(got.count(b"end\n"), 2)

    def test_an_idle_connection_is_still_closed(self):
        server = uvicorn.Server(uvicorn.Config(tiny, host="127.0.0.1", port=0, log_level="critical", timeout_keep_alive=1, http="h11"))
        threading.Thread(target=server.run, daemon=True).start()
        while not server.started:
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
            s.sendall(b"GET /quick HTTP/1.1\r\nHost: x\r\n\r\n")
            t0, got = time.time(), b""
            while chunk := s.recv(65536):  # the answer, then nothing: the server hangs up after its second
                got += chunk
        server.should_exit = True
        self.assertIn(b"end\n", got)
        self.assertLess(time.time() - t0, 5)


if __name__ == "__main__":
    unittest.main()
