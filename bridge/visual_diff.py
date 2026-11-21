"""Where two versions of a timeline differ, as change points with before/after source frames.

Pure logic plus the grid renderer. Standard library only; never imports Resolve. The
operation in ``bridge/operations.py`` reads both timelines' video clips (without opening
either one) and hands plain dicts to :func:`diff`. The MCP tool then calls
:func:`render_grid` in the server process, which pulls source frames with ffmpeg and
lays them out with the contact-sheet helper in ``bridge/media_render.py``.

Model. Each timeline is flattened to its *picture*: at every frame the topmost enabled
video clip that has media wins (titles and generators have no source and are skipped).
Each run maps timeline frames to a position in a source file, in seconds. Runs of the two
versions that show the same media over overlapping source ranges are matched, and the
longest order-preserving set of matches is kept. Everything between two kept matches is a
change: original content that is not shown any more was removed, revised content that is
new was inserted, content shown in a different order was moved, and a change in empty
space is a gap that opened or closed. Frames are absolute timeline frames throughout.
"""

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

LIMITATIONS = ("Frames come from the clips' source media (ffmpeg), not from Resolve's viewer: grades, Fusion "
               "effects, transforms, titles, generators and composites are not shown. Change points follow the "
               "picture (top enabled video clip with media, skipping the bridge's caption overlays); audio-only edits appear only in structural_changes. "
               "Retimed clips are mapped linearly and may be off by a frame or two.")
MAX_PIECES = 2000


def parse_rate(raw, default=24.0):
    """Timeline frame rate from Resolve's setting text ('23.976', '29.97 DF', '25')."""
    try:
        rate = float(str(raw).replace("DF", "").strip())
    except (TypeError, ValueError):
        return float(default)
    if not rate > 0:
        return float(default)
    for nominal in (24, 30, 48, 60, 120):
        if abs(rate - nominal / 1.001) < 0.002:
            return nominal / 1.001
    return rate


def describe_length(frames, fps):
    frames = int(round(frames))
    if abs(frames) < round(fps):
        return "%d frame%s" % (abs(frames), "" if abs(frames) == 1 else "s")
    return "%.1f s" % (abs(frames) / float(fps))


# --------------------------------------------------------------- picture runs

def prepare_clip(clip, fps):
    """Add the source mapping to a raw clip dict (src_sec at clip start, seconds per timeline frame)."""
    clip = dict(clip)
    start, end = float(clip["start"]), float(clip["end"])
    src_fps = float(clip.get("src_fps") or fps) or fps
    src_start = clip.get("src_start")
    src_start = float(src_start if src_start is not None else clip.get("left_offset") or 0)
    rate = 1.0 / fps
    retimed = False
    if clip.get("still"):
        # A still has no source timeline; frames into the still stand in for source time.
        src_start = float(clip.get("left_offset") or 0)
        clip["src_sec"] = src_start / fps
    else:
        clip["src_sec"] = src_start / src_fps
        src_end = clip.get("src_end")
        if src_end is not None and end > start:
            span = (float(src_end) - src_start + 1) / src_fps
            if abs(span - (end - start) / fps) > max(2.0 / src_fps, 2.0 / fps):
                rate, retimed = span / (end - start), True
    clip["rate"] = rate
    clip["retimed"] = retimed
    return clip


def build_runs(clips, fps):
    """Flatten video clips to non-overlapping picture runs [{t0, t1, clip}], topmost enabled media clip first."""
    usable = [prepare_clip(c, fps) for c in clips
              if c.get("key") and not c.get("overlay") and c.get("enabled", True) is not False
              and float(c["end"]) > float(c["start"])]
    if not usable:
        return []
    bounds = sorted({float(c["start"]) for c in usable} | {float(c["end"]) for c in usable})
    by_track = sorted(usable, key=lambda c: (-int(c.get("track") or 1), float(c["start"])))
    runs = []
    for a, b in zip(bounds, bounds[1:]):
        top = next((c for c in by_track if float(c["start"]) <= a and float(c["end"]) >= b), None)
        if top is None:
            continue
        if runs and runs[-1]["clip"] is top and abs(runs[-1]["t1"] - a) < 1e-6:
            runs[-1]["t1"] = b
        else:
            runs.append({"t0": a, "t1": b, "clip": top})
    return runs


def source_at(run, t):
    clip = run["clip"]
    return clip["src_sec"] + (t - float(clip["start"])) * clip["rate"]


def run_at(runs, frame):
    for run in runs:
        if run["t0"] <= frame < run["t1"]:
            return run
    return None


