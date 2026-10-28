import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, ChevronDown, Loader2, X } from "lucide-react";
import { getRecord, listRecords } from "@/api/endpoints";
import { useDebounced } from "@/hooks/useDebounced";
import type { LinkDef, LinkRecord } from "@/types";

interface LinkFieldProps {
  link: LinkDef;
  /** Stable id used in query keys (e.g. "sales-orders.customer"). */
  cacheKey: string;
  value: string | number | null | undefined;
  /** The row / form values this field belongs to (for `display` and `filters`). */
  values: Record<string, any>;
  parentValues: Record<string, any>;
  onSelect: (value: string | number | null, record: LinkRecord | null) => void;
  disabled?: boolean;
  invalid?: boolean;
  required?: boolean;
  compact?: boolean;
  placeholder?: string;
  id?: string;
}

const recordLabel = (link: LinkDef, rec: LinkRecord): string =>
  link.label ? link.label(rec) : String(rec[link.labelField || "name"] ?? rec[link.valueField || "id"] ?? "");

/** Searchable async dropdown that stores a foreign-key value. */
export const LinkField: React.FC<LinkFieldProps> = ({
  link,
  cacheKey,
  value,
  values,
  parentValues,
  onSelect,
  disabled,
  invalid,
  required,
  compact,
  placeholder,
  id,
}) => {
  const { t } = useTranslation();
  const valueField = link.valueField || "id";
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [highlight, setHighlight] = useState(0);
  const debounced = useDebounced(search, 250);
  const inputRef = useRef<HTMLInputElement>(null);
  const [rect, setRect] = useState<{ left: number; top: number; width: number } | null>(null);

  const filters = useMemo(() => link.filters?.(values, parentValues) ?? {}, [link, values, parentValues]);
  const filtersKey = JSON.stringify(filters);

  const hasValue = value !== null && value !== undefined && value !== "";
  const knownLabel = hasValue ? link.display?.(values) : undefined;

  // Label for a stored value when the record itself doesn't carry one.
  const { data: fetchedLabel } = useQuery({
    queryKey: ["link-label", cacheKey, link.endpoint ?? "fetcher", String(value)],
    queryFn: async () => {
      if (link.endpoint && valueField === "id") {
        return recordLabel(link, await getRecord(link.endpoint, value as string | number));
      }
      const rows = link.fetcher
        ? await link.fetcher("")
        : (await listRecords(link.endpoint!, { ...filters })).results;
      const hit = rows.find((r) => String(r[valueField]) === String(value));
      return hit ? recordLabel(link, hit) : String(value);
    },
    enabled: hasValue && !knownLabel,
    staleTime: 5 * 60 * 1000,
  });

  const { data: options, isFetching } = useQuery({
    queryKey: ["lookup", cacheKey, link.endpoint ?? "fetcher", debounced, filtersKey],
    queryFn: async (): Promise<LinkRecord[]> => {
      if (link.fetcher) return link.fetcher(debounced);
      return (await listRecords(link.endpoint!, { search: debounced, ...filters })).results;
    },
    enabled: open && !disabled,
    staleTime: 30 * 1000,
  });

  const label = knownLabel ?? fetchedLabel ?? (hasValue ? String(value) : "");

  // Anchor the dropdown under the input (portal so table overflow can't clip it).
  const place = () => {
    const el = inputRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setRect({ left: r.left, top: r.bottom + 4, width: Math.max(r.width, 240) });
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

  useEffect(() => setHighlight(0), [options]);

  const choose = (rec: LinkRecord) => {
    onSelect(rec[valueField], rec);
    setOpen(false);
    setSearch("");
  };

  const clear = () => {
    onSelect(null, null);
    setSearch("");
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open && (e.key === "ArrowDown" || e.key === "Enter")) {
      if (e.key === "ArrowDown") {
        setOpen(true);
        e.preventDefault();
      }
      return;
    }
    if (!open) return;
    const list = options ?? [];
    if (e.key === "ArrowDown") {
      setHighlight((h) => Math.min(h + 1, Math.max(list.length - 1, 0)));
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      setHighlight((h) => Math.max(h - 1, 0));
      e.preventDefault();
    } else if (e.key === "Enter") {
      if (list[highlight]) choose(list[highlight]);
      e.preventDefault();
    } else if (e.key === "Escape") {
      setOpen(false);
      e.preventDefault();
    }
  };

  const base =
    "w-full bg-slate-950 border rounded-lg text-slate-200 placeholder:text-slate-500 hover:border-slate-700 focus:outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 transition-colors disabled:opacity-60 disabled:cursor-not-allowed " +
    (compact ? "px-2 py-1.5 text-xs " : "px-3 py-2 text-sm ") +
    (invalid ? "border-rose-500" : "border-slate-800");

  return (
    <div className="relative">
      <input
        id={id}
        ref={inputRef}
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        autoComplete="off"
        disabled={disabled}
        placeholder={placeholder || t("common.select")}
        value={open ? search : label}
        onFocus={() => {
          if (!disabled) {
            setOpen(true);
            setSearch("");
          }
        }}
        onBlur={() => setOpen(false)}
        onChange={(e) => {
          setSearch(e.target.value);
          setOpen(true);
        }}
        onKeyDown={onKeyDown}
        className={`${base} pr-12 truncate`}
      />
      <div className="absolute inset-y-0 right-1.5 flex items-center gap-1 text-slate-500">
        {isFetching && open && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
        {hasValue && !required && !disabled && (
          <button
            type="button"
            tabIndex={-1}
            onMouseDown={(e) => e.preventDefault()}
            onClick={clear}
            className="p-0.5 rounded hover:text-slate-200 hover:bg-slate-800"
            aria-label={t("common.clear")}
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
        <ChevronDown className="w-3.5 h-3.5 pointer-events-none" />
      </div>

      {open &&
        rect &&
        createPortal(
          <ul
            role="listbox"
            style={{ position: "fixed", left: rect.left, top: rect.top, width: rect.width }}
            className="z-[80] max-h-64 overflow-auto bg-slate-900 border border-slate-800 rounded-xl shadow-2xl p-1 text-sm animate-pop-in"
          >
            {(options ?? []).map((rec, idx) => (
              <li
                key={String(rec[valueField])}
                role="option"
                aria-selected={String(rec[valueField]) === String(value)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  choose(rec);
                }}
                onMouseEnter={() => setHighlight(idx)}
                className={`flex items-center gap-2 rounded-lg px-2.5 py-2 cursor-pointer ${
                  idx === highlight ? "bg-slate-800/70 text-slate-100" : "text-slate-300"
                }`}
              >
                <div className="flex-1 min-w-0">
                  <div className={`truncate ${String(rec[valueField]) === String(value) ? "font-medium text-indigo-300" : ""}`}>
                    {recordLabel(link, rec)}
                  </div>
                  {link.subLabel?.(rec) && <div className="text-[11px] text-slate-500 truncate">{link.subLabel(rec)}</div>}
                </div>
                {String(rec[valueField]) === String(value) && <Check className="w-4 h-4 shrink-0 text-indigo-400" />}
              </li>
            ))}
            {!isFetching && (options ?? []).length === 0 && (
              <li className="px-2.5 py-2 text-slate-500">{t("common.no_results")}</li>
            )}
            {isFetching && !options && <li className="px-2.5 py-2 text-slate-500">{t("common.loading")}</li>}
          </ul>,
          document.body
        )}
    </div>
  );
};
