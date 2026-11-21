"""Local speech-to-text with whisper.cpp, mapped onto Resolve timeline frames.

Runs in the MCP server process, never inside Resolve and never while holding the
Resolve operation lease: ``transcription_source`` reads the clip mapping quickly,
then this module decodes the source audio and transcribes it on this computer.
Nothing is uploaded. Standard library only.

Engine: ``whisper-cli`` (Homebrew ``whisper-cpp``) with a ggml model in
``<bridge home>/models``. Override with RESOLVE_AI_BRIDGE_WHISPER and
RESOLVE_AI_BRIDGE_WHISPER_MODEL.
"""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MODEL_PREFERENCE = ("large-v3-turbo", "large-v3", "medium", "small", "base", "tiny")
FILLERS = ("um", "uh", "uhm", "umm", "erm", "er", "ah", "hmm", "mm", "mhm")
MAX_AUDIO_SECONDS = 3 * 3600


class TranscriptionError(RuntimeError):
    """Transcription failed for a reason the user can act on."""


# --------------------------------------------------------------- engine lookup

def whisper_binary():
    configured = os.environ.get("RESOLVE_AI_BRIDGE_WHISPER")
    if configured and Path(configured).is_file():
        return configured
    for name in ("whisper-cli", "whisper-cpp"):
        found = shutil.which(name)
        if found:
            return found
    for folder in ("/opt/homebrew/bin", "/usr/local/bin"):
        candidate = Path(folder) / "whisper-cli"
        if candidate.is_file():
            return str(candidate)
    return None


def whisper_model(home):
    configured = os.environ.get("RESOLVE_AI_BRIDGE_WHISPER_MODEL")
    if configured and Path(configured).is_file():
        return configured
    models = sorted(Path(home, "models").glob("ggml-*.bin")) if home else []
    for preferred in MODEL_PREFERENCE:
        for model in models:
            if preferred in model.name:
                return str(model)
    return str(models[0]) if models else None


def engine_status(home):
    binary, model = whisper_binary(), whisper_model(home)
    return {"available": bool(binary and model), "binary": binary, "model": Path(model).name if model else None,
            "help": None if binary and model else (
                "Install whisper.cpp (brew install whisper-cpp) and put a ggml model, e.g. "
                "ggml-large-v3-turbo-q5_0.bin from huggingface.co/ggerganov/whisper.cpp, in %s/models."
                % home)}


# ------------------------------------------------------------------ helpers

