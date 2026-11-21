"""Styled Text+ titles: colour parsing, safe-area layout, font lookup and the op against Fusion fakes.

These run against tests/fakes.py and tests/fusion_fakes.py, not a real Resolve.
"""

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fusion_fakes
from bridge import titles
from bridge.operations import OperationError, ResolveOperations
from fakes import Clip, Project, Resolve, Timeline
from fusion_fakes import Comp

TEXT_PLUS_INPUTS = (
    "StyledText", "Font", "Style", "Size", "Center", "Red1", "Green1", "Blue1", "Alpha1",
    "HorizontalLeftCenterRight", "VerticalTopCenterBottom", "Enabled2", "ElementShape2", "Level2", "Opacity2",
    "Red2", "Green2", "Blue2", "Alpha2", "ExtendHorizontal2", "ExtendVertical2", "EffectMask",
)
FAKE_FONTS = {
    "families": {
        "helveticaneue": {"family": "Helvetica Neue", "styles": {"Regular": "/f/hn.ttc", "Bold": "/f/hn.ttc",
                                                                "Light": "/f/hn.ttc"}},
        "avenirnext": {"family": "Avenir Next", "styles": {"Regular": "/f/an.ttc", "Demi Bold": "/f/an.ttc"}},
        "arial": {"family": "Arial", "styles": {"Regular": "/f/arial.ttf"}},
    },
    "aliases": {"helveticaneuelight": ("Helvetica Neue", "Light")},
    "directories": ["/f"], "files_scanned": 3, "unreadable": 0,
}


def sfnt(family, style, full=None, legacy=None):
    """A minimal TrueType file that has only a 'name' table (Windows Unicode records)."""
    names = [(1, legacy or family), (2, style), (4, full or "%s %s" % (family, style)), (16, family), (17, style)]
    strings, records = b"", []
    for name_id, text in names:
        data = text.encode("utf-16-be")
        records.append(struct.pack(">HHHHHH", 3, 1, 0x409, name_id, len(data), len(strings)))
        strings += data
    table = struct.pack(">HHH", 0, len(records), 6 + 12 * len(records)) + b"".join(records) + strings
    header = struct.pack(">IHHHH", 0x00010000, 1, 16, 0, 0)
    record = b"name" + struct.pack(">III", 0, 12 + 16, len(table))
    return header + record + table


