import type { IconName } from "./icons";

/** Human labels for tool calls, in editor language. */

export type Category = "inspect" | "frame" | "audio" | "fusion" | "timeline" | "color" | "transform" | "deliver" | "code" | "agent";

const PREFIX = /^mcp__[^_]+(?:[-_][^_]+)*?__/;

export function bareName(name: string): string {
  if (name.startsWith("mcp__")) {
    const parts = name.split("__");
    return parts[parts.length - 1];
  }
  return name.replace(PREFIX, "");
}

const CATEGORY: Record<string, Category> = {
  resolve_status: "inspect", bridge_capabilities: "inspect", current_context: "inspect", timeline_overview: "inspect",
  project_info: "inspect", list_timelines: "inspect", list_media: "inspect", list_render_presets: "inspect",
  compare_timelines: "inspect", project_health: "inspect", review_silence: "audio", get_clip_transform: "inspect",
  get_clip_grade: "inspect", inspect_fusion: "inspect", fusion_inspect_tool: "inspect", render_status: "deliver",
  timeline_frame: "frame", timeline_audio: "audio", transcribe: "audio", generate_subtitles: "audio",
  find_filler_words: "audio", review_transcript_cuts: "timeline", search_transcript: "audio",
  remove_pauses: "timeline", gaming_cleanup: "timeline", remove_ranges: "timeline", add_markers: "timeline", contact_sheet: "frame",
  detect_shots: "frame", detect_beats: "audio", animated_captions: "timeline", index_footage: "frame", search_footage: "frame", project_overview: "inspect", resolve_api: "code",
  render_check: "deliver", trim_edit: "timeline", slip_clip: "timeline", slide_clip: "timeline", roll_edit: "timeline",
  list_ai_timelines: "inspect", cleanup_ai_timelines: "timeline", match_shot: "frame", visual_diff: "frame",
  styled_title: "fusion", timeline_notes: "inspect", resolve_timeline_note: "timeline",
  Task: "agent", Agent: "agent",
  fusion_build: "fusion", fusion_keyframe_input: "fusion", fusion_apply_setting: "fusion", fusion_run_lua: "fusion",
  animate_zoom: "fusion", apply_blur_effect: "fusion", apply_spotlight_mask: "fusion", keyframe_clip_saturation: "fusion",
  animate_color_fx: "fusion", change_clip_speed: "fusion",
  append_media: "timeline", add_image: "timeline", split_clip: "timeline", add_track: "timeline", set_track_name: "timeline",
  create_timeline: "timeline", open_timeline: "timeline", set_playhead: "timeline", add_marker: "timeline",
  delete_marker: "timeline", insert_title: "timeline", create_compound_clip: "timeline", delete_clips: "timeline",
  apply_silence_cuts: "timeline", preview_timeline: "timeline", import_media: "timeline", open_page: "timeline",
  set_clip_property: "transform", set_clip_enabled: "timeline", set_clip_color: "timeline",
  set_clip_grade: "color", set_clip_transform: "transform",
  render_current_timeline: "deliver", save_project: "deliver",
};

export function category(name: string): Category {
  return CATEGORY[bareName(name)] ?? "code";
}

export function isResolveTool(name: string) {
  return name.startsWith("mcp__");
}

const clipNames = new Map<string, string>();
/** Let labels say "Interview_A.mov" instead of a raw item id. */
export function rememberClips(clips: { id?: string; label?: string; name?: string }[]) {
  for (const c of clips) {
    if (c.name && c.id) clipNames.set(String(c.id), c.name);
    if (c.name && c.label) clipNames.set(String(c.label), c.name);
  }
}

function clip(input: any): string {
  const id = input?.item_id;
  if (!id || id === "playhead") return "this clip";
  const known = clipNames.get(String(id));
  if (known) return known;
  return String(id).length > 14 ? "clip " + String(id).slice(0, 8) + "…" : String(id);
}

function q(s: unknown) {
  return `“${String(s)}”`;
}

