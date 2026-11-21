import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { DEMO, thumbUrl, type Agent, type Attachment, type RewindPreview, type ChatEvent, type Context, type FileItem, type MediaRef, type Settings, type Skill, type Thread, type NotesResult, type ContextUsage } from "./api";
import { Icon } from "./icons";
import { demoPosters } from "./demo";
import { Markdown } from "./markdown";
import { linkify, SeekLink } from "./seek";
import { Zoomable } from "./lightbox";
import { comboOf, DEFAULT_SHORTCUTS, formatCombo, isRecording, matches, SHORTCUTS, useShortcuts, type ShortcutAction } from "./shortcuts";
import { ago, bytes, ctxClips, ctxProject, ctxTimecode, ctxTimeline, secs, type Item, type Pending, type Step, type Task } from "./model";
import {
  allowScope,
  approvalSentence,
  approvalWhat,
  bareName,
  category,
  describe,
  detail,
  graphFromBuild,
  graphFromKeyframe,
  isInternalTool,
  toolGlyph,
  isResolveTool,
  nodeFamily,
  type NodeChip,
} from "./tools";

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

// ------------------------------------------------------------------ primitives

/**
 * One tooltip at a time. A `[data-tip]` control shows its tooltip (styles.css) only while it holds `data-tip-on`:
 * set on hover, or on focus reached with Tab; never on focus that returns programmatically (after Escape closes a
 * menu, say). Pressing, Escape and scrolling hide it.
 */
export function useSingleTooltip() {
  useEffect(() => {
    let on: Element | null = null;
    let tabbing = false;
    const hide = () => {
      on?.removeAttribute("data-tip-on");
      on = null;
    };
    const show = (el: Element | null) => {
      if (el === on) return;
      hide();
      if (el) {
        // An ⓘ sits right after its label, anywhere across the panel: open its explanation toward the roomier side.
        if (el.hasAttribute("data-tip-click")) {
          const r = el.getBoundingClientRect();
          el.setAttribute("data-tip-align", r.left + r.width / 2 < window.innerWidth / 2 ? "start" : "end");
        }
        el.setAttribute("data-tip-on", "");
        on = el;
      }
    };
    const tipOf = (t: EventTarget | null) => (t instanceof Element ? t.closest("[data-tip]") : null);
    const over = (e: PointerEvent) => show(tipOf(e.target));
    const leave = (e: PointerEvent) => !e.relatedTarget && hide();
    const key = (e: KeyboardEvent) => {
      tabbing = e.key === "Tab";
      if (!tabbing) hide();
    };
    const focus = (e: FocusEvent) => {
      if (tabbing) show(tipOf(e.target));
    };
    const blur = (e: FocusEvent) => {
      if (on && on === tipOf(e.target) && !on.matches(":hover")) hide();
    };
    // ⓘ buttons also open on click, so explanations never depend on hover (the panel may not get mouse-moved events).
    // A second click on the same ⓘ closes it; a hover that happened to show it first doesn't count as opened.
    let clicked: Element | null = null;
    let wasClicked: Element | null = null;
    const down = () => {
      tabbing = false;
      wasClicked = on && on === clicked ? on : null;
      clicked = null;
      hide();
    };
    const click = (e: MouseEvent) => {
      const el = e.target instanceof Element ? e.target.closest("[data-tip-click]") : null;
      if (el && el !== wasClicked) {
        show(el);
        clicked = el;
      }
      wasClicked = null;
    };
    document.addEventListener("pointerover", over);
    document.addEventListener("pointerout", leave);
    document.addEventListener("keydown", key, true);
    document.addEventListener("focusin", focus);
    document.addEventListener("focusout", blur);
    document.addEventListener("pointerdown", down, true);
    document.addEventListener("click", click, true);
    document.addEventListener("scroll", hide, true);
    return () => {
      document.removeEventListener("pointerover", over);
      document.removeEventListener("pointerout", leave);
      document.removeEventListener("keydown", key, true);
      document.removeEventListener("focusin", focus);
      document.removeEventListener("focusout", blur);
      document.removeEventListener("click", click, true);
      document.removeEventListener("pointerdown", down, true);
      document.removeEventListener("scroll", hide, true);
    };
  }, []);
}

/** Where an icon-only control's tooltip opens (styles.css `[data-tip]`). */
export type Tip = { pos?: "below"; align?: "start" | "end" };

export function IconButton({
  label,
  onClick,
  children,
  active,
  className,
  tip,
  expanded,
}: {
  label: string;
  onClick?: () => void;
  children: ReactNode;
  active?: boolean;
  className?: string;
  tip?: Tip;
  expanded?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      data-tip={label}
      data-tip-pos={tip?.pos}
      data-tip-align={tip?.align}
      aria-expanded={expanded}
      onClick={onClick}
      className={cx(
        "grid size-8 place-items-center rounded-lg text-muted transition-colors duration-150 ease-out hover:bg-white/[0.06] hover:text-ink-strong active:bg-white/[0.09]",
        active && "bg-raised-2 text-ink-strong",
        className,
      )}
    >
      {children}
    </button>
  );
}

/** The menu that is open right now, so opening another closes it. */
let openMenu: { id: object; close: () => void } | null = null;

export function Popover({
  open,
  onClose,
  children,
  align = "left",
  up,
  className,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  align?: "left" | "right" | "stretch";
  up?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // One menu at a time, app-wide: opening this one closes whichever was open.
  const me = useRef({});
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    if (openMenu && openMenu.id !== me.current) openMenu.close();
    openMenu = { id: me.current, close: () => closeRef.current() };
    return () => {
      if (openMenu?.id === me.current) openMenu = null;
    };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    document.body.dataset.popover = "1";
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement;
      if (!ref.current?.contains(t) && !t.closest("[data-popover-trigger]")) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      delete document.body.dataset.popover;
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div
      ref={ref}
      className={cx(
        "pop absolute z-40 max-w-[calc(100vw-2*var(--gutter))] rounded-lg border border-white/[0.07] bg-raised shadow-[0_24px_48px_-16px_rgba(0,0,0,0.85)]",
        up ? "bottom-full mb-2" : "top-full mt-1.5",
        align === "stretch" ? "inset-x-0" : align === "right" ? "right-0 min-w-56" : "left-0 min-w-56",
        className,
      )}
      style={{ ["--pop-origin" as any]: `${up ? "bottom" : "top"} ${align === "right" ? "right" : align === "stretch" ? "center" : "left"}`, ["--pop-y" as any]: up ? "6px" : "-6px" }}
    >
      {children}
    </div>
  );
}

/**
 * One menu option, set like a macOS menu: an optional glyph (no tile around it), the label, and, when it needs one,
 * its explanation as a quiet second line. `hint` sits flush right on the same line (a model's one-word nature);
 * `end` is the control (switch, check). The whole row is one button: there are no ⓘ buttons anywhere.
 */
function MenuRow({
  role,
  checked,
  onClick,
  icon,
  iconTone,
  title,
  info,
  hint,
  end,
  disabled,
}: {
  role: "menuitem" | "menuitemcheckbox" | "menuitemradio";
  checked?: boolean;
  onClick: () => void;
  icon?: ReactNode;
  iconTone?: string;
  title: string;
  info?: string;
  hint?: string;
  end?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <div className="px-1.5">
      <button
        type="button"
        role={role}
        aria-checked={role === "menuitem" ? undefined : checked}
        onClick={onClick}
        disabled={disabled}
        className={cx(
          "flex w-full gap-2.5 rounded-[7px] px-2.5 text-left transition-colors duration-150 ease-out hover:bg-white/[0.05] focus-visible:bg-white/[0.05] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent",
          info ? "items-start py-[7px]" : "h-8 items-center",
        )}
      >
        {icon && <span className={cx("grid h-[18px] w-4 shrink-0 place-items-center", iconTone ?? "text-faint")}>{icon}</span>}
        <span className={cx("min-w-0", hint ? "shrink-0" : "flex-1")}>
          <span className={cx("block truncate text-[12.5px] leading-[18px]", checked ? "text-ink-strong" : "text-ink")}>{title}</span>
          {info && <span className="mt-px block text-[11.5px] leading-[1.45] text-faint">{info}</span>}
        </span>
        {hint && <span className="min-w-0 flex-1 truncate text-right text-[11.5px] leading-[18px] text-faint">{hint}</span>}
        {end && <span className="flex h-[18px] shrink-0 items-center">{end}</span>}
      </button>
    </div>
  );
}

function Switch({ on }: { on: boolean }) {
  return (
    <span className={cx("block h-[18px] w-[30px] shrink-0 rounded-full p-[2px] transition-colors duration-200 ease-out", on ? "bg-accent" : "bg-line-3")} aria-hidden="true">
      <span className={cx("block size-[14px] rounded-full transition-[transform,background-color] duration-200 ease-[var(--ease-out)]", on ? "translate-x-[12px] bg-accent-ink" : "bg-ink")} />
    </span>
  );
}

// ---------------------------------------------------------------------- header

export function Header({
  threads,
  current,
  onSelect,
  onNew,
  onDelete,
  settings,
  agents,
  onSettings,
  version,
  onDrag,
  onCollapse,
  demo,
  rain,
  onRain,
}: {
  threads: Thread[];
  current: Thread | undefined;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  settings: Settings;
  agents: Agent[];
  onSettings: (p: Partial<Settings> & { clear_always_allow?: boolean }) => void;
  version: string | null;
  onDrag: (e: React.MouseEvent) => void;
  onCollapse?: () => void;
  demo?: boolean;
  rain: boolean;
  onRain: (on: boolean) => void;
}) {
  const sc = useShortcuts();
  const [menu, setMenu] = useState<null | "threads" | "settings">(null);
  return (
    // z-30: the header and its menus sit above the transcript's depth-of-field blur bands (z-3) in every theme.
    <header
      onMouseDown={onDrag}
      className="t-header relative z-30 flex h-11 shrink-0 items-center gap-0.5 bg-panel pr-[calc(var(--gutter)-6px)] pl-[calc(var(--gutter)-8px)] select-none"
    >
      <div className="relative flex min-w-0 flex-1 items-center gap-1.5">
        <button
          type="button"
          data-popover-trigger
          onClick={() => setMenu(menu === "threads" ? null : "threads")}
          aria-expanded={menu === "threads"}
          aria-label={`Chat: ${current?.title || "New chat"}. Switch chat`}
          className="flex max-w-full min-w-0 items-center gap-1 rounded-lg px-2 py-1.5 text-[13px] font-medium text-ink-strong transition-colors duration-150 hover:bg-raised-2"
        >
          <span className="t-title truncate">{current?.title || "New chat"}</span>
          <Icon.Chevron className={cx("shrink-0 text-faint transition-transform duration-200", menu === "threads" && "rotate-180")} />
        </button>
        {demo && (
          <span
            tabIndex={0}
            data-tip="Demo data: names, frames and results are made up."
            data-tip-pos="below"
            data-tip-align="start"
            className="tag shrink-0 text-faint"
          >
            Demo
          </span>
        )}
        <Popover open={menu === "threads"} onClose={() => setMenu(null)} className="left-1 w-[300px]">
          <div className="max-h-80 overflow-y-auto p-1.5">
            {threads.length === 0 && <p className="px-2.5 py-2 text-[12px] text-muted">No chats yet.</p>}
            {threads.map((t) => (
              <div key={t.id} className={cx("group flex items-center gap-1 rounded-lg", t.id === current?.id && "bg-raised-2/70")}>
                <button
                  type="button"
                  onClick={() => {
                    onSelect(t.id);
                    setMenu(null);
                  }}
                  className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2.5 py-2 text-left hover:bg-raised-2/60"
                >
                  <span className={cx("size-1.5 shrink-0 rounded-full", t.running ? "bg-live" : "bg-line-3")} />
                  <span className="truncate text-[12.5px] text-ink">{t.title || "New chat"}</span>
                  <span className="ml-auto shrink-0 text-[11px] text-faint">{ago(t.updated)}</span>
                </button>
                <IconButton label="Delete chat" onClick={() => onDelete(t.id)} className="size-7 text-faint/60 hover:text-ink focus-visible:text-ink" tip={{ align: "end" }}>
                  <Icon.Trash size={12} />
                </IconButton>
              </div>
            ))}
          </div>
        </Popover>
      </div>
      <IconButton label={`New chat  ${formatCombo(sc.newChat)}`} onClick={onNew} tip={{ pos: "below", align: "end" }}>
        <Icon.Plus />
      </IconButton>
      <div className="relative">
        <span data-popover-trigger>
          <IconButton label="Settings" onClick={() => setMenu(menu === "settings" ? null : "settings")} active={menu === "settings"} expanded={menu === "settings"} tip={{ pos: "below", align: "end" }}>
            <Icon.Sliders />
          </IconButton>
        </span>
        <Popover open={menu === "settings"} onClose={() => setMenu(null)} align="right">
          <SettingsPanel settings={settings} agents={agents} onSettings={onSettings} version={version} rain={rain} onRain={onRain} />
        </Popover>
      </div>
      {onCollapse && (
        <IconButton label={`Collapse to bubble  ${formatCombo(sc.collapse)}`} onClick={onCollapse} tip={{ pos: "below", align: "end" }}>
          <Icon.Bubble />
        </IconButton>
      )}
    </header>
  );
}

