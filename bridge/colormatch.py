"""Numeric shot matching: measure two clips' source media and solve an ASC CDL.

Runs in the MCP server process, outside Resolve. Standard library only; frames
come from ffmpeg (rawvideo rgb24 over a pipe, about 64 px wide), never from PIL.

Everything here measures SOURCE media inside each clip's used range. Existing
Resolve grades, Fusion, transforms and overlays are not part of the measurement
or the preview. Resolve is reached only through the ``call(op, params)``
function the caller passes in (the bridge transport in production).
"""

import json
import math
import os
import shutil
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp",
    ".exr", ".dpx", ".tga", ".psd", ".heic", ".heif", ".jp2", ".cin",
}
IDENTITY = {"slope": [1.0, 1.0, 1.0], "offset": [0.0, 0.0, 0.0], "power": [1.0, 1.0, 1.0], "saturation": 1.0}
# Sane ranges for an automatic match. Anything outside these is a re-light, not a shot match.
LIMITS = {"slope": (0.4, 2.5), "offset": (-0.3, 0.3), "power": (0.5, 2.0), "saturation": (0.5, 1.6)}
LUMA = (0.2126, 0.7152, 0.0722)  # Rec.709, used for the CDL saturation step.
SAMPLE_WIDTH = 64
DEFAULT_FRAMES = 10
BLACK_LEVEL = 0.02   # A pixel whose brightest channel is below this is letterbox / crushed black.
WHITE_LEVEL = 0.98   # A pixel whose darkest channel is above this is clipped white.
CHANNELS = ("r", "g", "b")
PREVIEW_HEIGHT = 270
PREVIEW_LAYOUT = ["reference (source frame)", "target before (source frame)",
                  "target after (source frame + proposed CDL)"]
PREVIEW_NOTE = ("Source frames with the proposed CDL, rendered by ffmpeg. This is NOT Resolve's render: existing "
                "node grades, Fusion, transforms and overlays on either clip are not included.")


# Bridge errors that are certain to have happened before any change.
REFUSAL_MARKERS = ("Nothing was changed", "Timeline item not found", "before editing", "no new operation started",
                   "Unknown operation")


class MatchError(RuntimeError):
    """Shot matching cannot continue for a reason the user can act on."""


# ------------------------------------------------------------------ CDL math

def apply_cdl(rgb, cdl):
    """ASC CDL on one normalized pixel: clamp(in*slope+offset)^power, then Rec.709 saturation."""
    out = []
    for value, slope, offset, power in zip(rgb, cdl["slope"], cdl["offset"], cdl["power"]):
        value = value * slope + offset
        value = 0.0 if value <= 0.0 else (1.0 if value >= 1.0 else value)
        out.append(value ** power if value > 0.0 else 0.0)
    saturation = float(cdl.get("saturation", 1.0))
    if saturation != 1.0:
        luma = LUMA[0] * out[0] + LUMA[1] * out[1] + LUMA[2] * out[2]
        out = [min(1.0, max(0.0, luma + saturation * (value - luma))) for value in out]
    return out


def apply_cdl_pixels(pixels, cdl):
    return [apply_cdl(pixel, cdl) for pixel in pixels]


def clamp_cdl(cdl):
    """Clamp every parameter to LIMITS. Returns (cdl, [names that were clamped])."""
    clamped, out = [], {}
    for key in ("slope", "offset", "power"):
        low, high = LIMITS[key]
        values = []
        for index, value in enumerate(cdl[key]):
            limited = min(high, max(low, float(value)))
            if abs(limited - float(value)) > 1e-12:
                clamped.append("%s.%s" % (key, CHANNELS[index]))
            values.append(limited)
        out[key] = values
    low, high = LIMITS["saturation"]
    saturation = float(cdl.get("saturation", 1.0))
    out["saturation"] = min(high, max(low, saturation))
    if abs(out["saturation"] - saturation) > 1e-12:
        clamped.append("saturation")
    return out, clamped


