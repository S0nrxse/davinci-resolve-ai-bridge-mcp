"""Activity map for gameplay: where is something happening, and where is dead air?

Combines, per BIN seconds of one clip's source window:
- speech      word timings from the mic track (always protected)
- game audio  loudness and onsets on the game track, judged against the track's OWN rolling baseline, so a
              constant soundtrack doesn't count as action but a gunshot over it does
- picture     scene-change score (motion, cuts, kill feeds), black frames (loading screens) and frozen frames
              (menus, pause screens)

Dead air = no speech, no game event and no motion, for at least ``min_dead`` seconds; black or frozen picture
counts as dead even with game music playing. Everything is streamed through ffmpeg, so multi-hour recordings
don't load into memory. Standard library only.
"""

import array
import json
import math
import re
import shutil
import subprocess
import sys

BIN = 0.25  # Seconds per analysis bin.
SR = 8000  # Analysis sample rate; plenty for loudness and onsets.
HOP = 400  # 50 ms energy hops.

PRESETS = {
    # min_dead: shortest dead span worth cutting; pre/post: air kept around game events; speech_pad: around words.
    "vod": {"min_dead": 6.0, "pre": 2.0, "post": 1.5, "speech_pad": 0.5, "keep": 0.4},
    "youtube": {"min_dead": 3.0, "pre": 1.5, "post": 1.0, "speech_pad": 0.4, "keep": 0.3},
    "shorts": {"min_dead": 1.5, "pre": 0.8, "post": 0.6, "speech_pad": 0.3, "keep": 0.2},
}


class ActivityError(RuntimeError):
    """Analysis failed for a reason the user can act on."""


# ------------------------------------------------------------------ streams

