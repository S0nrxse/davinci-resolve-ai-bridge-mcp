import type { ReactNode, SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { size?: number };

/**
 * One icon set on a 16px grid (live area 2–14), 1.4 strokes with round ends: the geometric glyphs (CLEAN) drawn
 * over a base dictionary (BASE) that also supplies any glyph CLEAN leaves out.
 */
const STYLE = { strokeWidth: 1.4, strokeLinecap: "round", strokeLinejoin: "round" } as const;

const F = { fill: "currentColor", stroke: "none" } as const;

const BASE = {
  Plus: <path d="M8 3.5v9M3.5 8h9" />,
  Send: <path d="M8 12.5v-9M4.5 7 8 3.5 11.5 7" />,
  Stop: <rect x="4.5" y="4.5" width="7" height="7" rx="1.2" {...F} />,
  Chevron: <path d="m5.5 6.5 2.5 2.5 2.5-2.5" />,
  ChevronRight: <path d="m6.5 4.5 3 3.5-3 3.5" />,
  Sliders: (
    <>
      <path d="M3 5h6M12 5h1M3 11h1M7 11h6" />
      <circle cx="10.5" cy="5" r="1.5" />
      <circle cx="5.5" cy="11" r="1.5" />
    </>
  ),
  History: <path d="M3 8a5 5 0 1 0 1.5-3.6M3 3v2.5h2.5M8 5.5V8l1.8 1.2" />,
  Check: <path d="m3.8 8.4 2.6 2.6 5.8-6" />,
  X: <path d="m4.5 4.5 7 7M11.5 4.5l-7 7" />,
  Warn: <path d="M8 2.8 14 13H2L8 2.8ZM8 6.5v3M8 11.3v.2" />,
  Copy: (
    <>
      <rect x="5.5" y="5.5" width="7" height="7" rx="1.3" />
      <path d="M10.5 5.5v-1A1.3 1.3 0 0 0 9.2 3.2H4.5a1.3 1.3 0 0 0-1.3 1.3v4.7a1.3 1.3 0 0 0 1.3 1.3h1" />
    </>
  ),
  Trash: <path d="M3.5 4.5h9M6.5 4.5V3.2h3v1.3M4.7 4.5l.6 8.3h5.4l.6-8.3" />,
  Pin: <path d="M3 13 6.5 9.5M5.5 6l4.5 4.5M9.5 2.8l3.7 3.7-2.2.9-2.4 2.4.2 2-4.4-4.4 2-.2 2.4-2.4.7-2Z" />,
  Eye: (
    <>
      <path d="M1.8 8S4 3.8 8 3.8 14.2 8 14.2 8 12 12.2 8 12.2 1.8 8 1.8 8Z" />
      <circle cx="8" cy="8" r="1.9" />
    </>
  ),
  Frame: (
    <>
      <rect x="2" y="3.5" width="12" height="9" rx="1.3" />
      <path d="m2.5 11 3.3-3.2 2.4 2.2 1.9-1.6 3.4 2.9" />
    </>
  ),
  Wave: <path d="M2 8h1.2M4.6 5.5v5M7 3.5v9M9.4 6v4M11.8 4.8v6.4M14 8h-.2" />,
  Node: (
    <>
      <rect x="1" y="4.5" width="5.5" height="7" rx="1.2" />
      <rect x="9.5" y="4.5" width="5.5" height="7" rx="1.2" fill="currentColor" fillOpacity="0.35" />
      <path d="M6.5 8h3" strokeWidth={1.6} />
    </>
  ),
  Film: (
    <>
      <rect x="1.8" y="4" width="12.4" height="8" rx="1" />
      <path d="M1.8 6.5h2M1.8 9.5h2M12.2 6.5h2M12.2 9.5h2M5.5 4v8M10.5 4v8" />
    </>
  ),
  Color: (
    <>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M8 2.4v11.2M8 8l4.8-2.8" />
    </>
  ),
  Move: (
    <>
      <rect x="3.5" y="3.5" width="9" height="9" rx="1" strokeDasharray="1.6 1.6" />
      <path d="M8 5.8v4.4M5.8 8h4.4" />
    </>
  ),
  Export: <path d="M8 2.8v7M5.2 5.4 8 2.6l2.8 2.8M3 9.5v2.7c0 .6.5 1 1 1h8c.6 0 1-.4 1-1V9.5" />,
  Terminal: (
    <>
      <rect x="2" y="3" width="12" height="10" rx="1.4" />
      <path d="m4.8 6.3 2 1.7-2 1.7M8.3 10h3" />
    </>
  ),
  Diamond: <path d="M8 3 13 8l-5 5-5-5 5-5Z" {...F} />,
  Shield: <path d="M8 2.3 13 4v3.8c0 3-2.2 5.2-5 6-2.8-.8-5-3-5-6V4l5-1.7Z" />,
  Bolt: <path d="M9 2 3.8 9h3.7L7 14l5.2-7H8.5L9 2Z" />,
  Layers: <path d="m8 2.8 5.6 3L8 8.8l-5.6-3L8 2.8ZM2.4 8.8l5.6 3 5.6-3" />,
  Bubble: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M5.5 8h5" />
    </>
  ),
  Paperclip: <path d="m13 7.6-5.2 5.2a3.2 3.2 0 0 1-4.5-4.5l5.6-5.6a2.1 2.1 0 0 1 3 3L6.4 11.2a1.1 1.1 0 0 1-1.5-1.5l5-5" />,
  More: (
    <>
      <circle cx="3.8" cy="8" r=".9" fill="currentColor" />
      <circle cx="8" cy="8" r=".9" fill="currentColor" />
      <circle cx="12.2" cy="8" r=".9" fill="currentColor" />
    </>
  ),
  Brain: <path d="M6 3.2a2 2 0 0 0-2 2v.2a2 2 0 0 0-1.2 3.4A2.2 2.2 0 0 0 5 12.6a1.8 1.8 0 0 0 3 .4V3.9a1.8 1.8 0 0 0-2-.7ZM10 3.2a2 2 0 0 1 2 2v.2a2 2 0 0 1 1.2 3.4 2.2 2.2 0 0 1-2.2 3.8 1.8 1.8 0 0 1-3 .4" />,
  Agents: (
    <>
      <circle cx="5" cy="5.5" r="2" />
      <circle cx="11" cy="5.5" r="2" />
      <path d="M1.8 13c.4-2 1.6-3.2 3.2-3.2S8 11 8.2 13M7.8 13c.4-2 1.6-3.2 3.2-3.2s2.8 1.2 3.2 3.2" />
    </>
  ),
  Image: (
    <>
      <rect x="2" y="2.8" width="12" height="10.4" rx="1.5" />
      <circle cx="5.8" cy="6.2" r="1.2" />
      <path d="m2.5 11.8 3.6-3.4 2.6 2.4 1.8-1.5 3 2.7" />
    </>
  ),
  File: <path d="M4 2.5h5l3 3v8H4zM9 2.5v3h3" />,
  Folder: <path d="M2 4.2c0-.6.4-1 1-1h3l1.3 1.5H13c.6 0 1 .4 1 1v6.3c0 .6-.4 1-1 1H3c-.6 0-1-.4-1-1z" />,
  Search: (
    <>
      <circle cx="7" cy="7" r="4" />
      <path d="m10 10 3.5 3.5" />
    </>
  ),
  PinOff: <path d="M3 13 6.5 9.5M5.5 6l4.5 4.5M9.5 2.8l3.7 3.7-2.2.9-2.4 2.4.2 2-4.4-4.4 2-.2 2.4-2.4.7-2ZM2.5 2.5l11 11" />,
  Pause: (
    <>
      <circle cx="8" cy="8" r="5.8" />
      <path d="M6.6 5.8v4.4M9.4 5.8v4.4" />
    </>
  ),
  Return: <path d="M4 3.5v4.3c0 .9.7 1.7 1.7 1.7h7M10 7l2.7 2.5L10 12" />,
  Steer: <path d="M3 12.5c0-4 2.6-6.8 7-6.8h3M10.6 3.2l2.6 2.5-2.6 2.5" />,
  Unlock: (
    <>
      <rect x="3" y="7.2" width="10" height="6.6" rx="1.4" />
      <path d="M5.3 7.2V5.3a2.7 2.7 0 0 1 5.2-1" />
    </>
  ),
  Clip: (
    <>
      <rect x="1.8" y="5" width="12.4" height="6" rx="1.2" />
      <path d="M5 5v6M11 5v6" />
    </>
  ),
  Dock: (
    <>
      <rect x="2" y="3" width="12" height="10" rx="1.4" />
      <path d="M10 3v10" />
    </>
  ),
  Rewind: <path d="M3.25 3.5v3.25H6.5M3.6 6.6A5 5 0 1 1 4.4 11.2" />,
  // Step glyphs: what a step does, drawn on the same 16px grid.
  Scissors: (
    <>
      <circle cx="4.4" cy="4.6" r="1.9" />
      <circle cx="4.4" cy="11.4" r="1.9" />
      <path d="M6 5.8 13.2 11.2M6 10.2 13.2 4.8" />
    </>
  ),
  Type: <path d="M3.5 3.5h9M8 3.5v9M6.25 12.5h3.5" />,
  Flag: <path d="M4 13.5V2.75M4 3.25h7.75l-1.9 2.6 1.9 2.65H4" />,
  Drop: <path d="M8 2.5c2.5 3 4.1 5.2 4.1 7.2a4.1 4.1 0 0 1-8.2 0c0-2 1.6-4.2 4.1-7.2Z" />,
  Play: <path d="M5 3.4v9.2l7.6-4.6Z" />,
  List: <path d="M5.5 4.25h8M5.5 8h8M5.5 11.75h5M2.75 4.25h.01M2.75 8h.01M2.75 11.75h.01" />,
  Pulse: <path d="M1.75 8.25h2.75l1.75-4 3 7.5 1.75-3.5h3.25" />,
  Gauge: <path d="M2.75 11.25a5.25 5.25 0 1 1 10.5 0M8 11.25l2.75-3.25" />,
  Save: <path d="M3.25 3.25c0-.4.3-.75.75-.75h6.5l2.25 2.25V12c0 .4-.35.75-.75.75H4c-.45 0-.75-.35-.75-.75ZM5.5 2.5v2.75h4.25V2.5M5.5 12.75V9.25h5v3.5" />,
  Import: <path d="M8 2.5v7.25M5 6.75l3 3 3-3M3 12.75h10" />,
  Playhead: <path d="M8 6v8M5 2.5h6v2.25L8 6.75 5 4.75Z" />,
  /** A thought: a trail of three rising beads, smallest first. */
  Thought: (
    <>
      <circle cx="3.25" cy="12.75" r="1" fill="currentColor" stroke="none" />
      <circle cx="6.5" cy="9.75" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="11" cy="5.5" r="2.75" />
    </>
  ),
  Info: (
    <>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M8 7.25v3.5M8 5.2v.1" />
    </>
  ),
  Toggles: (
    <>
      <rect x="2" y="3" width="12" height="4" rx="2" />
      <circle cx="11.9" cy="5" r="1" {...F} />
      <rect x="2" y="9" width="12" height="4" rx="2" />
      <circle cx="4.1" cy="11" r="1" {...F} />
    </>
  ),
};

