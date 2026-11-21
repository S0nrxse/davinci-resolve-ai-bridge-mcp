"""Beat tracking for music clips, mapped onto Resolve timeline frames.

Pure standard library: ffmpeg decodes mono 16-bit PCM to a pipe, then
- an onset-strength envelope is built from two bands (full band and a
  first-difference high-pass) as rectified log-energy rises per hop,
- tempo comes from the envelope's autocorrelation with a log-tempo prior
  around 120 BPM,
- beats come from Ellis' (2007) dynamic-programming tracker,
- downbeats are every 4th beat, phase chosen by the strongest onset sum.

Never prints to stdout (stdio belongs to MCP).
"""

import array
import math
import operator
import subprocess
import sys
from pathlib import Path

MAX_SECONDS = 15 * 60


class BeatError(RuntimeError):
    """Beat detection failed for a reason the user can act on."""


# ------------------------------------------------------------------ decoding

def decode_pcm(ffmpeg, file_path, start=0.0, duration=None, sr=22050):
    """Mono s16 samples (array('h')) for the requested window, via an ffmpeg pipe."""
    seconds = MAX_SECONDS if duration is None else min(float(duration), MAX_SECONDS)
    command = [ffmpeg, "-v", "error", "-nostdin", "-ss", "%.6f" % max(0.0, float(start)), "-t", "%.6f" % seconds,
               "-i", str(file_path), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(int(sr)), "-f", "s16le", "-"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=max(60, int(seconds) + 60))
    except subprocess.TimeoutExpired:
        raise BeatError("Decoding the audio took too long; try a shorter range.")
    except OSError as exc:
        raise BeatError("Could not run ffmpeg: %s" % exc)
    if result.returncode:
        message = result.stderr.decode(errors="replace").strip()
        if "matches no streams" in message or "does not contain any stream" in message:
            raise BeatError("This clip has no audio stream.")
        raise BeatError("ffmpeg could not decode this clip's audio: %s" % message[-300:])
    data = result.stdout
    samples = array.array("h")
    samples.frombytes(data[: len(data) - len(data) % 2])
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


# ------------------------------------------------------------------ envelope

def onset_envelope(samples, hop=512):
    """Onset strength per hop: summed positive rises of log energy in two bands, normalized."""
    mul, sub = operator.mul, operator.sub
    count = len(samples) // hop
    full, high = [], []
    for k in range(count):
        block = samples[k * hop:(k + 1) * hop]
        full.append(sum(map(mul, block, block)))
        diff = list(map(sub, block[1:], block[:-1]))
        high.append(sum(map(mul, diff, diff)))
    env = [0.0] * count
    for band in (full, high):
        logs = [math.log1p(v / hop) for v in band]
        prev = logs[0] if logs else 0.0
        for k in range(1, count):
            rise = logs[k] - prev
            if rise > 0:
                env[k] += rise
            prev = logs[k]
    # Remove slow drift with a short moving average, then scale to unit std.
    if count:
        win = 16
        running, acc = [], 0.0
        for k in range(count):
            acc += env[k]
            if k >= win:
                acc -= env[k - win]
            running.append(acc / min(k + 1, win))
        env = [max(0.0, e - 0.5 * r) for e, r in zip(env, running)]
        mean = sum(env) / count
        std = math.sqrt(sum((e - mean) ** 2 for e in env) / count) or 1.0
        env = [e / std for e in env]
    return env


def onset_peaks(env, frame_rate, threshold=1.5, min_gap_sec=0.05):
    """Local maxima of the envelope above ``threshold`` (in std units) as seconds."""
    gap = max(1, int(round(min_gap_sec * frame_rate)))
    peaks, last = [], -gap
    for k in range(1, len(env) - 1):
        if env[k] >= threshold and env[k] >= env[k - 1] and env[k] > env[k + 1] and k - last >= gap:
            peaks.append(k / frame_rate)
            last = k
    return peaks


# ------------------------------------------------------------------- tempo

