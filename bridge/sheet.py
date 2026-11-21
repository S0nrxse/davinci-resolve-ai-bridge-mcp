"""Contact sheets: one tiled PNG of rendered stills with each tile's timecode burned in.

Standard library only and never prints. Labels use a small built-in bitmap font, so nothing depends on ffmpeg's
drawtext filter (many builds, Homebrew's included, lack it). ffmpeg, when given, only decodes and scales each
still to raw RGB; without it, or if it fails, the PNG is decoded here (8- and 16-bit grey, grey+alpha, RGB,
RGBA and 8-bit palette, non-interlaced) and scaled nearest-neighbour.
"""
import math
import subprocess
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

BACKGROUND = (12, 12, 12)
LABEL_STRIP = (28, 28, 28)
LABEL_TEXT = (240, 240, 240)
MISSING_TILE = (88, 88, 88)
MISSING_TEXT = (230, 230, 230)

GLYPH_W, GLYPH_H = 5, 7

_GLYPHS = {
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
    ":": (".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."),
    ";": (".....", ".##..", ".##..", ".....", ".##..", "..#..", ".#..."),
    ".": (".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    "-": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    "#": (".#.#.", ".#.#.", "#####", ".#.#.", "#####", ".#.#.", ".#.#."),
    " ": (".....",) * 7,
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
}
FONT_CHARS = frozenset(_GLYPHS)


class SheetError(Exception):
    pass


# ------------------------------------------------------------------ PNG codec


def _swar_mask(length):
    return int.from_bytes(b"\x80" * length, "big")


def _bytes_sub(row, prev):
    """Byte-wise (row - prev) mod 256, done on whole rows as big integers (PNG Up filter)."""
    n, high = len(row), _swar_mask(len(row))
    x, y = int.from_bytes(row, "big"), int.from_bytes(prev, "big")
    return (((x | high) - (y & ~high)) ^ ((x ^ ~y) & high)).to_bytes(n, "big")


def _bytes_add(row, prev):
    """Byte-wise (row + prev) mod 256 (PNG Up unfilter)."""
    n, high = len(row), _swar_mask(len(row))
    x, y = int.from_bytes(row, "big"), int.from_bytes(prev, "big")
    return (((x & ~high) + (y & ~high)) ^ ((x ^ y) & high)).to_bytes(n, "big")


def _chunk(kind, data):
    return (len(data).to_bytes(4, "big") + kind + data
            + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big"))


def encode_png(width, height, rgb, level=6):
    """An 8-bit RGB PNG from packed rgb24 rows (Up filter on every row but the first)."""
    width, height = int(width), int(height)
    stride = width * 3
    if width < 1 or height < 1 or len(rgb) != stride * height:
        raise SheetError("encode_png needs %dx%d rgb24 data (%d bytes), got %d bytes."
                         % (width, height, stride * height, len(rgb)))
    rgb = bytes(rgb)
    rows, prev = [], None
    for y in range(height):
        row = rgb[y * stride:(y + 1) * stride]
        rows.append(b"\x00" + row if prev is None else b"\x02" + _bytes_sub(row, prev))
        prev = row
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0])
    return (PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(b"".join(rows), level))
            + _chunk(b"IEND", b""))


