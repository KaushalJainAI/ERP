import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";

export interface SelectItem {
  value: string;
  label: string;
}

interface SelectProps {
  value: string;
  options: SelectItem[];
  onChange: (value: string) => void;
  id?: string;
  disabled?: boolean;
  invalid?: boolean;
  compact?: boolean;
  /** Shown when no option matches `value`. */
  placeholder?: string;
  "aria-label"?: string;
  /** Extra classes for the trigger (e.g. a width). */
  className?: string;
  /** Rendered before the selected label inside the trigger (e.g. an icon). */
  leading?: React.ReactNode;
  /** Borderless trigger for toolbars. */
  variant?: "field" | "ghost";
}

/**
 * Themed single-choice dropdown (replaces native <select>, whose option list
 * can't be styled). Keyboard: Enter/Space/↓ open, ↑↓ move, Enter picks, Esc closes,
 * typing a letter jumps to the next option starting with it.
 */
export const Select: React.FC<SelectProps> = ({
  value,
  options,
  onChange,
  id,
  disabled,
  invalid,
  compact,
  placeholder,
  className = "",
  leading,
  variant = "field",
  ...rest
}) => {
  const listId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [rect, setRect] = useState<{ left: number; top: number; width: number; up: boolean } | null>(null);

  const selectedIndex = options.findIndex((o) => o.value === value);
  const selected = options[selectedIndex];

  // Anchor under the trigger (portal, so overflow-hidden parents can't clip it); flip up near the bottom.
  const place = () => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const up = window.innerHeight - r.bottom < 260 && r.top > 260;
    setRect({ left: r.left, top: up ? r.top - 4 : r.bottom + 4, width: Math.max(r.width, 160), up });
  };

  useLayoutEffect(() => {
    if (!open) return;
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!triggerRef.current?.contains(t) && !listRef.current?.contains(t)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Keep the highlighted option in view.
  useEffect(() => {
    if (open) listRef.current?.children[highlight]?.scrollIntoView({ block: "nearest" });
  }, [open, highlight]);

  const openList = () => {
    if (disabled) return;
    setHighlight(Math.max(selectedIndex, 0));
    setOpen(true);
  };

  const choose = (i: number) => {
    const o = options[i];
    if (!o) return;
    if (o.value !== value) onChange(o.value);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(e.key)) {
        e.preventDefault();
        openList();
      }
      return;
    }
    if (e.key === "ArrowDown") setHighlight((h) => Math.min(h + 1, options.length - 1));
    else if (e.key === "ArrowUp") setHighlight((h) => Math.max(h - 1, 0));
    else if (e.key === "Home") setHighlight(0);
    else if (e.key === "End") setHighlight(options.length - 1);
    else if (e.key === "Enter" || e.key === " ") choose(highlight);
    else if (e.key === "Escape" || e.key === "Tab") {
      setOpen(false);
      if (e.key === "Tab") return;
    } else if (e.key.length === 1) {
      const k = e.key.toLowerCase();
      const n = options.length;
      for (let step = 1; step <= n; step++) {
        const i = (highlight + step) % n;
        if (options[i].label.toLowerCase().startsWith(k)) {
          setHighlight(i);
          break;
        }
      }
    } else return;
    e.preventDefault();
  };

  const size = compact ? "px-2 py-1.5 text-xs" : "px-3 py-2 text-sm";
  const look =
    variant === "ghost"
      ? "border border-transparent bg-transparent hover:bg-slate-800/70"
      : `border bg-slate-950 hover:border-slate-700 ${invalid ? "border-rose-500" : "border-slate-800"}`;

  return (
    <>
      <button
        ref={triggerRef}
        id={id}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-invalid={invalid || undefined}
        aria-label={rest["aria-label"]}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={onKeyDown}
        className={`inline-flex items-center gap-2 rounded-lg text-start text-slate-200 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40 focus-visible:border-indigo-500 disabled:opacity-60 disabled:cursor-not-allowed ${size} ${look} ${
          open ? "border-indigo-500 ring-2 ring-indigo-500/20" : ""
        } ${className || "w-full"}`}
      >
        {leading}
        <span className={`flex-1 truncate ${selected ? "" : "text-slate-500"}`}>
          {selected ? selected.label : placeholder ?? " "}
        </span>
        <ChevronDown
          className={`w-4 h-4 shrink-0 text-slate-500 transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>

      {open &&
        rect &&
        createPortal(
          <ul
            ref={listRef}
            id={listId}
            role="listbox"
            aria-activedescendant={`${listId}-${highlight}`}
            style={{
              position: "fixed",
              left: rect.left,
              width: rect.width,
              ...(rect.up ? { bottom: window.innerHeight - rect.top } : { top: rect.top }),
            }}
            className="z-[90] max-h-64 overflow-auto rounded-xl border border-slate-800 bg-slate-900 p-1 text-sm shadow-2xl animate-pop-in"
          >
            {options.map((o, i) => {
              const isSel = o.value === value;
              return (
                <li
                  key={o.value}
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={isSel}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => choose(i)}
                  onMouseEnter={() => setHighlight(i)}
                  className={`flex items-center gap-2 rounded-lg px-2.5 py-2 cursor-pointer ${
                    i === highlight ? "bg-slate-800/70" : ""
                  } ${isSel ? "font-medium text-indigo-300" : i === highlight ? "text-slate-100" : "text-slate-300"}`}
                >
                  <span className="flex-1 truncate">{o.label || " "}</span>
                  {isSel && <Check className="w-4 h-4 shrink-0 text-indigo-400" aria-hidden />}
                </li>
              );
            })}
          </ul>,
          document.body
        )}
    </>
  );
};
