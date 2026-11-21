import { Fragment, useEffect, useState, type ReactNode } from "react";
import { api, DEMO, type SeekResult } from "./api";
import { demoSeek } from "./demo";

/**
 * Timecodes and clip names in the agent's words become quiet links that move Resolve's playhead there
 * (`POST /api/seek`). Dotted underline in the text colour, warm on hover with a small playhead mark; never loud blue.
 */

export const TIMECODE = /\b\d{1,2}[:;]\d{2}[:;]\d{2}[:;]\d{2}\b/g;
const MEDIA_EXT = "mov|mp4|m4v|mxf|mkv|avi|wav|mp3|aif|aiff|png|jpg|jpeg|tif|tiff|exr|dpx|braw|r3d";
/** A whole string that looks like a media file name (for inline code spans and clip lists). */
export const isMediaName = (s: string) => new RegExp(`^[^\\s/\\\\\`]{1,160}\\.(?:${MEDIA_EXT})$`, "i").test(s.trim());
/** Media file names inside plain text (no spaces in the name), for step summaries. */
const MEDIA_IN_TEXT = new RegExp(`[A-Za-z0-9][\\w.+-]*\\.(?:${MEDIA_EXT})\\b`, "gi");

// ------------------------------------------------------------------ toasts
type Toast = { id: number; text: string };
const listeners = new Set<(t: Toast, clear: boolean) => void>();
let seq = 0;
/** A short message above the composer (errors from seeking); one at a time, gone after a few seconds. */
export function toast(text: string) {
  const t = { id: ++seq, text };
  listeners.forEach((l) => l(t, false));
  setTimeout(() => listeners.forEach((l) => l(t, true)), 3600);
}
export function useToast(): [Toast | null, () => void] {
  const [t, setT] = useState<Toast | null>(null);
  useEffect(() => {
    const l = (n: Toast, clear: boolean) => setT((cur) => (clear ? (cur?.id === n.id ? null : cur) : n));
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  return [t, () => setT(null)];
}

async function seek(body: { timecode?: string; clip?: string }): Promise<SeekResult> {
  if (DEMO) return demoSeek(body);
  return api.seek(body);
}

/** One link: a timecode or a clip name. Success flashes it once; an error shows a toast. */
export function SeekLink({ timecode, clip, children, code }: { timecode?: string; clip?: string; children: ReactNode; code?: boolean }) {
  const [state, setState] = useState<"idle" | "busy" | "ok">("idle");
  const label = timecode ? `Move the playhead to ${timecode}` : `Move the playhead to ${clip}`;
  return (
    <button
      type="button"
      className={code ? "seek seek-code" : "seek"}
      data-state={state}
      aria-label={label}
      onClick={async (e) => {
        e.stopPropagation();
        if (state === "busy") return;
        setState("busy");
        try {
          await seek(timecode ? { timecode } : { clip });
          setState("ok");
          setTimeout(() => setState("idle"), 700);
        } catch (err: any) {
          setState("idle");
          toast(err?.message || "Couldn’t move the playhead.");
        }
      }}
    >
      {children}
    </button>
  );
}

/** Plain text with its timecodes (and, with `clips`, its media file names) turned into seek links. */
export function linkify(text: string, opts: { clips?: boolean } = {}): ReactNode[] {
  const re = opts.clips ? new RegExp(`${TIMECODE.source}|${MEDIA_IN_TEXT.source}`, "gi") : new RegExp(TIMECODE.source, "g");
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(<Fragment key={k++}>{text.slice(last, m.index)}</Fragment>);
    const tok = m[0];
    out.push(
      new RegExp(`^${TIMECODE.source}$`).test(tok) ? (
        <SeekLink key={k++} timecode={tok}>
          {tok}
        </SeekLink>
      ) : (
        <SeekLink key={k++} clip={tok}>
          {tok}
        </SeekLink>
      ),
    );
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(<Fragment key={k++}>{text.slice(last)}</Fragment>);
  return out;
}