_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def decode_png(data):
    """(width, height, rgb24 bytes) from PNG bytes. Alpha is dropped; 16-bit samples keep their high byte."""
    if not data.startswith(PNG_SIGNATURE):
        raise SheetError("Not a PNG file.")
    pos, header, palette, idat = 8, None, None, []
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = body
        elif kind == b"PLTE":
            palette = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if header is None or len(header) < 13 or not idat:
        raise SheetError("The PNG has no IHDR or image data.")
    width, height = int.from_bytes(header[0:4], "big"), int.from_bytes(header[4:8], "big")
    depth, colour, interlace = header[8], header[9], header[12]
    if colour not in _CHANNELS or interlace != 0 or depth not in (8, 16) or (colour == 3 and depth != 8):
        raise SheetError("Unsupported PNG layout (colour type %d, %d-bit, interlace %d)." % (colour, depth, interlace))
    if colour == 3 and not palette:
        raise SheetError("The palette PNG has no PLTE chunk.")
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as exc:
        raise SheetError("The PNG image data is corrupt: %s" % exc)
    channels = _CHANNELS[colour]
    bpp = channels * depth // 8
    stride = width * bpp
    if len(raw) < (stride + 1) * height:
        raise SheetError("The PNG image data is truncated.")
    prev = bytes(stride)
    out = bytearray()
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], raw[start + 1:start + 1 + stride]
        if kind == 0:
            row = bytes(line)
        elif kind == 2:
            row = _bytes_add(line, prev)
        elif kind in (1, 3, 4):
            row = bytearray(line)
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                if kind == 1:
                    row[i] = (row[i] + left) & 0xFF
                elif kind == 3:
                    row[i] = (row[i] + ((left + prev[i]) >> 1)) & 0xFF
                else:
                    up = prev[i]
                    corner = prev[i - bpp] if i >= bpp else 0
                    p = left + up - corner
                    pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                    pred = left if pa <= pb and pa <= pc else (up if pb <= pc else corner)
                    row[i] = (row[i] + pred) & 0xFF
            row = bytes(row)
        else:
            raise SheetError("The PNG uses unknown filter type %d." % kind)
        prev = row
        if depth == 16:
            row = row[0::2]
        if colour == 2:
            out += row
        elif colour == 6:
            out += bytes(b for i, b in enumerate(row) if i % 4 != 3)
        elif colour == 0:
            out += bytes(v for v in row for _ in range(3))
        elif colour == 4:
            out += bytes(v for v in row[0::2] for _ in range(3))
        else:
            out += b"".join(palette[3 * v:3 * v + 3] for v in row)
    if len(out) != width * height * 3:
        raise SheetError("The PNG palette is shorter than its indices.")
    return width, height, bytes(out)


# ------------------------------------------------------------------ pixels


def scale_nearest(width, height, rgb, new_width, new_height):
    """Nearest-neighbour resize of packed rgb24."""
    if (new_width, new_height) == (width, height):
        return bytes(rgb)
    stride = width * 3
    xs = [3 * ((x * width) // new_width) for x in range(new_width)]
    rows = []
    for y in range(new_height):
        src = (y * height) // new_height
        line = rgb[src * stride:(src + 1) * stride]
        rows.append(b"".join(line[x:x + 3] for x in xs))
    return b"".join(rows)


def fit(width, height, box_width, box_height):
    """The largest size with width/height's aspect that fits the box (at least 1x1)."""
    ratio = min(box_width / float(width), box_height / float(height))
    return max(1, min(box_width, int(round(width * ratio)))), max(1, min(box_height, int(round(height * ratio))))


class Canvas(object):
    """A packed rgb24 image to draw on; every drawing call clips to the canvas."""

    def __init__(self, width, height, colour=BACKGROUND):
        self.width, self.height = int(width), int(height)
        self.pixels = bytearray(bytes(colour) * (self.width * self.height))

    def pixel(self, x, y):
        offset = 3 * (y * self.width + x)
        return tuple(self.pixels[offset:offset + 3])

    def fill(self, x, y, width, height, colour):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.width, x + width), min(self.height, y + height)
        if x1 <= x0 or y1 <= y0:
            return
        line = bytes(colour) * (x1 - x0)
        for row in range(y0, y1):
            offset = 3 * (row * self.width + x0)
            self.pixels[offset:offset + len(line)] = line

    def blit(self, x, y, width, height, rgb):
        x0, x1 = max(0, x), min(self.width, x + width)
        if x1 <= x0:
            return
        for row in range(max(0, y), min(self.height, y + height)):
            src = 3 * ((row - y) * width + (x0 - x))
            dst = 3 * (row * self.width + x0)
            self.pixels[dst:dst + 3 * (x1 - x0)] = rgb[src:src + 3 * (x1 - x0)]

    def text(self, x, y, text, colour, scale=1):
        """Draw ``text`` with its top-left at (x, y). Characters outside the font draw as blanks."""
        scale = max(1, int(scale))
        for n, char in enumerate(str(text).upper()):
            glyph = _GLYPHS.get(char, _GLYPHS[" "])
            left = x + n * (GLYPH_W + 1) * scale
            for gy, line in enumerate(glyph):
                for gx, bit in enumerate(line):
                    if bit == "#":
                        self.fill(left + gx * scale, y + gy * scale, scale, scale, colour)

    def png(self):
        return encode_png(self.width, self.height, self.pixels)


def text_width(text, scale=1):
    count = len(str(text))
    return 0 if not count else (count * (GLYPH_W + 1) - 1) * max(1, int(scale))


def grid(count, columns=None):
    """(columns, rows) for ``count`` tiles: about square by default, never more columns than tiles."""
    count = max(1, int(count))
    columns = int(math.ceil(math.sqrt(count))) if columns is None else max(1, min(int(columns), count))
    return columns, int(math.ceil(count / float(columns)))


# ------------------------------------------------------------------ the sheet


def _ffmpeg_rgb(ffmpeg, path, width, height, timeout):
    proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path), "-vf",
                           "scale=%d:%d:flags=area" % (width, height), "-frames:v", "1", "-f", "rawvideo",
                           "-pix_fmt", "rgb24", "-"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout)
    if proc.returncode != 0 or len(proc.stdout) != width * height * 3:
        raise SheetError("ffmpeg could not decode %s" % path)
    return proc.stdout


