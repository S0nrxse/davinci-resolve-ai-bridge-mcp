---
name: resolve-ai-editing
description: Plan, perform, and verify careful edits in an open DaVinci Resolve project through Resolve AI Bridge MCP tools.
---

# Resolve AI Editing

Use this skill whenever a request involves the open DaVinci Resolve project.

## Mandatory Live Timeline Audit Protocol

**CRITICAL RULE FOR AI AGENTS**:
Before analyzing audio, generating subtitles/captions, rendering motion graphics, or proposing any edit:
1. **Always Call `timeline_overview` First**: Never assume previous timeline state, cached clip names, or raw files on disk. The user frequently records new microphone audio, cuts clips, trims in-points (`GetLeftOffset`), moves the playhead, or switches projects/timelines in DaVinci Resolve between prompts.
2. **Use Source Mapping Metadata**: Use timeline_audio's returned source_start_sec, timeline_start_frame and frame_rate. Do not assume source frames equal timeline frames on mixed-rate or retimed clips. Source analysis excludes Fairlight processing.
3. **Audio Formats**: Let timeline_audio normalize decoded media to 16-bit PCM. Do not infer timing errors solely from source bit depth.
4. **Timing Review**: Activity clusters are energy-based, not transcripts. Any anticipatory caption lead is an optional creative choice; audition the result rather than promising universal perceptual sync.
5. **GPU Cache Invalidation**: When placing newly rendered transparent subtitle or motion graphic videos onto the timeline, always use a unique timestamped filename or disable old overlapping clips on lower tracks so DaVinci Resolve's GPU video memory immediately displays the fresh render.

## Required Workflow

1. Call `resolve_status` before every editing session. On Free 21.1+, choose Workspace > Scripts > Resolve AI Bridge > Start AI Bridge (Lua) once per session. The native-file Lua connector is verified on macOS Free 21.1.2.10; Python runs in the external MCP process. On Python-enabled builds use Start AI Bridge or the portable Py3 fallback. Use Stop AI Bridge (Lua) for the Lua worker. Refresh the MCP server after updating.
2. Call `timeline_overview` before proposing an edit. For anything beyond the open timeline, call `project_overview` first.
3. Summarize what is open and identify ambiguities.
4. State a short plan before changing the timeline.
5. Prefer the stable `id` from the latest overview. Positional `label` values such as `V1.2` can change after edits. Duplicates and rebuilt clips have new IDs. Do not target a repeated name.
6. Before a batch of changes, use `preview_timeline`, inspect its new IDs, and keep the original. Use `compare_timelines` plus visual/audio review afterward.
7. Make one logical change per tool call.
8. Call `timeline_overview` again to verify the result.
9. Ask for explicit approval before deletion, ripple deletion, or starting a render.

## Working Across The Whole Project

You can work anywhere in the project, not just the open timeline.

- `project_overview` lists every timeline (with ids, length and track counts), the media pool bins and clips, project settings and the render queue in one read-only call. `current_context` also carries `timeline_count` and the first 20 timelines.
- Switch timelines with `open_timeline` before using timeline tools on another one. Say which timeline you changed.
- `resolve_api` calls any Resolve scripting API method when no structured tool fits: project and timeline settings, bins, clip properties, markers on media, render settings and so on. Chain steps with `$N` references (steps count from 0; `$N[i]`, `$N.key`). Prefer structured tools, which verify their results. Check every step's result, because Resolve often returns `False` or `None` instead of raising.
- `resolve_api` refuses deleting projects, timelines, clips, bins, media, stills or render jobs, closing the project and quitting unless `allow_destructive=true`. Use that only after the user explicitly approves the deletion. If a step fails, the steps before it already ran and are not undone; report them.

## Camera Motion, Smooth Zooms & Multi-Keyframing