/** [present, past] */
export function describe(name: string, input: any = {}): [string, string] {
  const n = bareName(name);
  const c = clip(input);
  switch (n) {
    case "resolve_status": return ["Checking the bridge", "Checked the bridge"];
    case "project_overview": return ["Reading the whole project", "Read the whole project"];
    case "open_timeline": return [`Opening timeline ${input.name ? q(input.name) : ""}`.trim(), `Opened timeline ${input.name ? q(input.name) : ""}`.trim()];
    case "resolve_api": {
      const steps = Array.isArray(input.steps) ? input.steps : [];
      const first = steps[0] ? `${steps[0].on ?? ""}.${steps[0].call ?? ""}` : "the Resolve API";
      const more = steps.length > 1 ? ` +${steps.length - 1}` : "";
      return [`Calling ${first}${more}`, `Called ${first}${more}`];
    }
    case "Task":
    case "Agent": {
      // The subagent glyph already says what this is; the title is the task itself.
      const what = String(input.description || input.subagent_type || "Subagent");
      const t = what.charAt(0).toUpperCase() + what.slice(1);
      return [t, t];
    }
    case "TodoWrite": return ["Updating the plan", "Updated the plan"];
    case "Glob":
    case "Grep": return ["Searching files", "Searched files"];
    case "current_context": return ["Checking the playhead", "Checked the playhead"];
    case "timeline_overview": return ["Reading the timeline", "Read the timeline"];
    case "project_info": return ["Reading project settings", "Read project settings"];
    case "inspect_fusion": return [`Inspecting Fusion on ${c}`, `Inspected Fusion on ${c}`];
    case "fusion_inspect_tool": return [`Reading ${input.tool ?? "node"} inputs`, `Read ${input.tool ?? "node"} inputs`];
    case "timeline_frame": return ["Looking at the frame", "Looked at the frame"];
    case "timeline_audio": return ["Listening to the audio", "Analyzed the audio"];
    case "transcribe": return [`Transcribing ${input.scope === "timeline" ? "the timeline" : c}`, `Transcribed ${input.scope === "timeline" ? "the timeline" : c}`];
    case "generate_subtitles": return ["Writing subtitles", "Wrote subtitles"];
    case "search_transcript": return [`Searching the transcript for ${q(input.query ?? "")}`, `Searched the transcript for ${q(input.query ?? "")}`];
    case "render_check": return ["Rendering a preview to check it", "Checked a preview render"];
    case "trim_edit": return input.dry_run ? ["Planning a trim", "Planned a trim"] : ["Trimming on a copy", "Trimmed on a copy"];
    case "slip_clip": return input.dry_run ? ["Planning a slip", "Planned a slip"] : ["Slipping a clip on a copy", "Slipped a clip on a copy"];
    case "slide_clip": return input.dry_run ? ["Planning a slide", "Planned a slide"] : ["Sliding a clip on a copy", "Slid a clip on a copy"];
    case "roll_edit": return input.dry_run ? ["Planning a roll", "Planned a roll"] : ["Rolling a cut on a copy", "Rolled a cut on a copy"];
    case "list_ai_timelines": return ["Listing the AI's timelines", "Listed the AI's timelines"];
    case "cleanup_ai_timelines": return input.action === "delete" ? ["Deleting AI timelines", "Deleted AI timelines"] : ["Archiving AI timelines", "Archived AI timelines"];
    case "match_shot": return input.apply ? ["Matching the shot's colour", "Matched the shot's colour"] : ["Measuring both shots' colour", "Measured both shots' colour"];
    case "visual_diff": return ["Comparing the two versions", "Compared the two versions"];
    case "styled_title": return input.dry_run ? ["Planning a title", "Planned a title"] : [`Adding the title ${q(input.text ?? "")}`, `Added the title ${q(input.text ?? "")}`];
    case "timeline_notes": return ["Reading your timeline notes", "Read your timeline notes"];
    case "resolve_timeline_note": return input.status === "skipped" ? ["Marking a note skipped", "Marked a note skipped"] : ["Marking a note done", "Marked a note done"];
    case "gaming_cleanup": return input.apply ? ["Cutting dead air on a copy", "Cut dead air on a copy"] : ["Finding dead air", "Found dead air"];
    case "remove_pauses": return input.apply ? ["Cutting pauses on a copy", "Cut pauses on a copy"] : ["Finding pauses", "Found pauses"];
    case "remove_ranges": return ["Cutting ranges on a copy of the timeline", "Cut ranges on a copy of the timeline"];
    case "add_markers": {
      const n = Array.isArray(input.markers) ? input.markers.length : 0;
      const what = n === 1 ? "a marker" : n ? `${n} markers` : "markers";
      return [`Adding ${what}`, `Added ${what}`];
    }
    case "contact_sheet": return [`Looking through ${input.scope === "media_pool" ? "the media pool" : "the footage"}`, `Looked through ${input.scope === "media_pool" ? "the media pool" : "the footage"}`];
    case "detect_shots": return ["Finding shot changes", "Found shot changes"];
    case "index_footage": return ["Indexing what’s on screen", "Indexed what’s on screen"];
    case "search_footage": {
      const what = input.text ? q(input.text) : input.facing_camera ? "shots facing the camera" : Array.isArray(input.labels) && input.labels.length ? input.labels.join(", ") : "the footage";
      return [`Looking for ${what}`, `Looked for ${what}`];
    }
    case "detect_beats": return ["Finding the beat", "Found the beat"];
    case "animated_captions": return [`Rendering ${input.style ?? "pop"} captions`, `Rendered ${input.style ?? "pop"} captions`];
    case "find_filler_words": return ["Finding filler words", "Found filler words"];
    case "review_transcript_cuts": return ["Marking cuts from the transcript", "Marked cuts for review"];
    case "review_silence": return ["Finding silences", "Marked silences for review"];
    case "apply_silence_cuts": return ["Cutting on a copy of the timeline", "Cut on a copy of the timeline"];
    case "fusion_build": return [`Building ${input.name ? q(input.name) : "a Fusion chain"} on ${c}`, `Built ${input.name ? q(input.name) : "a Fusion chain"} on ${c}`];
    case "fusion_keyframe_input": {
      const t = `${input.tool ?? "node"}.${input.input ?? "input"}`;
      const k = Array.isArray(input.keyframes) ? input.keyframes.length : 0;
      return k ? [`Keyframing ${t}`, `Set ${k} keys on ${t}`] : [`Setting ${t}`, `Set ${t}`];
    }
    case "fusion_apply_setting": return [`Pasting a Fusion setup on ${c}`, `Pasted a Fusion setup on ${c}`];
    case "fusion_run_lua": return [`Running a Fusion script on ${c}`, `Ran a Fusion script on ${c}`];
    case "animate_zoom": return [`Animating a zoom on ${c}`, `Animated a zoom on ${c}`];
    case "apply_blur_effect": return [`Adding blur to ${c}`, `Added blur to ${c}`];
    case "apply_spotlight_mask": return [`Adding a spotlight to ${c}`, `Added a spotlight to ${c}`];
    case "keyframe_clip_saturation": return [`Keyframing saturation on ${c}`, `Keyframed saturation on ${c}`];
    case "animate_color_fx": return [`Animating color on ${c}`, `Animated color on ${c}`];
    case "change_clip_speed": return [`Changing speed of ${c}`, `Changed speed of ${c}`];
    case "set_clip_transform": return [`Transforming ${c}`, `Transformed ${c}`];
    case "set_clip_grade": return [`Grading ${c}`, `Graded ${c}`];
    case "split_clip": return [`Splitting ${c}`, `Split ${c}`];
    case "add_image": return ["Placing an image", "Placed an image"];
    case "append_media": return ["Adding media to the timeline", "Added media to the timeline"];
    case "insert_title": return ["Adding a title", "Added a title"];
    case "add_marker": return ["Adding a marker", "Added a marker"];
    case "preview_timeline": return ["Duplicating the timeline", "Working on a copy"];
    case "compare_timelines": return ["Comparing timelines", "Compared timelines"];
    case "delete_clips": return ["Deleting clips", "Deleted clips"];
    case "render_current_timeline": return ["Queueing a render", "Queued a render"];
    case "save_project": return ["Saving the project", "Saved the project"];
    case "Bash": return ["Running a command", "Ran a command"];
    case "Read": return ["Reading a file", "Read a file"];
    case "Write": return ["Writing a file", "Wrote a file"];
    case "Edit": return ["Editing a file", "Edited a file"];
    case "WebSearch": return ["Searching the web", "Searched the web"];
    case "WebFetch": return ["Reading a page", "Read a page"];
    default: {
      const words = n.replace(/_/g, " ");
      return [words.charAt(0).toUpperCase() + words.slice(1), words.charAt(0).toUpperCase() + words.slice(1)];
    }
  }
}

