"""Footage index search: OCR text (with typos), labels, faces, span merging and timeline mapping."""
import unittest

from bridge import footage


def index(frames, origin=1000, fps=24, interval=1.0):
    return {"item_id": "c1", "name": "screen", "track": "V1", "fps": fps, "interval": interval,
            "source_start_sec": 5.0, "timeline_start_frame": origin, "timeline_end_frame": origin + 24 * 60, "frames": frames}


FRAMES = [
    {"t": 0, "text": ["Finder", "Desktop"], "labels": [["screenshot", 0.8]], "faces": []},
    {"t": 1, "text": ["Visual Studio Code", "main.py"], "labels": [["screenshot", 0.8]], "faces": []},
    {"t": 2, "text": ["Visual Studio Code", "TypeError: bad thing"], "labels": [["screenshot", 0.8]], "faces": []},
    {"t": 3, "text": ["Finder"], "labels": [["people", 0.7]], "faces": [{"h": 0.3, "yaw": 0.05, "pitch": 0.1}]},
    {"t": 4, "text": [], "labels": [["people", 0.7]], "faces": [{"h": 0.3, "yaw": 0.9, "pitch": 0.0}]},
]


class FootageSearchTests(unittest.TestCase):
    def test_text_spans_merge_and_map_to_timeline(self):
        hits = footage.search([index(FRAMES)], text="visual studio code")
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0]["start_frame"], hits[0]["end_frame"]), (1024, 1072))
        self.assertEqual(hits[0]["seconds"], 2.0)
        self.assertEqual(hits[0]["source_start_sec"], 6.0)

    def test_ocr_typos_are_tolerated(self):
        hits = footage.search([index(FRAMES)], text="TypeErorr")
        self.assertEqual(hits[0]["start_frame"], 1048)

    def test_facing_camera_uses_head_direction(self):
        hits = footage.search([index(FRAMES)], facing_camera=True)
        self.assertEqual([h["start_frame"] for h in hits], [1072])

    def test_conditions_combine(self):
        self.assertEqual(footage.search([index(FRAMES)], text="Finder", labels=["people"])[0]["start_frame"], 1072)

    def test_media_pool_clip_reports_source_seconds_only(self):
        pool = index(FRAMES)
        pool["timeline_start_frame"] = None
        hit = footage.search([pool], text="main.py")[0]
        self.assertNotIn("start_frame", hit)
        self.assertEqual(hit["source_start_sec"], 6.0)

    def test_needs_a_condition(self):
        with self.assertRaises(footage.FootageError):
            footage.search([index(FRAMES)])

    def test_summary_lists_top_labels(self):
        s = footage.summary(index(FRAMES))
        self.assertEqual(s["top_labels"][0], "screenshot")
        self.assertEqual((s["frames_with_text"], s["frames_with_faces"]), (4, 2))


if __name__ == "__main__":
    unittest.main()
