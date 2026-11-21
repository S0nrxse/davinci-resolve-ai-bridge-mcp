"""Gameplay activity map: speech protection, game-audio events, picture spikes, dead air, phantom words."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from bridge import activity as A

BINS = 240  # 60 s at 0.25 s per bin


def flat(value, bins=BINS):
    return [value] * bins


class ClassifyTests(unittest.TestCase):
    def test_speech_is_never_cut_even_on_a_frozen_menu(self):
        dead, totals = A.classify(BINS, [(20.0, 22.0)], flat(False), flat(0.0), [], [(15.0, 30.0)], "youtube")
        for d in dead:
            self.assertFalse(d["start_sec"] < 22.0 and d["end_sec"] > 20.0, d)
        self.assertGreater(totals["speech"], 2.0)

    def test_game_event_keeps_pre_and_post_roll(self):
        events = flat(False)
        events[160] = True  # 40 s
        dead, _ = A.classify(BINS, [], events, flat(0.0), [], [], "youtube")
        kept = [d for d in dead if d["start_sec"] <= 38.6 <= d["end_sec"] or d["start_sec"] <= 41.0 <= d["end_sec"]]
        self.assertEqual(kept, [])  # 38.5 s .. 41.25 s survives
        self.assertTrue(any(d["end_sec"] <= 38.5 for d in dead))

    def test_black_screen_is_dead_even_with_music(self):
        events = flat(True)  # loading-screen music trips the game-audio detector the whole time
        dead, _ = A.classify(BINS, [], events, flat(0.0), [(10.0, 20.0)], [], "youtube")
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0]["why"], "loading/menu")
        self.assertGreaterEqual(dead[0]["start_sec"], 10.0)
        self.assertLessEqual(dead[0]["end_sec"], 20.0)

    def test_min_dead_threshold_per_preset(self):
        events = flat(True)
        for b in range(80, 88):  # a 2 s quiet gap at 20-22 s
            events[b] = False
        short, _ = A.classify(BINS, [], events, flat(0.0), [], [], dict(A.PRESETS["shorts"], pre=0, post=0, keep=0))
        youtube, _ = A.classify(BINS, [], events, flat(0.0), [], [], dict(A.PRESETS["youtube"], pre=0, post=0, keep=0))
        self.assertEqual(len(short), 1)
        self.assertEqual(youtube, [])

    def test_motion_spikes_are_relative(self):
        steady = [0.05] * BINS
        steady[100] = 0.4
        spikes = A.motion_spikes(steady)
        self.assertTrue(spikes[100])
        self.assertEqual(sum(spikes), 1)  # constant motion (walking) isn't an event

    def test_game_events_relative_to_baseline(self):
        levels = [-30.0] * 1200  # 60 s of steady music at -30 dBFS in 50 ms hops
        for i in range(800, 840):  # a burst at 40-42 s
            levels[i] = -12.0
        events, _ = A.game_events(levels, BINS)
        self.assertTrue(any(events[160:168]))
        self.assertFalse(any(events[0:150]))

    def test_phantom_words_on_silence_are_dropped_quiet_speech_kept(self):
        mic = [-100.0] * 1200
        for i in range(328, 400):  # quiet real speech at 16.4-20 s around -55 dBFS
            mic[i] = -55.0
        kept, dropped = A.gate_words([(16.5, 17.0), (19.6, 19.9), (30.2, 31.0)], mic)
        self.assertEqual(kept, [(16.5, 17.0), (19.6, 19.9)])
        self.assertEqual(dropped, 1)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class IntegrationTests(unittest.TestCase):
    """A 20 s two-track recording: motion 0-8 s with a game burst at 3-4 s, frozen menu 8-20 s."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.path = d / "game.mp4"
        run = lambda *a: subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)
        run("-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=8", "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=12",
            "-filter_complex", "[0][1]concat=n=2:v=1:a=0,format=yuv420p[v]", "-map", "[v]", "-c:v", "libx264",
            "-preset", "ultrafast", str(d / "v.mp4"))
        run("-f", "lavfi", "-i", "sine=f=220:d=20:sample_rate=16000", "-f", "lavfi", "-i", "anoisesrc=d=20:a=0.6:r=16000",
            "-filter_complex", "[0]volume=0.03[m];[1]volume='between(t,3,4)':eval=frame[b];[m][b]amix=inputs=2:normalize=0[a]",
            "-map", "[a]", str(d / "game.wav"))
        run("-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "20", str(d / "mic.wav"))
        run("-i", str(d / "v.mp4"), "-i", str(d / "game.wav"), "-i", str(d / "mic.wav"), "-map", "0:v", "-map", "1:a",
            "-map", "2:a", "-c:v", "copy", "-c:a", "aac", "-shortest", str(cls.path))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_streams_and_dead_air(self):
        ff = shutil.which("ffmpeg")
        self.assertEqual(len(A.audio_streams(ff, self.path)), 2)
        src = {"item_id": "c", "name": "game", "file_path": str(self.path), "source_start_sec": 0.0, "duration_sec": 20.0,
               "timeline_start_frame": 0, "timeline_end_frame": 480, "fps": 24}
        res = A.analyze(src, ff, [], preset="youtube", game_stream=0, mic_stream=1)
        self.assertTrue(res["picture_checked"])
        cut = [(d["start_frame"] / 24, d["end_frame"] / 24) for d in res["dead"]]
        self.assertTrue(any(a <= 10 and b >= 19 for a, b in cut), cut)  # the frozen menu
        self.assertFalse(any(a < 4.0 and b > 3.0 for a, b in cut), cut)  # the burst survives


if __name__ == "__main__":
    unittest.main()
