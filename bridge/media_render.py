"""Labelled contact sheets and animated word captions, rendered natively on macOS.

The Homebrew ffmpeg used by the bridge has no drawtext/libass, so text is drawn by
a small Swift helper (``bridge/render/ResolveRender.swift``) compiled on first use
into ``<bridge home>/bin/ResolveRender``. ffmpeg is only used to pull still frames.
Runs in the MCP server process, outside Resolve. Standard library only.
"""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "render" / "ResolveRender.swift"
MAX_FFMPEG_CALLS = 200
STYLES = ("pop", "karaoke", "minimal")
POSITIONS = ("bottom", "center", "top")


class RenderError(RuntimeError):
    """Rendering failed for a reason the user can act on."""


# ------------------------------------------------------------------- helper

def helper_path(home):
    """Compile the Swift helper into <home>/bin on first use or when its source changed."""
    if sys.platform != "darwin":
        raise RenderError("Contact sheets and animated captions need macOS (the helper uses CoreText and AVFoundation).")
    xcrun = shutil.which("xcrun")
    if not xcrun:
        raise RenderError("The Xcode command line tools are missing. Run `xcode-select --install`, then try again.")
    if not SOURCE.is_file():
        raise RenderError("The render helper source is missing: %s" % SOURCE)
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    bin_dir = Path(home) / "bin"
    binary = bin_dir / "ResolveRender"
    stamp = bin_dir / "ResolveRender.sha256"
    try:
        if binary.is_file() and stamp.read_text(encoding="utf-8").strip() == digest:
            return str(binary)
    except OSError:
        pass
    bin_dir.mkdir(parents=True, exist_ok=True)
    arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
    temporary = bin_dir / (".ResolveRender-%s" % uuid.uuid4().hex[:8])
    try:
        result = subprocess.run([xcrun, "swiftc", "-O", "-target", "%s-apple-macos13.0" % arch,
                                 "-o", str(temporary), str(SOURCE)], capture_output=True, timeout=600)
        if result.returncode or not temporary.is_file():
            raise RenderError("Could not compile the render helper: %s" % result.stderr.decode(errors="replace")[-500:])
        os.replace(temporary, binary)
        stamp_tmp = bin_dir / (".sha-%s" % uuid.uuid4().hex[:8])
        stamp_tmp.write_text(digest, encoding="utf-8")
        os.replace(stamp_tmp, stamp)
    except subprocess.TimeoutExpired:
        raise RenderError("Compiling the render helper timed out.")
    finally:
        temporary.unlink(missing_ok=True)
    return str(binary)