def frames_to_tc(frame, fps):
    """Non-drop timecode for display (Resolve's own timecode is authoritative)."""
    rate = max(1, round(fps))
    frame = max(0, int(round(frame)))
    return "%02d:%02d:%02d:%02d" % (frame // (3600 * rate), frame // (60 * rate) % 60, frame // rate % 60, frame % rate)


def source_rate(source):
    """(fps, basis) for source-media frame numbers: the entry's media rate when it carries one
    (``media_fps`` or ``source_fps``), else the timeline rate. basis is "media" or "timeline"."""
    for key in ("media_fps", "source_fps"):
        try:
            value = float(source.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            return value, "media"
    return float(source["fps"]), "timeline"


def source_frame(source, timeline_frame):
    """Source-media frame (counted from the start of the media file, not its embedded timecode) shown at
    absolute ``timeline_frame``, for a normal-speed clip described by a ``transcription_source`` entry:
    source_start_sec + (timeline_frame - timeline_start_frame) / timeline fps, times ``source_rate``."""
    rate, _ = source_rate(source)
    seconds = float(source["source_start_sec"]) + (float(timeline_frame) - float(source["timeline_start_frame"])) / float(source["fps"])
    return int(math.floor(seconds * rate + 1e-6))


def _clean(word):
    return re.sub(r"[^\w']+", "", word.lower())


def _token_time(token):
    """Seconds for a token: the DTW-aligned time when whisper.cpp computed one, else its offset."""
    dtw = token.get("t_dtw")
    if isinstance(dtw, (int, float)) and dtw >= 0:
        return dtw / 100.0, True
    return (token.get("offsets") or {}).get("from", 0) / 1000.0, False


def parse_whisper_json(data):
    """Turn whisper.cpp --output-json-full into (segments, words) in seconds.

    Word times use DTW token alignment (``-dtw``) when present; whisper's plain
    token offsets can drift by seconds across pauses.
    """
    segments, words = [], []
    for seg in data.get("transcription") or []:
        offsets = seg.get("offsets") or {}
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        seg_start, seg_end = offsets.get("from", 0) / 1000.0, offsets.get("to", 0) / 1000.0
        seg_words, current = [], None
        for token in seg.get("tokens") or []:
            piece = token.get("text") or ""
            if not piece or piece.startswith("[_") or piece.startswith("<|"):
                continue
            start, _ = _token_time(token)
            if current is None or piece.startswith(" "):
                if current and current["word"].strip():
                    seg_words.append(current)
                current = {"word": piece.strip(), "start": start, "p": token.get("p")}
            else:
                current["word"] += piece
        if current and current["word"].strip():
            seg_words.append(current)
        seg_words = [w for w in seg_words if re.search(r"\w", w["word"])] or seg_words
        for i, word in enumerate(seg_words):  # A word ends where the next begins (capped), or at the segment end.
            nxt = seg_words[i + 1]["start"] if i + 1 < len(seg_words) else min(seg_end, word["start"] + 0.8)
            word["end"] = max(word["start"] + 0.04, min(nxt, word["start"] + 1.5))
        if seg_words:  # Tighten the segment to its aligned words.
            seg_start, seg_end = seg_words[0]["start"], max(seg_words[-1]["end"], seg_words[0]["start"] + 0.2)
        segments.append({"start": seg_start, "end": seg_end, "text": text})
        words.extend(seg_words)
    return segments, words


def dtw_preset(model_path):
    """whisper.cpp's alignment-head preset for a ggml model file name."""
    name = Path(model_path).name.lower()
    for key, preset in (("large-v3-turbo", "large.v3.turbo"), ("large-v3", "large.v3"), ("large-v2", "large.v2"),
                        ("large-v1", "large.v1"), ("medium.en", "medium.en"), ("medium", "medium"),
                        ("small.en", "small.en"), ("small", "small"), ("base.en", "base.en"), ("base", "base"),
                        ("tiny.en", "tiny.en"), ("tiny", "tiny")):
        if key in name:
            return preset
    return None


def _extract_audio(ffmpeg, file_path, start, duration, target, stream=None):
    # ``stream`` picks one audio track of a multi-track recording (OBS mic vs game); None = ffmpeg's default.
    select = ["-map", "0:a:%d" % int(stream)] if stream is not None else ["-vn"]
    command = [ffmpeg, "-v", "error", "-y", "-ss", "%.6f" % start, "-i", str(file_path), "-t", "%.6f" % duration,
               *select, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise TranscriptionError("Decoding the audio took too long; try a shorter range.")
    if result.returncode or not Path(target).exists():
        raise TranscriptionError("ffmpeg could not decode this clip's audio: %s" % result.stderr.decode(errors="replace")[-300:])


def _cache_key(source, model, language, prompt, stream=None):
    path = Path(source["file_path"])
    stat = path.stat()
    raw = json.dumps([str(path), stat.st_size, stat.st_mtime, round(source["source_start_sec"], 4),
                      round(source["duration_sec"], 4), Path(model).name, language, prompt, "dtw-v1", stream])
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def transcribe_source(source, home, language="auto", prompt="", ffmpeg=None, threads=None, stream=None):
    """Transcribe one clip's source range; returns segments and words in timeline frames.

    ``source`` comes from the ``transcription_source`` operation: file_path,
    source_start_sec, duration_sec, timeline_start_frame, fps, item_id, name.
    """
    binary, model = whisper_binary(), whisper_model(home)
    if not binary or not model:
        raise TranscriptionError(engine_status(home)["help"])
    ffmpeg = ffmpeg or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not Path(ffmpeg).exists() and not shutil.which(ffmpeg):
        raise TranscriptionError("Transcription needs ffmpeg to decode audio (brew install ffmpeg).")
    if not Path(source["file_path"]).is_file():
        raise TranscriptionError("The clip's media file is offline: %s" % source["file_path"])
    if source["duration_sec"] <= 0:
        raise TranscriptionError("The selected clip has no duration to transcribe.")
    if source["duration_sec"] > MAX_AUDIO_SECONDS:
        raise TranscriptionError("Clips longer than 3 hours are not transcribed in one go.")

    cache_dir = Path(home, "transcripts")
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _cache_key(source, model, language, prompt, stream)
    cached = cache_dir / (key + ".json")
    if cached.is_file():
        raw = json.loads(cached.read_text(encoding="utf-8"))
        from_cache = True
    else:
        work = Path(tempfile.mkdtemp(prefix="resolve-transcribe-"))
        try:
            wav = work / "audio.wav"
            _extract_audio(ffmpeg, source["file_path"], source["source_start_sec"], source["duration_sec"], wav, stream)
            command = [binary, "-m", model, "-f", str(wav), "-l", language or "auto", "-ojf", "-of", str(work / "out"),
                       "-np", "-t", str(threads or max(2, min(8, (os.cpu_count() or 4)))), "-sow"]
            preset = dtw_preset(model)
            if preset:  # Word-accurate timestamps; DTW needs flash attention off.
                command += ["-dtw", preset, "-nfa"]
            if prompt:
                command += ["--prompt", prompt[:800]]
            timeout = max(120, int(source["duration_sec"] * 2) + 120)
            try:
                result = subprocess.run(command, capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                raise TranscriptionError("Transcription took longer than %d s and was stopped." % timeout)
            output = work / "out.json"
            if result.returncode or not output.is_file():
                raise TranscriptionError("whisper.cpp failed: %s" % result.stderr.decode(errors="replace")[-400:])
            raw = json.loads(output.read_text(encoding="utf-8", errors="replace"))
            cached.write_text(json.dumps(raw), encoding="utf-8")
            from_cache = False
        finally:
            shutil.rmtree(work, ignore_errors=True)

    segments, words = parse_whisper_json(raw)
    fps = float(source["fps"])
    origin = float(source["timeline_start_frame"])
    end_limit = float(source.get("timeline_end_frame") or origin + source["duration_sec"] * fps)

    def frame(seconds):
        return int(min(end_limit, origin + round(seconds * fps)))

    for item in segments + words:
        item["start_frame"], item["end_frame"] = frame(item.pop("start")), frame(item.pop("end"))
    for seg in segments:
        seg["timecode"] = frames_to_tc(seg["start_frame"], fps)
    src_fps, src_basis = source_rate(source)
    for word in words:
        word["item_id"] = source.get("item_id")
        word["src_frame"] = source_frame(source, word["start_frame"])
    detected = ((raw.get("result") or {}).get("language")) or language
    return {"item_id": source.get("item_id"), "name": source.get("name"), "track": source.get("track"),
            "language": detected, "model": Path(model).name, "cached": from_cache, "cache_key": key,
            "fps": fps, "timeline_start_frame": int(origin), "src_fps": src_fps, "src_fps_basis": src_basis,
            "segments": segments, "words": words}


# -------------------------------------------------------------- derived output

def subtitle_cues(words, fps, max_chars=42, max_lines=2, max_seconds=6.0, min_seconds=0.8, gap_seconds=0.6):
    """Group words into readable cues: break at sentence ends, long pauses, length and duration limits."""
    cues, current = [], []
    limit = max_chars * max_lines

    def flush():
        if current:
            cues.append({"start_frame": current[0]["start_frame"], "end_frame": current[-1]["end_frame"],
                         "text": " ".join(w["word"] for w in current)})
            current.clear()

    for word in words:
        if current:
            text_len = len(" ".join(w["word"] for w in current)) + 1 + len(word["word"])
            span = (word["end_frame"] - current[0]["start_frame"]) / fps
            pause = (word["start_frame"] - current[-1]["end_frame"]) / fps
            ends_sentence = re.search(r"[.!?]$", current[-1]["word"]) and len(" ".join(w["word"] for w in current)) > 12
            if text_len > limit or span > max_seconds or pause > gap_seconds or ends_sentence:
                flush()
        current.append(word)
    flush()
    min_frames = int(round(min_seconds * fps))
    for i, cue in enumerate(cues):
        nxt = cues[i + 1]["start_frame"] if i + 1 < len(cues) else None
        if cue["end_frame"] - cue["start_frame"] < min_frames:
            cue["end_frame"] = cue["start_frame"] + min_frames
        if nxt is not None and cue["end_frame"] > nxt:
            cue["end_frame"] = nxt
        cue["text"] = _wrap(cue["text"], max_chars, max_lines)
    return [c for c in cues if c["end_frame"] > c["start_frame"]]


def _wrap(text, width, lines):
    if len(text) <= width or lines < 2:
        return text
    words = text.split()
    best, best_score = text, None
    for i in range(1, len(words)):  # Balanced two-line split.
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) <= width and len(b) <= width:
            score = abs(len(a) - len(b))
            if best_score is None or score < best_score:
                best, best_score = a + "\n" + b, score
    return best


def _srt_time(seconds):
    ms = max(0, int(round(seconds * 1000)))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def to_srt(cues, fps, origin_frame):
    """SRT with times relative to the timeline start, which is how Resolve places imported subtitles."""
    blocks = []
    for n, cue in enumerate(cues, 1):
        blocks.append("%d\n%s --> %s\n%s\n" % (n, _srt_time((cue["start_frame"] - origin_frame) / fps),
                                              _srt_time((cue["end_frame"] - origin_frame) / fps), cue["text"]))
    return "\n".join(blocks)


def filler_ranges(words, fps, fillers=FILLERS, padding_frames=2, merge_gap_frames=3):
    """Timeline ranges [start, end) covering filler words, padded and merged."""
    wanted = {_clean(f) for f in fillers}
    phrases = [tuple(_clean(p) for p in f.split()) for f in fillers if " " in f]
    ranges = []
    cleaned = [_clean(w["word"]) for w in words]
    for i, word in enumerate(words):
        hit = cleaned[i] in wanted
        for phrase in phrases:
            if tuple(cleaned[i:i + len(phrase)]) == phrase:
                hit = True
                ranges.append([words[i]["start_frame"], words[i + len(phrase) - 1]["end_frame"], " ".join(w["word"] for w in words[i:i + len(phrase)])])
        if hit and cleaned[i] in wanted:
            ranges.append([word["start_frame"], word["end_frame"], word["word"]])
    ranges.sort()
    merged = []
    for start, end, label in ranges:
        start, end = start - padding_frames, end + padding_frames
        if merged and start <= merged[-1][1] + merge_gap_frames:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2] += ", " + label
        else:
            merged.append([start, end, label])
    return [{"start_frame": int(s), "end_frame": int(e), "label": label} for s, e, label in merged if e > s]


def pause_ranges(words, fps, min_pause=0.7, keep=0.15):
    """Gaps between spoken words longer than ``min_pause`` seconds, as removable timeline ranges.

    ``keep`` seconds of air stay on each side of the cut so speech doesn't sound clipped.
    Speech-based: room tone, music and breaths between words still count as a pause.
    """
    ordered = sorted(words, key=lambda w: w["start_frame"])
    keep_frames = int(round(keep * fps))
    ranges = []
    for prev, nxt in zip(ordered, ordered[1:]):
        gap = (nxt["start_frame"] - prev["end_frame"]) / float(fps)
        if gap >= min_pause:
            a, b = prev["end_frame"] + keep_frames, nxt["start_frame"] - keep_frames
            if b > a:
                ranges.append({"start_frame": int(a), "end_frame": int(b), "seconds": round((b - a) / float(fps), 2),
                               "after": prev["word"], "before": nxt["word"]})
    return ranges


def search(results, query, max_hits=50, fuzzy=0.82):
    """Find a word or phrase in transcripts: exact (case/punctuation-insensitive) then fuzzy matches.

    Returns hits with the matching words' timeline frames and the surrounding sentence.
    """
    import difflib
    terms = [_clean(t) for t in str(query).split() if _clean(t)]
    if not terms:
        return []
    hits = []
    for r in results:
        words = r["words"]
        cleaned = [_clean(w["word"]) for w in words]
        n = len(terms)
        for i in range(0, max(0, len(words) - n + 1)):
            window = cleaned[i:i + n]
            if window == terms:
                score = 1.0
            else:
                score = difflib.SequenceMatcher(None, " ".join(window), " ".join(terms)).ratio()
                if score < fuzzy:
                    continue
            seg = next((s for s in r["segments"] if s["start_frame"] <= words[i]["start_frame"] <= s["end_frame"]), None)
            hits.append({"item_id": r.get("item_id"), "name": r.get("name"), "score": round(score, 3), "_fps": r["fps"],
                         "start_frame": words[i]["start_frame"], "end_frame": words[i + n - 1]["end_frame"],
                         "src_frame": words[i].get("src_frame"), "src_fps": r.get("src_fps"),
                         "src_fps_basis": r.get("src_fps_basis"),
                         "timecode": frames_to_tc(words[i]["start_frame"], r["fps"]),
                         "match": " ".join(w["word"] for w in words[i:i + n]),
                         "context": seg["text"] if seg else " ".join(w["word"] for w in words[max(0, i - 8):i + n + 8])})
    hits.sort(key=lambda h: (-h["score"], h["start_frame"]))
    unique, seen = [], set()
    for h in hits:  # Overlapping fuzzy windows collapse to the best one.
        key = (h["item_id"], h["start_frame"] // max(1, int(h.pop("_fps"))))
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return sorted(unique[:max_hits], key=lambda h: h["start_frame"])