def frame_source(runs, frame, tc=None):
    """What the source fallback would show at ``frame``: file, source second and clip."""
    frame = int(frame)
    info = {"timeline_frame": frame, "timecode": tc(frame) if tc else None}
    run = run_at(runs, frame)
    if run is None:
        info.update(gap=True, file_path=None, source_sec=None, clip_name=None)
        return info
    clip = run["clip"]
    info.update(gap=False, file_path=clip.get("file_path"), clip_name=clip.get("name"), item_id=clip.get("item_id"),
                track="V%d" % int(clip.get("track") or 1), still=bool(clip.get("still")),
                source_sec=0.0 if clip.get("still") else round(max(0.0, source_at(run, frame + 0.5)), 4))
    return info


# ------------------------------------------------------------------- matching

def match_pieces(orig_runs, rev_runs):
    """Pieces of identical source shown in both versions: {ot0, ot1, rt0, rt1, o, r}."""
    pieces = []
    by_key = {}
    for run in orig_runs:
        by_key.setdefault(run["clip"]["key"], []).append(run)
    for r in rev_runs:
        for o in by_key.get(r["clip"]["key"], ()):
            orate, rrate = o["clip"]["rate"], r["clip"]["rate"]
            if orate <= 0 or rrate <= 0:
                continue
            a = max(source_at(o, o["t0"]), source_at(r, r["t0"]))
            b = min(source_at(o, o["t1"]), source_at(r, r["t1"]))
            if (b - a) / rrate < 0.5 or (b - a) / orate < 0.5:
                continue
            pieces.append({"ot0": o["t0"] + (a - source_at(o, o["t0"])) / orate,
                           "ot1": o["t0"] + (b - source_at(o, o["t0"])) / orate,
                           "rt0": r["t0"] + (a - source_at(r, r["t0"])) / rrate,
                           "rt1": r["t0"] + (b - source_at(r, r["t0"])) / rrate, "o": o, "r": r})
    return pieces


def ordered_chain(pieces, tolerance=0.5):
    """The heaviest set of pieces in the same order in both versions (weighted longest chain)."""
    pieces = sorted(pieces, key=lambda p: (p["rt0"], p["ot0"]))
    if len(pieces) > MAX_PIECES:  # Very fragmented edits: keep the cheap greedy order.
        chain, last_o, last_r = [], float("-inf"), float("-inf")
        for p in pieces:
            if p["rt0"] >= last_r - tolerance and p["ot0"] >= last_o - tolerance:
                chain.append(p)
                last_o, last_r = p["ot1"], p["rt1"]
        return chain
    best, back = [], []
    for i, p in enumerate(pieces):
        weight = p["rt1"] - p["rt0"]
        score, link = weight, -1
        for j in range(i):
            q = pieces[j]
            if q["rt1"] <= p["rt0"] + tolerance and q["ot1"] <= p["ot0"] + tolerance and best[j] + weight > score:
                score, link = best[j] + weight, j
        best.append(score)
        back.append(link)
    if not best:
        return []
    index = max(range(len(best)), key=best.__getitem__)
    chain = []
    while index >= 0:
        chain.append(pieces[index])
        index = back[index]
    return list(reversed(chain))


def _subtract(segments, cuts):
    """Remove the ``cuts`` intervals from ``segments`` [(a, b, run)]."""
    out = list(segments)
    for c0, c1 in cuts:
        nxt = []
        for a, b, run in out:
            if c1 <= a or c0 >= b:
                nxt.append((a, b, run))
                continue
            if c0 > a:
                nxt.append((a, c0, run))
            if c1 < b:
                nxt.append((c1, b, run))
        out = nxt
    return [(a, b, run) for a, b, run in out if b - a >= 0.5]


def _content(runs, a, b):
    return [(max(a, r["t0"]), min(b, r["t1"]), r) for r in runs if r["t1"] > a and r["t0"] < b]


def _length(segments):
    return sum(b - a for a, b, _ in segments)


def _ranges(segments):
    return [[int(round(a)), int(round(b))] for a, b, _ in segments]


# --------------------------------------------------------------- change points

