"""Listing and tidying away the duplicate timelines the bridge creates. Fakes only; no Resolve."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fakes import Clip, Project, Resolve, Timeline  # noqa: E402
from bridge import timeline_cleanup  # noqa: E402
from bridge.operations import OperationError, ResolveOperations  # noqa: E402

D = "—"  # The em dash _duplicate_current puts before the 8-hex tag.


class Folder:
    def __init__(self, name):
        self.name, self.subs, self.clips = name, [], []
        self.uid = "folder-" + name

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.uid

    def GetSubFolderList(self):
        return list(self.subs)

    def GetClipList(self):
        return list(self.clips)


class TimelineItem:
    """The Media Pool item that stands for a timeline."""
    def __init__(self, timeline):
        self.timeline = timeline

    def GetUniqueId(self):
        return "mpi-" + self.timeline.uid

    def GetName(self):
        return self.timeline.name


def media_pool(project):
    """Give the shared fake Pool bins, MoveClips and DeleteTimelines on this instance only."""
    pool = project.pool
    root, footage = Folder("Master"), Folder("Footage")
    root.subs.append(footage)
    pool.root, pool.current_folder, pool.moves, pool.deletes = root, footage, [], []
    pool.refuse_move, pool.stuck = set(), set()
    pool.GetRootFolder = lambda: root
    pool.GetCurrentFolder = lambda: pool.current_folder
    pool.SetCurrentFolder = lambda folder: setattr(pool, "current_folder", folder) or True

    def add(parent, name):
        folder = Folder(name)
        parent.subs.append(folder)
        pool.current_folder = folder  # Resolve opens a newly added bin.
        return folder

    def move(items, folder):
        pool.moves.append([item.timeline.name for item in items])
        ok = True
        for item in items:
            if item.timeline.uid in pool.refuse_move:
                ok = False
                continue
            for bin_ in [root] + root.subs:
                if item in bin_.clips:
                    bin_.clips.remove(item)
            folder.clips.append(item)
        return ok

    def delete(timelines):
        pool.deletes.append([t.name for t in timelines])
        ok = True
        for timeline in timelines:
            if timeline.uid in pool.stuck:
                ok = False
            elif timeline in project.timelines:
                project.timelines.remove(timeline)
        return ok

    pool.AddSubFolder, pool.MoveClips, pool.DeleteTimelines = add, move, delete
    return pool


def add_timeline(project, name, clips=None):
    timeline = Timeline(project, name, clips if clips is not None else [Clip(0, 48), Clip(0, 48, kind="audio")])
    item = TimelineItem(timeline)
    timeline.GetMediaPoolItem = lambda: item
    project.timelines.append(timeline)
    if getattr(project.pool, "root", None) is not None:
        project.pool.root.clips.append(item)
    return timeline


class ClassifyTests(unittest.TestCase):
    def kind(self, name):
        info = timeline_cleanup.classify(name)
        return info and info["kind"]

    def test_every_bridge_pattern(self):
        cases = {
            "Checkpoint · Main · before “zoom” %s 1a2b3c4d" % D: "checkpoint",
            "Main preview %s 0123abcd" % D: "preview",
            "Main (tightened) %s 3b82c88d" % D: "tightened",
            "Dialogue cleanup %s deadbeef" % D: "tightened",
            "Main (rewound) %s 00ff00ff" % D: "rewound",
            "Before split %s abcdef01" % D: "before_split",
            "My custom cut %s 12345678" % D: "other_ai",
            "Main (tightened) %s 3b82c88d (tightened) %s 0936424c" % (D, D): "tightened",
        }
        for name, kind in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.kind(name), kind)

    def test_user_names_are_never_bridge_made(self):
        for name in ("Main", "Main (tightened)", "Checkpoint · Main · x", "Before split", "Main preview",
                     "Main (tightened) - 3b82c88d", "Main (tightened) %s 3B82C88D" % D, "Main %s 3b82c8" % D,
                     "Main (tightened) %s 3b82c88d " % D, ""):
            with self.subTest(name=name):
                self.assertIsNone(timeline_cleanup.classify(name))

    def test_source_names(self):
        info = timeline_cleanup.classify("Main (tightened) %s 3b82c88d (rewound) %s 11112222" % (D, D))
        self.assertEqual(info["kind"], "rewound")
        self.assertEqual(info["source_candidates"], ["Main (tightened) %s 3b82c88d" % D])
        self.assertEqual(timeline_cleanup.classify("Before split %s abcdef01" % D)["source_candidates"], [])
        self.assertEqual(timeline_cleanup.classify("Dialogue cleanup %s deadbeef" % D)["made_by"], "apply_silence_cuts")

    def test_checkpoint_original_found_even_with_separators_in_names(self):
        original = "Edit · v2 (tightened) %s 0936424c" % D
        info = timeline_cleanup.classify("Checkpoint · %s · before “a · b” %s 5279e886" % (original, D))
        timelines = [{"id": "o", "name": original}, {"id": "x", "name": "Edit"}]
        source, match, candidates = timeline_cleanup.likely_original(info, timelines)
        self.assertEqual((source, match, candidates), (original, {"id": "o", "name": original}, []))

    def test_likely_original_is_honest_about_duplicates_and_missing(self):
        info = timeline_cleanup.classify("Main (tightened) %s 3b82c88d" % D)
        twins = [{"id": "a", "name": "Main"}, {"id": "b", "name": "Main"}]
        self.assertEqual(timeline_cleanup.likely_original(info, twins), ("Main", None, ["a", "b"]))
        self.assertEqual(timeline_cleanup.likely_original(info, []), ("Main", None, []))


class CleanupOperationTests(unittest.TestCase):
    def setUp(self):
        self.project = Project([Clip(0, 240)])
        self.main = self.project.current
        self.main.name = "Main"
        self.pool = media_pool(self.project)
        self.project.pool.root.clips.append(TimelineItem(self.main))
        self.tight = add_timeline(self.project, "Main (tightened) %s 3b82c88d" % D)
        self.check = add_timeline(self.project, "Checkpoint · Main · before “zoom” %s 5279e886" % D)
        self.preview = add_timeline(self.project, "Main preview %s 0123abcd" % D)
        self.user = add_timeline(self.project, "Main (tightened)")  # Typed by the user: no tag.
        self.op = ResolveOperations(lambda: Resolve(self.project))

    def listing(self):
        out = self.op.dispatch("list_ai_timelines", {})
        return out, {entry["id"]: entry for entry in out["ai_timelines"]}

    def test_list_classifies_without_switching_timelines(self):
        self.project.current = self.tight
        out, by_id = self.listing()
        self.assertIs(self.project.current, self.tight)
        self.assertEqual(out["ai_timeline_count"], 3)
        self.assertEqual(out["by_kind"]["tightened"], 1)
        self.assertEqual({e["name"] for e in out["user_timelines"]}, {"Main", "Main (tightened)"})
        tight = by_id[self.tight.uid]
        self.assertTrue(tight["current"])
        self.assertFalse(tight["removable"])
        self.assertEqual(tight["likely_original"], {"id": self.main.uid, "name": "Main"})
        self.assertEqual((tight["clip_count"], tight["items"]["audio"]), (2, 1))
        self.assertEqual((tight["duration_frames"], tight["duration_seconds"]), (48, 2.0))
        self.assertEqual(by_id[self.check.uid]["kind"], "checkpoint")
        self.assertIn("protected", by_id[self.check.uid])
        self.assertTrue(by_id[self.preview.uid]["removable"])
        self.assertEqual(self.pool.moves + self.pool.deletes, [])

    def test_refuses_user_current_and_unknown_timelines(self):
        self.project.current = self.preview
        out = self.op.dispatch("cleanup_ai_timelines", {"timeline_ids": [self.user.uid, self.main.uid, self.preview.uid, "nope"],
                                                        "action": "delete"})
        self.assertFalse(out["changed"])
        self.assertEqual(out["deleted"], [])
        reasons = {entry["id"]: entry["reason"] for entry in out["refused"]}
        self.assertIn("Not a bridge-made", reasons[self.user.uid])
        self.assertIn("Not a bridge-made", reasons[self.main.uid])
        self.assertIn("open timeline", reasons[self.preview.uid])
        self.assertIn("No timeline", reasons["nope"])
        self.assertEqual(self.pool.deletes, [])
        self.assertEqual(len(self.project.timelines), 5)

    def test_checkpoints_are_excluded_unless_asked(self):
        out = self.op.dispatch("cleanup_ai_timelines", {"timeline_ids": [self.check.uid, self.tight.uid], "action": "delete"})
        self.assertEqual([entry["id"] for entry in out["deleted"]], [self.tight.uid])
        self.assertIn("Rewind", out["refused"][0]["reason"])
        self.assertIn("include_checkpoints", out["checkpoints"])
        self.assertIn(self.check, self.project.timelines)
        again = self.op.dispatch("cleanup_ai_timelines", {"timeline_ids": [self.check.uid], "action": "delete",
                                                          "include_checkpoints": True})
        self.assertEqual([entry["id"] for entry in again["deleted"]], [self.check.uid])
        self.assertIn("can no longer restore", again["warning"])

    def test_archive_moves_into_bin_and_keeps_media_pool_view(self):
        footage = self.pool.current_folder
        out = self.op.dispatch("cleanup_ai_timelines", {"timeline_ids": [self.tight.uid, self.preview.uid]})
        self.assertEqual(out["action"], "archive")
        self.assertTrue(out["bin_created"])
        self.assertEqual(out["media_pool_view"], "restored")
        self.assertIs(self.pool.current_folder, footage)
        self.assertEqual({entry["id"] for entry in out["archived"]}, {self.tight.uid, self.preview.uid})
        archive = next(f for f in self.pool.root.subs if f.name == "Agent Work")
        self.assertEqual({item.timeline for item in archive.clips}, {self.tight, self.preview})
        self.assertEqual(len(self.project.timelines), 5)  # Archiving never deletes.
        self.assertTrue(self.listing()[1][self.tight.uid]["in_archive_bin"])
        again = self.op.dispatch("cleanup_ai_timelines", {"timeline_ids": [self.tight.uid]})
        self.assertFalse(again["bin_created"])
        self.assertTrue(again["archived"][0]["already_archived"])
        self.assertFalse(again["changed"])

    def test_archive_reports_a_move_resolve_refused(self):
        self.pool.refuse_move.add(self.preview.uid)
        out = self.op.dispatch("cleanup_ai_timelines", {"timeline_ids": [self.tight.uid, self.preview.uid]})
        self.assertEqual([entry["id"] for entry in out["archived"]], [self.tight.uid])
        self.assertEqual([entry["id"] for entry in out["failed"]], [self.preview.uid])
        self.assertIn("Partial", out["note"])
        self.assertEqual(out["media_pool_view"], "restored")

    def test_delete_verifies_by_relisting_and_reports_partial_failure(self):
        self.pool.stuck.add(self.preview.uid)
        out = self.op.dispatch("cleanup_ai_timelines", {"timeline_ids": [self.tight.uid, self.preview.uid, self.tight.uid],
                                                        "action": "delete"})
        self.assertEqual(out["requested"], 2)
        self.assertEqual([entry["id"] for entry in out["deleted"]], [self.tight.uid])
        self.assertEqual([entry["id"] for entry in out["failed"]], [self.preview.uid])
        self.assertIn("Still in the project", out["failed"][0]["reason"])
        self.assertEqual(out["timeline_count_after"], 4)
        self.assertTrue(out["changed"])
        self.assertIn("Partial", out["note"])
        self.assertIs(self.project.current, self.main)

    def test_delete_trusts_the_relist_over_resolves_return_value(self):
        real = self.pool.DeleteTimelines
        self.pool.DeleteTimelines = lambda timelines: real(timelines) and False
        out = self.op.dispatch("cleanup_ai_timelines", {"timeline_ids": [self.tight.uid], "action": "delete"})
        self.assertEqual(out["deleted"][0]["resolve_returned"], False)
        self.assertEqual(out["failed"], [])

    def test_rejects_bad_arguments(self):
        with self.assertRaises(OperationError):
            self.op.dispatch("cleanup_ai_timelines", {"timeline_ids": [], "action": "archive"})
        with self.assertRaises(OperationError):
            self.op.dispatch("cleanup_ai_timelines", {"timeline_ids": [self.tight.uid], "action": "purge"})


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install requirements.txt to run MCP SDK checks")
class ServerToolTests(unittest.TestCase):
    def test_delete_needs_user_approval_and_archive_does_not(self):
        source = '''
import json
from unittest.mock import patch
from bridge import server
with patch.object(server, "_result", return_value='{"ok": true}') as sent:
    for action in ("delete", "Delete", " DELETE "):
        out = json.loads(server.cleanup_ai_timelines(["x"], action=action))
        assert out["ok"] is False and "approval" in out["error"], out
    assert not sent.called
    server.cleanup_ai_timelines(["x"])
    assert sent.call_args[0][1] == {"timeline_ids": ["x"], "action": "archive", "include_checkpoints": False}
    server.cleanup_ai_timelines(["x"], action="delete", user_approved=True)
    assert sent.call_args[0][1]["action"] == "delete"
    server.list_ai_timelines()
    assert sent.call_args[0][0] == "list_ai_timelines"
'''
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run([sys.executable, "-c", source], cwd=Path(__file__).resolve().parents[1],
                                    env=dict(os.environ, RESOLVE_AI_BRIDGE_HOME=home, PYTHONDONTWRITEBYTECODE="1"),
                                    capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
