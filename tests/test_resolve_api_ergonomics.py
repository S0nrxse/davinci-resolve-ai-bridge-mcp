"""resolve_api output shaping, for_each, id targets and getattr. Plain Python fakes, no Resolve."""
import json
import unittest

import test_project_api as base
from bridge import operations
from bridge.operations import OperationError
from fakes import Clip


class GradedClip(Clip):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cdl = None

    def SetCDL(self, cdl):
        self.cdl = cdl
        return True


class ConstResolve(base.FullResolve):
    EXPORT_FCPXML_1_10 = 7
    SOME_TABLE = [1, 2]


class ConstProject(base.FullProject):
    def ListRows(self):
        return [{"name": "row%d" % i} for i in range(5)]

    def LuaRows(self):
        return {1: "a", 2: "b", 3: "c"}


def ops_for(project):
    operations._TIMELINE_NAME_CACHE.clear()
    return operations.ResolveOperations(lambda: ConstResolve(project))


def clips(count):
    return [GradedClip(i * 10, i * 10 + 10, name="c%d" % i) for i in range(count)]


def run(project, steps, **params):
    return ops_for(project).dispatch("resolve_api", dict(params, steps=steps))


LIST_TRACK = {"on": "current_timeline", "call": "GetItemListInTrack", "args": ["video", 1]}


class OutputShapeTest(unittest.TestCase):
    def test_long_lists_are_compact_by_default(self):
        project = ConstProject(clips(30))
        result = run(project, [LIST_TRACK, {"on": "$0[25]", "call": "GetName"}])
        listed = result["steps"][0]["result"]
        self.assertEqual(listed["count"], 30)
        self.assertTrue(listed["truncated"])
        self.assertEqual(len(listed["items"]), operations.RESOLVE_API_LIST_CAP)
        self.assertEqual(listed["items"][0], {"name": "c0", "id": project.current.clips[0].uid})
        self.assertEqual(result["steps"][1]["result"], "c25")  # References still see the whole list.
        self.assertIn("full_lists", result["lists"])

    def test_full_lists_opts_out(self):
        result = run(ConstProject(clips(30)), [LIST_TRACK], full_lists=True)
        self.assertEqual(len(result["steps"][0]["result"]), 30)
        self.assertNotIn("lists", result)

    def test_short_lists_are_unchanged(self):
        result = run(ConstProject(clips(3)), [LIST_TRACK])
        self.assertEqual([item["name"] for item in result["steps"][0]["result"]], ["c0", "c1", "c2"])
        self.assertNotIn("lists", result)

    def test_return_steps_shrinks_other_steps(self):
        project = ConstProject(clips(30))
        steps = [LIST_TRACK, {"on": "$0[1]", "call": "GetName"}, {"call": "SetSetting", "args": ["x", "1"]}]
        full = run(project, steps, full_lists=True)
        result = run(project, steps, return_steps=[1], full_lists=True)
        self.assertLess(len(json.dumps(result)), len(json.dumps(full)) / 3)
        self.assertEqual(result["steps"][0], {"step": 0, "ok": True, "summary": "GetItemListInTrack -> list of 30"})
        self.assertEqual(result["steps"][1]["result"], "c1")
        self.assertEqual(result["steps"][2], {"step": 2, "ok": True, "summary": "SetSetting -> true"})

    def test_quiet_returns_only_the_last_result(self):
        project = ConstProject(clips(30))
        result = run(project, [LIST_TRACK, {"on": "$0[2]", "call": "GetName"}], quiet=True)
        self.assertEqual(result, {"ok": True, "steps_run": 2, "result": "c2", "note": result["note"]})

    def test_quiet_with_return_steps(self):
        result = run(ConstProject(clips(3)), [LIST_TRACK, {"on": "$0[2]", "call": "GetName"}],
                     quiet=True, return_steps=[0])
        self.assertEqual(result["result"], "c2")
        self.assertEqual([entry["step"] for entry in result["steps"]], [0])

    def test_return_steps_validated(self):
        for bad in ([5], "0", [True], [-1]):
            with self.assertRaisesRegex(OperationError, "return_steps"):
                run(ConstProject(), [{"call": "GetName"}], return_steps=bad)

    def test_failure_report_uses_brief_entries_in_quiet_mode(self):
        project = ConstProject(clips(30))
        with self.assertRaises(OperationError) as caught:
            run(project, [LIST_TRACK, {"on": "project", "call": "NoSuchMethod"}], quiet=True)
        self.assertIn("failed_step 1 (project.NoSuchMethod)", str(caught.exception))
        self.assertIn("list of 30", str(caught.exception))
        self.assertNotIn("c29", str(caught.exception))


