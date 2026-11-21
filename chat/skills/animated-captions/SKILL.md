---
name: animated-captions
description: Add word-by-word animated captions (TikTok/Reels style) to a Resolve timeline and pick a style that suits the edit. Use for social clips, shorts, or when the user asks for "captions that pop".
---

# Animated captions (Resolve)

1. Check `current_context`. For vertical (9:16) timelines put captions at `position="center"`; for 16:9 use
   `bottom`.
2. Pick a style from the content:
   - `pop` for energetic talking heads
   - `karaoke` for music or a narrated beat
   - `minimal` for interviews and documentary
3. Use `max_words=3` for fast speech and 4 otherwise. Use `uppercase=True` only for punchy short-form edits.
4. `animated_captions(scope="timeline", style=..., position=...)`. This renders a transparent overlay on a new
   top track.
5. Look at a frame with `timeline_frame` at a captioned moment and confirm that the captions are legible and
   don't cover faces. If they cover a face, re-run with another position.
6. Tell the user the overlay isn't editable text. To restyle, run it again and disable the old track.
