/**
 * Synthetic demo data for `?demo` (design review and screenshots without a live agent).
 * Every name, timecode and result here is invented and labeled as a demo in the UI.
 */
import type { AppState, ChatEvent, FileItem, Skill } from "./api";

const now = Date.now() / 1000;

/** A drawn stand-in for a viewer still (dim interview set, warm practical light). Not a real frame. */
export const demoFrame =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 180'>
<defs>
<linearGradient id='b' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='#2b3038'/><stop offset='1' stop-color='#0c0d0f'/></linearGradient>
<radialGradient id='l' cx='0.8' cy='0.28' r='0.55'><stop offset='0' stop-color='#e0a062' stop-opacity='0.6'/><stop offset='1' stop-color='#e0a062' stop-opacity='0'/></radialGradient>
<radialGradient id='r' cx='0.5' cy='0.4' r='0.5'><stop offset='0.6' stop-color='#000' stop-opacity='0'/><stop offset='1' stop-color='#000' stop-opacity='0.55'/></radialGradient>
</defs>
<rect width='320' height='180' fill='url(#b)'/>
<rect x='222' y='26' width='56' height='70' rx='2' fill='#454a52' opacity='0.45'/>
<rect width='320' height='180' fill='url(#l)'/>
<rect x='0' y='132' width='320' height='48' fill='#101113' opacity='0.6'/>
<ellipse cx='138' cy='76' rx='22' ry='26' fill='#1a1b1f'/>
<path d='M74 180c5-42 32-62 64-62s59 20 64 62z' fill='#1a1b1f'/>
<rect width='320' height='180' fill='url(#r)'/>
</svg>`,
  );

const svg = (body: string) => "data:image/svg+xml;utf8," + encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 180'>${body}</svg>`);

/** Drawn stand-ins for media pool poster frames. Not real footage. */
export const demoPosters: Record<string, string> = {
  m1: demoFrame,
  m2: svg(`<defs><linearGradient id='s' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='#0b1424'/><stop offset='1' stop-color='#1b2433'/></linearGradient></defs>
<rect width='320' height='180' fill='url(#s)'/>
<path d='M0 180V96h22v-30h18v42h16V74h26v34h14V52h24v56h12V84h20v24h18V62h30v46h16V90h22v18h18V70h24v110z' fill='#07090d'/>
<g fill='#f2b866' opacity='0.85'><circle cx='38' cy='150' r='5'/><circle cx='92' cy='158' r='3'/><circle cx='150' cy='146' r='7' opacity='0.5'/><circle cx='214' cy='156' r='4'/><circle cx='268' cy='148' r='6' opacity='0.6'/></g>
<g fill='#7fb4e6' opacity='0.7'><circle cx='120' cy='160' r='4'/><circle cx='240' cy='164' r='3'/></g>`),
  m3: svg(`<rect width='320' height='180' fill='#0d0d0e'/>
<rect x='136' y='66' width='48' height='48' rx='10' fill='none' stroke='#e98a2f' stroke-width='5'/>
<path d='M151 90h18M160 81v18' stroke='#f0f0f0' stroke-width='5' stroke-linecap='round'/>`),
};

export const demoMedia = [
  { id: "m1", name: "Interview_A_cam2.mov", bin: "Footage/Interviews", duration: "00:04:12:03", type: "Video + Audio", thumb: true, poster: demoPosters.m1 },
  { id: "m2", name: "Broll_city_night.mov", bin: "Footage/B-roll", duration: "00:00:21:10", type: "Video", thumb: true, poster: demoPosters.m2 },
  { id: "m3", name: "Logo_reveal.png", bin: "Graphics", type: "Still", thumb: true, poster: demoPosters.m3 },
];

const demoParams = new URLSearchParams(typeof location === "undefined" ? "" : location.search);

