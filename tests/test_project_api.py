"""project_overview, resolve_api and the project-scope fields of current_context."""
import unittest

from bridge import operations
from bridge.operations import OperationError, ResolveOperations
from fakes import Clip, Project, Resolve, Timeline


class Item:
    def __init__(self, name, kind="Video"):
        self.name = name
        self.props = {"Clip Name": name, "Type": kind, "Duration": "00:00:05:00", "File Path": "/m/%s.mov" % name}

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return "id-" + self.name

    def GetClipProperty(self, key=None):
        return self.props.get(key) if key else dict(self.props)


class Folder:
    def __init__(self, name, clips=(), subfolders=()):
        self.name, self.clips, self.subfolders = name, list(clips), list(subfolders)

    def GetName(self):
        return self.name

    def GetClipList(self):
        return list(self.clips)

    def GetSubFolderList(self):
        return list(self.subfolders)


class FullProject(Project):
    def __init__(self, clips=None, folder=None):
        super().__init__(clips)
        self.root = folder or Folder("Master")
        self.pool.GetRootFolder = lambda: self.root
        self.pool.CreateTimelineFromClips = self._create
        self.settings = {}
        self.deleted = []

    def _create(self, name, items):
        timeline = Timeline(self, name)
        timeline.source_items = items
        self.timelines.append(timeline)
        return timeline

    def GetRenderJobList(self):
        return [{"JobId": "a"}, {"JobId": "b"}]

    def SetSetting(self, key, value):
        self.settings[key] = value
        return True

    def DeleteTimelines(self, timelines):
        self.deleted.extend(timelines)
        return True


class FullResolve(Resolve):
    def GetCurrentPage(self):
        return "edit"

    def Fusion(self):
        return self

    def GetMediaStorage(self):
        return self


def ops_for(project):
    operations._TIMELINE_NAME_CACHE.clear()
    return ResolveOperations(lambda: FullResolve(project))


class ProjectOverviewTest(unittest.TestCase):
    def test_shape(self):
        bins = Folder("Master", [Item("a"), Item("b")], [Folder("Interviews", [Item("c")], [Folder("Day 2")])])
        project = FullProject([Clip(0, 100)], bins)
        project.current.DuplicateTimeline("Alt")
        result = ops_for(project).dispatch("project_overview")
        self.assertEqual(result["project"], "Test project")
        self.assertEqual(result["page"], "edit")
        self.assertEqual(result["timeline_count"], 2)
        self.assertEqual([t["name"] for t in result["timelines"]], ["Original", "Alt"])
        self.assertEqual([t["is_current"] for t in result["timelines"]], [True, False])
        self.assertEqual(result["timelines"][0]["tracks"]["video"], 1)
        self.assertEqual(result["timelines"][0]["duration_frames"], 100)
        self.assertEqual([b["path"] for b in result["media_pool"]["bins"]], ["Master", "Master/Interviews", "Master/Interviews/Day 2"])
        self.assertEqual(result["media_pool"]["clip_total"], 3)
        self.assertEqual(result["media_pool"]["clips"][2], {
            "id": "id-c", "name": "c", "bin": "Master/Interviews", "type": "Video", "duration": "00:00:05:00",
            "resolution": None, "file_path": "/m/c.mov"})
        self.assertEqual(result["render_jobs"], 2)
        self.assertEqual(result["settings"], {"fps": 24.0, "resolution": [1920, 1080]})
        self.assertIsNone(result["truncated"])

    def test_truncation(self):
        project = FullProject([], Folder("Master", [Item(str(i)) for i in range(10)], [Folder("x"), Folder("y")]))
        for i in range(3):
            project.current.DuplicateTimeline("T%d" % i)
        result = ops_for(project).dispatch("project_overview", {"timeline_limit": 2, "clip_limit": 4, "folder_limit": 2})
        self.assertEqual(len(result["timelines"]), 2)
        self.assertEqual(result["timeline_count"], 4)
        self.assertEqual(len(result["media_pool"]["clips"]), 4)
        self.assertEqual(len(result["media_pool"]["bins"]), 2)
        self.assertEqual(set(result["truncated"]), {"timelines", "clips", "folders"})

    def test_read_only(self):
        self.assertIn("project_overview", operations.QUEUE_READ_ONLY)
        self.assertNotIn("resolve_api", operations.QUEUE_READ_ONLY)


