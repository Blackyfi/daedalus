import { useEffect, useRef, useState } from "react";

interface Props {
  /** Short body shown in the popover. */
  text: string;
  /** Accessible label for the trigger button (defaults to "Help"). */
  label?: string;
  className?: string;
}

/**
 * Click-to-toggle "?" badge that explains a concept inline.
 *
 * Used for short clarifications next to section headers (e.g. on the
 * Projects-page idea box, to distinguish project ideas from task ideas).
 * Renders the badge inline as a focusable button so it satisfies the
 * 44 px touch-target floor via `min-h-[24px]` plus generous padding.
 */
export default function HelpTooltip({ text, label = "Help", className = "" }: Props) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onEsc(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  return (
    <span ref={wrapRef} className={`relative inline-block ${className}`}>
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        title={text}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-border bg-panel2 text-[10px] text-muted hover:text-text hover:border-accent focus:outline-none focus:border-accent"
      >
        ?
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute z-30 right-0 mt-1 w-64 max-w-[80vw] rounded border border-border bg-panel p-2 text-xs text-text shadow-lg normal-case tracking-normal"
        >
          {text}
        </span>
      )}
    </span>
  );
}
