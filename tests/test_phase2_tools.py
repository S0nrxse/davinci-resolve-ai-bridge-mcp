"""Transitions as items, edit_points, batch setters, diff_since, insert_clip, create_timeline and the Agent Work bin.

Fakes only; behaviour follows the live probes on Resolve 21.2 Free where they exist.
"""

import unittest

from bridge.operations import OperationError, ResolveOperations
from fakes import Clip, Media, Project, Resolve, Timeline
from test_in_place_edits import OffsetClip, pair


class NoMediaClip(Clip):
    """A transition item as Resolve 21.2 lists it: no media pool item and no offsets (found live)."""

    def GetMediaPoolItem(self):
        return None

    def GetLeftOffset(self):
        return None


def with_transition(project_clips, start, end, name="Cross Dissolve", kind="video"):
    return project_clips + [NoMediaClip(start, end, None, 0, name=name, kind=kind, track=1)]


class TransitionItemTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b, self.c = pair(0, 100, 10, "a"), pair(100, 200, 50, "b"), pair(200, 300, 0, "c")
        clips = [*self.a, *self.b, *self.c]
        clips = with_transition(clips, 88, 112)
        clips = with_transition(clips, 194, 206, name="Cross Fade 0 dB", kind="audio")
        self.project = Project(clips)
        self.op = ResolveOperations(lambda: Resolve(self.project))

    def test_labels_skip_transitions_and_overview_lists_them(self):
        out = self.op._op_timeline_overview({"detail": "full"})
        v1 = [c for c in out["clips"] if c["label"].startswith("V1.")]
        self.assertEqual([c["name"] for c in v1], ["a", "b", "c"])
        self.assertEqual([c["label"] for c in v1], ["V1.1", "V1.2", "V1.3"])
        self.assertEqual({(t["track"], t["start_frame"], t["end_frame"], t["name"]) for t in out["transitions"]},
                         {("V1", 88, 112, "Cross Dissolve"), ("A1", 194, 206, "Cross Fade 0 dB")})
        b = next(c for c in v1 if c["name"] == "b")
        self.assertEqual((b["transition_in"], b["transition_out"]), ("Cross Dissolve", None))
        audio_b = next(c for c in out["clips"] if c["label"] == "A1.2")
        self.assertEqual(audio_b["transition_out"], "Cross Fade 0 dB")
        self.assertIn("src_in", b)

    def test_compact_detail_is_small(self):
        out = self.op._op_timeline_overview({"detail": "compact"})
        self.assertEqual(set(out["clips"][0]), {"id", "label", "name", "start", "end"})

    def test_a_title_overlapping_one_clip_is_not_a_transition(self):
        project = Project([Clip(0, 100, Media("/x"), 0, name="bg", track=1),
                           NoMediaClip(50, 150, None, 0, name="Text+", track=1)])
        op = ResolveOperations(lambda: Resolve(project))
        self.assertEqual(len(op._op_timeline_overview({})["clips"]), 2)

    def test_lua_snapshot_names_filter_transitions(self):
        spans = [(0, 100, "a"), (88, 112, "Cross Dissolve"), (100, 200, "b")]
        self.assertEqual(ResolveOperations._transition_flags(spans), {1})


class EditPointTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b = pair(0, 100, 10, "a"), pair(100, 200, 50, "b")
        self.project = Project([*self.a, *self.b])
        self.op = ResolveOperations(lambda: Resolve(self.project))

    def test_in_place_trims_and_roll_in_one_call(self):
        out = self.op._op_edit_points({"ops": [{"op": "roll", "left_item_id": self.a[0].uid,
                                                "right_item_id": self.b[0].uid, "frames": 6},
                                               {"op": "trim", "item_id": self.b[0].uid, "tail": 10}]})
        self.assertTrue(out["verified"], out)
        self.assertEqual((self.a[0].end, self.b[0].start, self.b[0].source, self.b[0].end), (106, 106, 56, 190))
        self.assertEqual((self.a[1].end, self.b[1].start), (106, 106))  # linked audio followed
        self.assertEqual(len(self.project.timelines), 1)

    def test_roll_earlier_trims_the_left_clip_first(self):
        out = self.op._op_edit_points({"ops": [{"op": "roll", "left_item_id": self.a[0].uid,
                                                "right_item_id": self.b[0].uid, "frames": -8}]})
        self.assertTrue(out["verified"], out)
        self.assertEqual((self.a[0].end, self.b[0].start, self.b[0].source), (92, 92, 42))
        self.assertIn("unverified_call", out["applied"][0]["steps"][1])

    def test_slip_in_place_is_refused_honestly(self):
        with self.assertRaisesRegex(OperationError, "SetPosition"):
            self.op._op_edit_points({"ops": [{"op": "slip", "item_id": self.a[0].uid, "frames": 4}]})

    def test_stops_at_first_refusal_and_reports_applied(self):
        out = self.op._op_edit_points({"ops": [{"op": "trim", "item_id": self.a[0].uid, "head": 2},
                                               {"op": "trim", "item_id": self.a[0].uid, "tail": -5}]})
        self.assertEqual(len(out["applied"]), 1)
        self.assertIn("overlap", out["failed"]["error"])
        self.assertFalse(out["verified"])

    def test_new_timeline_takes_exactly_one_op(self):
        with self.assertRaisesRegex(OperationError, "ONE op"):
            self.op._op_edit_points({"mode": "new_timeline", "ops": [{"op": "trim", "item_id": "x"}] * 2})


class BatchSetterTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b = pair(0, 100, 0, "a"), pair(100, 200, 0, "b")
        self.project = Project([*self.a, *self.b])
        self.op = ResolveOperations(lambda: Resolve(self.project))

    def test_properties_by_range_with_read_back(self):
        out = self.op._op_set_clip_properties({"properties": {"AudioVolume": -3, "Opacity": 50},
                                               "range": {"start_frame": 0, "end_frame": 200}})
        self.assertTrue(out["verified"])
        self.assertEqual(self.a[1].props["AudioVolume"], -3)
        self.assertNotIn("AudioVolume", self.a[0].props)  # audio key skipped on video
        self.assertEqual(self.b[0].props["Opacity"], 50)
        with self.assertRaisesRegex(OperationError, "Unknown properties"):
            self.op._op_set_clip_properties({"properties": {"Opacty": 1}, "item_ids": [self.a[0].uid]})

    def test_grade_many_and_copy(self):
        for clip in (self.a[0], self.b[0]):
            clip.cdl = None
            clip.SetCDL = (lambda c: (lambda m: setattr(c, "cdl", dict(m)) or True))(clip)
            clip.GetCDL = (lambda c: (lambda node: c.cdl))(clip)
        out = self.op._op_set_clip_grade({"item_ids": [self.a[0].uid, self.b[0].uid], "slope": "1.1 1.0 0.9",
                                          "saturation": 0.8})
        self.assertEqual(out["applied"], 2)
        self.assertTrue(all(c["verified"] for c in out["clips"]))
        copied = self.op._op_copy_grade({"from_item_id": self.a[0].uid, "item_ids": [self.b[0].uid]})
        self.assertEqual(copied["copied_to"], 1)
        self.assertTrue(copied["checks"][0]["matches_node1_cdl"])

    def test_grade_without_saturation_still_applies(self):
        # Resolve 21.2 Free returns False from SetCDL unless every CDL key is present (found live).
        clip = self.a[0]
        clip.cdl = {"NodeIndex": 1, "Slope": "1 1 1", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": 0.7}

        def strict(m):
            if not {"Slope", "Offset", "Power", "Saturation"} <= set(m):
                return False
            clip.cdl = dict(m)
            return True
        clip.SetCDL = strict
        clip.GetCDL = lambda node: clip.cdl
        out = self.op._op_set_clip_grade({"item_id": clip.uid, "slope": "1.1 1.0 0.9"})
        self.assertTrue(out["applied"])
        self.assertTrue(out["verified"])
        self.assertAlmostEqual(float(clip.cdl["Saturation"]), 0.7)  # kept, not reset


class DiffSinceTests(unittest.TestCase):
    def test_changes_since_a_snapshot_are_structured(self):
        a, b = pair(0, 100, 0, "a"), pair(100, 200, 0, "b")
        project = Project([*a, *b])
        op = ResolveOperations(lambda: Resolve(project))
        snapshot = op._op_checkpoint_timeline({"label": "x"})["checkpoint"]["id"]
        b[0].SetRightOffset(b[0].GetRightOffset() + 10)
        b[1].props["AudioVolume"] = -6
        a[0].color = "Orange"
        out = op._op_diff_since({"snapshot_id": snapshot})
        kinds = {(c["change"], c.get("name")) for c in out["changes"]}
        self.assertIn(("trimmed", "b"), kinds)
        self.assertIn(("color", "a"), kinds)
        self.assertIn(("properties", "b"), kinds)
        state = op._op_diff_since({"snapshot_id": snapshot, "include_state": True})["state"]
        again = op._op_diff_since({"baseline": state})
        self.assertEqual(again["changes"], [])


class InsertAndCreateTests(unittest.TestCase):
    def test_insert_into_a_gap_and_refusals(self):
        a = pair(0, 100, 0, "a")
        media = Media("/tmp/new.mov")
        project = Project([*a])
        op = ResolveOperations(lambda: Resolve(project))
        op._find_media_items = lambda ids: [media]
        out = op._op_insert_clip({"media_id": "m", "src_in": 10, "src_out": 34, "at_frame": 100, "kind": "v"})
        self.assertTrue(out["verified"])
        self.assertEqual((out["placed"][0]["start"], out["placed"][0]["end"]), (100, 124))
        with self.assertRaisesRegex(OperationError, "overlap"):
            op._op_insert_clip({"media_id": "m", "src_in": 0, "src_out": 10, "at_frame": 95, "kind": "v"})
        with self.assertRaisesRegex(OperationError, "ripple"):
            op._op_insert_clip({"media_id": "m", "src_in": 0, "src_out": 10, "at_frame": 95, "ripple": True})

    def test_create_timeline_reads_back_fps_and_refuses_bad_strings(self):
        project = Project([])
        settings = {}

        class NewTimeline(Timeline):
            def SetSetting(self, key, value):
                if key == "timelineFrameRate" and value != "30":
                    return False  # what 21.2 Free did for 29.97
                settings[key] = value
                return True

            def GetSetting(self, key):
                return settings.get(key, "24" if key == "timelineFrameRate" else None)
        project.pool.CreateEmptyTimeline = lambda name: project.timelines.append(NewTimeline(project, name)) or project.timelines[-1]
        project.pool.GetRootFolder = lambda: None
        op = ResolveOperations(lambda: Resolve(project))
        ok = op.dispatch("create_timeline", {"name": "Edit 30", "fps": "30"})
        self.assertTrue(ok["settings"]["timelineFrameRate"]["verified"])
        bad = op.dispatch("create_timeline", {"name": "Edit 2997", "fps": "29.97"})
        self.assertFalse(bad["settings"]["timelineFrameRate"]["verified"])
        self.assertIn("only \"30\" was accepted", bad["note"])
        with self.assertRaisesRegex(OperationError, "fps must be one of"):
            op.dispatch("create_timeline", {"name": "x", "fps": "29.970"})


class AgentBinTests(unittest.TestCase):
    def test_bridge_made_timelines_go_to_agent_work_and_restore_the_view(self):
        class Folder:
            def __init__(self, name):
                self.name, self.subs = name, []

            def GetName(self):
                return self.name

            def GetSubFolderList(self):
                return self.subs
        project = Project([Clip(0, 100)])
        pool = project.pool
        root, footage = Folder("Master"), Folder("Footage")
        root.subs.append(footage)
        pool.current_folder, pool.moved = footage, []
        pool.GetRootFolder = lambda: root
        pool.GetCurrentFolder = lambda: pool.current_folder
        pool.SetCurrentFolder = lambda f: setattr(pool, "current_folder", f) or True

        def add(parent, name):
            folder = Folder(name)
            parent.subs.append(folder)
            pool.current_folder = folder
            return folder
        pool.AddSubFolder = add
        pool.MoveClips = lambda items, folder: pool.moved.append(folder.name) or True
        Timeline.GetMediaPoolItem = lambda self: "item-" + self.name
        self.addCleanup(lambda: delattr(Timeline, "GetMediaPoolItem"))
        op = ResolveOperations(lambda: Resolve(project))
        out = op.dispatch("preview_timeline", {})
        self.assertEqual(out["preview"]["bin"], "Agent Work")
        self.assertIs(pool.current_folder, footage)
        kept = op.dispatch("preview_timeline", {"bin": ""})
        self.assertIsNone(kept["preview"]["bin"])
        self.assertEqual(pool.moved, ["Agent Work"])


if __name__ == "__main__":
    unittest.main()
