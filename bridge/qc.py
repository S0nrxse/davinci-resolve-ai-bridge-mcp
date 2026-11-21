"""Render check: scan a rendered timeline preview for problems stills and transcripts cannot show.

Pure logic plus ffmpeg subprocesses. Standard library only: never import Resolve, ``mcp`` or
``fusionscript`` here, and never print to stdout (stdio is MCP JSON-RPC).

Picture (one decode): black segments (blackdetect), frozen picture (freezedetect) and shot cuts
(scene score), which give flash frames: shots under 3 frames between two longer shots.
Audio (one decode): integrated loudness, LRA and true peak (ebur128), short-term loudness jumps,
samples at about 0 dBFS, digital-silence dropouts mid-program and long quiet gaps.
Cut points: a small window around each timeline audio edit, for clicks and room-tone jumps.

Fades, holds, stills and quiet beats can be intentional, so every finding is phrased as a check.
"""

import array
import base64
import math
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

DEFAULTS = {
    "black_pix_th": 0.10,      # pixel luma (0-1) at or below this counts as black
    "black_pic_th": 0.98,      # share of black pixels for a black picture
    "freeze_noise_db": -60,    # freezedetect noise tolerance
    "freeze_min_s": 0.5,
    "freeze_hold_s": 3.0,      # longer static stretches are reported as info (stills, titles)
    "scene_threshold": 0.35,   # ffmpeg scene score for a cut
    "flash_max_frames": 2,     # shots of 1-2 frames
    "flash_neighbor_frames": 3,
    "clip_dbfs": -0.1,         # sample peak at or above this counts as hitting 0 dBFS
    "dropout_db": -80,         # digital silence
    "dropout_min_s": 0.02,
    "dropout_short_s": 1.0,    # shorter digital silences are warnings, longer ones info
    "edge_s": 0.25,            # ignore silence touching the head/tail by this much
    "quiet_db": -50,           # 100 ms RMS below this is quiet
    "quiet_min_s": 3.0,
    "jump_lu": 6.0,            # short-term loudness change between adjacent 3 s windows
    "jump_window_s": 3.0,
    "non_silent_lufs": -45.0,
    "loud_over_lu": 8.0,       # momentary loudness this far over the median short-term...
    "loud_min_s": 1.0,         # ...for at least this long
    "true_peak_dbtp": -1.0,
    "click_window_ms": 5.0,
    "click_ratio": 10.0,       # spike vs local median sample difference
    "click_min": 0.01,         # absolute sample step (about -40 dBFS)
    "floor_window_ms": 50.0,
    "floor_jump_db": 6.0,
    "floor_silent_db": -75.0,
    "max_cuts": 300,
}

LIMITS = [
    "Checked a low-resolution H.264 preview render, so fine detail, grain, focus and banding are not judged.",
    "DaVinci Resolve (Free) renders at most UHD and 60 fps; a timeline above that will not render in Free.",
    "Burned-in captions or titles colliding with on-screen text are not detected.",
    "Audio is the rendered mix after lossy encoding; peaks, clicks and loudness are measured on the decoded preview.",
    "Shot cuts use ffmpeg's scene score: strobes, flashes of light and very fast motion can look like flash frames.",
    "Fades, holds, stills and quiet beats can be intentional. Every finding is something to check, not a verdict.",
]


class QCError(RuntimeError):
    """The preview could not be analyzed."""


# ------------------------------------------------------------------ timecode


