"""In-place trims, clip length, clip volume, still length and FCPXML transitions, against fakes only.

The fakes follow what live probes showed on Resolve 21.2 Free: Set*Offset take absolute offsets, trim or extend
in place, move linked audio with the video and never ripple; SetMarkInOut(0, N-1, "all") gives a still N frames;
Timeline.Export(path, 6) writes a bundle folder path/Info.fcpxml; an imported spine <transition> becomes a
"Cross Dissolve" item on V1. None of this replaces a live check.
"""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from bridge.operations import OperationError, ResolveOperations
from fakes import Clip, Media, Project, Resolve, Timeline


class OffsetClip(Clip):
    """A clip whose media has a known length and whose offsets move it like Resolve does (linked items follow)."""

    def __init__(self, *args, media_frames=1000, quantize=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.media_frames, self.quantize, self.linked, self.item_markers = media_frames, quantize, [], {}

    def GetRightOffset(self):
        return self.media_frames - (self.source + self.GetDuration())

    def GetLinkedItems(self):
        return list(self.linked)

    def GetMarkers(self):
        return dict(self.item_markers)

    def _all(self):
        return [self] + self.linked

    def SetLeftOffset(self, value):
        value = value - 1 if self.quantize else value
        delta = value - self.source
        for clip in self._all():
            clip.start += delta
            clip.source = value
        return True

    def SetRightOffset(self, value):
        delta = value - self.GetRightOffset()
        for clip in self._all():
            clip.end -= delta
        return True


def pair(start, end, source, name, media_frames=1000, **kwargs):
    media = Media("/tmp/%s.mov" % name)
    media.props["Frames"] = str(media_frames)
    video = OffsetClip(start, end, media, source, name=name, kind="video", track=1, media_frames=media_frames, **kwargs)
    audio = OffsetClip(start, end, media, source, name=name, kind="audio", track=1, media_frames=media_frames, **kwargs)
    video.linked, audio.linked = [audio], [video]
    return video, audio


class TrimInPlaceTests(unittest.TestCase):
    def setUp(self):
        self.a = pair(0, 100, 10, "a")
        self.b = pair(100, 200, 50, "b")
        self.c = pair(220, 300, 0, "c")
        self.project = Project([*self.a, *self.b, *self.c])
        self.op = ResolveOperations(lambda: Resolve(self.project))

    def test_head_trim_moves_start_leaves_gap_and_takes_linked_audio(self):
        out = self.op._op_trim_in_place({"item_id": self.b[0].uid, "head_frames": 6})
        self.assertTrue(out["verified"], out)
        self.assertEqual((self.b[0].start, self.b[0].source), (106, 56))
        self.assertEqual((self.b[1].start, self.b[1].end), (106, 200))
        self.assertEqual(out["gaps_left"], [{"start_frame": 100, "end_frame": 106, "where": "before the clip",
                                             "touches_previous_clip": True}])
        self.assertEqual((self.a[0].end, self.c[0].start), (100, 220))  # no ripple
        self.assertEqual(out["other_clips_moved"], [])

    def test_tail_extension_into_a_gap_and_refusals(self):
        out = self.op._op_trim_in_place({"item_id": self.b[0].uid, "tail_frames": -20})
        self.assertEqual(self.b[0].end, 220)
        self.assertTrue(out["exact"])
        with self.assertRaisesRegex(OperationError, "overlap the clip starting at 220.*room: 0"):
            self.op._op_trim_in_place({"item_id": self.b[0].uid, "tail_frames": -1})
        with self.assertRaisesRegex(OperationError, "overlap the clip ending at 100"):
            self.op._op_trim_in_place({"item_id": self.b[0].uid, "head_frames": -5})
        with self.assertRaisesRegex(OperationError, "only 0 frames of media before"):
            self.op._op_trim_in_place({"item_id": self.c[0].uid, "head_frames": -5})
        with self.assertRaisesRegex(OperationError, "no frames"):
            self.op._op_trim_in_place({"item_id": self.c[0].uid, "head_frames": 50, "tail_frames": 30})
        self.assertEqual((self.b[0].start, self.b[0].end, self.c[0].start), (100, 220, 220))

    def test_media_end_is_refused(self):
        short = pair(400, 480, 0, "short", media_frames=90)
        project = Project([*short])
        op = ResolveOperations(lambda: Resolve(project))
        with self.assertRaisesRegex(OperationError, "only 10 frames of media after"):
            op._op_trim_in_place({"item_id": short[0].uid, "tail_frames": -11})
        self.assertTrue(op._op_trim_in_place({"item_id": short[0].uid, "tail_frames": -10})["verified"])

    def test_dry_run_and_quantized_readback(self):
        plan = self.op._op_trim_in_place({"item_id": self.b[0].uid, "head_frames": 4, "dry_run": True})
        self.assertTrue(plan["dry_run"])
        self.assertEqual(self.b[0].start, 100)
        odd = pair(500, 600, 40, "phone", quantize=True)
        project = Project([*odd])
        op = ResolveOperations(lambda: Resolve(project))
        out = op._op_trim_in_place({"item_id": odd[0].uid, "head_frames": 5})
        self.assertFalse(out["exact"])
        self.assertEqual(out["actual"]["start"], 504)
        self.assertIn("instead of", out["note"])

    def test_set_clip_length_on_a_still(self):
        media = Media("/tmp/photo.png")
        media.props.update(Type="Still", Frames="120")
        still = OffsetClip(0, 120, media, 0, name="photo", kind="video", track=2, media_frames=1353601)
        project = Project([still])
        op = ResolveOperations(lambda: Resolve(project))
        out = op._op_set_clip_length({"item_id": still.uid, "frames": 54})
        self.assertEqual((still.start, still.end), (0, 54))
        self.assertTrue(out["verified"])
        op._op_set_clip_length({"item_id": still.uid, "frames": 240})
        self.assertEqual(still.end, 240)


class ClipVolumeTests(unittest.TestCase):
    def test_volume_is_read_back_and_video_maps_to_linked_audio(self):
        video, audio = pair(0, 100, 0, "talk")
        project = Project([video, audio])
        op = ResolveOperations(lambda: Resolve(project))
        out = op._op_set_clip_volume({"item_ids": [video.uid], "db": -6})
        self.assertTrue(out["verified"])
        self.assertEqual(audio.props["AudioVolume"], -6.0)
        self.assertNotIn("AudioVolume", video.props)
        batch = op._op_set_clip_volume({"items": [{"item_id": audio.uid, "db": 3.25}]})
        self.assertEqual(batch["clips"][0]["before_db"], -6.0)
        self.assertEqual(batch["clips"][0]["after_db"], 3.25)
        with self.assertRaisesRegex(OperationError, "between"):
            op._op_set_clip_volume({"item_ids": [audio.uid], "db": 40})

    def test_unread_volume_is_not_claimed(self):
        video, audio = pair(0, 100, 0, "talk")
        audio.SetProperty = lambda key, value: True  # returns True, stores nothing
        op = ResolveOperations(lambda: Resolve(Project([video, audio])))
        out = op._op_set_clip_volume({"item_ids": [audio.uid], "db": -3})
        self.assertFalse(out["verified"])


class MarkedMedia(Media):
    def __init__(self, *args, marks=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.props.update(Type="Still", Frames="120")
        self.marks = marks or {}
        self.calls = []

    def GetMarkInOut(self):
        return {kind: dict(value) for kind, value in self.marks.items()}

    def SetMarkInOut(self, first, last, kind="all"):
        self.calls.append(("set", first, last, kind))
        self.marks = {"video": {"in": first, "out": last}, "audio": {"in": first, "out": last}}
        return True

    def ClearMarkInOut(self, kind="all"):
        self.calls.append(("clear", kind))
        self.marks = {}
        return True


class StillLengthTests(unittest.TestCase):
    def make(self, marks=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "logo.png"
        path.write_bytes(b"png")
        media = MarkedMedia(path, marks=marks)
        project = Project([Clip(0, 500)])
        pool = project.pool
        pool.GetRootFolder = lambda: None
        pool.ImportMedia = lambda paths: [media]
        requests = []

        def append(reqs):
            out = []
            for r in reqs:
                requests.append(dict(r))
                if "endFrame" in r:  # 21.2 Free ignores endFrame for stills: the preference length wins
                    length = 120
                else:
                    mark = media.marks.get("video")
                    length = mark["out"] - mark["in"] + 1 if mark else 120
                clip = Clip(r.get("recordFrame", 500), r.get("recordFrame", 500) + length, media, 0, track=r["trackIndex"])
                project.current.clips.append(clip)
                out.append(clip)
            return out
        pool.AppendToTimeline = append
        op = ResolveOperations(lambda: Resolve(project))
        op._walk_media = lambda *args: []
        return op, media, requests, path

    def test_marks_give_the_requested_length_and_are_cleared(self):
        op, media, requests, path = self.make()
        out = op._op_add_image({"path": str(path), "duration_frames": 192, "track_index": 1, "record_frame": 600})
        self.assertEqual(out["actual_duration_frames"], 192)
        self.assertEqual(out["length_method"], "mark in/out")
        self.assertNotIn("endFrame", requests[0])
        self.assertEqual(media.marks, {})
        self.assertTrue(out["media_marks_restored"])
        self.assertNotIn("notes", out)

    def test_previous_marks_are_restored(self):
        op, media, _, path = self.make(marks={"video": {"in": 5, "out": 40}, "audio": {"in": 5, "out": 40}})
        op._op_add_image({"path": str(path), "duration_frames": 48, "track_index": 1, "record_frame": 600})
        self.assertEqual(media.marks["video"], {"in": 5, "out": 40})
        self.assertEqual(media.calls[-1], ("set", 5, 40, "all"))

    def test_differing_marks_are_reported_not_hidden(self):
        op, media, _, path = self.make(marks={"video": {"in": 5, "out": 40}, "audio": {"in": 0, "out": 10}})
        out = op._op_add_image({"path": str(path), "duration_frames": 48, "track_index": 1, "record_frame": 600})
        self.assertFalse(out["media_marks_restored"])
        self.assertIn("different video and audio", out["media_marks_note"])


# ------------------------------------------------------------------ FCPXML transitions

def _time(frames, fps):
    value = Fraction(frames, fps)
    return "%ds" % value.numerator if value.denominator == 1 else "%d/%ds" % (value.numerator, value.denominator)


class FcpxmlTimeline(Timeline):
    """Exports V1 (with its clips' own audio) the way Resolve 21.2 Free does: a bundle folder with Info.fcpxml."""

    def Export(self, path, kind, subtype=None):
        if kind != 6:
            return False
        bundle = Path(path)
        bundle.mkdir(parents=True)
        root = ET.Element("fcpxml", {"version": "1.10"})
        resources = ET.SubElement(root, "resources")
        ET.SubElement(resources, "format", {"id": "r0", "frameDuration": "1/%ds" % self.fps})
        sequence = ET.SubElement(ET.SubElement(ET.SubElement(ET.SubElement(root, "library"), "event"), "project"),
                                 "sequence", {"format": "r0", "tcStart": _time(self.start, self.fps)})
        spine = ET.SubElement(sequence, "spine")
        self.project.assets = {}
        for index, clip in enumerate(sorted((c for c in self.clips if c.kind == "video"), key=lambda c: c.start), 1):
            ref = "r%d" % index
            self.project.assets[ref] = clip.media
            ET.SubElement(resources, "asset", {"id": ref, "name": clip.name})
            still = "still" in str(clip.media.props.get("Type", "")).lower()
            ET.SubElement(spine, "video" if still else "asset-clip", {"ref": ref, "name": clip.name, "offset": _time(clip.start, self.fps),
                                                "start": _time(clip.source, self.fps),
                                                "duration": _time(clip.end - clip.start, self.fps)})
        ET.ElementTree(root).write(str(bundle / "Info.fcpxml"), encoding="UTF-8", xml_declaration=True)
        return True


class FcpxmlPool:
    def __init__(self, project):
        self.project, self.imported, self.added, self.moved = project, [], [], []
        pool = self

        class Root:
            def GetName(self):
                return "Master"

            def GetClipList(self):
                return list(pool.added)

            def GetSubFolderList(self):
                return list(pool.folders)
        self.folders, self.root, self.current = [], Root(), None

    def GetRootFolder(self):
        return self.root

    def GetCurrentFolder(self):
        return self.current

    def SetCurrentFolder(self, folder):
        self.current = folder
        return True

    def AddSubFolder(self, parent, name):
        folder = type("Bin", (), {"GetName": lambda self: name, "GetClipList": lambda self: [],
                                  "GetSubFolderList": lambda self: []})()
        self.folders.append(folder)
        self.current = folder
        return folder

    def MoveClips(self, clips, folder):
        self.moved.append((len(clips), folder.GetName()))
        return True

    def ImportTimelineFromFile(self, path, options):
        root = ET.parse(path).getroot()
        self.imported.append(ET.tostring(root, encoding="unicode"))
        sequence = root.find(".//sequence")
        fps = int(1 / Fraction(sequence.get("tcStart") and root.find(".//format").get("frameDuration")[:-1]))
        start = int(Fraction(sequence.get("tcStart")[:-1]) * fps)
        clips = []
        for child in sequence.find("spine"):
            offset = int(Fraction(child.get("offset")[:-1]) * fps)
            length = int(Fraction(child.get("duration")[:-1]) * fps)
            if child.tag == "transition":
                clips.append(Clip(offset, offset + length, None, 0, name=child.get("name"), kind="video", track=1))
                clips[-1].GetMediaPoolItem = lambda: None
                clips[-1].GetLeftOffset = lambda: None  # Live: a transition item reports no offsets.
            elif child.tag in ("asset-clip", "video"):
                media = self.project.assets[child.get("ref")]
                source = int(Fraction(child.get("start")[:-1]) * fps)
                self.added.append(Media(media.props.get("File Path", "")))  # importSourceClips=True: a 2nd entry
                for kind in (("video",) if child.tag == "video" else ("video", "audio")):
                    clips.append(OffsetClip(offset, offset + length, media, source, name=child.get("name"), kind=kind, track=1))
        timeline = FcpxmlTimeline(self.project, options["timelineName"], clips, start, fps)
        self.project.timelines.append(timeline)
        assert options.get("importSourceClips") is True, "importSourceClips=False leaves clips without media (live)"
        return timeline


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.a, self.b, self.c = pair(86400, 86448, 0, "a"), pair(86448, 86496, 24, "b"), pair(86496, 86544, 30, "c")
        self.a[0].props.update(ZoomX=1.2, ZoomY=1.2)
        self.b[1].props["AudioVolume"] = -4.0
        self.project = Project()
        self.original = FcpxmlTimeline(self.project, "Edit", [*self.a, *self.b, *self.c], start=86400, fps=24)
        self.original.markers[10] = dict(color="Blue", name="note", note="", duration=1, customData="")
        self.project.current, self.project.timelines = self.original, [self.original]
        self.project.pool = FcpxmlPool(self.project)
        self.op = ResolveOperations(lambda: Resolve(self.project), runtime_home=self.home.name)

    def test_dissolves_land_on_a_new_timeline_with_settings_carried(self):
        out = self.op._op_add_transitions({"cuts": "all", "duration_frames": 12})
        self.assertTrue(out["verified"], out)
        new = self.project.current
        self.assertIsNot(new, self.original)
        self.assertIn("(transitions)", new.name)
        self.assertEqual(len(self.original.clips), 6)  # original untouched
        self.assertEqual([(t["start_frame"], t["end_frame"]) for t in out["transitions_found"]],
                         [(86442, 86454), (86490, 86502)])
        xml = self.project.pool.imported[-1]
        self.assertIn('<transition name="Cross Dissolve" offset="86442/24s" duration="12/24s">', xml)
        self.assertIn("FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265", xml)
        moved = {(c.kind, c.start): c for c in new.clips if c.GetMediaPoolItem() is not None}
        self.assertEqual(moved[("video", 86400)].props["ZoomX"], 1.2)
        self.assertEqual(moved[("audio", 86448)].props["AudioVolume"], -4.0)
        self.assertIn(10, new.markers)
        self.assertTrue(any("crossfade" in line for line in out["not_carried"]))
        self.assertTrue((Path(self.home.name) / "work").is_dir())  # Resolve writes inside the runtime home

    def test_cuts_without_handles_are_skipped_and_specific_cuts_honoured(self):
        self.c[0].source = self.c[1].source = 4  # only 4 frames of media before c's head
        out = self.op._op_add_transitions({"cuts": [86496, 86448, 12345], "duration_frames": 24})
        self.assertEqual([t["cut_frame"] for t in out["transitions"]], [86448])
        reasons = {s["cut_frame"]: s["reason"] for s in out["skipped"]}
        self.assertIn("12 before V1.3's start (has 4)", reasons[86496])
        self.assertIn("no V1 cut there", reasons[12345])

    def test_dry_run_and_unrepresentable_timelines_change_nothing(self):
        plan = self.op._op_add_transitions({"dry_run": True})
        self.assertTrue(plan["dry_run"])
        self.assertEqual(len(self.project.timelines), 1)
        self.original.clips.append(Clip(86400, 86410, Media("/tmp/x"), 0, name="title", kind="video", track=2))
        with self.assertRaisesRegex(OperationError, "V2.*Nothing was changed"):
            self.op._op_add_transitions({})
        self.assertEqual(len(self.project.timelines), 1)

    def test_new_media_entries_are_filed_and_default_anchors_do_not_warn(self):
        out = self.op._op_add_transitions({"duration_frames": 12})
        self.assertEqual(out["media_pool_entries"]["added"], 3)
        self.assertEqual(out["media_pool_entries"]["filed_into"], "Agent Work")
        self.assertEqual(self.project.pool.moved, [(3, "Agent Work")])
        self.assertFalse(any("Transform settings" in w for w in out["warnings"]), out["warnings"])

    def test_a_still_on_v1_is_exported_as_video_and_needs_no_handles(self):
        photo = Media("/tmp/photo.png")
        photo.props.update(Type="Still", Frames="120")
        still = OffsetClip(86544, 86592, photo, 0, name="photo", kind="video", track=1, media_frames=1353601)
        self.original.clips.append(still)
        out = self.op._op_add_transitions({"cuts": [86544], "duration_frames": 12})
        self.assertTrue(out["verified"], out)
        self.assertIn("<video ", self.project.pool.imported[-1])

    def test_audio_fades_are_written_but_marked_unverified(self):
        out = self.op._op_add_transitions({"duration_frames": 12, "audio_fades": True, "fade_frames": 6})
        self.assertIn('<fadeIn type="linear" duration="6/24s" />', self.project.pool.imported[-1])
        self.assertIsNone(out["audio_fades"]["verified"])
        self.assertTrue(any("audio fades" in line for line in out["not_carried"]))


if __name__ == "__main__":
    unittest.main()
