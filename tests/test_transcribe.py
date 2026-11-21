"""Local transcription: whisper.cpp parsing, subtitle cues, filler ranges, and the Resolve-side operations."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge import transcribe as tx
from bridge.operations import OperationError, ResolveOperations
from fakes import Clip, Media, Project, Resolve


def token(text, dtw=None, start=0):
    return {"text": text, "offsets": {"from": start, "to": start}, "t_dtw": -1 if dtw is None else dtw, "p": 0.9}


WHISPER = {
    "result": {"language": "en"},
    "transcription": [
        {"offsets": {"from": 0, "to": 9000}, "text": " Um, so I think we should cut here.",
         "tokens": [token("[_BEG_]"), token(" Um", 100), token(",", 120), token(" so", 150), token(" I", 180),
                    token(" think", 200), token(" we", 230), token(" should", 250), token(" cut", 280),
                    token(" here", 300), token(".", 320), token("[_TT_450]")]},
        {"offsets": {"from": 9000, "to": 12000}, "text": " Uh, next.",
         "tokens": [token(" Uh", 900), token(",", 910), token(" next", 950), token(".", 990)]},
    ],
}


class ParseTests(unittest.TestCase):
    def test_dtw_times_and_word_merging(self):
        segments, words = tx.parse_whisper_json(WHISPER)
        self.assertEqual([w["word"] for w in words][:3], ["Um,", "so", "I"])
        self.assertAlmostEqual(words[0]["start"], 1.0)
        self.assertAlmostEqual(words[1]["start"], 1.5)
        self.assertAlmostEqual(words[0]["end"], 1.5)  # ends where the next word starts
        self.assertAlmostEqual(segments[0]["start"], 1.0)  # tightened to aligned words
        self.assertEqual(segments[1]["text"], "Uh, next.")

    def test_falls_back_to_offsets_without_dtw(self):
        data = {"transcription": [{"offsets": {"from": 0, "to": 2000}, "text": " Hi there",
                                    "tokens": [token(" Hi", start=400), token(" there", start=900)]}]}
        _, words = tx.parse_whisper_json(data)
        self.assertAlmostEqual(words[0]["start"], 0.4)
        self.assertAlmostEqual(words[1]["start"], 0.9)

    def test_dtw_preset_from_model_name(self):
        self.assertEqual(tx.dtw_preset("/m/ggml-large-v3-turbo-q5_0.bin"), "large.v3.turbo")
        self.assertEqual(tx.dtw_preset("/m/ggml-base.en.bin"), "base.en")
        self.assertIsNone(tx.dtw_preset("/m/custom.bin"))


def words_at(spec, fps=24):
    """[(word, start_frame, end_frame)] -> word dicts."""
    return [{"word": w, "start_frame": s, "end_frame": e} for w, s, e in spec]


class CueTests(unittest.TestCase):
    def test_breaks_on_sentence_pause_and_length(self):
        words = words_at([("Hello", 0, 10), ("there", 10, 20), ("everyone.", 20, 30), ("Next", 31, 40),
                          ("idea", 40, 50), ("after", 90, 100), ("pause", 100, 110)])
        cues = tx.subtitle_cues(words, 24)
        self.assertEqual([c["text"] for c in cues], ["Hello there everyone.", "Next idea", "after pause"])

    def test_wraps_two_balanced_lines(self):
        words = words_at([(w, i * 5, i * 5 + 5) for i, w in enumerate("this is a fairly long subtitle line that needs wrapping".split())])
        cue = tx.subtitle_cues(words, 24, max_chars=30)[0]
        lines = cue["text"].split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(len(line) <= 30 for line in lines))

    def test_minimum_duration_without_overlap(self):
        words = words_at([("Hi.", 0, 2), ("Bye", 100, 102)])
        cues = tx.subtitle_cues(words, 24, min_seconds=1.0)
        self.assertEqual(cues[0]["end_frame"], 24)
        self.assertLessEqual(cues[0]["end_frame"], cues[1]["start_frame"])

    def test_srt_is_relative_to_timeline_start(self):
        srt = tx.to_srt([{"start_frame": 86424, "end_frame": 86448, "text": "One"}], 24, 86400)
        self.assertIn("00:00:01,000 --> 00:00:02,000", srt)
        self.assertTrue(srt.startswith("1\n"))

    def test_filler_ranges_pad_and_merge(self):
        words = words_at([("Um,", 10, 14), ("so", 14, 20), ("uh", 21, 24), ("next", 60, 70), ("you", 80, 84), ("know", 84, 88)])
        ranges = tx.filler_ranges(words, 24, fillers=("um", "uh", "you know"), padding_frames=1, merge_gap_frames=3)
        # "so" is real speech between the fillers, so they stay separate cuts.
        self.assertEqual([(r["start_frame"], r["end_frame"]) for r in ranges], [(9, 15), (20, 25), (79, 89)])
        self.assertIn("you know", ranges[2]["label"])

    def test_adjacent_fillers_merge(self):
        words = words_at([("um", 10, 14), ("uh", 15, 18)])
        ranges = tx.filler_ranges(words, 24, padding_frames=1)
        self.assertEqual([(r["start_frame"], r["end_frame"]) for r in ranges], [(9, 19)])


class TranscribeSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / "models").mkdir()
        (self.home / "models" / "ggml-large-v3-turbo-q5_0.bin").write_bytes(b"x")
        self.media = self.home / "talk.mov"
        self.media.write_bytes(b"media")
        self.calls = []

    def fake_run(self, command, **kwargs):
        self.calls.append(command)
        if "-ojf" in command:
            out = Path(command[command.index("-of") + 1] + ".json")
            out.write_text(json.dumps(WHISPER))
        else:
            Path(command[-1]).write_bytes(b"RIFF")
        return Mock(returncode=0, stderr=b"")

    def test_maps_to_timeline_frames_and_caches(self):
        source = {"file_path": str(self.media), "source_start_sec": 2.0, "duration_sec": 20.0,
                  "timeline_start_frame": 1000, "timeline_end_frame": 1480, "fps": 24, "item_id": "c1", "name": "Talk"}
        with patch.object(tx, "whisper_binary", return_value="/bin/whisper-cli"), patch("subprocess.run", side_effect=self.fake_run):
            first = tx.transcribe_source(source, self.home, ffmpeg=sys.executable)
            second = tx.transcribe_source(source, self.home, ffmpeg=sys.executable)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len([c for c in self.calls if "-ojf" in c]), 1)
        whisper = next(c for c in self.calls if "-ojf" in c)
        self.assertIn("large.v3.turbo", whisper)  # DTW alignment requested
        self.assertIn("-nfa", whisper)
        ffmpeg = self.calls[0]
        self.assertEqual(ffmpeg[ffmpeg.index("-ss") + 1], "2.000000")
        self.assertEqual(first["words"][0]["start_frame"], 1024)  # 1.0 s after the clip start
        self.assertEqual(first["segments"][0]["timecode"], tx.frames_to_tc(1024, 24))

    def test_missing_engine_explains_setup(self):
        with patch.object(tx, "whisper_binary", return_value=None):
            with self.assertRaises(tx.TranscriptionError) as ctx:
                tx.transcribe_source({"file_path": str(self.media), "source_start_sec": 0, "duration_sec": 1,
                                      "timeline_start_frame": 0, "fps": 24}, self.home)
        self.assertIn("brew install whisper-cpp", str(ctx.exception))


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "a.wav"
        path.write_bytes(b"x")
        self.video = Clip(0, 240, Media(path), name="cam", kind="video", track=1)
        self.voice = Clip(0, 240, Media(path), name="voice", kind="audio", track=1)
        self.music = Clip(0, 100, Media(path), name="music", kind="audio", track=2)
        self.project = Project([self.video, self.voice, self.music])
        self.op = ResolveOperations(lambda: Resolve(self.project))

    def test_clip_scope_prefers_audio_under_playhead(self):
        with patch.object(self.op, "_playhead_frame", return_value=50):
            out = self.op._op_transcription_source({})
        self.assertEqual([s["name"] for s in out["sources"]], ["voice"])
        self.assertEqual(out["sources"][0]["track"], "A1")
        self.assertIn("fingerprint", out)

    def test_timeline_scope_picks_busiest_audio_track(self):
        out = self.op._op_transcription_source({"scope": "timeline"})
        self.assertEqual([s["name"] for s in out["sources"]], ["voice"])

    def test_offline_media_is_skipped(self):
        self.voice.media = Media("/nope/missing.wav")
        out = self.op._op_transcription_source({"scope": "track", "track_index": 1})
        self.assertEqual(out["sources"], [])
        self.assertEqual(out["skipped"][0]["name"], "voice")

    def test_review_markers_match_silence_cut_format(self):
        fingerprint = self.op._op_transcription_source({"scope": "timeline"})["fingerprint"]
        out = self.op._op_review_transcript_cuts({"item_id": self.voice.uid, "fingerprint": fingerprint,
                                                  "ranges": [{"start_frame": 10, "end_frame": 20, "label": "um"},
                                                             {"start_frame": 300, "end_frame": 310}]})
        self.assertEqual(len(out["markers"]), 1)
        self.assertEqual(len(out["skipped"]), 1)  # outside the clip
        data = json.loads(self.project.current.markers[10]["customData"])
        self.assertEqual(data["bridge"], "silence-review-v1")
        self.assertEqual((data["start"], data["end"], data["item_id"]), (10, 20, self.voice.uid))

    def test_stale_fingerprint_is_refused(self):
        with self.assertRaises(OperationError):
            self.op._op_review_transcript_cuts({"item_id": self.voice.uid, "fingerprint": "old",
                                                "ranges": [{"start_frame": 1, "end_frame": 2}]})

    def test_import_subtitles_reports_when_not_placed(self):
        srt = Path(self.tmp.name) / "subs.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n")
        item = Mock()
        item.GetName.return_value = "subs.srt"
        item.GetClipProperty.return_value = {"Frames": "24"}
        self.project.pool.ImportMedia = Mock(return_value=[item])
        self.project.pool.AppendToTimeline = Mock(return_value=[])
        out = self.op._op_import_subtitles({"path": str(srt)})
        self.assertFalse(out["on_timeline"])
        self.assertIn("Drag it", out["note"])
        with self.assertRaises(OperationError):
            self.op._op_import_subtitles({"path": str(Path(self.tmp.name) / "x.txt")})


if __name__ == "__main__":
    unittest.main()
