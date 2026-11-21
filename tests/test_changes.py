"""Change tracking: sentences for edits made in Resolve between chat turns."""
import unittest

from chat.changes import describe, find_clip, timecode


def snap(items, markers=None, timeline=("t1", "Main"), timelines=None, project="Class"):
    return {"project": project, "timeline": {"id": timeline[0], "name": timeline[1]}, "fps": 24, "start_frame": 86400,
            "timelines": timelines or [{"id": "t1", "name": "Main"}], "markers": markers or {},
            "items": [dict(zip(("id", "name", "track", "start", "end", "offset"), i)) for i in items]}


BASE = [("a", "interview.mov", "V1", 86400, 86448, 0), ("b", "broll.mov", "V1", 86448, 86496, 10),
        ("c", "c.mov", "V1", 86496, 86520, 0), ("d", "d.mov", "V1", 86520, 86544, 0),
        ("e", "music.wav", "A1", 86400, 86544, 0)]


class DescribeTests(unittest.TestCase):
    def test_nothing_changed(self):
        self.assertEqual(describe(snap(BASE), snap(BASE)), [])

    def test_trim_slip_move_add_remove(self):
        after = [("a", "interview.mov", "V1", 86400, 86440, 0),   # tail trimmed 8 frames
                 ("b", "broll.mov", "V1", 86448, 86496, 22),     # slipped +12
                 ("c", "c.mov", "V2", 86496, 86520, 0),          # moved to V2
                 ("e", "music.wav", "A1", 86400, 86544, 0),
                 ("f", "title", "V3", 86410, 86430, 0)]          # added; d removed
        lines = describe(snap(BASE), snap(after))
        text = "\n".join(lines)
        self.assertIn("Trimmed “interview.mov” on V1 (tail -8 frames).", text)
        self.assertIn("Slipped “broll.mov” by +12 frames.", text)
        self.assertIn("Moved “c.mov” from V1 to V2", text)
        self.assertIn("Removed “d.mov” from V1 at 01:00:05:00.", text)
        self.assertIn("Added “title” on V3 at 01:00:00:10.", text)

    def test_ripple_is_one_line(self):
        after = [(i, n, t, s + (24 if i in "bcd" else 0), e + (24 if i in "bcd" else 0), o) for i, n, t, s, e, o in BASE]
        self.assertEqual(describe(snap(BASE), snap(after)), ["3 clips from 01:00:03:00 on moved by +24 frames (a ripple)."])

    def test_markers_and_timelines(self):
        before = snap(BASE, markers={"10": {"name": "Beat", "color": "Blue"}})
        after = snap(BASE, markers={"48": {"name": "Fix", "color": "Red"}},
                     timelines=[{"id": "t1", "name": "Main v2"}, {"id": "t2", "name": "Alt"}])
        lines = describe(before, after)
        self.assertIn("New timeline “Alt”.", lines)
        self.assertIn("Renamed timeline “Main” to “Main v2”.", lines)
        self.assertIn("Added red marker “Fix” at 01:00:02:00.", lines)
        self.assertIn("Removed marker “Beat” at 01:00:00:10.", lines)

    def test_switching_timeline_or_project_skips_the_clip_diff(self):
        other = snap([("z", "x", "V1", 0, 10, 0)], timeline=("t2", "Alt"))
        self.assertEqual(describe(snap(BASE), other), ["Opened timeline “Alt” (was “Main”)."])
        self.assertEqual(describe(snap(BASE), snap(BASE, project="Other")), ["Switched project from “Class” to “Other”."])

    def test_long_lists_are_capped(self):
        after = [("n%d" % i, "clip", "V2", 86400 + i, 86401 + i, 0) for i in range(40)]
        lines = describe(snap([]), snap(after))
        self.assertEqual(len(lines), 25)
        self.assertEqual(lines[-1], "…and 16 more changes.")


class SeekHelperTests(unittest.TestCase):
    def test_timecode_non_drop_and_drop_frame(self):
        self.assertEqual(timecode(86400, 24), "01:00:00:00")
        self.assertEqual(timecode(86400 + 100, 24), "01:00:04:04")
        self.assertEqual(timecode(1800, 29.97, drop_frame=True), "00;01;00;02")  # frames 0-1 skipped at 1 min
        self.assertEqual(timecode(17982, 29.97, drop_frame=True), "00;10;00;00")  # no drop on the 10th minute
        self.assertEqual(timecode(107892, 29.97, drop_frame=True), "01;00;00;00")
        self.assertEqual(timecode(1800, 29.97), "00:01:00:00")

    def test_find_clip_prefers_exact_then_video_then_earliest(self):
        fp = snap([("a1", "interview.mov", "A1", 86400, 86500, 0), ("v1", "interview.mov", "V1", 86600, 86700, 0),
                   ("v0", "interview.mov", "V2", 86500, 86600, 0), ("b", "broll_city.mov", "V1", 86700, 86800, 0)])
        item, n = find_clip(fp, "Interview.mov")
        self.assertEqual((item["id"], n), ("v0", 3))
        self.assertEqual(find_clip(fp, "city")[0]["id"], "b")
        self.assertEqual(find_clip(fp, "a1")[0]["id"], "a1")
        self.assertEqual(find_clip(fp, "nope"), (None, 0))


if __name__ == "__main__":
    unittest.main()