def estimate_period(env, frame_rate, min_bpm=60, max_bpm=200, prior_bpm=120.0, prior_octaves=1.0):
    """Beat period in envelope frames (float) and a 0..1 periodicity strength."""
    n = len(env)
    if n < 8:
        return None, 0.0
    # Smooth with a short triangle so impulse-like onsets still correlate at fractional beat periods.
    kernel = (1.0, 2.0, 3.0, 2.0, 1.0)
    half = len(kernel) // 2
    smooth = []
    for k in range(n):
        acc = wsum = 0.0
        for j, w in enumerate(kernel):
            i = k + j - half
            if 0 <= i < n:
                acc += w * env[i]
                wsum += w
        smooth.append(acc / wsum)
    mean = sum(smooth) / n
    x = [e - mean for e in smooth]
    energy = sum(map(operator.mul, x, x)) or 1.0
    lo = max(1, int(math.floor(60.0 * frame_rate / max_bpm)))
    hi = min(n - 1, int(math.ceil(60.0 * frame_rate / min_bpm)))
    if hi <= lo:
        return None, 0.0
    acf = {}
    for lag in range(max(1, lo - 1), min(n - 1, 2 * hi + 2)):
        acf[lag] = sum(map(operator.mul, x[:-lag], x[lag:])) / energy
    best, best_score = None, -1e18
    for lag in range(lo, hi + 1):
        bpm = 60.0 * frame_rate / lag
        weight = math.exp(-0.5 * (math.log2(bpm / prior_bpm) / prior_octaves) ** 2)
        # Reward lags whose double also correlates (helps against octave errors on sparse patterns).
        score = weight * (acf[lag] + 0.5 * acf.get(2 * lag, 0.0))
        if score > best_score:
            best, best_score = lag, score
    if best is None:
        return None, 0.0
    # Parabolic refinement of the autocorrelation peak.
    a, b, c = acf.get(best - 1, acf[best]), acf[best], acf.get(best + 1, acf[best])
    denom = a - 2 * b + c
    shift = 0.5 * (a - c) / denom if denom < 0 else 0.0
    period = best + max(-0.5, min(0.5, shift))
    return period, max(0.0, min(1.0, acf[best]))


# ------------------------------------------------------------- beat tracking

def track_beats(env, period, tightness=100.0):
    """Ellis (2007) DP beat tracker: indices of beat frames."""
    n = len(env)
    if not period or n == 0:
        return []
    lo, hi = int(round(period / 2)), int(round(period * 2))
    penalties = {d: -tightness * math.log(d / period) ** 2 for d in range(max(1, lo), hi + 1)}
    score = list(env)
    back = [-1] * n
    for t in range(n):
        best, arg = -1e18, -1
        start = max(0, t - hi)
        stop = t - max(1, lo)
        for tau in range(start, stop + 1):
            v = score[tau] + penalties[t - tau]
            if v > best:
                best, arg = v, tau
        if arg >= 0 and best > 0:
            score[t] = env[t] + best
            back[t] = arg
    tail = max(0, n - int(round(period)))
    end = max(range(tail, n), key=lambda i: score[i])
    beats = [end]
    while back[beats[-1]] >= 0:
        beats.append(back[beats[-1]])
    beats.reverse()
    # Trim weak beats at the edges (silence before/after the music).
    mean = sum(env) / n
    while beats and env[beats[0]] < 0.1 * mean and len(beats) > 2:
        beats.pop(0)
    while beats and env[beats[-1]] < 0.1 * mean and len(beats) > 2:
        beats.pop()
    return beats


def _refine(env, k):
    """Sub-frame peak position by parabolic interpolation around frame k."""
    if 0 < k < len(env) - 1:
        a, b, c = env[k - 1], env[k], env[k + 1]
        denom = a - 2 * b + c
        if denom < 0:
            return k + max(-0.5, min(0.5, 0.5 * (a - c) / denom))
    return float(k)


def _snap(env, k, radius):
    lo, hi = max(0, k - radius), min(len(env) - 1, k + radius)
    return max(range(lo, hi + 1), key=lambda i: env[i])


def downbeat_phase(env, beats, meter=4):
    if len(beats) < meter:
        return 0
    sums = [sum(env[b] for b in beats[p::meter]) / max(1, len(beats[p::meter])) for p in range(meter)]
    return max(range(meter), key=lambda p: sums[p])


# ------------------------------------------------------------------- public