Use `animate_zoom` to generate native Fusion camera keyframes directly on timeline clips:
- **14 Motion Curve Presets**: `linear`, `ease_in`, `quad_in`, `cubic_in`, `ease_out`, `quad_out`, `cubic_out`, `ease`, `quad_ease`, `cubic_ease`, `circular_ease`, `rebound_in`, `rebound_out`, `elastic_out`.
- **Zoom Out**: Pass `direction="out"` or `start_zoom > end_zoom` (e.g. `start_zoom=1.6, end_zoom=1.0`). Passing `zoom < 1.0` (e.g. `0.55x`) shrinks the video into a centered floating card with black letterbox/pillarbox canvas borders.
- **Multi-Keyframe Sequences (`keyframes`)**: Define multi-stage waypoint trajectories (e.g. fast punch-in -> typing hold -> gradual slow ease-in zoom out) with per-segment easing curves.
- **AI Scenario Presets**: `punch_in` (snappy 1.35x), `pop_in` (1.38x with overshoot), `slow_push` (subtle 1.15x push), `dramatic` (1.60x build-up), `reveal` (1.40x to 1.0x pull-out), and `cinematic` (1.25x smootherstep glide).

## Color Grading & Organic Shimmer Effects

- **Color Grades**: Use `set_clip_grade` and `get_clip_grade` to adjust saturation, slope, offset, and power via native ASC-CDL controls.
- **Color Transitions**: Use `keyframe_clip_saturation` to smoothly transition clips between 100% color and black-and-white.
- **Rainbow Cycles & Exposure Flicker**: Use `animate_color_fx` to generate continuous $360^\circ$ spectrum hue cycling and multi-harmonic organic lighting shimmer.

## Spotlight & Focus Masking

- **Spotlight Mask**: Use `apply_spotlight_mask` to darken the background (ambient brightness `0.0` for pure black shadow) and focus an animated, feathered spotlight circle/rectangle across key points on any timeline clip.

## Fusion Effects Stack

- Zoom, blur, spotlight, saturation and color fx share one bridge-owned chain in front of MediaOut. They stack, and the user's own Fusion nodes stay connected. `reset=True` removes only that effect's bridge nodes.
- Each `mask_name` is its own blur. Reuse the name to update it.
- Effect frames (`start_frame`, `end_frame`, keyframe `frame`) count from the clip's first frame. Use `timeline_start_frame`, `timeline_end_frame` or keyframe `timeline_frame` for absolute timeline frames. Frames outside the clip are refused.
- Check `verified` / `keyframes_verified` and `fusion_chain` in each reply, then confirm with `inspect_fusion`. A `null` value means the build would not report back, not success.

## Custom Fusion Graphs, Keyframes & Clip Transitions

Use these when no fixed effect above fits. **None of the recipes below has been live-tested in Resolve yet**; always check `verified`, `fusion_chain` and `inspect_fusion`, and look at the result on the Fusion page or viewer before calling it done.

- `current_context` (read-only, cheap) gives the playhead frame and the clip under it on every video track, with `offset` into the clip and `has_fusion`. Use it to resolve "this clip" and "here".
- `fusion_build(name, nodes, chain)` inserts a node graph as one custom stage (`custom:<name>`) before MediaOut, after the fixed bridge effects. Same name = replace; `reset=True` removes it. Tools are named `<name>_<node>`.
  - Input values: number, string, `[x, y]` (0..1 frame coordinates), `{"keyframes": [...]}` or `{"from": "Node" | "MediaIn" | "chain"}`.
  - Keyframes use clip-offset `frame` (or absolute `timeline_frame`). Default interpolation keys only your frames with Fusion's spline, which stays editable. Any key with `easing`, or `interpolation="baked"`, writes one key per frame with that curve.
  - The clip flows into the first `chain` node's main input. For a Merge, feed it on Foreground with `{"node": "Over", "input": "Foreground"}`.
  - Generators (Background, TextPlus, FastNoise) have no image input. They cannot sit in the chain; feed them into a Merge.