class ForEachTest(unittest.TestCase):
    def test_range_item_and_index(self):
        project = ConstProject(clips(6))
        result = run(project, [
            LIST_TRACK,
            {"for_each": "$0[*]", "range": [1, 4], "call": "SetCDL",
             "args": [{"NodeIndex": "$index", "Slope": "1 1 1", "Name": "$item"}]},
        ])
        touched = [clip.cdl["NodeIndex"] if clip.cdl else None for clip in project.current.clips]
        self.assertEqual(touched, [None, 1, 2, 3, None, None])  # stop is exclusive, $index is the list index
        self.assertIs(project.current.clips[2].cdl["Name"], project.current.clips[2])
        stats = result["steps"][1]["result"]
        self.assertEqual((stats["count"], stats["ok_count"], stats["first_index"], stats["last_index"]), (3, 3, 1, 3))
        self.assertEqual(stats["failed"], [])
        self.assertEqual(stats["results"], {"all": True})

    def test_item_paths_and_fixed_target(self):
        project = ConstProject()
        run(project, [{"call": "ListRows"},
                      {"for_each": "$0[*]", "range": [-2], "on": "project", "call": "SetSetting",
                       "args": ["$item.name", "$index"]}])
        self.assertEqual(project.settings, {"row3": 3, "row4": 4})

    def test_lua_array_and_later_reference(self):
        project = ConstProject()
        result = run(project, [{"call": "LuaRows"},
                               {"for_each": "$0", "on": "project", "call": "SetSetting", "args": ["$item", "v"]},
                               {"on": "project", "call": "SetSetting", "args": ["done", "$1[2]"]}])
        self.assertEqual(project.settings, {"a": "v", "b": "v", "c": "v", "done": True})
        self.assertTrue(result["ok"])

    def test_stops_at_first_failure_and_reports_indices(self):
        project = ConstProject(clips(4))
        project.current.clips[2].SetCDL = None  # not callable
        with self.assertRaises(OperationError) as caught:
            run(project, [LIST_TRACK, {"for_each": "$0[*]", "call": "SetCDL", "args": [{}]}])
        message = str(caught.exception)
        self.assertIn("for_each stopped at index 2", message)
        self.assertIn("indices 0-1 ran first", message)
        self.assertEqual([clip.cdl for clip in project.current.clips], [{}, {}, None, None])

    def test_continue_on_error(self):
        project = ConstProject(clips(4))
        project.current.clips[2].SetCDL = None
        result = run(project, [LIST_TRACK, {"for_each": "$0[*]", "call": "SetCDL", "args": [{}],
                                            "continue_on_error": True}])
        stats = result["steps"][1]["result"]
        self.assertEqual((stats["count"], stats["ok_count"]), (4, 3))
        self.assertEqual([entry["index"] for entry in stats["failed"]], [2])
        self.assertEqual(project.current.clips[3].cdl, {})

    def test_reports_false_results(self):
        project = ConstProject(clips(3))
        project.current.clips[1].SetCDL = lambda cdl: False
        result = run(project, [LIST_TRACK, {"for_each": "$0[*]", "call": "SetCDL", "args": [{}]}],
                     return_steps=[])
        self.assertIn("1 returned False/None", result["steps"][1]["summary"])

    def test_destructive_refused_inside_for_each(self):
        project = ConstProject()
        steps = [{"call": "SetSetting", "args": ["x", "1"]},
                 {"call": "GetTimelineByIndex", "args": [1]},
                 {"for_each": "$1", "on": "project", "call": "DeleteTimelines", "args": [["$item"]]}]
        with self.assertRaisesRegex(OperationError, "allow_destructive"):
            run(project, steps)
        self.assertEqual(project.settings, {})
        self.assertEqual(project.deleted, [])

    def test_call_cap(self):
        project = ConstProject()
        project.Many = lambda: list(range(operations.RESOLVE_API_MAX_CALLS + 1))
        with self.assertRaisesRegex(OperationError, "call limit|calls"):
            run(project, [{"call": "Many"}, {"for_each": "$0[*]", "on": "project", "call": "SetSetting",
                                             "args": ["$item", 1]}])
        self.assertEqual(project.settings, {})
        result = run(project, [{"call": "Many"}, {"for_each": "$0[*]", "range": [0, 499], "on": "project",
                                                  "call": "SetSetting", "args": ["$item", 1]}])
        self.assertEqual(result["steps"][1]["result"]["count"], 499)  # 1 + 499 = 500 calls

    def test_counts_as_one_step(self):
        project = ConstProject()
        steps = [{"call": "ListRows"}] + [{"call": "GetName"}] * 48 + [
            {"for_each": "$0[*]", "on": "project", "call": "SetSetting", "args": ["$item.name", 1]}]
        self.assertTrue(run(project, steps, quiet=True)["ok"])

    def test_bad_for_each_specs(self):
        for step in ({"for_each": "$1[*]", "call": "GetName"},  # not an earlier step
                     {"for_each": "items", "call": "GetName"},
                     {"for_each": "$0", "get": "project"},
                     {"for_each": "$0", "call": "GetName", "range": [1, "x"]},
                     {"for_each": "$0", "call": "GetName", "range": "1:3"}):
            with self.assertRaises(OperationError):
                run(ConstProject(), [{"call": "ListRows"}, step])

    def test_item_refs_outside_for_each_stay_literal(self):
        project = ConstProject()
        run(project, [{"call": "SetSetting", "args": ["$item", "$index"]}])
        self.assertEqual(project.settings, {"$item": "$index"})
        with self.assertRaises(OperationError):
            run(project, [{"on": "$item", "call": "GetName"}])


