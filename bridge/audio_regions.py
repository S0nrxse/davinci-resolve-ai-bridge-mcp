"""Speech regions and loudness from a clip's SOURCE audio, for cut points that don't drift into pauses.

Two analyses, both run through ffmpeg outside Resolve:

- ``speech_regions``: Whisper word timings (even with DTW) drift into the surrounding silence, word ends most
  of all. Short-hop (10 ms) RMS energy from the source file snaps each word to the nearest energy edge, merges
  words into speech regions, and reports the pauses between them with a suggested cut frame at each pause's
  quietest hop. Energy with no word (breaths, laughs, music, room noise) and words with no energy (likely
  Whisper phantoms) are reported separately, never silently dropped.
- ``measure_loudness``: EBU R128 integrated loudness, loudness range and true peak per clip, the gain to a
  target, and the programme loudness of all clips played back to back.

Both read the media file, not Resolve's mix: clip volume, track faders, fades, mute and effects are not heard.
Nothing here applies gain or edits the timeline. Standard library only; never writes to stdout.
"""

import array
import bisect
import math
import os
import re
import subprocess
import tempfile

HOP_SEC = 0.01  # 10 ms energy hops.
RATE = 16000  # Analysis sample rate for speech energy (speech edges live well below 8 kHz).
BRIDGE_SEC = 0.06  # Quiet gaps shorter than this inside a sound (plosive closures) don't split it.
SUPPORT_SLACK_SEC = 0.05  # A word needs a loud hop within its own span widened by this much.
TRUST_MARGIN_DB = 6.0  # A hop this far over the noise floor is audible (quiet speech), not room noise.
MAX_TAIL_SEC = 0.35  # A word's trusted end never runs more than this past its last loud hop.
THRESHOLD_MIN_DB = -75.0  # Adaptive threshold is never below this (digital silence is not "quiet speech").
THRESHOLD_CAP_DB = -25.0  # ...and never above this.
FLOOR_MIN_DB = -90.0
SILENT_DB = -100.0  # Level reported for digital silence.
MAX_WINDOW_SEC = 3 * 3600.0
EXACT_PROGRAM_MAX = 64  # Clips measured together in one exact ffmpeg pass; beyond this, pooled blocks.
MAX_SOURCES = 1000
GATE_LUFS = -70.0  # EBU R128 absolute gate.

SOURCE_NOTE = ("Measured on the clip's SOURCE audio file, not the Fairlight mix: Resolve clip volume, track "
               "faders, fades, mute, pan and audio effects are not included.")
LOUDNESS_NOTE = (SOURCE_NOTE + " Gains are relative to the clip's current level only if its clip volume is still "
                 "0 dB. Measuring does NOT apply gain: measure_loudness(apply=True) or set_clip_volume sets "
                 "Inspector > Audio > Volume on each clip; then re-check the rendered mix.")


class AudioRegionsError(RuntimeError):
    """Audio analysis failed for a reason the user can act on (bad input, missing media, ffmpeg failure)."""


# ------------------------------------------------------------------ ffmpeg

def _stderr_tail(data, limit=400):
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data or "")
    return text.strip()[-limit:]


def _run(cmd, timeout):
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise AudioRegionsError("ffmpeg took longer than %d s and was stopped." % timeout)
    except OSError as exc:
        raise AudioRegionsError("Could not run ffmpeg at %s (%s)." % (cmd[0], exc))


def _map(stream):
    return ["-map", "0:a:%d" % int(stream)] if stream is not None else ["-vn"]


def _check_ffmpeg(ffmpeg):
    if not ffmpeg:
        raise AudioRegionsError("ffmpeg is needed for audio analysis (brew install ffmpeg).")


def fine_hop_levels(ffmpeg, path, start, duration, stream=None, hop_sec=HOP_SEC, rate=RATE):
    """RMS level in dBFS per ``hop_sec`` of [start, start + duration) of the file (all channels pooled, so
    out-of-phase stereo can't cancel). Digital silence reads ``SILENT_DB``. Streamed line by line."""
    _check_ffmpeg(ffmpeg)
    samples = max(1, int(round(hop_sec * rate)))
    af = ("aresample=%d,asetnsamples=n=%d:p=0,astats=metadata=1:reset=1,"
          "ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-" % (rate, samples))
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-v", "error", "-ss", "%.6f" % start, "-t", "%.6f" % duration,
           "-i", str(path), *_map(stream), "-af", af, "-f", "null", "-"]
    levels = array.array("f")
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err)
        except OSError as exc:
            raise AudioRegionsError("Could not run ffmpeg at %s (%s)." % (ffmpeg, exc))
        try:
            for raw in proc.stdout:
                if raw.startswith(b"lavfi.astats.Overall.RMS_level="):
                    try:
                        value = float(raw.split(b"=", 1)[1])
                    except ValueError:
                        value = SILENT_DB
                    levels.append(max(SILENT_DB, value) if math.isfinite(value) else SILENT_DB)
            code = proc.wait(timeout=max(120, int(duration) + 60))
        except subprocess.TimeoutExpired:
            proc.kill()
            raise AudioRegionsError("Decoding the audio took too long; try a shorter range.")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()
        err.seek(0)
        tail = _stderr_tail(err.read())
    if code != 0 or not levels:
        raise AudioRegionsError("ffmpeg could not decode this clip's audio: %s" % (tail or "no audio samples"))
    return levels


