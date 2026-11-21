---
name: timeline-notes
description: Work through the notes the user left as markers on the DaVinci Resolve timeline ("make this punchier", "cut this um", "zoom in here"), do each edit, and mark every note done on the timeline. Use when the user says "do my notes", "do the markers", "work through my comments", "handle my timeline notes" or similar.
---

# Timeline notes (Resolve)

The user parks markers on the timeline and types a comment into each. You read them all, do the edits, and
mark each marker done so the user can see what happened without leaving Resolve.

Rules that never bend:
- **Never delete the user's markers.** Mark them with `resolve_timeline_note`; never call `delete_marker` on a note.
- Only markers returned by `timeline_notes` are notes. Pause, dead-air, shot, beat, QC and review markers the
  bridge made are not, and are not yours to resolve.
- Note frames are absolute timeline frames. Pass them unchanged to tools that take absolute frames.
- Report what Resolve actually did. If a tool returns less than asked, say so in the reply.

1. `timeline_notes`. If it returns no open notes, say so and stop. If the user said "redo" or asked about
   finished notes, call it with `include_done=True`.
2. Summarize the notes as a short numbered plan, one line each, with the timecode written as HH:MM:SS:FF
   exactly as returned (the panel makes those clickable), the clip under it, and what you intend to do:
   `3. 01:00:12:04 (Interview_A) "cut this um" -> cut the filler word on a copy.`
3. Group notes that belong together (two notes marking the start and end of one range, several "punchier"
   notes in one scene). Flag only the ambiguous ones ("fix this", a note that could mean two different edits)
   and ask about those, briefly. Go ahead with the clear ones once the user agrees to the plan.
4. Do each note with the bridge's existing tools. Read the clip first (`current_context`, `timeline_overview`,
   `contact_sheet`, `transcribe` / `search_transcript`) when the note depends on what is on screen or said.
   - Cuts, "cut this um", "tighten": `find_filler_words` / `review_transcript_cuts` then `apply_silence_cuts`,
     `remove_ranges`, or `trim_edit`, `slip_clip`, `slide_clip`, `roll_edit`.
   - "zoom in here", "punch in", motion: `animate_zoom` or `set_clip_transform`; transitions and effects with
     `fusion_build`.
   - Text: `insert_title` / `styled_title`. Color: `set_clip_grade`, `match_shot`. Speed: `change_clip_speed`.
   - When the panel says to work on a copy, run `preview_timeline` first and do every note there. The copy
     carries the markers, so the notes are resolved on the copy and the original keeps its open markers.
   - `remove_ranges`, `apply_silence_cuts` and `remove_pauses(apply=True)` build and open a new timeline and
     re-time its markers. After any of them, or any edit that moves time, call `timeline_notes` again and use
     the new frames.
5. After each note, `resolve_timeline_note(frame, status="done", reply="<one line: what you did>")`. Use
   `status="skipped"` with the reason when you could not or should not do it (for example a Studio-only
   effect, or the user declined). Check `verified` in the result; if it is false, tell the user.
6. Finish with the counts (done, skipped, still open) and anything that needs the user's eye: notes you
   interpreted, edits to audition, and limits such as manual Fusion or Fairlight work.