class IdTargetTest(unittest.TestCase):
    def media_project(self):
        a, b, c = base.Item("a"), base.Item("b"), base.Item("c")
        root = base.Folder("Master", [a], [base.Folder("Interviews", [b], [base.Folder("Day 2", [c])])])
        return ConstProject(clips(3), root)

    def test_media_found_in_nested_bin_and_usable_as_arg(self):
        project = self.media_project()
        result = run(project, [{"on": "media:id-c", "call": "GetName"},
                               {"get": "media:id-b"},
                               {"on": "media_pool", "call": "CreateTimelineFromClips", "args": ["Sel", ["$1"]]}])
        self.assertEqual(result["steps"][0]["result"], "c")
        self.assertEqual(project.timelines[-1].source_items, [project.root.subfolders[0].clips[0]])

    def test_media_missing_and_ambiguous(self):
        project = self.media_project()
        with self.assertRaisesRegex(OperationError, "media:nope was not found among the 3 clip"):
            run(project, [{"on": "media:nope", "call": "GetName"}])
        project.root.clips.append(base.Item("c"))
        with self.assertRaisesRegex(OperationError, "ambiguous"):
            run(project, [{"on": "media:id-c", "call": "GetName"}])

    def test_timeline_item_by_unique_id(self):
        project = self.media_project()
        target = project.current.clips[1]
        result = run(project, [{"on": "item:" + target.uid, "call": "SetClipColor", "args": ["Orange"]}])
        self.assertTrue(result["ok"])
        self.assertEqual(target.color, "Orange")
        with self.assertRaisesRegex(OperationError, "not found among the 3 item"):
            run(project, [{"on": "item:missing", "call": "GetName"}])

    def test_timeline_by_unique_id(self):
        project = self.media_project()
        alt = project.current.DuplicateTimeline("Alt")
        result = run(project, [{"on": "timeline:" + alt.uid, "call": "GetName"}])
        self.assertEqual(result["steps"][0]["result"], "Alt")
        with self.assertRaisesRegex(OperationError, "list_timelines"):
            run(project, [{"on": "timeline:missing", "call": "GetName"}])

    def test_id_target_inside_for_each(self):
        project = self.media_project()
        alt = project.current.DuplicateTimeline("Alt")
        alt.SetName = lambda name: setattr(alt, "name", name) or True
        run(project, [{"call": "ListRows"},
                      {"for_each": "$0[*]", "range": [4], "on": "timeline:" + alt.uid, "call": "SetName",
                       "args": ["$item.name"]}])
        self.assertEqual(alt.name, "row4")

    def test_empty_id_rejected_before_running(self):
        project = ConstProject()
        with self.assertRaisesRegex(OperationError, "Nothing ran"):
            run(project, [{"call": "SetSetting", "args": ["x", 1]}, {"on": "media:", "call": "GetName"}])
        self.assertEqual(project.settings, {})


class GetattrTest(unittest.TestCase):
    def test_scalar_constant(self):
        result = run(ConstProject(), [{"on": "resolve", "getattr": "EXPORT_FCPXML_1_10"}])
        self.assertEqual(result["steps"][0], {"step": 0, "on": "resolve", "getattr": "EXPORT_FCPXML_1_10",
                                              "result": 7})

    def test_constant_feeds_a_later_call(self):
        project = ConstProject()
        run(project, [{"on": "resolve", "getattr": "EXPORT_FCPXML_1_10"},
                      {"call": "SetSetting", "args": ["format", "$0"]}])
        self.assertEqual(project.settings, {"format": 7})

    def test_refuses_methods_and_non_scalars(self):
        with self.assertRaisesRegex(OperationError, 'is a method; use {"call": "GetProductName"}'):
            run(ConstProject(), [{"on": "resolve", "getattr": "GetProductName"}])
        with self.assertRaisesRegex(OperationError, "only strings, numbers"):
            run(ConstProject(), [{"on": "resolve", "getattr": "SOME_TABLE"}])
        with self.assertRaisesRegex(OperationError, "no attribute"):
            run(ConstProject(), [{"on": "resolve", "getattr": "NOT_THERE"}])

    def test_rejects_bad_names_and_mixed_kinds(self):
        for step in ({"on": "resolve", "getattr": "__class__"}, {"on": "resolve", "getattr": "a.b"},
                     {"on": "resolve", "getattr": "X", "call": "GetName"}, {"on": "resolve"}):
            with self.assertRaisesRegex(OperationError, "Nothing ran"):
                run(ConstProject(), [step])


if __name__ == "__main__":
    unittest.main()