def tc_to_frames(value, fps):
    """SMPTE label (HH:MM:SS:FF, or ;FF for drop-frame) to a frame count."""
    text = str(value or "").strip()
    drop = ";" in text
    parts = text.replace(";", ":").split(":")
    if len(parts) != 4:
        raise ValueError("Invalid timecode: %r" % value)
    hours, minutes, seconds, frames = [int(part) for part in parts]
    nominal = max(1, int(round(fps)))
    total = (hours * 3600 + minutes * 60 + seconds) * nominal + frames
    if drop:
        skipped = 2 if nominal == 30 else 4 if nominal == 60 else 0
        total_minutes = hours * 60 + minutes
        total -= skipped * (total_minutes - total_minutes // 10)
    return total


def frames_to_tc(frame, fps, drop=False):
    """Frame count to a SMPTE label; drop-frame labels for 29.97/59.94 when ``drop``."""
    frame = max(0, int(round(frame)))
    nominal = max(1, int(round(fps)))
    if drop and nominal in (30, 60):
        skipped = 2 if nominal == 30 else 4
        ten_minutes = nominal * 600 - skipped * 9
        minute = nominal * 60 - skipped
        blocks, remainder = divmod(frame, ten_minutes)
        frame += skipped * 9 * blocks + skipped * max(0, (remainder - skipped) // minute)
    else:
        drop = False
    seconds, frames = divmod(frame, nominal)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return "%02d:%02d:%02d%s%02d" % (hours, minutes, seconds, ";" if drop else ":", frames)


def timeline_timecode(frame, timeline_start_frame, start_timecode, fps, drop=False):
    """Timecode label of an absolute timeline frame, using the timeline's start timecode."""
    try:
        base = tc_to_frames(start_timecode, fps) if start_timecode else int(timeline_start_frame)
    except ValueError:
        base = int(timeline_start_frame)
    return frames_to_tc(base + int(frame) - int(timeline_start_frame), fps, drop)


# ------------------------------------------------------------------- ffmpeg


def _run(cmd, cwd=None, timeout=None):
    try:
        return subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise QCError("ffmpeg took longer than %d s: %s" % (timeout or 0, " ".join(map(str, cmd[:6]))))
    except OSError as exc:
        raise QCError("Could not run ffmpeg (%s)." % exc)


def _number(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def probe(ffmpeg, path):
    """Duration, video size/fps and audio rate/channels from ffmpeg's stream banner."""
    proc = _run([ffmpeg, "-hide_banner", "-i", str(path)], timeout=60)
    text = proc.stderr.decode("utf-8", "replace")
    info = {"duration": None, "video": None, "audio": None}
    found = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if found:
        info["duration"] = int(found.group(1)) * 3600 + int(found.group(2)) * 60 + float(found.group(3))
    for line in text.splitlines():
        if "Stream #" not in line:
            continue
        if " Video:" in line and info["video"] is None:
            size = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line)
            rate = re.search(r"([\d.]+) fps", line) or re.search(r"([\d.]+) tbr", line)
            info["video"] = {"width": int(size.group(1)) if size else None,
                             "height": int(size.group(2)) if size else None,
                             "fps": _number(rate.group(1)) if rate else None}
        elif " Audio:" in line and info["audio"] is None:
            rate = re.search(r"(\d+) Hz", line)
            layout = re.search(r"Hz,\s*([^,]+)", line)
            name = layout.group(1).strip() if layout else "stereo"
            channels = {"mono": 1, "stereo": 2, "2.1": 3, "quad": 4, "5.0": 5, "5.1": 6, "7.1": 8}.get(name.split("(")[0])
            count = re.search(r"(\d+) channels", name)
            info["audio"] = {"sample_rate": int(rate.group(1)) if rate else 48000,
                             "channels": channels or (int(count.group(1)) if count else 2)}
    if info["duration"] is None and info["video"] is None and info["audio"] is None:
        raise QCError("ffmpeg could not read %s." % path)
    return info


def _metadata_series(path, key):
    """Parse an ffmpeg (a)metadata print file into [(pts_time, value)] for one key."""
    series, current = [], None
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return series
    for line in lines:
        if line.startswith("frame:"):
            found = re.search(r"pts_time:\s*(-?[\d.]+)", line)
            current = float(found.group(1)) if found else None
        elif line.startswith(key + "=") and current is not None:
            raw = line.split("=", 1)[1].strip()
            value = float("-inf") if raw.startswith("-inf") else _number(raw)
            if value is not None:
                series.append((current, value))
    return series


# ------------------------------------------------------------------ picture


def picture_pass(ffmpeg, path, fps, workdir, opts):
    """One decode: black segments, frozen stretches and per-frame scene scores."""
    vf = ("blackdetect=d=%.6f:pic_th=%.3f:pix_th=%.3f,freezedetect=n=%sdB:d=%.3f,"
          "select='gte(scene,0)',metadata=mode=print:key=lavfi.scene_score:file=scene.txt"
          % (0.9 / fps, opts["black_pic_th"], opts["black_pix_th"], opts["freeze_noise_db"], opts["freeze_min_s"]))
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info", "-i", str(path), "-map", "0:v:0",
           "-an", "-vf", vf, "-f", "null", "-"]
    proc = _run(cmd, cwd=workdir, timeout=3600)
    text = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise QCError("ffmpeg picture analysis failed: %s" % text.strip().splitlines()[-1:])
    black = [(float(a), float(b)) for a, b in
             re.findall(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)", text)]
    freezes, start = [], None
    for name, value in re.findall(r"lavfi\.freezedetect\.freeze_(start|end):\s*([\d.]+)", text):
        if name == "start":
            start = float(value)
        elif start is not None:
            freezes.append((start, float(value)))
            start = None
    scores = [value for _, value in _metadata_series(Path(workdir) / "scene.txt", "lavfi.scene_score")]
    return {"black": black, "freeze": freezes, "freeze_open": start, "scores": scores}


def shot_cuts(scores, threshold):
    """Frame indexes where a new shot starts."""
    return [index for index, score in enumerate(scores) if index > 0 and score >= threshold]


def flash_frames(cuts, frame_count, max_frames=2, neighbor_frames=3):
    """Shots of 1..max_frames between two longer shots: [(first_frame_index, length)]."""
    bounds = [0] + list(cuts) + [frame_count]
    found = []
    # Skip the first and last shot: the render range itself may start or end mid-shot.
    for i in range(1, len(bounds) - 2):
        length = bounds[i + 1] - bounds[i]
        before = bounds[i] - bounds[i - 1]
        after = bounds[i + 2] - bounds[i + 1]
        if 1 <= length <= max_frames and before >= neighbor_frames and after >= neighbor_frames:
            found.append((bounds[i], length))
    return found


def _overlap(a0, a1, spans):
    return sum(max(0.0, min(a1, b1) - max(a0, b0)) for b0, b1 in spans)


def picture_findings(data, fps, duration, opts):
    findings = []
    frame = 1.0 / fps
    blacks = data["black"]
    flash_spans = []
    for start, end in blacks:
        frames = max(1, int(round((end - start) * fps)))
        head, tail = start <= frame * 0.5, end >= duration - frame * 0.5
        if head or tail:
            where = "head" if head else "tail"
            findings.append(_finding("info", "black", start, end,
                                     "Black for %s at the %s of the range from {tc} (a fade or a gap?); check it is intended."
                                     % (_dur(end - start, fps), where)))
        elif frames <= opts["flash_max_frames"]:
            flash_spans.append((start, end))
            findings.append(_finding("error", "black_flash", start, end,
                                     "%d-frame black flash at {tc}; usually a gap between clips. Check it." % frames))
        else:
            findings.append(_finding("warning", "black", start, end,
                                     "Black for %s at {tc}; check for a gap in the timeline or an intended dip to black."
                                     % _dur(end - start, fps)))
    freezes = list(data["freeze"])
    if data.get("freeze_open") is not None:
        freezes.append((data["freeze_open"], duration))
    for start, end in freezes:
        if end - start <= 0 or _overlap(start, end, blacks) >= 0.8 * (end - start):
            continue  # Frozen black is already reported as black.
        if end - start < opts["freeze_hold_s"]:
            findings.append(_finding("warning", "freeze", start, end,
                                     "Picture frozen for %s at {tc}; check for a stuck or held frame." % _dur(end - start, fps)))
        else:
            findings.append(_finding("info", "static", start, end,
                                     "Picture static for %s from {tc} (a still, title or hold?); check it is intended."
                                     % _dur(end - start, fps)))
    scores = data["scores"]
    cuts = shot_cuts(scores, opts["scene_threshold"])
    for index, length in flash_frames(cuts, len(scores), opts["flash_max_frames"], opts["flash_neighbor_frames"]):
        start, end = index * frame, (index + length) * frame
        if _overlap(start, end, flash_spans) > 0:
            continue
        findings.append(_finding("error", "flash_frame", start, end,
                                 "%d-frame flash at {tc}: a shot this short is usually a leftover frame. Check it." % length))
    return findings, len(cuts)


# -------------------------------------------------------------------- audio


def audio_pass(ffmpeg, path, workdir, opts):
    """One decode: EBU R128 summary, 100 ms loudness/peak/RMS series and digital silence."""
    graph = ("[0:a:0]aresample=48000,asetnsamples=n=4800:p=0,asplit=3[a][b][c];"
             "[a]ebur128=metadata=1:peak=true,"
             "ametadata=mode=print:key=lavfi.r128.S:file=short.txt,"
             "ametadata=mode=print:key=lavfi.r128.M:file=momentary.txt,anullsink;"
             "[b]astats=metadata=1:reset=1,"
             "ametadata=mode=print:key=lavfi.astats.Overall.Peak_level:file=peak.txt,"
             "ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=rms.txt,anullsink;"
             "[c]silencedetect=n=%sdB:d=%.3f[out]" % (opts["dropout_db"], opts["dropout_min_s"]))
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info", "-i", str(path), "-filter_complex", graph,
           "-map", "[out]", "-f", "null", "-"]
    proc = _run(cmd, cwd=workdir, timeout=3600)
    text = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise QCError("ffmpeg audio analysis failed: %s" % text.strip().splitlines()[-1:])
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else ""

    def grab(pattern):
        found = re.search(pattern, summary)
        if not found:
            return None
        return float("-inf") if found.group(1).startswith("-inf") else _number(found.group(1))

    silences, start = [], None
    for name, value in re.findall(r"silence_(start|end):\s*(-?[\d.]+)", text):
        if name == "start":
            start = max(0.0, float(value))
        elif start is not None:
            silences.append((start, float(value)))
            start = None
    work = Path(workdir)
    return {
        "integrated_lufs": grab(r"I:\s+(-?[\d.]+|-inf)\s+LUFS"),
        "lra_lu": grab(r"LRA:\s+(-?[\d.]+|-inf)\s+LU\b"),
        "true_peak_dbtp": grab(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS"),
        "short": _metadata_series(work / "short.txt", "lavfi.r128.S"),
        "momentary": _metadata_series(work / "momentary.txt", "lavfi.r128.M"),
        "peak": _metadata_series(work / "peak.txt", "lavfi.astats.Overall.Peak_level"),
        "rms": _metadata_series(work / "rms.txt", "lavfi.astats.Overall.RMS_level"),
        "silence": silences,
        "silence_open": start,
    }


def _runs(series, test, step=0.1, gap=0.0):
    """Merge consecutive series points passing ``test`` into [(start, end, values)]."""
    runs = []
    for t, value in series:
        if not test(value):
            continue
        if runs and t - runs[-1][1] <= step + gap + 1e-6:
            runs[-1][1] = t + step
            runs[-1][2].append(value)
        else:
            runs.append([t, t + step, [value]])
    return runs


def loudness_jumps(short, jump_lu, window_s=3.0, floor=-45.0, step=0.1):
    """Times where adjacent short-term windows ([t-3, t] vs [t, t+3]) differ by more than jump_lu."""
    times = [t for t, _ in short]
    values = [v for _, v in short]
    lag = max(1, int(round(window_s / step)))
    candidates = []
    for i in range(len(values) - lag):
        t = times[i] + step  # pts marks a frame's start; S covers the 3 s up to its end
        if t < window_s - 1e-6:
            continue
        before, after = values[i], values[i + lag]
        if before > floor and after > floor and abs(after - before) > jump_lu:
            candidates.append((t, after - before, before, after))
    picked = []
    for cand in candidates:  # one jump per 3 s: keep the largest
        if picked and cand[0] - picked[-1][0] < window_s:
            if abs(cand[1]) > abs(picked[-1][1]):
                picked[-1] = cand
        else:
            picked.append(cand)
    return picked


def typical_loudness(short, floor=-45.0):
    """Median short-term loudness of the non-silent program, or None."""
    values = [value for _, value in short if value > floor]
    return statistics.median(values) if values else None


def audio_findings(data, duration, fps, opts):
    findings = []
    edge = opts["edge_s"]
    for start, end, values in _runs(data["peak"], lambda v: v >= opts["clip_dbfs"], gap=0.4):
        findings.append(_finding("error", "clipping", start, min(end, duration),
                                 "Audio reaches 0 dBFS (peak %+.1f dBFS) at {tc}; check for clipping." % max(values)))
    silences = list(data["silence"])
    if data.get("silence_open") is not None:
        silences.append((data["silence_open"], duration))
    reported = []
    for start, end in silences:
        if start <= edge or end >= duration - edge:
            continue  # Silence at the head or tail is usually a fade or handle.
        reported.append((start, end))
        if end - start < opts["dropout_short_s"]:
            findings.append(_finding("warning", "dropout", start, end,
                                     "Audio drops to digital silence for %s at {tc}; check for a gap between audio clips."
                                     % _dur(end - start, fps)))
        else:
            findings.append(_finding("info", "digital_silence", start, end,
                                     "Digital silence for %s from {tc}; check it is intended." % _dur(end - start, fps)))
    for start, end, _ in _runs(data["rms"], lambda v: v < opts["quiet_db"]):
        end = min(end, duration)
        if end - start < opts["quiet_min_s"] or _overlap(start, end, reported) > 0.5 * (end - start):
            continue
        where = " at the head" if start <= edge else " at the tail" if end >= duration - edge else ""
        findings.append(_finding("info", "quiet_gap", start, end,
                                 "Quiet (below %d dBFS) for %s from {tc}%s; check nothing is missing."
                                 % (opts["quiet_db"], _dur(end - start, fps), where)))
    for t, delta, before, after in loudness_jumps(data["short"], opts["jump_lu"], opts["jump_window_s"],
                                                  opts["non_silent_lufs"]):
        findings.append(_finding("warning", "loudness_jump", t, None,
                                 "Loudness jumps %s %.1f LU around {tc} (%.0f to %.0f LUFS short-term); check the mix level."
                                 % ("up" if delta > 0 else "down", abs(delta), before, after)))
    typical = typical_loudness(data["short"], opts["non_silent_lufs"])
    if typical is not None:
        # Against the median short-term loudness, so one loud stretch cannot hide itself in the average.
        limit = typical + opts["loud_over_lu"]
        for start, end, values in _runs(data["momentary"], lambda v: v > limit, gap=0.3):
            if end - start >= opts["loud_min_s"]:
                findings.append(_finding("warning", "loud_section", start, min(end, duration),
                                         "Loud section for %s from {tc}: up to %.0f LUFS against a typical %.0f LUFS; check the level."
                                         % (_dur(end - start, fps), max(values), typical)))
    peak = data.get("true_peak_dbtp")
    if peak is not None and math.isfinite(peak) and peak > opts["true_peak_dbtp"]:
        findings.append(_finding("info", "true_peak", 0.0, None,
                                 "True peak is %+.1f dBTP; most platforms ask for %.0f dBTP or lower." % (peak, opts["true_peak_dbtp"])))
    return findings


# ---------------------------------------------------------------- cut points


def _read_f32(path):
    data = array.array("f")
    raw = Path(path).read_bytes()
    data.frombytes(raw[: len(raw) - len(raw) % 4])
    if sys.byteorder != "little":
        data.byteswap()
    return data


def click_score(samples, channels, sample_rate, center_s, window_ms=5.0):
    """Largest sample step within +-window_ms of center_s, against the local median step.

    Returns (spike, median, ratio, reference_max) across channels; the worst channel wins.
    """
    frames = len(samples) // max(1, channels)
    center = int(round(center_s * sample_rate))
    half = max(1, int(round(window_ms / 1000.0 * sample_rate)))
    lo, hi = max(1, center - half), min(frames, center + half + 1)
    best = (0.0, 0.0, 0.0, 0.0)
    if hi <= lo or frames < 3:
        return best
    for ch in range(channels):
        steps = [abs(samples[i * channels + ch] - samples[(i - 1) * channels + ch]) for i in range(1, frames)]
        core = steps[lo - 1:hi - 1]
        reference = steps[:lo - 1] + steps[hi - 1:]
        if not core or len(reference) < 10:
            continue
        spike = max(core)
        median = statistics.median(reference)
        ratio = spike / max(median, 1e-7)
        if ratio > best[2]:
            best = (spike, median, ratio, max(reference))
    return best


def floor_levels(samples, sample_rate, center_s, guard_s=0.025, window_ms=50.0):
    """10th-percentile 50 ms RMS (dBFS) before and after center_s; None when a side is too short."""
    size = max(1, int(round(window_ms / 1000.0 * sample_rate)))
    center = int(round(center_s * sample_rate))
    guard = int(round(guard_s * sample_rate))

    def p10(lo, hi):
        levels = []
        for start in range(lo, hi - size + 1, size):
            chunk = samples[start:start + size]
            levels.append(math.sqrt(sum(x * x for x in chunk) / size))
        if len(levels) < 3:
            return None
        levels.sort()
        value = levels[int(0.1 * (len(levels) - 1))]
        return 20 * math.log10(value) if value > 0 else float("-inf")

    return p10(0, max(0, center - guard)), p10(min(len(samples), center + guard), len(samples))


def cut_findings(ffmpeg, path, cut_times, duration, channels, workdir, opts):
    """Check each audio edit point for a click and a room-tone/noise-floor jump."""
    findings, checked = [], 0
    times = sorted(t for t in cut_times if 0.06 < t < duration - 0.06)
    for index, t in enumerate(times):
        neighbors = [abs(t - other) for other in (times[index - 1] if index else None,
                                                  times[index + 1] if index + 1 < len(times) else None) if other is not None]
        side = max(0.25, min([1.0] + [gap / 2.0 for gap in neighbors]))
        start = max(0.0, t - side)
        length = min(duration, t + side) - start
        center = t - start
        click_lo = max(0.0, center - 0.05)
        floor_file, click_file = Path(workdir) / "floor.raw", Path(workdir) / "click.raw"
        cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-y", "-ss", "%.4f" % start,
               "-t", "%.4f" % length, "-i", str(path),
               "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_f32le", "-f", "f32le", str(floor_file),
               "-map", "0:a:0", "-ss", "%.4f" % click_lo, "-t", "0.1", "-ar", "48000", "-c:a", "pcm_f32le",
               "-f", "f32le", str(click_file)]
        proc = _run(cmd, timeout=60)
        if proc.returncode != 0:
            continue
        checked += 1
        spike, median, ratio, reference_max = click_score(_read_f32(click_file), channels, 48000, center - click_lo,
                                                          opts["click_window_ms"])
        if spike >= opts["click_min"] and ratio >= opts["click_ratio"] and spike > 1.5 * reference_max:
            findings.append(_finding("warning", "click", t, None,
                                     "Possible click or pop at the audio cut at {tc} (sample step %.0fx the local level); "
                                     "check it and add a short crossfade if you hear it." % ratio))
        before, after = floor_levels(_read_f32(floor_file), 16000, center, window_ms=opts["floor_window_ms"])
        if before is None or after is None:
            continue
        silent = opts["floor_silent_db"]
        if before > silent and after > silent and abs(after - before) > opts["floor_jump_db"]:
            findings.append(_finding("warning", "noise_floor_jump", t, None,
                                     "Background level changes %s by %.0f dB at the audio cut at {tc} (%.0f to %.0f dBFS); "
                                     "check for a room-tone jump." % ("up" if after > before else "down",
                                                                       abs(after - before), before, after)))
    return findings, checked


# ------------------------------------------------------------------ assembly


def _finding(severity, kind, start, end, message):
    return {"severity": severity, "kind": kind, "start": float(start), "end": None if end is None else float(end),
            "message": message}


def _dur(seconds, fps):
    frames = int(round(seconds * fps))
    return "%d frame%s" % (frames, "" if frames == 1 else "s") if seconds < 1.0 else "%.1f s" % seconds


def analyze(path, ffmpeg, *, fps, range_start_frame, timeline_start_frame, start_timecode=None, drop_frame=False,
            cut_frames=(), range_end_frame=None, options=None):
    """Analyze a rendered preview. Times are seconds into the file; frames are absolute timeline frames."""
    if not ffmpeg:
        raise QCError("ffmpeg is needed for the render check. Install it (brew install ffmpeg) and try again.")
    if not path or not Path(path).is_file():
        raise QCError("Rendered preview not found: %s" % path)
    path = str(Path(path).resolve())  # ffmpeg runs with a scratch working directory
    opts = dict(DEFAULTS, **(options or {}))
    fps = float(fps)
    info = probe(ffmpeg, path)
    duration = float(info["duration"] or 0.0)
    if range_end_frame is not None and duration <= 0:
        duration = (int(range_end_frame) - int(range_start_frame)) / fps
    findings, report = [], {"cuts_detected": None, "audio_cuts_checked": 0, "audio_cuts_skipped": 0}
    loudness = None
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="resolve-qc-") as work:
        if info["video"]:
            picture, cuts = picture_findings(picture_pass(ffmpeg, path, fps, work, opts), fps, duration, opts)
            findings += picture
            report["cuts_detected"] = cuts
        if info["audio"]:
            data = audio_pass(ffmpeg, path, work, opts)
            loudness = {key: (None if data[key] is None or not math.isfinite(data[key]) else round(data[key], 1))
                        for key in ("integrated_lufs", "lra_lu", "true_peak_dbtp")}
            findings += audio_findings(data, duration, fps, opts)
            times = sorted({(int(f) - int(range_start_frame)) / fps for f in cut_frames})
            times = [t for t in times if 2.0 / fps < t < duration - 2.0 / fps]
            report["audio_cuts_skipped"] = max(0, len(times) - opts["max_cuts"])
            cut, checked = cut_findings(ffmpeg, path, times[: opts["max_cuts"]], duration,
                                        info["audio"]["channels"], work, opts)
            findings += cut
            report["audio_cuts_checked"] = checked
    for item in findings:
        frame = int(range_start_frame) + int(math.floor(item["start"] * fps + 1e-6))
        tc = timeline_timecode(frame, timeline_start_frame, start_timecode, fps, drop_frame)
        item["time_seconds"] = round(item.pop("start"), 3)
        end = item.pop("end")
        if end is not None:
            item["duration_seconds"] = round(max(0.0, end - item["time_seconds"]), 3)
            item["duration_frames"] = max(1, int(round((end - item["time_seconds"]) * fps)))
        item["timeline_frame"] = frame
        item["timecode"] = tc
        item["message"] = item["message"].replace("{tc}", tc)
    findings.sort(key=lambda f: (f["time_seconds"], SEVERITY_ORDER[f["severity"]], f["kind"]))
    by_kind = {}
    for item in findings:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
    summary = {"errors": sum(f["severity"] == "error" for f in findings),
               "warnings": sum(f["severity"] == "warning" for f in findings),
               "info": sum(f["severity"] == "info" for f in findings),
               "by_kind": by_kind}
    summary.update(report)
    return {"file": str(path), "duration_seconds": round(duration, 3), "fps": fps,
            "video": info["video"], "audio": info["audio"], "loudness": loudness, "summary": summary,
            "findings": findings, "analysis_seconds": round(time.monotonic() - started, 1), "limits": list(LIMITS)}