def change_points(orig_runs, rev_runs, orig_range, rev_range, fps):
    """Every change between kept matches, unmerged. Frames are absolute timeline frames."""
    pieces = match_pieces(orig_runs, rev_runs)
    chain = ordered_chain(pieces)
    kept = {id(p) for p in chain}
    moved = [p for p in pieces if id(p) not in kept]
    moved_o = [(p["ot0"], p["ot1"]) for p in moved]
    moved_r = [(p["rt0"], p["rt1"]) for p in moved]
    points = []
    o_start, o_end = orig_range
    r_start, r_end = rev_range
    edges = ([{"ot1": o_start, "rt1": r_start}] + chain + [{"ot0": o_end, "rt0": r_end}])
    for left, right in zip(edges, edges[1:]):
        oa, ob = left["ot1"], max(left["ot1"], right["ot0"])
        ra, rb = left["rt1"], max(left["rt1"], right["rt0"])
        o_seg, r_seg = _content(orig_runs, oa, ob), _content(rev_runs, ra, rb)
        removed, inserted = _subtract(o_seg, moved_o), _subtract(r_seg, moved_r)
        moved_away = _length(o_seg) - _length(removed)
        moved_here = _length(r_seg) - _length(inserted)
        gap_delta = ((rb - ra) - _length(r_seg)) - ((ob - oa) - _length(o_seg))
        amounts = {"removed_frames": _length(removed), "inserted_frames": _length(inserted),
                   "moved_frames": moved_here, "moved_away_frames": moved_away, "gap_delta_frames": gap_delta}
        if all(abs(v) < 0.5 for v in amounts.values()):
            continue
        if removed:
            before = removed[0][0]
        elif o_seg:
            before = o_seg[0][0]
        else:
            before = ob if ob < o_end else max(o_start, oa - 1)
        if inserted:
            after = inserted[0][0]
        elif r_seg:
            after = r_seg[0][0]
        else:
            after = rb if rb < r_end else max(r_start, ra - 1)
        point = {"original_frame": int(round(oa)), "revised_frame": int(round(ra)),
                 "original_end_frame": int(round(ob)), "revised_end_frame": int(round(rb)),
                 "before_frame": int(round(before)), "after_frame": int(round(after)),
                 "removed": _ranges(removed), "inserted": _ranges(inserted)}
        point.update({k: int(round(v)) for k, v in amounts.items()})
        names = [seg[2]["clip"].get("name") for seg in (removed or o_seg)] + \
                [seg[2]["clip"].get("name") for seg in (inserted or r_seg)]
        point["clips"] = sorted({n for n in names if n})[:4]
        points.append(point)
    seen = set()
    for p in chain:  # Same picture, different clip settings (zoom, crop, opacity...).
        o_clip, r_clip = p["o"]["clip"], p["r"]["clip"]
        pair = (id(o_clip), id(r_clip))
        if pair in seen or o_clip.get("props") is None or r_clip.get("props") is None:
            continue
        seen.add(pair)
        before_props, after_props = o_clip["props"] or {}, r_clip["props"] or {}
        changed = sorted(k for k in set(before_props) | set(after_props) if before_props.get(k) != after_props.get(k))
        if changed:
            points.append({"original_frame": int(round(p["ot0"])), "revised_frame": int(round(p["rt0"])),
                           "original_end_frame": int(round(p["ot1"])), "revised_end_frame": int(round(p["rt1"])),
                           "before_frame": int(round(p["ot0"])), "after_frame": int(round(p["rt0"])),
                           "removed": [], "inserted": [], "removed_frames": 0, "inserted_frames": 0,
                           "moved_frames": 0, "moved_away_frames": 0, "gap_delta_frames": 0,
                           "properties_changed": changed[:8], "clips": [r_clip.get("name")]})
    for point in points:
        point["kind"] = _kind(point)
        point["significance"] = _significance(point, fps)
    return sorted(points, key=lambda p: (p["revised_frame"], p["original_frame"])), len(chain), len(moved)


def _kind(point):
    if point.get("properties_changed") and not any(point[k] for k in ("removed_frames", "inserted_frames",
                                                                      "moved_frames", "moved_away_frames",
                                                                      "gap_delta_frames")):
        return "properties"
    kinds = []
    if point["removed_frames"] and point["inserted_frames"]:
        kinds.append("replaced")
    elif point["removed_frames"]:
        kinds.append("removed")
    elif point["inserted_frames"]:
        kinds.append("inserted")
    if point["moved_frames"]:
        kinds.append("moved")
    if point["moved_away_frames"]:
        kinds.append("moved_away")
    if point["gap_delta_frames"]:
        kinds.append("gap_opened" if point["gap_delta_frames"] > 0 else "gap_closed")
    if point.get("properties_changed"):
        kinds.append("properties")
    return kinds[0] if len(kinds) == 1 else "mixed"


def _significance(point, fps):
    frames = (point["removed_frames"] + point["inserted_frames"] + point["moved_frames"]
              + point["moved_away_frames"] + abs(point["gap_delta_frames"]))
    return frames + (0.25 * fps if point.get("properties_changed") else 0)


