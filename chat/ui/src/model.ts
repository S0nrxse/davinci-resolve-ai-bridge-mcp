import type { ChatEvent, ClipRef, Context } from "./api";

export type Step = {
  id: string;
  name: string;
  input: any;
  ts: number;
  result?: { ok: boolean; summary: string; image?: string | null; ts: number };
  permission?: { request: string; decision?: string };
  /** Work a subagent did inside this call (Task/Agent tools). */
  children: Item[];
};

export type Item =
  | { kind: "user"; key: string; e: ChatEvent }
  | { kind: "text"; key: string; e: ChatEvent }
  | { kind: "thought"; key: string; e: ChatEvent }
  | { kind: "tool"; key: string; step: Step }
  | { kind: "end"; key: string; e: ChatEvent }
  | { kind: "notice"; key: string; e: ChatEvent }
  | { kind: "checkpoint"; key: string; e: ChatEvent }
  | { kind: "tasks"; key: string; e: ChatEvent }
  | { kind: "compacted"; key: string; e: ChatEvent };

/** One entry of the agent's own task list (the server sends the whole list on every change). */
export type Task = { id: string; subject: string; status: "pending" | "in_progress" | "completed"; active_form?: string };

export type Pending = { request: string; tool: string; input: any; tool_id?: string; ts?: number };

/** Fold the event log into ordered items: thoughts, text and tool calls exactly where they happened. */
export function fold(events: ChatEvent[]): { items: Item[]; pending: Pending[]; tasks: Task[] | null; tasksThisTurn: boolean } {
  const items: Item[] = [];
  const steps = new Map<string, Step>();
  const perms = new Map<string, Pending>();
  const decided = new Map<string, string>();

  let lastAsked: string | undefined;
  let tasks: Task[] | null = null;
  let tasksAt = -1;
  let lastUserAt = -1;
  const target = (parent?: string | null) => (parent && steps.get(parent)?.children) || items;

  events.forEach((e, n) => {
    const ts = e.ts ?? 0;
    switch (e.type) {
      case "user":
        if (!e.steer) lastUserAt = n;
        items.push({ kind: "user", key: e.id ?? "u" + n, e });
        break;
      case "text":
        if (e.text?.trim()) target(e.parent).push({ kind: "text", key: "t" + n, e });
        break;
      case "thought":
        if (e.text?.trim()) target(e.parent).push({ kind: "thought", key: "h" + n, e });
        break;
      case "tool_start": {
        if (steps.has(e.tool_id)) break;
        const step: Step = { id: e.tool_id ?? "s" + n, name: e.name, input: e.input ?? {}, ts, children: [] };
        steps.set(step.id, step);
        target(e.parent).push({ kind: "tool", key: "s" + step.id, step });
        break;
      }
      case "tool_result": {
        const step = steps.get(e.tool_id);
        if (step) step.result = { ok: e.ok !== false, summary: e.summary ?? "", image: e.image, ts };
        break;
      }
      case "permission":
        lastAsked = e.tool_id;
        perms.set(e.request, { request: e.request, tool: e.tool, input: e.input, tool_id: e.tool_id, ts });
        break;
      case "permission_resolved":
        decided.set(e.request, e.decision);
        break;
      case "turn_end":
        if (!e.ok && e.error) items.push({ kind: "end", key: "e" + n, e });
        break;
      case "notice":
        items.push({ kind: "notice", key: "n" + n, e });
        break;
      case "compacted":
        items.push({ kind: "compacted", key: "z" + n, e });
        break;
      case "tasks": {
        // The last list wins: it sits where it last changed, and only once in the transcript.
        const prev = items.findIndex((it) => it.kind === "tasks");
        if (prev >= 0) items.splice(prev, 1);
        items.push({ kind: "tasks", key: "k" + n, e });
        tasks = Array.isArray(e.tasks) ? (e.tasks as Task[]) : [];
        tasksAt = n;
        break;
      }
      case "checkpoint": {
        // The server saves the restore point once the first edit is approved, so the event lands after that step
        // started; show it just before the step it protects.
        const at = items.findIndex((it) => it.kind === "tool" && it.step.id === lastAsked && !it.step.result);
        if (at >= 0) items.splice(at, 0, { kind: "checkpoint", key: "c" + n, e });
        else items.push({ kind: "checkpoint", key: "c" + n, e });
        break;
      }
    }
  });
  for (const [req, p] of perms) {
    const step = p.tool_id ? steps.get(p.tool_id) : undefined;
    if (step) step.permission = { request: req, decision: decided.get(req) };
  }
  const pending = Array.from(perms.values()).filter((p) => !decided.has(p.request));
  return { items, pending, tasks, tasksThisTurn: tasksAt > lastUserAt };
}

// ---------------------------------------------------------------- context

function nameOf(v: unknown): string | undefined {
  if (!v) return undefined;
  if (typeof v === "string") return v;
  if (typeof v === "object" && v && "name" in v) return String((v as any).name ?? "");
  return undefined;
}

export function ctxProject(c?: Context | null) {
  return nameOf(c?.project) ?? (c as any)?.project_name;
}
export function ctxTimeline(c?: Context | null) {
  return nameOf(c?.timeline) ?? (c as any)?.timeline_name;
}
export function ctxTimecode(c?: Context | null): string | undefined {
  if ((c as any)?.playhead_timecode) return (c as any).playhead_timecode;
  const p = c?.playhead as any;
  if (!p) return (c as any)?.timecode;
  return typeof p === "string" ? p : p.timecode ?? (p.frame != null ? `frame ${p.frame}` : undefined);
}
export function ctxClips(c?: Context | null): ClipRef[] {
  const list = ((c as any)?.clips_at_playhead ?? c?.under_playhead ?? c?.items_under_playhead ?? c?.clips_under_playhead ?? []) as ClipRef[];
  if (!Array.isArray(list)) return [];
  return [...list].sort((a, b) => trackNo(b.track) - trackNo(a.track));
}
export function trackNo(t: unknown): number {
  const n = parseInt(String(t ?? "").replace(/\D/g, ""), 10);
  return Number.isFinite(n) ? n : 0;
}
export function trackLabel(t: unknown): string {
  if (t == null) return "V?";
  const s = String(t);
  return /^[VA]\d+/i.test(s) ? s.toUpperCase() : "V" + s;
}

export function ago(ts: number): string {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function secs(a: number, b?: number) {
  if (!b) return "";
  const d = b - a;
  return d < 1 ? "<1s" : d < 60 ? `${Math.round(d)}s` : `${Math.floor(d / 60)}m ${Math.round(d % 60)}s`;
}

export function bytes(n: number) {
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

/** "01:00:12:04" at a whole-number frame rate → frames since 00:00:00:00 (null if it isn't a timecode). */
export function tcToFrames(tc: string, fps: number): number | null {
  const m = /^(\d+)[:;.](\d+)[:;.](\d+)[:;.](\d+)$/.exec(tc.trim());
  if (!m) return null;
  return ((Number(m[1]) * 60 + Number(m[2])) * 60 + Number(m[3])) * fps + Number(m[4]);
}

/** Frames → "HH:MM:SS:FF", keeping the separator style of the timecode it came from (";" for drop frame). */
export function framesToTc(n: number, fps: number, like = ":"): string {
  const sep = like.includes(";") ? ";" : ":";
  const f = Math.max(0, Math.floor(n));
  const ff = f % fps;
  const total = Math.floor(f / fps);
  const p2 = (v: number) => String(v).padStart(2, "0");
  return [p2(Math.floor(total / 3600)), p2(Math.floor(total / 60) % 60), p2(total % 60)].join(":") + sep + p2(ff);
}