# --------------------------------------------------------------- orchestration


MARKER_LABELS = {
    "black_flash": "black flash", "flash_frame": "flash frame", "black": "black", "freeze": "frozen picture",
    "clipping": "clipping", "dropout": "audio dropout", "loudness_jump": "loudness jump",
    "loud_section": "loud section", "click": "click at cut", "noise_floor_jump": "room tone jump",
}


def marker_requests(findings):
    """Red markers for errors and yellow for warnings, one per timeline frame (worst first)."""
    marks, seen = [], set()
    for item in sorted(findings, key=lambda f: (SEVERITY_ORDER[f["severity"]], f["time_seconds"])):
        if item["severity"] not in ("error", "warning") or item["timeline_frame"] in seen:
            continue
        seen.add(item["timeline_frame"])
        marks.append({"frame": item["timeline_frame"], "color": "Red" if item["severity"] == "error" else "Yellow",
                      "name": "QC: " + MARKER_LABELS.get(item["kind"], item["kind"].replace("_", " ")),
                      "note": item["message"], "duration": min(item.get("duration_frames") or 1, 10000)})
    return sorted(marks, key=lambda m: m["frame"])


NOT_DISPATCHED = "no new operation started"


def _patient(call, operation, params, timeout, sleep, clock, patience=120.0):
    """Retry only calls the transport proves never dispatched (another client held the Resolve lease)."""
    began = clock()
    while True:
        try:
            return call(operation, params, timeout)
        except Exception as exc:
            never_sent = NOT_DISPATCHED in str(exc) or type(exc).__name__ == "BridgeOffline"
            if not never_sent or clock() - began > patience:
                raise
            sleep(1.0)