/** A short detail line for approval cards and step rows. */
export function detail(name: string, input: any = {}): string | null {
  const n = bareName(name);
  if (n === "resolve_api" && Array.isArray(input.steps))
    return input.steps.map((st: any) => `${st.on}.${st.call}(${(st.args ?? []).map((a: any) => JSON.stringify(a)).join(", ")})`).join("  →  ");
  if ((n === "Task" || n === "Agent") && input.prompt) return String(input.prompt).split("\n")[0];
  if ((n === "Grep" || n === "Glob") && input.pattern) return String(input.pattern);
  if ((n === "WebSearch" || n === "WebFetch") && (input.query || input.url)) return String(input.query || input.url);
  if (n === "Bash") return input.command ?? null;
  if (["Write", "Edit", "Read", "NotebookEdit"].includes(n) && input.file_path) return String(input.file_path).split("/").slice(-2).join("/");
  if (n === "fusion_build" && Array.isArray(input.nodes)) return input.nodes.map((x: any) => x.type).join(" → ");
  if (n === "animate_zoom") return [input.preset, input.start_zoom && `${input.start_zoom}×`, input.end_zoom && `→ ${input.end_zoom}×`].filter(Boolean).join(" ") || null;
  if (n === "fusion_run_lua") return String(input.code ?? input.lua ?? "").split("\n")[0] || null;
  if (n === "fusion_keyframe_input" && Array.isArray(input.keyframes)) return input.keyframes.map((k: any) => `${k.frame ?? k.timeline_frame}f`).join(" · ");
  return argSummary(input);
}

