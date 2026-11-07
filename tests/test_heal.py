"""Self-healing yt-dlp: a download or lookup that fails because YouTube changed something
asks music.py for a new yt-dlp and waits for it, at most MAX_HEALS times; other failures don't."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yeerr_test_env  # noqa: F401  (sets up the data folders)

import app
import music
import yt

BROKEN = "ERROR: [youtube] kkkkkkkkkkk: nsig extraction failed: Some formats may be missing"
REMOVED = "ERROR: [youtube] kkkkkkkkkkk: Video unavailable. This video has been removed by the uploader"


def job():
    return {"job": "h1", "id": "kkkkkkkkkkk", "kind": "video", "quality": "720", "sponsorblock": False, "status": "queued",
            "stop": False, "title": "", "channel": "", "user_id": None}


def download(j, error):
    with mock.patch.object(yt.yt_dlp, "YoutubeDL") as ydl, mock.patch.object(yt.threading, "Timer") as timer, \
            mock.patch.object(yt.yt_dlp, "parse_options", return_value=mock.Mock(ydl_opts={})):
        ydl.return_value.__enter__.return_value.extract_info.side_effect = yt.yt_dlp.utils.DownloadError(error)
        yt.run_download(j)
    return timer


class Wiring(unittest.TestCase):
    def test_parlor_wires_yt_to_music(self):
        with mock.patch.object(music, "start_worker"), mock.patch.object(yt, "start"), \
                mock.patch.object(app.library, "start"), mock.patch.object(app.stack, "on", return_value=False), \
                mock.patch.object(yt, "heal_soon"), mock.patch.object(yt, "restarting"), mock.patch.object(music, "others_busy"):
            app.start()
            self.assertIs(yt.heal_soon, music.heal_soon)
            self.assertIs(music.others_busy, yt.busy)
            self.assertFalse(yt.restarting())
            for event in (music.healing, music.restart_wanted):
                event.set()
                try:
                    self.assertTrue(yt.restarting(), "replacing yt-dlp, and the restart to load it, hold new downloads")
                finally:
                    event.clear()

    def test_vendored_heal(self):
        """music.py's heal as vendored here: stable first, nightly when stable has nothing newer, then a restart."""
        tried = []
        with mock.patch.multiple(music, HEAL=True, UPDATE_HOURS=24, _healed=0.0, IDLE_POLL=0,
                                 HEALED=Path(tempfile.mkdtemp()) / "healed",
                                 update_ytdlp=lambda pre=False: tried.append(pre) or pre, others_busy=lambda: False):
            try:
                self.assertTrue(music.heal("ERROR: [youtube] x: nsig extraction failed"))
                self.assertEqual(tried, [False, True])
                self.assertTrue(music.restart_wanted.is_set())
                self.assertFalse(music.heal("ERROR: [youtube] x: nsig extraction failed"), "at most hourly")
            finally:
                music.restart_wanted.clear()


class Heal(unittest.TestCase):
    def setUp(self):
        self.healed = []
        patch = mock.patch.object(yt, "heal_soon", lambda why: self.healed.append(why) or music.looks_broken(why))
        patch.start()
        self.addCleanup(patch.stop)
        on = mock.patch.multiple(music, HEAL=True, UPDATE_HOURS=24)
        on.start()
        self.addCleanup(on.stop)

    def test_broken_download_waits_for_the_new_yt_dlp(self):
        j = job()
        timer = download(j, BROKEN)
        self.assertEqual(j["status"], "waiting")
        self.assertIn("waiting for a yt-dlp update", j["error"])
        self.assertIn("nsig extraction failed", self.healed[0])
        delay, again = timer.call_args[0]
        self.assertEqual(delay, yt.HEAL_WAIT)
        self.assertIn("waiting", yt.RUNNING, "a restart picks waiting jobs back up")
        with mock.patch.object(yt._pending, "put") as put:
            again()
        self.assertEqual(j["status"], "queued")
        put.assert_called_once_with(j)

    def test_real_yt_dlp_errors_reach_the_job(self):
        """yt-dlp's command-line defaults swallow extraction errors (extract_info returns None); a
        download must see the real error, or neither a rate limit nor a broken yt-dlp is ever noticed."""
        for error, status in (("The page needs to be reloaded.", "waiting"), ("Video unavailable. This video has been removed", "failed"),
                              ("The current session has been rate-limited by YouTube for up to an hour.", "waiting")):
            j = job()
            with mock.patch.object(yt.YoutubeIE, "_real_extract", side_effect=yt.yt_dlp.utils.ExtractorError(error, expected=True)), \
                    mock.patch.object(yt.threading, "Timer"):
                yt.run_download(j)
            self.assertEqual(j["status"], status, j["error"])
            self.assertNotIn("NoneType", j["error"])

    def test_at_most_three_tries(self):
        j = job()
        for _ in range(yt.MAX_HEALS):
            download(j, BROKEN)
            self.assertEqual(j["status"], "waiting")
            j["status"] = "queued"
        download(j, BROKEN)
        self.assertEqual(j["status"], "failed")
        self.assertIn("nsig extraction failed", j["error"])

    def test_other_failures_still_fail(self):
        for error in (REMOVED, "ERROR: [youtube] kkkkkkkkkkk: Private video. Sign in if you've been granted access"):
            j = job()
            download(j, error)
            self.assertEqual(j["status"], "failed", error)
        with mock.patch.object(music, "HEAL", False):
            j = job()
            download(j, BROKEN)
            self.assertEqual(j["status"], "failed", "HEAL=0: no waiting for a fix that won't come")

    def test_broken_lookup_heals_and_says_so(self):
        with mock.patch.object(yt.yt_dlp, "YoutubeDL") as ydl:
            ydl.return_value.__enter__.return_value.extract_info.side_effect = yt.yt_dlp.utils.DownloadError(BROKEN)
            with self.assertRaises(yt.HTTPException) as e:
                yt.extract("https://www.youtube.com/watch?v=kkkkkkkkkkk")
        self.assertEqual(e.exception.status_code, 503)
        self.assertIn("updating yt-dlp", e.exception.detail)
        self.assertTrue(self.healed)

    def test_held_while_a_restart_is_coming(self):
        j = job()
        waits = iter([True, True, False])
        with mock.patch.object(yt, "restarting", lambda: next(waits)), mock.patch.object(yt.time, "sleep") as sleep, \
                mock.patch.object(yt, "run_download") as run:
            yt.serve(j)
        self.assertEqual(sleep.call_count, 2)
        run.assert_called_once_with(j)
        clock = iter(range(0, 10 ** 6, 60))
        with mock.patch.object(yt, "restarting", lambda: True), mock.patch.object(yt.time, "sleep"), \
                mock.patch.object(yt.time, "time", lambda: next(clock)), mock.patch.object(yt, "run_download") as run:
            yt.serve(j)
        run.assert_called_once_with(j)  # a restart that never comes holds it for HOLD, not forever


if __name__ == "__main__":
    unittest.main()