function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { id: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="flex rounded-[7px] bg-white/[0.04] p-0.5">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          onClick={() => onChange(o.id)}
          aria-pressed={value === o.id}
          className={cx(
            "h-7 flex-1 rounded-[5px] px-1 text-[12px] whitespace-nowrap transition-colors duration-150 ease-out",
            value === o.id ? "bg-white/[0.09] text-ink-strong" : "text-muted hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function SettingsPanel({
  settings,
  agents,
  onSettings,
  version,
  rain,
  onRain,
}: {
  settings: Settings;
  agents: Agent[];
  onSettings: (p: Partial<Settings> & { clear_always_allow?: boolean }) => void;
  version: string | null;
  rain: boolean;
  onRain: (on: boolean) => void;
}) {
  return (
    <div className="no-scrollbar max-h-[min(78vh,640px)] w-[300px] overflow-y-auto px-1.5 pt-3 pb-3 text-[12px]">
      <Field label="Agent for new chats" inset>
        <Segmented
          value={settings.agent}
          options={agents.map((a) => ({ id: a.id, label: a.label + (a.available ? "" : " (not installed)") }))}
          onChange={(agent) => onSettings({ agent })}
        />
      </Field>
      <div className="mt-2">
        <SettingToggle
          on={settings.attach_context}
          onToggle={() => onSettings({ attach_context: !settings.attach_context })}
          title="Project location"
          info="Each message tells Claude the open project, timeline, playhead and the clip under it, so “this clip” works."
        />
        <SettingToggle
          on={settings.track_changes !== false}
          onToggle={() => onSettings({ track_changes: settings.track_changes === false })}
          title="Tell Claude what I changed"
          info="Claude hears what you changed in Resolve by hand since its last reply."
        />
        <SettingToggle on={rain} onToggle={() => onRain(!rain)} title="Rain on the glass" info="Slow, faint rain behind the panel. Off when Reduce Motion is on." />
      </div>
      <Field label="Always allowed" info={settings.always_allow.length ? undefined : "Tools you choose “Always allow” for will run without asking."}>
        {settings.always_allow.length > 0 && (
          <p className="text-[12.5px] leading-[1.5] text-ink">
            {settings.always_allow.map((t) => describe(t)[0]).join(", ")}
            <button type="button" onClick={() => onSettings({ clear_always_allow: true })} className="ml-2 text-[12px] text-accent-hi hover:underline">
              Reset
            </button>
          </p>
        )}
      </Field>
      <ShortcutEditor value={settings.shortcuts} onSave={(shortcuts) => onSettings({ shortcuts })} />
      <p className="mt-4 px-2.5 text-[11px] text-faint">Resolve AI Bridge {version ?? ""}</p>
    </div>
  );
}

function SettingToggle({ on, onToggle, title, info }: { on: boolean; onToggle: () => void; title: string; info: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={onToggle}
      className="flex w-full items-start gap-3 rounded-[7px] px-2.5 py-[7px] text-left transition-colors duration-150 ease-out hover:bg-white/[0.05] focus-visible:bg-white/[0.05]"
    >
      <span className="min-w-0 flex-1">
        <span className="block text-[12.5px] leading-[18px] text-ink">{title}</span>
        <span className="mt-px block text-[11.5px] leading-[1.45] text-faint">{info}</span>
      </span>
      <Switch on={on} />
    </button>
  );
}

/**
 * Settings › Shortcuts: every action's combo, recorded by clicking it and pressing the keys. A combo another
 * action already uses is refused with a note; esc cancels recording; Reset restores the defaults.
 */
function ShortcutEditor({ value, onSave }: { value?: Record<string, string>; onSave: (m: Record<string, string>) => void }) {
  const map = useShortcuts();
  const [rec, setRec] = useState<ShortcutAction | null>(null);
  const [warn, setWarn] = useState<string | null>(null);
  const changed = SHORTCUTS.some((a) => map[a.id] !== DEFAULT_SHORTCUTS[a.id]);
  useEffect(() => {
    if (!rec) return;
    document.body.dataset.recording = "1";
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === "Escape" && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey) {
        setRec(null);
        return;
      }
      const combo = comboOf(e);
      if (!combo) return;
      const clash = SHORTCUTS.find((a) => a.id !== rec && map[a.id] === combo);
      if (clash) {
        setWarn(`${formatCombo(combo)} is already ${clash.label}. Pick another, or change that one first.`);
        return;
      }
      setWarn(null);
      onSave({ ...(value ?? {}), ...map, [rec]: combo });
      setRec(null);
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      delete document.body.dataset.recording;
      window.removeEventListener("keydown", onKey, true);
    };
  }, [rec, map, value, onSave]);
  return (
    <Field label="Shortcuts" info="Click one, then press the new keys. esc cancels.">
      <ul>
        {SHORTCUTS.map((a) => (
          <li key={a.id} className="flex h-8 items-center gap-2 px-2.5" title={a.hint}>
            <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink">{a.label}</span>
            <button
              type="button"
              onClick={() => {
                setWarn(null);
                setRec(rec === a.id ? null : a.id);
              }}
              aria-label={`${a.label}: ${formatCombo(map[a.id])}. Change`}
              className={cx(
                "h-6 min-w-11 rounded-[5px] px-2 text-[11.5px] tabular-nums transition-colors duration-150 ease-out",
                rec === a.id ? "bg-accent/15 text-accent-hi" : "text-muted hover:bg-white/[0.06] hover:text-ink",
              )}
            >
              {rec === a.id ? "Press keys…" : formatCombo(map[a.id])}
            </button>
          </li>
        ))}
        <li className="flex h-8 items-center gap-2 px-2.5" title="Set by the app; change it in the panel app's menu">
          <span className="min-w-0 flex-1 truncate text-[12.5px] text-muted">Show or hide the panel</span>
          <span className="h-6 min-w-11 px-2 text-center text-[11.5px] leading-6 text-faint">⌥⌘A</span>
        </li>
      </ul>
      {warn && <p className="rise px-2.5 text-[11.5px] leading-snug text-err">{warn}</p>}
      {changed && (
        <button type="button" onClick={() => onSave({ ...DEFAULT_SHORTCUTS })} className="px-2.5 text-[11.5px] text-accent-hi hover:underline">
          Reset to defaults
        </button>
      )}
    </Field>
  );
}

