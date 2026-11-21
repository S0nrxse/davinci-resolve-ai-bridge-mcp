"""render_frames: stills of the final picture from temporary Deliver renders, always cleaned up."""
import base64
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # fakes, when run as tests.test_render_frames

from bridge import qc
from bridge.operations import ResolveOperations
from fakes import Clip, Resolve
from test_render_check import FPS, TL_START, RenderProject

FFMPEG = os.environ.get("RESOLVE_AI_BRIDGE_FFMPEG") or shutil.which("ffmpeg")


def tiny_png(width, height):
    """A valid grey RGB PNG, standard library only."""
    def chunk(kind, data):
        return (len(data).to_bytes(4, "big") + kind + data
                + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big"))
    raw = b"".join(b"\x00" + b"\x80" * (3 * width) for _ in range(height))
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0])
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class FrameProject(RenderProject):
    """Rendering completes at once and writes a placeholder file, unless ``render`` says otherwise."""

    def __init__(self, length=2000, render=None):
        super().__init__([Clip(TL_START, TL_START + length, name="Picture")])
        self.render = render

    def StartRendering(self, *job_ids):
        self._log("StartRendering", *job_ids)
        for job_id in job_ids:
            job = next(job for job in self.jobs if job["JobId"] == job_id)
            self.state.update(format=job["format"], codec=job["codec"])
            self.current.SetMarkInOut(job["MarkIn"] - TL_START, job["MarkOut"] - TL_START)
            target = Path(job["TargetDir"]) / job["OutputFilename"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if self.render is not None:
                self.render(job, target)
            else:
                target.write_bytes(b"not really a movie")
            self.status.setdefault(job_id, {})
            if self.status[job_id].get("JobStatus") in (None, "Ready"):
                self.status[job_id] = {"JobStatus": "Complete", "CompletionPercentage": 100}
        return True


def fake_extract(fail=()):
    """Stands in for ffmpeg: a small PNG per index, or an error for the indices in ``fail``."""
    def extract(ffmpeg, video, indices, width, workdir, timeout=300):
        assert Path(video).is_file(), video
        out = {}
        for index in indices:
            if index in fail:
                out[index] = "ffmpeg produced no image for frame %d of the rendered preview." % index
                continue
            path = Path(workdir) / ("frame-%06d.png" % index)
            path.write_bytes(tiny_png(min(width, 64), 1 + index))  # height tells the frames apart
            out[index] = path
        return out
    return extract


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "runtime"
        self.check = self.home / "renders" / "check"
        self.calls = []

    def ops_for(self, project):
        self.project = project
        self.ops = ResolveOperations(lambda: Resolve(project), runtime_home=str(self.home))
        return self.ops

    def call(self, operation, params, timeout):
        self.calls.append((operation, dict(params)))
        return self.ops.dispatch(operation, params)

    def names(self):
        return [name for name, _ in self.calls]

    def run_frames(self, extract=None, call=None, **kwargs):
        kwargs.setdefault("sleep", lambda _: None)
        with mock.patch.object(qc, "extract_frames", extract or fake_extract()):
            return qc.render_frames(call or self.call, "ffmpeg", self.check, **kwargs)

    def assert_deliver_restored(self):
        self.assertEqual(self.project.jobs, [])
        self.assertEqual(self.project.presets, ["H.264 Master", "YouTube - 1080p"])
        self.assertEqual(self.project.GetCurrentRenderFormatAndCodec(), {"format": "mov", "codec": "ProRes422"})
        self.assertEqual(self.project.state["mode"], 0)
        self.assertEqual(self.project.current.GetMarkInOut(), {"video": {}, "audio": {}})

    def assert_every_job_cleaned_with_restore(self):
        previews = [params for name, params in self.calls if name == "render_preview"]
        cleanups = [params for name, params in self.calls if name == "render_preview_cleanup"]
        self.assertEqual(len(cleanups), len(previews))
        for params in cleanups:
            self.assertFalse(params["keep_file"])
            self.assertTrue(params["restore"]["preset"].startswith("ResolveAIBridge-QC-"))
            self.assertEqual(set(params["restore"]["before"]), {"format_codec", "render_mode", "mark_in_out", "page"})


class PageRestoreTests(Harness):
    """Starting a render switches Resolve to the Deliver page; cleanup puts the user's page back."""

    def test_page_is_put_back_after_each_render(self):
        project = FrameProject()
        state = {"page": "edit"}

        class PagedResolve(Resolve):
            def GetCurrentPage(self):
                return state["page"]

            def OpenPage(self, page):
                state["page"] = page
                return True

        self.project = project
        self.ops = ResolveOperations(lambda: PagedResolve(project), runtime_home=str(self.home))

        def call(operation, params, timeout):
            result = self.call(operation, params, timeout)
            if operation == "render_preview":
                state["page"] = "deliver"  # What StartRendering does in Resolve.
            return result

        result = self.run_frames(call=call, frames=[TL_START + 10])
        self.assertEqual(state["page"], "edit")
        self.assertTrue(result["settings_restored"])
        cleanups = [params for name, params in self.calls if name == "render_preview_cleanup"]
        self.assertEqual(cleanups[0]["restore"]["before"]["page"], "edit")


# ----------------------------------------------------------------- planning


class PickTests(unittest.TestCase):
    def test_even_picks_from_a_range_include_the_first_frame_and_stay_inside(self):
        self.assertEqual(qc.pick_frames(start_frame=100, end_frame=160, count=6), [100, 110, 120, 130, 140, 150])
        picks = qc.pick_frames(start_frame=86400, end_frame=86407, count=3)
        self.assertEqual(picks, [86400, 86402, 86404])
        self.assertEqual(qc.pick_frames(start_frame=10, end_frame=11, count=5), [10])
        self.assertEqual(qc.pick_frames(start_frame=10, end_frame=13, count=6), [10, 11, 12])
        for start, end, count in ((0, 1000, 7), (5, 17, 10), (86400, 86400 + 241, 9)):
            picks = qc.pick_frames(start_frame=start, end_frame=end, count=count)
            self.assertEqual(picks[0], start)
            self.assertLess(picks[-1], end)
            self.assertEqual(len(picks), count)

    def test_explicit_frames_are_sorted_deduplicated_and_capped(self):
        self.assertEqual(qc.pick_frames([300, 100, 300, 200.0, "100"]), [100, 200, 300])
        self.assertEqual(len(qc.pick_frames(list(range(10)) * 3)), 10)
        with self.assertRaisesRegex(ValueError, "11 distinct frames; at most 10"):
            qc.pick_frames(list(range(11)))
        with self.assertRaisesRegex(ValueError, "at most 3"):
            qc.pick_frames([1, 2, 3, 4], max_frames=3)

    def test_bad_requests_are_refused_clearly(self):
        cases = [({"frames": [1], "start_frame": 0, "end_frame": 5}, "not both"),
                 ({}, "Pass frames"), ({"start_frame": 5}, "both start_frame and end_frame"),
                 ({"frames": []}, "empty"), ({"frames": 12}, "list"), ({"frames": [True]}, "not True"),
                 ({"frames": [1.5]}, "whole"), ({"frames": [-1]}, "non-negative"),
                 ({"start_frame": 10, "end_frame": 10}, "exclusive"), ({"start_frame": 0, "end_frame": 9, "count": 0},
                                                                      "between 1 and 10"),
                 ({"start_frame": 0, "end_frame": 99, "count": 11}, "between 1 and 10")]
        for kwargs, message in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError, message):
                    qc.pick_frames(**kwargs)

    def test_spans_never_exceed_the_limit(self):
        self.assertEqual(qc.plan_spans([0, 100, 319, 320, 1000], 16, 20.0), [[0, 100, 319], [320], [1000]])
        self.assertEqual(qc.plan_spans([0, 479, 480], 24, 20.0), [[0, 479], [480]])
        self.assertEqual(qc.plan_spans([5, 6, 7], 24, 0.001), [[5], [6], [7]])

    def test_invalid_input_returns_an_error_without_any_bridge_call(self):
        def call(*_):
            raise AssertionError("no bridge call expected")
        for kwargs in ({"frames": list(range(11))}, {"frames": [1], "width": 0}, {"frames": [1], "width": "wide"},
                       {"frames": [1], "max_span_seconds": 0}, {}):
            result = qc.render_frames(call, "ffmpeg", "/nonexistent/check", **kwargs)
            self.assertFalse(result["ok"])
            self.assertTrue(result["error"])
        self.assertIn("ffmpeg is needed", qc.render_frames(call, None, "/nonexistent", frames=[1])["error"])

    def test_png_size_reads_the_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.png"
            path.write_bytes(tiny_png(33, 7))
            self.assertEqual(qc.png_size(path), (33, 7))
            path.write_bytes(b"GIF89a....................")
            self.assertIsNone(qc.png_size(path))