- `fusion_keyframe_input(tool, input, keyframes | value)` sets or animates any input of any node, including the user's own nodes. On an already-animated input `replace=True` replaces the animation and `replace=False` adds keys to it (BezierSpline numbers or XYPath points; a Fusion-page PolyPath is refused). `clear=True` removes the keys and leaves the input static at `value`, or at its value at clip offset `frame`. Check `mode` and `previous_modifier`.
- `fusion_inspect_tool(tool)` lists input ids, types, current values and keyframes. Call it rather than guessing an input id.
- `fusion_apply_setting(setting, insert_into_chain={entry, exit})` pastes `.setting`/macro/copied-node text and can wire it in as a custom stage.
- `fusion_graph(nodes, links, output, name)` builds a graph with explicit links such as `["chain", "Soft.Input"]`, `["Soft.Output", "Over.Background"]`, `["Vig.Mask", "Over.EffectMask"]`. One link must come from `chain` (the clip as it reaches the stage); `MediaIn` is the untouched source. `output` feeds MediaOut. Read `links_made`, `feeds_media_out` and `verified`.
- `fusion_copy_comp(from_item_id, to_item_ids, mode="replace" | "add")` copies a comp through a `.comp` file. `replace` is refused when the target's comp holds the user's nodes or bridge effects until the user approves (`user_approved=true`, the old comp is backed up first). Report each target's `warning` and `converted_tools`, and say that whether the copy's MediaIn shows the target's own media is not live-verified.
- `fusion_run_lua(code)` is a last resort with no tagging, verification or rollback. Explain the script to the user before running anything that changes the comp. Use `comp` (bound to the named clip); never `fusion:GetCurrentComp()`, which is the Fusion page's comp and is refused. If it fails with "did not run in this clip's Fusion composition", nothing ran: do not retry blindly.
- `fusion_apply_setting` and comp re-imports can turn a photo Loader into something else. Read `converted_tools` / `warning`, tell the user, and rebuild the Loader rather than claiming the comp is intact.

### Clip transitions without the Edit page

For a real Edit-page Cross Dissolve, use `add_transitions(cuts="all" | [cut frames], duration_frames)`: it builds a NEW
timeline "<name> (transitions)" through FCPXML, keeps the original, and reports `verified`, `transitions_found`,
`skipped` (cuts without media handles) and `not_carried`. Run `dry_run=true` first and ask. It refuses timelines with
more than V1 and its own A1 audio, titles, retimed clips or subtitles. `audio_fades=true` is unverified. Look at the
dissolves with `render_frames` before calling it done.

Otherwise Resolve's scripting API cannot add Edit-page transitions. Build a head (first N frames) or tail (last N frames) transition on one clip in Fusion instead. It blends the clip with black, or, where the clip is transparent, with whatever sits on the tracks below. For a tail, take the clip's `end - start` from `current_context`/`timeline_overview` and key from `duration - N` to `duration - 1`, or use `timeline_frame`.

- **Fade in from black (12 frames):**
  `nodes=[{"name":"Black","type":"Background","inputs":{"TopLeftAlpha":1}}, {"name":"Over","type":"Merge","inputs":{"Background":{"from":"Black"},"Blend":{"keyframes":[{"frame":0,"value":0},{"frame":12,"value":1}]}}}]`, `chain=[{"node":"Over","input":"Foreground"}]`. To fade to the tracks below instead, use `TopLeftAlpha: 0`.
- **Slide in from the left:** `nodes=[{"name":"Slide","type":"Transform","inputs":{"Center":{"keyframes":[{"frame":0,"value":[-0.5,0.5]},{"frame":15,"value":[0.5,0.5],"easing":"cubic_out"}]}}}]`. Uncovered canvas is transparent.
- **Zoom-blur in:** `nodes=[{"name":"Push","type":"Transform","inputs":{"Size":{"keyframes":[{"frame":0,"value":1.4},{"frame":12,"value":1,"easing":"ease_out"}]}}}, {"name":"Smear","type":"Blur","inputs":{"XBlurSize":{"keyframes":[{"frame":0,"value":25},{"frame":12,"value":0,"easing":"ease_out"}]}}}]`, `chain=["Push","Smear"]`.
- **Glitch hit:** a Transform whose `Center` jumps between a few offsets with `"easing":"step"` keys every 1–2 frames, plus a Blur whose `XBlurSize` spikes on the same frames. Keep the hit to 6–10 frames.
- **Tail fade out:** the fade-in graph with Blend keyed `1 → 0` over the last N frames, as its own stage name (for example `FadeOut`).

