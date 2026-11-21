"""Footage index: what is visible over time, using Apple Vision through the ResolveRender helper.

For each clip, frames are sampled every ``interval`` seconds and analysed on-device for on-screen
text (OCR), scene labels and faces (with head direction). The index is cached per media file and
source window, so searches after the first are instant. Standard library only; macOS only.
"""

import difflib
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from bridge import media_render

INDEX_VERSION = "footage-v1"
MAX_FRAMES_PER_CLIP = 3600
FACING_RADIANS = 0.35  # About 20 degrees of head turn or tilt.


class FootageError(RuntimeError):
    """Indexing or searching failed for a reason the user can act on."""


def _key(source, interval):
    path = Path(source["file_path"])
    stat = path.stat()
    raw = json.dumps([str(path), stat.st_size, stat.st_mtime, round(float(source.get("source_start_sec") or 0), 3),
                      round(float(source.get("duration_sec") or 0), 3), round(interval, 3), INDEX_VERSION])
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _extract(ffmpeg, source, interval, work):
    start = float(source.get("source_start_sec") or 0)
    duration = float(source.get("duration_sec") or 0)
    if duration <= 0:
        duration = interval  # Stills and unknown durations: one frame.
    count = min(MAX_FRAMES_PER_CLIP, max(1, int(duration / interval) + 1))
    command = [ffmpeg, "-v", "error", "-y", "-ss", "%.3f" % start, "-i", source["file_path"], "-t", "%.3f" % duration,
               "-an", "-vf", "fps=1/%.6f,scale='min(1280,iw)':-2" % interval, "-frames:v", str(count), "-q:v", "3",
               str(Path(work) / "f_%06d.jpg")]
    try:
        result = subprocess.run(command, capture_output=True, timeout=max(120, int(duration / 4) + 120))
    except subprocess.TimeoutExpired:
        raise FootageError("Sampling frames from %s took too long." % source.get("name"))
    files = sorted(Path(work).glob("f_*.jpg"))
    if not files:
        raise FootageError("Could not read frames from %s: %s" % (source.get("name"), result.stderr.decode(errors="replace")[-200:]))
    return [{"path": str(f), "t": round(i * interval, 3)} for i, f in enumerate(files)]