export const demoContext = {
  connected: true,
  state: "ready",
  project: "Demo · Studio Session",
  timeline: "Main Edit v3",
  timeline_count: 4,
  // ?demo&page=color previews the page-aware suggestions; ?demo&notes=0 hides the timeline notes.
  page: demoParams.get("page") || "edit",
  open_notes: demoParams.get("notes") === "0" ? 0 : 5,
  playhead_frame: 86412,
  playhead_timecode: "01:00:12:04",
  fps: 24,
  clips_at_playhead: [
    { id: "a91f", label: "V2.1", name: "Interview_A_cam2.mov", track: 2, has_fusion: true, start: 86380, end: 86520 },
    { id: "77c0", label: "V1.4", name: "Broll_city_night.mov", track: 1, has_fusion: false, start: 86200, end: 86460 },
  ],
};

export const demoState: AppState = {
  threads: [
    { id: "demo1", title: "Glitch transition into the interview", agent: "claude", created: now - 600, updated: now - 30, running: false, messages: 1 },
    { id: "demo2", title: "Lower third for Maya", agent: "claude", created: now - 7200, updated: now - 5400, running: false, messages: 3 },
  ],
  settings: { agent: "claude", model: "opus", effort: "high", mode: "ask", safe_copy: true, attach_context: true, always_allow: [] },
  agents: [
    { id: "claude", label: "Claude Code", available: true, approvals: true, path: null },
    { id: "codex", label: "Codex", available: false, approvals: false, path: null },
  ],
  context: demoContext,
  version: "1.14.4",
};

const M = "mcp__resolve-ai-bridge__";