### Face swap / photo over a moving face (from a real session; positions need checking with render_frames)

- Nodes: `Loader` (the photo, input `Clip` = path), `EllipseMask` (Center on the face, Width/Height relative to the
  comp width), a `Merge` whose Background is a transparent `Background` and Foreground the Loader with the mask on
  its `EffectMask`, a `Transform` (Size for scale, `XSize` < 1 to fake a head turning away), then a final `Merge` with
  the clip on Background and the face stack on Foreground.
- Merge Center is in normalized comp coordinates; a Merge Size of 1 places the foreground at its own pixel size, so a
  400 px photo on a 1920 px comp needs Size about 4.8 to fill the width.
- Key Center per frame (or every few frames) with `fusion_keyframe_input`; use `replace=false` to add keys and
  `clear=true` to start over. Check `converted_tools` after any paste: Resolve can turn a file Loader into MediaIn.
- Then `render_frames` on the first, middle and last frames before calling it aligned.

### Looks: CDL or Fusion

- A look over long sections belongs on the Color page: `set_clip_grade` (many clips via `item_ids` / `range`) or
  `copy_grade`. ColorCorrector in Fusion maps roughly as Gain = CDL slope and Brightness = CDL offset.
- Use Fusion only for animated looks (a grade that ramps in, a dream flash) or looks that must sit inside a comp.

### Other Fusion recipes

- **Lower third:**
  - TextPlus `Words`: `StyledText`, `Size` 0.06, `Center` [0.3, 0.15].
  - Merge `Over` with `Foreground: {"from":"Words"}`, `chain=["Over"]`. Animate `Words.Center` or `Over.Blend` to bring it on.
- **Parallax / Ken Burns pan:** Transform `Center` [0.45, 0.5] → [0.55, 0.5] and `Size` 1.1 → 1.15 across the clip.
- **Camera shake:** keyframe Transform `Center` with small random offsets (±0.005–0.015) every 2 frames, using linear easing. Keyframes are used rather than a Shake modifier because the build tools add nodes, not modifiers.
- **Glow pulse:** a Glow node with `Glow` keyed 0 → 0.6 → 0 around the beat frame.

## Seeing The Finished Picture, And Undo Points

- `timeline_frame` on Free is the raw source. To check a grade, Fusion effect, title or transform, ask first, then
  `render_frames(frames=[...])` (up to 10) or `render_frames(start_frame, end_frame, count)`; it returns ONE contact
  sheet with timecodes burned in (`sheet=false` for separate frames). It renders through
  Deliver and restores settings and In/Out marks; report `settings_restored` if false. Look at the images before
  calling a visual change done.
- Before a risky step or batch, `snapshot_timeline(label)`. To undo, `restore_snapshot(snapshot_id)`: it opens a NEW
  copy of the snapshot and deletes nothing, so say which timeline is now open. `list_snapshots` lists them.
- There is no automatic rollback of a failed batch. Rebuild tools keep the original; `resolve_api` steps that ran
  stay. Snapshots are the undo.

## Images And Overlays

- Use `add_image` for any still. Do not use `append_media` for a picture; that produces a one-frame clip.
- Give a real `duration_seconds`. Read `actual_duration_frames` in the reply and report it if it differs from the request;
  `set_clip_length` fixes a still's length in place afterwards.
- Put overlays on `track_index` 2 or higher so footage on V1 stays visible. The track is created automatically.
- Position with `pan_percent`, `tilt_percent`, `zoom`, and `opacity`, or afterwards with `set_clip_transform`.
- `pan` and `tilt` are pixels from centre. The percent variants are relative to the timeline resolution, which `project_info` reports.
- After placing an image, call `timeline_overview` and confirm the new item id before making further changes.