def index_source(source, home, ffmpeg, interval=1.0, ocr_level="accurate"):
    """Index one clip (cached). Returns {source fields..., interval, frames:[{t, text, labels, faces}], cached}."""
    if interval <= 0:
        raise FootageError("interval must be positive.")
    if not Path(source.get("file_path") or "").is_file():
        raise FootageError("Media is offline: %s" % source.get("file_path"))
    cache_dir = Path(home, "index")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / (_key(source, interval) + ".json")
    if cached.is_file():
        data = json.loads(cached.read_text(encoding="utf-8"))
        data["cached"] = True
        return data
    work = Path(tempfile.mkdtemp(prefix="resolve-footage-"))
    try:
        frames = _extract(ffmpeg, source, interval, work)
        out = work / "index.json"
        media_render._run_helper(home, "index", {"output": str(out), "frames": frames, "ocr_level": ocr_level},
                                 timeout=max(120, len(frames) // 2 + 120))
        analysed = json.loads(out.read_text(encoding="utf-8"))["frames"]
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for frame in analysed:
        frame.pop("path", None)
    data = {k: source.get(k) for k in ("item_id", "label", "name", "track", "file_path", "source_start_sec",
                                       "duration_sec", "timeline_start_frame", "timeline_end_frame", "fps")}
    data.update({"interval": interval, "frames": analysed})
    cached.write_text(json.dumps(data), encoding="utf-8")
    data["cached"] = False
    return data


def summary(index):
    """Compact overview of one indexed clip: most common labels, how much text and faces it has."""
    counts, text_frames, face_frames = {}, 0, 0
    for f in index["frames"]:
        for ident, _conf in f.get("labels") or []:
            counts[ident] = counts.get(ident, 0) + 1
        text_frames += 1 if f.get("text") else 0
        face_frames += 1 if f.get("faces") else 0
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:12]
    return {"item_id": index.get("item_id"), "name": index.get("name"), "frames": len(index["frames"]),
            "interval": index["interval"], "frames_with_text": text_frames, "frames_with_faces": face_frames,
            "top_labels": [k for k, _ in top], "cached": index.get("cached")}


def _norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def _text_hit(lines, query, fuzzy):
    q = _norm(query)
    if not q:
        return None
    for line in lines:
        n = _norm(line)
        if q in n:
            return line
    if fuzzy and len(q) >= 4:  # OCR misreads: compare against same-length windows of each line.
        for line in lines:
            n = _norm(line)
            size = len(q)
            for i in range(0, max(1, len(n) - size + 1), max(1, size // 4)):
                if difflib.SequenceMatcher(None, n[i:i + size], q).ratio() >= fuzzy:
                    return line
    return None


def _faces_camera(faces, min_size=0.05):
    for face in faces or []:
        if face.get("h", 0) >= min_size and abs(face.get("yaw", 9)) <= FACING_RADIANS and abs(face.get("pitch", 0)) <= FACING_RADIANS:
            return True
    return False


def search(indexes, text="", labels=(), facing_camera=False, min_faces=0, fuzzy=0.8, max_hits=100):
    """Spans where every given condition holds. Conditions: on-screen text (case-insensitive, OCR-tolerant),
    any of the scene labels, a face looking at the camera, at least ``min_faces`` faces."""
    wanted_labels = {l.lower().strip() for l in labels if l and l.strip()}
    if not (text or wanted_labels or facing_camera or min_faces):
        raise FootageError("Give text, labels, facing_camera or min_faces to search for.")
    spans = []
    for index in indexes:
        interval = float(index["interval"])
        fps = float(index.get("fps") or 24)
        origin = index.get("timeline_start_frame")
        current = None
        for frame in index["frames"]:
            evidence = []
            ok = True
            if text:
                hit = _text_hit(frame.get("text") or [], text, fuzzy)
                ok = ok and hit is not None
                if hit:
                    evidence.append(hit)
            if ok and wanted_labels:
                found = [ident for ident, _ in frame.get("labels") or [] if ident.lower() in wanted_labels
                         or any(w in ident.lower() for w in wanted_labels)]
                ok = bool(found)
                evidence += found[:2]
            if ok and facing_camera:
                ok = _faces_camera(frame.get("faces"))
                if ok:
                    evidence.append("facing camera")
            if ok and min_faces:
                ok = len(frame.get("faces") or []) >= min_faces
            t = float(frame["t"])
            if ok:
                if current and t - current["end_t"] <= interval * 1.6:
                    current["end_t"] = t
                    current["evidence"].update(evidence)
                else:
                    current = {"index": index, "start_t": t, "end_t": t, "evidence": set(evidence)}
                    spans.append(current)
    out = []
    for span in spans[:max_hits]:
        index = span["index"]
        fps = float(index.get("fps") or 24)
        origin = index.get("timeline_start_frame")
        end_t = span["end_t"] + float(index["interval"])
        entry = {"item_id": index.get("item_id"), "name": index.get("name"), "track": index.get("track"),
                 "source_start_sec": round(float(index.get("source_start_sec") or 0) + span["start_t"], 2),
                 "seconds": round(end_t - span["start_t"], 1), "evidence": sorted(span["evidence"])[:4]}
        if origin is not None and index.get("timeline_end_frame"):
            start_frame = int(origin + round(span["start_t"] * fps))
            entry["start_frame"] = start_frame
            entry["end_frame"] = int(min(index["timeline_end_frame"], origin + round(end_t * fps)))
            entry["timecode"] = media_render.frames_to_tc(start_frame, fps)
        out.append(entry)
    return out