# -------------------------------------------------------- pure: speech edges

def _percentile(ordered, fraction):
    if not ordered:
        return SILENT_DB
    return ordered[min(len(ordered) - 1, max(0, int(fraction * (len(ordered) - 1) + 0.5)))]


def adaptive_threshold(levels, floor_pct=0.10, speech_pct=0.95):
    """Speech/quiet threshold from the window itself: noise floor = low percentile, speech = high percentile,
    threshold 35% of the way up (at least 6 dB over the floor), at least 6 dB under speech, clamped to
    [THRESHOLD_MIN_DB, THRESHOLD_CAP_DB]."""
    ordered = sorted(levels)
    floor = max(FLOOR_MIN_DB, _percentile(ordered, floor_pct))
    speech = _percentile(ordered, speech_pct)
    contrast = max(0.0, speech - floor)
    threshold = floor + max(6.0, 0.35 * contrast)
    threshold = min(threshold, speech - 6.0, THRESHOLD_CAP_DB)
    threshold = max(threshold, THRESHOLD_MIN_DB)
    return {"threshold_db": round(threshold, 1), "floor_db": round(floor, 1), "speech_db": round(speech, 1),
            "contrast_db": round(contrast, 1)}


def _loud_runs(loud, hop_sec, bridge_sec=BRIDGE_SEC):
    """[(start_sec, end_sec)] of loud hops, joining runs separated by less than ``bridge_sec``."""
    runs, start = [], None
    for i, flag in enumerate(loud):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append([start, i])
            start = None
    if start is not None:
        runs.append([start, len(loud)])
    bridge = int(round(bridge_sec / hop_sec))
    merged = []
    for run in runs:
        if merged and run[0] - merged[-1][1] < bridge:
            merged[-1][1] = run[1]
        else:
            merged.append(run)
    return [(a * hop_sec, b * hop_sec) for a, b in merged]


def _nearest(candidates, target, lo, hi, prefer_later):
    """Candidate time in [lo, hi] nearest to target; ties go later (ends) or earlier (starts)."""
    a, b = bisect.bisect_left(candidates, lo - 1e-9), bisect.bisect_right(candidates, hi + 1e-9)
    best = None
    for t in candidates[a:b]:
        key = (abs(t - target), -t if prefer_later else t)
        if best is None or key < best[0]:
            best = (key, t)
    return None if best is None else best[1]


def _peak(levels, hop_sec, start, end):
    a, b = max(0, int(math.floor(start / hop_sec))), int(math.ceil(end / hop_sec))
    window = levels[a:max(a + 1, b)]
    return round(max(window), 1) if len(window) else SILENT_DB


def _subtract(span, holes):
    """Parts of ``span`` outside every (sorted, disjoint) hole: [(a, b, touches_a_hole)]."""
    a, b = span
    pieces, cursor = [], a
    for h0, h1 in holes:
        if h1 <= cursor:
            continue
        if h0 >= b:
            break
        if h0 > cursor:
            pieces.append((cursor, h0))
        cursor = max(cursor, h1)
    if cursor < b:
        pieces.append((cursor, b))
    edges = {round(x, 9) for hole in holes for x in hole}
    return [(p0, p1, round(p0, 9) in edges or round(p1, 9) in edges) for p0, p1 in pieces]


def trust_level(threshold_db, floor_db=None, margin_db=TRUST_MARGIN_DB):
    """Level above which a hop counts as audible (not room noise): ``margin_db`` over the noise floor, never
    above the speech threshold. Without a floor, the threshold itself."""
    if floor_db is None:
        return float(threshold_db)
    return min(float(threshold_db), float(floor_db) + float(margin_db))


