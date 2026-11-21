"""Pure helpers for styled Text+ titles: colours, safe-area layout and installed fonts.

Standard library only and never imports Resolve. ``ResolveOperations._op_styled_title``
uses these to build the exact Text+ input values, then reads them back from Fusion.

Text+ facts used here come from Resolve's own Developer/Fusion/FusionToolReference.md
(21.2): ``Center`` is normalised with (0, 0) at the bottom-left, ``Size`` is relative to
the image *width*, ``HorizontalLeftCenterRight`` is -1/0/1 for left/centre/right and
``VerticalTopCenterBottom`` is -1/0/1 for top/centre/bottom. Shading element 2 can be
turned into a "Border Fill" (``ElementShape2`` = 2) to draw a box behind the text.
"""

import os
import re
import struct
from pathlib import Path

SAFE_AREAS = {"title": 0.80, "action": 0.90, "none": 1.00}
POSITIONS = ("lower_third", "center", "top", "bottom_center", "upper_left", "upper_right")
ALIGNS = ("auto", "left", "center", "right")
H_ANCHOR = {"left": -1.0, "center": 0.0, "right": 1.0}
V_ANCHOR = {"top": -1.0, "middle": 0.0, "bottom": 1.0}
# preset -> (horizontal zone or None to follow align, vertical zone)
PRESETS = {
    "lower_third": (None, "lower_third"),
    "center": ("center", "middle"),
    "top": ("center", "top"),
    "bottom_center": ("center", "bottom"),
    "upper_left": ("left", "top"),
    "upper_right": ("right", "top"),
}
LOWER_THIRD_LIFT = 0.05      # lower thirds sit this far (of frame height) above the safe bottom edge
LINE_HEIGHT_EM = 1.2         # estimate only: Text+ line spacing 1 is roughly 1.2 em
AVERAGE_GLYPH_EM = 0.55      # estimate only: average advance width of a Latin glyph
FALLBACK_FONTS = ("Helvetica Neue", "Helvetica", "Arial", "Open Sans")
FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc", ".dfont")
NAMED_COLORS = {
    "white": "#FFFFFF", "black": "#000000", "red": "#FF0000", "green": "#00FF00", "blue": "#0000FF",
    "yellow": "#FFFF00", "cyan": "#00FFFF", "magenta": "#FF00FF", "gray": "#808080", "grey": "#808080",
    "transparent": "#00000000",
}
# Shading element 2 as a box behind the text. Padding is in Text+ Extend units; not tuned live.
BACKGROUND_PAD_X = 0.2
BACKGROUND_PAD_Y = 0.12


class TitleError(ValueError):
    """A title argument is invalid; the message says how to fix it."""


# ----------------------------------------------------------------- colours

def parse_color(value, what="color"):
    """'#RGB', '#RGBA', '#RRGGBB', '#RRGGBBAA', a few names, or 3-4 numbers (0-1 or 0-255) -> (r, g, b, a)."""
    if isinstance(value, (list, tuple)):
        if len(value) not in (3, 4) or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            raise TitleError("%s must have 3 or 4 numbers." % what)
        numbers = [float(v) for v in value]
        scale = 255.0 if any(v > 1.0 for v in numbers) else 1.0
        if any(v < 0 or v > 255 for v in numbers):
            raise TitleError("%s numbers must be between 0 and 1, or 0 and 255." % what)
        rgba = [v / scale for v in numbers] + ([1.0] if len(numbers) == 3 else [])
        return tuple(round(v, 6) for v in rgba)
    text = str(value or "").strip().lower()
    text = NAMED_COLORS.get(text, text).lower()
    match = re.fullmatch(r"#?([0-9a-f]{3,8})", text)
    if not match or len(match.group(1)) not in (3, 4, 6, 8):
        raise TitleError("%s must be a hex colour such as #FFFFFF, #FFFFFFCC or #FFF; got %r." % (what, value))
    digits = match.group(1)
    if len(digits) in (3, 4):
        digits = "".join(c * 2 for c in digits)
    if len(digits) == 6:
        digits += "ff"
    return tuple(round(int(digits[i:i + 2], 16) / 255.0, 6) for i in (0, 2, 4, 6))


def color_hex(rgba):
    return "#" + "".join("%02X" % int(round(max(0.0, min(1.0, c)) * 255)) for c in rgba)


# ------------------------------------------------------------------ layout

def _norm(text):
    return re.sub(r"[\s_\-]+", "", str(text or "").lower())


def safe_box(safe_area="title"):
    key = str(safe_area or "title").strip().lower()
    if key not in SAFE_AREAS:
        raise TitleError("safe_area must be one of %s." % ", ".join(SAFE_AREAS))
    margin = round((1.0 - SAFE_AREAS[key]) / 2.0, 6)
    return {"name": key, "left": margin, "right": round(1 - margin, 6), "bottom": margin, "top": round(1 - margin, 6)}