/** Compact key=value summary of a tool's arguments. */
export function argSummary(input: any): string | null {
  if (!input || typeof input !== "object") return null;
  const parts: string[] = [];
  for (const [k, v] of Object.entries(input)) {
    // The clip is already named in the label, so its id is noise here.
    if (v == null || v === "" || k === "item_id") continue;
    const val = typeof v === "object" ? (Array.isArray(v) ? `[${v.length}]` : "{…}") : String(v);
    parts.push(`${k}=${val.length > 28 ? val.slice(0, 27) + "…" : val}`);
    if (parts.length >= 4) break;
  }
  return parts.length ? parts.join("  ") : null;
}

// ------------------------------------------------------------- Fusion graph

export type NodeChip = { name: string; type: string; animated: string[]; keys: number[] };

export function nodeFamily(type: string): string {
  const t = type.toLowerCase();
  if (/media(in|out)|loader|saver/.test(t)) return "io";
  if (/transform|dvetransform|letterbox|corner|camera|perspective|resize|crop|scale/.test(t)) return "xf";
  if (/merge|dissolve|multimerge|channelbool/.test(t)) return "merge";
  if (/mask|polygon|bspline|ellipse|rectangle|wand|ranges/.test(t)) return "mask";
  if (/text|title/.test(t)) return "text";
  if (/background|noise|fastnoise|plasma|day|gradient|shader/.test(t)) return "gen";
  if (/color|bright|gamut|hue|curve|gain|saturat|lut/.test(t)) return "color";
  return "filter";
}

/** Read a fusion_build call's nodes into chips with their keyframe frames. */
export function graphFromBuild(input: any): { chips: NodeChip[]; span: number } | null {
  if (!Array.isArray(input?.nodes) || input.nodes.length === 0) return null;
  const byName = new Map<string, NodeChip>();
  let span = 0;
  for (const node of input.nodes) {
    const chip: NodeChip = { name: String(node.name ?? node.type), type: String(node.type ?? "Tool"), animated: [], keys: [] };
    for (const [key, value] of Object.entries(node.inputs ?? {})) {
      const keys = (value as any)?.keyframes;
      if (Array.isArray(keys)) {
        chip.animated.push(key);
        for (const k of keys) {
          const f = Number(k.frame ?? k.timeline_frame ?? 0);
          chip.keys.push(f);
          span = Math.max(span, f);
        }
      }
    }
    chip.keys = Array.from(new Set(chip.keys)).sort((a, b) => a - b);
    byName.set(chip.name, chip);
  }
  const chain = Array.isArray(input.chain) ? input.chain.map((c: any) => (typeof c === "string" ? c : String(c?.node ?? ""))) : [];
  const order: string[] = chain.length ? chain : Array.from(byName.keys());
  const chips = order.map((n) => byName.get(n)).filter(Boolean) as NodeChip[];
  for (const [n, chip] of byName) if (!order.includes(n)) chips.push(chip);
  return { chips, span };
}

export function graphFromKeyframe(input: any): { chips: NodeChip[]; span: number } | null {
  if (!input?.tool) return null;
  const keys = Array.isArray(input.keyframes) ? input.keyframes.map((k: any) => Number(k.frame ?? k.timeline_frame ?? 0)) : [];
  const chip: NodeChip = { name: String(input.tool), type: String(input.tool).replace(/\d+$/, ""), animated: [String(input.input ?? "")], keys };
  return { chips: [chip], span: Math.max(0, ...keys) };
}

const pct = (v: unknown) => (typeof v === "number" ? `${Math.round(v * 100)}%` : null);
const frames = (a: unknown, b: unknown) =>
  a == null && b == null ? null : `frames ${a ?? 0}–${b ?? "end"}`;

// ------------------------------------------------------------- approval sentences

/** Where an edit lands: the open timeline, and whether the editor asked for edits on a copy (Access › Work on a copy). */
export type Place = { timeline?: string; copy?: boolean };

