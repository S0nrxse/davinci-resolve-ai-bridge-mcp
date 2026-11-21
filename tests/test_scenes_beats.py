"""Shot detection (ffmpeg scene scores) and beat tracking (pure-Python DP tracker)."""
import array
import math
import random
import shutil
import subprocess
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from bridge import beats, scenes

FFMPEG = shutil.which("ffmpeg")
SR = 22050


def click_track(bpm, seconds, offset=0.31, noise=200, accent_every=0, seed=1):
    """Synthetic clicks (decaying 1.5 kHz bursts) over light noise; returns (samples, beat times)."""
    rnd = random.Random(seed)
    n = int(seconds * SR)
    s = [int(rnd.gauss(0, noise)) for _ in range(n)]
    truth, t, i = [], offset, 0
    while t < seconds - 0.05:
        k = int(t * SR)
        gain = 14000 if accent_every and i % accent_every == 0 else 9000
        for j in range(int(0.012 * SR)):
            if k + j < n:
                s[k + j] += int(gain * math.exp(-j / (0.002 * SR)) * math.sin(2 * math.pi * 1500 * j / SR))
        truth.append(t)
        t += 60.0 / bpm
        i += 1
    return array.array("h", [max(-32768, min(32767, v)) for v in s]), truth


def hit_rate(truth, found, tolerance=0.04):
    return sum(1 for t in truth if any(abs(t - f) <= tolerance for f in found)) / float(len(truth))


SOURCE = {"item_id": "c1", "label": "V1.1", "name": "Clip", "track": "V1", "file_path": "", "source_start_sec": 0.0,
          "duration_sec": 10.0, "timeline_start_frame": 1000, "timeline_end_frame": 1240, "fps": 24}


class SceneParsingTests(unittest.TestCase):
    METADATA_LOG = """[Parsed_metadata_2 @ 0x8ad] frame:0    pts:24576   pts_time:2
[Parsed_metadata_2 @ 0x8ad] lavfi.scene_score=0.400000
[Parsed_metadata_2 @ 0x8ad] frame:1    pts:43008   pts_time:3.5
[Parsed_metadata_2 @ 0x8ad] lavfi.scene_score=0.910571
"""
    SCDET_LOG = """[Parsed_scdet_1 @ 0x741] lavfi.scd.score: 15.625, lavfi.scd.time: 2
[Parsed_scdet_1 @ 0x741] lavfi.scd.score: 35.569, lavfi.scd.time: 3.5
"""

    def test_parses_metadata_print(self):
        self.assertEqual(scenes.parse_scene_log(self.METADATA_LOG), [(2.0, 0.4), (3.5, 0.910571)])

    def test_parses_scdet(self):
        cuts = scenes.parse_scene_log(self.SCDET_LOG)
        self.assertEqual([c[0] for c in cuts], [2.0, 3.5])
        self.assertAlmostEqual(cuts[1][1], 0.35569)

    def test_min_gap_keeps_strongest_in_cluster(self):
        cuts = [(0.0, 0.9), (2.0, 0.4), (2.2, 0.8), (2.4, 0.5), (5.0, 0.6), (12.0, 0.9)]
        kept = scenes.filter_cuts(cuts, min_gap_sec=0.5, duration=10)
        self.assertEqual(kept, [(2.2, 0.8), (5.0, 0.6)])  # t=0 and past-the-end dropped

    def test_frame_mapping_and_clamping(self):
        mapped = scenes.to_timeline([(2.0, 0.5), (9.99, 0.5), (50.0, 0.5)], SOURCE)
        self.assertEqual([m["timeline_frame"] for m in mapped], [1048, 1239, 1239])

    def test_no_video_stream_returns_note(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            src = dict(SOURCE, file_path=f.name)
            failure = Mock(returncode=1, stderr=b"Stream map '0:v:0' matches no streams.")
            with patch("subprocess.run", return_value=failure):
                result = scenes.detect_shots(src, "ffmpeg")
        self.assertEqual(result["cuts"], [])
        self.assertIn("no video", result["note"])

    def test_errors_and_validation(self):
        with self.assertRaises(scenes.ShotError):
            scenes.detect_shots(dict(SOURCE, file_path="/nope/missing.mov"), "ffmpeg")
        with tempfile.NamedTemporaryFile(suffix=".mov") as f:
            with self.assertRaises(scenes.ShotError):
                scenes.detect_shots(dict(SOURCE, file_path=f.name), "ffmpeg", threshold=3)
            with patch("subprocess.run", return_value=Mock(returncode=1, stderr=b"Invalid data found")):
                with self.assertRaises(scenes.ShotError):
                    scenes.detect_shots(dict(SOURCE, file_path=f.name), "ffmpeg")
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 1)):
                with self.assertRaises(scenes.ShotError):
                    scenes.detect_shots(dict(SOURCE, file_path=f.name), "ffmpeg")