/** A settings group: a quiet label, an optional sentence, then its controls. No ⓘ. */
function Field({ label, info, children, inset }: { label: string; info?: string; children: ReactNode; inset?: boolean }) {
  return (
    <div className="mt-4 space-y-1.5 first:mt-0">
      <div className="px-2.5">
        <p className="text-[11.5px] font-medium text-muted">{label}</p>
        {info && <p className="mt-0.5 text-[11.5px] leading-[1.45] text-faint">{info}</p>}
      </div>
      <div className={inset ? "px-1" : undefined}>{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------- context strip

/** Kept for callers that still pass the agent's state; the context row no longer shows it. */
export type Live = "idle" | "standby" | "live" | "hold";

/**
 * The row under the header appears only when the connection to Resolve is broken (or still being found): the exact
 * menu path to start the bridge, and Retry. Connected, there is nothing here; the empty state shows what is under the
 * playhead, and every message already carries it to the agent.
 */
export function ContextStrip({ ctx, onRetry }: { ctx: Context; onRetry: () => void; live?: Live; copy?: boolean }) {
  if (ctx.connected) return null;
  const starting = ctx.state === "starting";
  return (
    <div className="t-context relative z-20 shrink-0">
      <div className="flex items-start gap-3 px-(--gutter) pt-1 pb-3">
        <div className="min-w-0 flex-1 text-[12.5px] leading-snug">
          <p className={starting ? "text-muted" : "text-warn"}>{starting ? "Looking for Resolve…" : "Resolve isn’t connected"}</p>
          {!starting && (
            <p className="mt-1 text-[12px] text-muted">
              In Resolve: Workspace › Scripts › Resolve AI Bridge › <span className="text-ink">Start AI Bridge (Lua)</span>
            </p>
          )}
        </div>
        {!starting && (
          <button type="button" onClick={onRetry} className="h-8 shrink-0 rounded-lg bg-white/[0.06] px-3 text-[12px] text-ink transition-colors duration-150 ease-out hover:bg-white/[0.1]">
            Retry
          </button>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------- open / close

const reduceMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
const EASE_OUT = "cubic-bezier(0.22, 1, 0.36, 1)";
/** While a disclosure animates, the transcript shouldn't chase its bottom edge (App reads this). */
export const scrollHold = { until: 0 };

/**
 * Animate a box's height when its content opens or closes. Opening: the content appears and the box grows from its
 * old height. Closing: the content stays while the box shrinks to `collapsed()` (fading when it closes to nothing),
 * and only then is it swapped out, so nothing is clipped or popped mid-way. Returns whether the open layout is shown.
 */
export function useSmoothOpen(open: boolean, ref: RefObject<HTMLElement | null>, collapsed: () => number = () => 0, fade = true): boolean {
  const [shown, setShown] = useState(open);
  if (open && !shown) setShown(true);
  const before = useRef<number | null>(null);
  const first = useRef(true);
  useLayoutEffect(() => {
    const el = ref.current;
    if (first.current) {
      first.current = false;
      return;
    }
    if (!el) return;
    scrollHold.until = performance.now() + 520;
    el.getAnimations().forEach((a) => a.cancel());
    if (reduceMotion()) {
      if (!open) setShown(false);
      return;
    }
    const from = open ? (before.current ?? collapsed()) : el.offsetHeight;
    const to = open ? el.offsetHeight : collapsed();
    if (from === to) {
      if (!open) setShown(false);
      return;
    }
    const toZero = fade && Math.min(from, to) === 0;
    const kf: Keyframe[] = [
      { height: `${from}px`, ...(toZero ? { opacity: open ? 0 : 1, transform: open ? "translateY(-3px)" : "none" } : {}) },
      { height: `${to}px`, ...(toZero ? { opacity: open ? 1 : 0, transform: open ? "none" : "translateY(-3px)" } : {}) },
    ];
    el.style.overflow = "hidden";
    const duration = open ? 220 : 190;
    const a = el.animate(kf, { duration, easing: EASE_OUT });
    let settled = false;
    const settle = () => {
      if (settled) return;
      settled = true;
      el.style.overflow = "";
      if (!open) setShown(false);
    };
    a.onfinish = settle;
    // Animation events only fire on rendered frames; a timer makes sure a close still completes when they don't.
    const timer = setTimeout(() => {
      if (a.playState === "paused") return;
      a.finish();
      settle();
    }, duration + 60);
    a.oncancel = () => {
      settled = true;
      clearTimeout(timer);
      el.style.overflow = "";
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  useLayoutEffect(() => {
    before.current = ref.current?.offsetHeight ?? null;
  });
  return shown;
}

/** Content that opens and closes smoothly (height, fade and a 3px slide together). */
export function Fold({ open, children, className }: { open: boolean; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const shown = useSmoothOpen(open, ref);
  return (
    <div ref={ref} className={cx("flow-root", className)} aria-hidden={!open || undefined} inert={!open}>
      {shown && children}
    </div>
  );
}

const lineHeightOf = (el: HTMLElement | null) => (el ? parseFloat(getComputedStyle(el).lineHeight) || 18 : 18);

// ------------------------------------------------------------------- messages

/** What a message's controls can do; provided by App. */
export const MessageActions = createContext<{
  preview: (messageId: string) => Promise<RewindPreview>;
  rewind: (messageId: string, restoreProject: boolean) => Promise<void>;
  /** A turn is running: rewinding has to wait (the server refuses), so the panel says so up front with Stop. */
  running: boolean;
  stop: () => void;
  /** Bumps whenever the thread's events change, so an open preview is re-read instead of going stale. */
  version: number;
} | null>(null);

/** Copy text, falling back to a hidden textarea + execCommand where the async clipboard isn't allowed (WKWebView). */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the legacy path
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

/**
 * A message's Copy (and Rewind): two quiet glyphs under it. They stay out of sight except on the newest turn, until the
 * message is clicked (the panel may never get mouse-moved events, so a click reveals them), hovered or tabbed into.
 */
function MessageControls({
  text,
  onRewind,
  rewindOpen,
  rewindBusy,
  rewindRef,
  align = "start",
  shown,
}: {
  text: string;
  onRewind?: () => void;
  rewindOpen?: boolean;
  rewindBusy?: boolean;
  rewindRef?: RefObject<HTMLButtonElement | null>;
  align?: "start" | "end";
  shown?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  // Both buttons are the same 24px box with a 12px glyph; the row is pulled out by the box's 6px inset so the glyphs
  // line up with the text's edge.
  const btn = "grid size-6 shrink-0 place-items-center rounded-md transition-colors duration-150 ease-out hover:bg-white/[0.06] hover:text-ink focus-visible:text-ink";
  return (
    <div
      className={cx(
        "flex h-6 items-center gap-0.5 transition-opacity duration-200 ease-out",
        align === "end" ? "-mr-1.5 justify-end" : "-ml-1.5",
        !(shown || rewindOpen || rewindBusy || copied) && "opacity-0 group-hover/msg:opacity-100 group-focus-within/msg:opacity-100 group-data-[revealed]/msg:opacity-100",
      )}
    >
      <button
        type="button"
        aria-label="Copy"
        data-tip="Copy"
        data-tip-align={align}
        onClick={async () => {
          if (await copyText(text)) {
            setCopied(true);
            setTimeout(() => setCopied(false), 1400);
          }
        }}
        className={cx(btn, copied ? "text-ok hover:text-ok" : "text-faint")}
      >
        {copied ? <Icon.Check size={12} /> : <Icon.Copy size={12} />}
        <span className="sr-only" aria-live="polite">
          {copied ? "Copied" : ""}
        </span>
      </button>
      {onRewind && (
        <button
          type="button"
          ref={rewindRef}
          aria-label="Rewind to here"
          aria-expanded={rewindOpen}
          aria-busy={rewindBusy || undefined}
          data-tip="Rewind to here"
          data-tip-align={align}
          onClick={onRewind}
          className={cx(btn, rewindOpen || rewindBusy ? "bg-white/[0.07] text-ink" : "text-faint")}
        >
          <Icon.Rewind size={12} className={rewindBusy ? "animate-[spin_900ms_linear_infinite_reverse]" : undefined} />
        </button>
      )}
    </div>
  );
}

/** A click on a message (not a drag that selects text, not a control inside it) reveals its Copy / Rewind. */
function useReveal() {
  const [revealed, setRevealed] = useState(false);
  const onClick = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest("button, a, input, textarea")) return;
    if (String(window.getSelection?.() ?? "").length) return;
    setRevealed((r) => !r);
  };
  return { "data-revealed": revealed ? "" : undefined, onClick };
}

/**
 * Rewind to a message, confirmed inline under it. The preview (what goes: the chat after this point and, if the AI
 * changed the project since, whether a restore point can bring the timeline back) is read before the panel opens, so
 * it opens once at its final height instead of growing while it loads. Esc or Cancel closes it.
 */
function RewindConfirm({
  messageId,
  preview,
  error: loadError,
  onClose,
}: {
  messageId: string;
  preview: RewindPreview | null;
  error: string | null;
  onClose: () => void;
}) {
  const actions = useContext(MessageActions);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "chat" | "both">(null);
  const box = useRef<HTMLDivElement>(null);
  const running = Boolean(actions?.running);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || busy) return;
      e.preventDefault();
      e.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose, busy]);
  // Focus moves into the panel (without scrolling); once it has opened, make sure all of it is on screen.
  useEffect(() => {
    box.current?.focus({ preventScroll: true });
    const t = setTimeout(() => box.current?.scrollIntoView({ block: "nearest", behavior: reduceMotion() ? "auto" : "smooth" }), 240);
    return () => clearTimeout(t);
  }, []);
  useEffect(() => setError(null), [running]);
  const go = async (restore: boolean) => {
    if (!actions || busy) return; // one rewind per click, however fast the clicks
    setBusy(restore ? "both" : "chat");
    setError(null);
    try {
      await actions.rewind(messageId, restore);
    } catch (e: any) {
      setError(e.message);
      setBusy(null);
    }
  };
  const edits = preview?.project_edits ?? 0;
  const cp = preview?.checkpoint ?? null;
  const n = (k: number, one: string, many: string) => `${k} ${k === 1 ? one : many}`;
  const shownError = error ?? loadError;
  const quiet = "h-7 rounded-[7px] px-2.5 text-muted transition-colors duration-150 ease-out hover:bg-white/[0.05] hover:text-ink disabled:opacity-50";
  const second = "h-7 rounded-[7px] px-2.5 text-ink transition-colors duration-150 ease-out hover:bg-white/[0.06] disabled:opacity-50";
  const primary = "h-7 rounded-[7px] bg-accent px-3 font-medium text-accent-ink transition-colors duration-150 ease-out hover:bg-accent-hi disabled:opacity-50";
  return (
    <div ref={box} tabIndex={-1} role="alertdialog" aria-label="Rewind to here?" aria-busy={Boolean(busy) || undefined} className="pt-1.5 pb-1 text-[12.5px] leading-[1.55] outline-none">
      <p className="font-medium text-ink-strong">Rewind to here?</p>
      <p className="text-muted">The chat after this message is removed and Claude forgets it. Your message goes back in the box.</p>
      {running ? (
        <p className="mt-2 text-ink">Stop the current turn first.</p>
      ) : (
        <>
          {preview && edits > 0 && cp && (
            <p className="mt-2 text-muted">
              <span className="text-err">The AI made {n(edits, "change", "changes")} to your project since.</span>{" "}
              <span className="text-ink">Chat and timeline</span> opens a copy of “{cp.timeline ?? "the timeline"}” as it was; the current one is kept. Imports, new timelines and project settings aren’t undone.{" "}
              <span className="text-ink">Chat only</span> leaves the project as it is.
            </p>
          )}
          {preview && edits > 0 && !cp && (
            <p className="mt-2 text-muted">
              <span className="text-err">{n(edits, "project change", "project changes")} since can’t be undone</span> (no restore point was saved). Rewinding only resets the chat.
            </p>
          )}
        </>
      )}
      <Fold open={Boolean(shownError) && !running}>
        <p className="pt-1.5 text-err" role="alert">
          {shownError}
        </p>
      </Fold>
      <div className="mt-2.5 flex flex-wrap items-center gap-1">
        {running ? (
          <button type="button" onClick={() => actions?.stop()} className={second}>
            Stop the turn
          </button>
        ) : preview && edits > 0 && cp ? (
          <>
            <button type="button" disabled={Boolean(busy)} onClick={() => go(true)} className={primary}>
              {busy === "both" ? "Rewinding…" : "Rewind chat and timeline"}
            </button>
            <button type="button" disabled={Boolean(busy)} onClick={() => go(false)} className={second}>
              {busy === "chat" ? "Rewinding…" : "Chat only"}
            </button>
          </>
        ) : (
          <button type="button" disabled={!preview || Boolean(busy)} onClick={() => go(false)} className={primary}>
            {busy ? "Rewinding…" : edits > 0 ? "Rewind the chat" : "Rewind"}
          </button>
        )}
        <button type="button" disabled={Boolean(busy)} onClick={onClose} className={quiet}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/**
 * Open/close and preview for one message's rewind panel. The preview is read before opening (the Rewind glyph turns
 * while it loads) and re-read while open whenever the thread changes, so it never shows a stale count.
 */
function useRewind(messageId: string | undefined) {
  const actions = useContext(MessageActions);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<RewindPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const load = useCallback(async () => {
    if (!actions || !messageId) return false;
    const my = ++seq.current;
    try {
      const p = await actions.preview(messageId);
      if (my !== seq.current) return false;
      setPreview(p);
      setError(null);
    } catch (e: any) {
      if (my !== seq.current) return false;
      setError(e.message);
    }
    return true;
  }, [actions, messageId]);
  const toggle = async () => {
    if (loading) return;
    if (open) {
      setOpen(false);
      return;
    }
    setLoading(true);
    const ok = await load();
    setLoading(false);
    if (ok) setOpen(true);
  };
  // Keep an open preview current: re-read it when the thread changes (a turn ends, an edit lands).
  const version = actions?.version ?? 0;
  const running = actions?.running ?? false;
  useEffect(() => {
    if (open) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, running]);
  return { open, setOpen, loading, preview, error, toggle };
}

/**
 * A restore point saved before the AI's first edit after a message: one quiet line in the log. Clicking it says what
 * it is (never a hover-only ⓘ).
 */
export function CheckpointRow({ e }: { e: ChatEvent }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="log-row group text-faint hover:text-muted">
        <span className="log-glyph">
          <Icon.Rewind size={12} />
        </span>
        <span className="min-w-0 truncate">Restore point saved{e.timeline ? ` · ${e.timeline}` : ""}</span>
      </button>
      <Fold open={open}>
        <p className="pb-1 pl-[var(--log-indent)] text-[12px] leading-[1.5] text-faint">A copy of the timeline as it was, in the AI Checkpoints bin. Rewind can bring it back.</p>
      </Fold>
    </div>
  );
}

/**
 * The editor's message: no bubble and no box. It is set in the editor's own voice, a warm ink hung on a 1px warm rule
 * in the margin, the way a script sets dialogue apart from action lines, so a long chat can be skimmed by its rules.
 * Everything that belongs to the message (the "you changed" note, the steer label, attachments, controls, the rewind
 * confirmation) hangs at the same indent.
 */
export function UserMessage({ e, first, latest }: { e: ChatEvent; first?: boolean; latest?: boolean }) {
  const atts = (e.attachments ?? []) as (Attachment & { preview?: string })[];
  const refs = (e.refs ?? []) as MediaRef[];
  const lands = useLands();
  const actions = useContext(MessageActions);
  const rw = useRewind(e.id ? String(e.id) : undefined);
  const rewindBtn = useRef<HTMLButtonElement>(null);
  const canRewind = Boolean(e.rewind && e.id && actions);
  const changes = Array.isArray(e.changes) ? (e.changes as string[]).filter(Boolean) : [];
  const reveal = useReveal();
  const closeRewind = useCallback(() => {
    rw.setOpen(false);
    rewindBtn.current?.focus({ preventScroll: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div className={cx("group/msg flex flex-col", !first && (e.steer ? "mt-1" : "mt-[18px]"))} data-msg-id={e.id ? String(e.id) : undefined} {...reveal}>
      {changes.length > 0 && <ChangesLine changes={changes} />}
      <div className={cx("you", lands && "rise")} data-steer={e.steer ? "" : undefined}>
        {e.steer && <p className="text-[11.5px] leading-[18px] text-faint">Sent while it worked</p>}
        {(atts.length > 0 || refs.length > 0) && (
          <div className={cx("flex flex-wrap gap-1.5 py-0.5", e.text && "mb-1.5")}>
            {atts.map((a) => (
              <SentAttachment key={a.id} a={a} />
            ))}
            {refs.map((r) => (
              <AttachmentChip key={r.id ?? r.name} name={r.name ?? "Clip"} meta={r.type ?? "Media pool"} kind="media" mediaType={r.type} preview={posterOf(r)} />
            ))}
          </div>
        )}
        {e.text && <p className="you-text text-[14px] leading-[1.55] break-words whitespace-pre-wrap">{e.text}</p>}
      </div>
      {(e.text || canRewind) && (
        <div className="mt-0.5 pl-[var(--you-indent)]">
          <MessageControls text={String(e.text ?? "")} onRewind={canRewind ? rw.toggle : undefined} rewindOpen={rw.open} rewindBusy={rw.loading} rewindRef={rewindBtn} shown={latest} />
        </div>
      )}
      {e.id && (
        <Fold open={rw.open} className="pl-[var(--you-indent)]">
          <RewindConfirm messageId={String(e.id)} preview={rw.preview} error={rw.error} onClose={closeRewind} />
        </Fold>
      )}
    </div>
  );
}

/**
 * What the editor changed by hand in Resolve since the agent's last reply (the server tells the agent the same list).
 * A quiet, collapsible line above the message it was sent with: informational, not a warning.
 */
function ChangesLine({ changes }: { changes: string[] }) {
  const [open, setOpen] = useState(false);
  const n = changes.length;
  return (
    <div className="mb-1 pl-[var(--you-indent)]">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex h-6 max-w-full items-center gap-1.5 text-[11.5px] text-faint transition-colors duration-150 ease-out hover:text-muted focus-visible:text-muted"
      >
        <span className="truncate">
          You changed {n} {n === 1 ? "thing" : "things"} in Resolve since the last reply
        </span>
        <Icon.Chevron size={10} className={cx("shrink-0 transition-transform duration-200 ease-out", open && "rotate-180")} />
      </button>
      <Fold open={open}>
        <ul className="mt-0.5 mb-1.5 space-y-0.5 text-[12px] leading-[1.5] text-muted">
          {changes.map((t, i) => (
            <li key={i} className="break-words">
              {t}
            </li>
          ))}
        </ul>
      </Fold>
    </div>
  );
}

/** "frame_01_00_12_04.png" → "Frame 01:00:12:04"; other names pass through. */
export function attachmentLabel(name: string): string {
  const m = /frame[\s_-]*(?:at[\s_-]*)?(\d{2})[_:.\-](\d{2})[_:.\-](\d{2})[_:;.\-](\d{2})/i.exec(name);
  return m ? `Frame ${m[1]}:${m[2]}:${m[3]}:${m[4]}` : name;
}

/** A clip's poster frame when the server (or demo) has one. */
export function posterOf(r: MediaRef): string | undefined {
  return r.poster ?? (r.thumb && r.id ? thumbUrl(r.id) : undefined);
}

/** A 16:9 still: the real image when it loads, the type icon tile when it doesn't. */
/** `zoom`: a caption; the poster then opens in the lightbox when clicked (or Enter). */
function Poster({ src, type, kind, className, zoom }: { src?: string; type?: string; kind: "image" | "media" | "file"; className: string; zoom?: string }) {
  const [failed, setFailed] = useState(false);
  const Glyph = kind === "media" ? mediaGlyph(type) : kind === "image" ? Icon.Image : Icon.File;
  if (src && !failed) {
    const img = <img src={src} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} className={cx("block shrink-0 rounded-[4px] bg-raised-2 object-cover", className)} />;
    return zoom ? (
      <Zoomable src={src} caption={zoom} className="shrink-0 rounded-[4px]">
        {img}
      </Zoomable>
    ) : (
      img
    );
  }
  return (
    <span className={cx("grid shrink-0 place-items-center rounded-[4px] bg-raised-2", kind === "media" ? "text-node-xf" : "text-muted", className)}>
      <Glyph size={12} />
    </span>
  );
}

/** An attachment's poster: a local preview, else the server's poster for its thumb_id (videos and images). */
export function attachmentPoster(a: Attachment & { preview?: string }): string | undefined {
  return a.preview ?? fileThumb(a);
}
const attachmentKind = (a: Attachment): "image" | "media" | "file" =>
  a.type.startsWith("image/") ? "image" : a.type.startsWith("video/") || a.type.startsWith("audio/") ? "media" : "file";
function attachmentMeta(a: Attachment & { preview?: string }): string | undefined {
  if (a.name !== attachmentLabel(a.name)) return "Still from the viewer";
  if (a.type.startsWith("video/")) return a.size ? `Video · ${bytes(a.size)}` : "Video";
  if (a.type.startsWith("image/")) return a.size ? `Image · ${bytes(a.size)}` : "Image";
  if (a.type.startsWith("audio/")) return a.size ? `Audio · ${bytes(a.size)}` : "Audio";
  return a.size ? bytes(a.size) : undefined;
}

/**
 * An attachment in a sent message. Images (and viewer stills) with a picture show as the picture itself, clickable to
 * view; everything else, or an image whose picture fails to load, is a chip (with a poster for videos).
 */
function SentAttachment({ a }: { a: Attachment & { preview?: string } }) {
  const [failed, setFailed] = useState(false);
  const src = attachmentPoster(a);
  if (attachmentKind(a) === "image" && src && !failed)
    return (
      <Zoomable src={src} caption={attachmentLabel(a.name)} className="shrink-0 rounded-lg">
        <img
          src={src}
          alt={attachmentLabel(a.name)}
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
          className="block h-[84px] w-auto max-w-[200px] min-w-[60px] rounded-lg bg-raised-2 object-cover"
        />
      </Zoomable>
    );
  return <AttachmentChip name={a.name} meta={attachmentMeta(a)} kind={attachmentKind(a)} mediaType={a.type} preview={src} />;
}

function AttachmentChip({
  name,
  meta,
  kind,
  preview,
  mediaType,
  onRemove,
}: {
  name: string;
  meta?: string;
  kind: "image" | "media" | "file";
  preview?: string;
  mediaType?: string;
  onRemove?: () => void;
}) {
  const label = attachmentLabel(name);
  return (
    <span className="flex h-[38px] max-w-[220px] shrink-0 items-center gap-2.5 rounded-lg bg-white/[0.05] py-[5px] pr-1.5 pl-[5px] text-[12px]" title={name}>
      <Poster src={preview} type={mediaType} kind={kind} zoom={preview ? name : undefined} className={kind === "file" && !preview ? "h-[28px] w-[28px]" : "h-[28px] w-[50px]"} />
      <span className="flex min-w-0 flex-col gap-0.5 leading-[1.15]">
        <span className={cx("truncate text-ink-strong", label !== name && "tabular-nums")}>{label}</span>
        {meta && <span className="truncate text-[10.5px] text-faint">{meta}</span>}
      </span>
      {onRemove ? (
        <button type="button" aria-label={`Remove ${label}`} onClick={onRemove} className="grid size-6 shrink-0 place-items-center rounded-md text-faint transition-colors duration-150 ease-out hover:bg-white/[0.08] hover:text-ink">
          <Icon.X size={11} />
        </button>
      ) : (
        <span className="w-1" />
      )}
    </span>
  );
}

/**
 * A secondary line of text (a subagent's note, a tool's result): one line that fades out at its end; clicking it
 * shows the whole text.
 */
function OneLine({ text, className, icon }: { text: string; className?: string; icon?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const wide = useSmoothOpen(open, ref, () => lineHeightOf(ref.current), false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !wide) setOverflows(el.scrollWidth > el.clientWidth + 1);
  }, [text, wide]);
  return (
    <p className={cx("flex gap-1.5", className)}>
      {icon}
      <span
        ref={ref}
        onClick={() => (overflows || open) && setOpen(!open)}
        title={overflows && !open ? "Show all" : undefined}
        className={cx(
          "block min-w-0 flex-1 break-words",
          wide ? "whitespace-pre-wrap" : "overflow-hidden whitespace-nowrap",
          !wide && overflows && "fade-right cursor-pointer",
          (overflows || open) && "cursor-pointer",
        )}
      >
        {linkify(text, { clips: true })}
      </span>
    </p>
  );
}

/** `copy` is set on the last text of a turn: the whole reply (every text block of the turn), as markdown. */
export function AssistantText({ e, streaming, depth = 0, copy, latest }: { e: ChatEvent; streaming: boolean; depth?: number; copy?: string; latest?: boolean }) {
  const reveal = useReveal();
  if (depth && !streaming) return <OneLine text={plain(String(e.text ?? ""))} className="text-[12px] leading-[1.55] text-muted hover:text-ink" />;
  if (!depth && !streaming && copy)
    return (
      <div className="group/msg text-[13.5px] leading-[1.6] text-ink" {...reveal}>
        <Markdown text={e.text} />
        <div className="mt-1">
          <MessageControls text={copy} shown={latest} />
        </div>
      </div>
    );
  return (
    <div className={cx(depth ? "text-[12px] leading-[1.55] text-muted" : "text-[13.5px] leading-[1.6] text-ink", streaming && "caret")}>
      <Markdown text={e.text} />
    </div>
  );
}

/** A turn that ended in an error: the reason in plain words, in the error colour, with Copy. No box. */
export function ErrorNote({ e, onCopy }: { e: ChatEvent; onCopy?: (s: string) => void }) {
  return (
    <div className="flex items-start gap-2 text-[12.5px] leading-[1.5]">
      <Icon.Warn size={13} className="mt-[3px] shrink-0 text-err" />
      <p className="min-w-0 flex-1 break-words text-ink">{String(e.error)}</p>
      {onCopy && (
        <button type="button" onClick={() => onCopy(String(e.error))} className="shrink-0 text-[11.5px] text-faint transition-colors duration-150 ease-out hover:text-ink">
          Copy
        </button>
      )}
    </div>
  );
}

// ------------------------------------------------------------ the transcript

/** Everything that reads as a line in the agent's log: steps, thoughts, restore points and the finished task list. */
const isWork = (it: Item) => it.kind === "tool" || it.kind === "thought" || it.kind === "checkpoint" || it.kind === "tasks";

/**
 * When the top-level transcript mounted. Steps that render in its first moments (opening a chat with history)
 * appear in place; only steps that arrive later land on the rail with the snap-in motion.
 */
const MountedAt = createContext(0);
function useLands() {
  const at = useContext(MountedAt);
  const [lands] = useState(() => performance.now() - at > 400);
  return lands;
}

/**
 * Render items in order. Consecutive thoughts and tool calls form one quiet log: one 24px line per step, a glyph in a
 * narrow margin, the label in editor language. No rail, no boxes, no per-step timings.
 */
/** `liveTasks`: the task list is pinned above the composer right now, so its transcript entry stays hidden. */
export function Transcript({ items: all, running, depth = 0, liveTasks = false }: { items: Item[]; running: boolean; depth?: number; liveTasks?: boolean }) {
  // Hidden things never become log lines (an empty log would still open a gap): bookkeeping tools, and the task list
  // while it's pinned above the composer.
  const items = all.filter(
    (it) => !(it.kind === "tool" && isInternalTool(it.step.name)) && !(it.kind === "tasks" && (liveTasks || !Array.isArray(it.e.tasks) || !(it.e.tasks as Task[]).length)),
  );
  const groups: (Item | Item[])[] = [];
  for (const it of items) {
    const last = groups[groups.length - 1];
    if (isWork(it) && Array.isArray(last)) last.push(it);
    else groups.push(isWork(it) ? [it] : it);
  }
  const lastItem = items[items.length - 1];
  // The reply's Copy sits on its last text block and copies every text block of that turn.
  const replyCopy = new Map<string, string>();
  let lastReply: string | undefined;
  if (!depth) {
    let turn: Item[] = [];
    const close = () => {
      const texts = turn.filter((x) => x.kind === "text");
      if (texts.length) {
        lastReply = texts[texts.length - 1].key;
        replyCopy.set(lastReply, texts.map((x) => String((x as any).e.text ?? "").trim()).filter(Boolean).join("\n\n"));
      }
      turn = [];
    };
    for (const it of items) {
      if (it.kind === "user") {
        close();
        lastReply = undefined;
      } else turn.push(it);
    }
    if (!running) close(); // a reply still being written has nothing final to copy yet
  }
  const firstUser = items.find((it) => it.kind === "user")?.key;
  const lastUser = [...items].reverse().find((it) => it.kind === "user")?.key;
  const [mountedAt] = useState(() => performance.now());
  const body = (
    <div className={cx("flex flex-col", depth ? "gap-0.5" : "gap-3.5")}>
      {groups.map((g) => {
        if (Array.isArray(g))
          return (
            <ol key={g[0].key} className="flex flex-col" aria-label="Agent steps">
              {g.map((it) => {
                if (it.kind === "thought") return <ThoughtRow key={it.key} e={it.e} live={running && it === lastItem} />;
                if (it.kind === "tool") return <ToolRow key={it.key} step={it.step} running={running} depth={depth} />;
                if (it.kind === "checkpoint")
                  return (
                    <li key={it.key}>
                      <CheckpointRow e={it.e} />
                    </li>
                  );
                if (it.kind === "tasks") {
                  return (
                    <li key={it.key}>
                      <TaskCard tasks={it.e.tasks as Task[]} live={false} />
                    </li>
                  );
                }
                return null;
              })}
            </ol>
          );
        const it = g;
        if (it.kind === "user") return <UserMessage key={it.key} e={it.e} first={it.key === firstUser} latest={it.key === lastUser && !lastReply} />;
        if (it.kind === "text") return <AssistantText key={it.key} e={it.e} depth={depth} streaming={running && it === lastItem} copy={replyCopy.get(it.key)} latest={it.key === lastReply} />;
        if (it.kind === "end") return <ErrorNote key={it.key} e={it.e} onCopy={(s) => copyText(s)} />;
        if (it.kind === "compacted") return <CompactedRow key={it.key} e={it.e} />;
        if (it.kind === "notice")
          return (
            <p key={it.key} className="text-[11.5px] text-faint">
              {it.e.text}
            </p>
          );
        return null;
      })}
    </div>
  );
  return depth ? body : <MountedAt.Provider value={mountedAt}>{body}</MountedAt.Provider>;
}

/** Seconds since a step started, ticking once a second ("4s", "1:12"): the only sign a step is still running. */
function StepClock({ since }: { since: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const draw = () => {
      const t = Math.max(0, Math.floor(Date.now() / 1000 - since));
      if (ref.current) ref.current.textContent = t < 60 ? `${t}s` : `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
    };
    draw();
    const id = setInterval(draw, 1000);
    return () => clearInterval(id);
  }, [since]);
  return <span ref={ref} className="text-[11.5px] text-faint tabular-nums" aria-hidden="true" />;
}

const plain = (s: string) =>
  s
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*\s][^*]*)\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .trim();

/**
 * A thought: no "Thought for 2s" row, just the thought itself, one faint italic line in the log that fades at its end.
 * The margin glyph opens the whole thought; so does clicking the line. While the agent is thinking it shows in full.
 */
function ThoughtRow({ e, live }: { e: ChatEvent; live: boolean }) {
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const body = useRef<HTMLParagraphElement>(null);
  const expanded = open || live;
  const wide = useSmoothOpen(expanded, body, () => lineHeightOf(body.current), false);
  const text = plain(String(e.text ?? ""));
  useLayoutEffect(() => {
    const el = body.current;
    if (el && !wide) setOverflows(el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1);
  }, [text, wide]);
  const canToggle = !live && (overflows || open);
  const lands = useLands();
  return (
    <li className={cx("flex items-start", lands && "snap-in")}>
      <button
        type="button"
        onClick={() => canToggle && setOpen(!open)}
        aria-expanded={canToggle ? expanded : undefined}
        aria-label={live ? "Thinking" : expanded ? "Show less of this thought" : "Show the whole thought"}
        disabled={!canToggle}
        className="log-glyph h-6 text-faint transition-colors duration-150 ease-out enabled:hover:text-ink"
      >
        <Icon.Thought size={13} />
      </button>
      <p
        ref={body}
        onClick={() => canToggle && setOpen(!open)}
        className={cx(
          "min-w-0 flex-1 py-[3px] text-[12.5px] leading-[18px] break-words text-faint italic",
          wide ? "pb-1.5 whitespace-pre-wrap" : "overflow-hidden whitespace-nowrap",
          !wide && overflows && "fade-right cursor-pointer hover:text-muted",
          live && "text-muted",
        )}
      >
        {text ? linkify(text, { clips: true }) : live ? "Thinking" : ""}
      </p>
    </li>
  );
}

export type Decision = "allow" | "deny" | "always";
/** Pending approvals, so the tool row that asked can show its own Allow / Decline. */
export const ApprovalContext = createContext<{ pending: Pending[]; decide: (req: string, d: Decision) => void; timeline?: string; copy?: boolean } | null>(null);

const RISKY = ["delete_clips", "apply_silence_cuts", "render_current_timeline", "fusion_run_lua", "resolve_api", "Bash"];

function ToolRow({ step, running, depth }: { step: Step; running: boolean; depth: number }) {
  const [open, setOpen] = useState(false);
  const [kids, setKids] = useState(true);
  const approvals = useContext(ApprovalContext);
  const cat = category(step.name);
  const Glyph = Icon[toolGlyph(step.name)];
  const [present, past] = describe(step.name, step.input);
  const waiting = Boolean(step.permission && !step.permission.decision);
  const denied = step.permission?.decision === "deny";
  const done = Boolean(step.result);
  const failed = done && step.result?.ok === false;
  const active = !done && !waiting && !denied && running;
  const isAgent = cat === "agent";
  const d = isAgent || waiting ? null : detail(step.name, step.input);
  const output = step.result?.summary?.trim();
  const receipt = step.result?.ok && (bareName(step.name) === "fusion_build" || bareName(step.name) === "fusion_keyframe_input") ? step : null;
  // The glyph is quiet; colour only says something: Fusion steel, a subagent's lilac, warm while it waits on you, red
  // when it failed, bright while it runs.
  const tone = failed ? "text-err" : waiting ? "text-accent-hi" : active ? "text-ink-strong" : cat === "fusion" ? "text-node-xf" : isAgent ? "text-node-mask" : "text-faint";
  const expanded = isAgent ? kids : open;
  const stepsInside = step.children.filter((c) => c.kind === "tool").length;
  const ask = waiting ? approvals?.pending.find((p) => p.request === step.permission?.request) : undefined;
  const askIndex = ask ? approvals!.pending.indexOf(ask) : -1;
  const lands = useLands();
  return (
    <li className={cx(lands && "snap-in")}>
      <button
        type="button"
        onClick={() => (isAgent ? setKids(!kids) : setOpen(!open))}
        aria-expanded={expanded}
        data-tip={d && !open && !ask ? d : undefined}
        data-tip-mono=""
        data-tip-pos="below"
        data-tip-align="start"
        className="log-row group"
      >
        <span className={cx("log-glyph step-glyph transition-colors duration-300", tone)}>
          <Glyph size={13} />
        </span>
        <span
          className={cx(
            "min-w-0 truncate transition-colors duration-150",
            denied ? "text-faint line-through decoration-faint/50" : active || waiting || ask ? "text-ink-strong" : failed ? "text-ink" : "text-muted group-hover:text-ink",
          )}
        >
          {done || denied ? past : present}
        </span>
        <span className="min-w-3 flex-1" />
        <span className="flex shrink-0 items-center gap-1.5 text-[11.5px]">
          {isAgent && !kids && stepsInside > 0 && <span className="text-faint">{stepsInside === 1 ? "1 step" : `${stepsInside} steps`}</span>}
          {waiting && !ask && <span className="text-accent-hi">Needs approval</span>}
          {denied && <span className="text-faint">Declined</span>}
          {active && <StepClock since={step.ts} />}
          {failed && <span className="text-err">Failed</span>}
          <Icon.Chevron
            size={11}
            className={cx(
              "text-faint transition duration-200 ease-out",
              expanded ? "rotate-180 opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100",
            )}
          />
        </span>
      </button>
      {ask && approvals && (
        <InlineApproval
          key={ask.request}
          pending={ask}
          input={ask.input && Object.keys(ask.input).length ? ask.input : step.input}
          active={askIndex === 0}
          since={ask.ts}
          count={approvals.pending.length}
          onDecide={(dd) => approvals.decide(ask.request, dd)}
        />
      )}
      {failed && output && (
        <Fold open={!open}>
          <OneLine text={output} className="pb-1 pl-[var(--log-indent)] text-[12px] text-err/90" />
        </Fold>
      )}
      {step.result?.image && (
        <Fold open={!open}>
          <Zoomable src={step.result.image} caption={done ? past : present} className="mt-0.5 mb-2 ml-[var(--log-indent)] block rounded-[5px]">
            <img src={step.result.image} alt="Frame from the timeline" className="block aspect-video w-40 rounded-[5px] object-cover" />
          </Zoomable>
        </Fold>
      )}
      {!isAgent && (
        <Fold open={open}>
          <StepDetail step={step} />
        </Fold>
      )}
      {isAgent && step.children.length > 0 && (
        <Fold open={kids}>
          <div className="pt-0.5 pb-1 pl-[var(--log-indent)]">
            <Transcript items={step.children} running={running && !done} depth={depth + 1} />
          </div>
        </Fold>
      )}
      {isAgent && done && output && !(kids && step.children.some((c) => c.kind === "text")) && (
        <OneLine text={output} className="pb-1.5 pl-[var(--log-indent)] text-[12px] leading-[1.5] text-muted hover:text-ink" />
      )}
      {receipt && <NodeReceipt step={receipt} />}
    </li>
  );
}

/** Pretty-printed JSON with quiet token colors; falls back to plain text when it isn't JSON. */
function JsonBlock({ value, text, error, className }: { value?: unknown; text?: string; error?: boolean; className?: string }) {
  let v: unknown = value;
  let raw: string | null = null;
  if (text !== undefined) {
    try {
      v = JSON.parse(text);
    } catch {
      raw = text;
    }
  }
  const base = cx("overflow-auto px-2.5 py-2 tc text-[10.5px] leading-[1.6] whitespace-pre-wrap break-words", className);
  if (raw !== null) return <pre className={cx(base, error ? "text-err" : "text-ink")}>{raw}</pre>;
  if (v && typeof v === "object" && Object.keys(v as object).length === 0) return <pre className={cx(base, "text-faint")}>No arguments</pre>;
  const src = JSON.stringify(v, null, 2) ?? "";
  const out: ReactNode[] = [];
  const re = /("(?:[^"\\]|\\.)*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(src))) {
    if (m.index > last) out.push(<span key={k++} className="text-faint">{src.slice(last, m.index)}</span>);
    if (m[1] && m[2]) {
      out.push(<span key={k++} className="text-json-key">{m[1]}</span>);
      out.push(<span key={k++} className="text-faint">{m[2]}</span>);
    } else if (m[1]) out.push(<span key={k++} className={error ? "text-err" : "text-json-str"}>{m[1]}</span>);
    else if (m[3]) out.push(<span key={k++} className={m[3] === "null" ? "text-faint" : m[3] === "false" && error ? "text-err" : "text-json-lit"}>{m[3]}</span>);
    else out.push(<span key={k++} className="text-json-lit">{m[4]}</span>);
    last = m.index + m[0].length;
  }
  if (last < src.length) out.push(<span key={k++} className="text-faint">{src.slice(last)}</span>);
  return <pre className={base}>{out}</pre>;
}

/**
 * An opened step: what happened in plain words (the outcome, what it did in editor terms, the error), then the raw
 * request and response one click further. Set as indented text under the row; only the JSON sits in a well.
 */
function StepDetail({ step }: { step: Step }) {
  const [copied, setCopied] = useState(false);
  const [raw, setRaw] = useState(false);
  const r = step.result;

  let verified = false;
  try {
    verified = Boolean(r?.summary && JSON.parse(r.summary)?.result?.verified);
  } catch {
    verified = false;
  }
  const plan = approvalWhat(step.name, step.input);
  const link = "text-faint transition-colors duration-150 ease-out hover:text-ink";
  return (
    <div className="pt-0.5 pb-2.5 pl-[var(--log-indent)] text-[12px] leading-[1.55]">
      {r?.image && (
        <Zoomable src={r.image} caption={describe(step.name, step.input)[1]} className="mb-2 block w-full rounded-[5px]">
          <img src={r.image} alt="Frame from the timeline" className="block aspect-video w-full rounded-[5px] object-cover" />
        </Zoomable>
      )}
      <p className={!r ? "text-muted" : r.ok ? "text-ink" : "text-err"}>
        {!r ? "Running" : r.ok ? (verified ? "Done. Resolve confirmed it." : "Done.") : "Failed."}
        {r && <span className="text-faint tabular-nums"> {secs(step.ts, r.ts)}</span>}
      </p>
      {plan && <p className="text-muted">{linkify(plan, { clips: true })}</p>}
      {r && !r.ok && r.summary && <p className="break-words text-err/90">{r.summary}</p>}
      <p className="mt-1 flex items-center gap-3 text-[11.5px]">
        <button type="button" onClick={() => setRaw(!raw)} aria-expanded={raw} className={link}>
          {raw ? "Hide request" : "Request and response"}
        </button>
        <button
          type="button"
          onClick={() => {
            copyText(`${JSON.stringify(step.input, null, 2)}\n\n${r?.summary ?? ""}`);
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          }}
          className={cx(link, copied && "text-ok hover:text-ok")}
        >
          {copied ? "Copied" : "Copy"}
        </button>
        {raw && <span className="truncate text-faint/70">{bareName(step.name)}</span>}
      </p>
      <Fold open={raw}>
        <div className="mt-1.5 overflow-hidden rounded-[6px] bg-well/80">
          <JsonBlock value={step.input} className="max-h-48" />
          {r && (r.summary ? <JsonBlock text={r.summary} error={!r.ok} className="max-h-64 border-t border-white/[0.04]" /> : <p className="px-2.5 py-2 text-[11px] text-faint">No output</p>)}
        </div>
      </Fold>
    </div>
  );
}

/** Allow / Decline right under the step that asked, so a request appears once. */
function InlineApproval({
  pending,
  input,
  active,
  count,
  onDecide,
}: {
  pending: Pending;
  input: any;
  active: boolean;
  since?: number;
  count: number;
  onDecide: (d: Decision) => void;
}) {
  const sc = useShortcuts();
  const ctx = useContext(ApprovalContext);
  useApprovalKeys(active, onDecide);
  return (
    <ApprovalStrip
      pending={pending}
      input={input}
      active={active}
      count={count}
      onDecide={onDecide}
      timeline={ctx?.timeline}
      copy={ctx?.copy}
      allowKey={sc.allow}
      className="pt-0.5 pb-3 pl-[var(--log-indent)]"
    />
  );
}

/**
 * An approval is a sentence and two actions, set under the step that asks: what will change, with its values ("Zoom
 * 100% → 115% · frames 12–end"), where ("On “Main”"), then Allow and Decline. "Always allow" is a quiet link at the
 * end of the row; the raw request is one click away. No card, no tint, no icon.
 */
function ApprovalStrip({
  pending,
  input,
  active,
  count,
  onDecide,
  timeline,
  copy,
  allowKey,
  className,
  title,
}: {
  pending: Pending;
  input: any;
  active: boolean;
  count: number;
  onDecide: (d: Decision) => void;
  timeline?: string;
  copy?: boolean;
  allowKey: string;
  className?: string;
  /** Shown when the request has no step row of its own (the docked fallback). */
  title?: string;
}) {
  const name = bareName(pending.tool);
  const resolveTool = isResolveTool(pending.tool);
  // One sentence: what changes and, for project edits, where ("Cut pauses longer than 0.6 s on a copy of “Main”").
  const sentence = approvalSentence(pending.tool, input, resolveTool ? { timeline, copy } : undefined);
  const plan = sentence.text;
  const graph = name === "fusion_build" ? graphFromBuild(input) : null;
  const risky = RISKY.includes(name);
  const resolve = isResolveTool(pending.tool);
  const [raw, setRaw] = useState(false);
  const where = sentence.placed
    ? null
    : resolve
      ? timeline
        ? `${copy ? "On a copy of" : "On"} “${timeline}”`
        : copy
          ? "On a copy of the timeline"
          : null
      : risky
        ? "Runs on your Mac"
        : "Changes a file on your Mac";
  return (
    <div role="alertdialog" aria-label={`Approve: ${plan ?? describe(pending.tool)[0]}`} className={cx("rise", className)}>
      {title && <p className="mb-0.5 text-[12.5px] text-ink-strong">{title}</p>}
      {plan && <p className="text-[13px] leading-[1.5] break-words text-ink-strong">{withValues(plan)}</p>}
      {graph && (
        <div className="pt-1 pb-0.5">
          <NodeChain chips={graph.chips} />
        </div>
      )}
      {(risky || where || count > 1) && (
        <p className="text-[12px] leading-[1.5] text-faint">
          {risky && <span className="text-err">Hard to undo. </span>}
          {[where, count > 1 && `1 of ${count} waiting`].filter(Boolean).join(" · ")}
        </p>
      )}
      <Fold open={raw}>
        <div className="mt-1.5 overflow-hidden rounded-[6px] bg-well/80">
          <JsonBlock value={input} className="max-h-40" />
        </div>
      </Fold>
      <div className="mt-2 flex items-center gap-1 text-[12.5px]">
        <button
          type="button"
          onClick={() => onDecide("allow")}
          className="flex h-7 items-center gap-2 rounded-[7px] bg-accent px-3 font-medium text-accent-ink transition-[background-color,transform] duration-150 ease-out hover:bg-accent-hi active:scale-[0.98]"
        >
          Allow
          {active && <span className="text-[11px] font-normal opacity-55">{formatCombo(allowKey)}</span>}
        </button>
        <button type="button" onClick={() => onDecide("deny")} className="h-7 rounded-[7px] px-2.5 text-ink transition-colors duration-150 ease-out hover:bg-white/[0.06]">
          Decline
        </button>
        <span className="flex-1" />
        <button type="button" onClick={() => setRaw(!raw)} aria-expanded={raw} className="h-7 shrink-0 px-1 text-[11.5px] text-faint transition-colors duration-150 ease-out hover:text-ink">
          {raw ? "Hide request" : "Request"}
        </button>
        <button
          type="button"
          onClick={() => onDecide("always")}
          className="h-7 min-w-0 truncate px-1 text-[11.5px] text-faint transition-colors duration-150 ease-out hover:text-ink"
          title={`Allow ${allowScope(pending.tool)} from now on without asking. Undo in Settings.`}
        >
          Always allow {allowScope(pending.tool)}
        </button>
      </div>
    </div>
  );
}

/** Numbers, percentages and ranges inside a sentence, in tabular figures so they line up ("Zoom 100% → 115%"). */
function withValues(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\d[\d.,:%]*(?:\s*[→–-]\s*(?:\d[\d.,:%]*|end))?/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(
      <span key={k++} className="tabular-nums">
        {m[0]}
      </span>,
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Allow / Decline shortcuts for the first waiting request; neither acts while the editor is typing. */
function useApprovalKeys(active: boolean, onDecide: (d: Decision) => void) {
  const cb = useRef(onDecide);
  cb.current = onDecide;
  const sc = useShortcuts();
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (isRecording() || e.defaultPrevented) return;
      // Never decide while the editor is typing: a correction in progress must not approve (or decline) the edit
      // it is correcting. Esc in the composer only leaves the composer.
      const typing = document.activeElement as HTMLTextAreaElement | HTMLInputElement | null;
      const inField = Boolean(typing && ["TEXTAREA", "INPUT"].includes(typing.tagName));
      if (matches(e, sc.allow)) {
        if (inField && typing!.value.trim()) return;
        e.preventDefault();
        cb.current("allow");
      } else if (matches(e, sc.decline)) {
        if (inField || document.body.dataset.popover) return;
        e.preventDefault();
        cb.current("deny");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, sc]);
}

/** Minutes and seconds since the turn started ("0:14"), ticking once a second. */
function RunClock({ since }: { since?: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const start = since ?? Date.now() / 1000;
    const draw = () => {
      const t = Math.max(0, Math.floor(Date.now() / 1000 - start));
      if (ref.current) ref.current.textContent = `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
    };
    draw();
    const id = setInterval(draw, 1000);
    return () => clearInterval(id);
  }, [since]);
  return <span ref={ref} className="text-faint tabular-nums" />;
}

/** The last line of a running turn: that it's working, and for how long. No lamp, no timecode costume. */
export function WorkingRow({ since, waiting }: { since?: number; waiting: boolean }) {
  return (
    <div className="rise mt-2.5 flex h-6 items-center gap-1.5 text-[12.5px]" role="status" aria-live="polite">
      <span className="text-muted">{waiting ? "Waiting for you" : "Working"}</span>
      <RunClock since={since} />
    </div>
  );
}

// ----------------------------------------------------------- context window

/** Tokens, as editors read them: 940, 41k, 1.2M. */
export function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1).replace(/\.0$/, "")}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(Math.round(n));
}
export const usagePct = (u?: ContextUsage | null) => (u && u.window ? Math.min(100, Math.round((u.used / u.window) * 100)) : null);

/** Where the chat was summarized to free context: a quiet divider ("Context compacted · 37k → 9k"). */
export function CompactedRow({ e }: { e: ChatEvent }) {
  const pre = Number(e.pre_tokens);
  const post = Number(e.post_tokens);
  const sizes = pre > 0 ? ` · ${tokens(pre)}${post > 0 ? ` → ${tokens(post)}` : ""}` : "";
  return (
    <div className="flex items-center gap-3 text-[11.5px] text-faint" role="separator" aria-label="Context compacted">
      <span className="h-px flex-1 bg-[linear-gradient(to_right,transparent,var(--color-line-2))]" />
      <span className="shrink-0">
        Context compacted{e.trigger === "auto" ? " automatically" : ""}
        {sizes}
      </span>
      <span className="h-px flex-1 bg-[linear-gradient(to_left,transparent,var(--color-line-2))]" />
    </div>
  );
}

/** While the agent summarizes its context: in place of the working line. */
export function CompactingRow() {
  return (
    <div className="rise mt-2.5 flex h-6 items-center text-[12.5px] text-muted" role="status" aria-live="polite">
      Compacting the context…
    </div>
  );
}

/** The model menu's context section: how full the window is, a hairline meter, and the compact action. */
function ContextSection({
  usage,
  compact,
}: {
  usage?: ContextUsage | null;
  compact?: { blocked: string | null; busy: boolean; onCompact: (focus?: string) => void } | null;
}) {
  const [focus, setFocus] = useState("");
  const [asking, setAsking] = useState(false);
  const pct = usagePct(usage);
  if (!usage && !compact) return null;
  const tone = pct == null ? "bg-accent/70" : pct >= 90 ? "bg-err" : pct >= 70 ? "bg-warn" : "bg-accent/80";
  return (
    <div className="px-4 pt-3 pb-3">
      <div className="flex items-baseline gap-1.5 text-[12px]">
        <span className="flex-1 text-ink">Context</span>
        <span className="text-muted tabular-nums">
          {usage ? (
            <>
              {tokens(usage.used)}
              {usage.window ? ` of ${tokens(usage.window)}` : " tokens"}
              {pct != null && <span className="text-faint"> · {pct}%</span>}
            </>
          ) : (
            <span className="text-faint">Not measured yet</span>
          )}
        </span>
      </div>
      {pct != null && (
        <div className="mt-2 h-[2px] overflow-hidden rounded-full bg-white/[0.08]" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="Context window used">
          <div className={cx("h-full rounded-full transition-[width] duration-500 ease-out", tone)} style={{ width: `${Math.max(2, pct)}%` }} />
        </div>
      )}
      {compact && (
        <div className="mt-2">
          {asking && !compact.blocked ? (
            <input
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  compact.onCompact(focus.trim() || undefined);
                }
              }}
              autoFocus
              aria-label="What to keep when compacting"
              placeholder="What to keep, then ↵"
              className="h-7 w-full rounded-[6px] bg-white/[0.05] px-2.5 text-[12px] text-ink-strong outline-none placeholder:text-faint"
            />
          ) : (
            <p className="flex items-center gap-3 text-[12px]">
              <button
                type="button"
                disabled={Boolean(compact.blocked) || compact.busy}
                onClick={() => compact.onCompact(focus.trim() || undefined)}
                className="text-accent-hi transition-colors duration-150 ease-out hover:text-accent disabled:text-faint"
              >
                {compact.busy ? "Compacting…" : "Compact now"}
              </button>
              {!compact.blocked && !compact.busy && (
                <button type="button" onClick={() => setAsking(true)} className="text-faint transition-colors duration-150 ease-out hover:text-ink">
                  Compact with a focus…
                </button>
              )}
            </p>
          )}
          <p className="mt-1 text-[11.5px] leading-snug text-faint">{compact.blocked ?? "Summarizes the chat so far to free space; the transcript stays."}</p>
        </div>
      )}
    </div>
  );
}

// ----------------------------------------------------------------- task list

/** A task's mark, as Things draws it: a hollow ring, the current one ringed warm, a done one a quiet check. */
function TaskMark({ status }: { status: Task["status"] }) {
  if (status === "completed")
    return (
      <span className="grid size-[14px] place-items-center text-ok/80" aria-hidden="true">
        <Icon.Check size={13} strokeWidth={1.8} />
      </span>
    );
  return (
    <span className="grid size-[14px] place-items-center" aria-hidden="true">
      <span className={cx("size-[11px] rounded-full", status === "in_progress" ? "border-[1.5px] border-accent" : "border border-line-3")} />
    </span>
  );
}

function TaskRows({ tasks }: { tasks: Task[] }) {
  return (
    <ol className="pt-1 pb-0.5">
      {tasks.map((t) => {
        const doing = t.status === "in_progress";
        const done = t.status === "completed";
        return (
          <li key={t.id} className="flex min-h-6 items-start gap-2 py-[3px] text-[12.5px] leading-[18px]">
            <span className="mt-[2px]">
              <TaskMark status={t.status} />
            </span>
            <span className={cx("min-w-0 flex-1 break-words", done ? "text-faint" : doing ? "text-ink-strong" : "text-muted")}>
              {doing && t.active_form ? t.active_form : t.subject}
              <span className="sr-only">{done ? " (done)" : doing ? " (in progress)" : ""}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The agent's own task list, drawn like a Things checklist. While a turn runs it sits above the composer as one line,
 * the task in hand and "2 of 5", the whole list a click away. Afterwards the final list stays in the transcript where
 * it last changed, folded to one log line.
 */
export function TaskCard({ tasks, live }: { tasks: Task[]; live: boolean }) {
  const [open, setOpen] = useState(false);
  const done = tasks.filter((t) => t.status === "completed").length;
  const doing = tasks.find((t) => t.status === "in_progress");
  const all = done === tasks.length;
  const headline = live ? (doing ? doing.active_form || doing.subject : all ? "Wrapping up" : "Planning the next step") : all ? `All ${tasks.length} tasks done` : `${done} of ${tasks.length} tasks done`;
  return (
    <div role={live ? "status" : undefined}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className={cx("log-row group", live ? "text-ink" : "text-muted hover:text-ink")}>
        <span className="log-glyph">
          {live ? <TaskMark status={doing ? "in_progress" : all ? "completed" : "pending"} /> : <Icon.List size={13} className="text-faint" />}
        </span>
        <span className="min-w-0 truncate">{headline}</span>
        <span className="min-w-3 flex-1" />
        {live && <span className="shrink-0 text-[11.5px] text-faint tabular-nums">{done} of {tasks.length}</span>}
        <Icon.Chevron size={11} className={cx("ml-1.5 shrink-0 text-faint transition-transform duration-200 ease-out", live ? !open && "rotate-180" : open && "rotate-180")} />
      </button>
      <Fold open={open}>
        <TaskRows tasks={tasks} />
      </Fold>
    </div>
  );
}

// ---------------------------------------------------------- Fusion node chain

const FAMILY_COLOR: Record<string, string> = {
  io: "var(--color-node-io)",
  xf: "var(--color-node-xf)",
  merge: "var(--color-node-merge)",
  filter: "var(--color-node-filter)",
  mask: "var(--color-node-mask)",
  gen: "var(--color-node-gen)",
  text: "var(--color-node-text)",
  color: "var(--color-node-color)",
};

/**
 * The chain as a line of type, the way you'd sketch it in a notebook: In ─ ● Shake ─ ● Split ─ … ─ Out. Each node
 * is its name after a small dot in its family's colour; hairline wires join them and draw in. It wraps like a
 * sentence instead of scrolling inside a box.
 */
export function NodeChain({ chips }: { chips: NodeChip[] }) {
  const wire = (i: number) => <span aria-hidden="true" className="wire-draw mx-1.5 h-px w-3 shrink-0 bg-line-3" style={{ animationDelay: `${i * 70}ms` }} />;
  return (
    <p className="flex flex-wrap items-center gap-y-1 text-[12px] leading-[18px]" aria-label={`Node chain: ${chips.map((c) => c.name).join(", ")}`}>
      <span className="text-faint">In</span>
      {chips.map((c, i) => (
        <span key={c.name + i} className="flex items-center">
          {wire(i)}
          <span className="flex items-center gap-1.5 text-ink" title={`${c.name} · ${c.type}`}>
            <span aria-hidden="true" className="size-[6px] shrink-0 rounded-full" style={{ background: FAMILY_COLOR[nodeFamily(c.type)] }} />
            {c.name}
          </span>
        </span>
      ))}
      {wire(chips.length)}
      <span className="text-faint">Out</span>
    </p>
  );
}

/** One ruler per animated input: its name, a hairline with diamonds at the keyed frames, and the span in frames. */
function KeyRulers({ chips, span }: { chips: NodeChip[]; span: number }) {
  const animated = chips.filter((c) => c.keys.length > 0).slice(0, 4);
  if (!animated.length) return null;
  return (
    <div className="grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-1.5 text-[11.5px]">
      {animated.map((c, row) => {
        const color = FAMILY_COLOR[nodeFamily(c.type)];
        return (
          <div key={c.name} className="contents">
            <span className="max-w-32 truncate text-muted" title={`${c.name} · ${c.animated.join(", ")}`}>
              {c.name} <span className="text-faint">{c.animated.join(", ")}</span>
            </span>
            <span className="relative h-3">
              <span className="absolute inset-x-0 top-1/2 h-px bg-white/[0.09]" />
              {c.keys.map((f, i) => (
                <span
                  key={f}
                  className="key-in absolute top-1/2 -mt-[3px] -ml-[3px] size-[6px] rotate-45 rounded-[1px]"
                  style={{ left: `${span ? (f / span) * 100 : 0}%`, background: color, animationDelay: `${160 + row * 70 + i * 50}ms` }}
                  title={`${c.name} · frame ${f}`}
                />
              ))}
            </span>
            <span className="text-faint tabular-nums">{row === animated.length - 1 ? `${span}f` : ""}</span>
          </div>
        );
      })}
    </div>
  );
}

/** What a Fusion step built, as a small diagram under its row: the chain, then a ruler per animated input. */
function NodeReceipt({ step }: { step: Step }) {
  const name = bareName(step.name);
  const graph = name === "fusion_build" ? graphFromBuild(step.input) : graphFromKeyframe(step.input);
  if (!graph || graph.chips.length === 0) return null;
  return (
    <div className="rise space-y-2 pt-1 pb-3 pl-[var(--log-indent)]">
      {name === "fusion_build" && <NodeChain chips={graph.chips} />}
      <KeyRulers chips={graph.chips} span={graph.span} />
    </div>
  );
}

// -------------------------------------------------------------- approval card

/** Fallback for a request that has no matching step in the transcript: the same sentence, docked above the composer. */
export function ApprovalCard({ pending, count, onDecide }: { pending: Pending; count: number; onDecide: (d: Decision) => void }) {
  const sc = useShortcuts();
  const actx = useContext(ApprovalContext);
  useApprovalKeys(true, onDecide);
  return (
    <ApprovalStrip
      pending={pending}
      input={pending.input}
      active
      count={count}
      onDecide={onDecide}
      timeline={actx?.timeline}
      copy={actx?.copy}
      allowKey={sc.allow}
      title={describe(pending.tool, pending.input)[0]}
      className="px-1"
    />
  );
}

function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return <kbd className={cx("kc", className)}>{children}</kbd>;
}

// ----------------------------------------------------------------- composer

const MODELS = [
  { id: "default", label: "Default", hint: "Claude Code’s own setting" },
  { id: "fable", label: "Fable", hint: "Most capable" },
  { id: "opus", label: "Opus", hint: "Deep, careful work" },
  { id: "sonnet", label: "Sonnet", hint: "Fast and capable" },
  { id: "haiku", label: "Haiku", hint: "Fastest, for quick questions" },
];
const EFFORTS = [
  { id: "low", label: "Low", hint: "Fastest, for small tweaks" },
  { id: "medium", label: "Med", hint: "Plans briefly first" },
  { id: "high", label: "High", hint: "Plans each edit" },
  { id: "xhigh", label: "X-High", hint: "For multi-node builds" },
  { id: "max", label: "Max", hint: "Thinks longest, slowest" },
];

export type Draft = { attachments: (Attachment & { preview?: string })[]; refs: MediaRef[] };

/** An icon-only toolbar control that opens a menu above the composer; its tooltip names it and its state. */
function ToolbarButton({
  open,
  onClick,
  label,
  children,
  className,
  tip,
}: {
  open: boolean;
  onClick: () => void;
  label: string;
  children: ReactNode;
  className?: string;
  tip?: Tip;
}) {
  return (
    <button
      type="button"
      data-popover-trigger
      onClick={onClick}
      aria-label={label}
      data-tip={label}
      data-tip-align={tip?.align}
      aria-haspopup="menu"
      aria-expanded={open}
      className={cx(
        "flex h-8 min-w-8 shrink-0 items-center justify-center gap-1.5 rounded-lg px-1.5 text-[12px] whitespace-nowrap transition-colors duration-150",
        open ? "bg-raised-2 text-ink-strong" : "text-faint hover:bg-raised-2 hover:text-ink",
        className,
      )}
    >
      {children}
    </button>
  );
}

/**
 * The toolbar is icons only. If the panel is too narrow even for that, `data-fit="1"` drops the model name and
 * leaves the reasoning meter. Runs before paint on resize and when a label changes.
 */
function useToolbarFit(ref: React.RefObject<HTMLDivElement | null>, key: string) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fit = () => {
      if (!el.clientWidth) return;
      el.dataset.fit = "";
      if (el.scrollWidth > el.clientWidth + 0.5) el.dataset.fit = "1";
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, key]);
}

/** "/" opens the file picker and "$" the skill picker when typed at the start of a word. */
type Trigger = { kind: "file" | "skill"; start: number; query: string };
type Cmd = { cmd: "compact"; name: string };
const COMPACT_CMD: Cmd = { cmd: "compact", name: "Compact context" };

function findTrigger(text: string, caret: number): Trigger | null {
  const m = /(^|\s)([/$])([^\s/$]*)$/.exec(text.slice(0, caret));
  if (!m) return null;
  return { kind: m[2] === "/" ? "file" : "skill", start: caret - m[3].length - 1, query: m[3] };
}

/** Reasoning as a five-stop slider (Low … Max) that snaps; arrow keys step it. */
function EffortSlider({ value, onChange }: { value: number; onChange: (i: number) => void }) {
  const pct = (value / (EFFORTS.length - 1)) * 100;
  return (
    <div className="px-4 pt-3 pb-3">
      <div className="flex items-center gap-1.5 text-[12px]">
        <span className="flex-1 text-ink">Reasoning</span>
        <span className="font-medium text-ink-strong">{EFFORTS[value].label}</span>
      </div>
      <div className="relative mt-2.5 h-6">
        <div aria-hidden="true" className="pointer-events-none absolute inset-x-[7px] top-1/2 h-[2px] -translate-y-1/2 rounded-full bg-white/[0.1]">
          <div className="h-full rounded-full bg-accent/80" style={{ width: `${pct}%` }} />
          {EFFORTS.map((e, i) => (
            <span
              key={e.id}
              className={cx("absolute top-1/2 h-[6px] w-px -translate-x-1/2 -translate-y-1/2", i <= value ? "bg-accent/70" : "bg-white/[0.18]")}
              style={{ left: `${(i / (EFFORTS.length - 1)) * 100}%` }}
            />
          ))}
        </div>
        <input
          type="range"
          min={0}
          max={EFFORTS.length - 1}
          step={1}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          aria-label="Reasoning"
          aria-valuetext={EFFORTS[value].label}
          className="effort-range absolute inset-0 w-full"
        />
      </div>
    </div>
  );
}

/** A "/" result's poster: the server's thumbnail for media pool clips and found video/image files (demo posters in the demo). */
export function fileThumb(f: { thumb_id?: string }): string | undefined {
  if (!f.thumb_id) return undefined;
  return DEMO ? demoPosters[f.thumb_id] : thumbUrl(f.thumb_id);
}

/** The "/" and "$" pickers: an inline list over the composer that filters as you type. */
function TriggerList({
  trigger,
  items,
  active,
  loading,
  onPick,
  onHover,
}: {
  trigger: Trigger;
  items: (FileItem | Skill | Cmd)[];
  active: number;
  loading: boolean;
  onPick: (i: number) => void;
  onHover: (i: number) => void;
}) {
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);
  const file = trigger.kind === "file";
  return (
    <div className="pop absolute inset-x-0 bottom-full z-40 mb-2 rounded-lg border border-white/[0.07] bg-raised shadow-[0_24px_48px_-16px_rgba(0,0,0,0.85)]" style={{ ["--pop-origin" as any]: "bottom left" }}>
      <p className="flex items-center gap-1.5 px-4 pt-2.5 pb-1 text-[11.5px] text-faint">
        {file ? (items[0] && "cmd" in items[0] ? "Commands, clips and files" : "Attach a clip or file") : "Use a skill"}
        {trigger.query && <span className="truncate text-muted">“{trigger.query}”</span>}
      </p>
      <div ref={list} role="listbox" aria-label={file ? "Files" : "Skills"} className="max-h-60 overflow-y-auto p-1.5 pt-0.5">
        {items.length === 0 && <p className="px-2 py-2 text-[12px] text-muted">{loading ? "Looking…" : file ? "No matching clips or files" : "No matching skills"}</p>}
        {items.map((it, i) => {
          if ("cmd" in it)
            return (
              <button
                key="cmd-compact"
                type="button"
                role="option"
                aria-selected={i === active}
                data-i={i}
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => onHover(i)}
                onClick={() => onPick(i)}
                className={cx("flex h-9 w-full items-center gap-2.5 rounded-lg px-2 text-left transition-colors duration-150 ease-out", i === active && "bg-white/[0.06]")}
              >
                <span className="grid w-[44px] shrink-0 place-items-center text-faint">
                  <Icon.Layers size={13} />
                </span>
                <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink-strong">{it.name}</span>
                <span className="shrink-0 text-[11.5px] text-faint">/compact</span>
              </button>
            );
          const f = file ? (it as FileItem) : null;
          const k = !file ? (it as Skill) : null;
          const Glyph = f ? (f.kind === "media" ? Icon.Film : f.kind === "recent" ? Icon.History : /\.(png|jpe?g|gif|webp|tiff?)$/i.test(f.name) ? Icon.Image : Icon.File) : Icon.Bolt;
          return (
            <button
              key={(f?.path ?? k?.name ?? "") + i}
              type="button"
              role="option"
              aria-selected={i === active}
              data-i={i}
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => onHover(i)}
              onClick={() => onPick(i)}
              title={f?.path ?? k?.description}
              className={cx("flex w-full items-center gap-2.5 rounded-lg px-2 text-left transition-colors duration-150 ease-out", f ? "h-11" : "h-9", i === active && "bg-white/[0.06]")}
            >
              {f ? (
                <Poster
                  key={f.thumb_id ?? f.path}
                  src={fileThumb(f)}
                  type={f.kind === "media" ? "video" : undefined}
                  kind={f.kind === "media" ? "media" : /\.(png|jpe?g|gif|webp|tiff?)$/i.test(f.name) ? "image" : "file"}
                  className="h-[26px] w-[44px]"
                />
              ) : (
                <span className="grid w-4 shrink-0 place-items-center text-faint">
                  <Glyph size={13} />
                </span>
              )}
              <span className={cx("min-w-0 flex-1 truncate text-[12.5px] text-ink-strong")}>{f ? f.name : `$${k!.name}`}</span>
              <span className="max-w-[50%] min-w-0 shrink truncate text-[11px] text-faint">{f ? (f.detail ?? (f.size ? bytes(f.size) : "")) : (k!.description ?? "")}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function Composer({
  running,
  onSend,
  onStop,
  settings,
  agent,
  onSettings,
  seed,
  disabled,
  draft,
  setDraft,
  onFiles,
  onFrame,
  onPickMedia,
  busy,
  ctx,
  resetKey,
  loadFiles,
  loadSkills,
  onAttachPath,
  usage,
  compact,
}: {
  running: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  settings: Settings;
  agent: Agent | undefined;
  onSettings: (p: Partial<Settings>) => void;
  seed: { text: string; n: number } | null;
  disabled?: string | null;
  draft: Draft;
  setDraft: (d: Draft) => void;
  onFiles: (files: File[]) => void;
  onFrame: () => void;
  onPickMedia: () => void;
  busy: boolean;
  ctx: Context;
  /** Changes when the editor opens or starts a chat; the draft is cleared. */
  resetKey?: number;
  loadFiles: (q: string) => Promise<FileItem[]>;
  loadSkills: () => Promise<Skill[]>;
  onAttachPath: (item: FileItem) => void;
  /** How full the context window is (shown in the model menu). */
  usage?: ContextUsage | null;
  /** Compacting, when the agent can: why it's unavailable right now (or null), whether it's running, and how. */
  compact?: { blocked: string | null; busy: boolean; onCompact: (focus?: string) => void } | null;
}) {
  const sc = useShortcuts();
  const [text, setText] = useState("");
  useEffect(() => {
    if (resetKey) setText("");
  }, [resetKey]);
  const [menu, setMenu] = useState<null | "attach" | "model" | "access">(null);
  const ref = useRef<HTMLTextAreaElement>(null);
  const mirror = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const connected = Boolean(ctx.connected);
  const card = useRef<HTMLDivElement>(null);
  const [sent, setSent] = useState(0);

  // ---- "/" and "$" pickers
  const [trigger, setTrigger] = useState<Trigger | null>(null);
  const dismissed = useRef<number | null>(null);
  const [fileItems, setFileItems] = useState<FileItem[]>([]);
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(0);
  const readTrigger = (value: string, caret: number) => {
    const t = findTrigger(value, caret);
    if (t && dismissed.current === t.start) return setTrigger(null);
    if (!t) dismissed.current = null;
    setTrigger(t);
  };
  useEffect(() => setActive(0), [trigger?.kind, trigger?.query]);
  useEffect(() => {
    if (trigger?.kind !== "file") return;
    setLoading(true);
    let live = true;
    const t = setTimeout(() => {
      loadFiles(trigger.query)
        .then((items) => live && setFileItems(items.slice(0, 50)))
        .catch(() => live && setFileItems([]))
        .finally(() => live && setLoading(false));
    }, 120);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [trigger?.kind, trigger?.query, loadFiles]);
  useEffect(() => {
    if (trigger?.kind !== "skill" || skills) return;
    setLoading(true);
    loadSkills()
      .then(setSkills, () => setSkills([]))
      .finally(() => setLoading(false));
  }, [trigger?.kind, skills, loadSkills]);
  const skillItems = useMemo(() => {
    const n = (trigger?.query ?? "").toLowerCase();
    return (skills ?? []).filter((k) => !n || `${k.name} ${k.description ?? ""}`.toLowerCase().includes(n)).slice(0, 50);
  }, [skills, trigger?.query]);
  // "/compact" as a command at the top of the "/" picker, at the start of a message, for agents that can compact.
  const showCompact = Boolean(compact && trigger?.kind === "file" && trigger.start === 0 && "compact".startsWith((trigger.query ?? "").toLowerCase()));
  const pickerItems: (FileItem | Skill | Cmd)[] =
    trigger?.kind === "file" ? [...(showCompact ? [COMPACT_CMD] : []), ...fileItems] : trigger?.kind === "skill" ? skillItems : [];
  // Where the caret goes after a programmatic edit; applied right after React writes the new value.
  const pendingCaret = useRef<number | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && pendingCaret.current != null) {
      el.focus();
      el.setSelectionRange(pendingCaret.current, pendingCaret.current);
      pendingCaret.current = null;
    }
  }, [text]);
  const replaceTrigger = (t: Trigger, insert: string) => {
    const el = ref.current;
    const caret = el?.selectionStart ?? text.length;
    const next = text.slice(0, t.start) + insert + text.slice(caret);
    pendingCaret.current = t.start + insert.length;
    setText(next);
    setTrigger(null);
  };
  const choose = (i: number) => {
    if (!trigger) return;
    const it = pickerItems[i];
    if (!it) return;
    if ("cmd" in it) {
      // The command goes in as text; Enter sends it (a focus can follow it: "/compact keep the glitch").
      replaceTrigger(trigger, "/compact ");
      return;
    }
    if (trigger.kind === "file") {
      replaceTrigger(trigger, "");
      onAttachPath(it as FileItem);
    } else {
      replaceTrigger(trigger, `$${(it as Skill).name} `);
    }
  };
  // Skill tokens in the text ("$name" for a known skill) are drawn as chips by a mirror behind the textarea.
  const skillNames = useMemo(() => new Set((skills ?? []).map((k) => k.name)), [skills]);
  useEffect(() => {
    if (!skills && /\$[\w-]/.test(text)) loadSkills().then(setSkills, () => setSkills([]));
  }, [text, skills, loadSkills]);
  const mirrored = useMemo(() => {
    if (!skillNames.size || !text.includes("$")) return null;
    const parts: ReactNode[] = [];
    const re = /\$([\w.-]+)/g;
    let last = 0;
    let m: RegExpExecArray | null;
    let k = 0;
    while ((m = re.exec(text))) {
      if (!skillNames.has(m[1])) continue;
      parts.push(text.slice(last, m.index));
      parts.push(
        <mark key={k++} className="rounded-[5px] bg-accent/16 text-transparent shadow-[0_0_0_2px_color-mix(in_oklab,var(--color-accent)_16%,transparent)]">
          {m[0]}
        </mark>,
      );
      last = m.index + m[0].length;
    }
    if (!k) return null;
    parts.push(text.slice(last) + "​");
    return parts;
  }, [text, skillNames]);

  // Three lines at rest (CSS min-height); grows with the text up to 40% of the panel, then scrolls. Growing is
  // immediate (an animated grow lets the caret scroll the top line under the border mid-transition); shrinking
  // after a send or a cut eases down. Re-measured when the panel is resized.
  const firstSize = useRef(true);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const size = (animate: boolean) => {
      const prev = el.style.height;
      el.style.transition = "none";
      el.style.height = "0px";
      const max = Math.round(window.innerHeight * 0.4);
      const next = Math.min(el.scrollHeight, max);
      el.style.overflowY = el.scrollHeight > max ? "auto" : "hidden";
      el.style.height = prev || `${next}px`;
      void el.offsetHeight;
      const shrinking = parseFloat(prev) > next;
      el.style.transition = animate && shrinking && !matchMedia("(prefers-reduced-motion: reduce)").matches ? "height 200ms cubic-bezier(0.16, 1, 0.3, 1)" : "none";
      el.style.height = `${next}px`;
    };
    size(!firstSize.current);
    firstSize.current = false;
    let w = el.clientWidth;
    const ro = new ResizeObserver(() => {
      if (el.clientWidth === w) return;
      w = el.clientWidth;
      size(false);
    });
    ro.observe(el);
    const onWin = () => size(false);
    window.addEventListener("resize", onWin);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", onWin);
    };
  }, [text]);
  // The send press: the viewfinder marks snap shut around the composer.
  useEffect(() => {
    const el = card.current;
    if (!sent || !el) return;
    el.dataset.sent = "";
    const t = setTimeout(() => delete el.dataset.sent, 440);
    return () => clearTimeout(t);
  }, [sent]);
  useEffect(() => {
    if (seed) {
      setText(seed.text);
      ref.current?.focus();
    }
  }, [seed]);
  // Panel-wide shortcuts that belong to the composer (from Settings › Shortcuts).
  const openSkillPicker = () => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    const at = el.selectionStart ?? text.length;
    const lead = at > 0 && !/\s/.test(text[at - 1]) ? " $" : "$";
    const next = text.slice(0, at) + lead + text.slice(at);
    pendingCaret.current = at + lead.length;
    setText(next);
    dismissed.current = null;
    setTrigger({ kind: "skill", start: at + lead.length - 1, query: "" });
  };
  const keys = useRef({ sc, openSkillPicker });
  keys.current = { sc, openSkillPicker };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || isRecording()) return;
      const k = keys.current.sc;
      if (matches(e, k.focusComposer)) {
        e.preventDefault();
        ref.current?.focus();
      } else if (matches(e, k.attachFile)) {
        e.preventDefault();
        fileInput.current?.click();
      } else if (matches(e, k.pickSkill)) {
        e.preventDefault();
        keys.current.openSkillPicker();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const hasDraft = Boolean(text.trim() || draft.attachments.length || draft.refs.length);
  const steerable = agent?.id === "claude";
  const submit = () => {
    if (!hasDraft || disabled || busy) return;
    if (running && !steerable) return;
    onSend(text.trim());
    setText("");
    setTrigger(null);
    setSent((n) => n + 1);
  };
  const noApprovals = Boolean(agent && !agent.approvals);
  const full = settings.mode === "auto" || noApprovals;
  const model = MODELS.find((m) => m.id === (settings.model || "default")) ?? MODELS[0];
  const effort = EFFORTS.find((e) => e.id === settings.effort) ?? EFFORTS[2];
  const toggle = (m: typeof menu) => setMenu(menu === m ? null : m);
  const close = () => setMenu(null);
  const tc = ctxTimecode(ctx);
  const tl = ctxTimeline(ctx);
  const effortLevel = Math.max(0, EFFORTS.indexOf(effort));
  const accessLabel = noApprovals ? "No approvals" : full ? "Full access" : "Ask first";
  const bar = useRef<HTMLDivElement>(null);
  useToolbarFit(bar, [model.label, effort.label, accessLabel, settings.safe_copy, running, hasDraft].join("|"));
  const pickerOpen = Boolean(trigger) && !disabled;
  return (
    <div ref={card} className="t-composer relative rounded-2xl border transition-[border-color] duration-200 ease-out">
      {pickerOpen && trigger && (
        <TriggerList trigger={trigger} items={pickerItems} active={active} loading={loading} onPick={choose} onHover={setActive} />
      )}
      {(draft.attachments.length > 0 || draft.refs.length > 0 || busy) && (
        <div className="no-scrollbar flex gap-2 overflow-x-auto px-3 pt-3 [mask-image:linear-gradient(to_right,#000_calc(100%-20px),transparent)] after:block after:w-3 after:shrink-0 after:content-['']">
          {draft.attachments.map((a) => (
            <AttachmentChip
              key={a.id}
              name={a.name}
              meta={attachmentMeta(a)}
              kind={attachmentKind(a)}
              mediaType={a.type}
              preview={attachmentPoster(a)}
              onRemove={() => setDraft({ ...draft, attachments: draft.attachments.filter((x) => x.id !== a.id) })}
            />
          ))}
          {draft.refs.map((r) => (
            <AttachmentChip
              key={r.id ?? r.name}
              name={r.name ?? "Clip"}
              meta={r.duration ?? r.type ?? "Media pool"}
              kind="media"
              mediaType={r.type}
              preview={posterOf(r)}
              onRemove={() => setDraft({ ...draft, refs: draft.refs.filter((x) => x !== r) })}
            />
          ))}
          {busy && <span className="flex h-[38px] shrink-0 items-center px-1 text-[12px] text-muted">Attaching…</span>}
        </div>
      )}
      <div className="relative">
        {mirrored && (
          <div ref={mirror} aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden px-4 pt-3.5 pb-2.5 text-[14px] leading-[1.5] break-words whitespace-pre-wrap text-transparent">
            {mirrored}
          </div>
        )}
        <textarea
          ref={ref}
          value={text}
          rows={3}
          onChange={(e) => {
            setText(e.target.value);
            readTrigger(e.target.value, e.target.selectionStart ?? e.target.value.length);
          }}
          onSelect={(e) => {
            const el = e.currentTarget;
            if (el.selectionStart === el.selectionEnd) readTrigger(el.value, el.selectionStart);
            else setTrigger(null);
          }}
          onBlur={() => setTrigger(null)}
          onScroll={(e) => {
            if (mirror.current) mirror.current.scrollTop = e.currentTarget.scrollTop;
          }}
          onKeyDown={(e) => {
            if (pickerOpen && trigger) {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                const n = pickerItems.length;
                if (n) setActive((i) => (e.key === "ArrowDown" ? (i + 1) % n : (i - 1 + n) % n));
                return;
              }
              // "/compact" typed out in full and Enter: send it as typed.
              if (e.key === "Enter" && !e.shiftKey && showCompact && trigger.query.toLowerCase() === "compact" && !e.nativeEvent.isComposing) {
                e.preventDefault();
                setTrigger(null);
                submit();
                return;
              }
              if ((e.key === "Enter" || e.key === "Tab") && !e.shiftKey && pickerItems.length && !e.nativeEvent.isComposing) {
                e.preventDefault();
                choose(active);
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                dismissed.current = trigger.start;
                setTrigger(null);
                return;
              }
            }
            if (matches(e, sc.send) && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            } else if (e.key === "Escape") {
              // Esc leaves the composer; the draft is never wiped.
              e.preventDefault();
              e.currentTarget.blur();
            }
          }}
          onPaste={(e) => {
            const files = Array.from(e.clipboardData.files);
            if (files.length) {
              e.preventDefault();
              onFiles(files);
            }
          }}
          placeholder={disabled ?? (running ? (steerable ? "Steer it: add a note or a correction…" : "Wait for this turn, or stop it to change course") : "Describe an edit. / attaches, $ uses a skill")}
          disabled={Boolean(disabled)}
          className="relative block min-h-[calc(3*1.5em+24px)] w-full resize-none bg-transparent px-4 pt-3.5 pb-2.5 text-[14px] leading-[1.5] text-ink-strong outline-none placeholder:text-faint disabled:cursor-not-allowed"
          aria-label="Message"
          aria-expanded={pickerOpen}
          aria-haspopup="listbox"
        />
      </div>
      {running && steerable && hasDraft && (
        <p className="rise flex items-center gap-1.5 px-4 pb-1 text-[11px] text-muted">
          <Icon.Steer size={11} className="text-faint" />
          <span>
            <span className="text-ink">{formatCombo(sc.send)}</span> sends it now. Claude reads it before its next step.
          </span>
        </p>
      )}
      <input
        ref={fileInput}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          onFiles(Array.from(e.target.files ?? []));
          e.target.value = "";
        }}
      />
      <div ref={bar} className="tb flex items-center gap-0.5 px-2 pb-2">
        <div className="shrink-0">
          <span data-popover-trigger>
            <IconButton label="Attach a frame, clip or file" onClick={() => toggle("attach")} active={menu === "attach"} expanded={menu === "attach"} tip={{ align: "start" }} className="text-faint">
              <Icon.Plus />
            </IconButton>
          </span>
          <Popover open={menu === "attach"} onClose={close} up className="w-[260px]">
            <div className="py-1.5" role="menu" aria-label="Attach">
              <MenuRow
                role="menuitem"
                icon={<Icon.Frame size={13} />}
                title="Current frame"
                info={connected ? `A still of the viewer${tc ? ` at ${tc}` : ""}.` : "Start the bridge in Resolve first."}
                disabled={!connected}
                onClick={() => {
                  close();
                  onFrame();
                }}
              />
              <MenuRow
                role="menuitem"
                icon={<Icon.Film size={13} />}
                title="Media pool clip…"
                info={connected ? "Point the agent at a clip in this project." : "Start the bridge in Resolve first."}
                disabled={!connected}
                onClick={() => {
                  close();
                  onPickMedia();
                }}
              />
              <MenuRow
                role="menuitem"
                icon={<Icon.Paperclip size={13} />}
                title="Files…"
                info={`Images, LUTs, .setting files, scripts. ${formatCombo(sc.attachFile)} opens this too.`}
                onClick={() => {
                  close();
                  fileInput.current?.click();
                }}
              />
            </div>
          </Popover>
        </div>
        <div className="shrink-0">
          <ToolbarButton
            open={menu === "access"}
            onClick={() => toggle("access")}
            label={`${accessLabel}${settings.safe_copy ? " · on a copy" : ""}`}
            tip={{ align: "start" }}
            className={full ? "text-warn hover:text-warn" : undefined}
          >
            <span className="relative grid place-items-center">
              {full ? <Icon.Unlock size={14} /> : <Icon.Shield size={14} />}
              {settings.safe_copy && <span className="absolute -top-0.5 -right-1 size-[5px] rounded-full bg-accent-hi ring-2 ring-raised" />}
            </span>
          </ToolbarButton>
          <Popover open={menu === "access"} onClose={close} up className="w-[290px] max-w-[calc(100vw-2*var(--gutter))]">
            <div className="py-1.5" role="menu" aria-label="Project access">
              {noApprovals ? (
                <MenuRow
                  role="menuitemradio"
                  checked
                  icon={<Icon.Unlock size={13} />}
                  iconTone="text-warn"
                  title="Full access"
                  info={`${agent?.label ?? "This agent"} can’t ask before it edits. Switch to Claude Code in Settings for approvals.`}
                  onClick={close}
                />
              ) : (
                <>
                  <MenuRow
                    role="menuitemradio"
                    checked={!full}
                    onClick={() => {
                      onSettings({ mode: "ask" });
                      close();
                    }}
                    icon={<Icon.Shield size={13} />}
                    title="Ask first"
                    end={!full && <Icon.Check size={13} className="shrink-0 text-accent-hi" />}
                    info="Asks before each change to the project, shell command or file write. Reading never asks."
                  />
                  <MenuRow
                    role="menuitemradio"
                    checked={full}
                    onClick={() => {
                      onSettings({ mode: "auto" });
                      close();
                    }}
                    icon={<Icon.Unlock size={13} />}
                    iconTone="text-warn"
                    title="Full access"
                    end={full && <Icon.Check size={13} className="shrink-0 text-accent-hi" />}
                    info="Changes the project without asking; every step still shows. Pair it with Work on a copy."
                  />
                </>
              )}
              <div className="mx-4 my-1.5 h-px bg-white/[0.06]" />
              <MenuRow
                role="menuitemcheckbox"
                checked={settings.safe_copy}
                onClick={() => onSettings({ safe_copy: !settings.safe_copy })}
                icon={<Icon.Layers size={13} />}
                iconTone={settings.safe_copy ? "text-accent-hi" : undefined}
                title="Work on a copy"
                end={<Switch on={settings.safe_copy} />}
                info={`Claude duplicates ${tl ? `“${tl}”` : "the timeline"} before its first edit and works on the duplicate. The original is never touched.`}
              />
              <MenuRow
                role="menuitemcheckbox"
                checked={settings.checkpoints !== false}
                onClick={() => onSettings({ checkpoints: settings.checkpoints === false })}
                icon={<Icon.Rewind size={13} />}
                iconTone={settings.checkpoints !== false ? "text-accent-hi" : undefined}
                title="Restore points before edits"
                end={<Switch on={settings.checkpoints !== false} />}
                info="Saves the timeline before the first edit after each message, so Rewind can bring it back. Kept in an AI Checkpoints bin."
              />
            </div>
          </Popover>
        </div>
        <span className="min-w-1 flex-1" />
        <div className="tb-model shrink-0">
          <ToolbarButton open={menu === "model"} onClick={() => toggle("model")} label={`Model ${model.label} · Reasoning ${effort.label}`} tip={{ align: "end" }}>
            <span className="max-w-24 truncate text-ink">{model.label}</span>
            <span className="tb-model-label shrink-0 text-faint">· {effort.label}</span>
            {(usagePct(usage) ?? 0) >= 70 && <span className={cx("shrink-0 tabular-nums", usagePct(usage)! >= 90 ? "text-err" : "text-warn")}>· {usagePct(usage)}%</span>}
            <Icon.Chevron size={11} className={cx("shrink-0 text-faint transition-transform duration-200", menu === "model" && "rotate-180")} />
          </ToolbarButton>
          <Popover open={menu === "model"} onClose={close} up align="right" className="w-[264px]">
            <div className="pt-1.5" role="menu" aria-label="Model">
              {MODELS.map((m) => (
                <MenuRow
                  key={m.id}
                  role="menuitemradio"
                  checked={m.id === model.id}
                  onClick={() => onSettings({ model: m.id })}
                  title={m.label}
                  hint={m.hint}
                  end={<Icon.Check size={13} className={cx("shrink-0 text-accent-hi", m.id !== model.id && "invisible")} />}
                />
              ))}
              <div className="mx-4 mt-1.5 h-px bg-white/[0.06]" />
              <EffortSlider value={effortLevel} onChange={(i) => onSettings({ effort: EFFORTS[i].id })} />
              {(usage || compact) && <div className="mx-4 h-px bg-white/[0.06]" />}
              <ContextSection
                usage={usage}
                compact={
                  compact && {
                    ...compact,
                    onCompact: (f) => {
                      close();
                      compact.onCompact(f);
                    },
                  }
                }
              />
            </div>
          </Popover>
        </div>
        {running && (
          <button
            type="button"
            onClick={onStop}
            aria-label="Stop the agent"
            data-tip={`Stop the agent  ${formatCombo(sc.stop)}`}
            data-tip-align="end"
            className="ml-1 grid size-8 shrink-0 place-items-center rounded-full bg-white/[0.06] text-live transition-colors duration-150 ease-out hover:bg-live/15 active:bg-live/20"
          >
            <Icon.Stop size={12} />
          </button>
        )}
        {(!running || hasDraft) && (
          <button
            type="button"
            onClick={submit}
            aria-label={running ? "Send now to steer" : "Send"}
            style={running ? { marginLeft: 8 } : undefined}
            data-tip={running ? "Send now · Claude reads it before its next step" : `Send  ${formatCombo(sc.send)}`}
            data-tip-align="end"
            disabled={!hasDraft || Boolean(disabled) || busy || (running && !steerable)}
            className="ml-1 grid size-8 shrink-0 place-items-center rounded-full bg-accent text-accent-ink transition-[background-color,color,transform] duration-150 ease-out hover:bg-accent-hi active:scale-95 disabled:bg-white/[0.06] disabled:text-faint"
          >
            <span key={sent} className={cx("grid place-items-center", sent > 0 && "send-launch")}>
              <Icon.Send strokeWidth={1.8} />
            </span>
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------- media pool picker

function mediaGlyph(type?: string) {
  const t = (type ?? "").toLowerCase();
  if (/still|image|png|jpg/.test(t)) return Icon.Image;
  if (/audio/.test(t) && !/video/.test(t)) return Icon.Wave;
  if (/timeline|compound/.test(t)) return Icon.Layers;
  return Icon.Film;
}

export function MediaPicker({ load, onPick, onClose }: { load: () => Promise<MediaRef[]>; onPick: (r: MediaRef) => void; onClose: () => void }) {
  const [items, setItems] = useState<MediaRef[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    load().then(setItems, (e) => setError(e.message));
  }, [load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    document.body.dataset.popover = "1";
    window.addEventListener("keydown", onKey);
    return () => {
      delete document.body.dataset.popover;
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (items ?? []).filter((i) => !needle || `${i.name} ${i.bin}`.toLowerCase().includes(needle)).slice(0, 200);
  }, [items, q]);
  useEffect(() => setActive(0), [q]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);
  return (
    <div className="absolute inset-0 z-50 flex flex-col justify-end bg-base/85 px-(--gutter) pb-(--gutter)" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        role="dialog"
        aria-label="Attach a media pool clip"
        className="rise flex max-h-[62%] flex-col overflow-hidden rounded-xl border border-white/[0.07] bg-raised shadow-[0_-16px_40px_-16px_rgba(0,0,0,0.85)]"
      >
        <div className="flex items-center gap-2 border-b border-line px-3 py-2">
          <Icon.Search size={13} className="shrink-0 text-faint" />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setActive((n) => Math.min(n + 1, shown.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((n) => Math.max(n - 1, 0));
              } else if (e.key === "Enter" && shown[active]) {
                e.preventDefault();
                onPick(shown[active]);
              }
            }}
            placeholder="Search the media pool"
            aria-label="Search the media pool"
            className="h-6 min-w-0 flex-1 bg-transparent text-[12.5px] text-ink-strong outline-none placeholder:text-faint"
          />
          <IconButton label="Close (esc)" onClick={onClose} className="size-6">
            <Icon.X size={12} />
          </IconButton>
        </div>
        <div ref={list} className="overflow-y-auto p-1" role="listbox" aria-label="Media pool clips">
          {error && <p className="px-2 py-3 text-[12px] text-err">{error}</p>}
          {!items && !error && (
            <p className="flex items-center gap-2 px-2 py-3 text-[12px] text-muted">
              <span className="size-3 animate-spin rounded-full border border-faint border-t-transparent" />
              Reading the media pool…
            </p>
          )}
          {items && shown.length === 0 && <p className="px-2 py-3 text-[12px] text-muted">{q ? `No clips match “${q}”.` : "The media pool is empty."}</p>}
          {shown.map((m, i) => {
            return (
              <button
                key={(m.id ?? "") + i}
                data-i={i}
                type="button"
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                onClick={() => onPick(m)}
                className={cx("flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left", i === active && "bg-raised-2")}
              >
                <Poster src={posterOf(m)} type={m.type} kind="media" className="h-[32px] w-[57px]" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] text-ink-strong">{m.name}</span>
                  <span className="block truncate text-[11px] text-muted">
                    {m.bin || "Master"}
                    {m.type ? ` · ${m.type}` : ""}
                  </span>
                </span>
                {m.duration && <span className="tc shrink-0 text-[10.5px] text-faint">{m.duration}</span>}
              </button>
            );
          })}
        </div>
        {items && shown.length > 0 && (
          <p className="flex items-center gap-1.5 border-t border-line px-3 py-1.5 text-[10.5px] text-faint">
            {shown.length === 1 ? "1 clip" : `${shown.length} clips`}
            <span className="ml-auto flex items-center gap-1">
              <Kbd>↑↓</Kbd> move <Kbd>↵</Kbd> attach
            </span>
          </p>
        )}
      </div>
    </div>
  );
}

// -------------------------------------------------------------- empty state

export const NOTES_PROMPT = "$timeline-notes Do my open timeline notes.";

/**
 * The open timeline notes (markers carrying a comment), as a quiet chip above the composer: the count opens the list,
 * "Do them" sends the notes prompt. Loading the list fails quietly while the bridge can't answer.
 */
export function NotesChip({ count, load, onDo, disabled }: { count: number; load: () => Promise<NotesResult>; onDo: () => void; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<NotesResult | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!open) return;
    let live = true;
    setFailed(false);
    load().then(
      (d) => live && setData(d),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [open, load, count]);
  const notes = (data?.notes ?? []).filter((n) => n.status === "open");
  return (
    <div className="relative flex items-center gap-1 px-1 text-[12px]">
      <button
        type="button"
        data-popover-trigger
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex h-7 items-center gap-1.5 rounded-md px-1.5 -ml-1.5 text-muted transition-colors duration-150 ease-out hover:bg-white/[0.05] hover:text-ink"
      >
        <span aria-hidden="true" className="h-2.5 w-[2px] rounded-full bg-accent/80" />
        {count === 1 ? "1 timeline note" : `${count} timeline notes`}
        <Icon.Chevron size={10} className={cx("text-faint transition-transform duration-200 ease-out", open && "rotate-180")} />
      </button>
      <span className="text-faint">·</span>
      <button type="button" onClick={onDo} disabled={disabled} className="h-7 rounded-md px-1.5 font-medium text-accent-hi transition-colors duration-150 ease-out hover:bg-accent/10 disabled:opacity-50">
        Do them
      </button>
      <Popover open={open} onClose={() => setOpen(false)} up className="w-[320px] p-1.5">
        {failed ? (
          <p className="px-2 py-2 text-muted">Couldn’t read the notes from Resolve.</p>
        ) : !data ? (
          <p className="px-2 py-2 text-faint">Reading the markers…</p>
        ) : notes.length === 0 ? (
          <p className="px-2 py-2 text-muted">No open notes.</p>
        ) : (
          <ul className="no-scrollbar max-h-64 overflow-y-auto">
            {notes.map((n) => (
              <li key={`${n.frame}-${n.name}`} className="rounded-md px-2 py-1.5">
                <p className="flex items-baseline gap-2">
                  <span className="tc shrink-0 text-[11px] text-muted">
                    <SeekLink timecode={n.timecode}>{n.timecode}</SeekLink>
                  </span>
                  <span className="min-w-0 text-[12.5px] leading-[1.45] text-ink">{n.note || n.name}</span>
                </p>
                {n.clips && n.clips.length > 0 && (
                  <p className="mt-0.5 truncate pl-[calc(11ch+8px)] text-[11px] text-faint">
                    {n.clips.map((cl, i) => (
                      <span key={cl.item_id ?? cl.name}>
                        {i > 0 && ", "}
                        <SeekLink clip={cl.name}>{cl.name}</SeekLink>
                      </span>
                    ))}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Popover>
    </div>
  );
}

/**
 * The empty state: almost nothing. One question, optically centred over the night, and one muted line that says
 * honestly where you are (the clip under the playhead and its timeline, or that no timeline or project is open).
 * Recent chats live in the title menu; the composer is the only action.
 */
export function EmptyState({ ctx }: { ctx: Context }) {
  const clip = ctxClips(ctx)[0];
  const name = clip?.name ?? clip?.label;
  const tl = ctxTimeline(ctx);
  const project = ctxProject(ctx);
  let where: ReactNode = null;
  if (ctx.connected) {
    if (name && tl)
      where = (
        <>
          <span className="text-muted">{name}</span> in {tl}
        </>
      );
    else if (tl) where = <>Nothing under the playhead in {tl}</>;
    else if (project) where = <>No timeline open in {project}</>;
    else where = "No project open";
  }
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-6 pb-[12vh] text-center">
      <p className="t-display text-[25px] leading-tight font-semibold tracking-[-0.025em] text-balance text-ink-strong">
        {ctx.connected ? "What are we cutting?" : "Start the bridge to begin"}
      </p>
      {where && <p className="mt-3 max-w-full truncate text-[12.5px] text-faint">{where}</p>}
    </div>
  );
}

/**
 * The rain over the panel: hairline streaks, faint enough to read as texture (styles.css `.cabin-rain`). Behind
 * everything; off under Reduce Motion or the Settings switch.
 */
export function NightRain({ off }: { off: boolean }) {
  return (
    <div className="night-air" data-off={off ? "" : undefined} aria-hidden="true">
      <div className="cabin-rain">
        <div>
          <i className="far" />
          <i className="near" />
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------- banners

export function AuthBanner({ agent, detail: msg }: { agent: string; detail?: string }) {
  const cmd = agent === "codex" ? "codex login" : "claude";
  const [copied, setCopied] = useState(false);
  return (
    <div className="px-(--gutter) pt-1 pb-3 text-[12px] leading-[1.5]">
      <p className="text-warn">{agent === "codex" ? "Codex" : "Claude Code"} is signed out</p>
      <p className="mt-0.5 text-muted">
        Open Terminal, run <code className="tc text-ink">{cmd}</code>
        {agent === "codex" ? "" : (
          <>
            {" "}and type <code className="tc text-ink">/login</code>
          </>
        )}
        , then send your message again.
      </p>
      {msg && <p className="mt-1 truncate text-[11px] text-faint">{msg}</p>}
      <button
        type="button"
        onClick={() => {
          navigator.clipboard?.writeText(cmd);
          setCopied(true);
          setTimeout(() => setCopied(false), 1400);
        }}
        className="mt-1 text-[12px] text-accent-hi hover:underline"
      >
        {copied ? "Copied" : "Copy the command"}
      </button>
    </div>
  );
}
