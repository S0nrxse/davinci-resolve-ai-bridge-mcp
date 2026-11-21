"""Shot / scene-change detection with ffmpeg, mapped onto Resolve timeline frames.

Runs outside Resolve on the clip's source file. Standard library only; never
prints to stdout (stdio belongs to MCP).

Primary detector: ``select='gt(scene,T)',metadata=print`` (scores 0..1, the same
scale as ``threshold``). ``scdet`` output (scores 0..100) is also understood.
"""

import re
import subprocess
from pathlib import Path

_PTS = re.compile(r"pts_time:\s*(-?[\d.]+)")
_SCORE = re.compile(r"lavfi\.scene_score=\s*([\d.]+)")
_SCDET = re.compile(r"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*(-?[\d.]+)")
_NO_VIDEO = ("does not contain any stream", "Output file does not contain any stream",
             "matches no streams", "Stream specifier", "no video")


class ShotError(RuntimeError):
    """Shot detection failed for a reason the user can act on."""


def parse_scene_log(text):
    """Return [(time_sec, score 0..1)] from metadata=print or scdet stderr."""
    cuts = []
    pending = None
    for line in text.splitlines():
        m = _SCDET.search(line)
        if m:
            cuts.append((float(m.group(2)), float(m.group(1)) / 100.0))
            continue
        m = _PTS.search(line)
        if m and "metadata" in line:
            pending = float(m.group(1))
            continue
        m = _SCORE.search(line)
        if m and pending is not None:
            cuts.append((pending, float(m.group(1))))
            pending = None
    cuts.sort()
    return cuts


def filter_cuts(cuts, min_gap_sec=0.5, max_shots=500, duration=None):
    """Drop cuts at t<=0, past the end, or closer than min_gap_sec to the previous kept cut.

    Within a cluster of close cuts the strongest one wins.
    """
    kept = []
    for t, score in sorted(cuts):
        if t <= 0 or (duration is not None and t >= duration):
            continue
        if kept and t - kept[-1][0] < min_gap_sec:
            if score > kept[-1][1]:
                kept[-1] = (t, score)
            continue
        kept.append((t, score))
    return kept[:max_shots]


def to_timeline(cuts, source):
    """Map (time_sec, score) to dicts with an absolute timeline frame clamped inside the clip."""
    fps = float(source.get("fps") or 24)
    start = int(round(float(source.get("timeline_start_frame") or 0)))
    end = source.get("timeline_end_frame")
    end = int(round(float(end))) if end is not None else start + int(round(float(source.get("duration_sec") or 0) * fps))
    out = []
    for t, score in cuts:
        frame = start + int(round(t * fps))
        frame = max(start, min(frame, max(start, end - 1)))
        out.append({"time_sec": round(t, 4), "score": round(score, 4), "timeline_frame": frame})
    return out


def detect_shots(source, ffmpeg, threshold=0.3, min_gap_sec=0.5, max_shots=500):
    """Detect hard cuts / camera changes in a clip's used source window."""
    path = source.get("file_path")
    if not path or not Path(path).is_file():
        raise ShotError("The clip's media file is offline: %s" % path)
    if not 0 < float(threshold) < 1:
        raise ShotError("threshold must be between 0 and 1 (0.3 is a good default; lower finds softer changes).")
    start = max(0.0, float(source.get("source_start_sec") or 0))
    duration = float(source.get("duration_sec") or 0)
    if duration <= 0:
        raise ShotError("The selected clip has no duration to analyze.")
    command = [ffmpeg, "-hide_banner", "-nostats", "-ss", "%.6f" % start, "-t", "%.6f" % duration,
               "-i", str(path), "-map", "0:v:0", "-an", "-sn", "-dn",
               "-vf", "scale=320:-2,select='gt(scene\\,%.4f)',metadata=print" % float(threshold),
               "-f", "null", "-"]
    timeout = max(60, int(duration * 1.5) + 60)
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ShotError("Shot detection took longer than %d s and was stopped; try a shorter range." % timeout)
    except OSError as exc:
        raise ShotError("Could not run ffmpeg: %s" % exc)
    log = result.stderr.decode(errors="replace")
    base = {"item_id": source.get("item_id"), "name": source.get("name"), "threshold": float(threshold),
            "analyzed_sec": round(duration, 3)}
    if result.returncode:
        if any(marker in log for marker in _NO_VIDEO):
            return dict(base, cuts=[], analyzed_sec=0.0, note="This clip has no video stream.")
        raise ShotError("ffmpeg could not analyze this clip: %s" % log.strip().splitlines()[-1][-300:] if log.strip() else "ffmpeg failed.")
    cuts = filter_cuts(parse_scene_log(log), min_gap_sec, max_shots, duration)
    return dict(base, cuts=to_timeline(cuts, source))
