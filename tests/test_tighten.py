"""tighten: floor-aware word trust, the pause/filler cut planner, review markers, and source-frame mapping.

Pure logic with injected fakes; nothing here contacts DaVinci Resolve or runs ffmpeg/whisper.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge import audio_regions as ar
from bridge import tighten as tg
from bridge import transcribe as tx

HOP = ar.HOP_SEC
FPS = 24.0


def levels_for(duration, spans, floor_db=-80.0):
    """10 ms hop levels: ``floor_db`` everywhere, each (start, end, db) span at its own level."""
    levels = [floor_db] * int(round(duration / HOP))
    for a, b, db in spans:
        for i in range(int(round(a / HOP)), int(round(b / HOP))):
            levels[i] = db
    return levels


def word(text, s, e, ts=None, te=None, trusted=True, item="c1"):
    """Planner word in frames. ts/te given -> carries trust fields; trusted=False -> phantom."""
    w = {"word": text, "start_frame": s, "end_frame": e, "item_id": item}
    if ts is not None or not trusted:
        w.update(trusted=trusted, trusted_start_frame=ts if trusted else None,
                 trusted_end_frame=te if trusted else None)
    return w


def plan(words, **kw):
    kw.setdefault("lead_frames", 4)
    kw.setdefault("tail_frames", 6)
    kw.setdefault("max_pause_frames", 12)
    return tg.plan_cuts(words, FPS, **kw)


class WordTrustTests(unittest.TestCase):
    def test_phrase_final_stretch_is_capped_past_last_loud_hop(self):
        # "here" is loud 0.50-1.00, then room noise just above floor+6 until 2.40; Whisper stretched it to 2.50.
        levels = levels_for(3.2, [(0.50, 1.00, -20.0), (1.00, 2.40, -72.0), (2.60, 3.00, -20.0)])
        result = ar.refine_regions(levels, [{"word": "here", "start": 0.50, "end": 2.50},
                                            {"word": "next", "start": 2.60, "end": 3.00}],
                                   hop_sec=HOP, threshold_db=-50.0, floor_db=-80.0, max_tail_seconds=0.35)
        here, nxt = result["words"]
        self.assertEqual(result["trust_db"], -74.0)
        self.assertTrue(here["trusted"] and here["tail_capped"])
        self.assertAlmostEqual(here["trusted_start"], 0.50)
        self.assertAlmostEqual(here["trusted_end"], 1.35)  # last loud hop 1.00 + 0.35, not the 2.40 noise
        self.assertAlmostEqual(here["last_loud"], 1.00)
        self.assertAlmostEqual(here["end"], 2.50)  # existing refined end is unchanged
        self.assertFalse(nxt["tail_capped"])
        self.assertAlmostEqual(nxt["trusted_end"], 3.00)

    def test_tail_cap_is_configurable(self):
        levels = levels_for(3.0, [(0.50, 1.00, -20.0), (1.00, 2.40, -72.0)])
        result = ar.refine_regions(levels, [{"word": "here", "start": 0.50, "end": 2.50}], hop_sec=HOP,
                                   threshold_db=-50.0, floor_db=-80.0, max_tail_seconds=0.10)
        self.assertAlmostEqual(result["words"][0]["trusted_end"], 1.10)

    def test_quiet_speech_above_floor_is_trusted_phantom_is_not(self):
        levels = levels_for(3.0, [(0.50, 0.90, -60.0)])  # under the -50 threshold, 20 dB over the floor
        result = ar.refine_regions(levels, [{"word": "soft", "start": 0.50, "end": 0.90},
                                            {"word": "ghost", "start": 2.00, "end": 2.40}],
                                   hop_sec=HOP, threshold_db=-50.0, floor_db=-80.0)
        soft, ghost = result["words"]
        self.assertFalse(soft["supported"])  # unchanged: below threshold
        self.assertTrue(soft["trusted"])
        self.assertAlmostEqual(soft["trusted_start"], 0.50)
        self.assertAlmostEqual(soft["trusted_end"], 0.90)
        self.assertFalse(ghost["trusted"])
        self.assertIsNone(ghost["trusted_start"])

    def test_without_floor_trust_uses_threshold(self):
        self.assertEqual(ar.trust_level(-50.0), -50.0)
        self.assertEqual(ar.trust_level(-50.0, -80.0), -74.0)
        self.assertEqual(ar.trust_level(-50.0, -52.0), -50.0)  # never above the threshold


class PlannerTests(unittest.TestCase):
    def test_long_pause_is_cut_to_tail_plus_lead(self):
        result = plan([word("hello", 100, 120, 100, 120), word("world", 160, 180, 160, 180)])
        self.assertEqual(len(result["cuts"]), 1)
        cut = result["cuts"][0]
        self.assertEqual((cut["start_frame"], cut["end_frame"]), (126, 156))
        self.assertEqual(cut["kind"], "pause")
        self.assertEqual(cut["pause_frames"], 40)
        self.assertEqual(cut["kept_frames"], 10)
        self.assertEqual(cut["seconds"], 1.25)
        self.assertEqual((cut["words_before"], cut["words_after"]), ("hello", "world"))
        self.assertEqual(result["totals"]["frames"], 30)

    def test_short_pause_is_kept(self):
        result = plan([word("a", 100, 120, 100, 120), word("b", 132, 150, 132, 150)])
        self.assertEqual(result["cuts"], [])

    def test_max_pause_threshold(self):
        words = [word("a", 100, 120, 100, 120), word("b", 140, 150, 140, 150)]  # 20-frame pause
        self.assertEqual(len(plan(words, max_pause_frames=19)["cuts"]), 1)
        self.assertEqual(plan(words, max_pause_frames=20)["cuts"], [])

    def test_default_max_pause_is_half_a_second(self):
        result = tg.plan_cuts([word("a", 100, 120, 100, 120), word("b", 140, 150, 140, 150)], FPS)
        self.assertEqual(result["settings"]["max_pause_frames"], 12)
        self.assertEqual(len(result["cuts"]), 1)

    def test_exclude_ranges_are_never_cut(self):
        words = [word("hello", 100, 120, 100, 120), word("world", 160, 180, 160, 180)]
        result = plan(words, exclude_ranges=[{"start_frame": 130, "end_frame": 140}])
        spans = [(c["start_frame"], c["end_frame"]) for c in result["cuts"]]
        self.assertEqual(spans, [(126, 130), (140, 156)])
        for a, b in spans:
            self.assertTrue(b <= 130 or a >= 140)
        self.assertEqual(result["skipped"][0]["reason"], "trimmed by exclude_ranges")
        whole = plan(words, exclude_ranges=[{"start_frame": 110, "end_frame": 170}])
        self.assertEqual(whole["cuts"], [])
        self.assertEqual(whole["skipped"][0]["reason"], "inside exclude_ranges")

    def test_cuts_never_enter_a_trusted_word(self):
        words = [word("one", 100, 120, 100, 120), word("two", 160, 175, 160, 175), word("three", 200, 230, 200, 230),
                 word("four", 300, 320, 300, 320)]
        result = plan(words, lead_frames=0, tail_frames=0, max_pause_frames=5)
        guards = [(w["trusted_start_frame"], w["trusted_end_frame"]) for w in words]
        self.assertEqual(len(result["cuts"]), 3)
        for c in result["cuts"]:
            for a, b in guards:
                self.assertTrue(c["end_frame"] <= a or c["start_frame"] >= b, (c, a, b))

    def test_overlapping_words_never_get_cut(self):
        # Two tracks' transcripts can overlap: "long" covers the gap between "a" and "b".
        words = [word("a", 100, 110, 100, 110), word("long", 105, 200, 105, 200), word("b", 210, 220, 210, 220)]
        cuts = plan(words, lead_frames=0, tail_frames=0, max_pause_frames=5)["cuts"]
        self.assertEqual([(c["start_frame"], c["end_frame"]) for c in cuts], [(200, 210)])  # only after "long"

    def test_phrase_final_stretch_does_not_hide_the_pause(self):
        stretched = [word("here.", 100, 200, 100, 130), word("Next", 210, 230, 210, 230)]
        cut = plan(stretched)["cuts"]
        self.assertEqual([(c["start_frame"], c["end_frame"]) for c in cut], [(136, 206)])
        # The plain transcript guard (no trust fields) over-protects: the 10-frame gap is not cut.
        plain = [word("here.", 100, 200), word("Next", 210, 230)]
        self.assertEqual(plan(plain)["cuts"], [])

    def test_untrusted_phantom_does_not_block_and_is_reported(self):
        words = [word("hello", 100, 120, 100, 120), word("thanks", 135, 145, trusted=False),
                 word("world", 160, 180, 160, 180)]
        cuts = plan(words)["cuts"]
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["untrusted_words_inside"], "thanks")
        self.assertEqual(plan(words)["totals"]["untrusted_words"], 1)

    def test_words_before_and_after_context(self):
        words = [word(t, 10 * i, 10 * i + 8, 10 * i, 10 * i + 8) for i, t in enumerate("a b c d e f".split())]
        words += [word(t, 200 + 10 * i, 208 + 10 * i, 200 + 10 * i, 208 + 10 * i)
                  for i, t in enumerate("g h i j k l".split())]
        cut = plan(words)["cuts"][0]
        self.assertEqual(cut["words_before"], "c d e f")
        self.assertEqual(cut["words_after"], "g h i j")
        self.assertEqual(plan(words, context_words=2)["cuts"][0]["words_before"], "e f")

    def test_timecode_is_absolute(self):
        start = 86400  # 01:00:00:00 at 24 fps
        cut = plan([word("a", start, start + 10, start, start + 10),
                    word("b", start + 60, start + 70, start + 60, start + 70)])["cuts"][0]
        self.assertEqual(cut["start_frame"], start + 16)
        self.assertEqual(cut["timecode"], "01:00:00:16")

    def test_fillers_removed_with_tight_padding(self):
        words = [word("so", 100, 110, 100, 110), word("um", 116, 124, 116, 124), word("I", 128, 140, 128, 140)]
        self.assertEqual(plan(words)["cuts"], [])  # fillers off: "um" is a word like any other
        cut = plan(words, fillers=True)["cuts"]
        self.assertEqual([(c["start_frame"], c["end_frame"], c["kind"]) for c in cut], [(116, 124, "filler")])
        self.assertEqual(cut[0]["removed_words"], "um")
        self.assertEqual((cut[0]["words_before"], cut[0]["words_after"]), ("so", "I"))

    def test_filler_takes_its_pause_with_it(self):
        words = [word("so", 80, 100, 80, 100), word("uh,", 130, 140, 130, 140), word("I", 200, 210, 200, 210)]
        cut = plan(words, fillers=True)["cuts"]
        self.assertEqual([(c["start_frame"], c["end_frame"]) for c in cut], [(106, 196)])
        self.assertEqual(cut[0]["label"], "uh,")

    def test_unanalysed_words_use_full_span_guards(self):
        clips = [{"source": {"item_id": "c1", "name": "A"}, "words": [{"word": "x", "start_frame": 1, "end_frame": 5}],
                  "analysis": None, "error": "ffmpeg missing"},
                 {"source": {"item_id": "c2", "name": "B"}, "words": [],
                  "analysis": {"words": [{"word": "y", "start_frame": 9, "end_frame": 12, "trusted": True,
                                          "trusted_start_frame": 9, "trusted_end_frame": 11}],
                               "analysis": {"warnings": ["low contrast"]}}}]
        words, warnings = tg.collect_words(clips)
        self.assertEqual([(w["word"], w["item_id"]) for w in words], [("x", "c1"), ("y", "c2")])
        self.assertNotIn("trusted", words[0])
        self.assertTrue(any("ffmpeg missing" in w for w in warnings))
        self.assertTrue(any("low contrast" in w for w in warnings))


class SettingsTests(unittest.TestCase):
    def test_validation(self):
        settings = tg.validate("timeline", "plan", 4, 6, None, [{"start_frame": 50, "end_frame": 60},
                                                                {"start_frame": 55, "end_frame": 70}], fps=25)
        self.assertEqual(settings["max_pause_frames"], 12)  # round(0.5 * 25)
        self.assertEqual(settings["exclude_ranges"], [(50, 70)])
        for bad in (dict(mode="cut"), dict(scope="bin"), dict(lead_frames=-1), dict(tail_frames=1.5),
                    dict(exclude_ranges=[{"start_frame": 10, "end_frame": 10}]), dict(exclude_ranges="x"),
                    dict(max_pause_frames=True)):
            with self.assertRaises(tg.TightenError, msg=bad):
                tg.validate(**bad)

    def test_lcut_and_crossfade_are_reported_not_applied(self):
        self.assertEqual(tg.not_applied(0, 0), [])
        report = tg.not_applied(3, 2)
        self.assertEqual([r["parameter"] for r in report], ["lcut_frames", "audio_crossfade_frames"])
        self.assertIn("Not applied", report[0]["reason"])
        self.assertIn("FCPXML", report[1]["reason"])

    def test_review_markers_are_purple_and_capped(self):
        cuts = plan([word("w%d" % i, 100 * i, 100 * i + 10, 100 * i, 100 * i + 10) for i in range(6)])["cuts"]
        markers, truncated = tg.review_markers(cuts, limit=3)
        self.assertEqual((len(markers), truncated), (3, 2))
        self.assertTrue(all(m["color"] == "Purple" and m["name"].startswith("Tighten") for m in markers))
        self.assertEqual(markers[0]["frame"], cuts[0]["start_frame"])
        self.assertEqual(markers[0]["duration"], cuts[0]["frames"])
        self.assertIn("w0 | w1", markers[0]["note"])


WHISPER = {"result": {"language": "en"}, "transcription": [
    {"offsets": {"from": 0, "to": 3000}, "text": " Cut here.",
     "tokens": [{"text": " Cut", "offsets": {"from": 1000, "to": 1000}, "t_dtw": 100, "p": 0.9},
                {"text": " here", "offsets": {"from": 1500, "to": 1500}, "t_dtw": 150, "p": 0.9}]}]}


class SourceFrameTests(unittest.TestCase):
    SOURCE = {"file_path": "x.mov", "source_start_sec": 10.0, "duration_sec": 20.0, "timeline_start_frame": 86448,
              "timeline_end_frame": 86928, "fps": 24.0, "item_id": "c7", "name": "Talk"}

    def test_timeline_rate_with_offsets(self):
        self.assertEqual(tx.source_rate(self.SOURCE), (24.0, "timeline"))
        self.assertEqual(tx.source_frame(self.SOURCE, 86448), 240)  # clip start = 10 s into the media
        self.assertEqual(tx.source_frame(self.SOURCE, 86448 + 24), 264)  # 11 s
        self.assertEqual(tx.source_frame(self.SOURCE, 86448 + 13), 253)

    def test_media_rate_when_the_entry_has_one(self):
        source = dict(self.SOURCE, media_fps=30.0)
        self.assertEqual(tx.source_rate(source), (30.0, "media"))
        self.assertEqual(tx.source_frame(source, 86448 + 24), 330)  # 11 s at 30 fps
        self.assertEqual(tx.source_rate(dict(self.SOURCE, media_fps="bad")), (24.0, "timeline"))

    def test_transcribe_source_words_carry_item_and_src_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "models").mkdir()
            (home / "models" / "ggml-base.bin").write_bytes(b"x")
            media = home / "talk.mov"
            media.write_bytes(b"media")

            def fake_run(command, **kwargs):
                if "-ojf" in command:
                    Path(command[command.index("-of") + 1] + ".json").write_text(json.dumps(WHISPER))
                else:
                    Path(command[-1]).write_bytes(b"RIFF")
                return Mock(returncode=0, stderr=b"")

            source = dict(self.SOURCE, file_path=str(media))
            with patch.object(tx, "whisper_binary", return_value="/bin/whisper-cli"), \
                    patch("subprocess.run", side_effect=fake_run):
                result = tx.transcribe_source(source, home, ffmpeg=sys.executable)
        first, second = result["words"]
        self.assertEqual(first["start_frame"], 86448 + 24)  # 1.0 s into the clip
        self.assertEqual((first["item_id"], first["src_frame"]), ("c7", 264))
        self.assertEqual(second["src_frame"], 276)  # 1.5 s into the clip = 11.5 s of media
        self.assertEqual((result["src_fps"], result["src_fps_basis"]), (24.0, "timeline"))
        hits = tx.search([result], "here")
        self.assertEqual((hits[0]["item_id"], hits[0]["src_frame"], hits[0]["src_fps_basis"]), ("c7", 276, "timeline"))
        self.assertEqual(hits[0]["start_frame"], 86448 + 36)


if __name__ == "__main__":
    unittest.main()