def blend_cdl(cdl, strength):
    """Interpolate every parameter toward identity: 0 is no change, 1 is the full match."""
    strength = min(1.0, max(0.0, float(strength)))
    out = {key: [IDENTITY[key][i] + (float(cdl[key][i]) - IDENTITY[key][i]) * strength for i in range(3)]
           for key in ("slope", "offset", "power")}
    out["saturation"] = 1.0 + (float(cdl.get("saturation", 1.0)) - 1.0) * strength
    return out


def cdl_strings(cdl):
    """The space-separated form set_clip_grade / SetCDL take."""
    out = {key: " ".join("%.6f" % v for v in cdl[key]) for key in ("slope", "offset", "power")}
    out["saturation"] = "%.6f" % float(cdl["saturation"])
    return out


def rounded_cdl(cdl, digits=4):
    out = {key: [round(float(v), digits) for v in cdl[key]] for key in ("slope", "offset", "power")}
    out["saturation"] = round(float(cdl["saturation"]), digits)
    return out


def is_identity(cdl, tolerance=1e-3):
    if not cdl:
        return True
    try:
        for key in ("slope", "offset", "power"):
            if any(abs(float(a) - b) > tolerance for a, b in zip(cdl[key], IDENTITY[key])):
                return False
        return abs(float(cdl.get("saturation", 1.0)) - 1.0) <= tolerance
    except (KeyError, TypeError, ValueError):
        return False


# ---------------------------------------------------------------- statistics

def _percentile(ordered, q):
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * q
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def usable_pixels(pixels):
    """Drop near-black (letterbox) and clipped-white pixels unless that would leave almost nothing."""
    kept = [p for p in pixels if max(p) >= BLACK_LEVEL and min(p) <= WHITE_LEVEL]
    return (kept, len(pixels) - len(kept)) if len(kept) >= max(16, len(pixels) * 0.05) else (list(pixels), 0)


def stats(pixels):
    """Per-channel mean and 5/50/95 percentiles plus mean chroma (max-min), in normalized code values."""
    if not pixels:
        raise MatchError("No pixels were sampled.")
    out = {"mean": [], "p05": [], "p50": [], "p95": []}
    for channel in range(3):
        values = sorted(p[channel] for p in pixels)
        out["mean"].append(sum(values) / len(values))
        out["p05"].append(_percentile(values, 0.05))
        out["p50"].append(_percentile(values, 0.50))
        out["p95"].append(_percentile(values, 0.95))
    out["saturation"] = sum(max(p) - min(p) for p in pixels) / len(pixels)
    out["pixels"] = len(pixels)
    return out


def residual(a, b):
    """How far stats ``a`` are from ``b``: RMS over channels in 0-1 code values (x255 for 8-bit steps)."""
    percentile = [a[k][c] - b[k][c] for k in ("p05", "p50", "p95") for c in range(3)]
    means = [a["mean"][c] - b["mean"][c] for c in range(3)]
    rms = math.sqrt(sum(d * d for d in percentile) / len(percentile))
    mean_rms = math.sqrt(sum(d * d for d in means) / 3)
    return {"percentile_rms": round(rms, 5), "percentile_rms_8bit": round(rms * 255, 2),
            "mean_rms": round(mean_rms, 5), "saturation_diff": round(a["saturation"] - b["saturation"], 5)}


def rounded_stats(value, digits=4):
    return {key: ([round(v, digits) for v in item] if isinstance(item, list) else
                  (round(item, digits) if isinstance(item, float) else item)) for key, item in value.items()}


# -------------------------------------------------------------------- solver

