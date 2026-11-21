"""Plan dialogue tightening cuts: long pauses (and optionally filler words) between trusted words.

Pure logic, standard library only, never writes to stdout. ``bridge/server.py`` gathers the inputs (a cached
transcript plus ``audio_regions.speech_regions`` floor-aware word trust per clip) and either places review
markers or applies every cut in ONE ``remove_ranges`` call, which builds a new timeline and keeps the original.

All frames are absolute timeline frames, end exclusive.

Word guards: a word whose audio analysis succeeded is protected only over its trusted extent (audio above the
room-noise floor, its end capped a short way past the last loud hop, so Whisper stretching a phrase-final word
across the next pause cannot hide that pause). A word with no audio above the floor (a likely Whisper phantom)
is not protected and is reported when a cut removes it. When a clip's audio analysis failed, its words are
protected over their full transcript span (the plain, over-protective guard), and a warning says so.
"""

from bridge import transcribe as tx

MAX_MARKERS = 300
CONTEXT_WORDS = 4
MIN_CUT_FRAMES = 2
DEFAULT_MAX_PAUSE_SEC = 0.5

NOT_APPLIED_REASON = {
    "lcut_frames": ("Not applied: remove_ranges rebuilds the timeline with audio and video cut at the same frame. "
                    "No verified Resolve API offsets a piece's audio from its video, so no L-cut/J-cut was made."),
    "audio_crossfade_frames": ("Not applied: no verified Resolve API adds audio crossfades on a rebuilt timeline. "
                               "Transitions only exist through the FCPXML route (add_transitions), which handles "
                               "video Cross Dissolves on V1, not audio crossfades at these cuts."),
}


class TightenError(ValueError):
    """Bad tighten input the caller can fix."""


def _int(value, name, low=0, high=100000):
    if isinstance(value, bool):
        raise TightenError("%s must be a whole number of frames." % name)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise TightenError("%s must be a whole number of frames." % name)
    if number != value and not (isinstance(value, float) and float(number) == value):
        raise TightenError("%s must be a whole number of frames." % name)
    if not low <= number <= high:
        raise TightenError("%s must be between %d and %d." % (name, low, high))
    return number


def validate(scope="timeline", mode="plan", lead_frames=4, tail_frames=6, max_pause_frames=None,
             exclude_ranges=None, lcut_frames=0, audio_crossfade_frames=0, fps=None):
    """Checked settings dict, or TightenError. ``max_pause_frames`` None becomes ~0.5 s once ``fps`` is known."""
    scope = str(scope or "").lower()
    if scope not in ("timeline", "track", "clip"):
        raise TightenError("scope must be timeline, track or clip.")
    mode = str(mode or "").lower()
    if mode not in ("plan", "apply"):
        raise TightenError("mode must be plan or apply.")
    settings = {"scope": scope, "mode": mode, "lead_frames": _int(lead_frames, "lead_frames", 0, 1000),
                "tail_frames": _int(tail_frames, "tail_frames", 0, 1000),
                "lcut_frames": _int(lcut_frames, "lcut_frames", 0, 1000),
                "audio_crossfade_frames": _int(audio_crossfade_frames, "audio_crossfade_frames", 0, 1000)}
    if max_pause_frames is None:
        settings["max_pause_frames"] = None if fps is None else max(1, int(round(DEFAULT_MAX_PAUSE_SEC * float(fps))))
    else:
        settings["max_pause_frames"] = _int(max_pause_frames, "max_pause_frames", 0, 100000)
    settings["exclude_ranges"] = normalize_ranges(exclude_ranges)
    return settings


def normalize_ranges(ranges):
    """Sorted, merged [(start, end)] from [{start_frame, end_frame}] (end exclusive)."""
    if ranges in (None, ""):
        return []
    if not isinstance(ranges, (list, tuple)):
        raise TightenError("exclude_ranges must be a list of {start_frame, end_frame}.")
    spans = []
    for r in ranges:
        if not isinstance(r, dict):
            raise TightenError("Each exclude range needs start_frame and end_frame.")
        try:
            a, b = int(r["start_frame"]), int(r["end_frame"])
        except (KeyError, TypeError, ValueError):
            raise TightenError("Each exclude range needs integer start_frame and end_frame.")
        if b <= a:
            raise TightenError("Exclude range %d-%d is empty; end_frame is exclusive and must be after start_frame."
                               % (a, b))
        spans.append((a, b))
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def not_applied(lcut_frames=0, audio_crossfade_frames=0):
    """Honest report of accepted-but-unsupported parameters."""
    out = []
    for name, value in (("lcut_frames", lcut_frames), ("audio_crossfade_frames", audio_crossfade_frames)):
        if value:
            out.append({"parameter": name, "value": value, "reason": NOT_APPLIED_REASON[name]})
    return out