/** 0.6 → "0.6", 2 → "2", 0.15000001 → "0.15". */
const num = (v: unknown) => (typeof v === "number" ? String(Math.round(v * 100) / 100) : String(v));
const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const signed = (n: unknown) => (typeof n === "number" && n > 0 ? `+${n}` : String(n ?? 0));
const quoted = (s: unknown, max = 40) => {
  const t = String(s ?? "").replace(/\s+/g, " ").trim();
  return `“${t.length > max ? t.slice(0, max - 1) + "…" : t}”`;
};
const nameOf = (s: unknown) => (s ? ` as ${quoted(s)}` : "");
/** "on “Main”", "on a copy of “Main”"; `copy` forces the copy (tools that always duplicate the timeline). */
function where(place: Place | undefined, copy = false): string {
  const tl = place?.timeline ? `“${place.timeline}”` : "the timeline";
  return copy || place?.copy ? `on a copy of ${tl}` : place?.timeline ? `on ${tl}` : "";
}
const join = (...parts: (string | null | undefined | false)[]) => parts.filter(Boolean).join(" ");

/** One Resolve API step as the editor would read it: `timeline.SetSetting(“timelineFrameRate”)`. */
function apiCall(st: any): string {
  const args = Array.isArray(st?.args) ? st.args : [];
  const first = args.length ? (typeof args[0] === "string" ? quoted(args[0], 24) : typeof args[0] === "number" ? String(args[0]) : "…") : "";
  return `${st?.on ?? "resolve"}.${st?.call ?? "?"}(${first}${args.length > 1 ? ", …" : ""})`;
}

/** A tool's arguments as words when there's no phrasing for it: "min pause 0.6 · apply yes". */
export function readableArgs(input: any): string | null {
  if (!input || typeof input !== "object") return null;
  const parts: string[] = [];
  for (const [k, v] of Object.entries(input)) {
    if (v == null || v === "" || k === "item_id" || k === "user_approved") continue;
    const key = k.replace(/_/g, " ");
    const val =
      typeof v === "boolean" ? (v ? "yes" : "no") : typeof v === "number" ? num(v) : typeof v === "string" ? (v.length > 32 ? v.slice(0, 31) + "…" : v) : Array.isArray(v) ? plural(v.length, "item") : "…";
    parts.push(`${key} ${val}`);
    if (parts.length >= 4) break;
  }
  return parts.length ? parts.join(" · ") : null;
}

/**
 * What an edit will do, as one sentence in the editor's words, with where it lands when `place` is given: "Cut pauses
 * longer than 0.6 s on a copy of “Main”". `placed` says whether the sentence already names the place, so the approval
 * doesn't repeat it. Unknown tools fall back to their arguments as words.
 */