def solve_channel(target, reference):
    """Slope/offset from the 5th and 95th percentiles, power from the median.

    ``target`` and ``reference`` are (p05, p50, p95). For each candidate power
    the slope and offset that land p05 and p95 exactly are fixed, and the power
    whose median lands closest to the reference median wins.
    """
    t5, t50, t95 = target
    r5, r50, r95 = reference
    if t95 - t5 < 0.02 or r95 - r5 < 0.005:
        # A flat channel (blank frame, single color) has no contrast to match; shift the median only.
        return 1.0, r50 - t50, 1.0

    def fit(power):
        low, high = max(r5, 0.0) ** (1.0 / power), max(r95, 0.0) ** (1.0 / power)
        slope = (high - low) / (t95 - t5)
        offset = low - slope * t5
        middle = t50 * slope + offset
        middle = max(0.0, min(1.0, middle)) ** power
        return slope, offset, abs(middle - r50)

    steps = 400
    candidates = [math.exp(math.log(LIMITS["power"][0]) + (math.log(LIMITS["power"][1]) - math.log(LIMITS["power"][0]))
                           * i / steps) for i in range(steps + 1)]
    best = min(range(len(candidates)), key=lambda i: fit(candidates[i])[2])
    low, high = candidates[max(0, best - 1)], candidates[min(steps, best + 1)]
    for _ in range(40):  # Ternary refinement between the neighbouring grid points.
        a, b = low + (high - low) / 3, high - (high - low) / 3
        if fit(a)[2] <= fit(b)[2]:
            high = b
        else:
            low = a
    power = (low + high) / 2
    if abs(power - 1.0) < 1e-3 and fit(1.0)[2] <= fit(power)[2] + 1e-6:
        power = 1.0
    slope, offset, _ = fit(power)
    return slope, offset, power


def solve_cdl(target_pixels, reference_pixels, strength=1.0):
    """CDL that maps the target's percentiles onto the reference's, then clamped and blended."""
    target, target_excluded = usable_pixels(target_pixels)
    reference, reference_excluded = usable_pixels(reference_pixels)
    target_stats, reference_stats = stats(target), stats(reference)
    raw = {"slope": [], "offset": [], "power": [], "saturation": 1.0}
    power_limited = []  # The power search is bounded, so a hit on the bound is a clamp.
    for channel in range(3):
        slope, offset, power = solve_channel(
            [target_stats[k][channel] for k in ("p05", "p50", "p95")],
            [reference_stats[k][channel] for k in ("p05", "p50", "p95")])
        raw["slope"].append(slope)
        raw["offset"].append(offset)
        raw["power"].append(power)
        if min(abs(power - bound) for bound in LIMITS["power"]) < 1e-3:
            power_limited.append("power.%s" % CHANNELS[channel])
    # Saturation last: CDL saturation scales (channel - luma), so mean chroma scales by the same factor.
    graded = stats(apply_cdl_pixels(target, dict(raw, saturation=1.0)))
    raw["saturation"] = reference_stats["saturation"] / graded["saturation"] if graded["saturation"] > 1e-4 else 1.0
    solved, clamped = clamp_cdl(raw)
    clamped += power_limited
    final = blend_cdl(solved, strength)
    after = stats(apply_cdl_pixels(target, final))
    return {
        "cdl": final,
        "full_strength_cdl": solved,
        "unclamped_cdl": raw,
        "clamped": clamped,
        "strength": min(1.0, max(0.0, float(strength))),
        "reference_stats": reference_stats,
        "target_stats": target_stats,
        "predicted_target_stats": after,
        "residual_before": residual(target_stats, reference_stats),
        "residual_after_predicted": residual(after, reference_stats),
        "excluded_pixels": {"reference": reference_excluded, "target": target_excluded},
    }


# ---------------------------------------------------------------------- LUT