# ------------------------------------------------------------------ inputs

def collect_words(clips):
    """Planner words from per-clip inputs.

    ``clips``: [{"source": transcription_source entry, "words": transcript words,
    "analysis": speech_regions result or None, "error": str or None}]. Uses the analysis' refined, trust-carrying
    words when present, otherwise the raw transcript words (full-span guards). Returns (words, warnings).
    """
    words, warnings = [], []
    for clip in clips:
        source = clip.get("source") or {}
        item_id = source.get("item_id")
        analysis = clip.get("analysis")
        if analysis and isinstance(analysis.get("words"), list):
            for w in analysis["words"]:
                words.append(dict(w, item_id=item_id))
            for note in (analysis.get("analysis") or {}).get("warnings") or []:
                warnings.append("%s: %s" % (source.get("name") or item_id, note))
        else:
            for w in clip.get("words") or []:
                entry = {"word": w.get("word", ""), "start_frame": int(w["start_frame"]), "end_frame": int(w["end_frame"]),
                         "item_id": item_id}
                words.append(entry)
            if clip.get("words"):
                warnings.append("%s: audio analysis failed (%s); its words are protected over their whole transcript "
                                "span, which over-protects and may hide pauses." % (source.get("name") or item_id,
                                                                                   clip.get("error") or "no analysis"))
    return words, warnings


def _guard(w):
    """(start, end) a cut must never enter, or None for an untrusted (no audio above the floor) word."""
    if "trusted" in w:
        if not w.get("trusted") or w.get("trusted_start_frame") is None or w.get("trusted_end_frame") is None:
            return None
        a, b = int(w["trusted_start_frame"]), int(w["trusted_end_frame"])
    else:
        a, b = int(w["start_frame"]), int(w["end_frame"])
    return (a, max(b, a + 1))


def _subtract(span, holes):
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
    return pieces


def _merge(spans):
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _text(words):
    return " ".join(str(w.get("word", "")).strip() for w in words if str(w.get("word", "")).strip())


# ----------------------------------------------------------------- planner

