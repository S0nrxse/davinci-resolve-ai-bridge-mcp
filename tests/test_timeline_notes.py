"""Timeline notes: classification, frames, timecode, clips under notes, resolving and open_notes. Fakes only."""
import ast
import json
import unittest
from pathlib import Path

from bridge import notes
from bridge.operations import OperationError, QUEUE_READ_ONLY, ResolveOperations
from fakes import Clip, Project, Resolve, Timeline


class NotesTimeline(Timeline):
    """Fake timeline with drop-frame setting, UpdateMarkerCustomData and switchable AddMarker failures."""
    drop = False
    refuse_colors = ()
    refuse_all = False
    allow_update = True

    def GetSetting(self, key):
        if key == "timelineDropFrameTimecode":
            return "1" if self.drop else "0"
        return super().GetSetting(key)

    def AddMarker(self, frame, color, name, note, duration, customData=""):
        if self.refuse_all or color in self.refuse_colors:
            return False
        return super().AddMarker(frame, color, name, note, duration, customData)

    def UpdateMarkerCustomData(self, frame, data):
        if not self.allow_update or frame not in self.markers:
            return False
        self.markers[frame]["customData"] = data
        return True


def marker(color="Blue", name="", note="", duration=1, custom=""):
    return dict(color=color, name=name, note=note, duration=duration, customData=custom)


PAUSE = marker("Purple", "Pause 1.2s", "Would be removed by remove_pauses(apply=True)", 29)


class ClassificationTests(unittest.TestCase):
    def test_user_markers_are_notes(self):
        self.assertTrue(notes.is_note(marker(name="punchier")))
        self.assertTrue(notes.is_note(marker(note="cut this um")))
        self.assertTrue(notes.is_note(marker("Red", "Beat", "make this hit harder")))  # user note beats legacy name

    def test_empty_default_marker_is_not_a_note(self):
        self.assertFalse(notes.is_note(marker()))
        self.assertFalse(notes.is_note(marker(name="  ", custom="user data")))

    def test_legacy_untagged_bridge_markers_are_excluded(self):
        for m in (PAUSE, marker("Purple", "Pause 0.8s"), marker("Purple", "Dead air 7.5s", "loading/menu", 180),
                  marker("Cyan", "Shot", "cam.mov (score 0.41)"), marker("Green", "Beat"), marker("Red", "Bar"),
                  marker("Red", "QC: black frames", "Black for 12 frames"), marker("Yellow", "QC: clipping", "x"),
                  marker("Yellow", "Review silence", "Source audio appears silent; review before cutting."),
                  marker("Pink", "Review cut: um"), marker(name="AI marker", custom="resolve-ai-bridge")):
            self.assertTrue(notes.is_bridge_marker(m), m)
            self.assertFalse(notes.is_note(m), m)

    def test_tagged_and_scheme_markers_are_excluded(self):
        self.assertFalse(notes.is_note(marker(name="Chapter 1", custom='{"aibridge":true}')))
        self.assertFalse(notes.is_note(marker(name="x", custom=json.dumps({"bridge": "silence-review-v1", "id": "a"}))))

    def test_resolved_note_is_still_a_note(self):
        done = notes.resolved_marker(marker(name="zoom", note="zoom in here"), "done", "Punched in 120%.")
        self.assertTrue(notes.is_note(done))
        self.assertEqual(notes.note_state(done)["status"], "done")

    def test_bridge_tag_keeps_caller_data(self):
        self.assertEqual(json.loads(notes.bridge_tag("")), {"aibridge": True})
        self.assertEqual(json.loads(notes.bridge_tag(None)), {"aibridge": True})
        self.assertEqual(json.loads(notes.bridge_tag('{"k": 1}')), {"k": 1, "aibridge": True})
        self.assertEqual(json.loads(notes.bridge_tag("plain")), {"aibridge": True, "data": "plain"})
        for text in ("", "plain", '{"k": 1}'):
            self.assertTrue(notes.is_bridge_marker(marker(name="x", custom=notes.bridge_tag(text))))


