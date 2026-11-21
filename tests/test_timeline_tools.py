"""Pause ranges, transcript search, batch markers, video sources, and whole-timeline range removal."""
import json
import tempfile
import unittest
from pathlib import Path

from bridge import transcribe as tx
from bridge.operations import OperationError, ResolveOperations
from fakes import Clip, Media, Project, Resolve


def words_at(spec):
    return [{"word": w, "start_frame": s, "end_frame": e} for w, s, e in spec]


class PauseAndSearchTests(unittest.TestCase):
    def test_pause_ranges_keep_air_on_both_sides(self):
        words = words_at([("one", 0, 10), ("two", 12, 20), ("three", 50, 60), ("four", 61, 70)])
        ranges = tx.pause_ranges(words, 24, min_pause=0.7, keep=0.125)  # keep = 3 frames
        self.assertEqual(len(ranges), 1)
        self.assertEqual((ranges[0]["start_frame"], ranges[0]["end_frame"]), (23, 47))
        self.assertEqual((ranges[0]["after"], ranges[0]["before"]), ("two", "three"))

    def test_short_gaps_are_not_pauses(self):
        self.assertEqual(tx.pause_ranges(words_at([("a", 0, 10), ("b", 20, 30)]), 24, min_pause=0.7), [])

    def test_search_exact_and_fuzzy(self):
        results = [{"item_id": "c1", "name": "Talk", "fps": 24,
                    "segments": [{"start_frame": 0, "end_frame": 100, "text": "We train neural networks on data."}],
                    "words": words_at([("We", 0, 5), ("train", 5, 10), ("neural", 10, 20), ("networks", 20, 30),
                                       ("on", 30, 32), ("data.", 32, 40)])}]
        exact = tx.search(results, "Neural Networks")
        self.assertEqual(len(exact), 1)
        self.assertEqual((exact[0]["start_frame"], exact[0]["end_frame"], exact[0]["score"]), (10, 30, 1.0))
        self.assertIn("train neural", exact[0]["context"])
        fuzzy = tx.search(results, "nural network")
        self.assertEqual(fuzzy[0]["start_frame"], 10)
        self.assertEqual(tx.search(results, "kubernetes"), [])


class TimelineToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "talk.mov"
        path.write_bytes(b"x")
        self.media = Media(path)
        self.video = Clip(0, 240, self.media, source=100, name="cam", kind="video", track=1)
        self.audio = Clip(0, 240, self.media, source=100, name="mic", kind="audio", track=1)
        self.broll = Clip(240, 300, Media(path), source=0, name="broll", kind="video", track=1)
        self.project = Project([self.video, self.audio, self.broll])
        self.op = ResolveOperations(lambda: Resolve(self.project))

    def test_media_sources_timeline_scope(self):
        out = self.op._op_media_sources({"scope": "timeline"})
        self.assertEqual([s["name"] for s in out["sources"]], ["cam", "broll"])
        self.assertEqual(out["sources"][0]["track"], "V1")

    def test_add_markers_skips_duplicates_and_out_of_range(self):
        out = self.op._op_add_markers({"markers": [{"frame": 10, "color": "green", "name": "Beat"},
                                                   {"frame": 10, "name": "dup"}, {"frame": 9999}]})
        self.assertEqual(out["added"], 1)
        self.assertEqual(self.project.current.markers[10]["color"], "Green")
        self.assertEqual(len(out["skipped"]), 2)

    def test_remove_ranges_rebuilds_every_track_on_a_copy(self):
        original = self.project.current
        original.markers[50] = dict(color="Blue", name="keep me", note="", duration=1, customData="")
        original.markers[110] = dict(color="Red", name="inside cut", note="", duration=1, customData="")
        out = self.op._op_remove_ranges({"ranges": [{"start_frame": 100, "end_frame": 120},
                                                    {"start_frame": 115, "end_frame": 130},  # overlaps -> merged
                                                    {"start_frame": 250, "end_frame": 260}]})
        self.assertIsNot(self.project.current, original)
        self.assertEqual(len(original.clips), 3)  # original untouched
        self.assertEqual(out["removed_frames"], 40)
        copy = self.project.current
        video = sorted((c for c in copy.clips if c.kind == "video"), key=lambda c: c.start)
        audio = sorted((c for c in copy.clips if c.kind == "audio"), key=lambda c: c.start)
        self.assertEqual([(c.start, c.end, c.source) for c in video], [(0, 100, 100), (100, 210, 230), (210, 220, 0), (220, 260, 20)])
        self.assertEqual([(c.start, c.end) for c in audio], [(0, 100), (100, 210)])
        self.assertIn(50, copy.markers)
        self.assertEqual(out["markers_dropped"], 1)

    def test_remove_ranges_maps_mixed_frame_rates_by_time(self):
        class PhoneClip(Clip):  # 30 fps media playing in real time on a 24 fps timeline.
            def GetSourceEndFrame(self):
                return self.source + round(self.GetDuration() * 30 / 24) - 1
        media = Media(self.media.props["File Path"], fps=30)
        phone = PhoneClip(0, 240, media, source=100, name="phone", kind="video", track=1)
        project = Project([phone])
        op = ResolveOperations(lambda: Resolve(project))
        requests = []
        original_append = project.pool.AppendToTimeline
        project.pool.AppendToTimeline = lambda reqs: (requests.extend(reqs), original_append(reqs))[1]
        op._op_remove_ranges({"ranges": [{"start_frame": 96, "end_frame": 120}]})
        self.assertEqual([(r["startFrame"], r["endFrame"], r["recordFrame"]) for r in requests],
                         [(100, 220, 0), (250, 400, 96)])  # 96 timeline frames = 120 source frames

    def test_remove_ranges_refuses_retimed_clips(self):
        class FastClip(Clip):  # Source span twice the timeline time: a speed change.
            def GetSourceEndFrame(self):
                return self.source + self.GetDuration() * 2 - 1
        project = Project([FastClip(0, 240, Media(self.media.props["File Path"], fps=30), source=0, kind="video")])
        with self.assertRaises(OperationError):
            ResolveOperations(lambda: Resolve(project))._op_remove_ranges({"ranges": [{"start_frame": 10, "end_frame": 20}]})

    def test_remove_ranges_refuses_removing_everything(self):
        with self.assertRaises(OperationError):
            self.op._op_remove_ranges({"ranges": [{"start_frame": 0, "end_frame": 300}]})


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "a.mov"
        path.write_bytes(b"x")
        self.project = Project([Clip(0, 100, Media(path), source=0, name="cam", kind="video", track=1)])
        self.project.current.name = "Main"
        self.op = ResolveOperations(lambda: Resolve(self.project))

    def test_checkpoint_copies_and_stays_on_the_original(self):
        original = self.project.current
        out = self.op._op_checkpoint_timeline({"label": "before “zoom”"})
        self.assertIs(self.project.current, original)
        self.assertEqual(len(self.project.timelines), 2)
        self.assertTrue(out["checkpoint"]["name"].startswith("Checkpoint · Main · before “zoom”"))
        self.assertEqual(out["timeline"]["name"], "Main")
        self.assertIsNone(out["bin"])  # The fake has no bins; filing is best effort and reported.

    def test_checkpoint_bin_keeps_the_users_media_pool_view(self):
        class Folder:
            def __init__(self, name):
                self.name, self.subs = name, []
            def GetName(self):
                return self.name
            def GetSubFolderList(self):
                return self.subs
        root, footage = Folder("Master"), Folder("Footage")
        root.subs.append(footage)
        pool = self.project.pool
        pool.current_folder, pool.moved = footage, []
        pool.GetRootFolder = lambda: root
        pool.GetCurrentFolder = lambda: pool.current_folder
        pool.SetCurrentFolder = lambda f: setattr(pool, "current_folder", f) or True
        def add(parent, name):
            folder = Folder(name)
            parent.subs.append(folder)
            pool.current_folder = folder  # Resolve opens a new bin
            return folder
        pool.AddSubFolder = add
        pool.MoveClips = lambda items, folder: pool.moved.append((items, folder.name)) or True
        from fakes import Timeline
        Timeline.GetMediaPoolItem = lambda self: "item-" + self.name
        self.addCleanup(lambda: delattr(Timeline, "GetMediaPoolItem"))
        out = self.op._op_checkpoint_timeline({})
        self.assertEqual(out["bin"], "Agent Work")
        self.assertIs(pool.current_folder, footage)
        self.assertEqual(pool.moved[0][1], "Agent Work")

    def test_restore_opens_a_fresh_copy_and_keeps_everything(self):
        original = self.project.current
        checkpoint_id = self.op._op_checkpoint_timeline({})["checkpoint"]["id"]
        original.clips.clear()  # An "AI edit" after the checkpoint.
        out = self.op._op_restore_checkpoint({"checkpoint_id": checkpoint_id})
        restored = self.project.current
        self.assertTrue(restored.name.startswith("Main (rewound)"))
        self.assertEqual(len(restored.clips), 1)
        self.assertEqual(out["previous"]["name"], "Main")
        self.assertIn(original, self.project.timelines)  # nothing deleted
        self.assertEqual(len(self.project.timelines), 3)
        again = self.op._op_restore_checkpoint({"checkpoint_id": checkpoint_id})  # checkpoint still intact
        self.assertNotEqual(again["restored"]["id"], out["restored"]["id"])

    def test_restore_refuses_a_missing_checkpoint(self):
        with self.assertRaises(OperationError):
            self.op._op_restore_checkpoint({"checkpoint_id": "gone"})


if __name__ == "__main__":
    unittest.main()