## Titles

`insert_title` is best effort. Available names depend on the Resolve version and installed templates. Check `text_set` in the reply. When it is false, say the text must be typed in the Inspector, or offer to build the title in Remotion instead.

## Editing Clips On The Timeline

- Scale, reframe, or blend an existing clip with `set_clip_transform`. It takes `zoom`, `zoom_x`/`zoom_y`, `pan`/`tilt` (pixels), the percent variants, `rotation`, crop, `opacity`, `composite_mode`, and modes such as `scaling`, `resize_filter`, `retime_process`, and `motion_estimation`. This is a static transform.
- `split_clip` creates a verified checkpoint before rebuilding a normal-rate clip. It copies static transforms and the current grade layer, but rejects Fusion comps and mixed rates. Other grade layers, fades, links and metadata are not guaranteed. Inspect the checkpoint and result; never describe a failed rebuild as fully rolled back.
- Group multiple timeline items into a clean single compound clip with `create_compound_clip(item_ids=['V1.1', 'A1.1'], name='Scene 1')` or `create_compound_clip(item_id='playhead')`.
- Adjust constant video speed with `change_clip_speed(speed=0.75)`. It edits a tagged bridge-owned TimeSpeed node and preserves existing connections. Speed 1 neutralizes that node. Timeline duration and linked audio are unchanged; reverse is rejected. Clip-FPS mode affects every use of the media.
- Clip-targeting operations that expose `item_id` accept `item_id="playhead"` to act on the clip under the playhead, so you need not look up the id first. Still confirm with `timeline_overview` afterwards.
- Preferences: when the user states a standing preference (their working timeline, a bin, how tight to cut, a
  loudness target), save it with `set_preferences`; read `get_preferences` at the start of a session.
- Names: `place_still` = `add_image`, `place_media` = place a source range (`src_out` exclusive) or append;
  `fusion_inspect` = `inspect_fusion` + `fusion_inspect_tool` (and reports the comp size masks are relative to).
- In-place trims: `trim_in_place(item_id, head_frames, tail_frames)` (positive trims, negative extends) and
  `set_clip_length(item_id, frames)` for stills/titles edit the OPEN timeline directly, with no copy and no ripple:
  neighbours never move, linked audio follows, trims leave gaps (`gaps_left`) and extensions into a neighbour or past
  the media are refused. Prefer them for small fixes (they keep transitions, fades and hand edits that a rebuild loses).
  Ask first, consider `snapshot_timeline`, run `dry_run=true`, and report `exact` / `actual`. Clips cannot be moved in
  place on Free 21.2 (SetPosition / MoveItems are rejected): moves still need trim_edit / slide_clip on a copy.
- `edit_points(ops=[...])` does several trims/rolls in one call (in place); `mode="new_timeline"` for one slip, slide or
  rippled trim on a copy. `insert_clip` drops a source range into empty space. `ripple_delete_ranges` cuts ranges
  from every track in one copy (never chain trim_edit).
- Batch: `set_clip_properties(properties, item_ids | range)`, `set_clip_grade(item_ids | range, ...)`, `copy_grade`.
  Read `verified` per clip.
- `tighten(mode="plan")` first (words before/after each cut), then `mode="apply"` after a yes: one new timeline.
- `timeline_overview` is compact by default; `detail="full"` adds source in/out, media fps, volume and transitions.
  Transitions are reported under `transitions`, never as clips.
- `set_clip_volume(item_ids, db)` sets clip volume and reads it back; `measure_loudness(apply=true)` sets each clip
  to its measured gain. Ask before either.
- `split_clip` is only near-reversible: it deletes and re-adds the clip. Confirm the frame is right before cutting, and inspect the result.

## Remotion & Headless Compositor Workflow

Use Remotion when the user asks for complex animated typography, floating callouts, explainers, or designed motion scenes.

