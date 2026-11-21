"""audio_regions: word-edge refinement on source energy, speech regions/pauses/cut points, and EBU R128 loudness."""

import math
import os
import random
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from bridge import audio_regions as ar

FFMPEG = shutil.which("ffmpeg") or ("/opt/homebrew/bin/ffmpeg" if os.path.exists("/opt/homebrew/bin/ffmpeg") else None)
HOP = ar.HOP_SEC


def levels_for(duration, loud_spans, loud_db=-20.0, floor_db=-80.0, overrides=None):
    """Synthetic 10 ms hop levels: ``floor_db`` everywhere, ``loud_db`` inside each (start, end) span."""
    n = int(round(duration / HOP))
    levels = [floor_db] * n
    for a, b in loud_spans:
        for i in range(int(round(a / HOP)), int(round(b / HOP))):
            levels[i] = loud_db
    for i, value in (overrides or {}).items():
        levels[i] = value
    return levels


def words_sec(*items):
    return [{"word": w, "start": s, "end": e} for w, s, e in items]


class RefineRegionsTests(unittest.TestCase):
    def setUp(self):
        # Sound: "hello" 0.50-1.00, "world" 1.20-1.60, a breath 2.00-2.25, "again" 2.80-3.30.
        # Whisper drifted: starts early, ends late (into the silence). "thanks" is a phantom over silence.
        self.levels = levels_for(4.0, [(0.50, 1.00), (1.20, 1.60), (2.00, 2.25), (2.80, 3.30)],
                                 overrides={250: -85.0})
        self.words = words_sec(("hello", 0.42, 1.10), ("world", 1.25, 1.75), ("again", 2.75, 3.42),
                               ("thanks", 3.60, 3.80))
        self.result = ar.refine_regions(self.levels, self.words, hop_sec=HOP, threshold_db=-50.0, duration_sec=4.0)

    def test_words_snap_to_energy_edges(self):
        refined = {w["word"]: w for w in self.result["words"]}
        for word, start, end in (("hello", 0.50, 1.00), ("world", 1.20, 1.60), ("again", 2.80, 3.30)):
            self.assertAlmostEqual(refined[word]["start"], start, places=6, msg=word)
            self.assertAlmostEqual(refined[word]["end"], end, places=6, msg=word)
            self.assertTrue(refined[word]["snapped_start"] and refined[word]["snapped_end"])
        self.assertAlmostEqual(refined["hello"]["orig_start"], 0.42)

    def test_close_words_merge_and_regions_are_padded(self):
        regions = self.result["regions"]
        self.assertEqual(len(regions), 2)
        self.assertEqual(regions[0]["text"], "hello world")
        self.assertEqual(regions[0]["word_count"], 2)
        self.assertAlmostEqual(regions[0]["speech_start"], 0.50)
        self.assertAlmostEqual(regions[0]["speech_end"], 1.60)
        self.assertAlmostEqual(regions[0]["start"], 0.42)
        self.assertAlmostEqual(regions[0]["end"], 1.68)
        self.assertEqual(regions[1]["text"], "again")
        self.assertAlmostEqual(regions[1]["start"], 2.72)
        self.assertAlmostEqual(regions[1]["end"], 3.38)

    def test_phantom_word_and_energy_only_are_reported_not_dropped(self):
        unsupported = self.result["unsupported_words"]
        self.assertEqual([w["word"] for w in unsupported], ["thanks"])
        self.assertAlmostEqual(unsupported[0]["start"], 3.60)
        self.assertNotIn("thanks", " ".join(r["text"] for r in self.result["regions"]))
        self.assertEqual(len(self.result["words"]), 4)
        energy = self.result["energy_only"]
        self.assertEqual(len(energy), 1)
        self.assertAlmostEqual(energy[0]["start"], 2.00)
        self.assertAlmostEqual(energy[0]["end"], 2.25)
        self.assertFalse(energy[0]["adjacent_to_speech"])

    def test_pause_cut_point_is_at_quietest_hop(self):
        pauses = self.result["pauses"]
        self.assertEqual(len(pauses), 1)
        self.assertAlmostEqual(pauses[0]["start"], 1.68)
        self.assertAlmostEqual(pauses[0]["end"], 2.72)
        self.assertAlmostEqual(pauses[0]["cut_sec"], 2.505)
        self.assertEqual(pauses[0]["quietest_db"], -85.0)
        self.assertEqual(pauses[0]["energy_only"], 1)
        self.assertEqual((pauses[0]["after"], pauses[0]["before"]), ("world", "again"))

    def test_word_never_crosses_its_neighbour(self):
        levels = levels_for(2.0, [(0.50, 1.00)])
        result = ar.refine_regions(levels, words_sec(("one", 0.50, 0.85), ("two", 0.90, 1.20)),
                                   hop_sec=HOP, threshold_db=-50.0)
        one, two = result["words"]
        self.assertLessEqual(one["end"], two["start"] + 1e-9)
        self.assertLessEqual(one["end"], 0.90 + 1e-9)
        self.assertAlmostEqual(two["end"], 1.00)

    def test_shift_is_limited_to_max_shift(self):
        levels = levels_for(2.0, [(0.50, 1.00)])
        result = ar.refine_regions(levels, words_sec(("far", 0.20, 0.70)), hop_sec=HOP, threshold_db=-50.0,
                                   max_shift_seconds=0.1)
        word = result["words"][0]
        self.assertFalse(word["snapped_start"])  # Onset at 0.50 is 0.30 s away.
        self.assertAlmostEqual(word["start"], 0.20)

    def test_energy_trailing_a_word_is_adjacent(self):
        levels = levels_for(2.0, [(0.50, 1.50)])
        result = ar.refine_regions(levels, words_sec(("short", 0.50, 0.80)), hop_sec=HOP, threshold_db=-50.0,
                                   max_shift_seconds=0.1)
        self.assertEqual(len(result["energy_only"]), 1)
        self.assertTrue(result["energy_only"][0]["adjacent_to_speech"])
        self.assertAlmostEqual(result["energy_only"][0]["end"], 1.50)

    def test_short_energy_only_is_dropped_but_counted(self):
        levels = levels_for(2.0, [(0.50, 0.55)])
        result = ar.refine_regions(levels, [], hop_sec=HOP, threshold_db=-50.0)
        self.assertEqual(result["energy_only"], [])
        self.assertEqual(result["energy_only_dropped_short"], 1)

    def test_padding_is_clamped_to_window_and_neighbours(self):
        levels = levels_for(1.2, [(0.02, 0.40), (0.60, 1.18)])
        result = ar.refine_regions(levels, words_sec(("a", 0.02, 0.40), ("b", 0.60, 1.18)), hop_sec=HOP,
                                   threshold_db=-50.0, pad_seconds=0.5, merge_gap_seconds=0.1)
        first, second = result["regions"]
        self.assertAlmostEqual(first["start"], 0.0)
        self.assertAlmostEqual(first["end"], 0.50)
        self.assertAlmostEqual(second["start"], 0.50)
        self.assertAlmostEqual(second["end"], 1.20)
        self.assertEqual(result["pauses"], [])