# ------------------------------------------------------------ orchestration


class RenderFramesTests(Harness):
    def test_close_frames_render_once_and_everything_is_cleaned_up(self):
        self.ops_for(FrameProject())
        result = self.run_frames(start_frame=TL_START, end_frame=TL_START + 120, count=4, width=500)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["extracted"], 4)
        self.assertEqual(self.names(), ["render_preview", "render_status", "render_preview_cleanup"])
        preview = self.calls[0][1]
        self.assertEqual((preview["start_frame"], preview["end_frame"], preview["width"]),
                         (TL_START, TL_START + 91, 500))
        self.assertEqual([(r["start_frame"], r["end_frame"]) for r in result["renders"]], [(TL_START, TL_START + 91)])
        frames = result["frames"]
        self.assertEqual([f["timeline_frame"] for f in frames], [TL_START, TL_START + 30, TL_START + 60, TL_START + 90])
        self.assertEqual(frames[1]["timecode"], "01:00:01:06")
        for frame in frames:
            self.assertNotIn("error", frame)
            self.assertEqual(base64.b64decode(frame["png_base64"])[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual((frame["width"], frame["height"]), (64, 1 + frame["timeline_frame"] - TL_START))
            self.assertNotIn("path", frame)
        self.assertTrue(result["settings_restored"])
        self.assertIn("real Deliver render", result["note"])
        self.assertIn("H264", result["note"])
        self.assertIn("restored afterwards", result["note"])
        self.assert_every_job_cleaned_with_restore()
        self.assert_deliver_restored()
        self.assertEqual(list(self.check.iterdir()), [])  # no PNGs, renders or temp folders left

    def test_far_apart_frames_become_sequential_jobs(self):
        self.ops_for(FrameProject(length=2000))
        wanted = [TL_START + 1990, TL_START, TL_START + 100, TL_START + 1000, TL_START + 1100]
        result = self.run_frames(frames=wanted)
        self.assertTrue(result["ok"], result)
        # First job planned at 16 fps (320 frames), later ones at the reported 24 fps (480 frames).
        self.assertEqual([(r["start_frame"] - TL_START, r["end_frame"] - TL_START) for r in result["renders"]],
                         [(0, 101), (1000, 1101), (1990, 1991)])
        self.assertEqual(self.names(), ["render_preview", "render_status", "render_preview_cleanup"] * 3)
        self.assertEqual([f["timeline_frame"] for f in result["frames"]], sorted(wanted))
        for frame, render_start in zip(result["frames"], [0, 0, 1000, 1000, 1990]):
            data = base64.b64decode(frame["png_base64"])  # each still is its own render's frame, never overwritten
            self.assertEqual(int.from_bytes(data[20:24], "big"), 1 + frame["timeline_frame"] - TL_START - render_start)
        self.assertEqual(len(result["cleanup"]), 3)
        self.assertTrue(result["settings_restored"])
        self.assert_every_job_cleaned_with_restore()
        self.assert_deliver_restored()

    def test_keep_files_returns_paths_inside_frames_dir(self):
        self.ops_for(FrameProject())
        result = self.run_frames(frames=[TL_START + 5], keep_files=True)
        path = Path(result["frames"][0]["path"])
        self.assertTrue(path.is_file())
        self.assertIn(self.check.resolve(), path.resolve().parents)
        self.assertEqual(Path(result["frames_dir"]), path.parent.parent)
        self.assert_deliver_restored()

    def test_failed_status_still_cleans_up_and_reports_no_frames(self):
        self.ops_for(FrameProject())
        project = self.project
        project.StartRendering = lambda job_id: project.status.update(
            {job_id: {"JobStatus": "Failed", "Error": "disk full"}}) or True
        result = self.run_frames(frames=[TL_START, TL_START + 10])
        self.assertFalse(result["ok"])
        self.assertIn("Failed: disk full", result["error"])
        self.assertTrue(all("Failed: disk full" in f["error"] and f["png_base64"] is None for f in result["frames"]))
        self.assertEqual(self.names()[-1], "render_preview_cleanup")
        self.assertTrue(result["settings_restored"])
        self.assert_every_job_cleaned_with_restore()
        self.assert_deliver_restored()

    def test_render_that_never_starts_or_finishes_is_cleaned_up_and_stops_later_jobs(self):
        self.ops_for(FrameProject())
        project = self.project
        project.StartRendering = lambda job_id: False
        result = self.run_frames(frames=[TL_START, TL_START + 1900])
        self.assertFalse(result["ok"])
        self.assertEqual(self.names(), ["render_preview", "render_preview_cleanup"])
        self.assertIn("did not start", result["frames"][0]["error"])
        self.assertIn("Not rendered", result["frames"][1]["error"])
        self.assert_every_job_cleaned_with_restore()
        self.assert_deliver_restored()

        self.calls.clear()
        self.ops_for(RenderProject([Clip(TL_START, TL_START + 2000)]))  # never completes
        ticks = iter(range(0, 10000, 30))
        result = self.run_frames(frames=[TL_START, TL_START + 1900], max_wait_seconds=100, clock=lambda: next(ticks))
        self.assertIn("did not finish", result["frames"][0]["error"])
        self.assertEqual(self.names().count("render_preview"), 1)
        self.assertTrue(result["cleanup"][0]["stopped_render"])
        self.assert_every_job_cleaned_with_restore()
        self.assert_deliver_restored()

    def test_queue_failure_needs_no_cleanup_and_claims_nothing(self):
        self.ops_for(FrameProject(length=100))
        result = self.run_frames(frames=[TL_START + 500])  # outside the timeline: render_preview refuses
        self.assertFalse(result["ok"])
        self.assertIn("outside the timeline", result["frames"][0]["error"])
        self.assertEqual(self.names(), ["render_preview"])
        self.assertIsNone(result["settings_restored"])
        self.assertEqual(result["cleanup"], [])
        self.assert_deliver_restored()

    def test_partial_extraction_is_reported_per_frame(self):
        self.ops_for(FrameProject())
        result = self.run_frames(frames=[TL_START, TL_START + 7, TL_START + 9], extract=fake_extract(fail={7}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["extracted"], 2)
        self.assertIn("Only 2 of 3", result["warning"])
        bad = result["frames"][1]
        self.assertEqual(bad["timeline_frame"], TL_START + 7)
        self.assertIsNone(bad["png_base64"])
        self.assertIn("frame 7", bad["error"])
        self.assertEqual(bad["timecode"], "01:00:00:07")
        self.assertTrue(result["frames"][0]["png_base64"] and result["frames"][2]["png_base64"])
        self.assert_every_job_cleaned_with_restore()
        self.assert_deliver_restored()

    def test_extraction_crash_still_cleans_up(self):
        self.ops_for(FrameProject())

        def broken(*_args, **_kwargs):
            raise OSError("decoder exploded")
        result = self.run_frames(frames=[TL_START], extract=broken)
        self.assertFalse(result["ok"])
        self.assertIn("decoder exploded", result["frames"][0]["error"])
        self.assertEqual(self.names()[-1], "render_preview_cleanup")
        self.assertTrue(result["settings_restored"])
        self.assert_deliver_restored()
        self.assertEqual(list(self.check.iterdir()), [])

    def test_missing_file_after_complete_is_an_error_not_a_frame(self):
        self.ops_for(FrameProject(render=lambda job, target: None))  # Complete, but no file
        result = self.run_frames(frames=[TL_START])
        self.assertFalse(result["ok"])
        self.assertIn("did not appear", result["frames"][0]["error"])
        self.assert_deliver_restored()

    def test_cleanup_error_marks_settings_unrestored_and_stops_further_jobs(self):
        self.ops_for(FrameProject())

        def call(operation, params, timeout):
            if operation == "render_preview_cleanup":
                self.calls.append((operation, dict(params)))
                raise RuntimeError("Request record: x. No automatic replay or Python fallback was sent.")
            return self.call(operation, params, timeout)
        result = self.run_frames(frames=[TL_START, TL_START + 1900], call=call)
        self.assertFalse(result["settings_restored"])
        self.assertIn("may not be restored", result["cleanup"][0]["error"])
        self.assertIn("check the Deliver page", result["note"])
        self.assertTrue(result["frames"][0]["png_base64"])  # extracted before cleanup, so still honest
        self.assertIn("Not rendered", result["frames"][1]["error"])
        self.assertEqual(self.names().count("render_preview"), 1)

    def test_unconfirmed_restore_is_not_reported_as_restored(self):
        self.ops_for(FrameProject())

        def call(operation, params, timeout):
            reply = self.call(operation, params, timeout)
            if operation == "render_preview_cleanup":
                reply = dict(reply, settings_restored=False)
            return reply
        result = self.run_frames(frames=[TL_START, TL_START + 1900], call=call)
        self.assertFalse(result["settings_restored"])
        self.assertIn("check the Deliver page", result["note"])
        self.assertEqual(self.names().count("render_preview"), 1)

    def test_out_of_range_frame_from_a_short_render_is_not_claimed(self):
        self.ops_for(FrameProject())

        def call(operation, params, timeout):
            reply = self.call(operation, params, timeout)
            if operation == "render_preview":
                reply = dict(reply, start_frame=reply["start_frame"] + 5)  # Resolve rendered a different range
            return reply
        result = self.run_frames(frames=[TL_START, TL_START + 9], call=call)
        self.assertIn("outside the rendered range", result["frames"][0]["error"])
        self.assertTrue(result["frames"][1]["png_base64"])
        self.assert_deliver_restored()


# ------------------------------------------------------------ real ffmpeg


def _has_encoder():
    if not FFMPEG:
        return False
    proc = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True)
    encoders = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return " geq " in proc.stdout and "libx264" in encoders


@unittest.skipUnless(_has_encoder(), "ffmpeg with geq and libx264 is needed")
class FfmpegFrameTests(Harness):
    """A master clip whose frame n is a distinct solid colour; the fake Deliver render cuts [MarkIn, MarkOut]."""

    STEP = 4
    LENGTH = 60

    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        cls.master = Path(cls.media.name) / "master.mp4"
        colours = "geq=r='%d*N':g='128':b='255-%d*N'" % (cls.STEP, cls.STEP)
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "nullsrc=s=64x36:r=%d:d=%f,format=rgb24,%s" % (FPS, cls.LENGTH / FPS + 0.001, colours),
                        "-frames:v", str(cls.LENGTH), "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p",
                        str(cls.master)], check=True, timeout=120)

    @classmethod
    def tearDownClass(cls):
        cls.media.cleanup()

    def render(self, job, target):
        """Like Deliver: frames MarkIn..MarkOut (timeline-absolute, inclusive) at the job's size, H.264."""
        first, last = job["MarkIn"] - TL_START, job["MarkOut"] - TL_START
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(self.master), "-vf",
                        "select='between(n,%d,%d)',setpts=N/%d/TB,scale=%d:%d" % (
                            first, last, FPS, job["FormatWidth"], job["FormatHeight"]),
                        "-r", str(FPS), "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", str(target)],
                       check=True, timeout=120)

    def centre_rgb(self, png_bytes):
        path = Path(self.tmp.name) / "probe.png"
        path.write_bytes(png_bytes)
        out = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf",
                              "crop=1:1:iw/2:ih/2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True, timeout=60).stdout
        return tuple(out[:3])

    def test_each_png_is_exactly_the_requested_frame(self):
        self.ops_for(FrameProject(length=self.LENGTH, render=self.render))
        wanted = [0, 1, 7, 8, 9, 23, 24, 36, 58, 59]
        # 0.5 s spans: 8 frames planned at 16 fps for the first job, then 12 at the reported 24 fps.
        result = qc.render_frames(self.call, FFMPEG, self.check, frames=[TL_START + n for n in wanted], width=100,
                                  max_span_seconds=0.5, sleep=lambda _: None)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["extracted"], len(wanted), [f.get("error") for f in result["frames"]])
        self.assertEqual([(r["start_frame"] - TL_START, r["end_frame"] - TL_START) for r in result["renders"]],
                         [(0, 8), (8, 10), (23, 25), (36, 37), (58, 60)])
        for frame in result["frames"]:
            index = frame["timeline_frame"] - TL_START
            red, green, blue = self.centre_rgb(base64.b64decode(frame["png_base64"]))
            self.assertEqual(int(round(red / float(self.STEP))), index, (index, red, green, blue))
            self.assertLessEqual(abs(blue - (255 - self.STEP * index)), 3, (index, red, green, blue))
            self.assertLessEqual(abs(green - 128), 2)
            self.assertEqual((frame["width"], frame["height"]), (100, 56))  # rendered at 160x90, scaled to width
        self.assertTrue(result["settings_restored"])
        self.assert_deliver_restored()
        self.assertEqual(list(self.check.iterdir()), [])

    def test_frame_missing_from_a_short_file_is_an_error(self):
        def short(job, target):
            self.render(dict(job, MarkOut=job["MarkOut"] - 3), target)  # Resolve rendered 3 frames too few
        self.ops_for(FrameProject(length=self.LENGTH, render=short))
        result = qc.render_frames(self.call, FFMPEG, self.check, frames=[TL_START + 2, TL_START + 20],
                                  sleep=lambda _: None)
        self.assertTrue(result["ok"])
        self.assertTrue(result["frames"][0]["png_base64"])
        self.assertIsNone(result["frames"][1]["png_base64"])
        self.assertIn("no image for frame 18", result["frames"][1]["error"])
        self.assertEqual(self.centre_rgb(base64.b64decode(result["frames"][0]["png_base64"]))[0] // self.STEP, 2)


if __name__ == "__main__":
    unittest.main()
