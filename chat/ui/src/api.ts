export type ChatEvent = {
  type: string;
  thread?: string;
  ts?: number;
  [key: string]: any;
};

export type Thread = {
  id: string;
  title: string;
  agent: string;
  created: number;
  updated: number;
  running: boolean;
  messages: number;
};

export type ThreadDetail = {
  id: string;
  title: string;
  agent: string;
  session: string | null;
  events: ChatEvent[];
  running: boolean;
  context_usage?: ContextUsage | null;
};

export type Settings = {
  agent: string;
  model: string;
  effort: string;
  mode: "ask" | "auto";
  safe_copy: boolean;
  attach_context: boolean;
  always_allow: string[];
  /** Overrides for keyboard shortcuts: action → combo ("Meta+Enter"). Missing actions use the defaults. */
  shortcuts?: Record<string, string>;
  /** Save a copy of the timeline before the AI's first edit after each message (default on), so Rewind can restore it. */
  checkpoints?: boolean;
  /** Tell the agent what the editor changed in Resolve by hand since its last reply (default on). */
  track_changes?: boolean;
};

/** Where `POST /api/seek` put the playhead. */
export type SeekResult = { ok: boolean; timecode?: string; clip?: string; track?: number | string; matches?: number };
/** A timeline marker with a note for the agent. */
export type TimelineNote = {
  frame: number;
  timecode: string;
  duration?: number;
  end_frame?: number;
  name?: string;
  note: string;
  color?: string;
  status: "open" | "done" | "skipped";
  reply?: string;
  clips?: { name: string; track?: number | string; item_id?: string }[];
};
export type NotesResult = { notes: TimelineNote[]; open: number; done: number };

/** A saved copy of the timeline from before the AI's first edit after a message. */
export type Checkpoint = { id: string; name?: string; timeline?: string; bin?: string };
/** What rewinding to a message would remove, asked before confirming. */
export type RewindPreview = { messages: number; project_edits: number; checkpoint: Checkpoint | null; checkpoints_enabled?: boolean };
export type RewindResult = {
  text?: string;
  attachments?: Attachment[];
  refs?: MediaRef[];
  restored?: { restored?: { name?: string } } | null;
  project_restored?: boolean;
};

/** A "/" picker result: a media pool clip, a recent attachment, or a file on disk. */
export type FileItem = { path: string; name: string; kind: "media" | "recent" | "file"; size?: number; detail?: string; thumb_id?: string };
/** A "$" picker result: a Claude Code skill the agent can be asked to use. */
export type Skill = { name: string; description?: string; scope?: "user" | "bundled"; path?: string };

/** `thumb_id`: a video or image file's poster at thumbUrl(thumb_id), when the server can render one. */
export type Attachment = { id: string; name: string; type: string; size: number; path?: string; preview?: string; thumb_id?: string };
export type MediaRef = {
  id?: string;
  name?: string;
  bin?: string;
  file_path?: string;
  type?: string;
  duration?: string;
  /** The server can render a poster frame for this clip at thumbUrl(id). */
  thumb?: boolean;
  /** Demo only: an inline still standing in for the poster frame. */
  poster?: string;
};

/** `compact`: the agent can summarize its own context (Claude can; Codex can't). */
export type Agent = { id: string; label: string; available: boolean; approvals: boolean; path: string | null; compact?: boolean };
/** How full the agent's context window is; `window` may be unknown (null). */
export type ContextUsage = { used: number; window?: number | null; model?: string | null };

export type ClipRef = {
  id?: string;
  label?: string;
  name?: string;
  track?: number | string;
  start?: number;
  end?: number;
  has_fusion?: boolean;
};

export type Context = {
  connected: boolean;
  state?: string;
  detail?: string;
  project?: string | { name?: string };
  timeline?: string | { name?: string; id?: string };
  page?: string;
  /** Timeline markers carrying a note the agent hasn't done yet. */
  open_notes?: number;
  playhead?: { frame?: number; timecode?: string } | string;
  fps?: number;
  resolution?: unknown;
  under_playhead?: ClipRef[];
  items_under_playhead?: ClipRef[];
  clips_under_playhead?: ClipRef[];
  [key: string]: any;
};

export type AppState = {
  threads: Thread[];
  settings: Settings;
  agents: Agent[];
  context: Context;
  version: string | null;
};