export function approvalSentence(name: string, input: any = {}, place?: Place): { text: string | null; placed: boolean } {
  const n = bareName(name);
  const c = clip(input);
  const at = (copy = false) => where(place, copy);
  const done = (text: string | null, placed = true) => ({ text: text ? text.replace(/\s+/g, " ").trim() : null, placed: placed && Boolean(place?.timeline || place?.copy) });
  switch (n) {
    case "remove_pauses": {
      const min = input.min_pause_seconds ?? input.min_pause;
      const keep = input.keep_seconds;
      const scope = input.scope === "clip" || input.item_id ? `in ${c}` : "";
      return input.apply
        ? done(join(`Cut pauses${min != null ? ` longer than ${num(min)} s` : ""}`, scope, keep != null ? `(keeping ${num(keep)} s of air)` : "", at(true)))
        : done(join(`Find pauses${min != null ? ` longer than ${num(min)} s` : ""}`, scope, input.mark === false ? "" : "and mark them", at()));
    }
    case "apply_silence_cuts": {
      const k = Array.isArray(input.marker_ids) ? input.marker_ids.length : 0;
      return done(join(`Cut ${k ? `the ${plural(k, "marked silence")}` : "the marked silences"}`, at(true), nameOf(input.name)));
    }
    case "gaming_cleanup":
      return input.apply ? done(join("Cut dead air", input.preset && `for ${String(input.preset)}`, at(true))) : done(join("Find dead air and mark it", at()));
    case "remove_ranges": {
      const r = Array.isArray(input.ranges) ? input.ranges : [];
      const total = r.reduce((t: number, x: any) => t + Math.max(0, Number(x?.end_frame ?? 0) - Number(x?.start_frame ?? 0)), 0);
      return done(join(`Cut ${r.length ? plural(r.length, "range") : "ranges"}${total ? ` (${plural(total, "frame")})` : ""} out of every track`, at(true), nameOf(input.name)));
    }
    case "trim_edit":
      return done(join(`Trim the ${input.edge === "head" ? "head" : "tail"} of ${c} by ${signed(input.frames)} frames`, input.ripple === false ? "without closing the gap" : "", at(true)));
    case "slip_clip":
      return done(join(`Slip ${c} by ${signed(input.frames)} frames`, at(true)));
    case "slide_clip":
      return done(join(`Slide ${c} by ${signed(input.frames)} frames`, at(true)));
    case "roll_edit": {
      const l = clip({ item_id: input.left_item_id });
      const r = clip({ item_id: input.right_item_id });
      return done(join(`Roll the cut between ${l} and ${r} by ${signed(input.frames)} frames`, at(true)));
    }
    case "split_clip":
      return done(join(`Split ${c}`, input.timecode ? `at ${input.timecode}` : input.frame != null ? `at frame ${input.frame}` : "at the playhead", at()));
    case "delete_clips": {
      const k = Array.isArray(input.item_ids) ? input.item_ids.length : 0;
      return done(join(`Delete ${k === 1 ? clip({ item_id: input.item_ids[0] }) : k ? plural(k, "clip") : "the clips"}`, input.ripple ? "and close the gaps" : "", at()));
    }
    case "animate_zoom": {
      const z = [pct(input.start_zoom), pct(input.end_zoom)].filter(Boolean).join(" → ");
      return done(join(z ? `Zoom ${c} ${z}` : `Animate a zoom on ${c}`, frames(input.start_frame, input.end_frame) && `over ${frames(input.start_frame, input.end_frame)}`, input.preset && `(${String(input.preset).replace(/_/g, " ")})`, at()));
    }
    case "set_clip_transform": {
      const ch: string[] = [];
      if (typeof input.zoom === "number") ch.push(`zoom ${pct(input.zoom)}`);
      if (typeof input.zoom_x === "number" || typeof input.zoom_y === "number") ch.push(`zoom ${pct(input.zoom_x ?? 1)} × ${pct(input.zoom_y ?? 1)}`);
      if (input.pan != null || input.pan_percent != null) ch.push(input.pan_percent != null ? `pan ${num(input.pan_percent)}%` : `pan ${num(input.pan)} px`);
      if (input.tilt != null || input.tilt_percent != null) ch.push(input.tilt_percent != null ? `tilt ${num(input.tilt_percent)}%` : `tilt ${num(input.tilt)} px`);
      if (input.rotation != null) ch.push(`rotate ${num(input.rotation)}°`);
      if (input.opacity != null) ch.push(`opacity ${num(input.opacity)}%`);
      if (input.flip_x) ch.push("flip horizontally");
      if (input.composite_mode) ch.push(`${String(input.composite_mode).toLowerCase()} blend`);
      return done(join(`Set ${c}’s ${ch.length ? ch.join(", ") : "transform"}`, at()));
    }
    case "change_clip_speed": {
      const sp = input.speed_percent ?? (typeof input.speed === "number" ? input.speed * 100 : null);
      return done(join(input.reverse ? `Reverse ${c}` : sp != null ? `Play ${c} at ${num(sp)}%` : input.slow_down_percent != null ? `Slow ${c} by ${num(input.slow_down_percent)}%` : input.speed_up_percent != null ? `Speed ${c} up by ${num(input.speed_up_percent)}%` : `Change ${c}’s speed`, at()));
    }
    case "set_clip_grade":
      return done(join(input.reset ? `Reset the grade on ${c}` : `Grade ${c}`, typeof input.saturation === "number" && `(saturation ${num(input.saturation)})`, at()));
    case "apply_blur_effect":
      return done(join(`Blur ${input.width < 1 || input.height < 1 ? "part of " : ""}${c}`, input.blur_size != null && `(${num(input.blur_size)})`, input.animate ? "and animate it" : "", at()));
    case "fusion_build": {
      if (!Array.isArray(input.nodes)) return done(join(`Build a Fusion chain on ${c}`, at()));
      const keyed = input.nodes.filter((x: any) => x.inputs && Object.values(x.inputs).some((v: any) => v && Array.isArray(v.keyframes))).length;
      return done(join(`Build ${input.name ? `“${input.name}”` : "a Fusion chain"} on ${c}: ${plural(input.nodes.length, "node")}${keyed ? `, ${keyed} animated` : ""}`, at()));
    }
    case "fusion_keyframe_input":
      return done(join(`Set ${Array.isArray(input.keyframes) ? plural(input.keyframes.length, "keyframe") : "keyframes"}${input.input ? ` on ${input.tool ? `${input.tool} ` : ""}${input.input}` : ""}`, `on ${c}`, at()));
    case "fusion_apply_setting":
      return done(join(`Apply a Fusion setting to ${c}`, at()));
    case "fusion_run_lua":
      return done(join(`Run a Fusion script on ${c}`, at()));
    case "add_markers": {
      const m = Array.isArray(input.markers) ? input.markers : [];
      const label = m.length === 1 ? (m[0]?.name || m[0]?.note) : null;
      return done(join(m.length === 1 ? `Add a marker${label ? ` ${quoted(label)}` : ""}${m[0]?.frame != null ? ` at frame ${m[0].frame}` : ""}` : `Add ${m.length ? plural(m.length, "marker") : "markers"}`, at()));
    }
    case "add_marker":
      return done(join(`Add a ${String(input.color ?? "").toLowerCase() || ""} marker ${quoted(input.name || input.note || "")}`.replace("  ", " "), input.frame != null ? `at frame ${input.frame}` : "at the playhead", at()));
    case "insert_title":
      return done(join(`Add a ${input.title_name ?? "Text+"} title${input.text ? ` reading ${quoted(input.text)}` : ""} at the playhead`, at()));
    case "styled_title": {
      const pos = String(input.position ?? "lower_third").replace(/_/g, " ");
      return done(join(`Add the title ${quoted(input.text)} as a ${pos}`, input.duration_frames ? `for ${plural(input.duration_frames, "frame")}` : "", input.start_frame != null ? `from frame ${input.start_frame}` : "", at()));
    }
    case "generate_subtitles":
      return done(join(`Write subtitles for ${input.scope === "clip" || input.item_id ? c : "the timeline"}`, input.import_to_timeline === false ? "(file only)" : "and place them", at()), input.import_to_timeline !== false);
    case "animated_captions":
      return done(join(`Add word-by-word captions${input.style ? ` in the “${input.style}” style` : ""}`, input.scope === "clip" || input.item_id ? `to ${c}` : "", at()));
    case "add_image":
      return done(join(`Add ${String(input.path ?? "an image").split("/").pop()}`, input.duration_seconds ? `for ${num(input.duration_seconds)} s` : "", input.at_playhead ? "at the playhead" : "", at()));
    case "append_media": {
      const k = (Array.isArray(input.media_ids) ? input.media_ids.length : 0) + (Array.isArray(input.paths) ? input.paths.length : 0);
      return done(join(`Add ${k ? plural(k, "clip") : "media"} to the end of`, place?.timeline ? (place.copy ? `a copy of “${place.timeline}”` : `“${place.timeline}”`) : "the timeline"));
    }
    case "import_media":
      return done(`Import ${Array.isArray(input.paths) ? plural(input.paths.length, "file") : "files"} into the media pool`, false);
    case "create_timeline":
      return done(`Create a timeline called ${quoted(input.name)}`, false);
    case "open_timeline":
      return done(`Open the timeline ${input.name ? quoted(input.name) : ""}`, false);
    case "render_current_timeline": {
      const folder = String(input.output_dir ?? "").split("/").filter(Boolean).pop();
      return done(join(`${input.start ? "Render" : "Queue a render of"} ${place?.timeline ? `“${place.timeline}”` : "the timeline"}`, folder && `to ${folder}`, input.preset && `with ${quoted(input.preset, 28)}`), false);
    }
    case "render_check":
      return done("Render a short preview to check the edit", false);
    case "save_project":
      return done("Save the project", false);
    case "cleanup_ai_timelines":
      return done(input.action === "delete" ? "Delete the AI’s scratch timelines" : "Move the AI’s scratch timelines into a bin", false);
    case "resolve_api": {
      const steps = Array.isArray(input.steps) ? input.steps : [];
      if (!steps.length) return done("Call Resolve’s scripting API", false);
      return done(`Call ${apiCall(steps[0])}${steps.length > 1 ? ` and ${plural(steps.length - 1, "more call")}` : ""} in Resolve’s API`, false);
    }
    case "Bash":
      return done(input.command ? `Run ${quoted(input.command, 60)}` : "Run a shell command", false);
    case "Write":
    case "Edit":
    case "NotebookEdit":
      return done(`${n === "Write" ? "Write" : "Edit"} ${String(input.file_path ?? "a file").split("/").slice(-2).join("/")}`, false);
  }
  return done(readableArgs(input), false);
}