def _await_render(call, preview, poll_seconds, max_wait_seconds, sleep, clock):
    """Poll render_status until the preview job completes; returns (job status entry, render seconds)."""
    job_id = preview.get("job_id")
    if not preview.get("started"):
        raise QCError("Resolve did not start the preview render.")
    began = clock()
    job, last_percent = {}, None
    while True:
        try:
            report = call("render_status", {"job_id": job_id}, 30.0)
        except Exception as exc:  # read-only, so a busy or slow bridge is simply polled again
            if clock() - began > max_wait_seconds:
                raise QCError("Could not read the preview render's status: %s" % exc)
            sleep(poll_seconds)
            continue
        jobs = report.get("jobs") or []
        job = jobs[0] if jobs else {}
        state = str(job.get("status") or "").lower()
        last_percent = job.get("percent", last_percent)
        if state in ("complete", "completed"):
            break
        if state in ("failed", "cancelled", "canceled"):
            raise QCError("Resolve reports the preview render %s%s." % (
                job.get("status"), (": %s" % job["error"]) if job.get("error") else ""))
        if clock() - began > max_wait_seconds:
            raise QCError("The preview render did not finish within %d s (last at %s%%)." % (max_wait_seconds, last_percent))
        sleep(poll_seconds)
    return job, round(clock() - began, 1)