class FrameAndTimecodeTests(unittest.TestCase):
    def test_relative_keys_become_absolute_frames(self):
        found = notes.collect({"48": marker(name="b"), 0.0: marker(name="a"), 24.0: PAUSE}, 86400, 24)
        self.assertEqual([n["frame"] for n in found], [86400, 86448])
        self.assertEqual(found[0]["timecode"], "01:00:00:00")
        self.assertEqual(found[1]["timecode"], "01:00:02:00")

    def test_drop_frame_timecode(self):
        self.assertEqual(notes.timecode(107892, 29.97, True), "01;00;00;00")
        self.assertEqual(notes.timecode(1800, 29.97, True), "00;01;00;02")
        self.assertEqual(notes.timecode(17982, 29.97, True), "00;10;00;00")
        self.assertEqual(notes.timecode(1800, 29.97, False), "00:01:00:00")
        self.assertEqual(notes.timecode(3600, 59.94, True), "00;01;00;04")

    def test_summary_counts_and_filter(self):
        found = notes.collect({0: marker(name="a"), 10: notes.resolved_marker(marker(name="b"), "done"),
                               20: notes.resolved_marker(marker(name="c"), "skipped")}, 0, 24)
        out = notes.summary(found)
        self.assertEqual((out["open"], out["done"], out["skipped"]), (1, 1, 1))
        self.assertEqual([n["name"] for n in out["notes"]], ["a"])
        self.assertEqual(len(notes.summary(found, include_done=True)["notes"]), 3)


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        clips = [Clip(1000, 1100, name="A-roll", track=1), Clip(1100, 1300, name="B-roll", track=1),
                 Clip(1050, 1150, name="Title", track=2), Clip(1000, 1300, name="VO", kind="audio", track=1)]
        self.timeline = NotesTimeline(self.project, clips=clips, start=1000)
        self.project.current = self.timeline
        self.project.timelines = [self.timeline]
        self.op = ResolveOperations(lambda: Resolve(self.project), transport="test")

    def test_timeline_notes_excludes_bridge_markers_and_finds_clips(self):
        self.timeline.markers.update({60.0: marker(name="zoom in here"), 10.0: PAUSE, 20.0: marker(),
                                      150.0: marker("Yellow", "", "make this punchier", 24)})
        out = self.op._op_timeline_notes({})
        self.assertEqual([n["frame"] for n in out["notes"]], [1060, 1150])
        self.assertEqual((out["open"], out["done"]), (2, 0))
        first, second = out["notes"]
        self.assertEqual([(c["name"], c["track"]) for c in first["clips"]],
                         [("A-roll", "V1"), ("Title", "V2"), ("VO", "A1")])
        self.assertEqual(first["clips"][0]["item_id"], self.timeline.clips[0].uid)
        self.assertEqual([c["name"] for c in second["clips"]], ["B-roll", "VO"])
        self.assertEqual((second["duration"], second["end_frame"], second["note"]), (24, 1174, "make this punchier"))

    def test_timeline_notes_is_read_only(self):
        self.assertIn("timeline_notes", QUEUE_READ_ONLY)
        self.assertNotIn("resolve_timeline_note", QUEUE_READ_ONLY)
        self.timeline.markers[5.0] = marker(name="n")
        before = json.dumps(self.timeline.markers, sort_keys=True)
        self.op._op_timeline_notes({})
        self.assertEqual(json.dumps(self.timeline.markers, sort_keys=True), before)

    def test_drop_frame_timeline(self):
        self.timeline.fps, self.timeline.start, self.timeline.drop = 29.97, 107892, True
        self.timeline.markers[1800.0] = marker(name="n")
        note = self.op._op_timeline_notes({})["notes"][0]
        self.assertEqual((note["frame"], note["timecode"]), (109692, "01;01;00;02"))

    def test_resolve_deletes_and_readds_with_reply(self):
        self.timeline.markers[60.0] = marker("Yellow", "zoom", "zoom in here", 12, "user-stuff")
        out = self.op._op_resolve_timeline_note({"frame": 1060, "status": "done", "reply": "Punched in to 120%."})
        self.assertTrue(out["verified"])
        self.assertEqual(out["method"], "re-added")
        m = self.timeline.markers[60.0]
        self.assertEqual((m["color"], m["name"], m["duration"]), ("Green", "zoom", 12))
        self.assertEqual(m["note"], "zoom in here\n— AI: Punched in to 120%.")
        data = json.loads(m["customData"])
        self.assertEqual(data["aibridge_note"]["status"], "done")
        self.assertEqual(data["user_custom_data"], "user-stuff")
        listed = self.op._op_timeline_notes({"include_done": True})
        self.assertEqual((listed["open"], listed["done"]), (0, 1))
        self.assertEqual(listed["notes"][0]["note"], "zoom in here")
        self.assertEqual(listed["notes"][0]["reply"], "Punched in to 120%.")
        # Resolving again replaces the reply rather than stacking it; reopening restores the original.
        self.op._op_resolve_timeline_note({"frame": 1060, "status": "skipped", "reply": "Needs Studio."})
        self.assertEqual(self.timeline.markers[60.0]["color"], "Cream")
        self.assertEqual(self.timeline.markers[60.0]["note"], "zoom in here\n— AI: Needs Studio.")
        self.op._op_resolve_timeline_note({"frame": 1060, "status": "open"})
        self.assertEqual(self.timeline.markers[60.0], marker("Yellow", "zoom", "zoom in here", 12, "user-stuff"))

    def test_skipped_falls_back_to_sand(self):
        self.timeline.refuse_colors = ("Cream",)
        self.timeline.markers[0.0] = marker(name="n")
        out = self.op._op_resolve_timeline_note({"frame": 1000, "status": "skipped"})
        self.assertEqual((out["color"], out["verified"]), ("Sand", True))

    def test_already_green_updates_custom_data_in_place(self):
        self.timeline.markers[0.0] = marker("Green", "n")
        out = self.op._op_resolve_timeline_note({"frame": 1000, "status": "done"})
        self.assertEqual(out["method"], "custom_data")
        self.assertTrue(out["verified"])

    def test_failed_readd_restores_original(self):
        original = marker("Blue", "punchier", "make this punchier", 30, "mine")
        self.timeline.markers[60.0] = dict(original)
        self.timeline.refuse_colors = ("Green",)
        with self.assertRaisesRegex(OperationError, "restored unchanged"):
            self.op._op_resolve_timeline_note({"frame": 1060, "status": "done", "reply": "x"})
        self.assertEqual(self.timeline.markers[60.0], original)

    def test_failed_readd_and_restore_reports_marker(self):
        self.timeline.markers[60.0] = marker("Blue", "punchier")
        self.timeline.refuse_all = True
        with self.assertRaisesRegex(OperationError, "could not restore.*punchier"):
            self.op._op_resolve_timeline_note({"frame": 1060, "status": "done"})

    def test_refuses_bridge_markers_and_missing_frames(self):
        self.timeline.markers[10.0] = dict(PAUSE)
        with self.assertRaisesRegex(OperationError, "not a timeline note"):
            self.op._op_resolve_timeline_note({"frame": 1010})
        self.assertEqual(self.timeline.markers[10.0], PAUSE)
        with self.assertRaisesRegex(OperationError, "No marker"):
            self.op._op_resolve_timeline_note({"frame": 5})
        with self.assertRaisesRegex(OperationError, "status"):
            self.op._op_resolve_timeline_note({"frame": 1010, "status": "maybe"})

    def test_current_context_counts_open_notes(self):
        self.timeline.markers.update({0.0: marker(name="a"), 10.0: dict(PAUSE), 20.0: marker(),
                                      30.0: notes.resolved_marker(marker(name="b"), "done")})
        self.assertEqual(self.op._op_current_context({})["open_notes"], 1)

    def test_current_context_omits_open_notes_on_error(self):
        def broken():
            raise RuntimeError("boom")
        self.timeline.GetMarkers = broken
        context = self.op._op_current_context({})
        self.assertNotIn("open_notes", context)
        self.assertEqual(context["timeline"], "Original")

    def test_bridge_made_markers_carry_the_tag(self):
        self.op._op_add_markers({"markers": [{"frame": 1010, "color": "Purple", "name": "Pause 1.0s"},
                                             {"frame": 1020, "name": "Chapter", "custom_data": '{"k": 1}'}]})
        self.op._op_add_marker({"frame": 30, "name": "AI marker"})
        for offset in (10.0, 20.0, 30):
            data = json.loads(self.timeline.markers[offset]["customData"])
            self.assertIs(data["aibridge"], True)
        self.assertEqual(json.loads(self.timeline.markers[20.0]["customData"])["k"], 1)
        self.assertEqual(self.op._op_timeline_notes({})["open"], 0)


class ServerToolTests(unittest.TestCase):
    def test_tools_are_registered(self):
        source = Path(__file__).resolve().parents[1].joinpath("bridge", "server.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        tools = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and any("mcp.tool" in ast.unparse(d) for d in node.decorator_list)}
        self.assertIn("timeline_notes", tools)
        self.assertEqual([a.arg for a in tools["resolve_timeline_note"].args.args], ["frame", "status", "reply"])


if __name__ == "__main__":
    unittest.main()