class ResolveApiTest(unittest.TestCase):
    def test_references_chain_through_steps(self):
        project = FullProject([], Folder("Master", [Item("a"), Item("b")]))
        result = ops_for(project).dispatch("resolve_api", {"steps": [
            {"on": "media_pool", "call": "GetRootFolder"},
            {"on": "$0", "call": "GetClipList"},
            {"on": "media_pool", "call": "CreateTimelineFromClips", "args": ["Selects", ["$1[1]"]]},
            {"on": "$2", "call": "GetName"},
            {"call": "SetSetting", "args": ["timelineFrameRate", "$$25"]},
        ]})
        self.assertTrue(result["ok"])
        self.assertEqual(project.timelines[-1].source_items, [project.root.clips[1]])
        self.assertEqual(result["steps"][3]["result"], "Selects")
        self.assertEqual(result["steps"][1]["result"][0], {"object": "Object", "name": "a", "id": "id-a"})
        self.assertEqual(project.settings, {"timelineFrameRate": "$25"})

    def test_dict_and_one_based_lua_tables(self):
        ops = ops_for(FullProject())
        raw = [{"a": {1: "first", 2: "second"}}]
        self.assertEqual(ops._api_ref("$0.a[0]", raw), "first")
        with self.assertRaisesRegex(OperationError, "not run yet"):
            ops._api_ref("$1", raw)

    def test_destructive_refused_before_anything_runs(self):
        project = FullProject()
        steps = [{"call": "SetSetting", "args": ["x", "1"]},
                 {"on": "project", "call": "DeleteTimelines", "args": [["$0"]]}]
        with self.assertRaisesRegex(OperationError, "allow_destructive"):
            ops_for(project).dispatch("resolve_api", {"steps": steps})
        self.assertEqual(project.settings, {})

    def test_destructive_allowed_when_approved(self):
        project = FullProject()
        result = ops_for(project).dispatch("resolve_api", {"allow_destructive": True, "steps": [
            {"on": "project", "call": "GetTimelineByIndex", "args": [1]},
            {"on": "project", "call": "DeleteTimelines", "args": [["$0"]]}]})
        self.assertTrue(result["ok"])
        self.assertEqual(project.deleted, [project.current])

    def test_failure_mid_way_reports_completed_steps(self):
        project = FullProject()
        with self.assertRaises(OperationError) as caught:
            ops_for(project).dispatch("resolve_api", {"steps": [
                {"call": "SetSetting", "args": ["x", "1"]},
                {"on": "project", "call": "NoSuchMethod"}]})
        message = str(caught.exception)
        self.assertIn("failed_step 1 (project.NoSuchMethod)", message)
        self.assertIn("already ran", message)
        self.assertEqual(project.settings, {"x": "1"})

    def test_rejects_private_and_bad_targets(self):
        ops = ops_for(FullProject())
        for steps in ([{"call": "__class__"}], [{"on": "os", "call": "GetName"}], [{"call": ""}],
                      [{"call": "GetName", "args": "x"}], [{"call": "GetName"}] * 51):
            with self.assertRaises(OperationError):
                ops.dispatch("resolve_api", {"steps": steps})

    def test_non_object_target(self):
        with self.assertRaisesRegex(OperationError, "not a Resolve object"):
            ops_for(FullProject()).dispatch("resolve_api", {"steps": [
                {"call": "GetName"}, {"on": "$0", "call": "GetName"}]})


class CurrentContextProjectScopeTest(unittest.TestCase):
    def test_no_project(self):
        context = ops_for(None)._op_current_context({})
        self.assertIsNone(context["timeline_count"])
        self.assertEqual(context["timelines"], [])

    def test_lists_timelines_with_current_flag(self):
        project = FullProject([Clip(0, 10)])
        project.current.DuplicateTimeline("Alt")
        context = ops_for(project)._op_current_context({})
        self.assertEqual(context["timeline_count"], 2)
        self.assertEqual([(t["name"], t["is_current"]) for t in context["timelines"]], [("Original", True), ("Alt", False)])

    def test_names_are_cached_but_current_flag_is_fresh(self):
        project = FullProject([Clip(0, 10)])
        alt = project.current.DuplicateTimeline("Alt")
        ops = ops_for(project)
        ops._op_current_context({})
        project.timelines[0].name = "Renamed"
        project.current = alt
        context = ops._op_current_context({})
        self.assertEqual([(t["name"], t["is_current"]) for t in context["timelines"]], [("Original", False), ("Alt", True)])

    def test_no_timeline_still_lists_timelines(self):
        project = FullProject([])
        project.current = None
        context = ops_for(project)._op_current_context({})
        self.assertEqual(context["timeline_count"], 1)
        self.assertFalse(context["timelines"][0]["is_current"])


if __name__ == "__main__":
    unittest.main()
