"""render_check: preview render with Deliver settings restored, job cleanup, and ffmpeg QC analysis."""
import array
import math
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from bridge import qc
from bridge.operations import OperationError, ResolveOperations
from fakes import Clip, Project, Resolve, Timeline

FFMPEG = ResolveOperations._ffmpeg_path() or shutil.which("ffmpeg")
SR = 48000
FPS = 24
TL_START = 86400


# ------------------------------------------------------------------ media


def _tone(count, amp, phase=0.0, freq=440.0):
    return [amp * math.sin(2 * math.pi * freq * i / SR + phase) for i in range(count)]


def _noise(count, db, rng):
    level = 10 ** (db / 20.0)
    return [rng.gauss(0, level) for _ in range(count)]


def _mix(*parts):
    return [sum(values) for values in zip(*parts)]


def _n(seconds):
    return int(round(seconds * SR))


def _encode(path, video_filter, audio, seconds):
    """H.264 + AAC MP4, like Resolve's preview render."""
    raw = Path(str(path) + ".raw")
    raw.write_bytes(array.array("f", audio).tobytes())
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *video_filter,
                    "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", str(raw),
                    "-filter_complex", "concat=n=%d:v=1:a=0,format=yuv420p[v];[%d:a]pan=stereo|c0=c0|c1=c0[a]"
                    % (video_filter.count("-f"), video_filter.count("-f")),
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                    "-b:a", "192k", "-t", str(seconds), str(path)], check=True, timeout=120)
    raw.unlink()


def make_program(path):
    """16 s at 24 fps with known problems.

    Picture: shots 0-2 s, black 2.0-2.5 s, shot to 4.5 s, a 2-frame red flash, shot, a 1 s frozen hold at
    8.083 s, shot to the end. Audio: a click at the 2.0 s cut, a clean cut at 4.0 s, digital silence
    5.0-5.2 s, a room-tone jump (-60 -> -40 dBFS) at the 8.0 s cut, and a loud section 11-13 s.
    """
    rng = random.Random(7)
    audio = _mix(_tone(_n(2.0), 0.1, math.pi / 2), _noise(_n(2.0), -60, rng))      # ends at +peak
    audio += _mix(_tone(_n(3.0), 0.1, -math.pi / 2), _noise(_n(3.0), -60, rng))    # starts at -peak: click
    audio += [0.0] * _n(0.2) + _tone(_n(0.8), 0.1)
    audio += _noise(_n(2.0), -60, rng) + _noise(_n(2.0), -40, rng)
    audio += _tone(_n(1.0), 0.1) + _tone(_n(2.0), 0.7) + _tone(_n(3.0), 0.1)
    size = "size=640x360:rate=%d" % FPS
    video = ["-f", "lavfi", "-i", "testsrc2=%s:duration=2" % size,
             "-f", "lavfi", "-i", "color=black:%s:duration=0.5" % size,
             "-f", "lavfi", "-i", "mandelbrot=%s,trim=duration=2" % size,
             "-f", "lavfi", "-i", "color=red:%s,trim=end_frame=2" % size,
             "-f", "lavfi", "-i", "testsrc2=%s:duration=3.5" % size,
             "-f", "lavfi", "-i", "smptebars=%s:duration=1" % size,
             "-f", "lavfi", "-i", "mandelbrot=%s,trim=duration=7" % size]
    _encode(path, video, audio, 16)