def write_cube(cdl, path, size=17, title="Resolve AI Bridge shot match"):
    """Write the CDL as a .cube 3D LUT (red changes fastest, as the format requires)."""
    size = int(size)
    if size < 2 or size > 65:
        raise MatchError("LUT size must be between 2 and 65.")
    lines = ['TITLE "%s"' % title.replace('"', "'"), "LUT_3D_SIZE %d" % size,
             "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    scale = size - 1
    for b in range(size):
        for g in range(size):
            for r in range(size):
                out = apply_cdl((r / scale, g / scale, b / scale), cdl)
                lines.append("%.6f %.6f %.6f" % tuple(out))
    Path(path).write_text("\n".join(lines) + "\n", encoding="ascii")
    return str(path)


# ------------------------------------------------------------------ sampling

def sample_times(start, duration, count=DEFAULT_FRAMES, still=False):
    """``count`` times spread across the used range, keeping clear of the first and last 5%."""
    if still or duration <= 0:
        return [float(start)]
    count = max(1, int(count))
    low, high = start + duration * 0.05, start + duration * 0.95
    step = (high - low) / count
    return [low + step * (i + 0.5) for i in range(count)]


def probe(ffmpeg, path):
    """Stream tags that decide the YUV matrix. Unknown fields stay None."""
    ffprobe = str(Path(ffmpeg).with_name("ffprobe")) if os.path.sep in str(ffmpeg) else None
    if not ffprobe or not os.access(ffprobe, os.X_OK):
        ffprobe = shutil.which("ffprobe")
    info = {"pix_fmt": None, "color_space": None, "color_transfer": None, "height": None}
    if not ffprobe:
        return info
    try:
        result = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "stream=pix_fmt,color_space,color_transfer,height", "-of", "json", str(path)],
                                capture_output=True, timeout=20)
        stream = (json.loads(result.stdout.decode(errors="replace") or "{}").get("streams") or [{}])[0]
        info.update({key: stream.get(key) for key in info})
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return info


def color_matrix(info, still=False):
    """swscale's untagged default is BT.601; untagged HD video is almost always BT.709."""
    pix_fmt = str(info.get("pix_fmt") or "")
    tagged = info.get("color_space") not in (None, "", "unknown", "reserved")
    if still or tagged or (pix_fmt and not pix_fmt.startswith("yuv")):
        return "auto"
    return "bt709" if int(info.get("height") or 1080) >= 720 else "bt601"