def parse_position(position):
    """A preset name, or a custom [x, y] / 'x,y' in normalised Text+ coordinates."""
    if isinstance(position, (list, tuple)) and len(position) == 2:
        point = [float(position[0]), float(position[1])]
    else:
        text = str(position or "lower_third").strip().lower().replace(" ", "_").replace("-", "_")
        if text in PRESETS:
            return text, None
        parts = text.replace("_", "").split(",")
        try:
            point = [float(parts[0]), float(parts[1])] if len(parts) == 2 else None
        except ValueError:
            point = None
        if point is None:
            raise TitleError("position must be one of %s, or 'x,y' in 0..1 (0,0 is bottom-left)." % ", ".join(POSITIONS))
    if not all(0.0 <= v <= 1.0 for v in point):
        raise TitleError("A custom position must be inside the frame: x and y between 0 and 1.")
    return "custom", point


def layout(width, height, position="lower_third", align="auto", safe_area="title", size=0.08, text=""):
    """Text+ Center and anchors for a preset inside a safe area, plus an estimated text box.

    Everything is normalised to the frame (0..1, origin bottom-left). The text box estimate assumes
    average Latin glyph widths; it is a guide for warnings, not a measurement of the rendered text.
    """
    width, height = int(width), int(height)
    if width < 16 or height < 16:
        raise TitleError("Timeline resolution %dx%d is not usable." % (width, height))
    size = float(size)
    if not 0.001 <= size <= 0.5:
        raise TitleError("size is Text+ Size (relative to frame width); use 0.001 to 0.5, e.g. 0.05 to 0.1.")
    align_key = str(align or "auto").strip().lower()
    if align_key not in ALIGNS:
        raise TitleError("align must be one of %s." % ", ".join(ALIGNS))
    box = safe_box(safe_area)
    preset, point = parse_position(position)
    notes = []
    if preset == "custom":
        h_zone = "center" if align_key == "auto" else align_key
        v_zone = "middle"
        x, y = point
    else:
        h_zone, v_zone = PRESETS[preset]
        if h_zone is None:
            h_zone = "left" if align_key == "auto" else align_key
        elif align_key not in ("auto", h_zone):
            notes.append("position %s fixes the horizontal placement to %s; align=%s was not used."
                         % (preset, h_zone, align_key))
        x = {"left": box["left"], "center": 0.5, "right": box["right"]}[h_zone]
        y = {"bottom": box["bottom"], "lower_third": box["bottom"] + LOWER_THIRD_LIFT,
             "middle": 0.5, "top": box["top"]}[v_zone]
    v_key = {"lower_third": "bottom"}.get(v_zone, v_zone)

    lines = str(text or "").split("\n") if text else [""]
    em_px = size * width
    est_w = max(len(line) for line in lines) * AVERAGE_GLYPH_EM * em_px / width
    est_h = len(lines) * LINE_HEIGHT_EM * em_px / height
    left = {"left": x, "center": x - est_w / 2, "right": x - est_w}[h_zone]
    bottom = {"bottom": y, "middle": y - est_h / 2, "top": y - est_h}[v_key]
    estimate = {"left": round(left, 4), "right": round(left + est_w, 4),
                "bottom": round(bottom, 4), "top": round(bottom + est_h, 4)}
    fits = (estimate["left"] >= box["left"] - 1e-6 and estimate["right"] <= box["right"] + 1e-6
            and estimate["bottom"] >= box["bottom"] - 1e-6 and estimate["top"] <= box["top"] + 1e-6)
    result = {
        "preset": preset, "frame": {"width": width, "height": height,
                                    "orientation": "portrait" if height > width else "landscape" if width > height else "square"},
        "safe_area": box, "center": [round(x, 6), round(y, 6)],
        "center_pixels": [round(x * width, 1), round((1 - y) * height, 1)],
        "align_used": h_zone, "vertical_anchor": v_key,
        "h_anchor": H_ANCHOR[h_zone], "v_anchor": V_ANCHOR[v_key],
        "size": size, "em_pixels": round(em_px, 1),
        "estimated_box": estimate, "estimated_fits_safe_area": fits,
    }
    if not fits and str(text or "").strip():
        usable_w = box["right"] - box["left"]
        scale = min(usable_w / est_w if est_w else 1.0, (box["top"] - box["bottom"]) / est_h if est_h else 1.0)
        result["suggested_max_size"] = round(size * min(1.0, scale), 4)
        notes.append("The text may run outside the %s-safe area (estimate). Try size <= %.3f or add line breaks."
                     % (box["name"], result["suggested_max_size"]))
    if result["frame"]["orientation"] == "portrait" and v_zone in ("lower_third", "bottom"):
        notes.append("Vertical video: platform buttons and captions often cover the bottom fifth; "
                     "consider position='center' or a custom 'x,y'.")
    if notes:
        result["notes"] = notes
    return result


