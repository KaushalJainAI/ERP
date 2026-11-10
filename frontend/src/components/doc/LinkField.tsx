import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, ChevronDown, Loader2, Plus, X } from "lucide-react";
import { createRecord, getRecord, listRecords, normaliseError, summariseError } from "@/api/endpoints";
import { useDebounced } from "@/hooks/useDebounced";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
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

type QuickCreate = NonNullable<LinkDef["quickCreate"]>;

const recordLabel = (link: LinkDef, rec: LinkRecord): string =>
  link.label ? link.label(rec) : String(rec[link.labelField || "name"] ?? rec[link.valueField || "id"] ?? "");

const inputCls =
  "w-full min-h-[44px] bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 hover:border-slate-700 focus:outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 disabled:opacity-60";
const labelCls = "block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5";
const btnCls =
  "inline-flex min-h-[44px] items-center justify-center gap-1.5 px-4 rounded-lg text-sm font-medium disabled:opacity-50 transition-colors";

/** Small sheet for the extra fields of a quick create (e.g. an item's GST %). */
const QuickAddSheet: React.FC<{
  title: string;
  fields: NonNullable<QuickCreate["extraFields"]>;
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  error: string | null;
  busy: boolean;
  onClose: () => void;
  onSave: () => void;
}> = ({ title, fields, values, onChange, error, busy, onClose, onSave }) => {
  const { t } = useTranslation();
  return (
    <Modal
      open
      title={title}
      onClose={() => {
        if (!busy) onClose();
      }}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy} className={`${btnCls} bg-slate-900 border border-slate-800 text-slate-100 hover:bg-slate-800`}>
            {t("common.close")}
          </button>
          <button type="submit" form="quick-add-form" disabled={busy} className={`${btnCls} bg-indigo-600 hover:bg-indigo-500 text-white`}>
            {busy && <Loader2 className="w-4 h-4 animate-spin" />}
            {t("common.save_short")}
          </button>
        </>
      }
    >
      <form
        id="quick-add-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy) onSave();
        }}
        className="space-y-3"
      >
        {fields.map((f) => (
          <label key={f.key} className="block">
            <span className={labelCls}>{t(f.labelKey)}</span>
            {f.type === "select" ? (
              <select value={values[f.key] ?? ""} onChange={(e) => onChange(f.key, e.target.value)} disabled={busy} className={inputCls}>
                <option value="">{t("common.select")}</option>
                {(f.options ?? []).map((o) => (
                  <option key={o.value} value={o.value}>
                    {t(o.labelKey)}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                value={values[f.key] ?? ""}
                onChange={(e) => onChange(f.key, e.target.value)}
                disabled={busy}
                className={inputCls}
              />
            )}
          </label>
        ))}
        {error && (
          <p role="alert" className="text-sm text-rose-300">
            {error}
          </p>
        )}
      </form>
    </Modal>
  );
};

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
  const toast = useToast();
  const queryClient = useQueryClient();
  const valueField = link.valueField || "id";
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [highlight, setHighlight] = useState(0);
  const debounced = useDebounced(search, 250);
  const inputRef = useRef<HTMLInputElement>(null);
  const [rect, setRect] = useState<{ left: number; top: number; width: number } | null>(null);
  // Open quick-create sheet: the text to create and the extra field values.
  const [sheet, setSheet] = useState<{ text: string; extra: Record<string, string> } | null>(null);
  const [sheetError, setSheetError] = useState<string | null>(null);

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

  // Quick create: the "Add '<text>'" row shows only once the lookup for the typed text has settled,
  // so a half-typed or still-loading search cannot create a duplicate.
  const quick: QuickCreate | undefined = link.quickCreate;
  const quickText = search.trim();
  const showQuick = !!quick && open && !disabled && quickText.length > 0 && debounced.trim() === quickText && !isFetching;

  const createMut = useMutation({
    mutationFn: (req: { text: string; extra: Record<string, string>; inSheet: boolean }) =>
      createRecord(quick!.endpoint, quick!.payload(req.text, req.extra)),
    onSuccess: (rec) => {
      setSheet(null);
      setSheetError(null);
      // Other fields that search the same endpoint should see the new record.
      queryClient.invalidateQueries({
        predicate: (q) => q.queryKey[0] === "lookup" && q.queryKey[2] === link.endpoint,
      });
      choose(rec as LinkRecord);
      const warnings = Array.isArray(rec.warnings) ? (rec.warnings as unknown[]).map(String) : [];
      warnings.forEach((w) => toast.info(w));
    },
    onError: (err, req) => {
      const msg = summariseError(normaliseError(err)) || t("common.action_failed");
      if (req.inSheet) setSheetError(msg);
      else toast.error(msg);
    },
  });

  const startQuick = () => {
    if (!quick || !quickText || createMut.isPending) return;
    const fields = quick.extraFields ?? [];
    if (fields.length === 0) {
      createMut.mutate({ text: quickText, extra: {}, inSheet: false });
      return;
    }
    setSheet({
      text: quickText,
      extra: Object.fromEntries(fields.map((f) => [f.key, f.defaultValue ?? ""])),
    });
    setSheetError(null);
    setOpen(false);
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
    const rowCount = list.length + (showQuick ? 1 : 0);
    if (e.key === "ArrowDown") {
      setHighlight((h) => Math.min(h + 1, Math.max(rowCount - 1, 0)));
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      setHighlight((h) => Math.max(h - 1, 0));
      e.preventDefault();
    } else if (e.key === "Enter") {
      if (highlight === addIdx) startQuick();
      else if (list[highlight]) choose(list[highlight]);
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

  const addIdx = showQuick ? (options ?? []).length : -1;

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
                className={`flex min-h-[44px] items-center gap-2 rounded-lg px-2.5 py-2 cursor-pointer ${
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
            {showQuick && (
              <li
                role="option"
                aria-selected={false}
                aria-disabled={createMut.isPending}
                onMouseDown={(e) => {
                  e.preventDefault();
                  startQuick();
                }}
                onMouseEnter={() => setHighlight(addIdx)}
                className={`flex min-h-[44px] items-center gap-2 rounded-lg px-2.5 py-2 cursor-pointer border-t border-slate-800 text-indigo-300 ${
                  addIdx === highlight ? "bg-slate-800/70 text-indigo-200" : ""
                }`}
              >
                {createMut.isPending ? <Loader2 className="w-4 h-4 shrink-0 animate-spin" /> : <Plus className="w-4 h-4 shrink-0" />}
                <span className="truncate">{t("link.quick_add", { text: quickText })}</span>
              </li>
            )}
          </ul>,
          document.body
        )}

      {sheet && quick && (
        <QuickAddSheet
          title={t("link.quick_add_title", { text: sheet.text })}
          fields={quick.extraFields ?? []}
          values={sheet.extra}
          onChange={(key, v) => setSheet((s) => (s ? { ...s, extra: { ...s.extra, [key]: v } } : s))}
          error={sheetError}
          busy={createMut.isPending}
          onClose={() => {
            setSheet(null);
            setSheetError(null);
          }}
          onSave={() => sheet && createMut.mutate({ text: sheet.text, extra: sheet.extra, inSheet: true })}
        />
      )}
    </div>
  );
};
