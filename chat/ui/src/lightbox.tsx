import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "./icons";

/**
 * Click any image in the chat (attachments, frame grabs, step results) to see it fit to the panel over a dimmed,
 * blurred backdrop. Esc or a click outside closes it; ← / → step through the chat's other images.
 */

type Shot = { src: string; caption: string };
let openFn: ((from: HTMLElement) => void) | null = null;

const shotOf = (el: Element): Shot => ({ src: (el as HTMLElement).dataset.lightbox ?? "", caption: (el as HTMLElement).dataset.lightboxCaption ?? "" });
const allShots = () => Array.from(document.querySelectorAll<HTMLElement>("[data-lightbox]")).filter((el) => el.dataset.lightbox);

/** A clickable, focusable image that opens the lightbox (Enter or click). */
export function Zoomable({ src, caption, className, children }: { src: string; caption: string; className?: string; children: ReactNode }) {
  return (
    <button
      type="button"
      data-lightbox={src}
      data-lightbox-caption={caption}
      aria-label={`View ${caption}`}
      onClick={(e) => {
        e.stopPropagation();
        openFn?.(e.currentTarget);
      }}
      className={className ? `cursor-zoom-in ${className}` : "cursor-zoom-in"}
    >
      {children}
    </button>
  );
}

export function Lightbox() {
  const [shots, setShots] = useState<Shot[]>([]);
  const [index, setIndex] = useState(0);
  const [phase, setPhase] = useState<"closed" | "open" | "closing">("closed");
  const opener = useRef<HTMLElement | null>(null);
  const closeBtn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    openFn = (from) => {
      const els = allShots();
      const i = Math.max(0, els.indexOf(from));
      opener.current = from;
      setShots(els.length ? els.map(shotOf) : [shotOf(from)]);
      setIndex(els.length ? i : 0);
      setPhase("open");
    };
    return () => {
      openFn = null;
    };
  }, []);

  const close = useCallback(() => {
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    setPhase("closing");
    setTimeout(() => {
      setPhase("closed");
      opener.current?.focus();
    }, reduce ? 0 : 160);
  }, []);

  useEffect(() => {
    if (phase !== "open") return;
    closeBtn.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close();
      } else if (e.key === "ArrowRight" && shots.length > 1) {
        e.preventDefault();
        setIndex((i) => (i + 1) % shots.length);
      } else if (e.key === "ArrowLeft" && shots.length > 1) {
        e.preventDefault();
        setIndex((i) => (i - 1 + shots.length) % shots.length);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [phase, shots.length, close]);

  if (phase === "closed" || !shots[index]) return null;
  const shot = shots[index];
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={shot.caption || "Image"}
      data-phase={phase}
      className="lightbox absolute inset-0 z-[70] flex flex-col items-center justify-center gap-3 p-(--gutter) pt-12"
      onClick={(e) => e.target === e.currentTarget && close()}
    >
      <button
        ref={closeBtn}
        type="button"
        aria-label="Close"
        onClick={close}
        className="absolute top-2.5 right-2.5 grid size-8 place-items-center rounded-lg text-muted transition-colors duration-150 ease-out hover:bg-white/[0.07] hover:text-ink"
      >
        <Icon.X size={14} />
      </button>
      <img src={shot.src} alt={shot.caption} className="lightbox-img min-h-0 max-w-full flex-initial rounded-lg object-contain shadow-[0_24px_48px_-16px_rgba(0,0,0,0.85)]" />
      <p className="flex max-w-full items-center gap-2 text-[12px] text-muted" onClick={(e) => e.target === e.currentTarget && close()}>
        <span className="min-w-0 truncate">{shot.caption}</span>
        {shots.length > 1 && (
          <span className="shrink-0 text-faint tabular-nums">
            {index + 1} / {shots.length}
          </span>
        )}
      </p>
    </div>
  );
}