1. Inspect `timeline_overview` to obtain the exact `(start_frame, duration, left_offset, file_path)`.
2. Render the animation or effect to a high-quality video in `out/`.
3. Import the file with `import_media(paths=[...])`.
4. Replace or place on the timeline at `record_frame = start_frame` using `append_media`.
5. If useless pauses or dead time were trimmed, ripple-shift all downstream clips on all tracks so there are **zero gaps and zero overlaps**.
6. Set playhead and verify the timeline visually.

## Resolve Free 21.2 Limitations (live-tested)

- No composited stills: `timeline_frame` is raw source. Use `render_frames` (a short Deliver render; ask first).
- No in-place MOVE: `TimelineItem.SetPosition` and `Timeline.MoveItems` exist but return False. In-place trims, rolls
  and lengths work (`edit_points`, `trim_in_place`, `set_clip_length`); slips, slides and rippled inserts need a copy.
- Transitions only through FCPXML (`add_transitions`, Cross Dissolve on V1, new timeline). Rebuilds drop existing
  transitions (`transitions_dropped`). Audio fades and crossfades have no verified API.
- Timeline fps is settable only on an EMPTY timeline and only some strings (only "30" was accepted in testing;
  "29.97" was refused). Free locks the project rate once media is in use: check rates before building.
- Stills: AppendToTimeline ignores endFrame; `add_image` uses media marks, `set_clip_length` fixes one in place.
- Clip volume (`AudioVolume`) is settable and reads back; Fairlight track faders and fades are not reachable.
- Never rebuild when an in-place op exists. Before a rebuild, run `diff_since` (or ask) so the user's hand edits are
  carried or listed, file intermediates in the Agent Work bin (default), and report `not_carried`.

## Frame Units Cheat Sheet

| Quantity | Unit |
| --- | --- |
| `frame`, `start`, `end`, transcript word frames, markers in tools | absolute timeline frames; `end` / `end_frame` EXCLUSIVE |
| AppendToTimeline / `insert_clip` `src_out`, `endFrame` | source frames, EXCLUSIVE |
| `left_offset` / `right_offset` | timeline frames of handle before / after the clip |
| `src_in` / `src_out`, transcript `src_frame` | source-media frames (`src_fps_basis` says whether media or timeline rate was used) |
| Fusion keyframe `frame` | clip offset (0 = first frame); the comp's render range starts at its own `COMPN_RenderStart` |
| Fusion mask/position values | normalized 0..1 of the comp image (comp size = the source media size, not the timeline) |
| Timeline marker offsets inside the API | relative to the timeline start; bridge tools take absolute frames |

## API Boundaries

The public Resolve scripting API does not expose every action from the Edit page. Edit-page transitions cannot be added; build clip-level transitions in Fusion as described above. Do not claim that arbitrary clip repositioning or Fusion animation worked unless a returned result and a fresh inspection confirm it. If a requested action has no MCP tool, explain the limitation and offer a safe alternative.

## Failure Behavior

- If `resolve_status` reports no connection, stop and give the start instructions above.
- On timeout or unknown outcome, never replay a mutation automatically. Inspect its request record and fresh timeline state first. Client timeout does not confirm cancellation.
- After a stale project/timeline rejection, inspect the intended context again before submitting a new edit. A
  worker restart with the same timeline open now reconnects by itself (`transport_note`). Errors containing
  "no new operation started" never reached Resolve and are safe to retry; parallel calls queue rather than fail.
- Read the error, inspect current state, and adjust the plan.
- Never report success based only on an attempted call.

## Source inspection and dialogue review

- timeline_frame returns a native MCP image. Check `composited`; source fallback excludes grades, Fusion, overlays and transforms. Do not verify a visual effect from a source-only image.
- timeline_audio uses per-channel peaks and channel energy. Check truncation and absolute timeline offsets; silence intervals use 50 ms windows and exclusive ends.
- review_silence places markers only. Audition candidates and obtain user approval of the selected marker IDs before apply_silence_cuts. Application creates a duplicate and supports an isolated clip or aligned AV pair; complex timelines stay manual.
- Stale markers are refused after timeline edits. Reanalyze rather than bypassing the check.
- project_health reports active-timeline source/gap/disabled-track issues; gaps can be intentional. On Resolve Free it also flags timelines above UHD or 60 fps, which Free cannot render. It does not inspect the full Fairlight mix or queued render settings.
- After an approved render starts, follow it with render_status and report the final status and any error.
- Run bridge_capabilities for API/decoder availability. Free uses the Lua or Python worker appropriate to its version; no Studio AI feature should be substituted.