def _run_helper(home, command, spec, timeout):
    binary = helper_path(home)
    work = Path(tempfile.mkdtemp(prefix="resolve-render-"))
    try:
        spec_path = work / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        try:
            result = subprocess.run([binary, command, str(spec_path)], capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RenderError("Rendering took longer than %d s and was stopped." % timeout)
        try:
            payload = json.loads(result.stdout.decode(errors="replace").strip().splitlines()[-1])
        except (ValueError, IndexError):
            payload = {"ok": False, "error": result.stderr.decode(errors="replace")[-400:] or "no output"}
        if result.returncode or not payload.get("ok"):
            raise RenderError("Render helper failed: %s" % payload.get("error"))
        return payload
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------- helpers

def frames_to_tc(frame, fps):
    rate = max(1, round(fps))
    frame = max(0, int(round(frame)))
    return "%02d:%02d:%02d:%02d" % (frame // (3600 * rate), frame // (60 * rate) % 60, frame // rate % 60, frame % rate)


def sample_times(start, duration, count):
    """``count`` evenly spaced times inside [start, start+duration], avoiding the first/last 5%."""
    if duration <= 0 or count <= 0:
        return []
    lo, hi = start + duration * 0.05, start + duration * 0.95
    step = (hi - lo) / count
    return [lo + step * (i + 0.5) for i in range(count)]


# ------------------------------------------------------------- contact sheets

def contact_sheets(sources, home, ffmpeg, frames_per_clip=4, max_cells=24, columns=None, cell_width=320, out_dir=None):
    """Grids of numbered stills from each source so a vision model can scan many clips at once.

    sources: [{item_id, label, name, track, file_path, source_start_sec, duration_sec,
               timeline_start_frame (None for media pool clips), fps}].
    Returns a list of sheets: [{"path", "width", "height", "cells": [{"n", "item_id", "label", "name", "track",
    "timeline_frame", "timecode", "source_sec"}]}]. Numbers run 1..N across sheets. The first sheet also carries
    "skipped": [{"item_id", "name", "reason"}] for offline/undecodable sources or the frame budget.
    """
    if not ffmpeg:
        raise RenderError("Contact sheets need ffmpeg to read frames (brew install ffmpeg).")
    frames_per_clip = max(1, min(int(frames_per_clip), 12))
    max_cells = max(1, min(int(max_cells), 48))
    cell_width = max(120, min(int(cell_width), 640))
    if columns is None:
        columns = frames_per_clip if 2 <= frames_per_clip <= 6 else 4  # one clip per row when it fits
    columns = max(1, min(int(columns), 8))
    max_cells = max(columns, max_cells - max_cells % columns)

    sources = list(sources or [])
    skipped = []
    per_clip = frames_per_clip
    if len(sources) * per_clip > MAX_FFMPEG_CALLS:
        per_clip = max(1, MAX_FFMPEG_CALLS // max(1, len(sources)))
    if len(sources) > MAX_FFMPEG_CALLS:
        for src in sources[MAX_FFMPEG_CALLS:]:
            skipped.append({"item_id": src.get("item_id"), "name": src.get("name"), "reason": "Over the 200-frame budget."})
        sources = sources[:MAX_FFMPEG_CALLS]

    out_dir = Path(out_dir or Path(home) / "renders")
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="resolve-sheet-"))
    cells = []
    try:
        for src in sources:
            path = src.get("file_path")
            if not path or not Path(path).is_file():
                skipped.append({"item_id": src.get("item_id"), "name": src.get("name"), "reason": "Media is offline."})
                continue
            fps = float(src.get("fps") or 24)
            start = float(src.get("source_start_sec") or 0)
            got = 0
            for t in sample_times(start, float(src.get("duration_sec") or 0), per_clip):
                image = work / ("%04d.jpg" % len(cells))
                command = [ffmpeg, "-v", "error", "-y", "-ss", "%.3f" % t, "-i", str(path), "-frames:v", "1",
                           "-vf", "scale=%d:-2" % cell_width, "-q:v", "3", str(image)]
                try:
                    result = subprocess.run(command, capture_output=True, timeout=30)
                except subprocess.TimeoutExpired:
                    continue
                if result.returncode or not image.is_file() or not image.stat().st_size:
                    continue
                origin = src.get("timeline_start_frame")
                frame = None if origin is None else int(round(float(origin) + (t - start) * fps))
                cells.append({"image": str(image), "item_id": src.get("item_id"), "label": src.get("label"),
                              "name": src.get("name") or Path(path).name, "track": src.get("track"),
                              "timeline_frame": frame,
                              "timecode": frames_to_tc(frame if frame is not None else t * fps, fps),
                              "source_sec": round(t, 3)})
                got += 1
            if not got:
                skipped.append({"item_id": src.get("item_id"), "name": src.get("name"),
                                "reason": "No frames could be decoded (audio-only or unsupported)."})
        if not cells:
            raise RenderError("No frames could be read from these clips. %s" % "; ".join(
                "%s: %s" % (s.get("name"), s["reason"]) for s in skipped[:5]))
        sheets = []
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for k in range(0, len(cells), max_cells):
            chunk = cells[k:k + max_cells]
            for i, cell in enumerate(chunk):
                cell["n"] = k + i + 1
            spec = {"output": str(out_dir / ("sheet-%s-%d.jpg" % (stamp, k // max_cells + 1))), "columns": columns,
                    "cell_width": cell_width, "background": "#121212",
                    "cells": [{"image": c["image"], "number": c["n"], "title": c["name"],
                               "subtitle": " · ".join(x for x in (c.get("track") or c.get("label"), c["timecode"]) if x)}
                              for c in chunk]}
            result = _run_helper(home, "sheet", spec, timeout=120)
            sheets.append({"path": result["output"], "width": result.get("width"), "height": result.get("height"),
                           "cells": [{key: c.get(key) for key in ("n", "item_id", "label", "name", "track",
                                                                  "timeline_frame", "timecode", "source_sec")} for c in chunk]})
        sheets[0]["skipped"] = skipped
        return sheets
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ captions

def caption_cues(words, fps, max_words=4, max_chars=28, pause_seconds=0.5, min_seconds=0.4):
    """Group words (absolute frames) into short, non-overlapping cues for animated captions."""
    cues, current = [], []
    pause = pause_seconds * fps

    def flush():
        if current:
            cues.append(list(current))
            current.clear()

    for w in words:
        text = str(w.get("word", "")).strip()
        if not text:
            continue
        word = {"text": text, "start": int(w["start_frame"]), "end": int(max(w["end_frame"], w["start_frame"] + 1))}
        if current:
            joined = len(" ".join(x["text"] for x in current)) + 1 + len(text)
            if (len(current) >= max_words or joined > max_chars or word["start"] - current[-1]["end"] > pause
                    or re.search(r"[.!?]$", current[-1]["text"])):
                flush()
        current.append(word)
    flush()
    out = []
    minimum = int(round(min_seconds * fps))
    for i, group in enumerate(cues):
        start, end = group[0]["start"], max(group[-1]["end"], group[0]["start"] + minimum)
        if i + 1 < len(cues):
            end = min(end, cues[i + 1][0]["start"])
        if end > start:
            out.append({"start": start, "end": end, "words": group})
    return out


def render_captions(words, fps, width, height, start_frame, end_frame, out_path, home, style="pop",
                    position="bottom", max_words=4, max_chars=28, uppercase=False, font=None):
    """Render word-by-word animated captions as a transparent ProRes 4444 .mov.

    words: [{"word", "start_frame", "end_frame"}] in absolute timeline frames. The movie starts at
    ``start_frame`` (place it there on the timeline). Returns {"path", "start_frame", "end_frame",
    "duration_frames", "cues", "style"}.
    """
    fps = float(fps)
    if not fps > 0:
        raise RenderError("fps must be positive.")
    width, height = int(width), int(height)
    if not (16 <= width <= 8192 and 16 <= height <= 8192):
        raise RenderError("Caption size %dx%d is out of range." % (width, height))
    if style not in STYLES:
        raise RenderError("style must be one of %s." % ", ".join(STYLES))
    if position not in POSITIONS:
        raise RenderError("position must be one of %s." % ", ".join(POSITIONS))
    words = [w for w in (words or []) if str(w.get("word", "")).strip()]
    if not words:
        raise RenderError("No words to caption; transcribe first.")
    start_frame = int(start_frame)
    cues = caption_cues(words, fps, max(1, int(max_words)), max(8, int(max_chars)))
    if end_frame is None:
        end_frame = (cues[-1]["end"] if cues else start_frame) + int(round(fps * 0.5))
    end_frame = int(end_frame)
    if end_frame <= start_frame:
        raise RenderError("end_frame must be after start_frame.")
    relative = []
    for cue in cues:
        a, b = max(cue["start"], start_frame), min(cue["end"], end_frame)
        if b <= a:
            continue
        relative.append({"start": a - start_frame, "end": b - start_frame,
                         "words": [{"text": w["text"], "start": w["start"] - start_frame, "end": w["end"] - start_frame}
                                   for w in cue["words"]]})
    if not relative:
        raise RenderError("No words fall between start_frame and end_frame.")
    out_path = str(out_path)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    spec = {"output": out_path, "width": width, "height": height, "fps": fps, "duration_frames": end_frame - start_frame,
            "cues": relative, "style": style, "position": position, "uppercase": bool(uppercase),
            "font_size": round(height * 0.07, 1), "max_width_ratio": 0.8,
            "colors": {"text": "#FFFFFF", "highlight": "#FFD43B", "outline": "#000000"}}
    if font:
        spec["font"] = font
    duration_sec = (end_frame - start_frame) / fps
    _run_helper(home, "captions", spec, timeout=int(max(120, duration_sec * 2)))
    return {"path": out_path, "start_frame": start_frame, "end_frame": end_frame,
            "duration_frames": end_frame - start_frame, "cues": len(relative), "style": style}