def _word_trust(levels, hop_sec, lo, hi, trust_db, threshold_db, max_tail):
    """Audible extent of a word inside [lo, hi): first/last hop at or above ``trust_db``, with the end capped
    to ``max_tail`` seconds past the last hop at or above ``threshold_db`` (Whisper stretches a phrase-final
    word across the following pause; quiet room noise must not keep that stretch alive)."""
    n = len(levels)
    a = min(n, max(0, int(math.floor(lo / hop_sec + 1e-9))))
    b = min(n, max(a, int(math.ceil(hi / hop_sec - 1e-9))))
    above = [i for i in range(a, b) if levels[i] >= trust_db]
    if not above:
        return {"trusted": False, "trusted_start": None, "trusted_end": None, "last_loud": None, "tail_capped": False}
    start, end = above[0] * hop_sec, (above[-1] + 1) * hop_sec
    loud = [i for i in range(a, b) if levels[i] >= threshold_db]
    last_loud = (loud[-1] + 1) * hop_sec if loud else None
    capped = False
    if last_loud is not None and hi > last_loud + max_tail + 1e-9:
        capped = True  # The word's own end ran on past the cap.
        end = min(end, last_loud + max_tail)
    return {"trusted": True, "trusted_start": start, "trusted_end": max(end, start + hop_sec),
            "last_loud": last_loud, "tail_capped": capped}


def refine_regions(levels, words, *, hop_sec=HOP_SEC, threshold_db, duration_sec=None, pad_seconds=0.08,
                   merge_gap_seconds=0.35, max_shift_seconds=0.2, min_region_seconds=0.15, floor_db=None,
                   trust_margin_db=TRUST_MARGIN_DB, max_tail_seconds=MAX_TAIL_SEC):
    """Pure core of ``speech_regions``. ``levels``: dBFS per hop from the window start. ``words``: dicts with
    ``start``/``end`` seconds from the window start and ``word`` text. Everything returned is in window seconds.

    Returns {"words", "regions", "energy_only", "unsupported_words", "pauses"}: refined words (with their
    original times), padded speech regions, sound outside every word, words with no sound under them, and the
    gaps between regions, each with ``cut_sec`` at its quietest hop.

    Every word also carries floor-aware trust fields (``trusted``, ``trusted_start``, ``trusted_end``,
    ``last_loud``, ``tail_capped``): the part of the word whose audio is above the room-noise floor
    (``trust_level``), searched over both its original and refined span, with the end capped to
    ``max_tail_seconds`` past its last hop above the speech threshold. Quiet speech below the threshold but
    above the floor stays trusted; a word with nothing above the floor is untrusted. These fields do not change
    ``start``/``end``, regions or pauses.
    """
    n = len(levels)
    duration = float(duration_sec) if duration_sec is not None else n * hop_sec
    loud = [lv >= threshold_db for lv in levels]
    onsets = [i * hop_sec for i in range(n) if loud[i] and (i == 0 or not loud[i - 1])]
    offsets = [(i + 1) * hop_sec for i in range(n) if loud[i] and (i == n - 1 or not loud[i + 1])]
    prefix = [0]
    for flag in loud:
        prefix.append(prefix[-1] + (1 if flag else 0))

    def any_loud(start, end):
        a = min(n, max(0, int(math.floor(start / hop_sec))))
        b = min(n, max(a, int(math.ceil(end / hop_sec))))
        return prefix[b] > prefix[a]

    ordered = []
    for index, w in enumerate(words):
        s = max(0.0, min(duration, float(w["start"])))
        e = max(s, min(duration, float(w["end"])))
        ordered.append({"index": index, "word": str(w.get("word", "")).strip(), "orig_start": s, "orig_end": e})
    ordered.sort(key=lambda w: (w["orig_start"], w["orig_end"], w["index"]))

    supported, unsupported = [], []
    for w in ordered:
        if any_loud(w["orig_start"] - SUPPORT_SLACK_SEC, w["orig_end"] + SUPPORT_SLACK_SEC):
            supported.append(w)
        else:
            w.update(start=w["orig_start"], end=w["orig_end"], supported=False, snapped_start=False,
                     snapped_end=False, peak_db=_peak(levels, hop_sec, w["orig_start"], w["orig_end"]))
            unsupported.append(w)

    # Snap supported words to energy edges, in order, never crossing a (supported) neighbour.
    prev_end = 0.0
    for k, w in enumerate(supported):
        s, e = w["orig_start"], w["orig_end"]
        next_start = supported[k + 1]["orig_start"] if k + 1 < len(supported) else duration
        lo = max(prev_end, s - max_shift_seconds)
        start = _nearest(onsets, s, lo, min(s + max_shift_seconds, max(e, s + hop_sec) - hop_sec), False)
        snapped_start = start is not None
        if start is None:
            start = max(s, prev_end)
        hi_bound = max(next_start, start + hop_sec)
        end = _nearest(offsets, e, max(start + hop_sec, e - max_shift_seconds), min(hi_bound, e + max_shift_seconds), True)
        snapped_end = end is not None
        if end is None:
            end = max(start + hop_sec, min(e, hi_bound))
        end = min(end, duration) if duration > start else end
        w.update(start=start, end=end, supported=True, snapped_start=snapped_start, snapped_end=snapped_end)
        prev_end = end

    trust_db = trust_level(threshold_db, floor_db, trust_margin_db)
    for w in supported + unsupported:
        lo, hi = min(w["start"], w["orig_start"]), max(w["end"], w["orig_end"])
        w.update(_word_trust(levels, hop_sec, lo, hi, trust_db, threshold_db, max_tail_seconds))

    # Merge refined words into regions.
    regions = []
    for w in supported:
        if regions and w["start"] - regions[-1]["end"] < merge_gap_seconds:
            regions[-1]["end"] = max(regions[-1]["end"], w["end"])
            regions[-1]["words"].append(w)
        else:
            regions.append({"start": w["start"], "end": w["end"], "words": [w]})
    speech_spans = [(r["start"], r["end"]) for r in regions]

    # Pad, clamped to the window and to half the gap to each neighbour.
    padded = []
    for i, r in enumerate(regions):
        left_room = r["start"] - (regions[i - 1]["end"] if i else 0.0)
        right_room = (regions[i + 1]["start"] if i + 1 < len(regions) else duration) - r["end"]
        pad_left = max(0.0, min(pad_seconds, left_room / 2.0 if i else left_room))
        pad_right = max(0.0, min(pad_seconds, right_room / 2.0 if i + 1 < len(regions) else right_room))
        padded.append({"start": max(0.0, r["start"] - pad_left), "end": min(duration, r["end"] + pad_right),
                       "speech_start": r["start"], "speech_end": r["end"],
                       "text": " ".join(w["word"] for w in r["words"] if w["word"]),
                       "word_count": len(r["words"]), "peak_db": _peak(levels, hop_sec, r["start"], r["end"])})

    # Sound outside every word: breaths, laughs, music, room noise.
    energy_only, dropped_short = [], 0
    for run in _loud_runs(loud, hop_sec):
        for a, b, near in _subtract((run[0], min(run[1], duration)), speech_spans):
            if b - a < min_region_seconds:
                dropped_short += 1
                continue
            energy_only.append({"start": a, "end": b, "adjacent_to_speech": near,
                                "peak_db": _peak(levels, hop_sec, a, b)})

    pauses = []
    for i in range(len(padded) - 1):
        a, b = padded[i]["end"], padded[i + 1]["start"]
        if b <= a:
            continue
        h0, h1 = int(math.ceil(a / hop_sec - 1e-9)), int(math.floor(b / hop_sec + 1e-9))
        h1 = min(h1, n)
        middle = (a + b) / 2.0
        if h1 > h0:
            quiet = min(range(h0, h1), key=lambda h: (levels[h], abs((h + 0.5) * hop_sec - middle)))
            cut, quiet_db = (quiet + 0.5) * hop_sec, round(levels[quiet], 1)
        else:
            cut, quiet_db = middle, None
        inside = sum(1 for e in energy_only if e["start"] < b and e["end"] > a)
        pauses.append({"start": a, "end": b, "cut_sec": min(max(cut, a), b), "quietest_db": quiet_db,
                       "after": padded[i]["text"].split(" ")[-1] if padded[i]["text"] else "",
                       "before": padded[i + 1]["text"].split(" ")[0] if padded[i + 1]["text"] else "",
                       "energy_only": inside})

    refined = sorted(supported + unsupported, key=lambda w: w["index"])
    return {"words": refined, "regions": padded, "energy_only": energy_only, "unsupported_words": unsupported,
            "pauses": pauses, "energy_only_dropped_short": dropped_short, "trust_db": round(trust_db, 1)}


