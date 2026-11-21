---
name: talking-head-cleanup
description: Tighten a talking-head or lecture edit in DaVinci Resolve. Removes long pauses and filler words on a copy of the timeline, then adds subtitles. Use when the user wants their recording "tightened", "cleaned up" or "ready to post".
---

# Talking-head cleanup (Resolve)

1. `resolve_status`, then `current_context` to confirm the timeline. Say which timeline you'll work on.
2. `remove_pauses(min_pause_seconds=0.7, mark=True)` and report the count and the seconds saved. Ask before
   applying. Suggest 0.5 s for fast social cuts and 1.0 s for lectures.
3. On approval, `remove_pauses(apply=True)`. This builds a tightened copy; the original stays untouched.
4. `find_filler_words()` on the new timeline. Whisper often drops "um" and "uh", so say how many were found
   and don't promise to catch every one. Apply approved markers with `apply_silence_cuts`.
5. `generate_subtitles(scope="timeline")`, then report whether `import.on_timeline` came back true.
6. Offer `animated_captions` (the $animated-captions skill) for social formats.
7. Summarize: old vs new duration, what was cut, and what still needs a human ear (breaths, jump cuts).