def _decode(ffmpeg, path, seconds, matrix, width=SAMPLE_WIDTH, still=False, timeout=45):
    command = [ffmpeg, "-v", "error", "-nostdin"]
    if not still:
        command += ["-ss", "%.6f" % max(0.0, seconds)]
    command += ["-i", str(path), "-frames:v", "1", "-an", "-sn",
                "-vf", "scale=%d:-2:flags=area:in_color_matrix=%s,format=rgb24" % (width, matrix),
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timed out"
    data = result.stdout
    if result.returncode or not data or len(data) % (width * 3):
        return None, (result.stderr.decode(errors="replace").strip()[-200:] or "no frame decoded")
    return data, None


def sample_source(ffmpeg, source, frames=DEFAULT_FRAMES):
    """Decode ``frames`` small frames from a media_sources entry. Returns pixels plus what was sampled."""
    path = source.get("file_path")
    if not path or not Path(path).is_file():
        raise MatchError("%s has no readable source file (offline, generated, or a compound/Fusion clip)."
                         % (source.get("name") or source.get("item_id")))
    still = Path(path).suffix.lower() in IMAGE_SUFFIXES
    info = probe(ffmpeg, path)
    matrix = color_matrix(info, still)
    start, duration = float(source.get("source_start_sec") or 0.0), float(source.get("duration_sec") or 0.0)
    times = sample_times(start, duration, frames, still)
    with ThreadPoolExecutor(max_workers=4) as pool:
        decoded = list(pool.map(lambda t: _decode(ffmpeg, path, t, matrix, still=still), times))
    pixels, used, failed = [], [], []
    for seconds, (data, error) in zip(times, decoded):
        if data is None:
            failed.append({"source_sec": round(seconds, 3), "error": error})
            continue
        used.append(round(seconds, 3))
        pixels.extend((data[i] / 255.0, data[i + 1] / 255.0, data[i + 2] / 255.0) for i in range(0, len(data), 3))
    if not pixels:
        raise MatchError("ffmpeg could not decode any frame of %s: %s" % (path, failed[:1]))
    hdr = str(info.get("color_transfer") or "") in ("smpte2084", "arib-std-b67")
    return {"pixels": pixels, "sampled_source_sec": used, "failed_frames": failed, "still": still,
            "color_matrix": matrix, "hdr": hdr, "probe": info,
            "preview_sec": used[len(used) // 2] if used else start}


# ------------------------------------------------------------------- preview

def render_preview(ffmpeg, reference, target, cube_path, out_path, height=PREVIEW_HEIGHT, timeout=60):
    """reference | target before | target after, one PNG, all from source frames."""
    def inputs(entry):
        args = [] if entry["still"] else ["-ss", "%.6f" % entry["preview_sec"]]
        return args + ["-i", str(entry["file_path"])]

    cube = str(cube_path).replace("\\", "/").replace("'", "\\'").replace(":", "\\:")
    graph = ("[0:v]scale=-2:{h}:in_color_matrix={rm},format=rgb24,setsar=1,pad=iw+8:ih:0:0:black[r];"
             "[1:v]scale=-2:{h}:in_color_matrix={tm},format=rgb24,setsar=1,split[b0][a0];"
             "[b0]pad=iw+8:ih:0:0:black[b];[a0]lut3d=file='{cube}'[a];[r][b][a]hstack=inputs=3"
             ).format(h=int(height), rm=reference["color_matrix"], tm=target["color_matrix"], cube=cube)
    command = [ffmpeg, "-v", "error", "-nostdin", "-y"] + inputs(reference) + inputs(target) + [
        "-filter_complex", graph, "-frames:v", "1", str(out_path)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "Preview rendering timed out."
    if result.returncode or not Path(out_path).is_file():
        return None, "Preview rendering failed: %s" % result.stderr.decode(errors="replace").strip()[-300:]
    return str(out_path), None


# ------------------------------------------------------------- orchestration

PLAYHEAD_TOKENS = ("playhead", "current", "under_playhead", "at_playhead")
# shot_match_sources is read-only but not (yet) on the queue's read-only list, so a client that has not inspected
# the timeline is refused; the fallback uses media_sources and get_clip_grade, which are read-only everywhere.
FALLBACK_MARKERS = ("before editing", "Unknown operation", "context changed")


def _check_video(entry, role, item_id):
    if not entry.get("file_path") or not Path(str(entry["file_path"])).is_file():
        raise MatchError("The %s clip %r has no readable source media (offline, generated, or a compound/Fusion "
                         "clip)." % (role, item_id))
    if not str(entry.get("track", "")).startswith("V"):
        raise MatchError("The %s clip %r is on %s; shot matching needs a video clip." % (role, item_id, entry.get("track")))
    return entry


def _source(call, item_id, role):
    try:
        payload = call("media_sources", {"scope": "clip", "item_id": str(item_id)})
    except Exception as exc:
        raise MatchError("The %s clip %r was not found or is not readable on the current timeline: %s"
                         % (role, item_id, exc))
    sources = payload.get("sources") or []
    if not sources:
        reason = "; ".join(s.get("reason", "") for s in payload.get("skipped") or []) or "no readable source media"
        raise MatchError("The %s clip %r has no readable source media (%s)." % (role, item_id, reason))
    return _check_video(sources[0], role, item_id)


def _grade(call, item_id):
    try:
        return call("get_clip_grade", {"item_id": item_id})
    except Exception as exc:
        return {"error": str(exc)}


def lookup(call, reference_item_id, target_item_id):
    """(reference, target, reference_grade, target_grade, ops_used). One timeline scan when the bridge allows it."""
    for role, item_id in (("reference", reference_item_id), ("target", target_item_id)):
        if not item_id or str(item_id).strip().lower() in PLAYHEAD_TOKENS:
            raise MatchError("Pass the %s clip's unique id from timeline_overview (or its V-track label); "
                             "'playhead' is ambiguous for a two-clip match." % role)
    if str(reference_item_id).strip() == str(target_item_id).strip():
        raise MatchError("The reference and target are the same clip.")
    try:
        payload = call("shot_match_sources", {"reference_item_id": str(reference_item_id),
                                              "target_item_id": str(target_item_id)})
    except Exception as exc:
        if not any(marker in str(exc) for marker in FALLBACK_MARKERS):
            raise MatchError("Could not read the reference %r and target %r: %s" % (reference_item_id, target_item_id, exc))
    else:
        reference, target = payload["reference"], payload["target"]
        return (_check_video(reference, "reference", reference_item_id), _check_video(target, "target", target_item_id),
                reference.pop("grade", None), target.pop("grade", None), "shot_match_sources")
    reference = _source(call, reference_item_id, "reference")
    target = _source(call, target_item_id, "target")
    return (reference, target, _grade(call, reference["item_id"]), _grade(call, target["item_id"]),
            "media_sources+get_clip_grade")


def grade_warnings(role, grade):
    """(warnings, graded): plain-language notes about grades the measurement cannot see."""
    warnings = []
    if not grade or grade.get("error"):
        warnings.append("Could not read the %s clip's grade (%s)." % (role, (grade or {}).get("error", "unknown")))
        return warnings, None
    nodes = int(grade.get("num_color_nodes") or 0)
    cdl = grade.get("node_1_cdl")
    if nodes > 1:
        warnings.append("The %s clip has %d color nodes. Their effect is not measured; the match uses its source "
                        "media." % (role, nodes))
    if cdl is not None and not is_identity(cdl):
        warnings.append("The %s clip's node 1 already has a non-identity CDL %s." % (role, json.dumps(cdl)))
    if cdl is None and nodes <= 1:
        warnings.append("This Resolve build did not report the %s clip's node 1 CDL (TimelineItem.GetCDL), so an "
                        "existing grade cannot be ruled out." % role)
    if int(grade.get("fusion_comp_count") or 0) > 0:
        warnings.append("The %s clip has a Fusion composition; its effect is not measured." % role)
    graded = nodes > 1 or (cdl is not None and not is_identity(cdl)) or int(grade.get("fusion_comp_count") or 0) > 0
    return warnings, graded


def match_shot(call, ffmpeg, reference_item_id, target_item_id, strength=1.0, apply=False,
               frames=DEFAULT_FRAMES, work_dir=None):
    """Measure both clips, solve a CDL, render a preview and optionally apply it.

    ``call(op, params)`` reaches the bridge. Read-only unless ``apply`` is true;
    then the only write is ``apply_shot_match`` (SetCDL on the target, read back).
    Returns ``(report, preview_png_path_or_None)``.
    """
    try:
        strength = float(strength)
    except (TypeError, ValueError):
        raise MatchError("strength must be a number from 0 to 1.")
    if not math.isfinite(strength) or not 0.0 <= strength <= 1.0:
        raise MatchError("strength must be between 0 and 1.")
    if not ffmpeg or not (os.access(str(ffmpeg), os.X_OK) or shutil.which(str(ffmpeg))):
        raise MatchError("Shot matching needs ffmpeg (Free-compatible). Install it, e.g. `brew install ffmpeg`.")
    reference, target, reference_grade, target_grade, lookup_ops = lookup(call, reference_item_id, target_item_id)
    if reference.get("item_id") == target.get("item_id"):
        raise MatchError("The reference and target are the same clip.")

    reference_sample = sample_source(ffmpeg, reference, frames)
    target_sample = sample_source(ffmpeg, target, frames)
    solution = solve_cdl(target_sample["pixels"], reference_sample["pixels"], strength)
    cdl = solution["cdl"]

    warnings, reference_graded = grade_warnings("reference", reference_grade)
    if reference_graded:
        warnings.append("The reference is graded, but the match targets its UNGRADED source look, not what the "
                        "viewer shows.")
    target_warnings, target_graded = grade_warnings("target", target_grade)
    warnings += target_warnings
    if target_graded and apply:
        warnings.append("Applying replaces the target's node 1 CDL; other nodes still apply on top, so Resolve's "
                        "result will differ from the preview.")
    if solution["clamped"]:
        warnings.append("Clamped to safe ranges: %s. The shots differ by more than a CDL should fix; expect a partial "
                        "match." % ", ".join(solution["clamped"]))
    for role, sample in (("reference", reference_sample), ("target", target_sample)):
        if sample["hdr"]:
            warnings.append("The %s source is HDR (PQ/HLG). These measurements are untone-mapped code values and "
                            "may not match how Resolve's color management shows it." % role)
        if sample["failed_frames"]:
            warnings.append("%d %s frame(s) failed to decode and were skipped." % (len(sample["failed_frames"]), role))
    if solution["residual_after_predicted"]["percentile_rms"] > 0.03:
        warnings.append("Predicted residual is still noticeable; lighting or white-balance differences beyond a CDL "
                        "remain.")

    work = Path(work_dir or Path(tempfile.gettempdir()) / "resolve-shot-match")
    work.mkdir(parents=True, exist_ok=True)
    stem = "match_%s" % uuid.uuid4().hex[:12]
    cube_path = write_cube(cdl, work / (stem + ".cube"))
    preview_path, preview_error = render_preview(
        ffmpeg, dict(reference_sample, file_path=reference["file_path"]),
        dict(target_sample, file_path=target["file_path"]), cube_path, work / (stem + ".png"))

    def describe(entry, sample):
        return {"item_id": entry.get("item_id"), "label": entry.get("label"), "name": entry.get("name"),
                "track": entry.get("track"), "file_path": entry.get("file_path"),
                "source_start_sec": entry.get("source_start_sec"), "duration_sec": entry.get("duration_sec"),
                "sampled_source_sec": sample["sampled_source_sec"], "color_matrix": sample["color_matrix"],
                "still": sample["still"]}

    report = {
        "reference": describe(reference, reference_sample),
        "target": describe(target, target_sample),
        "measured": "source media inside each clip's used range, %d frames each at %d px wide, normalized Rec.709 "
                    "code values; near-black and clipped-white pixels excluded" % (frames, SAMPLE_WIDTH),
        "reference_stats": rounded_stats(solution["reference_stats"]),
        "target_stats": rounded_stats(solution["target_stats"]),
        "predicted_target_stats": rounded_stats(solution["predicted_target_stats"]),
        "residual_before": solution["residual_before"],
        "residual_after_predicted": solution["residual_after_predicted"],
        "proposed_cdl": rounded_cdl(cdl),
        "set_clip_grade_args": dict(cdl_strings(cdl), item_id=target["item_id"], node_index=1),
        "full_strength_cdl": rounded_cdl(solution["full_strength_cdl"]),
        "unclamped_cdl": rounded_cdl(solution["unclamped_cdl"]),
        "clamped": solution["clamped"],
        "strength": solution["strength"],
        "excluded_pixels": solution["excluded_pixels"],
        "reference_grade": reference_grade,
        "target_grade": target_grade,
        "lut_path": cube_path,
        "preview": {"path": preview_path, "layout": PREVIEW_LAYOUT, "note": PREVIEW_NOTE, "error": preview_error},
        "warnings": warnings,
        "limits": [
            "Matches source media, not existing grades on either clip.",
            "If the reference is already graded, the match targets its ungraded look.",
            "A CDL is a global per-channel curve; lighting, mixed white balance and local contrast differences "
            "will not fully match.",
            "Statistics follow content: a bright whiteboard or a dark wall in one shot biases the match. Pick shots "
            "with similar framing, or lower strength.",
            "Residual 'after' numbers are predicted from the sampled source pixels, not measured from Resolve.",
        ],
        "applied": False,
        "bridge_reads": lookup_ops,
    }
    if apply:
        try:
            result = call("apply_shot_match", {
                "target_item_id": target["item_id"], "reference_item_id": reference["item_id"],
                "expected_file_path": target["file_path"], "node_index": 1,
                "slope": cdl["slope"], "offset": cdl["offset"], "power": cdl["power"],
                "saturation": cdl["saturation"]})
        except Exception as exc:
            # A refusal ("Nothing was changed") is certain; a transport failure mid-call is not.
            refused = any(marker in str(exc) for marker in REFUSAL_MARKERS)
            result = {"applied": False if refused else None, "error": str(exc),
                      "note": "Refused before any change." if refused else
                      "The apply request failed and its outcome is unknown. Check get_clip_grade on the target "
                      "(node_1_cdl) before retrying; nothing is replayed automatically."}
        report["apply_result"] = result
        report["applied"] = result.get("applied")
        report["verified"] = result.get("verified")
    else:
        report["next_step"] = ("Nothing was changed. Show the preview and numbers to the user and ask before calling "
                               "match_shot again with apply=true.")
    return report, preview_path