export const demoEvents: ChatEvent[] = [
  { type: "user", id: "u1", ts: now - 60, text: "Build a glitch transition into this clip over the first 12 frames. Keep it punchy, not cheesy.", context: demoContext, attachments: [{ id: "att1", name: "frame_01_00_12_04.png", type: "image/png", size: 418000, thumb_id: "m1" }], rewind: true, checkpoint: { id: "cp1", name: "Main Edit v3 · before AI 14:02", timeline: "Main Edit v3", bin: "AI Checkpoints" } },
  { type: "thought", msg: "m0", ts: now - 59.5, end: now - 58.2, text: "The user wants a glitch *into* the clip, so it belongs on the head, frames 0–12. Punchy, not cheesy: short RGB split that resolves fast, a few frames of shake, a directional smear. First check whether the clip already has Fusion nodes so I don't break their chain." },
  { type: "text", msg: "m1", ts: now - 58, text: "I'll inspect the clip's Fusion comp first, then build the glitch on its head." },
  { type: "tool_start", tool_id: "t1", ts: now - 57, name: M + "current_context", input: {} },
  { type: "tool_result", tool_id: "t1", ts: now - 56, ok: true, summary: '{"ok": true, "result": {"timeline": "Main Edit v3"}}' },
  { type: "tool_start", tool_id: "t2", ts: now - 56, name: M + "inspect_fusion", input: { item_id: "a91f" } },
  { type: "tool_result", tool_id: "t2", ts: now - 55, ok: true, summary: '{"ok": true, "result": {"has_fusion": true, "bridge_chain": {"stages_before_media_out": []}}}' },
  {
    type: "tool_start",
    tool_id: "t3",
    ts: now - 54,
    name: M + "fusion_build",
    input: {
      item_id: "a91f",
      name: "GlitchIn",
      chain: ["Shake", "ChannelSplit", "RGBOffset", "Blur"],
      nodes: [
        { name: "Shake", type: "Transform", inputs: { Center: { keyframes: [{ frame: 0, value: [0.53, 0.48] }, { frame: 3, value: [0.47, 0.51] }, { frame: 6, value: [0.51, 0.5] }, { frame: 12, value: [0.5, 0.5] }] } } },
        { name: "ChannelSplit", type: "ChannelBoolean", inputs: {} },
        { name: "RGBOffset", type: "Merge", inputs: { Blend: { keyframes: [{ frame: 0, value: 1 }, { frame: 8, value: 0.3 }, { frame: 12, value: 0 }] } } },
        { name: "Blur", type: "DirectionalBlur", inputs: { Length: { keyframes: [{ frame: 0, value: 0.08 }, { frame: 5, value: 0.02 }, { frame: 12, value: 0 }] } } },
      ],
    },
  },
  { type: "checkpoint", message: "u1", id: "cp1", name: "Main Edit v3 · before AI 14:02", timeline: "Main Edit v3", bin: "AI Checkpoints", ts: now - 54.1 },
  { type: "permission", request: "p1", ts: now - 54, tool: M + "fusion_build", tool_id: "t3", input: {} },
  { type: "permission_resolved", request: "p1", ts: now - 50, decision: "allow" },
  { type: "tool_result", tool_id: "t3", ts: now - 48, ok: true, summary: '{"ok": true, "result": {"stage": "custom:GlitchIn", "verified": true}}' },
  { type: "tool_start", tool_id: "a1", ts: now - 47.5, name: "Task", input: { description: "Check other intros for matching style", prompt: "Look through the other timelines for existing glitch or RGB-split treatments and report their timing." } },
  { type: "thought", msg: "s0", ts: now - 47.4, end: now - 47.1, parent: "a1", text: "Scan the 4 timelines for Fusion comps named like glitch/RGB." },
  { type: "tool_start", tool_id: "a2", ts: now - 47.2, name: M + "project_overview", input: {}, parent: "a1" },
  { type: "tool_result", tool_id: "a2", ts: now - 46.6, ok: true, parent: "a1", summary: '{"ok": true, "result": {"timeline_count": 4, "timelines": [{"name": "Main Edit v3"}, {"name": "Teaser 30s"}]}}' },
  { type: "text", msg: "s1", ts: now - 46.2, parent: "a1", text: "Teaser 30s uses a 6-frame RGB split at 2 cuts (00:00:04:12 and 00:00:19:03); nothing else matches." },
  { type: "tool_result", tool_id: "a1", ts: now - 46, ok: true, summary: "Teaser 30s uses a 6-frame RGB split at 2 cuts." },
  { type: "tool_start", tool_id: "t4", ts: now - 45.8, name: M + "timeline_frame", input: { frame: 86404 } },
  { type: "tool_result", tool_id: "t4", ts: now - 45, ok: true, summary: '{"ok": true, "result": {"frame": 86404, "timecode": "01:00:12:00", "width": 1920, "height": 1080}}', image: demoFrame },
  {
    type: "text",
    msg: "m2",
    ts: now - 44,
    text:
      "Done. **GlitchIn** now sits on the head of `Interview_A_cam2.mov` (V2), 01:00:11:08 to 01:00:11:20:\n\n- A jittered Transform shakes the frame for the first 6 frames\n- RGB offset fades from full to clean by frame 12\n- A directional blur smears the first 5 frames\n\nFusion read back every key. Want it harder? Say “more offset” or “longer”, and I'll adjust only those keys.",
  },
  { type: "turn_end", ts: now - 44, ok: true },
  { type: "user", id: "u2", ts: now - 20, text: "Nice. Push the zoom on the interview after the glitch, slow, and add a vignette.", context: demoContext, rewind: true, changes: ["Trimmed “Interview_A_cam2.mov” on V2 (tail −8 frames).", "Added red marker “Fix” at 01:00:02:00."] },
  { type: "tool_start", tool_id: "t5", ts: now - 18, name: M + "animate_zoom", input: { item_id: "a91f", preset: "slow_push", start_zoom: 1.0, end_zoom: 1.15, start_frame: 12 } },
  { type: "permission", request: "p2", ts: now - 18, tool: M + "animate_zoom", tool_id: "t5", input: { item_id: "a91f", preset: "slow_push", start_zoom: 1.0, end_zoom: 1.15, start_frame: 12 } },
];