const hash = new URLSearchParams(location.hash.replace(/^#/, ""));
export const DEMO = new URLSearchParams(location.search).has("demo");
const token = hash.get("t") || sessionStorage.getItem("chat-token") || "";
if (hash.get("t")) {
  sessionStorage.setItem("chat-token", hash.get("t")!);
  history.replaceState(null, "", location.pathname + location.search);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch("/api/" + path, {
    method,
    headers: { "X-Chat-Token": token, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data as T;
}

/** A media pool clip's poster frame (PNG rendered by the server from the clip's file). */
export function thumbUrl(id: string) {
  return `/api/thumb?id=${encodeURIComponent(id)}&t=${encodeURIComponent(token)}`;
}

export const api = {
  state: () => request<AppState>("GET", "state"),
  thread: (id: string) => request<ThreadDetail>("GET", `threads/${id}`),
  newThread: (agent?: string) => request<ThreadDetail>("POST", "threads", { agent }),
  deleteThread: (id: string) => request("DELETE", `threads/${id}`),
  send: (id: string, text: string, attachments: string[] = [], refs: MediaRef[] = []) =>
    request("POST", `threads/${id}/messages`, { text, attachments, refs }),
  upload: async (file: File) => {
    const buf = new Uint8Array(await file.arrayBuffer());
    let bin = "";
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    return request<Attachment>("POST", "attachments", { name: file.name || "pasted.png", type: file.type, data: btoa(bin) });
  },
  frame: () => request<Attachment>("POST", "attachments/frame", {}),
  media: () => request<{ items: MediaRef[]; count: number }>("GET", "media"),
  interrupt: (id: string) => request("POST", `threads/${id}/interrupt`, {}),
  decide: (id: string, req: string, decision: "allow" | "deny" | "always") =>
    request("POST", `threads/${id}/permissions/${encodeURIComponent(req)}`, { decision }),
  settings: (patch: Partial<Settings> & { clear_always_allow?: boolean }) => request<Settings>("POST", "settings", patch),
  refreshContext: () => request("POST", "context", {}),
  files: (q: string) => request<{ items: FileItem[] }>("GET", `files?q=${encodeURIComponent(q)}`),
  attachPath: (path: string) => request<Attachment>("POST", "attachments/path", { path }),
  skills: () => request<{ items: Skill[] }>("GET", "skills"),
  seek: (body: { timecode?: string; clip?: string }) => request<SeekResult>("POST", "seek", body),
  notes: () => request<NotesResult>("GET", "notes"),
  compact: (id: string, instructions?: string) => request<{ ok?: boolean }>("POST", `threads/${id}/compact`, instructions ? { instructions } : {}),
  rewindPreview: (id: string, message: string) => request<RewindPreview>("POST", `threads/${id}/rewind`, { message, preview: true }),
  rewind: (id: string, message: string, restore_project: boolean) => request<RewindResult>("POST", `threads/${id}/rewind`, { message, restore_project }),
};

export function subscribe(onEvent: (e: ChatEvent) => void, onStatus: (online: boolean) => void): () => void {
  const source = new EventSource(`/api/events?t=${encodeURIComponent(token)}`);
  source.onopen = () => onStatus(true);
  source.onerror = () => onStatus(false);
  source.onmessage = (m) => {
    try {
      onEvent(JSON.parse(m.data));
    } catch {
      /* ignore malformed */
    }
  };
  return () => source.close();
}

/** Messages to the native macOS shell, when hosted in it. */
export function shell(message: Record<string, unknown>) {
  const handler = (window as any).webkit?.messageHandlers?.shell;
  if (handler) handler.postMessage(message);
}
export const IN_SHELL = Boolean((window as any).webkit?.messageHandlers?.shell);

/** Fold an event into a thread's event list the same way the server persists it. */
export function foldEvent(events: ChatEvent[], e: ChatEvent): ChatEvent[] {
  if (e.type === "text_delta") {
    const last = events[events.length - 1];
    if (last && last.type === "text" && last.msg === e.msg) {
      const next = events.slice(0, -1);
      next.push({ ...last, text: last.text + e.text });
      return next;
    }
    return [...events, { type: "text", msg: e.msg, text: e.text, ts: e.ts }];
  }
  if (e.type === "thought_delta") {
    const last = events[events.length - 1];
    if (last && last.type === "thought" && last.msg === e.msg) {
      const next = events.slice(0, -1);
      next.push({ ...last, text: last.text + e.text, end: e.ts });
      return next;
    }
    return [...events, { type: "thought", msg: e.msg, text: e.text, ts: e.ts, end: e.ts, parent: e.parent }];
  }
  if (e.type === "tool_input") {
    return events.map((x) => (x.type === "tool_start" && x.tool_id === e.tool_id ? { ...x, input: e.input } : x));
  }
  if (["thinking", "status", "tool_pending", "threads", "context", "settings", "thread_reset", "context_usage", "compacting"].includes(e.type)) return events;
  return [...events, e];
}