def analyze_samples(samples, sr=22050, hop=512, min_bpm=60, max_bpm=200):
    """Beat analysis of raw samples: (bpm, confidence, beat_times, downbeat_times, note)."""
    frame_rate = sr / float(hop)
    env = onset_envelope(samples, hop)
    if not env or max(env) <= 0:
        return None, 0.0, [], [], "No rhythmic onsets were found (silence or very quiet audio)."
    period, strength = estimate_period(env, frame_rate, min_bpm, max_bpm)
    if not period:
        return None, 0.0, [], [], "The clip is too short to estimate a tempo."
    indices = track_beats(env, period)
    radius = max(1, int(round(period * 0.1)))
    snapped = [_snap(env, k, radius) for k in indices]
    times = [(_refine(env, k) + 0.5) * hop / sr for k in snapped]  # Hop centre: an onset lands mid-frame on average.
    times = sorted(set(round(t, 4) for t in times))
    bpm = 60.0 * frame_rate / period
    if len(times) >= 4:  # Least-squares fit of beat times vs index is far finer than integer lags.
        idx = list(range(len(times)))
        mi, mt = sum(idx) / len(idx), sum(times) / len(times)
        var = sum((i - mi) ** 2 for i in idx)
        if var:
            slope = sum((i - mi) * (t - mt) for i, t in zip(idx, times)) / var
            if slope > 0 and min_bpm * 0.9 <= 60.0 / slope <= max_bpm * 1.1:
                bpm = 60.0 / slope
    beat_frames = [int(round(t * sr / hop - 0.5)) for t in times]
    beat_frames = [max(0, min(len(env) - 1, k)) for k in beat_frames]
    phase = downbeat_phase(env, beat_frames)
    downs = times[phase::4]
    mean_all = sum(env) / len(env)
    mean_beats = sum(env[k] for k in beat_frames) / max(1, len(beat_frames))
    salience = max(0.0, min(1.0, (mean_beats / (mean_all or 1.0) - 1.0) / 4.0))
    confidence = round(max(0.0, min(1.0, 0.5 * salience + 0.5 * strength)), 3)
    note = "Steady tempo assumed; downbeats are every 4th beat (4/4 guess)."
    if confidence < 0.3:
        note = "Low confidence: the rhythm is weak or irregular. " + note
    return bpm, confidence, times, downs, note


def _timeline(times, source):
    fps = float(source.get("fps") or 24)
    start = int(round(float(source.get("timeline_start_frame") or 0)))
    end = source.get("timeline_end_frame")
    end = int(round(float(end))) if end is not None else None
    out = []
    for t in times:
        frame = start + int(round(t * fps))
        if end is not None and frame >= end:
            continue
        out.append({"time_sec": round(t, 4), "timeline_frame": frame})
    return out


def detect_beats(source, ffmpeg, min_bpm=60, max_bpm=200, hop=512, sr=22050):
    """Tempo, beats and downbeats for a clip's used source window (capped at 15 minutes)."""
    path = source.get("file_path")
    if not path or not Path(path).is_file():
        raise BeatError("The clip's media file is offline: %s" % path)
    if not 20 <= float(min_bpm) < float(max_bpm) <= 400:
        raise BeatError("Use 20 <= min_bpm < max_bpm <= 400.")
    duration = float(source.get("duration_sec") or 0)
    if duration <= 0:
        raise BeatError("The selected clip has no duration to analyze.")
    analyzed = min(duration, MAX_SECONDS)
    samples = decode_pcm(ffmpeg, path, float(source.get("source_start_sec") or 0), analyzed, sr)
    base = {"item_id": source.get("item_id"), "name": source.get("name"), "analyzed_sec": round(len(samples) / float(sr), 3)}
    if len(samples) < sr * 2:
        return dict(base, bpm=None, confidence=0.0, beats=[], downbeats=[], note="Too little audio to find a beat (under 2 s).")
    bpm, confidence, times, downs, note = analyze_samples(samples, sr, hop, min_bpm, max_bpm)
    if duration > MAX_SECONDS:
        note = "Only the first 15 minutes were analyzed. " + note
    return dict(base, bpm=round(bpm, 2) if bpm else None, confidence=confidence,
                beats=_timeline(times, source), downbeats=_timeline(downs, source), note=note)