def load_tile(path, box_width, box_height, ffmpeg=None, timeout=60):
    """(width, height, rgb24) of the still at ``path`` fitted inside the box, aspect preserved."""
    with open(str(path), "rb") as handle:
        data = handle.read(24)
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        raise SheetError("%s is not a PNG." % path)
    source_width, source_height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if not source_width or not source_height:
        raise SheetError("%s has no pixels." % path)
    width, height = fit(source_width, source_height, box_width, box_height)
    if ffmpeg:
        try:
            return width, height, _ffmpeg_rgb(ffmpeg, path, width, height, timeout)
        except (OSError, subprocess.SubprocessError, SheetError):
            pass  # decode it here instead
    with open(str(path), "rb") as handle:
        source_width, source_height, rgb = decode_png(handle.read())
    return width, height, scale_nearest(source_width, source_height, rgb, width, height)


def tile_label(timecode, timeline_frame, tile_width, scale):
    """The longest label form that fits the tile at ``scale``."""
    timecode = timecode or "--:--:--:--"
    for text in ("%s  FRAME %s" % (timecode, timeline_frame), "%s #%s" % (timecode, timeline_frame), timecode):
        if text_width(text, scale) <= tile_width - 4 * scale:
            return text
    return timecode


def compose(tiles, tile_width=480, tile_height=None, columns=None, gap=4, ffmpeg=None, timeout=60):
    """One PNG grid of ``tiles`` in the given order, left to right then top to bottom.

    Each tile is {"timeline_frame", "timecode", "path"} where path is a PNG or None for a frame that was not
    rendered (drawn as a grey tile saying NOT RENDERED). A still that cannot be decoded is drawn grey with NOT
    DECODED. Every tile gets a dark strip underneath with its timecode and absolute frame number. Returns
    {"png", "width", "height", "columns", "rows", "tile_width", "tile_height", "tiles"}.
    """
    if not tiles:
        raise SheetError("No tiles to lay out.")
    tile_width = int(tile_width)
    tile_height = int(tile_height) if tile_height else max(1, int(round(tile_width * 9 / 16.0)))
    columns, rows = grid(len(tiles), columns)
    scale = max(1, tile_width // 240)
    strip = (GLYPH_H + 4) * scale
    cell_height = tile_height + strip
    canvas = Canvas(gap + columns * (tile_width + gap), gap + rows * (cell_height + gap))
    placed = []
    for index, tile in enumerate(tiles):
        x = gap + (index % columns) * (tile_width + gap)
        y = gap + (index // columns) * (cell_height + gap)
        record = {"index": index, "timeline_frame": tile.get("timeline_frame"), "timecode": tile.get("timecode"),
                  "rendered": False}
        missing = "NOT RENDERED"
        if tile.get("path"):
            try:
                width, height, rgb = load_tile(tile["path"], tile_width, tile_height, ffmpeg, timeout)
                canvas.fill(x, y, tile_width, tile_height, (0, 0, 0))
                canvas.blit(x + (tile_width - width) // 2, y + (tile_height - height) // 2, width, height, rgb)
                record["rendered"] = True
            except (OSError, SheetError) as exc:
                missing = "NOT DECODED"
                record["error"] = str(exc)
        if not record["rendered"]:
            canvas.fill(x, y, tile_width, tile_height, MISSING_TILE)
            text_scale = scale if text_width(missing, scale) <= tile_width - 4 else 1
            canvas.text(x + (tile_width - text_width(missing, text_scale)) // 2,
                        y + (tile_height - GLYPH_H * text_scale) // 2, missing, MISSING_TEXT, text_scale)
        canvas.fill(x, y + tile_height, tile_width, strip, LABEL_STRIP)
        label = tile_label(record["timecode"], record["timeline_frame"], tile_width, scale)
        canvas.text(x + 2 * scale, y + tile_height + 2 * scale, label, LABEL_TEXT, scale)
        placed.append(record)
    return {"png": canvas.png(), "width": canvas.width, "height": canvas.height, "columns": columns, "rows": rows,
            "tile_width": tile_width, "tile_height": tile_height, "tiles": placed}
