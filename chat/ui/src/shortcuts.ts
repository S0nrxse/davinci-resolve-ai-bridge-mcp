/**
 * Keyboard shortcuts. Every key handler in the panel reads its combo from this map (defaults merged with the
 * editor's overrides from `settings.shortcuts`), so changing one in Settings › Shortcuts changes it everywhere.
 *
 * A combo is a string of modifiers in a fixed order then the key, joined by "+": "Meta+Shift+K", "Enter", "Escape".
 * Single-character keys are stored upper-case.
 */
import { createContext, useContext } from "react";

export type ShortcutAction = "send" | "newChat" | "allow" | "decline" | "focusComposer" | "stop" | "collapse" | "attachFile" | "pickSkill";

export const SHORTCUTS: { id: ShortcutAction; label: string; hint: string }[] = [
  { id: "send", label: "Send", hint: "Send the message (⇧↵ always adds a new line)" },
  { id: "newChat", label: "New chat", hint: "Start a new chat" },
  { id: "allow", label: "Allow", hint: "Allow the step that is waiting for approval" },
  { id: "decline", label: "Decline", hint: "Decline the step that is waiting for approval" },
  { id: "focusComposer", label: "Focus composer", hint: "Put the cursor in the message box" },
  { id: "stop", label: "Stop", hint: "Stop the agent while it works" },
  { id: "collapse", label: "Collapse to bubble", hint: "Shrink the panel to its bubble (in the app)" },
  { id: "attachFile", label: "Attach file", hint: "Open the file picker" },
  { id: "pickSkill", label: "Pick skill", hint: "Open the skill picker ($)" },
];

export const DEFAULT_SHORTCUTS: Record<ShortcutAction, string> = {
  send: "Enter",
  newChat: "Meta+N",
  allow: "Meta+Enter",
  decline: "Escape",
  focusComposer: "Meta+L",
  stop: "Meta+.",
  collapse: "Meta+Shift+W",
  attachFile: "Meta+U",
  pickSkill: "Meta+K",
};

const MODS = ["Meta", "Ctrl", "Alt", "Shift"] as const;

/** The combo a key event represents, or null for a bare modifier press. */
export function comboOf(e: KeyboardEvent | React.KeyboardEvent): string | null {
  const k = e.key;
  if (!k || ["Meta", "Control", "Alt", "Shift", "CapsLock", "Dead", "Process"].includes(k)) return null;
  // With ⌥ held, macOS turns letters into symbols (⌥K is "˚"); fall back to the physical key.
  let key = k;
  if (e.altKey && /^Key[A-Z]$/.test(e.code)) key = e.code.slice(3);
  else if (e.altKey && /^Digit\d$/.test(e.code)) key = e.code.slice(5);
  if (key === " ") key = "Space";
  if (key.length === 1) key = key.toUpperCase();
  const parts: string[] = [];
  if (e.metaKey) parts.push("Meta");
  if (e.ctrlKey) parts.push("Ctrl");
  if (e.altKey) parts.push("Alt");
  if (e.shiftKey && key.length > 1) parts.push("Shift");
  else if (e.shiftKey && /[A-Z0-9]/.test(key)) parts.push("Shift");
  parts.push(key);
  return parts.join("+");
}

export function matches(e: KeyboardEvent | React.KeyboardEvent, combo: string | undefined): boolean {
  return Boolean(combo) && comboOf(e) === normalize(combo!);
}

function normalize(combo: string): string {
  const parts = combo.split("+");
  const key = parts.pop() ?? "";
  const mods = MODS.filter((m) => parts.includes(m));
  return [...mods, key.length === 1 ? key.toUpperCase() : key].join("+");
}

const GLYPH: Record<string, string> = {
  Meta: "⌘",
  Ctrl: "⌃",
  Alt: "⌥",
  Shift: "⇧",
  Enter: "↵",
  Escape: "esc",
  Backspace: "⌫",
  Delete: "⌦",
  Tab: "⇥",
  Space: "Space",
  ArrowUp: "↑",
  ArrowDown: "↓",
  ArrowLeft: "←",
  ArrowRight: "→",
};

/** "Meta+Shift+K" → "⌘⇧K". */
export function formatCombo(combo: string | undefined): string {
  if (!combo) return "—";
  return combo
    .split("+")
    .map((p) => GLYPH[p] ?? p)
    .join("");
}

export function withDefaults(overrides?: Partial<Record<string, string>> | null): Record<ShortcutAction, string> {
  const out = { ...DEFAULT_SHORTCUTS };
  for (const a of Object.keys(out) as ShortcutAction[]) {
    const v = overrides?.[a];
    if (typeof v === "string" && v) out[a] = v;
  }
  return out;
}

/** True while Settings is recording a new combo, so no other handler acts on the keys being pressed. */
export const isRecording = () => document.body.dataset.recording === "1";

export const ShortcutsContext = createContext<Record<ShortcutAction, string>>(DEFAULT_SHORTCUTS);
export const useShortcuts = () => useContext(ShortcutsContext);