## Transcripts, Subtitles And Filler Cuts (local, Free-compatible)

Resolve's own transcription and auto-captions are Studio-only. These tools transcribe on this computer with
whisper.cpp instead; nothing is uploaded.

- `transcribe(scope="clip"|"track"|"timeline", include_words=False)`: segments (and words) in absolute timeline
  frames with timecodes. Cached, so repeat calls are instant. Use it to answer "where do I say X", to plan cuts,
  and to name chapter markers (`add_marker`).
- `generate_subtitles(scope="timeline")`: writes a readable `.srt` (sentence and pause aware, two balanced lines of
  at most 42 characters, 6 s max) and tries to place it on a subtitle track. Report `import.on_timeline`; when it
  is false, tell the user the file is in the media pool to drag onto the timeline.
- `find_filler_words()`: marks "um/uh/erm…" (or given words and phrases) as pink "Review cut" markers. Nothing is
  cut. For any other transcript-chosen range (a fluffed take, a repeated sentence), call `transcribe` with
  `include_words=True` and pass ranges plus the returned `fingerprint` to `review_transcript_cuts`.
- Apply approved markers with `apply_silence_cuts(marker_ids=[...])`. It cuts on a new duplicate timeline and
  keeps the original. It handles one isolated dialogue clip (or an aligned video/audio pair) at a time.
- Setup if the engine is missing: `brew install whisper-cpp` and a ggml model in `<bridge home>/models`
  (`python3 install.py --with-whisper` does both). Transcripts use source audio, not the Fairlight mix.


## Seeing, Searching And Tightening

- `contact_sheet(scope="timeline"|"track"|"clip"|"media_pool", bin="")`: labelled grids of sampled frames. You can
  see them: use them to find shots ("looking at camera", "wide shot", "whiteboard"), judge takes, and pick b-roll.
  Cell numbers map to item ids and timecodes in the JSON legend. Source frames, without grades or effects.
- `search_transcript(query)`: exact then fuzzy phrase hits with timecodes and context. For meaning-based questions
  read `transcribe` segments yourself.
- `remove_pauses(min_pause_seconds=0.7)`: speech gaps across the timeline. Report and mark first; `apply=True`
  builds a new tightened timeline (all tracks), original untouched. Ask before applying.