def ffprobe_path(ffmpeg):
    import os
    sibling = os.path.join(os.path.dirname(str(ffmpeg)), "ffprobe") if ffmpeg and os.path.isabs(str(ffmpeg)) else None
    for candidate in (sibling, shutil.which("ffprobe"), "/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def audio_streams(ffmpeg, path):
    """Audio tracks in a recording: [{"stream": n (audio-relative), "title", "channels"}]."""
    probe = ffprobe_path(ffmpeg)
    if not probe:
        return [{"stream": 0, "title": None, "channels": None}]
    result = subprocess.run([probe, "-v", "error", "-select_streams", "a", "-show_entries",
                             "stream=index,channels:stream_tags=title,handler_name", "-of", "json", str(path)],
                            capture_output=True, timeout=30)
    try:
        streams = json.loads(result.stdout or b"{}").get("streams", [])
    except ValueError:
        streams = []
    return [{"stream": n, "title": (s.get("tags") or {}).get("title") or (s.get("tags") or {}).get("handler_name"),
             "channels": s.get("channels")} for n, s in enumerate(streams)]


# --------------------------------------------------------------- game audio

def hop_levels(ffmpeg, path, start, duration, stream=None):
    """Per-50ms RMS level in dBFS. ffmpeg measures it natively (astats), streamed line by line."""
    select = ["-map", "0:a:%d" % int(stream)] if stream is not None else ["-vn"]
    af = ("aresample=%d,asetnsamples=n=%d:p=0,astats=metadata=1:reset=1,"
          "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-" % (SR, HOP))
    command = [ffmpeg, "-v", "error", "-ss", "%.3f" % start, "-i", str(path), "-t", "%.3f" % duration, *select,
               "-ac", "1", "-af", af, "-f", "null", "-"]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    levels = array.array("f")
    for raw in proc.stdout:
        if raw.startswith(b"lavfi.astats.Overall.RMS_level="):
            value = raw.split(b"=", 1)[1].strip()
            try:
                level = float(value)
            except ValueError:
                level = -100.0
            levels.append(level if math.isfinite(level) else -100.0)
    proc.wait(timeout=60)
    if not levels:
        raise ActivityError("Could not read audio levels: %s" % proc.stderr.read().decode(errors="replace")[-200:])
    return levels


def game_events(levels, bins, rise_db=6.0, onset_db=8.0, floor_db=-50.0, window_sec=30.0):
    """Per-bin event flags: louder than the track's rolling median by ``rise_db``, or a sharp onset."""
    per_bin = max(1, int(round(BIN / 0.05)))
    bin_level = [max(levels[i:i + per_bin]) if levels[i:i + per_bin] else -100.0 for i in range(0, per_bin * bins, per_bin)]
    bin_level += [-100.0] * (bins - len(bin_level))
    half = max(1, int(window_sec / BIN / 2))
    events = [False] * bins
    # Rolling median from a decimated history keeps this fast on hour-long tracks.
    step = max(1, int(1.0 / BIN))
    for b in range(bins):
        level = bin_level[b]
        if level < floor_db:
            continue
        lo, hi = max(0, b - half), min(bins, b + half)
        window = sorted(bin_level[lo:hi:step]) or [level]
        median = window[len(window) // 2]
        onset = level - bin_level[b - 1] if b else 0.0
        if level >= median + rise_db or onset >= onset_db:
            events[b] = True
    return events, bin_level


# ------------------------------------------------------------------ picture

def picture_activity(ffmpeg, path, start, duration, bins, fps=4.0):
    """Per-bin motion (scene score) plus black and frozen spans, in one ffmpeg pass."""
    vf = ("fps=%g,scale=160:-2,blackdetect=d=1.0:pix_th=0.10,freezedetect=n=-55dB:d=2,"
          "select='gte(scene,0)',metadata=print:key=lavfi.scene_score" % fps)
    command = [ffmpeg, "-hide_banner", "-ss", "%.3f" % start, "-i", str(path), "-t", "%.3f" % duration,
               "-an", "-vf", vf, "-f", "null", "-"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=max(120, int(duration * 0.6) + 120))
    except subprocess.TimeoutExpired:
        raise ActivityError("Scanning the picture took too long.")
    log = result.stderr.decode(errors="replace")
    motion = [0.0] * bins
    pts = None
    for line in log.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            pts = float(m.group(1))
            continue
        m = re.search(r"lavfi\.scene_score=([\d.]+)", line)
        if m and pts is not None:
            b = int(pts / BIN)
            if 0 <= b < bins:
                motion[b] = max(motion[b], float(m.group(1)))
    def spans(start_key, end_key):
        found, opened = [], None
        for line in log.splitlines():
            a = re.search(start_key + r":\s*([\d.]+)", line)
            if a:
                opened = float(a.group(1))
            e = re.search(end_key + r":\s*([\d.]+)", line)
            if e and opened is not None:
                found.append((opened, float(e.group(1))))
                opened = None
        if opened is not None:
            found.append((opened, duration))
        return found
    black = spans("black_start", "black_end")
    frozen = spans(r"lavfi\.freezedetect\.freeze_start", r"lavfi\.freezedetect\.freeze_end")
    has_video = "Stream" in log and "Video" in log
    return motion, black, frozen, has_video


# ---------------------------------------------------------------- combining

def _mark(flags, start_sec, end_sec, value=True):
    a, b = max(0, int(start_sec / BIN)), min(len(flags), int(math.ceil(end_sec / BIN)))
    for i in range(a, b):
        flags[i] = value


def motion_spikes(motion, floor=0.02, factor=3.0, window_sec=30.0):
    """Visual events: scene-score spikes well above the clip's own recent level (flashes, explosions, kill-feed
    pops, hard cuts). Steady motion such as walking around doesn't count; quiet traversal is cuttable."""
    half = max(1, int(window_sec / BIN / 2))
    step = max(1, int(1.0 / BIN))
    spikes = [False] * len(motion)
    for b, m in enumerate(motion):
        if m < floor:
            continue
        window = sorted(motion[max(0, b - half):b + half:step]) or [m]
        if m >= factor * window[len(window) // 2]:
            spikes[b] = True
    return spikes


def classify(bins, words_sec, events, motion, black, frozen, preset):
    """Per-bin reasons to keep, and dead spans. Times are seconds from the clip window's start."""
    p = PRESETS[preset] if isinstance(preset, str) else preset
    speech = [False] * bins
    for w0, w1 in words_sec:
        _mark(speech, w0 - p["speech_pad"], w1 + p["speech_pad"])
    action = [False] * bins
    for b, hit in enumerate(events):
        if hit:
            _mark(action, b * BIN - p["pre"], (b + 1) * BIN + p["post"])
    moving = [False] * bins
    for b, hit in enumerate(motion_spikes(motion)):
        if hit:
            _mark(moving, b * BIN - p["pre"] / 2, (b + 1) * BIN + p["post"])
    dead_picture = [False] * bins
    for s0, s1 in black + frozen:
        _mark(dead_picture, s0, s1)
    keep, reason = [False] * bins, [None] * bins
    for b in range(bins):
        if speech[b]:
            keep[b], reason[b] = True, "speech"
        elif dead_picture[b]:
            keep[b], reason[b] = False, "loading/menu"
        elif action[b]:
            keep[b], reason[b] = True, "game audio"
        elif moving[b]:
            keep[b], reason[b] = True, "on-screen action"
    dead, b = [], 0
    while b < bins:
        if keep[b]:
            b += 1
            continue
        e = b
        while e < bins and not keep[e]:
            e += 1
        a_sec, b_sec = b * BIN + p["keep"], e * BIN - p["keep"]
        if b == 0:
            a_sec = 0.0
        if e == bins:
            b_sec = bins * BIN
        if b_sec - a_sec >= p["min_dead"]:
            share = sum(dead_picture[b:e]) / float(e - b)
            why = "loading/menu" if share >= 0.5 else "quiet (no speech or action)"
            dead.append({"start_sec": round(a_sec, 3), "end_sec": round(b_sec, 3), "seconds": round(b_sec - a_sec, 2), "why": why})
        b = e
    totals = {}
    for r in reason:
        totals[r or "dead"] = totals.get(r or "dead", 0) + BIN
    return dead, {k: round(v, 1) for k, v in totals.items()}


def gate_words(words_sec, mic_levels, margin_db=6.0, slack_sec=0.2):
    """Drop words "spoken" into silence. Whisper invents text ("Thank you.") on quiet audio. A real word has
    sound on the mic, within ``slack_sec`` of its (approximate) timing, at least ``margin_db`` above the mic's
    floor (the 10th percentile of the whole track, digital silence included). Quiet real speech still passes:
    this only removes words with essentially nothing on the mic."""
    if not mic_levels:
        return words_sec, 0
    ordered = sorted(mic_levels)
    floor = ordered[len(ordered) // 10]
    kept, dropped = [], 0
    for w0, w1 in words_sec:
        a = max(0, int((w0 - slack_sec) / 0.05))
        b = max(a + 1, int(math.ceil((w1 + slack_sec) / 0.05)))
        if max(mic_levels[a:b], default=-100.0) >= floor + margin_db:
            kept.append((w0, w1))
        else:
            dropped += 1
    return kept, dropped


def analyze(source, ffmpeg, words, preset="youtube", game_stream=None, use_picture=True, mic_stream=None):
    """Activity map for one clip. ``words`` carry absolute timeline frames (from transcribe)."""
    duration = float(source["duration_sec"])
    if duration <= 0:
        raise ActivityError("The clip has no duration.")
    bins = max(1, int(math.ceil(duration / BIN)))
    fps = float(source["fps"])
    origin = float(source["timeline_start_frame"])
    words_sec = [((w["start_frame"] - origin) / fps, (w["end_frame"] - origin) / fps) for w in words]
    dropped = 0
    if mic_stream is not None and mic_stream != game_stream:
        mic_levels = hop_levels(ffmpeg, source["file_path"], source["source_start_sec"], duration, mic_stream)
        words_sec, dropped = gate_words(words_sec, mic_levels)
    levels = hop_levels(ffmpeg, source["file_path"], source["source_start_sec"], duration, game_stream)
    events, _ = game_events(levels, bins)
    if use_picture:
        motion, black, frozen, has_video = picture_activity(ffmpeg, source["file_path"], source["source_start_sec"], duration, bins)
    else:
        motion, black, frozen, has_video = [0.0] * bins, [], [], False
    dead, totals = classify(bins, words_sec, events, motion, black, frozen, preset)
    end_frame = float(source.get("timeline_end_frame") or origin + duration * fps)
    ranges = []
    for d in dead:
        a = int(round(origin + d["start_sec"] * fps))
        b = int(min(end_frame, round(origin + d["end_sec"] * fps)))
        if b > a:
            ranges.append({"start_frame": a, "end_frame": b, "seconds": d["seconds"], "why": d["why"]})
    return {"item_id": source.get("item_id"), "name": source.get("name"), "analyzed_sec": round(duration, 1),
            "seconds_by_reason": totals, "dead": ranges, "picture_checked": bool(use_picture and has_video),
            "phantom_words_dropped": dropped}


def pick_mic_stream(source, home, ffmpeg, streams, excerpt_sec=90.0):
    """Which audio track is the voice? Transcribe a short excerpt of each and score confident words.
    Whisper invents words ("Thank you.") on noise and game audio, so low-probability words don't count."""
    from bridge import transcribe
    if len(streams) <= 1:
        return 0, []
    duration = float(source["duration_sec"])
    start = float(source["source_start_sec"]) + max(0.0, duration / 2 - excerpt_sec / 2)
    probe = dict(source, source_start_sec=start, duration_sec=min(duration, excerpt_sec))
    scores = []
    for s in streams:
        try:
            words = transcribe.transcribe_source(probe, home, ffmpeg=ffmpeg, stream=s["stream"])["words"]
        except Exception:
            words = []
        confident = [w for w in words if (w.get("p") or 0) >= 0.5]
        scores.append({"stream": s["stream"], "title": s.get("title"), "words": len(words), "confident_words": len(confident)})
    best = max(scores, key=lambda x: (x["confident_words"], x["words"]))
    return best["stream"], scores
