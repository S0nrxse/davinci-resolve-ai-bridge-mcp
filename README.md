# DaVinci Resolve AI Bridge MCP

<img width="768" alt="Resolve AI Bridge demo" src="https://raw.githubusercontent.com/flamexnreal/davinci-resolve-ai-bridge-mcp/main/assets/demo.gif" />

[![npm version](https://img.shields.io/npm/v/davinci-resolve-ai-bridge-mcp.svg)](https://www.npmjs.com/package/davinci-resolve-ai-bridge-mcp)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![DaVinci Resolve Free & Studio](https://img.shields.io/badge/DaVinci%20Resolve-Free%20%26%20Studio-brightgreen.svg)](https://www.blackmagicdesign.com/products/davinciresolve)

Let your AI assistant help with the edit. Resolve AI Bridge connects Claude, Codex, Cursor, Antigravity, and other MCP-compatible assistants to the project open in **DaVinci Resolve Free or Studio**.

Inspect a timeline, place media, animate a zoom, adjust color, or try an edit on a duplicate timeline before touching the original.

**Current Resolve Free works through the Lua connector.** Native-file Lua
startup and editing are live-tested on **macOS Free 21.1.2.10**. Python runs in
the external MCP server; Resolve itself needs no Python, FFI or socket library.
Choose **Start AI Bridge (Lua)** on Free 21.1+. The existing Python worker remains
available on older Python-enabled Free builds and Studio. See
[compatibility evidence and limits](docs/COMPATIBILITY.md).

### Built to stay light

- **Works with current Resolve Free.** Lua editing is verified on macOS Free 21.1.2.10; the older Python worker is verified on Free 21.0.3.7. Studio-only effects stay Studio-only.
- **One click to start.** Choose **Start AI Bridge (Lua)** on current Free, or **Start AI Bridge** on Python-enabled builds. Your AI gets the existing editing tools.
- **A small, local runtime.** No remote bridge server or manually entered token. Website dependencies stay out of the CLI package; FFmpeg and Remotion are optional.
- **Keep your tokens for the edit.** The launcher starts locally, without an AI request or extra startup tools.

> ⭐ Finding it useful? Give the bridge a star—it helps other editors find it.

---

## Quick setup

You'll need **Python 3.10+** ([download](https://www.python.org/downloads/)).

### Option A: Run directly with npx (Recommended for Node / npm users)

```bash
npx davinci-resolve-ai-bridge-mcp
```

### Option B: One-line shell install

**macOS / Linux** (run in Terminal):

```bash
curl -fsSL https://raw.githubusercontent.com/flamexnreal/davinci-resolve-ai-bridge-mcp/main/install.sh | bash
```

**Windows** (run in PowerShell):

```powershell
irm https://raw.githubusercontent.com/flamexnreal/davinci-resolve-ai-bridge-mcp/main/install.ps1 | iex
```

From a downloaded/cloned repository, run `python3 install.py --with-ffmpeg` (Windows: `py install.py --with-ffmpeg`), or use `install-macos.command` / `install-windows.bat` for the base install.

Node/npm is only needed for the npm installer and optional website/motion-graphics development.

The installer sets up `~/.resolve-ai-bridge`, a private Python environment, the Resolve menu entries, editing skills, and detected supported AI clients. Valid client configs are backed up and merged; invalid configs are left unchanged with a diagnostic. Antigravity and other clients can use the generated `mcp-config.json` manually.

Wait for **INSTALL COMPLETE**, then reopen Resolve. If setup stops after downloading, see [installer troubleshooting](docs/TROUBLESHOOTING.md#the-one-line-installer-downloads-but-stops).

<br>

<details>
<summary><b>Optional: add FFmpeg for source frames and audio</b></summary>

For Free-compatible source frame capture and efficient audio decoding, also install the optional private FFmpeg binary:

```bash
npx davinci-resolve-ai-bridge-mcp --with-ffmpeg
```

This optional download stays in the bridge's private Python environment. It is not a Studio dependency. An existing `ffmpeg` on PATH also works; macOS `afconvert` supports audio extraction without FFmpeg.

</details>

---

## Chat panel next to Resolve (macOS)

**Resolve AI Chat** docks a chat panel on the right edge of Resolve's window, so you can talk to Claude Code
without leaving the edit. It knows the project, timeline, playhead and clip under the playhead, and it asks
before each change to your project (**Allow** ⌘↵ / **Decline** esc). Fusion edits come back as a node
chain with keyframe markers.

1. Install as above. The installer builds `~/Applications/Resolve AI Chat.app` (needs the Xcode command
   line tools; skip it with `--no-chat-app`).
2. Sign in to Claude Code once: run `claude` and type `/login`.
3. In Resolve, start the bridge, then choose **Workspace → Scripts → Resolve AI Bridge → Open AI Chat**,
   or open the app. Press **⌥⌘A** to show or hide the panel.

Resolve Free can't embed panels (Workflow Integrations are Studio only), so the panel is a native window
that follows Resolve's window. Details, architecture and what has been verified: [docs/CHAT.md](docs/CHAT.md).

---

## Connect to Resolve

1. Open **DaVinci Resolve** with a project.
2. Choose **Workspace → Scripts → Resolve AI Bridge → Start AI Bridge (Lua)** on Free 21.1+. On Python-enabled builds, use **Start AI Bridge**. The folder may appear under **Utility**.
3. Ask your AI: **“Call resolve_status, then bridge_capabilities and timeline_overview.”** Open or create a timeline before using timeline tools; status works without one.

**Start from the menu and get editing.** Start once per Resolve session. In **Workspace → Console**, look for **RESOLVE AI BRIDGE LUA READY** (Lua) or **RESOLVE AI BRIDGE READY** (Python). Use **Stop AI Bridge (Lua)** for the Lua worker or **Stop AI Bridge** for Python; an active operation finishes first. Clicking Start again won't create another worker.

If the menu is missing after installation, quit and reopen Resolve once. Refresh/restart your AI client's MCP server after setup or a tool-schema update.

The Lua connector is live-tested on **macOS / Resolve Free 21.1.2.10**. Python startup remains verified on **Free 21.0.3.7 / Python 3.14.6**. Windows, Linux and other builds still need live verification.

<br>

<details>
<summary>Console fallback and startup details</summary>

On **Free 21.1+**, open **Workspace → Console**, select **Lua**, paste this portable fallback and press Enter:

```lua
if fusion:GetData("ResolveAIBridge.NativeApp")~=tostring(fusion) then fusion:RunScript((os.getenv("RESOLVE_AI_BRIDGE_HOME") or ((os.getenv("USERPROFILE") or os.getenv("HOME")).."/.resolve-ai-bridge")).."/ResolveLua.lua") end
```

On Python-enabled builds, select **Py3** and use:

```python
import os;exec(open(os.path.join(os.path.expanduser(os.getenv("RESOLVE_AI_BRIDGE_HOME") or "~/.resolve-ai-bridge"),"ResolveConsole.py"),encoding="utf-8").read())
```

**Live startup validation:** macOS, DaVinci Resolve **Free 21.0.3.7**, Python **3.14.6**: menu start, real status requests, repeated start, Stop, quit/reopen/start, and Console fallback passed. The separate native-file Lua connector supports the tested Free 21.1.2 build. Windows/Linux and other builds still need live verification.

The Lua menu worker stays in its native script context and uses `bmd.wait` between requests, keeping the editing UI usable. The Python menu route keeps a separate `fuscript` process alive; its Py3 fallback starts a daemon worker and returns after a short handshake. The Lua worker uses signed local file messages, with no socket or FFI dependency. No public MCP tool or AI startup call is added.

</details>

<br>

<details>
<summary>Studio: connect automatically with external scripting</summary>

Studio can use the same menu workflow. To allow direct attach, set **Preferences → System → General → External scripting using → Local**. When external scripting is available, the bridge connects directly. It probes that connection in a disposable process and uses the Console transport when direct attach is unavailable.

</details>

---

## What you can do

| In your edit | The bridge can help with |
| --- | --- |
| Find your bearings | Inspect clips, tracks, markers, media, project settings, and timeline health. |
| Build the timeline | Import media, place images and overlays, create timelines, and group compound clips. |
| Add movement | Set clip transforms, animate zooms, adjust supported speed controls, and stack spotlight/blur effects. |
| Work with color | Read and adjust supported grades, animate saturation, and add color shifts. |
| Review before committing | Try edits on duplicate timelines, compare structure, and mark candidate silence cuts. |
| Check source media | View source frames and analyze source audio levels, silence, and timing. |
| Deliver | Queue an approved render, follow its progress, and catch settings Resolve Free can't render. |

<br>

<details>
<summary>Full MCP tool list</summary>

| Tool | What it does |
| --- | --- |
| `resolve_status`, `bridge_capabilities` | Connection, version, current project and decoder/API availability. |
| `timeline_overview`, `project_info`, `list_timelines` | Inspect timeline structure, unique clip IDs, labels and settings. `timeline_overview(detail="compact"|"standard"|"full")`: compact by default; full adds source in/out, media fps, clip volume, Fusion comp count and transitions in/out. Transitions (which Resolve lists as items) are kept out of clip lists and labels and reported under `transitions`. |
| `timeline_frame` | Return a native MCP image plus metadata. Composite capture is attempted first; FFmpeg source fallback is labeled explicitly. |
| `render_frames` | SEE the finished picture: up to 10 timeline frames (or N evenly spaced in a range) rendered through a short temporary Deliver job, with grades, Fusion, titles and transforms. By default one contact-sheet image comes back: a grid with each tile's timecode and frame burned in (drawn by the bridge, no ffmpeg drawtext needed); `sheet=false` returns one PNG per frame. Same save/restore of Deliver settings and In/Out marks as `render_check`, and the jobs and files are removed. Renders, so ask first. Live-checked on Resolve 21.2 Free (a mid-dissolve frame rendered as a blend); the contact sheet is tested against fakes and real ffmpeg. |
| `timeline_audio` | Source PCM peak/RMS per channel, silence intervals, energy envelope, onsets, activity clusters or a trimmed WAV. |
| `transcribe`, `generate_subtitles` | Local speech-to-text (whisper.cpp, nothing uploaded, works on Free): word-level timeline frames, cached; readable `.srt` placed on a subtitle track when Resolve allows. |
| `find_filler_words`, `review_transcript_cuts` | Mark "um/uh" or any transcript-chosen range as reviewable cuts; apply with `apply_silence_cuts` on a duplicate timeline. |
| `speech_regions` | Read-only: Whisper word timings snapped to the source audio's energy, so cut points don't drift into pauses. Speech regions, pauses with a suggested cut frame at the quietest point, sound with no words, and words with no sound (likely phantoms), in absolute timeline frames. Source audio, not the Fairlight mix. `words`: "compact" (default, `[word, start, end]`), "full" (every snap detail) or "none". Live-checked on 21.2 Free. |
| `measure_loudness` | Integrated LUFS, LRA and true peak per clip and for all clips back to back (ffmpeg EBU R128), with the dB change each needs to reach a target such as -14 or -16 LUFS, limited by a true-peak ceiling. Source audio only. Read-only unless `apply=true`, which sets each clip's volume to its gain with `set_clip_volume` and reports before/after. |
| `snapshot_timeline`, `list_snapshots`, `restore_snapshot` | Mid-task undo: save a verified copy of the open timeline ("Checkpoint · …", shared with the chat panel), list them, and restore one by opening a NEW copy of it. A restore deletes nothing: the timeline that was open stays as it was. Clips in the restored copy have new unique ids. Live-checked on 21.2 Free (a trim undone; transform and opacity kept). |
| `search_transcript`, `remove_pauses`, `remove_ranges` | Find phrases with timecodes; tighten pauses across the whole timeline; cut any ranges from every track on a duplicate timeline. |
| `edit_points` | Trims and rolls in ONE call, in place (`Set*Offset`, each read back, no copy, no ripple), or one trim/slip/slide/roll on a rebuilt copy (`mode="new_timeline"`). Slip and slide are refused in place: nothing in Resolve 21.2 Free's API moves a clip. `trim_edit`, `slip_clip`, `slide_clip` and `roll_edit` remain as the copy-based tools. |
| `insert_clip`, `ripple_delete_ranges` | Place a source range `[src_in, src_out)` into empty space at an absolute frame, in place (a rippled insert is refused: clips can't be moved on Free); cut ranges from every track and close the gaps in one new timeline, reporting `transitions_dropped`. |
| `set_clip_properties`, `copy_grade` | Batch `SetProperty` (AudioVolume, Opacity, ZoomX, Pan, crop, composite... ) on clips picked by ids or a frame range, each value read back; copy one clip's grade to many. `set_clip_grade` also takes `item_ids` / `range` and reports a GetCDL read-back. |
| `diff_since` | Read-only: structured changes on the open timeline since a snapshot (or a baseline state): clips added, removed, moved, trimmed, enabled/color/property changes, transitions and markers. Call it before a rebuild to keep the user's hand edits. |
| `tighten` | Plan or apply dialogue tightening in one call: pauses (and optionally fillers) from energy-snapped word timings with floor-aware word trust, `lead_frames`/`tail_frames` air, `max_pause_frames`, `exclude_ranges`; every cut lists the words before and after. `mode="apply"` makes one `remove_ranges` copy. L-cuts and audio crossfades are reported as not applied (no verified API). Live-checked on 21.2 Free: a 15 s spoken clip lost two long pauses and an "um" (5.3 s), every sentence intact; preference defaults were used. |
| `trim_in_place`, `set_clip_length` | Trim (positive) or extend (negative) a clip's head and tail directly on the open timeline with `SetLeftOffset`/`SetRightOffset`: no copy, no ripple, linked audio follows, neighbours never move. Extending into a neighbour or past the media is refused; gaps left are reported; everything is read back (mixed frame rates can land a frame off). `set_clip_length` sets a still's (or title's) length. Offsets verified live on Resolve 21.2 Free; the tools themselves are tested against fakes. `SetPosition` and `Timeline.MoveItems` exist on 21.2 Free but return False for every signature tried, so clips cannot be moved in place. |
| `set_clip_volume` | Clip volume in dB (Inspector > Audio > Volume, `AudioVolume`), read back; a video clip maps to its linked audio. `measure_loudness(apply=true)` uses it. Verified live on 21.2 Free at -6, 0 and +6 dB. |
| `add_transitions` | Cross Dissolves at V1 cuts (all, or listed cut frames) on a NEW timeline "<name> (transitions)", through an FCPXML 1.10 export, edit and import (verified live on 21.2 Free). Re-applies transforms, grades, Fusion comps, clip properties, clip and timeline markers, enabled state and color, then checks the layout. Refuses timelines beyond one video track with its own audio, titles/generators, retimed clips, subtitles or a frame-rate/start mismatch; stills on V1 are supported (any handle length). Importing adds a second media pool entry per file (the same files), which is filed into the Agent Work bin and reported under `media_pool_entries`. Transform values are re-applied only where they differ. Optional audio fades are written but unverified. Live-checked on 21.2 Free (2 dissolves, verified). |
| `trim_edit`, `slip_clip`, `slide_clip`, `roll_edit` | Frame-accurate moves: trim a head or tail (ripple or not), slip the source, slide a clip between its neighbours, roll a shared cut. Each builds a new timeline (original kept), rebuilds only the changed clips, refuses edits beyond the media handles and verifies by reading back. `dry_run=true` returns the plan only. Tested against fakes; not yet run on a live Resolve. |
| `list_ai_timelines`, `cleanup_ai_timelines` | List the duplicate timelines the bridge made (previews, "(tightened)", "(rewound)", "Before split", chat checkpoints) with kind, clip count, duration and likely original; archive them into the "Agent Work" bin (where every bridge-made timeline is filed by default) or delete them (`user_approved=true`). User timelines and the open timeline are refused; checkpoints are skipped unless `include_checkpoints=true`. |
| `match_shot` | Match one clip's color to another by measurement: samples source frames, solves a CDL (slope/offset/power/saturation) from 5/50/95 percentiles, returns the numbers, residual error and a reference / before / after preview. `apply=true` (ask first) sets node 1's CDL and verifies it with GetCDL (Resolve 21.2+). Matches source media, not existing grades. |
| `render_check` | Renders a small H.264 preview of the timeline or a frame range, then reports black frames, 1–2 frame flashes, frozen picture, clipping, dropouts, quiet gaps, loudness jumps, and clicks or room-tone jumps at audio cuts, with timecodes and loudness/LRA/true peak. Optional `mark=true` adds QC markers. Saves the Deliver settings and In/Out marks and restores them after the render, then removes its job and file. Renders, so ask first. The post-render restore is tested against fakes only. |
| `visual_diff` | Read-only: a before/after image grid plus a JSON list of where two timeline versions differ (removed, inserted, replaced, moved, gaps, clip settings), without opening either timeline. Source frames only: no grades, effects or titles. |
| `timeline_notes`, `resolve_timeline_note` | Read the notes you left as markers (any marker with a name or comment, excluding the bridge's own pause/dead-air/shot/beat/QC/review markers) with timecode and the clips under each; mark one done (Green) or skipped (Cream) with a one-line reply appended to the note. The chat panel shows open notes above the composer with a "Do them" button. Recolouring is a delete and re-add that is read back; the original is restored if the re-add fails. Tested against fakes only. |
| `styled_title` | A Text+ title at an absolute frame with exact font, style, size (relative to frame width), colour and safe-area position, plus an optional background box; `dry_run` first. Reports font fallback, the Text+ readback (`text_set`, `font_set`, `size_set`, `center_set`) and where Resolve actually placed it. Not yet inserted on a live Resolve. |
| `gaming_cleanup` | Gameplay cut-up: never cuts talking or game-audio/on-screen action, only loading screens, frozen menus and quiet dead air. Reads OBS mic and game tracks separately and filters Whisper phantom words. Presets `vod`, `youtube` and `shorts`; marks first, applies on a duplicate timeline. Checked on synthetic recordings, not yet on a real Resolve project. |
| `contact_sheet`, `detect_shots`, `detect_beats`, `add_markers` | Let the AI see footage as labelled frame grids; find camera cuts; find tempo/beats/bars; place many markers at once. |
| `index_footage`, `search_footage` | On-device (Apple Vision) index of on-screen text, scene labels and faces; find when something is visible. |
| `animated_captions` | Word-highlight captions rendered natively (macOS) as a transparent overlay on a new top track. |
| `preview_timeline`, `compare_timelines` | Duplicate/open a review timeline and compare structure/properties/markers without switching during comparison. |
| `project_health` | Check active-timeline missing sources, gaps, disabled/locked tracks/clips, supplied delivery expectations and Resolve Free's UHD/60 fps render limits. |
| `review_silence`, `apply_silence_cuts` | Place candidate markers, then apply explicitly accepted intervals on a duplicate of an isolated dialogue clip or aligned AV pair. |
| `open_timeline`, `create_timeline`, `set_playhead`, `open_page` | Navigate Resolve. `create_timeline(name, fps, width, height, bin)` sets fps/size while the timeline is empty and reads them back (on 21.2 Free only "30" was accepted in testing), and warns when the media pool's video runs at another rate. Every timeline the bridge creates is filed into the **Agent Work** bin (`bin=""` to leave it where Resolve puts it); the media pool view is restored. |
| `list_media`, `import_media`, `append_media`, `add_image` | Inspect/import/place media and duration-controlled stills. `list_media(query, kind)` filters by name or file path (exact matches first) and `kind="clips"` leaves timelines out; files in the pool more than once are grouped under `same_file_entries`. `add_image` sets the still's length with temporary media In/Out marks (Resolve 21.2 Free ignores the append end frame for stills), then restores the item's previous marks; it reports the real length and how it was set. |
| `set_clip_transform`, `get_clip_transform`, `animate_zoom` | Static transforms and a keyframed Fusion zoom that keeps your other Fusion nodes connected. |
| `split_clip` | Rebuild a normal-speed clip into two pieces, with a verified timeline checkpoint first. See restrictions below. |
| `insert_title` | Best-effort title insertion; check `text_set`. |
| `create_compound_clip`, `change_clip_speed` | Compound creation and constant video speed through a bridge-owned TimeSpeed node. |
| `get_clip_grade`, `set_clip_grade`, `keyframe_clip_saturation`, `animate_color_fx` | Inspect/change available grade controls and Fusion color animation on bridge-owned nodes. |
| `apply_blur_effect`, `apply_spotlight_mask` | Stackable Fusion blurs (gaussian, defocus, directional) and spotlight masks, static or keyframed. |
| `inspect_fusion` | Read-only view of a clip's Fusion nodes and the bridge's effect chain. |
| `current_context` | Cheap read-only snapshot: project, timeline, page, playhead, the clip under it on each video track, and the timeline list. |
| `project_overview` | Read-only picture of the whole project: every timeline, media pool bins and clips, settings and render queue. |
| `resolve_api` | Escape hatch: chain any Resolve scripting API calls across the project. Compact by default (long lists come back as `{count, items}`; `return_steps`, `quiet`, `full_lists`), `for_each` over an earlier list with `$item`/`$index`, targets by id (`item:<uuid>`, `media:<uuid>`, `timeline:<uuid>`), and `getattr` steps for constants such as `EXPORT_FCPXML_1_10`. Deleting data needs `allow_destructive=true` after the user approves. |
| `fusion_build`, `fusion_keyframe_input`, `fusion_inspect_tool` | Build any Fusion node graph as a named stage before MediaOut, set, keyframe, re-key (`replace=false` merges into an existing spline or XYPath) or clear (`clear=true`) any input of any node, and list a node's inputs. Clip-level transitions are built this way. |
| `fusion_apply_setting`, `fusion_run_lua` | Paste `.setting`/macro text (optionally wired into the chain; file Loaders Resolve converts are reported) and an advanced Lua escape hatch with `comp` bound to the named clip. A guard refuses to run in any other comp. Lua connector or Studio direct attach only. |
| `fusion_inspect` | One read-only Fusion view: without `tool`, nodes, connections, bridge chain and `frame_format` (comp width/height, render range and the MediaIn source file; masks and Merge centres are relative to this image, which is the source media's size); with `tool`, that node's inputs, values and keyframes. `inspect_fusion` and `fusion_inspect_tool` remain as aliases. |
| `place_still`, `place_media` | Clearer names for `add_image` (a still for exactly N frames) and placing media (`src_in`/`src_out` source frames, `src_out` exclusive, at a record frame via `insert_clip`, or appended whole via `append_media`). The old names remain. Live-checked on 21.2 Free: exact ranges with linked audio, and a 36-frame still on V2 at zoom 0.5 and 80% opacity, confirmed in a render. |
| `get_preferences`, `set_preferences` | Per-project editor preferences in the bridge home: working timeline, agent bin (default for every bridge-made timeline), tighten defaults, loudness target, notes. Tools default from them. Live-checked on 21.2 Free. |
| `fusion_graph`, `fusion_copy_comp` | Build a node graph from explicit `["Tool.Output", "Node.Input"]` links (sources: graph nodes, your tools, `MediaIn` or `chain`) as one named stage before MediaOut, with full rollback (including a comp the call created). Copy a clip's comp onto other clips through ExportFusionComp/ImportFusionComp; `replace` backs up and replaces a target's single comp (refused over your nodes or bridge effects without approval, never stacks a second comp), `add` stacks it. Converted file Loaders are reported per target. |
| `add_marker`, `delete_marker`, `set_clip_property`, `set_clip_color`, `set_clip_enabled` | Markers and clip metadata/state. |
| `add_track`, `set_track_name`, `delete_clips`, `save_project` | Track management, explicitly requested deletion, and saving. |
| `list_render_presets`, `render_current_timeline`, `render_status` | Inspect presets, queue/start an approved render, and follow its progress. |

</details>

<br>

### Motion graphics with Remotion

For custom typography, lower thirds, or animated callouts, your AI can create a Remotion video and import it with `append_media`. Remotion is a separate, optional toolchain; it isn't bundled into the bridge's Python runtime.

[Motion graphics workflows](docs/REMOTION.md) · [Editing recipes](docs/RECIPES.md)

---

## A few things to know

**Free support doesn't unlock Studio features.** Source frames exclude timeline effects, and source audio isn't the Fairlight mix. Some edits have limits: splits rebuild clips, title text is best effort, and silence cuts are restricted to supported clip layouts.

<br>

<details>
<summary>Editing workflow, clip IDs, and frame/timecode rules</summary>

1. Call `resolve_status` and `timeline_overview`.
2. Use each clip's **`id`**, which uses its Resolve unique ID when available. `label` values such as `V1.2` remain accepted but can change after insertions/deletions. Rebuilt and duplicated clips have new IDs.
3. Call `preview_timeline` before a batch of changes. It creates and opens a copy while keeping the original.
4. Get fresh IDs from `timeline_overview`, make edits, and inspect after each change.
5. Use `compare_timelines(original, preview)` to review structural changes. Audition/view the result in Resolve too: the comparison does not render pixels or compare all Fusion, color, or Fairlight data.

All public `frame` inputs and clip start/end positions use **absolute timeline frames**. Marker offsets are relative to timeline start. Timecodes support non-drop-frame and 29.97/59.94 drop-frame (`HH:MM:SS;FF`). Cut end positions are exclusive.

Fusion effects (`animate_zoom`, blur, spotlight, saturation, color fx) are the exception, because they live inside one clip: `start_frame`, `end_frame` and keyframe `frame` count from the clip's first frame (0). Pass `timeline_start_frame`, `timeline_end_frame` or keyframe `timeline_frame` to use absolute timeline frames instead.

</details>

<br>

<details>
<summary>Tool compatibility and limitations</summary>

- **Free edition:** Use the native-file Lua worker on Free 21.1+. It uses ordinary timeline, marker, clip and Fusion APIs and shares the existing Python editing implementation outside Resolve. No Studio AI transcription, Magic Mask, Smart Reframe, or neural feature was added.
- **Frame images:** Source fallback shows decoded source media, **not** the graded/composited viewer. It omits Fusion, transforms and overlays. `mode="composite"` fails explicitly if a composited still is unavailable. Without a resize utility, actual dimensions are reported rather than invented.
- **Audio:** Analysis reads source PCM, not the Fairlight mix. It excludes timeline gain, fades, mute, effects and retiming. Known source retiming is rejected. Silence uses 50 ms windows, not speech transcription. Results include the source offset, absolute timeline origin, exclusive ends and a truncation flag; default analysis limit is 300 seconds.
- **Splits:** Fusion clips, locked tracks, mixed rates and known retiming are rejected before deletion. A checkpoint is retained. Rebuilt clips restore static transforms, enabled state, clip color and the current color-grade layer. Audio links, fades, keyframes, other grade layers and all metadata are not guaranteed. On failure, the tool opens the checkpoint and reports any partial attempted timeline honestly.
- **Dialogue cuts:** Automatic application is restricted to one isolated clip or one aligned video/audio pair. It rejects stale review markers and complex multi-clip timelines. The original is kept intact. Review audio gain, fades and non-review markers on the result.
- **Fusion effects:** Zoom, blur, spotlight, saturation and color fx share one bridge-owned chain before MediaOut. Effects, speed and resets passed live checks through the Lua connector on macOS Free 21.1.2.10 and through Python on Free 21.0.3.7. Tests also cover preserving user nodes. Other builds, Studio and large comps need verification; inspect results with `inspect_fusion` or the Fusion page.
- **Project-wide tools:** `project_overview` and `resolve_api` are covered by mocked tests only and **have not been live-tested in Resolve yet**. Over the Lua connector every API call is a round trip, so large projects make `project_overview` slower; its limits keep it bounded.
- **Rebuilds** (`trim_edit`, `slip_clip`, `slide_clip`, `roll_edit`, `remove_ranges`, `remove_pauses`): each rebuilt clip gets its static transform, crop, composite mode and opacity, current grade layer, enabled state, clip color, Fusion comps, any other property `GetProperty` reports (each read back; `AudioVolume` is tried too), clip markers when the piece starts at the original's source frame, and audio/video links. Every reply lists `carried` and `not_carried`. Transitions, fades and keyframed audio levels cannot be read through the API and are never carried. Tested against fakes only.
- **Fusion edits must show, not just exist:** on Resolve 21.2 Free, nodes and values set through the API outside an undo group were kept in the clip's comp (inspection and `ExportFusionComp` showed them) but the viewer and Deliver renders kept using the old comp, even after opening the Fusion page. Every bridge Fusion edit now runs inside `StartUndo`/`EndUndo(true)`, which marks the comp modified so Resolve re-reads it; this also gives the Fusion page one undo step per edit. Checked live with `render_frames`: a `fusion_graph` tint on a clip never opened on the Fusion page rendered, and a `fusion_copy_comp` copy rendered on its target showing the target's own frames. Earlier "passed live checks" notes for Fusion effects checked the comp's structure, not the rendered picture.
- **General Fusion tools:** `fusion_graph` and `fusion_copy_comp` are live-checked as above. `fusion_build`, `fusion_keyframe_input`, `fusion_inspect_tool`, `fusion_apply_setting`, `fusion_run_lua` and `current_context` are covered by mocked tests (including running the generated Lua under `lupa`) but **have not been live-tested in Resolve yet**. `fusion_apply_setting` depends on `bmd.readstring` and `comp:Paste` being available to scripts on your build. On Resolve 21.2 Free a live session showed `fusion_run_lua` code seeing the Fusion page's comp instead of the named clip's; `comp` is now bound explicitly and a one-time tag guard stops any script that lands in another comp (`fusion_build`/`fusion_keyframe_input` then apply the same edits through node handles). These fixes are tested against fakes, not yet live. Resolve's API cannot add Edit-page transitions directly: `add_transitions` builds Cross Dissolves through an FCPXML round trip on a new timeline, and the documented clip-level Fusion transitions remain the alternative for other looks.
- **Speed:** Fusion mode changes video timing inside the clip, not timeline duration or linked audio. Resetting to 1× neutralizes only the updated bridge's own node; user-created and older untagged TimeSpeed nodes are left alone. Reverse mapping is not verified and is rejected. Clip-attributes mode changes **all uses of that media**, multiplying its current source FPS; it is not a per-clip reset or speed ramp.
- **Validation:** Automated tests cover mocked Resolve operations, source decoding and MCP responses. They do not certify every operation on every Free-version build or platform. See [testing guidance](docs/TESTING.md) for the manual integration checklist.

</details>

---

## Connect your AI client

Most setup is handled by the installer. For manual configuration, use the absolute-path entry in `~/.resolve-ai-bridge/mcp-config.json`. No token needs to be typed into your client; the server and worker share a local token file automatically.

<br>

<details>
<summary>Codex / ChatGPT desktop setup</summary>

The installer registers the bridge when it finds the Codex CLI. Check with:

```bash
codex mcp get resolve-ai-bridge
```

If needed, register the installed runtime on macOS/Linux:

```bash
codex mcp add resolve-ai-bridge -- "$HOME/.resolve-ai-bridge/.venv/bin/python" "$HOME/.resolve-ai-bridge/bridge/server.py"
```

On Windows PowerShell:

```powershell
codex mcp add resolve-ai-bridge -- "$HOME/.resolve-ai-bridge/.venv/Scripts/python.exe" "$HOME/.resolve-ai-bridge/bridge/server.py"
```

Restart/refresh the MCP server in the desktop app after setup or a tool-schema update. Start a new task and ask: **“Call resolve_status, then bridge_capabilities and timeline_overview.”** With no timeline open, `resolve_status` can still confirm the project connection.

The ChatGPT desktop app and local Codex clients on the same host share the Codex MCP configuration. **ChatGPT web does not read this local configuration**; remote plugin/tunnel setup is a separate integration and is not installed by this project. See [official MCP setup documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

For lengthy source analysis, the optional Codex server setting `tool_timeout_sec = 180` allows more time than its default timeout. Keep it inside the existing `[mcp_servers.resolve-ai-bridge]` table.

</details>

<br>

<details>
<summary>Other clients, npm server mode, and generated commands</summary>

Use the absolute-path server entry generated at `~/.resolve-ai-bridge/mcp-config.json`. The installer also writes `claude-command.txt` and `codex-command.txt` there.

For clients that support npm commands, **install first**, then start the server with the explicit `--serve` mode:

```json
{
  "mcpServers": {
    "resolve-ai-bridge": {
      "command": "npx",
      "args": ["-y", "davinci-resolve-ai-bridge-mcp", "--serve"]
    }
  }
}
```

`npx davinci-resolve-ai-bridge-mcp` without `--serve` is the **installer**, not an MCP stdio process. Prefer the generated absolute Python command for predictable offline startup. The installer-created `resolve-ai-bridge` launcher starts the server directly; npm's executable with the same name requires `--serve`.

No token is needed in the client configuration. The server and Console worker share a local token file automatically.

</details>

---

## Troubleshooting & updates

If the bridge is offline, open a project and choose **Start AI Bridge (Lua)** on Free 21.1+, or **Start AI Bridge** on Python-enabled builds. Run the doctor from the repository for a connection check:

```bash
python3 tools/doctor.py
```

Use `python3 tools/doctor.py --offline` for file checks without contacting Resolve.

Before updating or uninstalling, stop the worker and quit your MCP clients. The installer validates the replacement before activating it and keeps the previous runtime for recovery.

[Troubleshooting](docs/TROUBLESHOOTING.md) · [Testing](docs/TESTING.md) · [Release notes](RELEASE_NOTES.md)

<br>

<details>
<summary>Request safety, timeouts, and update recovery</summary>

Console requests carry a unique ID, deadline, worker session and the project/timeline IDs last observed by that MCP client. Inspect `resolve_status` or `timeline_overview` in each client before its first queued edit. Mutations validate that context immediately before execution; stale or unavailable IDs are rejected. If only the worker session changed (Stop/Start, worker restart) and the same project and timeline are still open, the client re-observes and the edit goes ahead with a `transport_note`; a switched timeline is still refused, naming both. Calls in one MCP process run one at a time in arrival order, and waiting for another client's operation has its own budget (`RESOLVE_AI_BRIDGE_LOCK_WAIT_SECONDS`, default 180) instead of eating the operation's timeout. A Lua refusal the worker proves came before dispatch (for example `stale_request` after Resolve stalled) is retried once; nothing that may have reached dispatch is retried, and a failed read no longer forces re-inspection. There is no general batch rollback: rebuilds work on a copy and keep the original, Fusion builds roll back their own nodes, and `resolve_api` steps that ran are not undone, so take `snapshot_timeline` before a risky batch. Restart the Console worker after updating: older workers are refused by the new client. Only one Console worker should use a runtime directory.

The Lua route signs local request/reply/heartbeat bytes with HMAC-SHA256. Complete operations hold a cross-process lock and native lease; object handles and Stop requests belong to one session. Partial file reads are retried without resending edits. An uncertain response halts further native calls, records an unknown outcome and requires fresh inspection. Lua's heartbeat runs between native calls and can pause during a long native call; it is not a progress guarantee.

The heartbeat reports `busy`, `state` (`running`, `completed`, `failed`, or initial `idle`) and `request_id`. Its reporting thread makes no Resolve API calls; project/timeline details are cached during jobs. A fresh heartbeat proves the Python reporter is alive, not that a native operation is making progress. A native call holding Python's GIL may still prevent heartbeat updates.

A client timeout stops waiting; it **does not confirm cancellation**. Queued requests expire before execution, but active native work can finish later. Inspect `~/.resolve-ai-bridge/requests/<request-id>.json` and the timeline before attempting another edit. Completed/failed records retain results; a surviving `running` record after a crash means an unknown outcome and prevents replay of that ID. This is duplicate suppression, not an exactly-once guarantee. Completed/failed records are cleaned up after 7 days; `running` and explicit Lua `unknown` records stay for 30 days. The Python worker runs cleanup while active; the Lua transport runs it on tool calls, at most once every 10 minutes per MCP process/runtime. Cleanup also removes unread Python replies after an hour and rotates its log at 1 MB. A newly submitted request has a new ID.

In auto mode, direct attach falls back to Console only before dispatch. Forcing `RESOLVE_AI_BRIDGE_MODE=direct` disables worker fallback and reports offline if direct attach is unavailable. Unexpected exceptions after dispatch report an unknown outcome without replay. UI switches during native execution cannot be locked out: keep the intended project/timeline open while edits run. Context validation catches mismatches immediately before execution, not switches away and back between observations.

Before updating, stop the Console worker and MCP clients. The installer builds and validates a staged runtime and dependencies before activation, retains the old directory at `~/.resolve-ai-bridge.previous`, and restores it on a caught activation failure. A hard interruption can leave a sibling `.install-lock` and `.stage-*` directory; see troubleshooting for recovery. Configuration/menu registration follows activation and is not one transaction with it. Run `python3 tools/doctor.py --offline` for file diagnostics without contacting Resolve.

</details>

<br>

<details>
<summary>Development and verification</summary>

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
node tools/check-package.mjs
python3 tools/doctor.py
```

Website development dependencies are separate from the published CLI's runtime dependencies:

```bash
npm ci
npm run build
```

The package check creates and inspects a temporary npm tarball; it does not publish. GitHub Actions runs the suite, package check, website build and dependency audit on Linux, macOS and Windows. Install optional `lupa` (`python3 -m pip install lupa`) to run the Fusion Lua tests too. Maintainers: see [release instructions](docs/RELEASING.md).

</details>

---

## License

MIT. See [LICENSE](LICENSE).