- `gaming_cleanup(preset="youtube")`: gameplay. Speech is never cut, game-audio events (relative to the game
  track's own baseline) and on-screen action spikes keep their run-up, and only loading/black screens, frozen
  menus and quiet stretches go. OBS mic and game tracks are found automatically, and phantom Whisper words are
  dropped. Marks purple "Dead air" first; `apply=True` builds a new timeline. Ask first. Use the $gaming-cleanup
  skill for the full flow.
- Editing moves: for "nudge this cut N frames" use `roll_edit(left_item_id, right_item_id, frames)`; to change one
  clip's length use `trim_edit(item_id, edge, frames, ripple)`. `slip_clip` changes what a clip shows without moving
  it; `slide_clip` moves it and its neighbours give or take the frames. Positive frames move the edit point later.
  Run with `dry_run=true` first and show the plan, then ask. Each run creates a new timeline. If it refuses with
  "only N frames of handle", offer an edit of at most N frames; don't fall back to split-and-delete.
- Housekeeping: `list_ai_timelines` (read-only) shows the duplicates the bridge made. Show the user, then
  `cleanup_ai_timelines(ids)` archives them (default) into the "Agent Work" bin; `action="delete"` only after a yes,
  with `user_approved=true`. "Checkpoint · …" timelines are the chat panel's rewind points: skip them unless asked.
- `match_shot(reference_item_id, target_item_id)`: read-only by default. Show the preview and residual numbers. The
  preview is source frames with the proposed CDL, not Resolve's render. Ask before `apply=true`, check `verified`,
  and keep `apply_result.previous` to undo with `set_clip_grade`. Clamped values or a high residual mean lighting or
  content differ too much for a CDL; offer a lower `strength`, and pick shots with similar framing.
- `render_check(start_frame, end_frame)`: before calling an edit finished, offer it. It renders, so ask first; pass
  a range to check just what changed. Treat each finding as "check": fades, holds and quiet beats can be
  intentional. Flash frames and black flashes usually mean a leftover frame or gap; clicks at cuts want a short
  crossfade. If `settings_restored` or `cleanup` is false, tell the user to check the Deliver page and In/Out marks.
- `visual_diff(original, preview)`: after edits on a copy, SEE the change points (rows are the biggest changes;
  `changes` has all of them with absolute frames). Removals use the original's timecode, insertions the revised
  one's. Source frames only, and audio-only edits show only in `structural_changes`: audition before calling it done.
- `styled_title(..., dry_run=True)` first: show the plan (track, timecode, font, position) and ask before inserting,
  preferably on a copy. `size` is relative to frame WIDTH; presets sit inside title-safe (80%) or action-safe (90%);
  in 9:16 avoid the bottom fifth. Report `font.fallback_used`, `placement` and any readback entry whose `set` is false.
- Timeline notes: for "do my notes", call `timeline_notes`, summarize a numbered plan with HH:MM:SS:FF timecodes and
  ask only about ambiguous ones (the $timeline-notes skill has the full flow). Note frames are absolute; call it
  again after any edit that shifts timing. After each note, `resolve_timeline_note(frame, status, reply)`; never
  delete the user's markers, and check `verified`.
- `remove_ranges(ranges)`: cut any ranges from every track on a duplicate timeline. Ask first. It and the trim tools
  report `carried` (transform, grade, enabled, color, Fusion comps, read-back clip properties such as AudioVolume,
  clip markers where safe, links) and `not_carried` (always transitions, fades and keyframed audio levels). Before a
  rebuild, ask whether the user made hand edits (crossfades, fades) that will need redoing, and list `not_carried`.
- `speech_regions(scope, item_id)`: word timings snapped to the audio energy. Use its pauses and `cut_points` for cut
  frames instead of raw Whisper word ends, and review `energy_only` / `unsupported_words` rather than cutting them.
  Words come back compact (`[word, start, end]`); pass `words="none"` on long clips, `"full"` only for a short range.
- `list_media(query=..., kind="clips")` finds one file without paging through every bin and timeline.
- `measure_loudness(target_lufs=-14)`: per-clip and program LUFS with the gain each clip needs. Read-only by default;
  `apply=true` (ask first) sets each clip's volume to its gain and reports before/after. Source audio, so the gain is
  the absolute clip volume to use, and track faders still apply: confirm with render_check.
- `detect_shots(threshold=0.3, mark=False)`: camera cuts inside long recordings, as timeline frames.
- `detect_beats(mark="downbeats")`: tempo, beats and bars of a music clip for cutting on the beat.
- `animated_captions(style="pop"|"karaoke"|"minimal")`: word-highlight captions rendered natively as a transparent
  overlay on a new top track (Free compatible). Not editable text: call again to restyle.
- `add_markers([...])`: many markers in one call (chapters, beats, notes).
- `search_footage(text="error", labels=[], facing_camera=False)`: when something is visible, from an on-device
  Apple Vision index (on-screen text with OCR typo tolerance, scene labels, faces and head direction), sampled every
  second and cached per file. Great for screen recordings ("when does VS Code open", "find the error dialog") and
  talking heads ("looking at camera"). `index_footage` pre-warms long footage and lists each clip's top labels.
  Confirm hits visually with `contact_sheet` or `timeline_frame` before cutting.