/** Demo results for the "/" file picker and the "$" skill picker. */
export const demoFiles: FileItem[] = [
  { path: "/Volumes/Media/Footage/Interviews/Interview_A_cam2.mov", name: "Interview_A_cam2.mov", kind: "media", detail: "Footage/Interviews · 00:04:12:03", thumb_id: "m1" },
  { path: "/Volumes/Media/Footage/B-roll/Broll_city_night.mov", name: "Broll_city_night.mov", kind: "media", detail: "Footage/B-roll · 00:00:21:10", thumb_id: "m2" },
  { path: "/Users/demo/Desktop/frame_01_00_12_04.png", name: "frame_01_00_12_04.png", kind: "recent", size: 418000, detail: "Attached earlier", thumb_id: "m1" },
  { path: "/Users/demo/Movies/Logo_reveal.png", name: "Logo_reveal.png", kind: "file", size: 96_000, detail: "Movies", thumb_id: "m3" },
  { path: "/Users/demo/Movies/LUTs/Night_Steel.cube", name: "Night_Steel.cube", kind: "file", size: 1_210_000, detail: "Movies/LUTs" },
  { path: "/Users/demo/Downloads/lower_third.setting", name: "lower_third.setting", kind: "file", size: 24_000, detail: "Downloads" },
  { path: "/Users/demo/Documents/Script_v4.pdf", name: "Script_v4.pdf", kind: "file", size: 312_000, detail: "Documents" },
];

export const demoSkills: Skill[] = [
  { name: "resolve-ai-editing", description: "Plan, perform and verify careful edits in Resolve", scope: "bundled" },
  { name: "fusion-glitch", description: "RGB split, shake and smear transitions in Fusion", scope: "user" },
  { name: "lower-thirds", description: "Animated name titles that match the show's style", scope: "user" },
  { name: "subtitle-pass", description: "Transcribe, chunk and style subtitles", scope: "user" },
];

/** Demo answers to "what would rewinding here remove?", one per warning variant. */
export const demoRewind: Record<string, { messages: number; project_edits: number; checkpoint: { id: string; name: string; timeline: string; bin: string } | null }> = {
  u1: { messages: 2, project_edits: 2, checkpoint: { id: "cp1", name: "Main Edit v3 · before AI 14:02", timeline: "Main Edit v3", bin: "AI Checkpoints" } },
  u2: { messages: 1, project_edits: 1, checkpoint: null },
};

/** Demo answers for `POST /api/seek`: known clips and any timecode succeed; anything else fails like the server. */
export async function demoSeek(body: { timecode?: string; clip?: string }) {
  await new Promise((r) => setTimeout(r, 160));
  if (body.timecode) return { ok: true, timecode: body.timecode };
  const known = ["Interview_A_cam2.mov", "Broll_city_night.mov", "Logo_reveal.png"];
  if (body.clip && known.includes(body.clip)) return { ok: true, timecode: "01:00:11:08", clip: body.clip, track: body.clip.startsWith("Interview") ? 2 : 1, matches: 1 };
  throw new Error(`No clip called “${body.clip}” on the current timeline.`);
}

/** Demo timeline notes (markers with comments for the agent). */
export const demoNotes = {
  open: 5,
  done: 1,
  notes: [
    { frame: 86280, timecode: "01:00:07:00", name: "Fix", note: "Cut the um before “so the idea”", color: "Red", status: "open", clips: [{ name: "Interview_A_cam2.mov", track: 2, item_id: "a91f" }] },
    { frame: 86412, timecode: "01:00:12:04", name: "Note", note: "Hold this shot 12 frames longer", color: "Blue", status: "open", clips: [{ name: "Interview_A_cam2.mov", track: 2, item_id: "a91f" }] },
    { frame: 86500, timecode: "01:00:15:16", name: "Note", note: "Too dark, lift the shadows a touch", color: "Blue", status: "open", clips: [{ name: "Broll_city_night.mov", track: 1, item_id: "77c0" }] },
    { frame: 86700, timecode: "01:00:24:00", name: "Title", note: "Lower third for Maya here", color: "Green", status: "open", clips: [] },
    { frame: 86900, timecode: "01:00:32:08", name: "Fix", note: "Music too loud under the interview", color: "Red", status: "open", clips: [{ name: "Interview_A_cam2.mov", track: 2, item_id: "a91f" }] },
    { frame: 86100, timecode: "01:00:00:12", name: "Fix", note: "Trim the slate", color: "Red", status: "done", reply: "Trimmed 12 frames off the head.", clips: [] },
  ],
};

/** Demo context-window usage for the model menu's meter. */
export const demoUsage = { used: 40602, window: 200000, model: "claude-opus" };