class ColorTests(unittest.TestCase):
    def test_hex_forms(self):
        self.assertEqual(titles.parse_color("#FFFFFF"), (1.0, 1.0, 1.0, 1.0))
        self.assertEqual(titles.parse_color("#000000AA"), (0.0, 0.0, 0.0, round(0xAA / 255, 6)))
        self.assertEqual(titles.parse_color("#f00"), (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(titles.parse_color("0f08"), (0.0, 1.0, 0.0, round(0x88 / 255, 6)))
        self.assertEqual(titles.parse_color("white"), (1.0, 1.0, 1.0, 1.0))

    def test_numbers(self):
        self.assertEqual(titles.parse_color([255, 0, 0]), (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(titles.parse_color((0.5, 0.25, 0, 0.5)), (0.5, 0.25, 0.0, 0.5))

    def test_rejects_bad_values(self):
        for bad in ("#12345", "#GGGGGG", "chartreuse-ish", [1, 2], [300, 0, 0], ""):
            with self.assertRaises(titles.TitleError):
                titles.parse_color(bad)

    def test_hex_round_trip(self):
        self.assertEqual(titles.color_hex(titles.parse_color("#11223344")), "#11223344")


class LayoutTests(unittest.TestCase):
    def test_lower_third_16x9_title_safe(self):
        placed = titles.layout(1920, 1080, "lower_third", "auto", "title", 0.08, "Jane Smith")
        self.assertEqual(placed["center"], [0.1, 0.15])
        self.assertEqual((placed["h_anchor"], placed["v_anchor"]), (-1.0, 1.0))
        self.assertEqual(placed["align_used"], "left")
        self.assertEqual(placed["center_pixels"], [192.0, 918.0])
        self.assertAlmostEqual(placed["em_pixels"], 153.6)
        self.assertTrue(placed["estimated_fits_safe_area"])

    def test_lower_third_9x16_is_same_normalised_point_with_portrait_note(self):
        placed = titles.layout(1080, 1920, "lower_third", "auto", "title", 0.08, "Jane Smith")
        self.assertEqual(placed["center"], [0.1, 0.15])
        self.assertEqual(placed["center_pixels"], [108.0, 1632.0])
        self.assertEqual(placed["frame"]["orientation"], "portrait")
        # Size is relative to width, so the em is much shorter relative to a tall frame.
        self.assertAlmostEqual(placed["em_pixels"], 86.4)
        self.assertTrue(any("Vertical video" in note for note in placed["notes"]))

    def test_presets_and_safe_areas(self):
        cases = {
            ("center", "title"): ([0.5, 0.5], 0.0, 0.0),
            ("top", "action"): ([0.5, 0.95], 0.0, -1.0),
            ("bottom_center", "title"): ([0.5, 0.1], 0.0, 1.0),
            ("upper_left", "action"): ([0.05, 0.95], -1.0, -1.0),
            ("upper_right", "title"): ([0.9, 0.9], 1.0, -1.0),
            ("bottom_center", "none"): ([0.5, 0.0], 0.0, 1.0),
        }
        for (preset, safe), (center, h, v) in cases.items():
            placed = titles.layout(1920, 1080, preset, "auto", safe, 0.05, "Hi")
            self.assertEqual(placed["center"], center, (preset, safe))
            self.assertEqual((placed["h_anchor"], placed["v_anchor"]), (h, v), (preset, safe))

    def test_align_moves_lower_third_but_not_fixed_presets(self):
        self.assertEqual(titles.layout(1920, 1080, "lower_third", "right", "title", 0.05, "x")["center"], [0.9, 0.15])
        self.assertEqual(titles.layout(1920, 1080, "lower_third", "center", "title", 0.05, "x")["center"], [0.5, 0.15])
        fixed = titles.layout(1920, 1080, "center", "left", "title", 0.05, "x")
        self.assertEqual(fixed["align_used"], "center")
        self.assertTrue(any("align=left was not used" in note for note in fixed["notes"]))

    def test_custom_point(self):
        placed = titles.layout(1080, 1920, "0.3,0.7", "left", "title", 0.05, "x")
        self.assertEqual((placed["preset"], placed["center"], placed["h_anchor"]), ("custom", [0.3, 0.7], -1.0))
        with self.assertRaises(titles.TitleError):
            titles.layout(1920, 1080, "1.5,0.2", "auto", "title", 0.05, "x")

    def test_overflow_warns_with_suggested_size(self):
        placed = titles.layout(1080, 1920, "center", "auto", "title", 0.2, "A very long single line of title text")
        self.assertFalse(placed["estimated_fits_safe_area"])
        self.assertLess(placed["suggested_max_size"], 0.2)

    def test_invalid_arguments(self):
        for kwargs in ({"position": "middle_earth"}, {"safe_area": "broadcast"}, {"align": "justify"},
                       {"size": 0.9}):
            args = dict(width=1920, height=1080, position="lower_third", align="auto", safe_area="title",
                        size=0.08, text="x")
            args.update(kwargs)
            with self.assertRaises(titles.TitleError):
                titles.layout(**args)


class FontTests(unittest.TestCase):
    def setUp(self):
        titles._FONT_CACHE.clear()
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        (root / "Supplemental").mkdir()
        (root / "Demo.ttf").write_bytes(sfnt("Demo Sans", "Regular"))
        (root / "DemoBold.ttf").write_bytes(sfnt("Demo Sans", "Bold"))
        (root / "Supplemental" / "DemoLight.otf").write_bytes(
            sfnt("Demo Sans", "Light", legacy="Demo Sans Light"))
        (root / "Hidden.ttf").write_bytes(sfnt(".Demo System", "Regular"))
        (root / "Old-Style.dfont").write_bytes(b"resource fork data")
        (root / "Broken.ttf").write_bytes(b"\x00\x01")
        (root / "notes.txt").write_text("not a font")
        self.index = titles.scan_fonts([self.dir.name])

    def tearDown(self):
        titles._FONT_CACHE.clear()
        self.dir.cleanup()

    def test_scan_reads_name_tables_and_file_names(self):
        families = self.index["families"]
        self.assertEqual(sorted(families["demosans"]["styles"]), ["Bold", "Light", "Regular"])
        self.assertIn("old", families)  # .dfont falls back to the file name
        self.assertNotIn("demosystem", families)  # hidden '.' faces are skipped
        self.assertEqual(self.index["files_scanned"], 6)

    def test_lookup_family_style_alias_and_combined_name(self):
        found = titles.resolve_font("demo sans", "bold", self.index)
        self.assertEqual((found["found"], found["family"], found["style"], found["style_found"]),
                         (True, "Demo Sans", "Bold", True))
        alias = titles.resolve_font("Demo Sans Light", "", self.index)
        self.assertEqual((alias["family"], alias["style"]), ("Demo Sans", "Light"))
        combined = titles.resolve_font("Demo Sans Bold", "", self.index)
        self.assertEqual((combined["family"], combined["style"]), ("Demo Sans", "Bold"))
        plain = titles.resolve_font("Demo Sans", "", self.index)
        self.assertEqual((plain["style"], plain["style_found"]), ("Regular", None))

    def test_missing_style_reports_the_one_used(self):
        found = titles.resolve_font("Demo Sans", "Black Italic", self.index)
        self.assertEqual((found["style"], found["style_found"]), ("Regular", False))
        self.assertIn("no 'Black Italic' style", found["note"])

    def test_fallback_is_reported(self):
        found = titles.resolve_font("Nope Grotesk", "", FAKE_FONTS)
        self.assertEqual((found["found"], found["family"], found["fallback_used"]), (False, "Helvetica Neue", "Helvetica Neue"))
        self.assertIn("not installed", found["note"])
        sf = titles.resolve_font("SF Pro Display", "", FAKE_FONTS)
        self.assertIn("SF Pro ships only", sf["note"])
        none = titles.resolve_font("Nope Grotesk", "", self.index)  # no fallback family in this folder
        self.assertEqual((none["found"], none["family"], none["fallback_used"]), (False, None, None))
        self.assertIn("template font is kept", none["note"])

    def test_no_font_keeps_template(self):
        self.assertIsNone(titles.resolve_font("", "", FAKE_FONTS)["family"])

    def test_scan_is_cached(self):
        self.assertIs(titles.scan_fonts([self.dir.name]), self.index)


class TitleTimeline(Timeline):
    """Fake timeline that inserts a Fusion Text+ title at the playhead like Resolve's ordinary API."""

    def __init__(self, project, clips=None, **kwargs):
        super().__init__(project, clips=clips)
        self.tracks = super().GetTrackCount("video")
        self.land_track = None       # None: top video track
        self.land_length = None      # None: honour nothing, use 120 frames (standard still length)
        self.ripple = 0
        self.template = None
        self.fail_insert = False
        self.inserted = []

    def GetTrackCount(self, kind):
        count = super().GetTrackCount(kind)
        return max(count, self.tracks) if kind == "video" else count

    def AddTrack(self, kind, *args):
        self.tracks += 1
        return True

    def InsertFusionTitleIntoTimeline(self, name):
        if self.fail_insert or name != "Text+":
            return None
        h, m, s, f = (int(x) for x in self.current.split(":"))
        frame = ((h * 3600 + m * 60 + s) * 24 + f)
        track = self.land_track or self.GetTrackCount("video")
        for clip in self.clips:
            if self.ripple and clip.kind == "video" and clip.start >= frame:
                clip.start += self.ripple
                clip.end += self.ripple
        title = Clip(frame, frame + (self.land_length or 120), name="Text+", track=track)
        title.media = None
        comp = Comp()
        if self.template is not False:
            tool = comp.AddTool("TextPlus")
            tool.SetAttrs({"TOOLS_Name": "Template"})
            tool.SetInput("Font", "Open Sans")
            tool.SetInput("Style", "Bold")
            self.template = tool
        title.fusion = comp
        self.clips.append(title)
        self.inserted.append(title)
        return title


def make(clips=None, transport="console"):
    project = Project(clips if clips is not None else [Clip(0, 480, track=1)])
    timeline = TitleTimeline(project, clips=project.current.clips)
    project.current = timeline
    project.timelines = [timeline]
    return ResolveOperations(lambda: Resolve(project), transport=transport), timeline


class StyledTitleOpTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(fusion_fakes.INPUTS, {"TextPlus": TEXT_PLUS_INPUTS})
        patcher.start()
        self.addCleanup(patcher.stop)
        fonts = patch.object(titles, "scan_fonts", return_value=FAKE_FONTS)
        fonts.start()
        self.addCleanup(fonts.stop)

    def params(self, **extra):
        base = {"text": "Jane Smith\nDirector", "start_frame": 48, "duration_frames": 72, "font": "Helvetica Neue",
                "style": "Bold", "size": 0.06, "color": "#FFCC00", "position": "lower_third", "align": "auto",
                "safe_area": "title", "background": "#000000AA"}
        base.update(extra)
        return base

    def test_dry_run_changes_nothing(self):
        op, timeline = make()
        plan = op._op_styled_title(self.params(dry_run=True))
        self.assertTrue(plan["dry_run"])
        self.assertFalse(plan["inserted"])
        self.assertEqual((plan["track"], plan["new_track"], plan["timecode"]), ("V2", True, "00:00:02:00"))
        self.assertEqual(plan["layout"]["center"], [0.1, 0.15])
        self.assertEqual(plan["font"]["family"], "Helvetica Neue")
        inputs = {entry["input"]: entry["value"] for entry in plan["text_plus_inputs"]}
        self.assertEqual(inputs["ElementShape2"], 2.0)
        self.assertEqual(inputs["Center"], [0.1, 0.15])
        self.assertEqual((timeline.inserted, timeline.tracks), ([], 1))

    def test_inserts_on_new_track_and_reads_back(self):
        op, timeline = make()
        timeline.current = "00:00:10:00"
        result = op._op_styled_title(self.params(duration_frames=120))
        self.assertTrue(result["inserted"])
        self.assertEqual(timeline.current, "00:00:10:00")  # playhead restored
        placement = result["placement"]
        self.assertEqual(placement["actual"], {"track": "V2", "start_frame": 48, "end_frame": 168, "duration_frames": 120})
        self.assertTrue(placement["track_matched"] and placement["start_matched"] and placement["duration_matched"])
        self.assertTrue(result["verified"])
        self.assertEqual((result["text_set"], result["font_set"], result["size_set"], result["center_set"]),
                         (True, True, True, True))
        tool = timeline.template
        self.assertEqual(tool.GetInput("StyledText"), "Jane Smith\nDirector")
        self.assertEqual((tool.GetInput("Font"), tool.GetInput("Style")), ("Helvetica Neue", "Bold"))
        self.assertEqual(titles.plain_value(tool.GetInput("Center")), [0.1, 0.15])
        self.assertAlmostEqual(tool.GetInput("Green1"), 0.8)
        self.assertAlmostEqual(tool.GetInput("Alpha2"), 0xAA / 255, places=5)
        self.assertNotIn("other_clips_changed", result)

    def test_readback_mismatch_is_reported(self):
        op, timeline = make()
        original_add = fusion_fakes.Tool.SetInput

        def stubborn(tool, name, value, time=None):
            if (name, value) == ("Font", "Helvetica Neue") or name == "Size":
                return None  # Fusion accepted the call but kept the old value
            return original_add(tool, name, value, time)

        with patch.object(fusion_fakes.Tool, "SetInput", stubborn):
            result = op._op_styled_title(self.params(duration_frames=120))
        self.assertFalse(result["verified"])
        self.assertEqual((result["font_set"], result["size_set"], result["text_set"]), (False, False, True))
        self.assertEqual(result["font_actual"], "Open Sans")
        font_row = next(row for row in result["readback"] if row["input"] == "Font")
        self.assertEqual((font_row["requested"], font_row["actual"]), ("Helvetica Neue", "Open Sans"))
        self.assertTrue(any("did not keep Font, Size" in note for note in result["notes"]))

    def test_missing_inputs_read_back_as_unknown(self):
        op, timeline = make()
        with patch.dict(fusion_fakes.INPUTS, {"TextPlus": ("StyledText", "Size", "Center", "EffectMask")}):
            result = op._op_styled_title(self.params(duration_frames=120, background=None, font=""))
        self.assertIsNone(result["verified"])
        self.assertTrue(result["text_set"])
        self.assertIsNone(result["font_set"])
        self.assertTrue(any("unverified" in note for note in result["notes"]))

    def test_track_and_length_mismatch_reported_honestly(self):
        op, timeline = make()
        timeline.land_track = 1
        result = op._op_styled_title(self.params(duration_frames=72))
        placement = result["placement"]
        self.assertEqual((placement["track_matched"], placement["duration_matched"]), (False, False))
        self.assertEqual(placement["actual"]["duration_frames"], 120)
        notes = " ".join(result["notes"])
        self.assertIn("not V2", notes)
        self.assertIn("left empty", notes)
        self.assertIn("Requested 72 frames", notes)

    def test_existing_track_conflict_refuses_before_any_change(self):
        op, timeline = make()
        with self.assertRaisesRegex(OperationError, "already has clips"):
            op._op_styled_title(self.params(track=1))
        self.assertEqual((timeline.inserted, timeline.tracks), ([], 1))
        with self.assertRaisesRegex(OperationError, "V5 does not exist"):
            op._op_styled_title(self.params(track=5))

    def test_ripple_from_insert_is_reported(self):
        op, timeline = make([Clip(0, 48, track=1), Clip(48, 480, track=1, name="Later")])
        timeline.ripple = 120
        result = op._op_styled_title(self.params(duration_frames=120))
        moved = result["other_clips_changed"]
        self.assertEqual([entry["name"] for entry in moved], ["Later"])
        self.assertEqual(moved[0]["after"][2], 168)
        self.assertTrue(any("Cmd+Z" in note for note in result["notes"]))

    def test_title_without_text_plus_is_not_claimed_styled(self):
        op, timeline = make()
        timeline.template = False
        result = op._op_styled_title(self.params(duration_frames=120))
        self.assertEqual((result["text_set"], result["verified"]), (False, False))

    def test_insert_failure_reports_the_added_track(self):
        op, timeline = make()
        timeline.fail_insert = True
        timeline.current = "00:00:01:00"
        with self.assertRaisesRegex(OperationError, "empty V2 was added"):
            op._op_styled_title(self.params())
        self.assertEqual(timeline.current, "00:00:01:00")

    def test_argument_validation(self):
        op, _ = make()
        for bad, message in (({"text": " "}, "text is required"), ({"start_frame": None}, "start_frame is required"),
                             ({"duration_frames": 0}, "duration_frames"), ({"color": "#12"}, "hex colour"),
                             ({"position": "nowhere"}, "position must be"), ({"start_frame": -5}, "before the timeline start")):
            with self.assertRaisesRegex(OperationError, message):
                op._op_styled_title(self.params(**bad))

    def test_lua_transport_builds_one_script(self):
        op, timeline = make(transport="lua")
        result = op._op_styled_title(self.params(duration_frames=120))
        script = timeline.inserted[0].fusion.scripts[-1]
        self.assertIn('local aib_title = comp:FindTool("Template")', script)
        self.assertIn('aib_title.Font = "Helvetica Neue"', script)
        self.assertIn("aib_title.Center = {0.1, 0.15}", script)
        self.assertIn("aib_title.ElementShape2 = 2", script)
        self.assertIn("lua_finished", result)

    def test_handler_is_registered(self):
        op, _ = make()
        self.assertIn("styled_title", op.handlers())


if __name__ == "__main__":
    unittest.main()
