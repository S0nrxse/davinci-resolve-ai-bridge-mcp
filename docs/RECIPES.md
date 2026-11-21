# Editing Recipes

These prompts are written to encourage inspection and verification.

## Safe First Test

```text
Call resolve_status and timeline_overview. Do not change clips. Add a blue marker at the current playhead named Bridge test, then inspect again and report the marker frame.
```

## Put A Logo On The Timeline

```text
Add /absolute/path/to/logo.png to video track 2 for 4 seconds at the current playhead. Scale it to 35 percent and place it in the bottom right corner inside a safe margin. Then inspect the timeline and tell me the new item id and its real duration in frames.
```

## Build A Simple Title Card

```text
Create a new timeline named Title Card. Add /absolute/path/to/background.jpg to V1 for 5 seconds, then add /absolute/path/to/logo.png to V2 for 5 seconds at 30 percent scale, centred. Inspect and confirm both durations match before telling me you are done.
```

## Fade An Overlay

```text
Inspect the timeline and find the image on V2. Set its opacity to 40, then read the transform back and confirm the value Resolve stored.
```

## Push In On A Clip Over Time

```text
Inspect the timeline. Take the clip under the playhead and animate its zoom from 100 percent to 125 percent over the clip's first two seconds using animate_zoom. Report keyframes_created and keyframes_verified from the reply: if keyframes_created is false, tell me a static zoom was applied instead and why, and do not claim the move is animated. Then call inspect_fusion and confirm the clip's other Fusion nodes are still connected.
```

## Fusion Transition On A Clip (not live-tested)

```text
Call current_context. On the clip under the playhead, build a 12-frame fade in from black with fusion_build named FadeIn (a black Background merged under the clip, Blend keyed 0 to 1). Report verified and fusion_chain, then call inspect_fusion and confirm my own Fusion nodes are still connected. For an Edit-page Cross Dissolve between two clips use add_transitions instead.
```

## Slide Or Zoom-Blur Transition (not live-tested)

```text
Call current_context. Make the clip under the playhead slide in from the left over 15 frames with a cubic_out ease using fusion_build named SlideIn. Then add a separate zoom-blur in over 12 frames named ZoomBlurIn (Transform Size 1.4 to 1 and Blur 25 to 0, ease_out). Report each stage's verified value and show me the bridge chain order.
```

## Animated Lower Third (not live-tested)

```text
On the clip under the playhead, build a lower third with fusion_build named LowerThird: a TextPlus reading "Jane Doe, Director" at size 0.06 and center [0.3, 0.15], merged over the clip. Animate its Center from [0.1, 0.15] to [0.3, 0.15] over the first 10 frames with ease_out. Use fusion_inspect_tool if you are unsure of an input id. Report verified.
```

## Keyframe One Of My Own Fusion Nodes (not live-tested)

```text
Call inspect_fusion on the clip under the playhead and list its tools. Then use fusion_inspect_tool on my Glow node and keyframe its Glow input 0 at frame 0, 0.6 at frame 6 and 0 at frame 12 with fusion_keyframe_input. Read it back with fusion_inspect_tool and tell me the keyframe frames.
```

## See A Grade Or Effect As It Renders

```text
Call current_context. I approve a short render: use render_frames on frames 86400, 86412 and 86424 and show me the contact sheet (timecodes are burned into each tile). Tell me whether settings_restored is true. Compare them with timeline_frame at the same frames, which shows the raw source.
```

## Undo Point Before A Risky Step

```text
Take snapshot_timeline labelled "before tightening", then tighten the pauses with remove_pauses(apply=true). If I don't like it, restore_snapshot opens a fresh copy of the snapshot; tell me which timeline is open and that nothing was deleted.
```

## Loudness Check

```text
Call measure_loudness(target_lufs=-16) on the timeline. List each clip's integrated LUFS and the gain it needs, flag peak-limited clips, and tell me which values to type into Inspector > Audio > Volume. Don't apply anything.
```

## Clean Cut Points From Speech

```text
Call speech_regions on the clip under the playhead. Show me the pauses longer than 0.5 s with their cut frames, and any sound with no words. Mark them with add_markers; don't cut yet.
```

## Trim A Clip In Place

```text
Call timeline_overview. Trim 8 frames off the head of the clip under the playhead with trim_in_place, dry_run first, then for real after I say yes. Report exact, the actual start/end and any gaps left. Don't ripple anything.
```

## Add Dissolves Between Shots

```text
Run add_transitions(cuts="all", duration_frames=12, dry_run=true) and show me which cuts get a dissolve and which are skipped for lack of handles. After I approve, run it for real, then use render_frames on the middle frame of each dissolve so I can see them. Remind me the original timeline is untouched.
```

## Even Out Clip Loudness

```text
Call measure_loudness(target_lufs=-16). Show me each clip's gain. After I approve, run it again with apply=true and list before/after volume per clip.
```

## Cut A Clip At The Playhead

```text
Inspect the timeline. Split the clip under the playhead at the current playhead frame with split_clip. Then inspect again and show me the two resulting item ids, their start and end frames, and report the retained checkpoint. Explain which clip data was preserved and which needs manual review.
```

## Import a Rendered Motion Graphic

```text
Inspect the project and current timeline. Import /absolute/path/to/out/video.mp4 into the current Media Pool. Confirm its media id, then ask me which video track and record frame to use before appending it.
```

## Organize With Clip Colors

```text
Inspect the timeline and list every clip id with its current color. Propose a simple color system for interviews, b-roll, graphics, and audio. Wait for approval, apply one category at a time, then verify.
```

## Disable Without Deleting

```text
Inspect the timeline and find the clip I describe. If its name is ambiguous, show me the matching ids. Disable the selected clip without deleting it, then verify its enabled state.
```

## Queue a Render

```text
Inspect the project and current timeline. Call list_render_presets and tell me which existing preset you plan to use and the output directory. Wait for approval. Add the render job but do not start rendering unless I separately approve it.
```

## Follow a Render

```text
I’ve started the queued render in Resolve. Call render_status every so often and tell me the percentage. When it finishes, report the status and the output file. If it fails, quote the error.
```

To queue and start a **new** job from the bridge, approve the render and use
`render_current_timeline(start=True)` once. For a job already in the queue,
start it in Resolve’s Deliver page so you don’t create a duplicate.

## Better Editing Brief

```text
Target: 30-second 16:9 product introduction
Audience: first-time customers
Pacing: calm, confident, no rapid montage
Must keep: all spoken lines and the final logo shot
May change: clip order, disabled takes, markers, clip colors, overlay graphics
Do not: delete source clips or start a render

First inspect the timeline. Summarize the existing story, propose an edit plan, and list anything the current MCP tools cannot perform reliably. Wait for approval before editing.
```

## Preview an edit

```text
Inspect my timeline, create a preview_timeline copy, and get fresh clip IDs. Apply the changes on that copy. Compare it with the original and tell me what changed, without deleting either timeline.
```

## Review dialogue pauses

```text
Inspect the timeline and call review_silence on my dialogue clip. Place review markers only. I will audition them and tell you which marker IDs to remove. Do not apply cuts yet.
```

## Project health

```text
Inspect the active timeline and run project_health for a 1920 by 1080, 24 fps delivery. Report missing sources and mismatches. List gaps and disabled tracks as items to review, not automatic mistakes.
```
