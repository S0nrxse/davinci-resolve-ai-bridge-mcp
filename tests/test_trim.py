"""Trim, slip, slide and roll: planner rules and the duplicate-timeline rebuild, against fakes only."""
import math
import tempfile
import unittest
from pathlib import Path

from bridge import trim
from bridge.operations import OperationError, ResolveOperations
from fakes import Clip, Media, Project, Resolve


def media(path, frames=1000, fps=24):
    item = Media(path, fps=fps)
    item.props["Frames"] = str(frames)
    return item


class RateClip(Clip):
    """A clip whose media runs at another rate, played in real time (GetSourceEndFrame in media frames)."""
    def __init__(self, *args, timeline_fps=24.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.timeline_fps = timeline_fps

    def GetSourceEndFrame(self):
        rate = float(self.media.props["FPS"]) / self.timeline_fps
        return self.source + int(math.ceil(self.GetDuration() * rate - 1e-9)) - 1


def conforming_append(project, timeline_fps=24.0, shift=0):
    """AppendToTimeline that conforms mixed rates like Resolve (floors the timeline length)."""
    requests = []

    def append(reqs):
        out = []
        for r in reqs:
            requests.append(dict(r))
            rate = float(r["mediaPoolItem"].props["FPS"]) / timeline_fps
            length = int(math.floor((r["endFrame"] - r["startFrame"]) / rate + 1e-9))
            start = r["recordFrame"] + shift
            clip = RateClip(start, start + length, r["mediaPoolItem"], r["startFrame"],
                            kind="video" if r["mediaType"] == 1 else "audio", track=r["trackIndex"],
                            timeline_fps=timeline_fps)
            project.current.clips.append(clip)
            out.append(clip)
        return out
    project.pool.AppendToTimeline = append
    return requests


def layout(timeline):
    """{"V1": [(start, end, source), ...]} of a fake timeline."""
    out = {}
    for c in timeline.clips:
        out.setdefault("%s%d" % ("V" if c.kind == "video" else "A", c.track), []).append((c.start, c.end, c.source))
    return {k: sorted(v) for k, v in sorted(out.items())}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "talk.mov"
        self.path.write_bytes(b"x")


class TrimRebuildTests(Base):
    """Three cuts a|b|c on V1 with linked audio on A1, and a music bed on A2 under everything."""

    def setUp(self):
        super().setUp()
        self.ma, self.mb, self.mc = media(self.path), media(self.path, frames=410), media(self.path)
        self.music = media(self.path, frames=5000)
        clips = []
        for m, start, source, name in ((self.ma, 0, 4, "a"), (self.mb, 100, 300, "b"), (self.mc, 200, 50, "c")):
            clips.append(Clip(start, start + 100, m, source, name=name, kind="video", track=1))
            clips.append(Clip(start, start + 100, m, source, name=name, kind="audio", track=1))
        clips.append(Clip(0, 300, self.music, 0, name="music", kind="audio", track=2))
        self.project = Project(clips)
        self.original = self.project.current
        self.original.markers[150] = dict(color="Blue", name="inside b", note="", duration=1, customData="")
        self.original.markers[250] = dict(color="Red", name="in c", note="", duration=1, customData="")
        self.before = layout(self.original)
        self.op = ResolveOperations(lambda: Resolve(self.project))
        self.requests = conforming_append(self.project)

    def ids(self, name, kind="video"):
        return next(c.uid for c in self.original.clips if c.name == name and c.kind == kind)

    def assert_original_kept(self):
        self.assertEqual(layout(self.original), self.before)
        self.assertIn(self.original, self.project.timelines)

    def assert_refused(self, op, params, text):
        with self.assertRaises(OperationError) as caught:
            getattr(self.op, op)(params)
        self.assertIn(text, str(caught.exception))
        self.assertIn("Nothing was changed", str(caught.exception))
        self.assertEqual(len(self.project.timelines), 1)  # No duplicate was made.
        self.assertIs(self.project.current, self.original)
        self.assert_original_kept()

    # ---- trim_edit

    def test_ripple_tail_trim_moves_later_clips_on_every_track(self):
        out = self.op._op_trim_edit({"item_id": self.ids("b"), "edge": "tail", "frames": -10, "ripple": True})
        new = self.project.current
        self.assertIsNot(new, self.original)
        self.assertIn("(trimmed)", new.name)
        self.assertEqual(out["result_timeline"]["name"], new.name)
        expected = {"V1": [(0, 100, 4), (100, 190, 300), (190, 290, 50)],
                    "A1": [(0, 100, 4), (100, 190, 300), (190, 290, 50)],
                    "A2": [(0, 300, 0)]}
        self.assertEqual(layout(new), expected)
        self.assertTrue(out["verified"], out["verification"])
        self.assertEqual(out["changed_count"], 4)  # b and c on V1 and A1; a and the music bed are untouched copies.
        self.assertEqual(len(self.requests), 4)
        self.assertTrue(any("music" in note and "spans the edit point" in note for note in out["notes"]))
        self.assertIn(240, new.markers)  # Marker in c moved with it.
        self.assertIn(150, new.markers)
        self.assertNotIn(250, new.markers)
        self.assert_original_kept()

    def test_non_ripple_tail_trim_leaves_a_gap_and_refuses_to_overwrite(self):
        out = self.op._op_trim_edit({"item_id": self.ids("b"), "edge": "tail", "frames": -10, "ripple": False})
        self.assertEqual(layout(self.project.current)["V1"], [(0, 100, 4), (100, 190, 300), (200, 300, 50)])
        self.assertEqual(out["changed_count"], 2)
        self.assertIn(250, self.project.current.markers)

    def test_non_ripple_extension_into_a_touching_clip_is_refused(self):
        self.assert_refused("_op_trim_edit", {"item_id": self.ids("b"), "edge": "tail", "frames": 5, "ripple": False},
                            "overlap")

    def test_non_ripple_extension_into_a_gap(self):
        for c in self.original.clips:  # Open a 20 frame gap after b on V1/A1.
            if c.name == "c":
                c.start, c.end = 220, 320
        self.before = layout(self.original)
        self.op._op_trim_edit({"item_id": self.ids("b"), "edge": "tail", "frames": 10, "ripple": False})
        self.assertEqual(layout(self.project.current)["A1"], [(0, 100, 4), (100, 210, 300), (220, 320, 50)])
        self.assert_original_kept()

    def test_ripple_head_trim_keeps_the_start_and_closes_up(self):
        out = self.op._op_trim_edit({"item_id": self.ids("b", "audio"), "edge": "head", "frames": 6})
        new = layout(self.project.current)
        self.assertEqual(new["V1"], [(0, 100, 4), (100, 194, 306), (194, 294, 50)])
        self.assertEqual(new["A1"], new["V1"])  # The video followed its linked audio.
        self.assertTrue(out["verified"])

    def test_ripple_head_extension_pushes_later_clips(self):
        self.op._op_trim_edit({"item_id": self.ids("b"), "edge": "head", "frames": -6})
        self.assertEqual(layout(self.project.current)["V1"], [(0, 100, 4), (100, 206, 294), (206, 306, 50)])

    def test_non_ripple_head_trim(self):
        self.op._op_trim_edit({"item_id": self.ids("b"), "edge": "head", "frames": 6, "ripple": False})
        self.assertEqual(layout(self.project.current)["V1"], [(0, 100, 4), (106, 200, 306), (200, 300, 50)])

    def test_head_handle_limit_is_refused_with_the_available_frames(self):
        self.assert_refused("_op_trim_edit", {"item_id": self.ids("a"), "edge": "head", "frames": -10},
                            "only 4 frames of head handle")

    def test_trim_refuses_to_remove_the_whole_clip(self):
        self.assert_refused("_op_trim_edit", {"item_id": self.ids("b"), "edge": "tail", "frames": -100}, "would be 0 frames")

    # ---- slip_clip

    def test_slip_moves_only_the_source_of_the_clip_and_its_audio(self):
        out = self.op._op_slip_clip({"item_id": self.ids("b"), "frames": 8})
        new = layout(self.project.current)
        self.assertEqual(new["V1"], [(0, 100, 4), (100, 200, 308), (200, 300, 50)])
        self.assertEqual(new["A1"], new["V1"])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(out["changed"][0]["after"]["source_out"], 407)
        self.assertIn("(slipped)", self.project.current.name)

    def test_slip_tail_handle_limit(self):  # b's media has 410 frames; it uses 300-399.
        self.assert_refused("_op_slip_clip", {"item_id": self.ids("b"), "frames": 12}, "only 10 frames of tail handle")

    # ---- slide_clip

    def test_slide_lengthens_and_shortens_neighbours(self):
        out = self.op._op_slide_clip({"item_id": self.ids("b"), "frames": 10})
        new = layout(self.project.current)
        self.assertEqual(new["V1"], [(0, 110, 4), (110, 210, 300), (210, 300, 60)])
        self.assertEqual(new["A1"], new["V1"])
        self.assertEqual(new["A2"], [(0, 300, 0)])
        self.assertEqual(max(e for _, e, _ in new["V1"]), 300)  # Total length unchanged.
        self.assertTrue(out["verified"])

    def test_slide_earlier(self):
        self.op._op_slide_clip({"item_id": self.ids("b"), "frames": -10})
        self.assertEqual(layout(self.project.current)["V1"], [(0, 90, 4), (90, 190, 300), (190, 300, 40)])

    def test_slide_refuses_when_a_neighbour_lacks_handles(self):
        # Sliding c later lengthens b's tail; b's media has 410 frames and b uses 300-399.
        self.assert_refused("_op_slide_clip", {"item_id": self.ids("c"), "frames": 11}, "only 10 frames of tail handle")

    # ---- roll_edit

    def test_roll_moves_the_shared_cut(self):
        out = self.op._op_roll_edit({"left_item_id": self.ids("a"), "right_item_id": self.ids("b"), "frames": 6})
        new = layout(self.project.current)
        self.assertEqual(new["V1"], [(0, 106, 4), (106, 200, 306), (200, 300, 50)])
        self.assertEqual(new["A1"], new["V1"])
        self.assertEqual(out["edit"]["type"], "roll")
        self.assertIn("(rolled)", self.project.current.name)

    def test_roll_earlier_accepts_clips_in_either_order(self):
        self.op._op_roll_edit({"left_item_id": self.ids("b"), "right_item_id": self.ids("a"), "frames": -6})
        self.assertEqual(layout(self.project.current)["V1"], [(0, 94, 4), (94, 200, 294), (200, 300, 50)])

    def test_roll_refuses_clips_that_do_not_touch(self):
        self.assert_refused("_op_roll_edit", {"left_item_id": self.ids("a"), "right_item_id": self.ids("c"), "frames": 2},
                            "no shared cut")

    def test_roll_refuses_beyond_the_right_clips_head_handle(self):
        for c in self.original.clips:
            if c.name == "b":
                c.source = 3
        self.before = layout(self.original)
        self.assert_refused("_op_roll_edit", {"left_item_id": self.ids("a"), "right_item_id": self.ids("b"), "frames": -5},
                            "only 3 frames of head handle")

    # ---- safety and honesty

    def test_dry_run_changes_nothing(self):
        out = self.op._op_slide_clip({"item_id": self.ids("b"), "frames": 10, "dry_run": True})
        self.assertTrue(out["dry_run"])
        self.assertEqual(len(self.project.timelines), 1)
        self.assertEqual(self.original.delete_calls, 0)
        self.assertEqual(self.requests, [])
        self.assert_original_kept()
        self.assertEqual(out["changed_count"], 6)

    def test_verification_reports_expected_vs_actual(self):
        self.requests = conforming_append(self.project, shift=1)  # Resolve "lands" every piece a frame late.
        out = self.op._op_roll_edit({"left_item_id": self.ids("a"), "right_item_id": self.ids("b"), "frames": 6})
        self.assertFalse(out["verified"])
        mismatches = out["verification"]["mismatches"]
        self.assertTrue(any("expected 0-106" in m and "found 1-107" in m for m in mismatches), mismatches)
        bad = [c for c in out["verification"]["clips"] if not c["ok"]]
        self.assertEqual(bad[0]["expected"], [0, 106, 4])
        self.assertEqual(bad[0]["actual"], [1, 107, 4])
        self.assertIn("VERIFICATION FAILED", out["note"])
        self.assert_original_kept()

    def test_rebuild_failure_keeps_the_original_and_says_so(self):
        self.project.pool.AppendToTimeline = lambda reqs: []
        with self.assertRaises(OperationError) as caught:
            self.op._op_slip_clip({"item_id": self.ids("b"), "frames": 2})
        self.assertIn("Original 'Original' is intact and reopened", str(caught.exception))
        self.assertIs(self.project.current, self.original)
        self.assert_original_kept()

    def test_retimed_clip_is_refused(self):
        class FastClip(Clip):  # Source span twice the timeline time: a speed change.
            def GetSourceEndFrame(self):
                return self.source + self.GetDuration() * 2 - 1
        fast = FastClip(300, 400, media(self.path), 0, name="fast", kind="video", track=1)
        self.original.clips.append(fast)
        self.before = layout(self.original)
        self.assert_refused("_op_slip_clip", {"item_id": fast.uid, "frames": 2}, "retimed")
        # A ripple that would have to move the retimed clip is refused too.
        self.assert_refused("_op_trim_edit", {"item_id": self.ids("b"), "edge": "tail", "frames": -2}, "retimed")


class MixedRateTests(Base):
    def test_30fps_media_on_a_24fps_timeline_maps_by_time(self):
        phone = media(self.path, frames=2000, fps=30)
        cam = media(self.path, frames=2000, fps=24)
        a = RateClip(0, 96, phone, 100, name="phone", kind="video", track=1)
        a_audio = RateClip(0, 96, phone, 100, name="phone", kind="audio", track=1)
        b = Clip(96, 192, cam, 500, name="cam", kind="video", track=1)
        project = Project([a, a_audio, b])
        op = ResolveOperations(lambda: Resolve(project))
        requests = conforming_append(project)
        out = op._op_slip_clip({"item_id": a.uid, "frames": 8})  # 8 timeline frames = 10 source frames.
        self.assertEqual([(r["startFrame"], r["endFrame"], r["recordFrame"]) for r in requests],
                         [(110, 230, 0), (110, 230, 0)])
        self.assertTrue(out["verified"], out["verification"])

        project.current = project.timelines[0]
        del requests[:]
        out = op._op_roll_edit({"left_item_id": a.uid, "right_item_id": b.uid, "frames": 4})
        by_type = {r["mediaPoolItem"].props["FPS"]: r for r in requests}
        self.assertEqual((by_type["30"]["startFrame"], by_type["30"]["endFrame"]), (100, 225))  # 100 tl frames
        self.assertEqual((by_type["24"]["startFrame"], by_type["24"]["recordFrame"]), (504, 100))
        self.assertTrue(out["verified"], out["verification"])
        self.assertEqual(layout(project.current)["V1"], [(0, 100, 100), (100, 192, 504)])

    def test_mixed_rate_handles_are_reported_in_timeline_frames(self):
        phone = media(self.path, frames=230, fps=30)  # Uses 100-219: 10 source frames = 8 timeline frames left.
        a = RateClip(0, 96, phone, 100, name="phone", kind="video", track=1)
        project = Project([a])
        op = ResolveOperations(lambda: Resolve(project))
        with self.assertRaises(OperationError) as caught:
            op._op_trim_edit({"item_id": a.uid, "edge": "tail", "frames": 9})
        self.assertIn("only 8 frames of tail handle", str(caught.exception))

    def test_drop_frame_timeline_keeps_exact_lengths(self):
        df = media(self.path, frames=5000, fps=29.97)
        a = Clip(1000, 1300, df, 200, name="a", kind="video", track=1)
        b = Clip(1300, 1600, df, 900, name="b", kind="video", track=1)
        project = Project([a, b])
        project.current.fps = "29.97 DF"
        op = ResolveOperations(lambda: Resolve(project))
        requests = conforming_append(project, timeline_fps=29.97)
        out = op._op_roll_edit({"left_item_id": a.uid, "right_item_id": b.uid, "frames": -6})
        self.assertEqual([(r["startFrame"], r["endFrame"], r["recordFrame"]) for r in requests],
                         [(200, 494, 1000), (894, 1200, 1294)])
        self.assertTrue(out["verified"], out["verification"])


class PlannerTests(unittest.TestCase):
    def clip(self, key, track_type, start, end, source=0, **extra):
        base = {"key": key, "label": key, "name": key, "track_type": track_type, "track_index": extra.pop("track", 1),
                "start": start, "end": end, "source": source, "ratio": 1.0, "media_frames": 10000,
                "media_id": key, "linked": [], "rebuildable": True, "locked": False}
        base.update(extra)
        return base

    def test_resolve_links_pair_clips_that_differ(self):  # A J-cut: audio starts earlier than its video.
        clips = [self.clip("v", "video", 100, 200, 500, linked=["a"]),
                 self.clip("a", "audio", 90, 200, 490, media_id="v", linked=["v"])]
        plan = trim.plan_trim(clips, "v", "tail", -5, ripple=False)
        self.assertEqual(plan["expected_tracks"], {"A1": [[90, 195, 490]], "V1": [[100, 195, 500]]})

    def test_title_that_must_move_is_refused_but_one_that_stays_is_fine(self):
        clips = [self.clip("v1", "video", 0, 100), self.clip("v2", "video", 100, 200),
                 self.clip("t", "video", 0, 50, track=2, source=None, rebuildable=False, reason="it has no media pool source")]
        trim.plan_trim(clips, "v1", "tail", -10, ripple=True)  # The title starts before the edit point.
        clips[2].update(start=150, end=180)
        with self.assertRaises(trim.PlanError) as caught:
            trim.plan_trim(clips, "v1", "tail", -10, ripple=True)
        self.assertIn("cannot be rebuilt", str(caught.exception))

    def test_locked_tracks_are_not_rippled_and_say_so(self):
        clips = [self.clip("v1", "video", 0, 100), self.clip("v2", "video", 100, 200),
                 self.clip("m", "audio", 150, 300, locked=True)]
        plan = trim.plan_trim(clips, "v1", "tail", -10, ripple=True)
        self.assertEqual(plan["expected_tracks"]["A1"], [[150, 300, 0]])
        self.assertTrue(any("locked" in note for note in plan["notes"]))

    def test_ripple_blocked_by_a_spanning_clip(self):
        clips = [self.clip("v1", "video", 0, 100), self.clip("v2", "video", 100, 200),
                 self.clip("m1", "audio", 50, 150), self.clip("m2", "audio", 150, 250)]
        plan = trim.plan_trim(clips, "v1", "tail", 10, ripple=True)  # Pushing later is fine.
        self.assertEqual(plan["expected_tracks"]["A1"], [[50, 150, 0], [160, 260, 0]])
        with self.assertRaises(trim.PlanError) as caught:  # Pulling m2 back would overlap m1.
            trim.plan_trim(clips, "v1", "tail", -10, ripple=True)
        self.assertIn("spans the edit point", str(caught.exception))

    def test_ambiguous_names_and_zero_frames(self):
        clips = [self.clip("x", "video", 0, 100, name="cam"), self.clip("y", "video", 100, 200, name="cam")]
        with self.assertRaises(trim.PlanError):
            trim.plan_slip(clips, "cam", 2)
        with self.assertRaises(trim.PlanError):
            trim.plan_slip(clips, "x", 0)
        with self.assertRaises(trim.PlanError):
            trim.plan_trim(clips, "x", "middle", 2)

    def test_timeline_start_is_a_floor(self):
        clips = [self.clip("v", "video", 86400, 86500, 100)]
        with self.assertRaises(trim.PlanError) as caught:
            trim.plan_trim(clips, "v", "head", -10, ripple=False, tl_start=86400)
        self.assertIn("before the timeline start", str(caught.exception))
        plan = trim.plan_slide(clips, "v", 10, tl_start=86400)
        self.assertEqual(plan["expected_tracks"]["V1"], [[86410, 86510, 100]])


if __name__ == "__main__":
    unittest.main()