# ------------------------------------------------------------ speech_regions

def _number(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AudioRegionsError("%s must be a number." % name)
    if not math.isfinite(number):
        raise AudioRegionsError("%s must be finite." % name)
    return number


def _window(source):
    if not isinstance(source, dict):
        raise AudioRegionsError("source must be a transcription_source entry (a dict).")
    for key in ("file_path", "source_start_sec", "duration_sec", "timeline_start_frame", "fps"):
        if source.get(key) in (None, ""):
            raise AudioRegionsError("source is missing %s." % key)
    fps = _number(source["fps"], "fps")
    duration = _number(source["duration_sec"], "duration_sec")
    start = _number(source["source_start_sec"], "source_start_sec")
    origin = _number(source["timeline_start_frame"], "timeline_start_frame")
    if fps <= 0:
        raise AudioRegionsError("fps must be positive.")
    if start < 0:
        raise AudioRegionsError("source_start_sec must not be negative.")
    end_frame = source.get("timeline_end_frame")
    end_frame = _number(end_frame, "timeline_end_frame") if end_frame is not None else origin + duration * fps
    return fps, duration, start, origin, end_frame


def _check_stream(stream):
    if stream is not None and (isinstance(stream, bool) or not isinstance(stream, int) or stream < 0):
        raise AudioRegionsError("stream must be a non-negative audio stream index (audio-relative).")


def _identity(source):
    return {"item_id": source.get("item_id"), "name": source.get("name"), "track": source.get("track"),
            "timeline_start_frame": source.get("timeline_start_frame"),
            "timeline_end_frame": source.get("timeline_end_frame")}


def speech_regions(source, words, ffmpeg, *, threshold_db=None, pad_seconds=0.08, merge_gap_seconds=0.35,
                   max_shift_seconds=0.2, min_region_seconds=0.15, stream=None, max_tail_seconds=MAX_TAIL_SEC,
                   trust_margin_db=TRUST_MARGIN_DB):
    """Speech regions, pauses and cut points for one clip, from its words and its source-audio energy.

    ``source``: a ``transcription_source`` entry. ``words``: ``transcribe_source`` words (``word``,
    ``start_frame``, ``end_frame`` in absolute timeline frames). Returns absolute timeline frames, exclusive
    ends; ``*_sec`` values are seconds from the clip's start on the timeline.

    Each word also has ``trusted`` and ``trusted_start_frame`` / ``trusted_end_frame`` (None when untrusted):
    the part of the word whose audio is above the room-noise floor, its end capped to ``max_tail_seconds``
    past the last hop above the threshold (``tail_capped``). See ``refine_regions``.
    """
    fps, duration, start, origin, end_limit = _window(source)
    if duration <= 0:
        raise AudioRegionsError("The clip has no duration to analyse.")
    if duration > MAX_WINDOW_SEC:
        raise AudioRegionsError("Clips longer than 3 hours are not analysed in one go.")
    if not os.path.isfile(str(source["file_path"])):
        raise AudioRegionsError("The clip's media file is offline: %s" % source["file_path"])
    if threshold_db is not None:
        threshold_db = _number(threshold_db, "threshold_db")
        if not -100.0 <= threshold_db <= 0.0:
            raise AudioRegionsError("threshold_db must be between -100 and 0 dBFS.")
    params = {}
    for name, value, low, high in (("pad_seconds", pad_seconds, 0.0, 2.0),
                                   ("merge_gap_seconds", merge_gap_seconds, 0.0, 10.0),
                                   ("max_shift_seconds", max_shift_seconds, 0.0, 1.0),
                                   ("min_region_seconds", min_region_seconds, 0.0, 10.0)):
        value = _number(value, name)
        if not low <= value <= high:
            raise AudioRegionsError("%s must be between %g and %g seconds." % (name, low, high))
        params[name] = value
    for name, value, low, high in (("max_tail_seconds", max_tail_seconds, 0.0, 5.0),
                                   ("trust_margin_db", trust_margin_db, 0.0, 40.0)):
        value = _number(value, name)
        if not low <= value <= high:
            raise AudioRegionsError("%s must be between %g and %g." % (name, low, high))
        params[name] = value
    _check_stream(stream)
    if not isinstance(words, (list, tuple)):
        raise AudioRegionsError("words must be a list of transcript words.")

    window_words, outside = [], 0
    for w in words:
        try:
            s = (float(w["start_frame"]) - origin) / fps
            e = (float(w["end_frame"]) - origin) / fps
        except (KeyError, TypeError, ValueError):
            raise AudioRegionsError("Each word needs start_frame and end_frame (absolute timeline frames).")
        if e <= 0 or s >= duration:
            outside += 1
            continue
        window_words.append({"start": s, "end": max(e, s), "word": w.get("word", "")})

    levels = fine_hop_levels(ffmpeg, source["file_path"], start, duration, stream)
    decoded = len(levels) * HOP_SEC
    stats = adaptive_threshold(levels)
    if threshold_db is None:
        threshold, method = stats["threshold_db"], "adaptive"
    else:
        threshold, method = threshold_db, "given"
    result = refine_regions(levels, window_words, hop_sec=HOP_SEC, threshold_db=threshold, duration_sec=duration,
                            floor_db=stats["floor_db"], **params)

    def frame_start(t):
        return int(max(math.floor(origin + 1e-6), min(end_limit, math.floor(origin + t * fps + 1e-6))))

    def frame_end(t):
        return int(max(math.floor(origin + 1e-6), min(end_limit, math.ceil(origin + t * fps - 1e-6))))

    def secs(t):
        return round(t, 3)

    regions = []
    for r in result["regions"]:
        regions.append({"start_frame": frame_start(r["start"]), "end_frame": frame_end(r["end"]),
                        "start_sec": secs(r["start"]), "end_sec": secs(r["end"]), "seconds": secs(r["end"] - r["start"]),
                        "speech_start_frame": frame_start(r["speech_start"]),
                        "speech_end_frame": frame_end(r["speech_end"]),
                        "text": r["text"], "word_count": r["word_count"], "peak_db": r["peak_db"]})
    pauses, cut_points = [], []
    for i, p in enumerate(result["pauses"]):
        a, b = regions[i]["end_frame"], regions[i + 1]["start_frame"]
        if b <= a:
            continue
        cut = min(b - 1, max(a, int(math.floor(origin + p["cut_sec"] * fps + 1e-6))))
        pauses.append({"start_frame": a, "end_frame": b, "start_sec": secs(p["start"]), "end_sec": secs(p["end"]),
                       "seconds": secs(p["end"] - p["start"]), "frames": b - a, "cut_frame": cut,
                       "cut_sec": secs(p["cut_sec"]), "quietest_db": p["quietest_db"], "after": p["after"],
                       "before": p["before"], "energy_only_inside": p["energy_only"]})
        cut_points.append(cut)
    energy_only = [{"start_frame": frame_start(e["start"]), "end_frame": frame_end(e["end"]),
                    "start_sec": secs(e["start"]), "end_sec": secs(e["end"]), "seconds": secs(e["end"] - e["start"]),
                    "peak_db": e["peak_db"], "adjacent_to_speech": e["adjacent_to_speech"]}
                   for e in result["energy_only"]]
    unsupported = [{"word": w["word"], "start_frame": frame_start(w["orig_start"]), "end_frame": frame_end(w["orig_end"]),
                    "start_sec": secs(w["orig_start"]), "end_sec": secs(w["orig_end"]), "peak_db": w["peak_db"]}
                   for w in result["unsupported_words"]]
    refined = [{"word": w["word"], "start_frame": frame_start(w["start"]), "end_frame": frame_end(w["end"]),
                "original_start_frame": frame_start(w["orig_start"]), "original_end_frame": frame_end(w["orig_end"]),
                "supported": w["supported"], "snapped_start": w["snapped_start"], "snapped_end": w["snapped_end"],
                "trusted": w["trusted"],
                "trusted_start_frame": frame_start(w["trusted_start"]) if w["trusted"] else None,
                "trusted_end_frame": frame_end(w["trusted_end"]) if w["trusted"] else None,
                "tail_capped": w["tail_capped"]}
               for w in result["words"]]

    warnings = []
    if decoded < duration - 0.1:
        warnings.append("Only %.2f s of the %.2f s window had audio; the media may be shorter than the clip."
                        % (decoded, duration))
    if result["unsupported_words"]:
        warnings.append("%d word(s) have no sound above the threshold under them (likely Whisper phantoms, or "
                        "speech quieter than %.1f dBFS); they are listed in unsupported_words and excluded from "
                        "regions." % (len(result["unsupported_words"]), threshold))
    if method == "adaptive" and stats["contrast_db"] < 10.0:
        warnings.append("Little level contrast in this window (%.1f dB between floor and speech); edges are "
                        "unreliable. Pass threshold_db to override." % stats["contrast_db"])
    analysis = {"hop_seconds": HOP_SEC, "sample_rate": RATE, "threshold_db": round(threshold, 1),
                "threshold_method": method, "floor_db": stats["floor_db"], "speech_db": stats["speech_db"],
                "trust_db": result["trust_db"],
                "decoded_seconds": round(decoded, 3), "window_seconds": round(duration, 3),
                "source_start_sec": start, "stream": stream, "words_in": len(words), "words_outside_window": outside,
                "energy_only_dropped_short": result["energy_only_dropped_short"], **params,
                "warnings": warnings,
                "note": SOURCE_NOTE + " Regions and cut points are suggestions; nothing on the timeline was changed."}
    return {**_identity(source), "fps": fps, "regions": regions, "pauses": pauses, "cut_points": cut_points,
            "energy_only": energy_only, "unsupported_words": unsupported, "words": refined, "analysis": analysis}


# ----------------------------------------------------------- measure_loudness

def parse_ebur128_summary(text):
    """{"integrated_lufs", "lra_lu", "true_peak_dbtp"} from ffmpeg ebur128's Summary (stderr). -inf -> None."""
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else ""

    def grab(pattern):
        found = re.search(pattern, summary)
        if not found or found.group(1).startswith("-inf"):
            return None
        try:
            value = float(found.group(1))
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    return {"found": bool(summary),
            "integrated_lufs": grab(r"I:\s+(-?[\d.]+|-inf)\s+LUFS"),
            "lra_lu": grab(r"LRA:\s+(-?[\d.]+|-inf)\s+LU\b"),
            "true_peak_dbtp": grab(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS")}


def _momentary(stdout):
    """lavfi.r128.M values (one per 100 ms frame) from ametadata print output."""
    values = []
    for line in stdout.splitlines():
        if line.startswith(b"lavfi.r128.M="):
            try:
                value = float(line.split(b"=", 1)[1])
            except ValueError:
                continue
            if math.isfinite(value):
                values.append(value)
    return values


def gated_loudness(momentary):
    """BS.1770 gated integrated loudness from 400 ms block loudness values (absolute -70, relative -10 LU)."""
    blocks = [m for m in momentary if m > GATE_LUFS]
    if not blocks:
        return None
    energy = lambda m: 10.0 ** ((m + 0.691) / 10.0)
    relative = -0.691 + 10.0 * math.log10(sum(energy(m) for m in blocks) / len(blocks)) - 10.0
    gated = [m for m in blocks if m > relative]
    if not gated:
        return None
    return -0.691 + 10.0 * math.log10(sum(energy(m) for m in gated) / len(gated))


_NO_AUDIO = ("matches no streams", "does not contain any stream", "Failed to set value '0:a:")


def _gain(integrated, peak, target, ceiling):
    if integrated is None:
        return {"gain_db": None, "peak_limited": False, "peak_limited_gain_db": None}
    gain = target - integrated
    limited = peak is not None and peak + gain > ceiling
    return {"gain_db": round(gain, 1), "peak_limited": bool(limited),
            "peak_limited_gain_db": round(ceiling - peak, 1) if limited else None}


def _measure_one(ffmpeg, source, stream, want_blocks):
    start = float(source["source_start_sec"])
    duration = float(source["duration_sec"])
    af = "ebur128=peak=true:framelog=quiet"
    if want_blocks:
        af += ":metadata=1,ametadata=mode=print:key=lavfi.r128.M:file=-"
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info", "-ss", "%.6f" % start, "-t", "%.6f" % duration,
           "-i", str(source["file_path"]), "-map", "0:a:%d" % int(stream or 0), "-af", af, "-f", "null", "-"]
    proc = _run(cmd, timeout=max(120, int(duration) + 60))
    text = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        if any(marker in text for marker in _NO_AUDIO):
            return None, "The media has no audio stream %d." % int(stream or 0), []
        raise AudioRegionsError("ffmpeg loudness measurement failed for %s: %s"
                                % (source.get("name") or source["file_path"], _stderr_tail(text)))
    data = parse_ebur128_summary(text)
    if not data["found"]:
        raise AudioRegionsError("ffmpeg printed no EBU R128 summary for %s: %s"
                                % (source.get("name") or source["file_path"], _stderr_tail(text)))
    return data, None, _momentary(proc.stdout) if want_blocks else []


def _loudness_values(data, duration, target, ceiling):
    integrated, reason = data["integrated_lufs"], None
    if integrated is not None and integrated <= GATE_LUFS + 0.05:
        integrated = None
    if integrated is None:
        reason = ("Too short to measure: EBU R128 needs at least one 400 ms block." if duration < 0.4 else
                  "Too quiet to measure: nothing above the -70 LUFS absolute gate.")
    peak = data["true_peak_dbtp"]
    out = {"integrated_lufs": None if integrated is None else round(integrated, 1),
           "lra_lu": None if integrated is None or data["lra_lu"] is None else round(data["lra_lu"], 1),
           "true_peak_dbtp": None if peak is None else round(peak, 1), "reason": reason}
    out.update(_gain(integrated, peak, target, ceiling))
    return out


def _channel_counts(ffmpeg, paths, stream):
    from bridge import activity  # Standard library only; reuses its ffprobe lookup.
    counts = {}
    for path in paths:
        try:
            streams = activity.audio_streams(ffmpeg, path)
            counts[path] = streams[int(stream or 0)].get("channels") if len(streams) > int(stream or 0) else None
        except Exception:
            counts[path] = None
    return counts


def _program_exact(ffmpeg, measured, stream):
    paths = sorted({str(s["file_path"]) for s in measured})
    counts = _channel_counts(ffmpeg, paths, stream)
    uniform = len({counts[p] for p in paths}) == 1 and None not in counts.values()
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info"]
    labels, chains = [], []
    for i, s in enumerate(measured):
        cmd += ["-ss", "%.6f" % float(s["source_start_sec"]), "-t", "%.6f" % float(s["duration_sec"]),
                "-i", str(s["file_path"])]
        if uniform:
            labels.append("[%d:a:%d]" % (i, int(stream or 0)))
        else:
            chains.append("[%d:a:%d]aformat=channel_layouts=stereo[p%d]" % (i, int(stream or 0), i))
            labels.append("[p%d]" % i)
    graph = ";".join(chains + ["%sconcat=n=%d:v=0:a=1,ebur128=peak=true:framelog=quiet[out]"
                               % ("".join(labels), len(measured))])
    cmd += ["-filter_complex", graph, "-map", "[out]", "-f", "null", "-"]
    total = sum(float(s["duration_sec"]) for s in measured)
    proc = _run(cmd, timeout=max(180, int(total) + 120))
    text = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise AudioRegionsError("ffmpeg programme loudness measurement failed: %s" % _stderr_tail(text))
    data = parse_ebur128_summary(text)
    if not data["found"]:
        raise AudioRegionsError("ffmpeg printed no EBU R128 summary for the programme: %s" % _stderr_tail(text))
    note = ("One ffmpeg pass over every measured clip's source window, back to back in timeline order.")
    if not uniform:
        note += " Clips have different (or unknown) channel counts, so each was converted to stereo first."
    return data, note


def measure_loudness(sources, ffmpeg, *, target_lufs=-14.0, true_peak_ceiling_db=-1.0, stream=None):
    """EBU R128 loudness of each clip's source window, and of all clips back to back (the programme).

    ``sources``: ``transcription_source`` entries. ``stream`` picks the audio track (audio-relative index,
    default 0) for every clip. Returns {"clips", "program", "target_lufs", "true_peak_ceiling_db", "stream",
    "note"}. Nothing is applied: gains are suggestions only.
    """
    _check_ffmpeg(ffmpeg)
    target = _number(target_lufs, "target_lufs")
    ceiling = _number(true_peak_ceiling_db, "true_peak_ceiling_db")
    if not -70.0 <= target <= 0.0:
        raise AudioRegionsError("target_lufs must be between -70 and 0 LUFS.")
    if not -30.0 <= ceiling <= 0.0:
        raise AudioRegionsError("true_peak_ceiling_db must be between -30 and 0 dBTP.")
    _check_stream(stream)
    if not isinstance(sources, (list, tuple)) or not sources:
        raise AudioRegionsError("sources must be a non-empty list of transcription_source entries.")
    if len(sources) > MAX_SOURCES:
        raise AudioRegionsError("At most %d clips can be measured at once." % MAX_SOURCES)
    for s in sources:
        _window(s)

    order = sorted(range(len(sources)), key=lambda i: (float(sources[i]["timeline_start_frame"]), i))
    pooled = len(sources) > EXACT_PROGRAM_MAX
    clips, measured, blocks, skipped = [], [], [], []
    for i in order:
        s = sources[i]
        duration = float(s["duration_sec"])
        entry = {**_identity(s), "source_start_sec": float(s["source_start_sec"]), "duration_sec": round(duration, 3)}
        if duration <= 0:
            reason = "The clip has no duration."
            data = None
        elif not os.path.isfile(str(s["file_path"])):
            reason, data = "The clip's media file is offline: %s" % s["file_path"], None
        else:
            data, reason, momentary = _measure_one(ffmpeg, s, stream, pooled)
        if data is None:
            entry.update(integrated_lufs=None, lra_lu=None, true_peak_dbtp=None, gain_db=None, peak_limited=False,
                         peak_limited_gain_db=None, reason=reason, measured=False)
            skipped.append({"item_id": s.get("item_id"), "name": s.get("name"), "reason": reason})
        else:
            entry.update(_loudness_values(data, duration, target, ceiling), measured=True)
            measured.append(s)
            blocks.extend(momentary)
        clips.append(entry)

    program = {"clips": len(measured), "seconds": round(sum(float(s["duration_sec"]) for s in measured), 3),
               "skipped": skipped}
    if not measured:
        program.update(integrated_lufs=None, lra_lu=None, true_peak_dbtp=None, method=None,
                       method_note="No clip could be measured.", reason="No clip could be measured.",
                       **_gain(None, None, target, ceiling))
    elif not pooled:
        data, note = _program_exact(ffmpeg, measured, stream)
        program.update(_loudness_values(data, program["seconds"], target, ceiling), method="exact_concat",
                       method_note=note)
    else:
        integrated = gated_loudness(blocks)
        peaks = [c["true_peak_dbtp"] for c in clips if c.get("true_peak_dbtp") is not None]
        data = {"integrated_lufs": integrated, "lra_lu": None, "true_peak_dbtp": max(peaks) if peaks else None}
        program.update(_loudness_values(data, program["seconds"], target, ceiling), method="pooled_blocks",
                       method_note=("APPROXIMATE: more than %d clips, so each clip's 400 ms loudness blocks were "
                                    "pooled and gated together (BS.1770 absolute and relative gates). Blocks that "
                                    "would straddle a join are missing, so expect a few tenths of a LU difference "
                                    "from a real render. Loudness range is not computed; true peak is the highest "
                                    "clip peak." % EXACT_PROGRAM_MAX))
    if program.get("integrated_lufs") is None and measured and program.get("reason") is None:
        program["reason"] = "Too quiet to measure."
    return {"clips": clips, "program": program, "target_lufs": target, "true_peak_ceiling_db": ceiling,
            "stream": int(stream or 0),
            "note": LOUDNESS_NOTE + " The programme is the clips played back to back in timeline order (no "
                                    "overlaps, crossfades or other tracks), not a render of the timeline."}