/** The approval sentence without a place (step details, labels). */
export function approvalWhat(name: string, input: any = {}): string | null {
  return approvalSentence(name, input).text;
}

/** The scope "Always allow" grants, as a plural noun ("zooms", "Fusion builds"). */
export function allowScope(name: string): string {
  const n = bareName(name);
  const nouns: Record<string, string> = {
    animate_zoom: "zooms",
    fusion_build: "Fusion builds",
    fusion_keyframe_input: "keyframe edits",
    apply_blur_effect: "blurs",
    remove_pauses: "pause cuts",
    apply_silence_cuts: "silence cuts",
    remove_ranges: "range cuts",
    trim_edit: "trims",
    add_markers: "markers",
    add_marker: "markers",
    add_subtitles: "subtitle edits",
    Bash: "shell commands",
    Write: "file writes",
    Edit: "file edits",
  };
  return nouns[n] ?? `“${describe(name)[1].replace(/ on .*$/, "").toLowerCase()}” steps`;
}

/** Claude Code's own bookkeeping (tool discovery, to-do lists, plan mode): not edits, so not shown as steps. */
const INTERNAL = new Set([
  "ToolSearch", "TodoWrite", "TodoRead", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
  "ExitPlanMode", "EnterPlanMode", "ListMcpResourcesTool", "ReadMcpResourceTool",
]);
export function isInternalTool(name: string): boolean {
  return INTERNAL.has(bareName(name));
}