def _await_file(output, sleep):
    """The file can land a moment after Resolve reports Complete."""
    for _ in range(20):
        if output and Path(output).is_file() and Path(output).stat().st_size > 0:
            break
        sleep(0.25)
    return output


def _cleanup_preview(call, preview, keep_file, sleep, clock):
    """render_preview_cleanup with the restore state; returns (cleanup report, settings_restored)."""
    try:
        cleanup = {"job_id": preview["job_id"], "keep_file": bool(keep_file)}
        if preview.get("restore_state"):
            cleanup["restore"] = preview["restore_state"]
        report = _patient(call, "render_preview_cleanup", cleanup, 60.0, sleep, clock)
        return report, report.get("settings_restored")
    except Exception as exc:
        return {"error": "The preview job may still be in the render queue, and the Deliver settings "
                         "and In/Out marks may not be restored: %s" % exc}, False


def run_check(call, ffmpeg, check_dir, *, start_frame=None, end_frame=None, width=640, mark=False, keep_file=False,
              poll_seconds=1.0, max_wait_seconds=1200.0, sleep=time.sleep, clock=time.monotonic, options=None):
    """Render a preview through ``call(operation, params, timeout) -> payload``, analyze it, then clean up.

    Always returns a dict with ``ok``. The render job is removed afterwards whether or not analysis worked.
    """
    if not ffmpeg:
        return {"ok": False, "error": "ffmpeg is needed for the render check. Install it (brew install ffmpeg) and try again."}
    params = {"output_dir": str(check_dir), "width": int(width)}
    if start_frame is not None:
        params["start_frame"] = int(start_frame)
    if end_frame is not None:
        params["end_frame"] = int(end_frame)
    try:
        preview = _patient(call, "render_preview", params, 120.0, sleep, clock)
    except Exception as exc:
        return {"ok": False, "error": "Could not queue the preview render: %s" % exc}
    job_id = preview.get("job_id")
    result = {"ok": False, "render": preview}
    try:
        job, result["render_seconds"] = _await_render(call, preview, poll_seconds, max_wait_seconds, sleep, clock)
        output = _await_file(job.get("output") or preview.get("output"), sleep)
        analysis = analyze(output, ffmpeg, fps=preview["fps"], range_start_frame=preview["start_frame"],
                           range_end_frame=preview.get("end_frame"),
                           timeline_start_frame=preview["timeline_start_frame"],
                           start_timecode=preview.get("start_timecode"), drop_frame=bool(preview.get("drop_frame")),
                           cut_frames=preview.get("audio_edit_points") or [], options=options)
        expected = (int(preview["end_frame"]) - int(preview["start_frame"])) / float(preview["fps"])
        if abs(analysis["duration_seconds"] - expected) > max(0.5, 3.0 / float(preview["fps"])):
            analysis["duration_note"] = ("The rendered file lasts %.2f s but the requested range is %.2f s; "
                                         "findings near the end may be offset." % (analysis["duration_seconds"], expected))
        result.update(ok=True, analysis=analysis)
        if mark:
            marks = marker_requests(analysis["findings"])
            if marks:
                try:
                    result["markers"] = _patient(call, "add_markers", {"markers": marks}, 120.0, sleep, clock)
                except Exception as exc:
                    result["markers"] = {"error": str(exc)}
            else:
                result["markers"] = {"added": 0, "note": "No errors or warnings to mark."}
    except Exception as exc:
        result["error"] = str(exc) if isinstance(exc, QCError) else "%s: %s" % (type(exc).__name__, exc)
    finally:
        if job_id:
            result["cleanup"], result["settings_restored"] = _cleanup_preview(call, preview, keep_file, sleep, clock)
    return result