def merge_points(points, window):
    """Merge change points whose revised positions are within ``window`` frames of each other."""
    merged = []
    for point in sorted(points, key=lambda p: (p["revised_frame"], p["original_frame"])):
        last = merged[-1] if merged else None
        if last is not None and point["revised_frame"] - last["revised_end_frame"] <= window:
            lead = point if point["significance"] > last["significance"] else last
            combined = dict(last)
            for key in ("removed_frames", "inserted_frames", "moved_frames", "moved_away_frames", "gap_delta_frames",
                        "significance"):
                combined[key] = last[key] + point[key]
            combined["removed"] = last["removed"] + point["removed"]
            combined["inserted"] = last["inserted"] + point["inserted"]
            combined["revised_end_frame"] = max(last["revised_end_frame"], point["revised_end_frame"])
            combined["original_end_frame"] = max(last["original_end_frame"], point["original_end_frame"])
            combined["before_frame"], combined["after_frame"] = lead["before_frame"], lead["after_frame"]
            props = sorted(set(last.get("properties_changed") or []) | set(point.get("properties_changed") or []))
            if props:
                combined["properties_changed"] = props[:8]
            combined["clips"] = sorted(set(last.get("clips") or []) | set(point.get("clips") or []))[:4]
            combined["merged"] = last.get("merged", 1) + point.get("merged", 1)
            combined["kind"] = _kind(combined)
            merged[-1] = combined
        else:
            merged.append(dict(point))
    return merged


def caption(point, fps, tc_original, tc_revised):
    """One line, e.g. 'Removed 2.3 s at 01:00:40:18'."""
    at_o, at_r = tc_original(point["original_frame"]), tc_revised(point["revised_frame"])
    parts = []
    if point["removed_frames"] and point["inserted_frames"]:
        parts.append("Replaced %s with %s at %s" % (describe_length(point["removed_frames"], fps),
                                                    describe_length(point["inserted_frames"], fps), at_o))
    elif point["removed_frames"]:
        parts.append("Removed %s at %s" % (describe_length(point["removed_frames"], fps), at_o))
    elif point["inserted_frames"]:
        parts.append("Inserted %s at %s" % (describe_length(point["inserted_frames"], fps), at_r))
    if point["moved_frames"]:
        parts.append("Moved %s here at %s" % (describe_length(point["moved_frames"], fps), at_r))
    if point["moved_away_frames"]:
        parts.append("%s moved away from %s" % (describe_length(point["moved_away_frames"], fps), at_o))
    if point["gap_delta_frames"] > 0:
        parts.append("Opened a %s gap at %s" % (describe_length(point["gap_delta_frames"], fps), at_r))
    elif point["gap_delta_frames"] < 0:
        parts.append("Closed a %s gap at %s" % (describe_length(point["gap_delta_frames"], fps), at_o))
    if point.get("properties_changed"):
        parts.append("Changed %s at %s" % (", ".join(point["properties_changed"][:3]), at_r))
    text = parts[0] if parts else "Changed at %s" % at_r
    if len(parts) > 1:
        text += " (+%d more)" % (len(parts) - 1)
    return text