class BeatTrackerTests(unittest.TestCase):
    def check(self, bpm):
        samples, truth = click_track(bpm, 30, offset=0.17 + bpm / 1000.0)
        est, confidence, times, downs, note = beats.analyze_samples(samples, SR)
        self.assertAlmostEqual(est, bpm, delta=bpm * 0.02)
        self.assertGreaterEqual(hit_rate(truth, times), 0.9)
        self.assertGreater(confidence, 0.5)

    def test_100_bpm(self):
        self.check(100)

    def test_128_bpm(self):
        self.check(128)

    def test_range_of_tempi(self):
        for bpm in (72, 90, 140, 174):
            with self.subTest(bpm=bpm):
                self.check(bpm)

    def test_downbeats_follow_accents(self):
        samples, truth = click_track(120, 24, offset=0.5, accent_every=4)
        _, _, times, downs, _ = beats.analyze_samples(samples, SR)
        accented = truth[::4]
        self.assertGreaterEqual(hit_rate(accented, downs), 0.9)
        self.assertLessEqual(len(downs), len(times) // 4 + 1)

    def test_silence_has_no_beats(self):
        bpm, confidence, times, downs, note = beats.analyze_samples(array.array("h", [0] * SR * 5), SR)
        self.assertIsNone(bpm)
        self.assertEqual(times, [])
        self.assertIn("No rhythmic onsets", note)

    def test_onset_peaks_finds_clicks(self):
        samples, truth = click_track(100, 10)
        env = beats.onset_envelope(samples)
        peaks = beats.onset_peaks(env, SR / 512.0)
        self.assertGreaterEqual(hit_rate(truth, [p + 256.0 / SR for p in peaks], 0.04), 0.9)

    def test_three_minute_runtime(self):
        samples, _ = click_track(120, 180)
        start = time.perf_counter()
        bpm, *_ = beats.analyze_samples(samples, SR)
        elapsed = time.perf_counter() - start
        self.assertAlmostEqual(bpm, 120, delta=2.4)
        self.assertLess(elapsed, 3.0, "3-minute analysis took %.2f s" % elapsed)

    def test_timeline_mapping_drops_beats_past_clip(self):
        mapped = beats._timeline([0.0, 1.0, 9.9, 9.99, 10.5], SOURCE)
        # 9.99 s rounds to frame 1240, the clip's exclusive end, so it is dropped like 10.5 s.
        self.assertEqual([m["timeline_frame"] for m in mapped], [1000, 1024, 1238])

    def test_validation(self):
        with self.assertRaises(beats.BeatError):
            beats.detect_beats(dict(SOURCE, file_path="/nope/x.wav"), "ffmpeg")
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            with self.assertRaises(beats.BeatError):
                beats.detect_beats(dict(SOURCE, file_path=f.name), "ffmpeg", min_bpm=200, max_bpm=100)
            with patch("subprocess.run", return_value=Mock(returncode=1, stderr=b"Stream map '0:a:0' matches no streams.", stdout=b"")):
                with self.assertRaisesRegex(beats.BeatError, "no audio"):
                    beats.detect_beats(dict(SOURCE, file_path=f.name), "ffmpeg")


@unittest.skipUnless(FFMPEG, "ffmpeg not installed")
class FfmpegIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_detects_hard_cuts_within_one_frame(self):
        video = self.dir / "cuts.mp4"
        # 2.0 s red, 1.5 s blue, 2.0 s test pattern at 24 fps: cuts at 2.0 s and 3.5 s.
        subprocess.run([FFMPEG, "-v", "error", "-y",
                        "-f", "lavfi", "-i", "color=red:s=320x240:r=24:d=2",
                        "-f", "lavfi", "-i", "color=blue:s=320x240:r=24:d=1.5",
                        "-f", "lavfi", "-i", "testsrc=s=320x240:r=24:d=2",
                        "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True, capture_output=True)
        src = dict(SOURCE, file_path=str(video), duration_sec=5.5, timeline_start_frame=0, timeline_end_frame=132)
        result = scenes.detect_shots(src, FFMPEG)
        times = [c["time_sec"] for c in result["cuts"]]
        self.assertEqual(len(times), 2, result)
        self.assertAlmostEqual(times[0], 2.0, delta=1 / 24.0)
        self.assertAlmostEqual(times[1], 3.5, delta=1 / 24.0)
        self.assertEqual([c["timeline_frame"] for c in result["cuts"]], [48, 84])

        # A source window starting at 1.0 s reports times relative to the window.
        shifted = scenes.detect_shots(dict(src, source_start_sec=1.0, duration_sec=4.5, timeline_start_frame=100), FFMPEG)
        self.assertAlmostEqual(shifted["cuts"][0]["time_sec"], 1.0, delta=1 / 24.0)
        self.assertEqual(shifted["cuts"][0]["timeline_frame"], 124)

    def test_audio_only_file_has_no_shots(self):
        audio = self.dir / "tone.wav"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=2", str(audio)], check=True, capture_output=True)
        result = scenes.detect_shots(dict(SOURCE, file_path=str(audio), duration_sec=2), FFMPEG)
        self.assertEqual(result["cuts"], [])

    def test_click_track_through_ffmpeg_decode(self):
        samples, truth = click_track(128, 20, offset=0.4)
        path = self.dir / "clicks.wav"
        with wave.open(str(path), "wb") as w:  # Written at 44.1 kHz stereo to exercise resampling/downmix.
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(44100)
            up = array.array("h")
            for v in samples:
                up.extend((v, v, v, v))
            w.writeframes(up.tobytes())
        src = dict(SOURCE, file_path=str(path), source_start_sec=0.0, duration_sec=20.0,
                   timeline_start_frame=86400, timeline_end_frame=86400 + 480)
        result = beats.detect_beats(src, FFMPEG)
        self.assertAlmostEqual(result["bpm"], 128, delta=128 * 0.02)
        found = [b["time_sec"] for b in result["beats"]]
        self.assertGreaterEqual(hit_rate(truth, found), 0.9)
        self.assertEqual(result["beats"][0]["timeline_frame"], 86400 + round(found[0] * 24))
        self.assertTrue(result["downbeats"])


if __name__ == "__main__":
    unittest.main()