# --------------------------------------------------------------- render frames


FRAMES_ASSUMED_MIN_FPS = 16.0  # slowest Resolve timeline rate: plans the first job before render_preview reports fps
FRAMES_STOP = "Not rendered: %s, so no further preview render was started."


def _frame_number(value, name):
    if isinstance(value, bool):
        raise ValueError("%s must be an absolute timeline frame, not %r." % (name, value))
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be an absolute timeline frame, not %r." % (name, value))
    if not math.isfinite(number) or number != int(number) or number < 0:
        raise ValueError("%s must be a whole, non-negative absolute timeline frame, not %r." % (name, value))
    return int(number)


def pick_frames(frames=None, start_frame=None, end_frame=None, count=6, max_frames=10):
    """Sorted, distinct absolute timeline frames: explicit ``frames``, or ``count`` spread evenly over a range.

    The range is [start_frame, end_frame) with end exclusive. Picks start at start_frame and step by
    (end - start) / count, so they all stay inside the range.
    """
    max_frames = int(max_frames)
    if max_frames < 1:
        raise ValueError("max_frames must be at least 1.")
    ranged = start_frame is not None or end_frame is not None
    if frames is not None and ranged:
        raise ValueError("Pass either frames or start_frame/end_frame, not both.")
    if frames is not None:
        if isinstance(frames, (str, bytes)) or not hasattr(frames, "__iter__"):
            raise ValueError("frames must be a list of absolute timeline frames.")
        picked = sorted({_frame_number(value, "Each frame") for value in frames})
        if not picked:
            raise ValueError("frames is empty: pass at least one absolute timeline frame.")
        if len(picked) > max_frames:
            raise ValueError("Asked for %d distinct frames; at most %d can be rendered per call. "
                             "Split the request." % (len(picked), max_frames))
        return picked
    if start_frame is None or end_frame is None:
        raise ValueError("Pass frames (absolute timeline frames), or both start_frame and end_frame (end exclusive).")
    start, end = _frame_number(start_frame, "start_frame"), _frame_number(end_frame, "end_frame")
    if end <= start:
        raise ValueError("end_frame (%d) must be after start_frame (%d); end_frame is exclusive." % (end, start))
    if isinstance(count, bool):
        raise ValueError("count must be a whole number of frames.")
    try:
        count = int(count)
    except (TypeError, ValueError):
        raise ValueError("count must be a whole number of frames.")
    if not 1 <= count <= max_frames:
        raise ValueError("count must be between 1 and %d." % max_frames)
    span = end - start
    count = min(count, span)
    return sorted({start + (i * span) // count for i in range(count)})


def plan_spans(frames, fps, max_span_seconds):
    """Group sorted frames greedily into clusters whose rendered range [first, last + 1) fits max_span_seconds."""
    limit = max(1, int(math.floor(float(max_span_seconds) * float(fps) + 1e-9)))
    clusters = []
    for frame in sorted(frames):
        if clusters and frame + 1 - clusters[-1][0] <= limit:
            clusters[-1].append(frame)
        else:
            clusters.append([frame])
    return clusters


def png_size(path):
    """Width and height from a PNG's IHDR chunk, or None if the file is not a PNG."""
    with open(path, "rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or not header.startswith(b"\x89PNG\r\n\x1a\n") or header[12:16] != b"IHDR":
        return None
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def _png_ok(path):
    try:
        return path.is_file() and path.stat().st_size > 0 and png_size(path) is not None
    except OSError:
        return False


def extract_frames(ffmpeg, video, indices, width, workdir, timeout=300):
    """Decode exact frames (0-based decode order, no keyframe seeking) of ``video`` to PNGs at most ``width`` wide.

    Returns {index: Path or error string}. One decode serves every frame; any frame it missed is retried alone
    so the error names that frame.
    """
    workdir = Path(workdir)
    video = str(Path(video).resolve())
    indices = sorted(set(int(i) for i in indices))
    scale = "scale='min(iw,%d)':-1" % int(width)
    paths = {index: workdir / ("frame-%06d.png" % index) for index in indices}
    outcome = {}
    if indices:
        # image2 output defaults to passthrough timing, so no -vsync/-fps_mode is needed (ffmpeg 4 to 8).
        graph = "[0:v:0]split=%d%s;" % (len(indices), "".join("[s%d]" % n for n in range(len(indices))))
        graph += ";".join("[s%d]select='eq(n,%d)',%s[o%d]" % (n, index, scale, n) for n, index in enumerate(indices))
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", video, "-filter_complex", graph]
        for n, index in enumerate(indices):
            cmd += ["-map", "[o%d]" % n, "-frames:v", "1", "-update", "1", str(paths[index])]
        try:
            _run(cmd, timeout=timeout)
        except QCError:
            pass  # each frame is retried alone below, which reports the error
    for index in indices:
        if _png_ok(paths[index]):
            outcome[index] = paths[index]
            continue
        try:
            proc = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", video, "-map", "0:v:0",
                         "-vf", "select='eq(n,%d)',%s" % (index, scale), "-frames:v", "1", "-update", "1",
                         str(paths[index])], timeout=timeout)
        except QCError as exc:
            outcome[index] = str(exc)
            continue
        if _png_ok(paths[index]):
            outcome[index] = paths[index]
        else:
            detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            outcome[index] = ("ffmpeg produced no image for frame %d of the rendered preview%s." % (
                index, (": %s" % detail[-1]) if detail else " (the file may be shorter than the requested range)"))
    return outcome


def _frames_note(codec_label, restored, failed_cleanup):
    note = ("These frames come from a real Deliver render of the timeline (grades, Fusion, titles, effects and "
            "transforms included), at reduced resolution and with lossy %s compression, so fine detail, grain and "
            "banding are not reliable. Each frame is decoded at its exact position in the render. " % codec_label)
    if failed_cleanup:
        note += ("The cleanup of at least one preview job failed or could not confirm the restore: check the Deliver "
                 "page (render settings and queue) and the timeline's In/Out marks before rendering anything else.")
    elif restored:
        note += "The Deliver settings and In/Out marks were saved before each render and restored afterwards."
    else:
        note += "No preview job was queued, so the Deliver settings and In/Out marks were not changed by a job."
    return note


def render_frames(call, ffmpeg, frames_dir, *, frames=None, start_frame=None, end_frame=None, count=6, width=960,
                  max_frames=10, max_span_seconds=20.0, poll_seconds=1.0, max_wait_seconds=600.0, keep_files=False,
                  sheet=False, columns=None, tile_width=480, keep_frames=False, sleep=time.sleep,
                  clock=time.monotonic):
    """Stills of the final picture through temporary Deliver renders: ``call(operation, params, timeout) -> payload``.

    Frames are absolute timeline frames. Requested frames are grouped into as few render_preview jobs as
    max_span_seconds allows (the first job is planned at the slowest timeline rate, later ones at the fps it
    reports), rendered one after another, and each job is always cleaned up with its restore state. Always
    returns a dict with ``ok`` (true when at least one frame was extracted); check each frame's ``error``.

    With ``sheet`` the extracted stills are also tiled, in timeline order, into one PNG contact sheet
    (``columns`` across, default about square; tiles ``tile_width`` px wide) with each tile's timecode and
    absolute frame burned in, returned as ``result["sheet"]``. Per-frame ``png_base64`` is then left out unless
    ``keep_frames``; if the sheet cannot be built, the per-frame images are kept and ``sheet_error`` says why.
    """
    try:
        picked = pick_frames(frames, start_frame, end_frame, count, max_frames)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    if sheet:
        try:
            if isinstance(tile_width, bool) or not 96 <= int(tile_width) <= 1920:
                raise ValueError
            tile_width = int(tile_width)
        except (TypeError, ValueError):
            return {"ok": False, "error": "tile_width must be a pixel count from 96 to 1920."}
        if columns is not None:
            try:
                if isinstance(columns, bool) or int(columns) != columns or int(columns) < 1:
                    raise ValueError
                columns = int(columns)
            except (TypeError, ValueError):
                return {"ok": False, "error": "columns must be a whole number of at least 1, or omitted."}
    try:
        if isinstance(width, bool) or int(width) < 16:
            raise ValueError
        width = int(width)
    except (TypeError, ValueError):
        return {"ok": False, "error": "width must be a pixel count of at least 16."}
    try:
        if not float(max_span_seconds) > 0:
            raise ValueError
    except (TypeError, ValueError):
        return {"ok": False, "error": "max_span_seconds must be a positive number of seconds."}
    if not ffmpeg:
        return {"ok": False, "error": "ffmpeg is needed to extract frames. Install it (brew install ffmpeg) and try again."}
    frames_dir = Path(frames_dir)
    try:
        frames_dir.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="frames-", dir=str(frames_dir)))
    except OSError as exc:
        return {"ok": False, "error": "Could not create a folder for the frames in %s: %s" % (frames_dir, exc)}
    entries = {frame: {"timeline_frame": frame, "timecode": None, "png_base64": None, "width": None, "height": None}
               for frame in picked}
    renders, cleanups, codec_label = [], [], None
    remaining, fps, stop = list(picked), None, None
    try:
        while remaining and stop is None:
            cluster = plan_spans(remaining, fps or FRAMES_ASSUMED_MIN_FPS, max_span_seconds)[0]
            remaining = remaining[len(cluster):]
            stop = _render_cluster(call, ffmpeg, work, cluster, width, entries, renders, cleanups,
                                   poll_seconds, max_wait_seconds, sleep, clock)
            fps = renders[-1].get("fps") or fps
            if renders[-1].get("codec"):
                codec_label = "%s %s" % (renders[-1].get("format") or "", renders[-1]["codec"])
        for frame in remaining:
            entries[frame]["error"] = FRAMES_STOP % stop
        stills = {}
        for frame, entry in entries.items():
            path = entry.pop("_path", None)
            if path is None:
                continue
            try:
                entry["png_base64"] = base64.b64encode(Path(path).read_bytes()).decode("ascii")
                stills[frame] = path
                if keep_files:
                    entry["path"] = str(path)
            except OSError as exc:
                entry["error"] = "Could not read the extracted PNG: %s" % exc
        contact, sheet_error = None, None
        if sheet and stills:
            contact, sheet_error = _contact_sheet(ffmpeg, picked, entries, stills, columns, tile_width)
    finally:
        if not keep_files:
            shutil.rmtree(str(work), ignore_errors=True)
    failed_cleanup = any(item.get("settings_restored") is not True for item in cleanups)
    settings_restored = (not failed_cleanup) if cleanups else None
    extracted = [entry for entry in entries.values() if entry["png_base64"]]
    if contact is not None and not keep_frames:
        undecoded = {tile["timeline_frame"] for tile in contact["tiles"] if tile.get("error")}
        for frame, entry in entries.items():
            if frame not in undecoded:  # a still the sheet could not show is still returned on its own
                entry.pop("png_base64", None)
    result = {"ok": bool(extracted), "frames": [entries[frame] for frame in picked],
              "requested": len(picked), "extracted": len(extracted), "renders": renders,
              "settings_restored": settings_restored, "cleanup": cleanups,
              "note": _frames_note((codec_label or "H.264").strip(), settings_restored, failed_cleanup)}
    if contact is not None:
        result["sheet"] = contact
        result["note"] += SHEET_NOTE
    elif sheet_error:
        result["sheet_error"] = sheet_error
    if keep_files:
        result["frames_dir"] = str(work)
    if not extracted:
        errors = [entry.get("error") for entry in entries.values() if entry.get("error")]
        result["error"] = "No frame was extracted. %s" % (errors[0] if errors else "")
    elif len(extracted) < len(picked):
        result["warning"] = ("Only %d of %d frames were extracted; the others carry an error and no image."
                             % (len(extracted), len(picked)))
    return result


