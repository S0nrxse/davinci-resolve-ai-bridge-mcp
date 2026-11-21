import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, DEMO, type ContextUsage, foldEvent, IN_SHELL, shell, subscribe, type AppState, type ChatEvent, type FileItem, type MediaRef, type Settings, type Skill } from "./api";
import { ApprovalCard, ApprovalContext, AuthBanner, Composer, ContextStrip, EmptyState, Header, MediaPicker, MessageActions, NightRain, NotesChip, TaskCard, CompactingRow, scrollHold, NOTES_PROMPT, fileThumb, Transcript, useSingleTooltip, WorkingRow, type Draft } from "./components";
import { useToast } from "./seek";
import { Lightbox } from "./lightbox";
import { demoEvents, demoFiles, demoFrame, demoMedia, demoNotes, demoRewind, demoSkills, demoState, demoUsage } from "./demo";
import { Icon } from "./icons";
import { ctxClips, ctxTimeline, fold, type Item } from "./model";
import { isRecording, matches, ShortcutsContext, withDefaults } from "./shortcuts";
import { rememberClips } from "./tools";

type Status = { state: string; detail?: string };
const EMPTY_DRAFT: Draft = { attachments: [], refs: [] };

export default function App() {
  const [state, setState] = useState<AppState | null>(DEMO ? demoState : null);
  const [threadId, setThreadId] = useState<string | null>(DEMO ? "demo1" : null);
  const [events, setEvents] = useState<ChatEvent[]>(DEMO ? demoEvents : []);
  // The demo opens mid-turn (paused on an approval) so steering, Stop and the working row are visible.
  const [running, setRunning] = useState(DEMO);
  const [status, setStatus] = useState<Status>({ state: "idle" });
  const [online, setOnline] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [seed, setSeed] = useState<{ text: string; n: number } | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const [dragging, setDragging] = useState(false);
  const threadRef = useRef<string | null>(threadId);
  threadRef.current = threadId;
  useSingleTooltip();
  // The rain on the glass can be switched off (Settings); remembered per Mac in localStorage.
  const [rain, setRainState] = useState(() => {
    try {
      return localStorage.getItem("resolve-ai-chat.rain") !== "off";
    } catch {
      return true;
    }
  });
  const setRain = useCallback((on: boolean) => {
    setRainState(on);
    try {
      localStorage.setItem("resolve-ai-chat.rain", on ? "on" : "off");
    } catch {
      // Not remembered; the choice still applies now.
    }
  }, []);
  const scrollIdle = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // ------------------------------------------------------------ data flow
  const localRewind = useRef(false);
  // How full this thread's context window is, and whether it's being summarized right now (both live-only events).
  const [usage, setUsage] = useState<ContextUsage | null>(DEMO ? demoUsage : null);
  const [compacting, setCompacting] = useState(false);
  const loadThread = useCallback(async (id: string) => {
    if (DEMO) return;
    const t = await api.thread(id);
    if (threadRef.current !== id) return;
    setEvents(t.events);
    setUsage(t.context_usage ?? null);
    setCompacting(false);
    setRunning(t.running);
    setStatus({ state: t.running ? "running" : "idle" });
  }, []);

  useEffect(() => {
    if (DEMO) return;
    api
      .state()
      .then((s) => {
        setState(s);
        const first = s.threads[0];
        if (first) setThreadId(first.id);
      })
      .catch((e) => setError(e.message));
    return subscribe(
      (e) => {
        if (e.type === "threads") return setState((s) => (s ? { ...s, threads: e.threads } : s));
        if (e.type === "context") return setState((s) => (s ? { ...s, context: e.context } : s));
        if (e.type === "settings") return setState((s) => (s ? { ...s, settings: e.settings } : s));
        if (e.type === "thread_reset") {
          // A rewind started here reloads the thread itself, after the removed messages have faded out.
          if (e.thread === threadRef.current && !localRewind.current) loadThread(e.thread).catch(() => {});
          return;
        }
        if (e.thread !== threadRef.current) return;
        if (e.type === "context_usage") return setUsage({ used: e.used, window: e.window, model: e.model });
        if (e.type === "compacting") return setCompacting(true);
        if (e.type === "compacted" || e.type === "turn_end") setCompacting(false);
        if (e.type === "status") {
          setStatus({ state: e.state, detail: e.detail });
          setRunning(e.state === "running");
          return;
        }
        setEvents((prev) => foldEvent(prev, e));
      },
      (ok) => {
        setOnline(ok);
        if (ok && threadRef.current) loadThread(threadRef.current).catch(() => {});
      },
    );
  }, [loadThread]);

  useEffect(() => {
    if (threadId && !DEMO) loadThread(threadId).catch((e) => setError(e.message));
    if (!threadId) {
      setEvents(DEMO ? [] : []);
      setRunning(false);
      if (!DEMO) setUsage(null);
      setCompacting(false);
    }
  }, [threadId, loadThread]);

  useEffect(() => {
    shell({ type: "ready" });
  }, []);

  // ------------------------------------------------------------- actions
  // Switching or starting a chat starts from a clean composer: a draft never carries into another chat.
  const [composerReset, setComposerReset] = useState(0);
  const openThread = useCallback((id: string | null) => {
    setSeed(null);
    setDraft(EMPTY_DRAFT);
    setComposerReset((n) => n + 1);
    setThreadId(id);
  }, []);
  const newThread = useCallback(() => {
    openThread(null);
    setEvents([]);
    setStatus({ state: "idle" });
  }, [openThread]);

  // `bare`: send just this text (e.g. "Do them" on the notes chip), leaving the composer's draft and attachments alone.
  const [sentAt, setSentAt] = useState<{ thread: string; ts: number } | null>(null);
  const send = useCallback(
    async (text: string, bare = false) => {
      setError(null);
      setNotice(null);
      if (!running) setSentAt({ thread: threadId ?? "new", ts: Date.now() / 1000 });
      const atts = bare ? [] : draft.attachments;
      const refs = bare ? [] : draft.refs;
      if (!bare) setDraft(EMPTY_DRAFT);
      if (DEMO) {
        setEvents((ev) => [...ev, { type: "user", id: String(Math.random()), text, ts: Date.now() / 1000, context: state?.context, attachments: atts, refs, steer: running, rewind: !running }]);
        // Demo: a new chat starts a pretend run (with an internal ToolSearch step, which stays hidden).
        if (!running && events.length === 0) {
          const t = Date.now() / 1000;
          setRunning(true);
          setEvents((ev) => [
            ...ev,
            { type: "tool_start", tool_id: "ds", ts: t + 0.2, name: "ToolSearch", input: { query: "select:resolve" } },
            { type: "tool_result", tool_id: "ds", ts: t + 0.4, ok: true, summary: "{}" },
            { type: "tool_start", tool_id: "dc", ts: t + 0.5, name: "mcp__resolve-ai-bridge__resolve_status", input: {} },
          ]);
        }
        return;
      }
      try {
        let id = threadId;
        if (!id) {
          const t = await api.newThread(state?.settings.agent);
          id = t.id;
          setSentAt((s) => (s && s.thread === "new" ? { ...s, thread: t.id } : s));
          threadRef.current = id;
          setThreadId(id);
          setEvents([]);
        }
        setRunning(true);
        await api.send(
          id,
          text,
          atts.map((a) => a.id),
          refs,
        );
      } catch (e: any) {
        if (!running) setRunning(false);
        if (!bare) setDraft({ attachments: atts, refs });
        setError(e.message);
      }
    },
    [threadId, state?.settings.agent, state?.context, draft, running, events.length],
  );

  const addFiles = useCallback(
    async (files: File[]) => {
      if (!files.length) return;
      setBusy(true);
      try {
        const added: Draft["attachments"] = [];
        for (const f of files) {
          if (DEMO) {
            added.push({ id: Math.random().toString(16).slice(2, 12), name: f.name, type: f.type, size: f.size, preview: f.type.startsWith("image/") ? URL.createObjectURL(f) : undefined });
            continue;
          }
          const a = await api.upload(f);
          added.push({ ...a, preview: f.type.startsWith("image/") ? URL.createObjectURL(f) : undefined });
        }
        setDraft((d) => ({ ...d, attachments: [...d.attachments, ...added] }));
      } catch (e: any) {
        setError(e.message);
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const addFrame = useCallback(async () => {
    if (DEMO) {
      const tc = String((state?.context as any)?.playhead_timecode ?? "");
      const name = `frame_${tc.replace(/:/g, "_")}.png`;
      setDraft((d) => ({ ...d, attachments: [...d.attachments, { id: "f" + Date.now(), name, type: "image/png", size: 418_000, preview: demoFrame }] }));
      return;
    }
    setBusy(true);
    try {
      const a = await api.frame();
      setDraft((d) => ({ ...d, attachments: [...d.attachments, a] }));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [state?.context]);

  // "/" in the composer: attach a file, clip or recent attachment by path (the server references it, no copy).
  const addPath = useCallback(async (item: FileItem) => {
    if (DEMO) {
      const image = /\.(png|jpe?g|gif|webp|tiff?)$/i.test(item.name);
      setDraft((d) => ({
        ...d,
        attachments: [...d.attachments, { id: "p" + Date.now(), name: item.name, type: image ? "image/png" : item.kind === "media" ? "video/quicktime" : "application/octet-stream", size: item.size ?? 0, path: item.path, preview: fileThumb(item) ?? (image ? demoFrame : undefined) }],
      }));
      return;
    }
    setBusy(true);
    try {
      const a = await api.attachPath(item.path);
      setDraft((d) => ({ ...d, attachments: [...d.attachments, { ...a, preview: a.preview ?? fileThumb(item) }] }));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, []);
  const loadFiles = useCallback(async (q: string): Promise<FileItem[]> => {
    if (DEMO) {
      const n = q.trim().toLowerCase();
      return demoFiles.filter((f) => !n || `${f.name} ${f.detail ?? ""}`.toLowerCase().includes(n));
    }
    return (await api.files(q)).items;
  }, []);
  const loadSkills = useCallback(async (): Promise<Skill[]> => (DEMO ? demoSkills : (await api.skills()).items), []);

  const loadMedia = useCallback(async (): Promise<MediaRef[]> => {
    if (DEMO) return demoMedia;
    return (await api.media()).items;
  }, []);

  const stop = useCallback(() => {
    if (threadId && !DEMO) api.interrupt(threadId).catch((e) => setError(e.message));
  }, [threadId]);

  const decide = useCallback(
    (req: string, d: "allow" | "deny" | "always") => {
      if (DEMO) {
        const now = Date.now() / 1000;
        const toolId = events.find((e) => e.type === "permission" && e.request === req)?.tool_id;
        setEvents((ev) => [...ev, { type: "permission_resolved", request: req, decision: d, ts: now }]);
        setTimeout(() => {
          const t = Date.now() / 1000;
          setEvents((ev) => [
            ...ev,
            ...(d === "deny"
              ? [{ type: "text", msg: "d" + t, ts: t, text: "Skipped the zoom. Nothing on the timeline changed." }]
              : [
                  { type: "tool_result", tool_id: toolId, ts: t, ok: true, summary: '{"ok": true, "result": {"verified": true}}' },
                  { type: "text", msg: "d" + t, ts: t, text: "The push now runs from frame 12 to the end of the clip, 1.0× to 1.15× with an ease-out. Fusion read the keys back. Want the vignette next?" },
                ]),
            { type: "turn_end", ts: t, ok: true },
          ]);
          setRunning(false);
        }, 900);
        return;
      }
      if (threadId) api.decide(threadId, req, d).catch((e) => setError(e.message));
    },
    [threadId, events],
  );

  // ------------------------------------------------------------- timeline notes, seek toasts
  const [toastMsg, dismissToast] = useToast();
  const loadNotes = useCallback(async () => (DEMO ? (demoNotes as any) : api.notes()), []);
  const openNotes = Number(state?.context?.open_notes) || 0;

  // ------------------------------------------------------------- compact
  const hasMessages = events.some((e) => e.type === "user");
  const compactBlocked = running ? "Wait for Claude to finish (or stop it) before compacting." : !hasMessages ? "Nothing to compact yet." : null;
  const compactNow = useCallback(
    (focus?: string) => {
      setError(null);
      if (DEMO) {
        setCompacting(true);
        setRunning(true);
        setTimeout(() => {
          setEvents((ev) => [...ev, { type: "compacted", trigger: "manual", pre_tokens: 40602, post_tokens: 9100, ts: Date.now() / 1000, instructions: focus }]);
          setUsage((u) => (u ? { ...u, used: 9100 } : u));
          setCompacting(false);
          setRunning(false);
        }, 1800);
        return;
      }
      if (!threadId) return;
      api.compact(threadId, focus).catch((e) => setError(e.message));
    },
    [threadId],
  );

  // ------------------------------------------------------------- rewind
  const [notice, setNotice] = useState<string | null>(null);
  const eventsRef = useRef(events);
  eventsRef.current = events;
  const runningRef = useRef(running);
  runningRef.current = running;
  const previewRewind = useCallback(
    async (messageId: string) => {
      if (DEMO) {
        if (runningRef.current) throw new Error("Stop the current turn before rewinding.");
        await new Promise((r) => setTimeout(r, 250));
        return { ...(demoRewind[messageId] ?? { messages: 1, project_edits: 0, checkpoint: null }), checkpoints_enabled: true };
      }
      if (!threadId) throw new Error("This chat isn’t saved yet.");
      return api.rewindPreview(threadId, messageId);
    },
    [threadId],
  );
  /** Fade out a message and everything after it (the part a rewind removes) before the transcript drops it. */
  const fadeOutFrom = (messageId: string) => {
    const start = document.querySelector(`[data-msg-id="${CSS.escape(messageId)}"]`);
    if (!start || matchMedia("(prefers-reduced-motion: reduce)").matches) return Promise.resolve();
    const els: Element[] = [];
    for (let el: Element | null = start; el; el = el.nextElementSibling) els.push(el);
    els.forEach((el) => el.animate([{ opacity: 1 }, { opacity: 0, transform: "translateY(4px)" }], { duration: 200, easing: "cubic-bezier(0.22, 1, 0.36, 1)", fill: "forwards" }));
    return new Promise((r) => setTimeout(r, 210));
  };
  const rewindTo = useCallback(
    async (messageId: string, restoreProject: boolean) => {
      let result: { text?: string; attachments?: any[]; refs?: MediaRef[]; restored?: any; project_restored?: boolean };
      if (DEMO) {
        if (runningRef.current) throw new Error("Stop the current turn before rewinding.");
        await new Promise((r) => setTimeout(r, 300));
        const ev = eventsRef.current;
        const at = ev.findIndex((x) => x.type === "user" && x.id === messageId);
        const msg = ev[at];
        await fadeOutFrom(messageId);
        setEvents(ev.slice(0, Math.max(0, at)));
        const cp = demoRewind[messageId]?.checkpoint;
        result = {
          text: msg?.text,
          attachments: msg?.attachments ?? [],
          refs: msg?.refs ?? [],
          project_restored: Boolean(restoreProject && cp),
          restored: restoreProject && cp ? { restored: { name: `${cp.timeline} (rewound)` } } : null,
        };
      } else {
        if (!threadId) return;
        localRewind.current = true;
        try {
          result = await api.rewind(threadId, messageId, restoreProject);
          await fadeOutFrom(messageId);
          await loadThread(threadId);
        } finally {
          localRewind.current = false;
        }
      }
      setSeed({ text: result.text ?? "", n: Date.now() });
      setDraft({ attachments: (result.attachments ?? []) as Draft["attachments"], refs: result.refs ?? [] });
      const opened = result.restored?.restored?.name;
      setNotice(result.project_restored && opened ? `Rewound. Opened “${opened}”; the previous timeline is kept.` : "Rewound the chat.");
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [threadId, loadThread],
  );
  const messageActions = useMemo(
    () => ({ preview: previewRewind, rewind: rewindTo, running, stop: DEMO ? () => setRunning(false) : stop, version: events.length }),
    [previewRewind, rewindTo, running, stop, events.length],
  );
  useEffect(() => setNotice(null), [threadId]);

  const updateSettings = useCallback((p: Partial<Settings> & { clear_always_allow?: boolean }) => {
    setState((s) => (s ? { ...s, settings: { ...s.settings, ...p, always_allow: p.clear_always_allow ? [] : s.settings.always_allow } } : s));
    if (!DEMO) api.settings(p).catch((e) => setError(e.message));
  }, []);

  const deleteThread = useCallback(
    (id: string) => {
      if (DEMO) return;
      api.deleteThread(id).catch((e) => setError(e.message));
      if (id === threadId) setThreadId(null);
    },
    [threadId],
  );

  // App-wide shortcuts (all from the editable map in Settings › Shortcuts).
  const shortcuts = useMemo(() => withDefaults(state?.settings.shortcuts), [state?.settings.shortcuts]);
  const keyState = useRef({ running, stop });
  keyState.current = { running, stop };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || isRecording()) return;
      if (matches(e, shortcuts.newChat)) {
        e.preventDefault();
        newThread();
      } else if (matches(e, shortcuts.stop) && keyState.current.running) {
        e.preventDefault();
        keyState.current.stop();
      } else if (matches(e, shortcuts.collapse) && IN_SHELL) {
        e.preventDefault();
        shell({ type: "collapse" });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [newThread, shortcuts]);

  // --------------------------------------------------------------- derive
  const { items, pending, tasks, tasksThisTurn } = useMemo(() => {
    for (const e of events) if (e.type === "user" && e.context) rememberClips(ctxClips(e.context));
    if (state?.context) rememberClips(ctxClips(state.context));
    return fold(events);
  }, [events, state?.context]);

  // The collapsed bubble shows a dot while the agent works or waits for approval.
  useEffect(() => {
    shell({ type: "badge", state: pending.length ? "approval" : running ? "running" : "idle" });
  }, [pending.length, running]);

  // Requests whose step is in the transcript are answered on that row; anything else falls back to a docked card.
  const inline = useMemo(() => {
    const reqs = new Set<string>();
    const walk = (list: Item[]) => {
      for (const it of list)
        if (it.kind === "tool") {
          if (it.step.permission) reqs.add(it.step.permission.request);
          walk(it.step.children);
        }
    };
    walk(items);
    return reqs;
  }, [items]);
  const docked = pending.find((p) => !inline.has(p.request));
  const approvals = useMemo(
    () => ({ pending, decide, timeline: ctxTimeline(state?.context), copy: state?.settings.safe_copy }),
    [pending, decide, state?.context, state?.settings.safe_copy],
  );
  const [atBottom, setAtBottom] = useState(true);

  const scroller = useRef<HTMLDivElement>(null);
  // The top bar overlays the transcript; its height (header, plus any offline or sign-in rows) pads the top.
  const topBar = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = topBar.current;
    if (!el) return;
    const set = () => el.parentElement?.style.setProperty("--top-h", `${el.offsetHeight}px`);
    set();
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => ro.disconnect();
  });
  const [content, setContent] = useState<HTMLDivElement | null>(null);
  const pinned = useRef(true);
  // While something the user just opened or closed animates (a step, a rewind panel), the transcript holds still
  // instead of chasing the bottom; afterwards it's pinned only if it really is at the bottom.
  const userToggle = useRef(-Infinity);
  const holding = () => performance.now() - userToggle.current < 700 || performance.now() < scrollHold.until;
  // A message you send always brings the transcript back to its newest end.
  const lastUser = [...items].reverse().find((it) => it.kind === "user")?.key;
  const seenUser = useRef(lastUser);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (lastUser !== seenUser.current) {
      seenUser.current = lastUser;
      pinned.current = true;
    }
    if (el && pinned.current && !holding()) el.scrollTop = el.scrollHeight;
  }, [items, pending.length]);
  // Stay pinned to the newest step whenever anything changes size after it mounts: the transcript (the composer
  // grows, the panel resizes) or its content (an approval card, a frame thumbnail loading, a step expanding).
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      if (holding()) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      else if (pinned.current) el.scrollTop = el.scrollHeight;
    });
    ro.observe(el);
    if (content) ro.observe(content);
    return () => ro.disconnect();
  }, [state === null, content]);
  // The "needs your approval" pill only shows while no part of the waiting card is on screen.
  const [cardVisible, setCardVisible] = useState(false);
  useEffect(() => {
    const root = scroller.current;
    const card = root?.querySelector('[role="alertdialog"]');
    if (!root || !card) {
      setCardVisible(false);
      return;
    }
    const io = new IntersectionObserver(([e]) => setCardVisible(e.isIntersecting), { root, threshold: 0 });
    io.observe(card);
    return () => io.disconnect();
  }, [items, pending.length, content]);

  if (!state) {
    return (
      <div className="grid h-full place-items-center p-6 text-center text-[12.5px] text-muted">
        {error ? <p>Can’t reach the chat server: {error}</p> : <span className="size-4 animate-spin rounded-full border border-faint border-t-transparent" />}
      </div>
    );
  }

  const current = state.threads.find((t) => t.id === threadId);
  const agentId = current?.agent ?? state.settings.agent;
  const agent = state.agents.find((a) => a.id === agentId);
  const authRequired = status.state === "auth_required";
  const disabled = agent && !agent.available ? `${agent.label} isn’t installed on this Mac` : null;
  // The turn started at the last message that wasn’t a mid-turn steer.
  const lastUserTs = [...events].reverse().find((e) => e.type === "user" && !e.steer)?.ts;
  // The run timer starts the moment you send: this thread's own send time, never another thread's or turn's.
  const runSince = sentAt && sentAt.thread === (threadId ?? "new") ? sentAt.ts : lastUserTs;
  const liveTasks = running && tasksThisTurn && Boolean(tasks?.length);

  return (
    <ShortcutsContext.Provider value={shortcuts}>
    <div
      className="t-app relative flex h-full flex-col overflow-clip bg-base"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={(e) => e.currentTarget === e.target && setDragging(false)}
      onDrop={(e) => {
        if (!e.dataTransfer.files.length) return;
        e.preventDefault();
        setDragging(false);
        addFiles(Array.from(e.dataTransfer.files));
      }}
    >
      {/* The night outside: fog in the sky (styles.css .t-app) and fine rain over everything, behind the content. */}
      <NightRain off={!rain} />
      <div className="grain" aria-hidden="true" />
      {/* The top bar floats over the conversation: text and rain scroll under it and show through, blurred by a blur that fades out. */}
      <div ref={topBar} className="t-topbar absolute inset-x-0 top-0 z-30">
      <Header
        threads={state.threads}
        current={current}
        onSelect={openThread}
        onNew={newThread}
        onDelete={deleteThread}
        settings={state.settings}
        agents={state.agents}
        onSettings={updateSettings}
        version={state.version}
        onCollapse={IN_SHELL ? () => shell({ type: "collapse" }) : undefined}
        demo={DEMO}
        rain={rain}
        onRain={setRain}
        onDrag={(e) => {
          if (!(e.target as HTMLElement).closest("button")) shell({ type: "drag" });
        }}
      />
      <ContextStrip ctx={state.context} onRetry={() => !DEMO && api.refreshContext()} />
      {!online && <p className="bg-warn/10 px-(--gutter) py-1.5 text-[12px] text-warn">Reconnecting to the chat server…</p>}
      {authRequired && <AuthBanner agent={agentId} detail={status.detail} />}
      </div>

      <div
        ref={scroller}
        onPointerDown={(e) => {
          // Only disclosure toggles hold the scroll; Allow, Send and the like keep following the newest step.
          // Opening something anchors the reading position there: stop following the bottom until the user scrolls
          // back down or sends a message.
          if ((e.target as HTMLElement).closest("[aria-expanded]")) {
            userToggle.current = performance.now();
            pinned.current = false;
          }
        }}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && (e.target as HTMLElement).closest("[aria-expanded]")) {
            userToggle.current = performance.now();
            pinned.current = false;
          }
        }}
        onScroll={(e) => {
          const el = e.currentTarget;
          // The scrollbar shows only while scrolling (styles.css .t-stage[data-scrolling]).
          el.dataset.scrolling = "";
          clearTimeout(scrollIdle.current);
          scrollIdle.current = setTimeout(() => delete el.dataset.scrolling, 900);
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          if (pinned.current !== atBottom) setAtBottom(pinned.current);
        }}
        className="t-stage flex min-h-0 flex-1 flex-col overflow-y-auto px-(--gutter) pt-4"
      >
        {items.length === 0 ? (
          <EmptyState ctx={state.context} />
        ) : (
          <div ref={setContent} className="mt-auto pb-4">
            <ApprovalContext.Provider value={approvals}>
              <MessageActions.Provider value={messageActions}>
                <Transcript key={threadId ?? "new"} items={items} running={running} liveTasks={liveTasks} />
              </MessageActions.Provider>
            </ApprovalContext.Provider>
            {/* While a step waits on its own inline card, that card is the status; no second "waiting" line. */}
            {compacting && <CompactingRow />}
            {running && !compacting && !(pending.length > 0 && !docked) && <WorkingRow key={runSince} since={runSince} waiting={pending.length > 0} />}
          </div>
        )}
      </div>

      {/* z-20: the composer, its upward menus, tooltips and the approval pill sit above the blur bands (z-3). */}
      <div className="t-footer relative z-20 shrink-0 space-y-2.5 px-(--gutter) pt-1.5 pb-(--gutter)">
        {toastMsg && (
          <div key={toastMsg.id} className="rise flex items-center gap-2 px-1 text-[12px] text-err" role="alert">
            <span className="min-w-0 flex-1">{toastMsg.text}</span>
            <button type="button" className="text-faint transition-colors duration-150 ease-out hover:text-ink" onClick={dismissToast}>
              Dismiss
            </button>
          </div>
        )}
        {liveTasks && tasks && <TaskCard tasks={tasks} live />}
        {openNotes > 0 && !docked && <NotesChip count={openNotes} load={loadNotes} onDo={() => send(NOTES_PROMPT, true)} disabled={running} />}
        {notice && !error && (
          <div className="rise flex items-center gap-2 px-1 text-[12px] text-muted" role="status">
            <span className="min-w-0 flex-1">{notice}</span>
            <button type="button" className="text-faint transition-colors duration-150 ease-out hover:text-ink" onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </div>
        )}
        {error && (
          <div className="flex items-start gap-2 text-[11.5px] text-err">
            <span className="min-w-0 flex-1">{error}</span>
            <button type="button" className="text-muted hover:text-ink" onClick={() => setError(null)}>
              Dismiss
            </button>
          </div>
        )}
        {docked && <ApprovalCard key={docked.request} pending={docked} count={pending.length} onDecide={(d) => decide(docked.request, d)} />}
        {!docked && pending.length > 0 && !cardVisible && (
          <button
            type="button"
            onClick={() => {
              const el = scroller.current;
              if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
            }}
            className="rise mx-auto flex h-7 items-center gap-1.5 rounded-full bg-raised px-3 text-[12px] text-ink shadow-[0_8px_20px_-10px_rgba(0,0,0,0.8)] transition-colors duration-150 ease-out hover:bg-raised-2"
          >
            <span className="size-[6px] rounded-full bg-accent" aria-hidden="true" />
            {pending.length === 1 ? "A step needs your approval" : `${pending.length} steps need your approval`}
            <Icon.Chevron size={11} className="text-faint" />
          </button>
        )}
        <Composer
          running={running}
          onSend={send}
          onStop={stop}
          settings={state.settings}
          agent={agent}
          onSettings={updateSettings}
          seed={seed}
          disabled={disabled}
          draft={draft}
          setDraft={setDraft}
          onFiles={addFiles}
          onFrame={addFrame}
          onPickMedia={() => setPicking(true)}
          busy={busy}
          ctx={state.context}
          resetKey={composerReset}
          loadFiles={loadFiles}
          loadSkills={loadSkills}
          onAttachPath={addPath}
          usage={usage}
          compact={agent?.compact || DEMO ? { blocked: compactBlocked, busy: compacting, onCompact: compactNow } : null}
        />
      </div>

      {picking && (
        <MediaPicker
          load={loadMedia}
          onClose={() => setPicking(false)}
          onPick={(r) => {
            setDraft((d) => ({ ...d, refs: [...d.refs, r] }));
            setPicking(false);
          }}
        />
      )}
      <Lightbox />
      {dragging && (
        <div className="pointer-events-none absolute inset-(--gutter) z-50 grid place-items-center rounded-2xl border border-dashed border-accent/50 bg-base/85 backdrop-blur-sm">
          <div className="flex flex-col items-center gap-2 text-[12.5px] text-ink">
            <Icon.Paperclip size={18} className="text-accent-hi" />
            Drop to attach
          </div>
        </div>
      )}
    </div>
    </ShortcutsContext.Provider>
  );
}