/**
 * The glyph a step shows: what it does, at a glance. Every tool this file knows has an entry; anything else falls
 * back by category (and unknown tools to a terminal).
 */
const GLYPH: Record<string, IconName> = {
  // cuts and trims
  remove_ranges: "Scissors", apply_silence_cuts: "Scissors", trim_edit: "Scissors", remove_pauses: "Scissors",
  split_clip: "Scissors", roll_edit: "Scissors", slip_clip: "Scissors", slide_clip: "Scissors", gaming_cleanup: "Scissors",
  review_transcript_cuts: "Scissors",
  delete_clips: "Trash", cleanup_ai_timelines: "Trash", delete_marker: "Trash",
  // sound and words
  transcribe: "Wave", timeline_audio: "Wave", detect_beats: "Wave", review_silence: "Wave", find_filler_words: "Wave",
  // searching
  search_transcript: "Search", search_footage: "Search", index_footage: "Search", Grep: "Search", Glob: "Search",
  WebSearch: "Search", WebFetch: "Search",
  // looking
  timeline_frame: "Frame", contact_sheet: "Frame", detect_shots: "Frame", open_page: "Frame",
  visual_diff: "Eye", inspect_fusion: "Eye", fusion_inspect_tool: "Eye", get_clip_transform: "Eye", get_clip_grade: "Eye",
  // words on screen
  insert_title: "Type", styled_title: "Type", generate_subtitles: "Type", animated_captions: "Type",
  // Fusion
  fusion_build: "Node", fusion_keyframe_input: "Node", fusion_apply_setting: "Node", apply_blur_effect: "Node",
  apply_spotlight_mask: "Node",
  // colour
  set_clip_grade: "Drop", keyframe_clip_saturation: "Drop", animate_color_fx: "Drop", match_shot: "Drop", set_clip_color: "Drop",
  // movement and speed
  animate_zoom: "Move", set_clip_transform: "Move", set_clip_property: "Move", change_clip_speed: "Gauge",
  // markers and notes
  add_marker: "Flag", add_markers: "Flag", timeline_notes: "Flag", resolve_timeline_note: "Flag",
  // timelines
  create_timeline: "Film", open_timeline: "Film", list_timelines: "Film", compare_timelines: "Film", list_ai_timelines: "Film",
  timeline_overview: "Film", append_media: "Film", set_clip_enabled: "Film",
  create_compound_clip: "Layers", add_track: "Layers", set_track_name: "Layers",
  set_playhead: "Playhead", current_context: "Playhead", preview_timeline: "Play",
  // media
  import_media: "Import", add_image: "Image", list_media: "List",
  // project and bridge
  resolve_status: "Pulse", bridge_capabilities: "Pulse", project_info: "List", project_overview: "List", project_health: "Pulse",
  save_project: "Save",
  // delivery
  render_current_timeline: "Export", render_check: "Export", render_status: "Export", list_render_presets: "Export",
  // code and files
  resolve_api: "Terminal", fusion_run_lua: "Terminal", Bash: "Terminal", Read: "File", Edit: "File", Write: "File",
  // agents
  Task: "Agents", Agent: "Agents",
};
const BY_CATEGORY: Record<Category, IconName> = {
  inspect: "Eye", frame: "Frame", audio: "Wave", fusion: "Node", timeline: "Film", color: "Drop",
  transform: "Move", deliver: "Export", code: "Terminal", agent: "Agents",
};
export function toolGlyph(name: string): IconName {
  return GLYPH[bareName(name)] ?? BY_CATEGORY[category(name)];
}