SHEET_NOTE = (" The contact sheet tiles the frames in timeline order, left to right then top to bottom, each "
              "labelled underneath with its timeline timecode and absolute frame; grey tiles were not rendered.")


def _contact_sheet(ffmpeg, picked, entries, stills, columns, tile_width):
    """(sheet dict, None) tiling every picked frame in order, or (None, why it could not be built)."""
    from bridge import sheet as contact
    reference = next(entries[frame] for frame in picked if frame in stills)
    tile_height = None
    if reference.get("width") and reference.get("height"):
        tile_height = max(1, int(round(tile_width * float(reference["height"]) / reference["width"])))
    tiles = [{"timeline_frame": frame, "timecode": entries[frame].get("timecode"), "path": stills.get(frame)}
             for frame in picked]
    try:
        built = contact.compose(tiles, tile_width=tile_width, tile_height=tile_height, columns=columns,
                                ffmpeg=ffmpeg)
    except (OSError, MemoryError, contact.SheetError) as exc:
        return None, "The contact sheet could not be built, so each frame is returned instead: %s" % exc
    png = built.pop("png")
    return dict(built, png_base64=base64.b64encode(png).decode("ascii")), None


def _render_cluster(call, ffmpeg, work, cluster, width, entries, renders, cleanups, poll_seconds, max_wait_seconds,
                    sleep, clock):
    """Render [first, last + 1) of one cluster, extract its frames into ``entries``, always clean up.

    Appends to ``renders`` and ``cleanups``. Returns None to carry on, or why no further job should start.
    """
    first, last = cluster[0], cluster[-1]
    render = {"job_id": None, "start_frame": first, "end_frame": last + 1, "render_seconds": None,
              "frames": list(cluster)}
    renders.append(render)
    params = {"output_dir": str(work), "width": max(160, min(int(width), 1920)),
              "start_frame": first, "end_frame": last + 1}
    try:
        preview = _patient(call, "render_preview", params, 120.0, sleep, clock)
    except Exception as exc:
        render["error"] = "Could not queue the preview render: %s" % exc
        for frame in cluster:
            entries[frame]["error"] = render["error"]
        return "the preview render for frames %d-%d could not be queued" % (first, last)
    job_id = preview.get("job_id")
    render.update(job_id=job_id, fps=preview.get("fps"), format=preview.get("format"), codec=preview.get("codec"),
                  render_width=preview.get("width"), render_height=preview.get("height"),
                  settings_restored_before_render=preview.get("settings_restored_before_render"))
    if preview.get("range_warning"):
        render["range_warning"] = preview["range_warning"]
    stop = None
    try:
        fps = float(preview["fps"])
        start = int(preview["start_frame"])
        for frame in cluster:
            entries[frame]["timecode"] = timeline_timecode(frame, preview["timeline_start_frame"],
                                                           preview.get("start_timecode"), fps,
                                                           bool(preview.get("drop_frame")))
        try:
            job, render["render_seconds"] = _await_render(call, preview, poll_seconds, max_wait_seconds, sleep, clock)
        except QCError as exc:
            if not str(exc).startswith("Resolve reports the preview render"):  # Failed/Cancelled: try the next job
                stop = "an earlier preview render did not start or finish"
            raise
        output = _await_file(job.get("output") or preview.get("output"), sleep)
        if not output or not Path(output).is_file() or Path(output).stat().st_size <= 0:
            raise QCError("Resolve reported the preview render complete, but the file did not appear: %s" % output)
        span = int(preview["end_frame"]) - start
        try:
            duration = probe(ffmpeg, output).get("duration")
            if duration and abs(duration - span / fps) > max(0.5, 3.0 / fps):
                render["duration_note"] = ("The rendered file lasts %.2f s but the requested range is %.2f s, so "
                                           "frames may not line up with the timeline." % (duration, span / fps))
        except QCError:
            pass
        wanted = {frame: frame - start for frame in cluster if 0 <= frame - start < span}
        for frame in cluster:
            if frame not in wanted:
                entries[frame]["error"] = "Frame %d is outside the rendered range %d-%d." % (frame, start, start + span)
        stills = Path(work) / ("job-%d" % len(renders))  # file names are per-render frame indices
        stills.mkdir(exist_ok=True)
        extracted = extract_frames(ffmpeg, output, wanted.values(), width, stills)
        for frame, index in wanted.items():
            got = extracted.get(index)
            if isinstance(got, Path):
                size = png_size(got)
                entries[frame].update(width=size[0], height=size[1], _path=got)
            else:
                entries[frame]["error"] = got or "ffmpeg produced no image for this frame."
    except Exception as exc:
        render["error"] = str(exc) if isinstance(exc, QCError) else "%s: %s" % (type(exc).__name__, exc)
        for frame in cluster:
            if "_path" not in entries[frame] and not entries[frame].get("error"):
                entries[frame]["error"] = render["error"]
    finally:
        if job_id:
            cleanup, restored = _cleanup_preview(call, preview, False, sleep, clock)
            cleanup = dict(cleanup, job_id=cleanup.get("job_id", job_id), settings_restored=restored)
            cleanups.append(cleanup)
            if restored is not True:
                stop = "an earlier preview's cleanup did not confirm the Deliver settings and In/Out marks were restored"
        else:
            stop = stop or "Resolve returned no job id for an earlier preview render"
    return stop
