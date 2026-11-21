"""Visual diff: change points between two timeline versions, the op on fakes and the grid spec.

Pure logic plus tests/fakes.py; no real Resolve, ffmpeg or render helper is used.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bridge import visual_diff as vd
from bridge.operations import OperationError, ResolveOperations
from fakes import Clip, Media, Project, Resolve, Timeline

FPS = 24.0


def clip(start, end, key="A", src=0, track=1, **extra):
    data = {"track": track, "start": start, "end": end, "key": key, "name": "%s.mov" % key, "item_id": "%s@%d" % (key, start),
            "enabled": True, "file_path": "/media/%s.mov" % key, "src_fps": FPS, "src_start": src,
            "src_end": src + (end - start) - 1, "left_offset": src, "still": False, "props": {}}
    data.update(extra)
    return data


def run(orig, rev, o_range=None, r_range=None, **kwargs):
    o_range = o_range or (0, max([c["end"] for c in orig] or [0]))
    r_range = r_range or (0, max([c["end"] for c in rev] or [0]))
    return vd.diff(orig, rev, FPS, FPS, o_range, r_range, **kwargs)


class PictureRunTests(unittest.TestCase):
    def test_topmost_enabled_media_wins_and_overlays_are_skipped(self):
        runs = vd.build_runs([clip(0, 100, "A"), clip(40, 60, "B", track=2),
                              clip(0, 100, "CAP", track=3, overlay=True), clip(70, 80, "C", track=2, enabled=False),
                              clip(80, 90, None, track=4)], FPS)
        self.assertEqual([(r["t0"], r["t1"], r["clip"]["key"]) for r in runs],
                         [(0, 40, "A"), (40, 60, "B"), (60, 100, "A")])

    def test_frame_source_maps_source_seconds_and_gaps(self):
        runs = vd.build_runs([clip(100, 200, "A", src=48)], FPS)
        info = vd.frame_source(runs, 124, tc=lambda f: "tc%d" % f)
        self.assertEqual((info["file_path"], info["timecode"], info["track"]), ("/media/A.mov", "tc124", "V1"))
        self.assertAlmostEqual(info["source_sec"], (48 + 24.5) / FPS, places=3)
        self.assertTrue(vd.frame_source(runs, 50)["gap"])

    def test_retimed_clip_maps_linearly(self):
        prepared = vd.prepare_clip(clip(0, 100, "A", src=0, src_end=199), FPS)
        self.assertTrue(prepared["retimed"])
        self.assertAlmostEqual(prepared["rate"], 2.0 / FPS)

    def test_parse_rate(self):
        self.assertAlmostEqual(vd.parse_rate("23.976"), 24 / 1.001)
        self.assertAlmostEqual(vd.parse_rate("29.97 DF"), 30 / 1.001)
        self.assertEqual(vd.parse_rate("25"), 25.0)
        self.assertEqual(vd.parse_rate(None, 30), 30.0)


class ChangePointTests(unittest.TestCase):
    def test_identical_timelines_have_no_changes(self):
        clips = [clip(0, 100, "A"), clip(100, 200, "B")]
        result = run(clips, [dict(c) for c in clips])
        self.assertEqual((result["changes"], result["rows"]), ([], []))

    def test_blade_split_is_not_a_change(self):
        result = run([clip(0, 200, "A")], [clip(0, 80, "A"), clip(80, 200, "A", src=80)])
        self.assertEqual(result["changes"], [])

    def test_removed_pause(self):
        result = run([clip(0, 240, "A")], [clip(0, 100, "A"), clip(100, 190, "A", src=150)])
        [change] = result["changes"]
        self.assertEqual((change["kind"], change["removed_frames"], change["original_frame"], change["revised_frame"]),
                         ("removed", 50, 100, 100))
        self.assertEqual(change["removed"], [[100, 150]])
        self.assertEqual(change["caption"], "Removed 2.1 s at f100")
        row = result["rows"][0]
        self.assertEqual((row["before"]["timeline_frame"], row["after"]["timeline_frame"]), (100, 100))
        self.assertAlmostEqual(row["before"]["source_sec"], 100.5 / FPS, places=3)
        self.assertAlmostEqual(row["after"]["source_sec"], 150.5 / FPS, places=3)
        self.assertEqual(result["summary"]["length_change"], "-2.1 s")

    def test_inserted_and_replaced(self):
        inserted = run([clip(0, 100, "A"), clip(100, 200, "B")],
                       [clip(0, 100, "A"), clip(100, 148, "N"), clip(148, 248, "B")])
        [change] = inserted["changes"]
        self.assertEqual((change["kind"], change["inserted_frames"], change["after_frame"]), ("inserted", 48, 100))
        self.assertEqual(change["caption"], "Inserted 2.0 s at f100")
        self.assertEqual(inserted["rows"][0]["before"]["clip_name"], "B.mov")  # what played there before
        replaced = run([clip(0, 100, "A"), clip(100, 160, "OLD"), clip(160, 260, "B")],
                       [clip(0, 100, "A"), clip(100, 130, "NEW"), clip(130, 230, "B")])
        [change] = replaced["changes"]
        self.assertEqual((change["kind"], change["removed_frames"], change["inserted_frames"]), ("replaced", 60, 30))
        self.assertEqual(change["caption"], "Replaced 2.5 s with 1.2 s at f100")
        self.assertEqual((replaced["rows"][0]["before"]["clip_name"], replaced["rows"][0]["after"]["clip_name"]),
                         ("OLD.mov", "NEW.mov"))

    def test_gap_closed_and_opened(self):
        closed = run([clip(0, 100, "A"), clip(130, 230, "B")], [clip(0, 100, "A"), clip(100, 200, "B")])
        [change] = closed["changes"]
        self.assertEqual((change["kind"], change["gap_delta_frames"]), ("gap_closed", -30))
        self.assertIn("Closed a 1.2 s gap", change["caption"])
        opened = run([clip(0, 100, "A"), clip(100, 200, "B")], [clip(0, 100, "A"), clip(112, 212, "B")])
        self.assertEqual(opened["changes"][0]["kind"], "gap_opened")
        self.assertEqual(opened["changes"][0]["caption"], "Opened a 12 frames gap at f100")

    def test_reordered_clips_are_moved_not_removed(self):
        result = run([clip(0, 100, "A"), clip(100, 140, "B"), clip(140, 240, "C")],
                     [clip(0, 100, "A"), clip(100, 200, "C"), clip(200, 240, "B")], merge_seconds=0)
        kinds = sorted(c["kind"] for c in result["changes"])
        self.assertEqual(kinds, ["moved", "moved_away"])
        self.assertEqual(result["summary"]["removed_frames"], 0)
        self.assertEqual(result["summary"]["inserted_frames"], 0)

    def test_property_change_on_the_same_picture(self):
        result = run([clip(0, 100, "A", props={"ZoomX": 1.0})], [clip(0, 100, "A", props={"ZoomX": 1.4})])
        [change] = result["changes"]
        self.assertEqual((change["kind"], change["properties_changed"]), ("properties", ["ZoomX"]))
        self.assertEqual(change["caption"], "Changed ZoomX at f0")

    def test_nearby_points_merge_and_rows_keep_the_biggest(self):
        orig = [clip(0, 1910, "A")]
        # Cuts of 5 and 6 frames ten frames apart, then 100 and 40 frames far away.
        rev = [clip(0, 100, "A"), clip(100, 110, "A", src=105), clip(110, 810, "A", src=121),
               clip(810, 1410, "A", src=921), clip(1410, 1759, "A", src=1561)]
        result = run(orig, rev, max_rows=2)
        self.assertEqual(len(result["changes"]), 3)
        merged = result["changes"][0]
        self.assertEqual((merged["kind"], merged["removed_frames"], merged["merged"]), ("removed", 11, 2))
        self.assertEqual([row["caption"] for row in result["rows"]], ["Removed 4.2 s at f821", "Removed 1.7 s at f1521"])
        self.assertEqual(result["summary"]["omitted"], 1)
        separate = run(orig, rev, merge_seconds=0)
        self.assertEqual(len(separate["changes"]), 4)

    def test_timecode_callbacks(self):
        result = run([clip(86400, 86640, "A")], [clip(86400, 86500, "A"), clip(86500, 86590, "A", src=150)],
                     o_range=(86400, 86640), r_range=(86400, 86590),
                     tc_original=lambda f: "O%d" % f, tc_revised=lambda f: "R%d" % f)
        change = result["changes"][0]
        self.assertEqual((change["original_timecode"], change["revised_timecode"]), ("O86500", "R86500"))
        self.assertEqual(result["rows"][0]["before"]["timecode"], "O86500")


class GridTests(unittest.TestCase):
    def test_grid_spec_and_render_without_frames(self):
        result = run([clip(0, 240, "A")], [clip(0, 100, "A"), clip(100, 190, "A", src=150)])
        seen = {}

        def helper(home, command, spec, timeout):
            seen.update(home=home, command=command, spec=spec)
            return {"ok": True, "output": spec["output"], "width": 830, "height": 300}

        with tempfile.TemporaryDirectory() as home:
            grid = vd.render_grid(result["rows"], home, "/nonexistent/ffmpeg", out_dir=home, run_helper=helper)
        self.assertEqual(seen["command"], "sheet")
        spec = seen["spec"]
        self.assertEqual((spec["columns"], len(spec["cells"])), (2, 2))
        self.assertEqual([c["number"] for c in spec["cells"]], ["1 BEFORE", "1 AFTER"])
        self.assertEqual(spec["cells"][0]["title"], "Removed 2.1 s at f100")
        self.assertIsNone(spec["cells"][0]["image"])  # source file missing: black cell, reported below
        self.assertEqual([f["reason"] for f in grid["frames"]], ["offline or undecodable"] * 2)

    def test_no_rows_and_no_ffmpeg_are_errors(self):
        with self.assertRaises(ValueError):
            vd.render_grid([], "/tmp", "/bin/ffmpeg")
        with self.assertRaises(RuntimeError):
            vd.render_grid([{"n": 1}], "/tmp", None)


def make_project():
    media = Media("/media/talk.mov")
    broll = Media("/media/broll.mov")
    original = [Clip(86400, 86640, media, 0, name="talk"), Clip(86640, 86700, broll, 0, name="broll")]
    project = Project(original)
    project.current.name = "Original"
    revised = project.current.DuplicateTimeline("Original — tight")
    revised.clips = [Clip(86400, 86500, media, 0, name="talk"), Clip(86500, 86590, media, 150, name="talk"),
                     Clip(86590, 86650, broll, 0, name="broll")]
    for item in revised.clips:
        item.props["ZoomX"] = 1.0
    project.timelines[0].start = project.timelines[1].start = 86400
    project.timelines[0].start_tc = project.timelines[1].start_tc = "01:00:00:00"
    switches = []
    original_set = project.SetCurrentTimeline
    project.SetCurrentTimeline = lambda t: switches.append(t) or original_set(t)
    return project, switches


class VisualDiffOpTests(unittest.TestCase):
    def test_reads_both_timelines_without_switching(self):
        project, switches = make_project()
        op = ResolveOperations(lambda: Resolve(project), transport="console")
        result = op._op_visual_diff({"original": "Original", "revised": "Original — tight", "max_rows": 4})
        self.assertEqual(switches, [])
        self.assertFalse(result["opened_timeline"])
        self.assertTrue(result["current_timeline_unchanged"])
        self.assertFalse(result["structurally_equal"])
        self.assertIn({"track": "video1", "status": "changed", "clips_before": 2, "clips_after": 3},
                      result["structural_changes"])
        [change] = result["changes"]
        self.assertEqual(change["caption"], "Removed 2.1 s at 01:00:04:04")
        self.assertEqual(change["revised_timecode"], "01:00:04:04")
        row = result["rows"][0]
        self.assertEqual((row["before"]["file_path"], row["after"]["file_path"]), ("/media/talk.mov", "/media/talk.mov"))
        self.assertIn("grades", result["limitations"])
        self.assertEqual(result["summary"]["length_change_frames"], -50)

    def test_same_or_unknown_timeline_is_refused(self):
        project, _ = make_project()
        op = ResolveOperations(lambda: Resolve(project), transport="console")
        with self.assertRaisesRegex(OperationError, "same timeline"):
            op._op_visual_diff({"original": "Original", "revised": "Original"})
        with self.assertRaisesRegex(OperationError, "missing or ambiguous"):
            op._op_visual_diff({"original": "Original", "revised": "Nope"})

    def test_equal_timelines_say_so(self):
        project, _ = make_project()
        copy = project.timelines[0].DuplicateTimeline("Copy")
        copy.start, copy.start_tc = 86400, "01:00:00:00"
        op = ResolveOperations(lambda: Resolve(project), transport="console")
        result = op._op_visual_diff({"original": "Original", "revised": "Copy"})
        self.assertEqual(result["rows"], [])
        self.assertIn("structurally equal", result["note"])

    def test_handler_is_registered(self):
        project, _ = make_project()
        self.assertIn("visual_diff", ResolveOperations(lambda: Resolve(project)).handlers())


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install requirements.txt to run MCP SDK checks")
class VisualDiffToolTests(unittest.TestCase):
    def test_tool_returns_json_then_image(self):
        source = '''
import json
from pathlib import Path
from unittest.mock import patch
from bridge import server, visual_diff
from mcp.types import ImageContent, TextContent
image = Path(server.client.HOME) / "grid.jpg"
image.write_bytes(b"jpeg-fixture")
payload = {"rows": [{"n": 1}], "changes": [], "summary": {}}
with patch.object(server.transport, "call", return_value=(payload, "lua")), \\
        patch.object(visual_diff, "render_grid", return_value={"path": str(image), "width": 1, "height": 1, "frames": []}):
    result = server.visual_diff("A", "B")
assert isinstance(result[0], TextContent) and isinstance(result[1], ImageContent), result
assert json.loads(result[0].text)["result"]["grid"]["width"] == 1
with patch.object(server.transport, "call", return_value=({"rows": [], "changes": []}, "lua")):
    only = server.visual_diff("A", "B")
assert len(only) == 1
with patch.object(server, "_result", return_value=json.dumps({"ok": True})) as called:
    server.styled_title("Hi", 10, 20, dry_run=True)
assert called.call_args[0][0] == "styled_title" and called.call_args[0][1]["dry_run"] is True
'''
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run([sys.executable, "-c", source], cwd=Path(__file__).resolve().parents[1],
                                    env=dict(os.environ, RESOLVE_AI_BRIDGE_HOME=home, PYTHONDONTWRITEBYTECODE="1"),
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