export type IconName = keyof typeof BASE;
type Glyphs = Partial<Record<IconName, ReactNode>>;

// ---------------------------------------------------------------- Edit Suite
const A: Glyphs = {
  Plus: <path d="M8 3v10M3 8h10" />,
  Send: <path d="M8 13V3.5M3.75 7.75 8 3.5l4.25 4.25" />,
  Stop: <rect x="4.5" y="4.5" width="7" height="7" rx="1.5" {...F} />,
  Chevron: <path d="m4.5 6.25 3.5 3.5 3.5-3.5" />,
  ChevronRight: <path d="m6.25 4.5 3.5 3.5-3.5 3.5" />,
  Sliders: (
    <>
      <path d="M2.5 4.5h4M9.5 4.5h4M2.5 11.5h7M12.5 11.5h1" />
      <circle cx="8" cy="4.5" r="1.5" />
      <circle cx="11" cy="11.5" r="1.5" />
    </>
  ),
  History: <path d="M2.75 8a5.25 5.25 0 1 0 1.54-3.71M2.75 3.25v2.5h2.5M8 5.5V8l1.75 1.25" />,
  Check: <path d="m3.25 8.25 3 3 6.5-6.5" />,
  X: <path d="m4 4 8 8M12 4l-8 8" />,
  Warn: <path d="M7.13 2.75a1 1 0 0 1 1.74 0l5.2 9.25a1 1 0 0 1-.87 1.5H2.8a1 1 0 0 1-.87-1.5ZM8 6.5v2.75M8 11.25h.01" />,
  Copy: (
    <>
      <rect x="5.5" y="5.5" width="8" height="8" rx="2" />
      <path d="M10.5 5.5v-1a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v4a2 2 0 0 0 2 2h1" />
    </>
  ),
  Trash: <path d="M2.75 4.5h10.5M6.25 4.5V3.25a.75.75 0 0 1 .75-.75h2a.75.75 0 0 1 .75.75V4.5M4.25 4.5l.6 8.1a1 1 0 0 0 1 .9h4.3a1 1 0 0 0 1-.9l.6-8.1" />,
  Pin: (
    <>
      <path d="M8 14s4.5-4.1 4.5-7.5a4.5 4.5 0 0 0-9 0C3.5 9.9 8 14 8 14Z" />
      <circle cx="8" cy="6.5" r="1.5" />
    </>
  ),
  Eye: (
    <>
      <path d="M1.75 8S4 3.5 8 3.5 14.25 8 14.25 8 12 12.5 8 12.5 1.75 8 1.75 8Z" />
      <circle cx="8" cy="8" r="2" />
    </>
  ),
  Frame: (
    <>
      <rect x="2" y="2.75" width="12" height="8.5" rx="2" />
      <path d="M5.5 13.75h5M8 11.25v2.5" />
    </>
  ),
  Image: (
    <>
      <rect x="2" y="2.75" width="12" height="10.5" rx="2" />
      <circle cx="6" cy="6.5" r="1.25" />
      <path d="m2.5 12 3.5-3.25 2.5 2.25 2-1.75 3 2.5" />
    </>
  ),
  Wave: <path d="M2.5 7v2M5.25 5v6M8 3v10M10.75 5.5v5M13.5 7v2" />,
  Node: (
    <>
      <rect x="1.5" y="5" width="5" height="6" rx="1.5" />
      <rect x="9.5" y="5" width="5" height="6" rx="1.5" fill="currentColor" fillOpacity="0.35" />
      <path d="M6.5 8h3" />
    </>
  ),
  Film: (
    <>
      <rect x="2" y="3" width="12" height="10" rx="2" />
      <path d="M5 3v10M11 3v10M2 6.5h3M2 9.5h3M11 6.5h3M11 9.5h3" />
    </>
  ),
  Color: (
    <>
      <circle cx="8" cy="5.9" r="3" />
      <circle cx="5.9" cy="9.6" r="3" />
      <circle cx="10.1" cy="9.6" r="3" />
    </>
  ),
  Move: <path d="M8 2v12M2 8h12M6.25 3.75 8 2l1.75 1.75M6.25 12.25 8 14l1.75-1.75M3.75 6.25 2 8l1.75 1.75M12.25 6.25 14 8l-1.75 1.75" />,
  Export: <path d="M8 2.5V10M5 5.25l3-3 3 3M3 9.5V12a1.5 1.5 0 0 0 1.5 1.5h7A1.5 1.5 0 0 0 13 12V9.5" />,
  Terminal: (
    <>
      <rect x="2" y="3" width="12" height="10" rx="2" />
      <path d="m5 6.5 1.75 1.5L5 9.5M8.5 10H11" />
    </>
  ),
  Shield: <path d="M8 2 13 3.75v4c0 3-2.1 5.2-5 6.25-2.9-1.05-5-3.25-5-6.25v-4ZM6 8l1.5 1.5 2.75-2.75" />,
  Unlock: (
    <>
      <rect x="3" y="7" width="10" height="7" rx="2" />
      <path d="M5.5 7V5a2.5 2.5 0 0 1 4.85-.85M8 10v1.25" />
    </>
  ),
  Layers: <path d="m8 2.75 5.75 3L8 8.75l-5.75-3ZM2.25 9 8 12l5.75-3" />,
  Bubble: (
    <>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M5.5 8h5" />
    </>
  ),
  Paperclip: <path d="m13 7.5-5.25 5.25a3.25 3.25 0 0 1-4.6-4.6l5.65-5.65a2.15 2.15 0 0 1 3.05 3.05L6.4 11.1a1.1 1.1 0 0 1-1.55-1.55l5-5" />,
  Brain: <path d="M8 3.5v9M8 3.5a2.25 2.25 0 0 0-4.25 1A2.25 2.25 0 0 0 3 8.5a2.25 2.25 0 0 0 1.5 3.5A2 2 0 0 0 8 12.5M8 3.5a2.25 2.25 0 0 1 4.25 1A2.25 2.25 0 0 1 13 8.5a2.25 2.25 0 0 1-1.5 3.5A2 2 0 0 1 8 12.5" />,
  Agents: (
    <>
      <circle cx="5.75" cy="5.5" r="2.25" />
      <path d="M1.75 13.25c.4-2.3 2-3.75 4-3.75s3.6 1.45 4 3.75M10.25 3.4a2.25 2.25 0 0 1 0 4.2M11.75 9.6c1.3.5 2.2 1.8 2.5 3.65" />
    </>
  ),
  File: <path d="M4.5 2h4.75l3.25 3.25v7.25A1.5 1.5 0 0 1 11 14H4.5A1.5 1.5 0 0 1 3 12.5v-9A1.5 1.5 0 0 1 4.5 2ZM9 2v3.5h3.5" />,
  Search: (
    <>
      <circle cx="7" cy="7" r="4.25" />
      <path d="m10.25 10.25 3.25 3.25" />
    </>
  ),
  Pause: (
    <>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M6.5 6v4M9.5 6v4" />
    </>
  ),
  Return: <path d="M3.5 3.5v3.75a2 2 0 0 0 2 2h7M10 6.5l2.75 2.75L10 12" />,
  Steer: <path d="M3 13c0-4.25 2.75-7 7-7h3M10.5 3.5 13 6l-2.5 2.5" />,
  Toggles: (
    <>
      <rect x="1.75" y="2.75" width="12.5" height="4.5" rx="2.25" />
      <circle cx="12" cy="5" r="1.1" {...F} />
      <rect x="1.75" y="8.75" width="12.5" height="4.5" rx="2.25" />
      <circle cx="4" cy="11" r="1.1" {...F} />
    </>
  ),
};

