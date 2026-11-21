"""Contact sheets for render_frames: a pure-Python PNG codec, bitmap-font labels and the tiled grid."""
import base64
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # fakes, when run as tests.test_contact_sheet

from bridge import qc, sheet
import test_render_frames as trf  # module import, so its test classes are not collected here a second time
from test_render_frames import FFMPEG, FrameProject, Harness, TL_START, _has_encoder, fake_extract


def png_bytes(width, height, colour_type, depth, samples, filter_type=0, palette=None):
    """A PNG built by hand from per-pixel sample tuples, every row using ``filter_type`` (0, 1 or 4 here)."""
    def chunk(kind, data):
        return len(data).to_bytes(4, "big") + kind + data + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")
    size = depth // 8
    rows, prev = [], None
    for y in range(height):
        line = b"".join(b"".join(v.to_bytes(size, "big") for v in samples[y * width + x]) for x in range(width))
        bpp = len(samples[0]) * size
        if filter_type == 0:
            rows.append(b"\x00" + line)
        elif filter_type == 1:
            rows.append(b"\x01" + bytes((line[i] - (line[i - bpp] if i >= bpp else 0)) & 0xFF
                                        for i in range(len(line))))
        else:
            up = prev or bytes(len(line))
            out = bytearray()
            for i in range(len(line)):
                a = line[i - bpp] if i >= bpp else 0
                b, c = up[i], (up[i - bpp] if i >= bpp else 0)
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                out.append((line[i] - (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 0xFF)
            rows.append(b"\x04" + bytes(out))
        prev = line
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([depth, colour_type, 0, 0, 0])
    body = chunk(b"IHDR", ihdr) + (chunk(b"PLTE", palette) if palette else b"")
    return sheet.PNG_SIGNATURE + body + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")


def gradient(width, height):
    return bytes(v for y in range(height) for x in range(width) for v in ((x * 37) & 255, (y * 91) & 255, (x * y) & 255))


class CodecTests(unittest.TestCase):
    def test_encode_decode_round_trip(self):
        for width, height in ((1, 1), (7, 5), (64, 33)):
            rgb = gradient(width, height)
            data = sheet.encode_png(width, height, rgb)
            self.assertEqual(data[:8], sheet.PNG_SIGNATURE)
            self.assertEqual((int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")), (width, height))
            self.assertEqual(sheet.decode_png(data), (width, height, rgb))

    def test_up_filter_helpers_wrap_per_byte(self):
        row, prev = bytes([0, 1, 255, 128, 3]), bytes([1, 255, 1, 128, 200])
        diff = sheet._bytes_sub(row, prev)
        self.assertEqual(diff, bytes((a - b) & 255 for a, b in zip(row, prev)))
        self.assertEqual(sheet._bytes_add(diff, prev), row)

    def test_decodes_the_layouts_ffmpeg_and_others_write(self):
        pixels = [(10 * i, 255 - 10 * i, (40 * i) & 255) for i in range(12)]  # 4x3
        expected = bytes(v for p in pixels for v in p)
        for filter_type in (0, 1, 4):
            with self.subTest(filter_type=filter_type):
                self.assertEqual(sheet.decode_png(png_bytes(4, 3, 2, 8, pixels, filter_type))[2], expected)
        rgba = [p + (7,) for p in pixels]
        self.assertEqual(sheet.decode_png(png_bytes(4, 3, 6, 8, rgba, 4))[2], expected)
        wide = [tuple(v * 257 for v in p) for p in pixels]
        self.assertEqual(sheet.decode_png(png_bytes(4, 3, 2, 16, wide, 1))[2], expected)
        grey = [(i * 20,) for i in range(12)]
        self.assertEqual(sheet.decode_png(png_bytes(4, 3, 0, 8, grey))[2], bytes(i * 20 for i in range(12) for _ in "rgb"))
        palette = bytes([1, 2, 3, 200, 100, 50])
        indexed = [(i % 2,) for i in range(12)]
        self.assertEqual(sheet.decode_png(png_bytes(4, 3, 3, 8, indexed, palette=palette))[2],
                         b"".join(palette[3 * (i % 2):3 * (i % 2) + 3] for i in range(12)))

    def test_bad_pngs_raise_sheet_errors(self):
        with self.assertRaises(sheet.SheetError):
            sheet.decode_png(b"GIF89a")
        good = sheet.encode_png(2, 2, bytes(12))
        with self.assertRaises(sheet.SheetError):
            sheet.decode_png(good[:33])  # header only, no image data
        with self.assertRaises(sheet.SheetError):
            sheet.encode_png(2, 2, bytes(11))


class DrawingTests(unittest.TestCase):
    def test_glyph_pixels_land_where_the_font_says(self):
        canvas = sheet.Canvas(40, 20, (0, 0, 0))
        canvas.text(3, 4, "1:", (255, 255, 255), scale=2)
        ink = {(x, y) for y in range(20) for x in range(40) if canvas.pixel(x, y) == (255, 255, 255)}
        expected = set()
        for n, char in enumerate("1:"):
            for gy, line in enumerate(sheet._GLYPHS[char]):
                for gx, bit in enumerate(line):
                    if bit == "#":
                        left, top = 3 + n * 12 + gx * 2, 4 + gy * 2
                        expected |= {(left + dx, top + dy) for dx in (0, 1) for dy in (0, 1)}
        self.assertEqual(ink, expected)
        self.assertIn((7, 4), ink)  # top of the 1's stem: glyph column 2, row 0
        self.assertNotIn((3, 4), ink)
        self.assertEqual(sheet.text_width("1:", 2), 22)

    def test_text_clips_at_the_canvas_edge_and_covers_the_needed_characters(self):
        canvas = sheet.Canvas(8, 8)
        canvas.text(5, 5, "88", (255, 0, 0), scale=3)  # must not raise or wrap
        self.assertEqual(canvas.pixel(0, 0), sheet.BACKGROUND)
        for text in ("01:00:01:06  FRAME 86430", "01:00:00;02 #12", "--:--:--:--", "NOT RENDERED", "NOT DECODED"):
            self.assertTrue(set(text) <= sheet.FONT_CHARS, text)

    def test_nearest_scale_and_fit(self):
        rgb = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 9, 9, 9])  # 2x2
        self.assertEqual(sheet.scale_nearest(2, 2, rgb, 4, 2)[:12], bytes([255, 0, 0] * 2 + [0, 255, 0] * 2))
        self.assertEqual(sheet.fit(1920, 1080, 480, 270), (480, 270))
        self.assertEqual(sheet.fit(1080, 1920, 480, 270), (152, 270))


def tiles_for(count, tmp, missing=()):
    tiles = []
    for n in range(count):
        path = None
        if n not in missing:
            path = Path(tmp) / ("t%d.png" % n)
            path.write_bytes(sheet.encode_png(32, 18, bytes([20 * n, 100, 200]) * (32 * 18)))
        tiles.append({"timeline_frame": 86400 + n, "timecode": "01:00:00:%02d" % n, "path": path})
    return tiles


class GridTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_layouts_for_one_five_and_ten_tiles(self):
        self.assertEqual(sheet.grid(1), (1, 1))
        self.assertEqual(sheet.grid(5), (3, 2))
        self.assertEqual(sheet.grid(10), (4, 3))
        self.assertEqual(sheet.grid(5, columns=5), (5, 1))
        self.assertEqual(sheet.grid(5, columns=9), (5, 1))
        self.assertEqual(sheet.grid(10, columns=3), (3, 4))
        for count, (columns, rows) in ((1, (1, 1)), (5, (3, 2)), (10, (4, 3))):
            with self.subTest(count=count):
                built = sheet.compose(tiles_for(count, self.tmp.name), tile_width=120, tile_height=68, gap=4)
                strip = (sheet.GLYPH_H + 4) * 1
                self.assertEqual((built["columns"], built["rows"]), (columns, rows))
                self.assertEqual((built["width"], built["height"]),
                                 (4 + columns * 124, 4 + rows * (68 + strip + 4)))
                self.assertEqual([t["index"] for t in built["tiles"]], list(range(count)))
                self.assertEqual([t["timeline_frame"] for t in built["tiles"]], [86400 + n for n in range(count)])
                width, height, rgb = sheet.decode_png(built["png"])
                self.assertEqual((width, height), (built["width"], built["height"]))
                last = count - 1  # centre of the last tile shows that tile's colour (red = 20 * n)
                cx = 4 + (last % columns) * 124 + 60
                cy = 4 + (last // columns) * (68 + strip + 4) + 34
                offset = 3 * (cy * width + cx)
                self.assertEqual(tuple(rgb[offset:offset + 3]), (20 * last, 100, 200))

    def test_failed_frame_gets_a_grey_labelled_tile(self):
        tiles = tiles_for(2, self.tmp.name, missing={1})
        tiles[1]["timecode"] = None
        built = sheet.compose(tiles, tile_width=200, tile_height=112, gap=4)
        self.assertEqual([t["rendered"] for t in built["tiles"]], [True, False])
        width, _, rgb = sheet.decode_png(built["png"])
        canvas = sheet.Canvas(width, built["height"])
        canvas.pixels[:] = rgb
        left = 4 + 204
        self.assertEqual(canvas.pixel(left + 2, 4 + 2), sheet.MISSING_TILE)
        ink = [x for x in range(left, left + 200) for y in range(4, 4 + 112) if canvas.pixel(x, y) == sheet.MISSING_TEXT]
        self.assertTrue(ink)  # NOT RENDERED is drawn in the tile
        label = [canvas.pixel(x, y) for x in range(left, left + 200) for y in range(4 + 112, 4 + 112 + 11)]
        self.assertIn(sheet.LABEL_TEXT, label)  # the label strip still carries --:--:--:-- and the frame

    def test_undecodable_still_says_not_decoded(self):
        tiles = tiles_for(1, self.tmp.name)
        tiles[0]["path"].write_bytes(sheet.PNG_SIGNATURE + b"\x00\x00\x00\x0dIHDR" + bytes(17))
        built = sheet.compose(tiles, tile_width=120)
        self.assertFalse(built["tiles"][0]["rendered"])
        self.assertTrue(built["tiles"][0]["error"])

    def test_labels_shorten_to_fit_narrow_tiles(self):
        self.assertEqual(sheet.tile_label("01:00:00:05", 86405, 480, 2), "01:00:00:05  FRAME 86405")
        self.assertEqual(sheet.tile_label("01:00:00:05", 86405, 120, 1), "01:00:00:05 #86405")
        self.assertEqual(sheet.tile_label("01:00:00:05", 86405, 96, 1), "01:00:00:05")


class RenderFramesSheetTests(Harness):
    def test_sheet_replaces_per_frame_images(self):
        self.ops_for(FrameProject())
        result = self.run_frames(frames=[TL_START, TL_START + 7, TL_START + 9, TL_START + 30, TL_START + 31],
                                 extract=fake_extract(fail={7}), sheet=True, tile_width=200)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["extracted"], 4)
        contact = result["sheet"]
        self.assertEqual((contact["columns"], contact["rows"]), (3, 2))
        self.assertEqual(base64.b64decode(contact["png_base64"])[:8], sheet.PNG_SIGNATURE)
        self.assertEqual(sheet.decode_png(base64.b64decode(contact["png_base64"]))[:2],
                         (contact["width"], contact["height"]))
        self.assertEqual([t["timeline_frame"] for t in contact["tiles"]],
                         [TL_START, TL_START + 7, TL_START + 9, TL_START + 30, TL_START + 31])
        self.assertEqual(contact["tiles"][3]["timecode"], "01:00:01:06")
        self.assertEqual([t["rendered"] for t in contact["tiles"]], [True, False, True, True, True])
        for frame in result["frames"]:
            self.assertNotIn("png_base64", frame)
        self.assertIn("frame 7", result["frames"][1]["error"])
        self.assertIn("contact sheet", result["note"])
        self.assert_deliver_restored()
        self.assertEqual(list(self.check.iterdir()), [])

    def test_keep_frames_and_default_off(self):
        self.ops_for(FrameProject())
        kept = self.run_frames(frames=[TL_START, TL_START + 1], sheet=True, keep_frames=True, columns=1)
        self.assertEqual((kept["sheet"]["columns"], kept["sheet"]["rows"]), (1, 2))
        self.assertTrue(all(f["png_base64"] for f in kept["frames"]))
        plain = self.run_frames(frames=[TL_START])
        self.assertNotIn("sheet", plain)
        self.assertTrue(plain["frames"][0]["png_base64"])

    def test_sheet_failure_falls_back_to_frames(self):
        self.ops_for(FrameProject())
        with mock.patch.object(sheet, "compose", side_effect=sheet.SheetError("boom")):
            result = self.run_frames(frames=[TL_START], sheet=True)
        self.assertNotIn("sheet", result)
        self.assertIn("boom", result["sheet_error"])
        self.assertTrue(result["frames"][0]["png_base64"])

    def test_no_sheet_when_nothing_was_extracted(self):
        self.ops_for(FrameProject())
        result = self.run_frames(frames=[TL_START], extract=fake_extract(fail={0}), sheet=True)
        self.assertFalse(result["ok"])
        self.assertNotIn("sheet", result)

    def test_bad_sheet_options_are_refused_before_rendering(self):
        def call(*_):
            raise AssertionError("no bridge call expected")
        for kwargs in ({"tile_width": 10}, {"tile_width": "big"}, {"columns": 0}, {"columns": 1.5}, {"columns": True}):
            result = qc.render_frames(call, "ffmpeg", "/nonexistent", frames=[1], sheet=True, **kwargs)
            self.assertFalse(result["ok"], kwargs)
            self.assertTrue(result["error"])


@unittest.skipUnless(_has_encoder(), "ffmpeg with geq and libx264 is needed")
class FfmpegSheetTests(trf.FfmpegFrameTests):
    """Real extracted frames from the solid-colour master, tiled; frame n has red = 4 * n."""

    # Only the master clip and render fake are shared; the inherited tests already run in test_render_frames.
    test_each_png_is_exactly_the_requested_frame = None
    test_frame_missing_from_a_short_file_is_an_error = None

    def test_tile_centres_show_their_frames(self):
        self.ops_for(FrameProject(length=self.LENGTH, render=self.render))
        wanted = [0, 5, 12, 30, 59]
        result = qc.render_frames(self.call, FFMPEG, self.check, frames=[TL_START + n for n in wanted], width=100,
                                  sleep=lambda _: None, sheet=True, tile_width=120)
        self.assertTrue(result["ok"], result)
        contact = result["sheet"]
        self.assertEqual((contact["columns"], contact["rows"], contact["tile_width"]), (3, 2, 120))
        self.assertEqual(contact["tile_height"], 67)  # 100x56 stills, scaled to 120 wide
        width, height, rgb = sheet.decode_png(base64.b64decode(contact["png_base64"]))
        strip = sheet.GLYPH_H + 4
        for tile, n in zip(contact["tiles"], wanted):
            self.assertTrue(tile["rendered"])
            cx = 4 + (tile["index"] % 3) * 124 + 60
            cy = 4 + (tile["index"] // 3) * (67 + strip + 4) + 33
            red, green, blue = rgb[3 * (cy * width + cx):3 * (cy * width + cx) + 3]
            self.assertEqual(int(round(red / float(self.STEP))), n, (n, red, green, blue))
            self.assertLessEqual(abs(green - 128), 3)
        self.assertEqual(list(self.check.iterdir()), [])
        # The sheet decodes with ffmpeg too (a real PNG, not just one this module can read).
        path = Path(self.tmp.name) / "sheet.png"
        path.write_bytes(base64.b64decode(contact["png_base64"]))
        out = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path), "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True, check=True, timeout=60).stdout
        self.assertEqual(out, rgb)


if __name__ == "__main__":
    unittest.main()