def plan_cuts(words, fps, *, lead_frames=4, tail_frames=6, max_pause_frames=None, exclude_ranges=None,
              fillers=False, filler_words=tx.FILLERS, context_words=CONTEXT_WORDS, min_cut_frames=MIN_CUT_FRAMES):
    """Cuts that shorten every pause longer than ``max_pause_frames`` to ``tail_frames`` + ``lead_frames``
    (air kept after the previous word and before the next), and with ``fillers`` also remove filler words
    (and the pause around them, down to the same air).

    ``words``: [{word, start_frame, end_frame, item_id?, trusted?, trusted_start_frame?, trusted_end_frame?}].
    Cuts never enter ``exclude_ranges`` or the guard of a word they are not removing. Returns
    {"cuts", "totals", "skipped", "settings"}.
    """
    fps = float(fps)
    if fps <= 0:
        raise TightenError("fps must be positive.")
    lead, tail = int(lead_frames), int(tail_frames)
    if max_pause_frames is None:
        max_pause_frames = max(1, int(round(DEFAULT_MAX_PAUSE_SEC * fps)))
    max_pause = int(max_pause_frames)
    if exclude_ranges and not isinstance(exclude_ranges[0], dict):  # Already-normalized (start, end) pairs.
        excludes = _merge([(int(r[0]), int(r[1])) for r in exclude_ranges])
    else:
        excludes = normalize_ranges(exclude_ranges)

    ordered = sorted((dict(w) for w in words), key=lambda w: (int(w["start_frame"]), int(w["end_frame"])))
    for i, w in enumerate(ordered):
        w["_i"], w["_guard"] = i, _guard(w)

    filler_set = set()
    if fillers and ordered:
        spans = tx.filler_ranges(ordered, fps, tuple(filler_words), padding_frames=0, merge_gap_frames=0)
        for w in ordered:
            s, e = int(w["start_frame"]), int(w["end_frame"])
            if any(r["start_frame"] <= s and e <= r["end_frame"] for r in spans):
                filler_set.add(w["_i"])

    anchors = sorted((w for w in ordered if w["_guard"] and w["_i"] not in filler_set), key=lambda w: w["_guard"])
    protected = _merge([w["_guard"] for w in anchors])
    filler_words_list = [w for w in ordered if w["_i"] in filler_set]

    candidates, skipped = [], []
    running_end = None
    for k in range(len(anchors) - 1):
        g = anchors[k]["_guard"]
        running_end = g[1] if running_end is None else max(running_end, g[1])
        prev_end, next_start = running_end, anchors[k + 1]["_guard"][0]
        if next_start <= prev_end:
            continue
        inside = [w for w in filler_words_list if prev_end <= int(w["start_frame"]) < next_start]
        if inside:
            spans = [w["_guard"] or (int(w["start_frame"]), int(w["end_frame"])) for w in inside]
            fs, fe = min(a for a, _ in spans), max(b for _, b in spans)
            start = max(prev_end, min(prev_end + tail, fs))
            end = min(next_start, max(next_start - lead, fe))
            kind, label = "filler", _text(inside)
        else:
            gap = next_start - prev_end
            if gap <= max_pause or gap <= lead + tail:
                continue
            start, end = prev_end + tail, next_start - lead
            kind, label = "pause", ""
        if end <= start:
            continue
        candidates.append({"start": start, "end": end, "kind": kind, "label": label, "gap": (prev_end, next_start),
                           "removing": {w["_i"] for w in inside}})

    cuts = []
    holes = _merge(list(excludes) + protected)
    for c in candidates:
        pieces = _subtract((c["start"], c["end"]), holes)
        if pieces != [(c["start"], c["end"])]:
            reason = "exclude_ranges" if _subtract((c["start"], c["end"]), excludes) != [(c["start"], c["end"])] \
                else "word guard"
            kept = [p for p in pieces if p[1] - p[0] >= min_cut_frames]
            skipped.append({"start_frame": c["start"], "end_frame": c["end"], "kind": c["kind"],
                            "reason": "trimmed by %s" % reason if kept else "inside %s" % reason,
                            "kept_pieces": [{"start_frame": a, "end_frame": b} for a, b in kept]})
            pieces = kept
        else:
            pieces = [p for p in pieces if p[1] - p[0] >= min_cut_frames]
        for a, b in pieces:
            cuts.append(_describe(a, b, c, ordered, fps, context_words))

    cuts.sort(key=lambda c: c["start_frame"])
    frames = sum(c["frames"] for c in cuts)
    totals = {"cuts": len(cuts), "pauses": sum(1 for c in cuts if c["kind"] == "pause"),
              "fillers": sum(1 for c in cuts if c["kind"] == "filler"), "frames": frames,
              "seconds": round(frames / fps, 2), "words": len(ordered),
              "untrusted_words": sum(1 for w in ordered if w["_guard"] is None)}
    settings = {"lead_frames": lead, "tail_frames": tail, "max_pause_frames": max_pause,
                "max_pause_seconds": round(max_pause / fps, 3), "fillers": bool(fillers),
                "exclude_ranges": [{"start_frame": a, "end_frame": b} for a, b in excludes]}
    return {"cuts": cuts, "totals": totals, "skipped": skipped, "settings": settings}


def _describe(a, b, cand, ordered, fps, context_words):
    before = [w for w in ordered if int(w["start_frame"]) < a]
    after = [w for w in ordered if int(w["start_frame"]) >= b]
    removed = [w for w in ordered if a <= int(w["start_frame"]) and int(w["end_frame"]) <= b]
    untrusted = [w for w in ordered if w["_guard"] is None and a <= int(w["start_frame"]) < b]
    prev_word = before[-1] if before else None
    cut = {"start_frame": a, "end_frame": b, "frames": b - a, "seconds": round((b - a) / fps, 2),
           "kind": cand["kind"], "timecode": tx.frames_to_tc(a, fps),
           "words_before": _text(before[-context_words:]), "words_after": _text(after[:context_words]),
           "pause_frames": cand["gap"][1] - cand["gap"][0],
           "kept_frames": (cand["gap"][1] - cand["gap"][0]) - (b - a),
           "item_id": (prev_word or (after[0] if after else {})).get("item_id")}
    if cand["label"]:
        cut["label"] = cand["label"]
    if removed:
        cut["removed_words"] = _text(removed)
    if untrusted:
        cut["untrusted_words_inside"] = _text(untrusted)
    return cut


# ------------------------------------------------------------------ markers

def review_markers(cuts, limit=MAX_MARKERS):
    """Purple "Tighten" review markers for add_markers (absolute frames), at most ``limit``. Returns
    (markers, truncated_count)."""
    markers = []
    for c in cuts[:max(0, int(limit))]:
        what = "filler %s" % c.get("label", "") if c["kind"] == "filler" else "pause"
        markers.append({"frame": c["start_frame"], "color": "Purple", "duration": max(1, c["frames"]),
                        "name": ("Tighten %s %.2fs" % (what, c["seconds"])).replace("  ", " ")[:120],
                        "note": ("...%s | %s... Would be removed by tighten(mode=\"apply\")."
                                 % (c["words_before"], c["words_after"]))[:500]})
    return markers, max(0, len(cuts) - len(markers))