# ------------------------------------------------------------------- fonts

def _font_names(path, limit=64):
    """[(family, style, full name, legacy family)] read from the OpenType 'name' table of each face."""
    faces = []
    with open(path, "rb") as handle:
        head = handle.read(12)
        if len(head) < 12:
            return faces
        if head[:4] == b"ttcf":
            count = min(struct.unpack(">I", head[8:12])[0], limit)
            raw = handle.read(4 * count)
            offsets = list(struct.unpack(">%dI" % count, raw)) if len(raw) == 4 * count else []
        else:
            offsets = [0]
        for offset in offsets:
            handle.seek(offset)
            header = handle.read(12)
            if len(header) < 12:
                continue
            tables = struct.unpack(">H", header[4:6])[0]
            directory = handle.read(16 * min(tables, 256))
            name_at = None
            for i in range(0, len(directory) - 15, 16):
                if directory[i:i + 4] == b"name":
                    name_at, name_len = struct.unpack(">II", directory[i + 8:i + 16])
                    break
            if name_at is None:
                continue
            handle.seek(name_at)
            table = handle.read(min(name_len, 1 << 20))
            if len(table) < 6:
                continue
            _, records, storage = struct.unpack(">HHH", table[:6])
            found = {}
            for r in range(records):
                at = 6 + 12 * r
                if at + 12 > len(table):
                    break
                platform, encoding, language, name_id, length, start = struct.unpack(">HHHHHH", table[at:at + 12])
                if name_id not in (1, 2, 4, 16, 17):
                    continue
                data = table[storage + start:storage + start + length]
                if platform == 3 and language in (0x409, 0):
                    rank, text = 0, data.decode("utf-16-be", "replace")
                elif platform == 0:
                    rank, text = 1, data.decode("utf-16-be", "replace")
                elif platform == 1 and language == 0:
                    rank, text = 2, data.decode("mac_roman", "replace")
                else:
                    continue
                if name_id not in found or rank < found[name_id][0]:
                    found[name_id] = (rank, text.strip("\x00 ").strip())
            get = lambda key: found.get(key, (9, ""))[1]
            family = get(16) or get(1)
            style = get(17) or get(2) or "Regular"
            if family:
                faces.append((family, style, get(4), get(1)))
    return faces


