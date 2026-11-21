"""Contact sheets and animated captions: grouping, sampling, spec generation, and real renders on macOS."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge import media_render as mr

FFMPEG = shutil.which("ffmpeg") or ("/opt/homebrew/bin/ffmpeg" if Path("/opt/homebrew/bin/ffmpeg").exists() else None)
FFPROBE = shutil.which("ffprobe") or ("/opt/homebrew/bin/ffprobe" if Path("/opt/homebrew/bin/ffprobe").exists() else None)
NATIVE = sys.platform == "darwin" and shutil.which("xcrun") is not None


def words(spec):
    return [{"word": w, "start_frame": s, "end_frame": e} for w, s, e in spec]


class GroupingTests(unittest.TestCase):
    def test_cues_respect_word_char_sentence_and_pause_limits(self):
        cues = mr.caption_cues(words([("one", 0, 5), ("two", 5, 10), ("three", 10, 15), ("four", 15, 20),
                                      ("five", 20, 25), ("end.", 25, 30), ("after", 31, 35), ("gap", 80, 90)]), 24)
        self.assertEqual([[w["text"] for w in c["words"]] for c in cues],
                         [["one", "two", "three", "four"], ["five", "end."], ["after"], ["gap"]])

    def test_cues_have_minimum_length_without_overlap(self):
        cues = mr.caption_cues(words([("Hi.", 0, 1), ("there", 5, 7)]), 24, min_seconds=0.4)
        self.assertEqual((cues[0]["start"], cues[0]["end"]), (0, 5))  # clamped to the next cue
        self.assertEqual(cues[1]["end"], 5 + 10)

    def test_char_limit(self):
        cues = mr.caption_cues(words([("extraordinarily", 0, 5), ("complicated", 5, 10), ("words", 10, 15)]), 24, max_chars=20)
        self.assertEqual([[w["text"] for w in c["words"]] for c in cues], [["extraordinarily"], ["complicated", "words"]])

    def test_sample_times_avoid_edges(self):
        times = mr.sample_times(10.0, 100.0, 4)
        self.assertEqual(len(times), 4)
        self.assertGreater(times[0], 15.0)
        self.assertLess(times[-1], 105.0)
        self.assertEqual(mr.sample_times(0, 0, 4), [])

    def test_timecode(self):
        self.assertEqual(mr.frames_to_tc(86400 + 24 * 12 + 4, 24), "01:00:12:04")


class SpecTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.specs = []

    def fake_helper(self, command, **kwargs):
        if command[0] == "/bin/helper":
            spec = json.loads(Path(command[2]).read_text())
            self.specs.append((command[1], spec))
            Path(spec["output"]).write_bytes(b"x")
            out = {"ok": True, "output": spec["output"], "width": 100, "height": 50}
            return Mock(returncode=0, stdout=json.dumps(out).encode(), stderr=b"")
        Path(command[-1]).write_bytes(b"jpg")  # ffmpeg still
        return Mock(returncode=0, stdout=b"", stderr=b"")

    def test_captions_spec_is_movie_relative(self):
        with patch.object(mr, "helper_path", return_value="/bin/helper"), patch("subprocess.run", side_effect=self.fake_helper):
            out = mr.render_captions(words([("Hello", 1000, 1010), ("world.", 1010, 1020)]), 24, 1920, 1080,
                                     990, 1040, self.home / "c.mov", self.home, style="karaoke")
        kind, spec = self.specs[0]
        self.assertEqual(kind, "captions")
        self.assertEqual(spec["duration_frames"], 50)
        self.assertEqual(spec["cues"][0]["start"], 10)
        self.assertEqual(spec["cues"][0]["words"][1]["start"], 20)
        self.assertEqual(spec["style"], "karaoke")
        self.assertEqual(out["cues"], 1)

    def test_captions_validate_inputs(self):
        with self.assertRaises(mr.RenderError):
            mr.render_captions([], 24, 1920, 1080, 0, 10, self.home / "c.mov", self.home)
        with self.assertRaises(mr.RenderError):
            mr.render_captions(words([("a", 0, 5)]), 24, 1920, 1080, 0, 10, self.home / "c.mov", self.home, style="wiggle")
        with self.assertRaises(mr.RenderError):
            mr.render_captions(words([("a", 0, 5)]), 0, 1920, 1080, 0, 10, self.home / "c.mov", self.home)

    def test_contact_sheet_numbers_and_skips(self):
        media = self.home / "a.mov"
        media.write_bytes(b"m")
        sources = [{"item_id": "a", "label": "V1.1", "name": "A", "track": "V1", "file_path": str(media),
                    "source_start_sec": 0, "duration_sec": 10, "timeline_start_frame": 86400, "fps": 24},
                   {"item_id": "b", "name": "B", "file_path": "/nope.mov", "source_start_sec": 0, "duration_sec": 5,
                    "timeline_start_frame": 0, "fps": 24}]
        with patch.object(mr, "helper_path", return_value="/bin/helper"), patch("subprocess.run", side_effect=self.fake_helper):
            sheets = mr.contact_sheets(sources, self.home, "/bin/ffmpeg", frames_per_clip=3, max_cells=2)
        self.assertEqual([c["n"] for s in sheets for c in s["cells"]], [1, 2, 3])
        self.assertEqual(sheets[0]["skipped"][0]["item_id"], "b")
        first = sheets[0]["cells"][0]
        self.assertGreater(first["timeline_frame"], 86400)
        self.assertTrue(first["timecode"].startswith("01:00:"))
        spec = self.specs[0][1]
        self.assertEqual(spec["cells"][0]["subtitle"].split(" · ")[0], "V1")

    def test_frame_budget_is_bounded(self):
        media = self.home / "a.mov"
        media.write_bytes(b"m")
        sources = [{"item_id": str(i), "name": str(i), "file_path": str(media), "source_start_sec": 0,
                    "duration_sec": 10, "timeline_start_frame": 0, "fps": 24} for i in range(150)]
        calls = []

        def counting(command, **kwargs):
            calls.append(command)
            return self.fake_helper(command, **kwargs)
        with patch.object(mr, "helper_path", return_value="/bin/helper"), patch("subprocess.run", side_effect=counting):
            mr.contact_sheets(sources, self.home, "/bin/ffmpeg", frames_per_clip=4)
        self.assertLessEqual(len([c for c in calls if c[0] == "/bin/ffmpeg"]), mr.MAX_FFMPEG_CALLS)


@unittest.skipUnless(NATIVE and FFMPEG, "needs macOS with Xcode command line tools and ffmpeg")
class NativeRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.tmp.name)
        mr.helper_path(cls.home)  # compile once

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_helper_is_cached_until_source_changes(self):
        binary = Path(mr.helper_path(self.home))
        mtime = binary.stat().st_mtime
        self.assertEqual(Path(mr.helper_path(self.home)).stat().st_mtime, mtime)

    def test_captions_movie_has_alpha(self):
        out = self.home / "captions.mov"
        result = mr.render_captions(words([("Fast", 0, 12), ("native", 12, 24), ("captions.", 24, 40)]), 24, 320, 180,
                                    0, 48, out, self.home, style="pop")
        self.assertTrue(out.is_file())
        self.assertEqual(result["duration_frames"], 48)
        if FFPROBE:
            probe = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                    "stream=codec_name,pix_fmt,width,height", "-of", "json", str(out)],
                                   capture_output=True, text=True)
            stream = json.loads(probe.stdout)["streams"][0]
            self.assertEqual(stream["codec_name"], "prores")
            self.assertTrue(stream["pix_fmt"].startswith("yuva444p"))
            self.assertEqual((stream["width"], stream["height"]), (320, 180))

    def test_contact_sheet_renders(self):
        clips = []
        for i, pattern in enumerate(("testsrc", "testsrc2")):
            path = self.home / ("clip%d.mp4" % i)
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "%s=s=640x360:r=24:d=2" % pattern,
                            "-pix_fmt", "yuv420p", str(path)], check=True)
            clips.append({"item_id": "c%d" % i, "label": "V1.%d" % (i + 1), "name": "Clip %d" % i, "track": "V1",
                          "file_path": str(path), "source_start_sec": 0, "duration_sec": 2,
                          "timeline_start_frame": 86400 + i * 48, "fps": 24})
        sheets = mr.contact_sheets(clips, self.home, FFMPEG, frames_per_clip=3, cell_width=240)
        self.assertEqual(len(sheets), 1)
        sheet = sheets[0]
        self.assertTrue(Path(sheet["path"]).is_file())
        self.assertEqual(len(sheet["cells"]), 6)
        self.assertGreater(sheet["width"], 3 * 240)
        self.assertGreater(sheet["height"], 2 * 135)


if __name__ == "__main__":
    unittest.main()