def make_clipped(path):
    audio = _tone(_n(1.0), 0.1) + [max(-1.0, min(1.0, x)) for x in _tone(_n(1.0), 1.4)] + _tone(_n(1.0), 0.1)
    _encode(path, ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=%d:duration=3" % FPS], audio, 3)


# ------------------------------------------------------------------ fakes


class RenderTimeline(Timeline):
    """Adds Resolve 19+'s mark in/out API; render MarkIn/MarkOut also move these marks."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.marks = {"video": {}, "audio": {}}

    def GetMarkInOut(self):
        return {kind: dict(value) for kind, value in self.marks.items()}

    def SetMarkInOut(self, mark_in, mark_out, kind="all"):
        if kind != "all":  # Resolve 21.2 Free, seen live: sets the In, drops the Out, returns False.
            self.marks[kind] = {"in": mark_in}
            return False
        for name in ("video", "audio"):
            self.marks[name] = {"in": mark_in, "out": mark_out}
        return True

    def ClearMarkInOut(self, kind="all"):
        for name in (("video", "audio") if kind == "all" else (kind,)):
            self.marks[name] = {}
        return True


class RenderProject(Project):
    """fakes.Project plus the ordinary render API, logging every call in order."""

    def __init__(self, clips=None, render_source=None):
        super().__init__(clips=None)
        self.current = RenderTimeline(self, clips=clips, start=TL_START, fps=FPS)
        self.current.start_tc = "01:00:00:00"
        self.timelines = [self.current]
        self.log = []
        self.formats = {"MP4": "mp4", "QuickTime": "mov", "Wave": "wav"}
        self.codecs = {"mp4": {"H.264": "H264", "H.265": "H265"}, "mov": {"H.264": "H264", "Apple ProRes 422": "ProRes422"}}
        self.presets = ["H.264 Master", "YouTube - 1080p"]
        self.saved = {}
        self.state = {"format": "mov", "codec": "ProRes422", "mode": 0, "settings": {"TargetDir": "/user/exports"}}
        self.jobs = []
        self.status = {}
        self.render_source = render_source
        self.rendering = False
        self.fail = set()

    def _log(self, name, *args):
        self.log.append((name,) + args)

    def GetRenderFormats(self):
        return dict(self.formats)

    def GetRenderCodecs(self, fmt):
        return dict(self.codecs.get(fmt, {}))

    def GetCurrentRenderFormatAndCodec(self):
        return {"format": self.state["format"], "codec": self.state["codec"]}

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        self._log("SetCurrentRenderFormatAndCodec", fmt, codec)
        if codec not in self.codecs.get(fmt, {}).values():
            return False
        self.state.update(format=fmt, codec=codec)
        return True

    def GetCurrentRenderMode(self):
        return self.state["mode"]

    def SetCurrentRenderMode(self, mode):
        self._log("SetCurrentRenderMode", mode)
        self.state["mode"] = mode
        return True

    def GetRenderPresetList(self):
        return list(self.presets)

    def SaveAsNewRenderPreset(self, name):
        self._log("SaveAsNewRenderPreset", name)
        if "save" in self.fail or name in self.presets:
            return False
        self.presets.append(name)
        self.saved[name] = {"format": self.state["format"], "codec": self.state["codec"],
                            "settings": dict(self.state["settings"])}
        return True

    def LoadRenderPreset(self, name):
        self._log("LoadRenderPreset", name)
        if name not in self.saved:
            return False
        preset = self.saved[name]
        self.state.update(format=preset["format"], codec=preset["codec"], settings=dict(preset["settings"]))
        return True

    def DeleteRenderPreset(self, name):
        self._log("DeleteRenderPreset", name)
        if name not in self.presets:
            return False
        self.presets.remove(name)
        self.saved.pop(name, None)
        return True

    def SetRenderSettings(self, settings):
        self._log("SetRenderSettings", dict(settings))
        if "settings" in self.fail:
            return False
        self.state["settings"].update(settings)
        if "MarkIn" in settings:  # Resolve's render range shares the timeline's In/Out marks.
            self.current.SetMarkInOut(settings["MarkIn"] - TL_START, settings["MarkOut"] - TL_START)
        return True

    def AddRenderJob(self):
        self._log("AddRenderJob")
        settings = self.state["settings"]
        job_id = "job-%d" % (len(self.jobs) + 1)
        extension = self.state["format"]
        self.jobs.append({"JobId": job_id, "TargetDir": settings.get("TargetDir"), "TimelineName": self.current.name,
                          "OutputFilename": "%s.%s" % (settings.get("CustomName", "Timeline 1"), extension),
                          "MarkIn": settings.get("MarkIn"), "MarkOut": settings.get("MarkOut"),
                          "FormatWidth": settings.get("FormatWidth"), "FormatHeight": settings.get("FormatHeight"),
                          "format": self.state["format"], "codec": self.state["codec"]})
        self.status[job_id] = {"JobStatus": "Ready", "CompletionPercentage": 0}
        return job_id

    def GetRenderJobList(self):
        return [{key: value for key, value in job.items() if key not in ("format", "codec")} for job in self.jobs]

    def GetRenderJobStatus(self, job_id):
        return dict(self.status.get(job_id, {}))

    def StartRendering(self, *job_ids):
        self._log("StartRendering", *job_ids)
        for job_id in job_ids:
            job = next(job for job in self.jobs if job["JobId"] == job_id)
            # Live on Resolve 21.2 Free, rendering put the job's format/codec and range back on the Deliver page.
            self.state.update(format=job["format"], codec=job["codec"])
            self.current.SetMarkInOut(job["MarkIn"] - TL_START, job["MarkOut"] - TL_START)
            if self.render_source is not None:
                Path(job["TargetDir"]).mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.render_source, Path(job["TargetDir"]) / job["OutputFilename"])
                self.status[job_id] = {"JobStatus": "Complete", "CompletionPercentage": 100}
            else:
                self.status[job_id] = {"JobStatus": "Rendering", "CompletionPercentage": 10}
                self.rendering = True
        return True

    def IsRenderingInProgress(self):
        return self.rendering

    def StopRendering(self):
        self._log("StopRendering")
        self.rendering = False
        for status in self.status.values():
            if status["JobStatus"] == "Rendering":
                status["JobStatus"] = "Cancelled"

    def DeleteRenderJob(self, job_id):
        self._log("DeleteRenderJob", job_id)
        before = len(self.jobs)
        self.jobs = [job for job in self.jobs if job["JobId"] != job_id]
        return len(self.jobs) < before


def program_clips():
    """Video over the 16 s range plus audio clips cut at 2.0 s, 4.0 s and 8.0 s (frames 48, 96, 192)."""
    start = TL_START
    return [Clip(start, start + 384, name="Picture"),
            Clip(start, start + 48, kind="audio", name="A"), Clip(start + 48, start + 96, kind="audio", name="B"),
            Clip(start + 96, start + 192, kind="audio", name="C"), Clip(start + 192, start + 384, kind="audio", name="D"),
            Clip(start + 100, start + 150, kind="audio", track=2, name="Muted")]


class RenderSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "runtime"
        self.check = self.home / "renders" / "check"
        self.project = RenderProject(program_clips())
        self.project.current.disabled.add(("audio", 2))
        self.ops = ResolveOperations(lambda: Resolve(self.project), runtime_home=str(self.home))

    def tearDown(self):
        self.tmp.cleanup()

    def preview(self, **params):
        return self.ops.dispatch("render_preview", dict({"output_dir": str(self.check)}, **params))


# ------------------------------------------------------------- operations


class RenderPreviewTests(RenderSetup):
    def test_settings_are_saved_before_changes_and_restored_after_the_job(self):
        result = self.preview(start_frame=TL_START + 24, end_frame=TL_START + 144)
        names = [entry[0] for entry in self.project.log]
        save, add = names.index("SaveAsNewRenderPreset"), names.index("AddRenderJob")
        load, start = names.index("LoadRenderPreset"), names.index("StartRendering")
        first_change = min(names.index(name) for name in ("SetCurrentRenderMode", "SetCurrentRenderFormatAndCodec",
                                                          "SetRenderSettings"))
        self.assertLess(save, first_change)
        self.assertLess(add, load)
        self.assertLess(load, start)
        self.assertNotIn("DeleteRenderPreset", names)  # kept until the render is over
        preset = self.project.log[save][1]
        self.assertTrue(preset.startswith(ResolveOperations.QC_PRESET_PREFIX))
        self.assertEqual(self.project.log[load][1], preset)
        self.assertTrue(result["settings_restored_before_render"])
        self.assertEqual(result["restore"]["unverified"], [])
        self.assertTrue(result["started"])
        self.assertEqual(self.project.log[start][1:], (result["job_id"],))
        self.assertEqual(result["restore_state"]["preset"], preset)
        # Rendering re-applied the job's format and In/Out range, as it did live; cleanup puts them back.
        self.assertEqual(self.project.GetCurrentRenderFormatAndCodec(), {"format": "mp4", "codec": "H264"})
        self.assertEqual(self.project.current.GetMarkInOut()["video"], {"in": 24, "out": 143})
        done = self.ops.dispatch("render_preview_cleanup", {"job_id": result["job_id"], "restore": result["restore_state"]})
        names = [entry[0] for entry in self.project.log]
        self.assertLess(names.index("DeleteRenderJob"), names.index("DeleteRenderPreset"))
        self.assertEqual(names[names.index("DeleteRenderPreset") - 1], "LoadRenderPreset")
        self.assertTrue(done["settings_restored"])
        self.assertEqual(done["restore"]["unverified"], [])
        self.assertEqual(self.project.presets, ["H.264 Master", "YouTube - 1080p"])
        self.assertEqual(self.project.GetCurrentRenderFormatAndCodec(), {"format": "mov", "codec": "ProRes422"})
        self.assertEqual(self.project.state["mode"], 0)
        self.assertEqual(self.project.state["settings"], {"TargetDir": "/user/exports"})
        self.assertEqual(self.project.current.GetMarkInOut(), {"video": {}, "audio": {}})

    def test_render_settings_cover_the_range_at_small_size(self):
        result = self.preview(start_frame=TL_START + 24, end_frame=TL_START + 144)
        settings = next(entry[1] for entry in self.project.log if entry[0] == "SetRenderSettings")
        self.assertEqual((settings["MarkIn"], settings["MarkOut"]), (TL_START + 24, TL_START + 143))
        self.assertIs(settings["SelectAllFrames"], False)
        self.assertEqual((settings["FormatWidth"], settings["FormatHeight"]), (640, 360))
        self.assertEqual(Path(settings["TargetDir"]), self.check.resolve())
        self.assertEqual(("mp4", "H264"), (result["format"], result["codec"]))
        self.assertEqual((result["start_frame"], result["end_frame"]), (TL_START + 24, TL_START + 144))
        self.assertEqual((result["job_mark_in"], result["job_mark_out"]), (TL_START + 24, TL_START + 143))
        self.assertEqual(result["timeline_start_frame"], TL_START)
        self.assertEqual((result["fps"], result["start_timecode"], result["drop_frame"]), (24.0, "01:00:00:00", False))
        self.assertTrue(result["output"].endswith(".mp4"))
        self.assertNotIn("range_warning", result)
        # Edit points inside the range on enabled tracks only (A2 is disabled).
        self.assertEqual(result["audio_edit_points"], [TL_START + 48, TL_START + 96])
        self.project.rendering = False
        whole = self.preview(width=853)
        self.assertEqual((whole["start_frame"], whole["end_frame"], whole["width"], whole["height"]),
                         (TL_START, TL_START + 384, 852, 480))
        self.assertEqual(whole["audio_edit_points"], [TL_START + 48, TL_START + 96, TL_START + 192])

    def test_falls_back_to_quicktime_h264(self):
        self.project.codecs["mp4"] = {"H.265": "H265"}
        result = self.preview()
        self.assertEqual((result["format"], result["codec"]), ("mov", "H264"))
        self.project.rendering = False
        self.project.codecs["mov"] = {"Apple ProRes 422": "ProRes422"}
        with self.assertRaisesRegex(OperationError, "no MP4 or QuickTime H.264"):
            self.preview()

    def test_refuses_output_outside_the_check_folder_without_touching_settings(self):
        with self.assertRaisesRegex(OperationError, "renders/check"):
            self.ops.dispatch("render_preview", {"output_dir": str(self.home / "renders")})
        self.assertEqual(self.project.log, [])

    def test_refuses_bad_ranges_and_busy_resolve(self):
        with self.assertRaisesRegex(OperationError, "outside the timeline"):
            self.preview(start_frame=TL_START - 10, end_frame=TL_START + 10)
        with self.assertRaisesRegex(OperationError, "outside the timeline"):
            self.preview(start_frame=TL_START + 50, end_frame=TL_START + 50)
        self.project.rendering = True
        with self.assertRaisesRegex(OperationError, "already rendering"):
            self.preview()
        self.assertEqual(self.project.log, [])

    def test_unsaved_preset_stops_before_any_change(self):
        self.project.fail.add("save")
        with self.assertRaisesRegex(OperationError, "temporary preset"):
            self.preview()
        self.assertEqual([entry[0] for entry in self.project.log], ["SaveAsNewRenderPreset"])
        self.assertEqual(self.project.jobs, [])

    def test_rejected_settings_still_restore_and_delete_the_preset(self):
        self.project.fail.add("settings")
        with self.assertRaisesRegex(OperationError, "rejected the preview render settings.*restored: True"):
            self.preview()
        names = [entry[0] for entry in self.project.log]
        self.assertIn("LoadRenderPreset", names)
        self.assertIn("DeleteRenderPreset", names)
        self.assertNotIn("AddRenderJob", names)
        self.assertEqual(self.project.presets, ["H.264 Master", "YouTube - 1080p"])
        self.assertEqual(self.project.GetCurrentRenderFormatAndCodec(), {"format": "mov", "codec": "ProRes422"})

    def test_reports_a_restore_it_could_not_verify(self):
        self.project.DeleteRenderPreset = lambda name: False
        result = self.preview()
        done = self.ops.dispatch("render_preview_cleanup", {"job_id": result["job_id"], "restore": result["restore_state"]})
        self.assertFalse(done["restore"]["preset_deleted"])
        self.assertFalse(done["settings_restored"])

    def test_unknown_format_before_is_reported_not_guessed(self):
        self.project.state.update(format="unknown", codec="")
        result = self.preview()
        done = self.ops.dispatch("render_preview_cleanup", {"job_id": result["job_id"], "restore": result["restore_state"]})
        self.assertIsNone(done["restore"]["format_codec_restored"])
        self.assertIn("format_codec_restored", done["restore"]["unverified"])
        self.assertIn("no current render format", done["restore"]["format_codec_note"])

    def test_existing_marks_are_put_back(self):
        self.project.current.SetMarkInOut(10, 20)
        result = self.preview(start_frame=TL_START + 30, end_frame=TL_START + 60)
        self.ops.dispatch("render_preview_cleanup", {"job_id": result["job_id"], "restore": result["restore_state"]})
        self.assertEqual(self.project.current.GetMarkInOut(), {"video": {"in": 10, "out": 20}, "audio": {"in": 10, "out": 20}})


class CleanupTests(RenderSetup):
    def test_removes_the_job_and_file(self):
        result = self.preview()
        output = Path(result["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"x")
        done = self.ops.dispatch("render_preview_cleanup", {"job_id": result["job_id"]})
        self.assertTrue(done["removed"])
        self.assertTrue(done["file_deleted"])
        self.assertFalse(output.exists())
        self.assertEqual(self.project.jobs, [])
        self.assertTrue(done["stopped_render"])  # the fake was still "rendering" the job

    def test_keep_file(self):
        result = self.preview()
        output = Path(result["output"])
        output.write_bytes(b"x")
        done = self.ops.dispatch("render_preview_cleanup", {"job_id": result["job_id"], "keep_file": True})
        self.assertTrue(done["removed"])
        self.assertFalse(done["file_deleted"])
        self.assertTrue(output.exists())

    def test_refuses_foreign_jobs(self):
        exports = Path(self.tmp.name) / "exports"
        exports.mkdir()
        (exports / "Final.mp4").write_bytes(b"x")
        self.project.jobs.append({"JobId": "user-job", "TargetDir": str(exports), "OutputFilename": "Final.mp4"})
        self.project.status["user-job"] = {"JobStatus": "Complete"}
        with self.assertRaisesRegex(OperationError, "Refusing"):
            self.ops.dispatch("render_preview_cleanup", {"job_id": "user-job"})
        # A sibling folder whose name merely starts with "check" is not the check folder.
        self.project.jobs.append({"JobId": "near", "TargetDir": str(self.check) + "-other", "OutputFilename": "a.mp4"})
        with self.assertRaisesRegex(OperationError, "Refusing"):
            self.ops.dispatch("render_preview_cleanup", {"job_id": "near"})
        self.assertEqual([job["JobId"] for job in self.project.jobs], ["user-job", "near"])
        self.assertTrue((exports / "Final.mp4").exists())
        self.assertNotIn("DeleteRenderJob", [entry[0] for entry in self.project.log])
        with self.assertRaisesRegex(OperationError, "not in the queue"):
            self.ops.dispatch("render_preview_cleanup", {"job_id": "missing"})

    def test_restore_only_touches_bridge_presets(self):
        result = self.preview()
        state = dict(result["restore_state"], preset="H.264 Master")
        with self.assertRaisesRegex(OperationError, "only ResolveAIBridge-QC"):
            self.ops.dispatch("render_preview_cleanup", {"job_id": result["job_id"], "restore": state})
        self.assertIn("H.264 Master", self.project.presets)
        self.assertEqual(len(self.project.jobs), 1)


# --------------------------------------------------------------- pure logic


class TimecodeTests(unittest.TestCase):
    def test_non_drop(self):
        self.assertEqual(qc.frames_to_tc(86400 + 292, 24), "01:00:12:04")
        self.assertEqual(qc.tc_to_frames("01:00:12:04", 24), 86692)
        self.assertEqual(qc.timeline_timecode(1000 + 48, 1000, "01:00:00:00", 24), "01:00:02:00")

    def test_drop_frame(self):
        self.assertEqual(qc.frames_to_tc(1800, 30000 / 1001, drop=True), "00:01:00;02")
        self.assertEqual(qc.frames_to_tc(17982, 30000 / 1001, drop=True), "00:10:00;00")
        self.assertEqual(qc.tc_to_frames("00:01:00;02", 30000 / 1001), 1800)
        start = qc.tc_to_frames("01:00:00;00", 30000 / 1001)
        self.assertEqual(qc.timeline_timecode(start + 1800, start, "01:00:00;00", 30000 / 1001, True), "01:01:00;02")
        self.assertEqual(qc.frames_to_tc(3600, 60000 / 1001, drop=True), "00:01:00;04")


class PureDetectionTests(unittest.TestCase):
    def test_flash_frames_need_longer_neighbours(self):
        # Shots: 0-10, 10-12 (2-frame flash), 12-30, 30-31 (1 frame), 31-40.
        self.assertEqual(qc.flash_frames([10, 12, 30, 31], 40), [(10, 2), (30, 1)])
        # A strobe run of one-frame shots is not a flash frame.
        self.assertEqual(qc.flash_frames([10, 11, 12, 13, 14], 40), [])
        # A short shot touching the range edges may be cut by the range itself.
        self.assertEqual(qc.flash_frames([2, 20], 40), [])

    def test_loudness_jumps_between_adjacent_windows(self):
        short = [(i / 10.0, -24.0 if i < 100 else -12.0) for i in range(200)]
        jumps = qc.loudness_jumps(short, 6.0)
        self.assertEqual(len(jumps), 1)
        self.assertAlmostEqual(jumps[0][1], 12.0)
        self.assertEqual(qc.loudness_jumps([(i / 10.0, -24.0) for i in range(200)], 6.0), [])
        silent_start = [(i / 10.0, -70.0 if i < 100 else -20.0) for i in range(200)]
        self.assertEqual(qc.loudness_jumps(silent_start, 6.0), [])

    def test_markers_red_errors_yellow_warnings_one_per_frame(self):
        findings = [{"severity": "warning", "kind": "click", "time_seconds": 1.0, "timeline_frame": 100, "message": "a"},
                    {"severity": "error", "kind": "flash_frame", "time_seconds": 1.0, "timeline_frame": 100,
                     "message": "b", "duration_frames": 2},
                    {"severity": "info", "kind": "static", "time_seconds": 2.0, "timeline_frame": 148, "message": "c"},
                    {"severity": "warning", "kind": "freeze", "time_seconds": 3.0, "timeline_frame": 172, "message": "d"}]
        marks = qc.marker_requests(findings)
        self.assertEqual([(m["frame"], m["color"], m["name"]) for m in marks],
                         [(100, "Red", "QC: flash frame"), (172, "Yellow", "QC: frozen picture")])
        self.assertEqual(marks[0]["duration"], 2)


# ----------------------------------------------------------- ffmpeg analysis


@unittest.skipUnless(FFMPEG, "ffmpeg is needed for the render check analysis tests")
class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.program = Path(cls.tmp.name) / "program.mp4"
        cls.clipped = Path(cls.tmp.name) / "clipped.mp4"
        make_program(cls.program)
        make_clipped(cls.clipped)
        cls.report = qc.analyze(cls.program, FFMPEG, fps=FPS, range_start_frame=TL_START,
                                timeline_start_frame=TL_START, start_timecode="01:00:00:00",
                                cut_frames=[TL_START + 48, TL_START + 96, TL_START + 192])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def found(self, kind, seconds, tolerance=0.15):
        return [f for f in self.report["findings"] if f["kind"] == kind and abs(f["time_seconds"] - seconds) <= tolerance]

    def test_black_gap(self):
        black = self.found("black", 2.0)
        self.assertEqual(len(black), 1)
        self.assertEqual(black[0]["severity"], "warning")
        self.assertEqual(black[0]["duration_frames"], 12)
        self.assertEqual((black[0]["timeline_frame"], black[0]["timecode"]), (TL_START + 48, "01:00:02:00"))

    def test_two_frame_flash(self):
        flash = self.found("flash_frame", 4.5)
        self.assertEqual(len(flash), 1)
        self.assertEqual(flash[0]["severity"], "error")
        self.assertEqual(flash[0]["timecode"], "01:00:04:12")
        self.assertEqual(flash[0]["message"], "2-frame flash at 01:00:04:12: a shot this short is usually a leftover frame. Check it.")
        self.assertEqual(len([f for f in self.report["findings"] if f["kind"] == "flash_frame"]), 1)

    def test_frozen_hold(self):
        freeze = self.found("freeze", 8.08)
        self.assertEqual(len(freeze), 1)
        self.assertAlmostEqual(freeze[0]["duration_seconds"], 1.0, delta=0.1)

    def test_digital_silence_dropout(self):
        dropout = self.found("dropout", 5.0)
        self.assertEqual(len(dropout), 1)
        self.assertAlmostEqual(dropout[0]["duration_seconds"], 0.2, delta=0.05)

    def test_click_only_at_the_bad_cut(self):
        self.assertEqual(len(self.found("click", 2.0, 0.01)), 1)
        self.assertEqual(self.found("click", 4.0, 0.5), [])
        self.assertEqual(self.found("click", 8.0, 0.5), [])
        self.assertEqual(self.report["summary"]["audio_cuts_checked"], 3)

    def test_noise_floor_jump(self):
        jump = self.found("noise_floor_jump", 8.0, 0.01)
        self.assertEqual(len(jump), 1)
        self.assertIn("up by 20 dB", jump[0]["message"])
        self.assertEqual(self.found("noise_floor_jump", 2.0, 0.01) + self.found("noise_floor_jump", 4.0, 0.01), [])

    def test_loud_section_and_loudness(self):
        loud = self.found("loud_section", 11.0, 0.5)
        self.assertEqual(len(loud), 1)
        self.assertAlmostEqual(loud[0]["duration_seconds"], 2.0, delta=0.5)
        self.assertTrue([f for f in self.report["findings"] if f["kind"] == "loudness_jump"])
        loudness = self.report["loudness"]
        self.assertTrue(-30 < loudness["integrated_lufs"] < -5)
        self.assertIsNotNone(loudness["lra_lu"])
        self.assertTrue(-10 < loudness["true_peak_dbtp"] < 0)
        self.assertEqual([f for f in self.report["findings"] if f["kind"] == "clipping"], [])

    def test_report_shape(self):
        times = [f["time_seconds"] for f in self.report["findings"]]
        self.assertEqual(times, sorted(times))
        for finding in self.report["findings"]:
            self.assertIn(finding["severity"], ("error", "warning", "info"))
            self.assertEqual(finding["timeline_frame"], TL_START + int(math.floor(finding["time_seconds"] * FPS + 1e-6)))
            self.assertTrue(finding["timecode"].startswith("01:00:"))
            self.assertNotIn("{tc}", finding["message"])
        summary = self.report["summary"]
        self.assertEqual(summary["errors"] + summary["warnings"] + summary["info"], len(self.report["findings"]))
        self.assertTrue(any("low-resolution" in line for line in self.report["limits"]))
        self.assertTrue(any("captions" in line for line in self.report["limits"]))

    def test_clipping(self):
        report = qc.analyze(self.clipped, FFMPEG, fps=FPS, range_start_frame=0, timeline_start_frame=0)
        clipping = [f for f in report["findings"] if f["kind"] == "clipping"]
        self.assertEqual(len(clipping), 1)
        self.assertEqual(clipping[0]["severity"], "error")
        self.assertAlmostEqual(clipping[0]["time_seconds"], 1.0, delta=0.15)

    def test_run_check_end_to_end_against_fakes(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        project = RenderProject(program_clips(), render_source=self.program)
        ops = ResolveOperations(lambda: Resolve(project), runtime_home=str(home))
        calls = []

        def call(operation, params, timeout):
            calls.append(operation)
            return ops.dispatch(operation, params)

        result = qc.run_check(call, FFMPEG, home / "renders" / "check", mark=True, sleep=lambda _: None)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(calls[0], "render_preview")
        self.assertIn("render_status", calls)
        self.assertEqual(calls[-2:], ["add_markers", "render_preview_cleanup"])
        self.assertTrue(result["cleanup"]["removed"] and result["cleanup"]["file_deleted"])
        self.assertEqual(project.jobs, [])
        self.assertEqual(list((home / "renders" / "check").iterdir()), [])
        self.assertTrue(result["render"]["settings_restored_before_render"])
        self.assertTrue(result["settings_restored"])
        self.assertEqual(project.presets, ["H.264 Master", "YouTube - 1080p"])
        self.assertEqual(project.GetCurrentRenderFormatAndCodec(), {"format": "mov", "codec": "ProRes422"})
        self.assertEqual(project.current.GetMarkInOut(), {"video": {}, "audio": {}})
        markers = project.current.GetMarkers()
        names = {value["name"] for value in markers.values()}
        self.assertIn("QC: flash frame", names)
        self.assertEqual({value["color"] for value in markers.values()}, {"Red", "Yellow"})
        self.assertIn(4 * FPS + 12, markers)  # marker frames are timeline-relative in Resolve
        self.assertEqual(markers[4 * FPS + 12]["color"], "Red")
        # A second marked run adds no duplicate markers.
        again = qc.run_check(call, FFMPEG, home / "renders" / "check", mark=True, sleep=lambda _: None)
        self.assertEqual(again["markers"]["added"], 0)
        self.assertEqual(len(project.current.GetMarkers()), len(markers))

    def test_run_check_cleans_up_after_a_failed_or_slow_render(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        project = RenderProject(program_clips())  # never completes on its own
        ops = ResolveOperations(lambda: Resolve(project), runtime_home=str(home))
        ticks = iter(range(0, 10000, 30))
        result = qc.run_check(lambda op, params, timeout: ops.dispatch(op, params), FFMPEG, home / "renders" / "check",
                              max_wait_seconds=100, sleep=lambda _: None, clock=lambda: next(ticks))
        self.assertFalse(result["ok"])
        self.assertIn("did not finish", result["error"])
        self.assertTrue(result["cleanup"]["removed"])
        self.assertTrue(result["cleanup"]["stopped_render"])
        self.assertEqual(project.jobs, [])

        project.StartRendering = lambda job_id: project.status.update({job_id: {"JobStatus": "Failed", "Error": "disk full"}}) or True
        failed = qc.run_check(lambda op, params, timeout: ops.dispatch(op, params), FFMPEG, home / "renders" / "check",
                              sleep=lambda _: None)
        self.assertFalse(failed["ok"])
        self.assertIn("Failed: disk full", failed["error"])
        self.assertTrue(failed["cleanup"]["removed"])
        self.assertEqual(project.jobs, [])
        self.assertTrue(result["settings_restored"] and failed["settings_restored"])
        self.assertEqual(project.presets, ["H.264 Master", "YouTube - 1080p"])
        self.assertEqual(project.current.GetMarkInOut(), {"video": {}, "audio": {}})


class PatienceTests(unittest.TestCase):
    def test_retries_only_calls_that_never_dispatched(self):
        attempts = []

        def busy_then_ok(operation, params, timeout):
            attempts.append(operation)
            if len(attempts) < 3:
                raise RuntimeError("Timed out before Lua dispatch; no new operation started.")
            return {"done": True}

        self.assertEqual(qc._patient(busy_then_ok, "render_preview_cleanup", {}, 1, lambda _: None, lambda: 0), {"done": True})
        self.assertEqual(len(attempts), 3)

        def unknown(operation, params, timeout):
            attempts.append(operation)
            raise RuntimeError("Request record: x. No automatic replay or Python fallback was sent.")

        attempts.clear()
        with self.assertRaisesRegex(RuntimeError, "No automatic replay"):
            qc._patient(unknown, "render_preview", {}, 1, lambda _: None, lambda: 0)
        self.assertEqual(len(attempts), 1)


if __name__ == "__main__":
    unittest.main()