/** The geometric set with a few softer, rounder primitives. */
const CLEAN: Glyphs = {
  ...A,
  Stop: <rect x="4.75" y="4.75" width="6.5" height="6.5" rx="1.8" {...F} />,
  Send: <path d="M8 12.75V3.25M4.25 7 8 3.25 11.75 7" />,
  Plus: <path d="M8 3.25v9.5M3.25 8h9.5" />,
  Check: <path d="m3.75 8.5 2.75 2.75 5.75-6" />,
};

function make(name: IconName) {
  const Glyph = ({ size = 14, ...rest }: P) => (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" {...STYLE} aria-hidden="true" {...rest}>
      {CLEAN[name] ?? BASE[name]}
    </svg>
  );
  Glyph.displayName = `Icon.${name}`;
  return Glyph;
}

export const Icon = Object.fromEntries((Object.keys(BASE) as IconName[]).map((n) => [n, make(n)])) as Record<IconName, ReturnType<typeof make>>;

export const CategoryIcon = {
  inspect: Icon.Eye,
  frame: Icon.Frame,
  audio: Icon.Wave,
  fusion: Icon.Node,
  timeline: Icon.Film,
  color: Icon.Color,
  transform: Icon.Move,
  deliver: Icon.Export,
  code: Icon.Terminal,
  agent: Icon.Agents,
} as const;