def font_directories():
    homes = {os.path.expanduser("~")}
    try:
        import pwd  # The real home, even when a sandboxed host points HOME into a container.
        homes.add(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        pass
    dirs = ["/System/Library/Fonts", "/Library/Fonts"] + sorted(str(Path(h) / "Library" / "Fonts") for h in homes)
    return [d for d in dirs if os.path.isdir(d)]


_FONT_CACHE = {}


def scan_fonts(directories=None, max_files=6000):
    """Index installed fonts: {normalised family: {"family", "styles": {style: file}}} plus aliases.

    Families come from each font's name table (typographic family first), with the file name as a
    fallback for files that cannot be parsed (for example .dfont). Hidden system faces ('.SF ...') are skipped.
    """
    directories = list(directories) if directories is not None else font_directories()
    stamp = []
    for directory in directories:
        try:
            stamp.append((directory, os.stat(directory).st_mtime))
        except OSError:
            stamp.append((directory, None))
    key = tuple(stamp)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    families, aliases, scanned, unreadable = {}, {}, 0, 0

    def add(family, style, path):
        if not family or family.startswith("."):
            return
        entry = families.setdefault(_norm(family), {"family": family, "styles": {}})
        entry["styles"].setdefault(style or "Regular", path)

    for directory in directories:
        for root, _dirs, files in os.walk(directory):
            for name in sorted(files):
                if not name.lower().endswith(FONT_SUFFIXES):
                    continue
                if scanned >= max_files:
                    break
                scanned += 1
                path = os.path.join(root, name)
                try:
                    faces = [] if name.lower().endswith(".dfont") else _font_names(path)
                except (OSError, struct.error, ValueError):
                    faces = []
                    unreadable += 1
                if not faces:
                    stem = Path(name).stem
                    family, _, style = stem.partition("-")
                    add(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", family), style or "Regular", path)
                    continue
                for family, style, full, legacy in faces:
                    add(family, style, path)
                    for alias in (full, legacy):
                        if alias and _norm(alias) != _norm(family):
                            aliases.setdefault(_norm(alias), (family, style if alias == full else None))
    index = {"families": families, "aliases": aliases, "directories": directories,
             "files_scanned": scanned, "unreadable": unreadable}
    _FONT_CACHE.clear()
    _FONT_CACHE[key] = index
    return index


def _pick_style(styles, wanted):
    if wanted:
        for style in styles:
            if _norm(style) == _norm(wanted):
                return style, True
    for preferred in ("Regular", "Roman", "Book", "Medium"):
        for style in styles:
            if _norm(style) == _norm(preferred):
                return style, not wanted
    return (sorted(styles)[0] if styles else "Regular"), not wanted


def resolve_font(font="", style="", index=None):
    """Match a requested family (and style) against installed fonts; choose and report a fallback.

    Returns {"requested", "requested_style", "found", "family", "style", "style_found", "file",
    "fallback_used", "note"}. ``family`` is None when no font was requested, which keeps the Text+
    template's own font.
    """
    font, style = str(font or "").strip(), str(style or "").strip()
    report = {"requested": font or None, "requested_style": style or None, "found": None, "family": None,
              "style": style or None, "style_found": None, "file": None, "fallback_used": None}
    if not font:
        report["note"] = "No font requested; the Text+ template keeps its own font (read back below)."
        return report
    index = index or scan_fonts()
    families = index["families"]
    entry = families.get(_norm(font))
    implied_style = None
    if entry is None and _norm(font) in index["aliases"]:
        family, implied_style = index["aliases"][_norm(font)]
        entry = families.get(_norm(family))
    if entry is None:  # "Family Style" written as one string
        words = font.split()
        for cut in range(len(words) - 1, 0, -1):
            candidate = families.get(_norm(" ".join(words[:cut])))
            if candidate is not None:
                entry, implied_style = candidate, " ".join(words[cut:])
                break
    report["found"] = entry is not None
    if entry is None:
        for fallback in FALLBACK_FONTS:
            entry = families.get(_norm(fallback))
            if entry is not None:
                report["fallback_used"] = entry["family"]
                break
    if entry is None:
        report["note"] = ("'%s' is not installed and no fallback (%s) was found; the Text+ template font is kept."
                          % (font, ", ".join(FALLBACK_FONTS)))
        return report
    chosen, matched = _pick_style(entry["styles"], style or implied_style)
    report.update(family=entry["family"], style=chosen, file=entry["styles"].get(chosen),
                  style_found=matched if (style or implied_style) else None,
                  available_styles=sorted(entry["styles"])[:40])
    if report["fallback_used"]:
        report["note"] = "'%s' is not installed on this Mac; used %s %s instead." % (font, entry["family"], chosen)
        if _norm(font).startswith(("sfpro", "sanfrancisco", "sfcompact")):
            report["note"] += (" SF Pro ships only as the hidden macOS system UI font; install SF Pro from "
                               "developer.apple.com/fonts to use it by name.")
    elif (style or implied_style) and not matched:
        report["note"] = "%s has no '%s' style; used %s." % (entry["family"], style or implied_style, chosen)
    return report


# -------------------------------------------------------------- Text+ inputs

def text_plus_inputs(text, font_report, size, color, placement, background=None):
    """Ordered [(input id, value)] for a Text+ tool. Values are str, float or (x, y)."""
    inputs = [("StyledText", str(text))]
    if font_report.get("family"):
        inputs.append(("Font", font_report["family"]))
        if font_report.get("style"):
            inputs.append(("Style", font_report["style"]))
    r, g, b, a = color
    inputs += [("Size", float(size)), ("Red1", r), ("Green1", g), ("Blue1", b), ("Alpha1", a),
               ("HorizontalLeftCenterRight", placement["h_anchor"]),
               ("VerticalTopCenterBottom", placement["v_anchor"]),
               ("Center", (float(placement["center"][0]), float(placement["center"][1])))]
    if background is not None:
        br, bg, bb, ba = background
        inputs += [("Enabled2", 1.0), ("ElementShape2", 2.0), ("Level2", 0.0), ("Opacity2", 1.0),
                   ("Red2", br), ("Green2", bg), ("Blue2", bb), ("Alpha2", ba),
                   ("ExtendHorizontal2", BACKGROUND_PAD_X), ("ExtendVertical2", BACKGROUND_PAD_Y)]
    return inputs


def readback_matches(expected, actual, tolerance=1e-4):
    """True, False, or None when Fusion returned nothing to compare."""
    if actual is None:
        return None
    try:
        if isinstance(expected, str):
            return str(actual) == expected
        if isinstance(expected, tuple):
            if isinstance(actual, dict):
                actual = [actual.get(i, actual.get(str(i))) for i in (1, 2)]
            actual = list(actual)
            return len(actual) >= 2 and all(abs(float(a) - float(e)) <= tolerance for a, e in zip(actual, expected))
        return abs(float(actual) - float(expected)) <= tolerance
    except (TypeError, ValueError):
        return False


def plain_value(value):
    if isinstance(value, dict):
        return [value.get(i, value.get(str(i))) for i in (1, 2)]
    if isinstance(value, tuple):
        return list(value)
    return value
