"""The installable app's server side: /sw.js is filled in with the app's files and a version, is never cached, every
file it keeps is served without a sign-in, a changed file changes the version, and OFFLINE=off serves the off switch."""
import json
import os
import re
import unittest
from unittest import mock

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import pwa


def filled(text):
    return {k: json.loads(re.search(rf"const {k} = (.+?);", text).group(1)) for k in ("VERSION", "SHELL", "OFF")}


class Worker(unittest.TestCase):
    def setUp(self):
        pwa.worker.cache_clear()
        self.addCleanup(pwa.worker.cache_clear)

    def test_served_filled_in_and_never_cached(self):
        r = env.client().get("/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r.headers["content-type"])
        self.assertEqual(r.headers["cache-control"], "no-cache")
        got = filled(r.text)
        self.assertRegex(got["VERSION"], r"^[0-9a-f]{12}$")
        self.assertFalse(got["OFF"])
        self.assertIn("js/player.js", got["SHELL"])
        self.assertIn("icon-maskable.png", got["SHELL"])
        self.assertNotIn("__", r.text.split("*/", 1)[1], "every placeholder filled")

    def test_every_kept_file_is_served_to_anyone(self):
        c = env.client()  # not signed in: the app has to open to show its sign-in
        for f in filled(c.get("/sw.js").text)["SHELL"]:
            self.assertEqual(c.get("/" + f.removeprefix("./")).status_code, 200, f)

    def test_a_changed_file_changes_the_version(self):
        before = filled(pwa.worker())["VERSION"]
        pwa.worker.cache_clear()
        real = pwa.Path.read_bytes
        with mock.patch.object(pwa.Path, "read_bytes", lambda p: real(p) + (b"/* changed */" if p.name == "app.css" else b"")):
            self.assertNotEqual(filled(pwa.worker())["VERSION"], before)

    def test_the_off_switch(self):
        with mock.patch.dict(os.environ, {"OFFLINE": "off"}):
            self.assertTrue(filled(env.client().get("/sw.js").text)["OFF"])

    def test_the_manifest_installs(self):
        m = env.client().get("/manifest.webmanifest").json()
        icons = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"] if i["type"] == "image/png"}
        self.assertTrue({("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= icons)
        self.assertEqual((m["display"], m["start_url"]), ("standalone", "./"))


if __name__ == "__main__":
    unittest.main()