class ThresholdTests(unittest.TestCase):
    def test_threshold_sits_between_floor_and_speech(self):
        stats = ar.adaptive_threshold(levels_for(4.0, [(1.0, 3.0)], loud_db=-20.0, floor_db=-60.0))
        self.assertEqual(stats["floor_db"], -60.0)
        self.assertEqual(stats["speech_db"], -20.0)
        self.assertGreater(stats["threshold_db"], -60.0)
        self.assertLess(stats["threshold_db"], -30.0)

    def test_threshold_has_a_cap_and_a_floor(self):
        loud = ar.adaptive_threshold(levels_for(2.0, [(0.0, 2.0)], loud_db=-3.0))
        self.assertLessEqual(loud["threshold_db"], ar.THRESHOLD_CAP_DB)
        silent = ar.adaptive_threshold([ar.SILENT_DB] * 200)
        self.assertEqual(silent["threshold_db"], ar.THRESHOLD_MIN_DB)
        self.assertEqual(silent["floor_db"], ar.FLOOR_MIN_DB)


class FrameConversionTests(unittest.TestCase):
    """Absolute timeline frames with a non-zero timeline start and source offset, without ffmpeg."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.media = Path(self.tmp.name, "clip.wav")
        self.media.write_bytes(b"")
        self.source = {"item_id": "item-1", "name": "clip", "track": "A1", "file_path": str(self.media),
                       "source_start_sec": 12.5, "duration_sec": 4.0, "timeline_start_frame": 86400.0,
                       "timeline_end_frame": 86496.0, "fps": 24.0}
        self.levels = levels_for(4.0, [(0.50, 1.00), (1.20, 1.60), (2.00, 2.25), (2.80, 3.30)],
                                 overrides={250: -85.0})

    def tearDown(self):
        self.tmp.cleanup()

    def frames(self, seconds):
        return 86400 + round(seconds * 24)

    def test_regions_pauses_and_cut_points_are_absolute_frames(self):
        words = [{"word": "hello", "start_frame": 86410, "end_frame": 86426},  # 0.417-1.083 s
                 {"word": "world", "start_frame": 86430, "end_frame": 86442},  # 1.25-1.75 s
                 {"word": "again", "start_frame": 86466, "end_frame": 86482},  # 2.75-3.417 s
                 {"word": "thanks", "start_frame": 86486, "end_frame": 86491},  # phantom over silence
                 {"word": "before", "start_frame": 86300, "end_frame": 86320}]  # outside the clip
        with mock.patch.object(ar, "fine_hop_levels", return_value=self.levels) as levels:
            result = ar.speech_regions(self.source, words, "/fake/ffmpeg", threshold_db=-50.0)
        args = levels.call_args[0]
        self.assertEqual((args[0], args[2], args[3]), ("/fake/ffmpeg", 12.5, 4.0))
        first, second = result["regions"]
        self.assertEqual((first["speech_start_frame"], first["speech_end_frame"]), (86412, 86439))  # 0.50, 1.60
        self.assertEqual((first["start_frame"], first["end_frame"]), (86410, 86441))  # 0.42, 1.68 padded
        self.assertEqual((second["start_frame"], second["end_frame"]), (86465, 86482))  # 2.72, 3.38
        self.assertAlmostEqual(first["start_sec"], 0.42)
        self.assertEqual(first["text"], "hello world")
        pause = result["pauses"][0]
        self.assertEqual((pause["start_frame"], pause["end_frame"]), (86441, 86465))
        self.assertEqual(result["cut_points"], [86460])  # 2.505 s -> frame 60.12
        self.assertEqual(pause["cut_frame"], 86460)
        self.assertEqual([w["word"] for w in result["unsupported_words"]], ["thanks"])
        self.assertEqual(result["unsupported_words"][0]["start_frame"], 86486)
        self.assertEqual(result["energy_only"][0]["start_frame"], self.frames(2.00))
        self.assertEqual(result["energy_only"][0]["end_frame"], self.frames(2.25))  # Exclusive end.
        self.assertEqual(result["analysis"]["words_outside_window"], 1)
        self.assertEqual(result["analysis"]["threshold_method"], "given")
        self.assertIn("not the Fairlight mix", result["analysis"]["note"])
        self.assertEqual((result["item_id"], result["track"], result["timeline_start_frame"]), ("item-1", "A1", 86400.0))
        for word in result["words"]:
            self.assertGreaterEqual(word["start_frame"], 86400)
            self.assertLessEqual(word["end_frame"], 86496)

    def test_adaptive_threshold_is_reported(self):
        with mock.patch.object(ar, "fine_hop_levels", return_value=self.levels):
            result = ar.speech_regions(self.source, [], "/fake/ffmpeg")
        analysis = result["analysis"]
        self.assertEqual(analysis["threshold_method"], "adaptive")
        self.assertEqual(analysis["floor_db"], -80.0)
        self.assertEqual(analysis["hop_seconds"], HOP)
        self.assertEqual(len(result["energy_only"]), 4)  # No words: every sound is energy-only.

    def test_bad_input_raises(self):
        with self.assertRaises(ar.AudioRegionsError):
            ar.speech_regions(dict(self.source, file_path=str(self.media) + ".missing"), [], "/fake/ffmpeg")
        with self.assertRaises(ar.AudioRegionsError):
            ar.speech_regions(self.source, [], "/fake/ffmpeg", threshold_db=5)
        with self.assertRaises(ar.AudioRegionsError):
            ar.speech_regions(self.source, [{"word": "x"}], "/fake/ffmpeg")
        with self.assertRaises(ar.AudioRegionsError):
            ar.measure_loudness([self.source], "/fake/ffmpeg", target_lufs=-80)
        with self.assertRaises(ar.AudioRegionsError):
            ar.measure_loudness([], "/fake/ffmpeg")


class LoudnessMathTests(unittest.TestCase):
    def test_gated_loudness(self):
        self.assertAlmostEqual(ar.gated_loudness([-20.0] * 50), -20.0, places=6)
        self.assertAlmostEqual(ar.gated_loudness([-20.0] * 50 + [-45.0] * 50), -20.0, places=6)  # Relative gate.
        self.assertIsNone(ar.gated_loudness([-80.0, -120.7]))

    def test_summary_parsing(self):
        text = ("[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n    I:         -23.4 LUFS\n"
                "    Threshold: -33.9 LUFS\n\n  Loudness range:\n    LRA:         4.2 LU\n\n"
                "  True peak:\n    Peak:       -inf dBFS\n")
        data = ar.parse_ebur128_summary(text)
        self.assertEqual((data["integrated_lufs"], data["lra_lu"], data["true_peak_dbtp"]), (-23.4, 4.2, None))
        self.assertFalse(ar.parse_ebur128_summary("no summary")["found"])

    def test_gain_and_peak_limit(self):
        values = ar._loudness_values({"integrated_lufs": -20.0, "lra_lu": 1.0, "true_peak_dbtp": -4.0}, 5.0,
                                     -14.0, -1.0)
        self.assertEqual((values["gain_db"], values["peak_limited"], values["peak_limited_gain_db"]), (6.0, True, 3.0))
        values = ar._loudness_values({"integrated_lufs": -70.0, "lra_lu": 0.0, "true_peak_dbtp": -60.0}, 5.0,
                                     -14.0, -1.0)
        self.assertIsNone(values["integrated_lufs"])
        self.assertIsNone(values["gain_db"])
        self.assertIn("-70 LUFS", values["reason"])


# ------------------------------------------------------------- ffmpeg-backed

def write_wav(path, segments, rate=16000, noise_db=-60.0, freq=997.0, seed=7):
    """Mono 16-bit WAV from [(seconds, rms_dbfs or None)]: a sine at that RMS level, or low noise.
    997 Hz, not 1 kHz: a perfectly periodic 1 kHz sine at 48 kHz makes ffmpeg probe the file as MPEG-TS."""
    rng = random.Random(seed)
    noise = 10 ** (noise_db / 20.0) * math.sqrt(3.0) if noise_db is not None else 0.0
    frames = bytearray()
    t = 0
    for seconds, level in segments:
        amp = 10 ** (level / 20.0) * math.sqrt(2.0) if level is not None else 0.0
        for _ in range(int(round(seconds * rate))):
            value = amp * math.sin(2 * math.pi * freq * t / rate) + rng.uniform(-noise, noise)
            frames += struct.pack("<h", max(-32767, min(32767, int(round(value * 32767)))))
            t += 1
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(frames))


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class FfmpegTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        # 1 s lead-in (outside the clip), then speech-like tones at 0.5-1.5 and 2.2-3.0 of the clip window.
        cls.speech = root / "speech.wav"
        write_wav(cls.speech, [(1.5, None), (1.0, -20.0), (0.7, None), (0.8, -24.0), (0.5, None)])
        cls.loud = root / "loud.wav"
        write_wav(cls.loud, [(3.0, -20.0)], rate=48000, noise_db=None)
        cls.quiet = root / "quiet.wav"
        write_wav(cls.quiet, [(3.0, -30.0)], rate=48000, noise_db=None)
        cls.silent = root / "silent.wav"
        write_wav(cls.silent, [(2.0, None)], rate=48000, noise_db=None)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def source(self, path, start=0.0, duration=3.0, origin=86400, item="i", name="clip"):
        return {"item_id": item, "name": name, "track": "A1", "file_path": str(path), "source_start_sec": start,
                "duration_sec": duration, "timeline_start_frame": origin, "timeline_end_frame": origin + duration * 24,
                "fps": 24.0}

    def test_fine_levels_find_tone_edges(self):
        levels = ar.fine_hop_levels(FFMPEG, self.speech, 1.0, 3.0)
        self.assertAlmostEqual(len(levels) * HOP, 3.0, delta=0.02)
        loud = [i for i, v in enumerate(levels) if v > -40]
        self.assertAlmostEqual(loud[0] * HOP, 0.5, delta=2 * HOP)
        self.assertAlmostEqual((loud[-1] + 1) * HOP, 3.0, delta=2 * HOP)
        self.assertAlmostEqual(levels[100], -20.0, delta=1.0)

    def test_speech_regions_edges_land_on_true_edges(self):
        src = self.source(self.speech, start=1.0, duration=3.5)
        words = [{"word": "first", "start_frame": 86400 + 10, "end_frame": 86400 + 39},  # 0.417-1.625 s
                 {"word": "second", "start_frame": 86400 + 51, "end_frame": 86400 + 75},  # 2.125-3.125 s
                 {"word": "ghost", "start_frame": 86400 + 79, "end_frame": 86400 + 83}]  # silence
        result = ar.speech_regions(src, words, FFMPEG)
        self.assertEqual(len(result["regions"]), 2)
        refined = {w["word"]: w for w in result["words"]}
        for word, start, end in (("first", 0.5, 1.5), ("second", 2.2, 3.0)):
            self.assertLessEqual(abs(refined[word]["start_frame"] - (86400 + start * 24)), 1, word)
            self.assertLessEqual(abs(refined[word]["end_frame"] - (86400 + end * 24)), 1, word)
        first, second = result["regions"]
        self.assertLessEqual(abs(first["speech_start_frame"] - 86412), 1)
        self.assertLessEqual(abs(first["speech_end_frame"] - 86436), 1)
        self.assertLessEqual(abs(second["speech_start_frame"] - (86400 + 2.2 * 24)), 1)
        self.assertEqual([w["word"] for w in result["unsupported_words"]], ["ghost"])
        self.assertEqual(len(result["cut_points"]), 1)
        self.assertTrue(first["end_frame"] <= result["cut_points"][0] < second["start_frame"])
        analysis = result["analysis"]
        self.assertEqual(analysis["threshold_method"], "adaptive")
        self.assertLess(analysis["floor_db"], -50)
        self.assertGreater(analysis["threshold_db"], analysis["floor_db"])
        self.assertAlmostEqual(analysis["decoded_seconds"], 3.5, delta=0.02)

    def test_loudness_per_clip_program_and_gain(self):
        sources = [self.source(self.quiet, origin=86472, item="q", name="quiet"),
                   self.source(self.loud, origin=86400, item="l", name="loud")]
        result = ar.measure_loudness(sources, FFMPEG, target_lufs=-14.0)
        loud, quiet = result["clips"]  # Timeline order.
        self.assertEqual((loud["item_id"], quiet["item_id"]), ("l", "q"))
        self.assertAlmostEqual(loud["integrated_lufs"] - quiet["integrated_lufs"], 10.0, delta=0.5)
        self.assertAlmostEqual(loud["integrated_lufs"], -20.0, delta=1.5)
        for clip in (loud, quiet):
            self.assertAlmostEqual(clip["gain_db"], -14.0 - clip["integrated_lufs"], delta=0.11)
            self.assertFalse(clip["peak_limited"])
            self.assertIsNone(clip["peak_limited_gain_db"])
        self.assertEqual(loud["timeline_start_frame"], 86400)
        program = result["program"]
        self.assertEqual(program["method"], "exact_concat")
        self.assertLess(program["integrated_lufs"], loud["integrated_lufs"])
        self.assertGreater(program["integrated_lufs"], quiet["integrated_lufs"])
        self.assertAlmostEqual(program["gain_db"], -14.0 - program["integrated_lufs"], delta=0.11)
        self.assertIn("does NOT apply gain", result["note"])  # measuring alone
        with mock.patch.object(ar, "EXACT_PROGRAM_MAX", 1):
            pooled = ar.measure_loudness(sources, FFMPEG, target_lufs=-14.0)["program"]
        self.assertEqual(pooled["method"], "pooled_blocks")
        self.assertIn("APPROXIMATE", pooled["method_note"])
        self.assertAlmostEqual(pooled["integrated_lufs"], program["integrated_lufs"], delta=0.5)

    def test_peak_limited_gain(self):
        result = ar.measure_loudness([self.source(self.loud)], FFMPEG, target_lufs=-3.0, true_peak_ceiling_db=-1.0)
        clip = result["clips"][0]
        self.assertTrue(clip["peak_limited"])
        self.assertAlmostEqual(clip["peak_limited_gain_db"], -1.0 - clip["true_peak_dbtp"], delta=0.11)
        self.assertLess(clip["peak_limited_gain_db"], clip["gain_db"])

    def test_unmeasurable_clips_report_a_reason(self):
        sources = [self.source(self.loud, duration=0.2, item="short"),
                   self.source(self.silent, duration=2.0, origin=86500, item="silent"),
                   self.source(Path(self.tmp.name, "missing.wav"), origin=86600, item="offline")]
        result = ar.measure_loudness(sources, FFMPEG)
        short, silent, offline = result["clips"]
        self.assertIsNone(short["integrated_lufs"])
        self.assertIn("Too short", short["reason"])
        self.assertIsNone(silent["integrated_lufs"])
        self.assertIsNone(silent["gain_db"])
        self.assertIn("Too quiet", silent["reason"])
        self.assertFalse(offline["measured"])
        self.assertIn("offline", offline["reason"])
        # The programme still contains the short clip's 0.2 s of tone, so it is measurable as a whole.
        self.assertEqual(result["program"]["clips"], 2)
        self.assertEqual([s["item_id"] for s in result["program"]["skipped"]], ["offline"])
        self.assertIsNotNone(result["program"]["integrated_lufs"])
        silent_only = ar.measure_loudness([self.source(self.silent, duration=2.0)], FFMPEG)["program"]
        self.assertIsNone(silent_only["integrated_lufs"])
        self.assertIsNone(silent_only["gain_db"])
        self.assertIn("Too quiet", silent_only["reason"])

    def test_missing_stream_and_ffmpeg_failure(self):
        result = ar.measure_loudness([self.source(self.loud)], FFMPEG, stream=3)
        self.assertIn("no audio stream", result["clips"][0]["reason"])
        broken = Path(self.tmp.name, "broken.wav")
        broken.write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunk")
        with self.assertRaises(ar.AudioRegionsError) as caught:
            ar.measure_loudness([self.source(broken)], FFMPEG)
        self.assertIn("ffmpeg", str(caught.exception))
        with self.assertRaises(ar.AudioRegionsError):
            ar.speech_regions(self.source(broken), [], FFMPEG)


if __name__ == "__main__":
    unittest.main()