def diff(orig_clips, rev_clips, orig_fps, rev_fps, orig_range, rev_range, max_rows=8, merge_seconds=1.0,
         tc_original=None, tc_revised=None):
    """Change points between two timelines' video clips, most significant first-cut to ``max_rows``.

    clips: [{track, start, end, key (media id), name, item_id, enabled, file_path, src_fps, src_start, src_end,
    left_offset, still, props}]. Ranges are (start_frame, end_frame) of each timeline.
    """
    tc_original = tc_original or (lambda f: "f%d" % f)
    tc_revised = tc_revised or (lambda f: "f%d" % f)
    fps = float(rev_fps or orig_fps or 24.0)
    orig_runs, rev_runs = build_runs(orig_clips, float(orig_fps)), build_runs(rev_clips, float(rev_fps))
    raw, kept, moved = change_points(orig_runs, rev_runs, orig_range, rev_range, fps)
    merged = merge_points(raw, int(round(max(0.0, float(merge_seconds)) * fps)))
    max_rows = max(1, min(int(max_rows), 16))
    chosen = sorted(sorted(merged, key=lambda p: -p["significance"])[:max_rows],
                    key=lambda p: (p["revised_frame"], p["original_frame"]))
    changes = []
    for point in merged:
        entry = dict(point)
        entry["caption"] = caption(point, fps, tc_original, tc_revised)
        entry["original_timecode"] = tc_original(point["original_frame"])
        entry["revised_timecode"] = tc_revised(point["revised_frame"])
        entry["significance"] = round(point["significance"], 2)
        changes.append(entry)
    rows = []
    for n, point in enumerate(chosen, 1):
        rows.append({"n": n, "kind": point["kind"], "caption": caption(point, fps, tc_original, tc_revised),
                     "before": frame_source(orig_runs, point["before_frame"], tc_original),
                     "after": frame_source(rev_runs, point["after_frame"], tc_revised)})
    retimed = sorted({r["clip"].get("name") for r in orig_runs + rev_runs if r["clip"].get("retimed")} - {None})
    summary = {"original_frames": int(orig_range[1] - orig_range[0]), "revised_frames": int(rev_range[1] - rev_range[0]),
               "length_change_frames": int((rev_range[1] - rev_range[0]) - (orig_range[1] - orig_range[0])),
               "removed_frames": sum(p["removed_frames"] for p in raw),
               "inserted_frames": sum(p["inserted_frames"] for p in raw),
               "moved_frames": sum(p["moved_frames"] for p in raw),
               "change_points": len(merged), "shown": len(rows), "omitted": max(0, len(merged) - len(rows)),
               "matched_pieces": kept, "moved_pieces": moved}
    summary["length_change"] = ("%s%s" % ("+" if summary["length_change_frames"] > 0 else "-" if summary["length_change_frames"] < 0 else "",
                                          describe_length(summary["length_change_frames"], fps)))
    if retimed:
        summary["retimed_clips"] = retimed[:10]
    overlays = sum(1 for c in list(orig_clips) + list(rev_clips) if c.get("overlay"))
    if overlays:
        summary["skipped_caption_overlays"] = overlays
    return {"changes": changes, "rows": rows, "summary": summary}


# ---------------------------------------------------------------- grid image

def _grab(ffmpeg, side, image, cell_width):
    path = side.get("file_path")
    if side.get("gap") or not path or not Path(path).is_file():
        return False
    command = [ffmpeg, "-v", "error", "-y", "-ss", "%.4f" % float(side.get("source_sec") or 0), "-i", str(path),
               "-frames:v", "1", "-vf", "scale=%d:-2" % cell_width, "-q:v", "3", str(image)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and image.is_file() and image.stat().st_size > 0


def _label(side):
    if side.get("gap"):
        return "%s · gap (no picture)" % (side.get("timecode") or side.get("timeline_frame"))
    return " · ".join(str(x) for x in (side.get("timecode"), side.get("track"), side.get("clip_name")) if x)


def grid_spec(rows, images, output, cell_width=400):
    """Contact-sheet spec: two columns per row, before then after."""
    cells = []
    for row in rows:
        for side in ("before", "after"):
            data = row[side]
            title = row["caption"] if side == "before" else (
                "After: " + ("gap" if data.get("gap") else (data.get("clip_name") or "")))
            cells.append({"image": images.get((row["n"], side)), "number": "%d %s" % (row["n"], side.upper()),
                          "title": title, "subtitle": "%s  %s" % (side.upper(), _label(data))})
    return {"output": str(output), "columns": 2, "cell_width": int(cell_width), "background": "#121212",
            "cells": cells}


def render_grid(rows, home, ffmpeg, out_dir=None, cell_width=400, run_helper=None):
    """Pull each before/after source frame and draw one labelled grid. Returns {path, width, height, frames}."""
    if not rows:
        raise ValueError("No change points to draw.")
    if not ffmpeg:
        raise RuntimeError("The visual diff needs ffmpeg to read source frames (brew install ffmpeg).")
    if run_helper is None:
        from bridge import media_render
        run_helper = media_render._run_helper
    out_dir = Path(out_dir or Path(home) / "renders")
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="resolve-vdiff-"))
    images, frames = {}, []
    try:
        for row in rows:
            for side in ("before", "after"):
                image = work / ("%02d-%s.jpg" % (row["n"], side))
                ok = _grab(ffmpeg, row[side], image, cell_width)
                if ok:
                    images[(row["n"], side)] = str(image)
                frames.append({"n": row["n"], "side": side, "decoded": ok,
                               "reason": None if ok else ("gap" if row[side].get("gap") else "offline or undecodable")})
        output = out_dir / ("visual-diff-%s.jpg" % time.strftime("%Y%m%d-%H%M%S"))
        result = run_helper(home, "sheet", grid_spec(rows, images, output, cell_width), 120)
        return {"path": result.get("output", str(output)), "width": result.get("width"),
                "height": result.get("height"), "frames": frames}
    finally:
        shutil.rmtree(work, ignore_errors=True)

