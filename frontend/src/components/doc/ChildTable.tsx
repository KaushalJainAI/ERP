import React, { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Trash2 } from "lucide-react";
import { FieldInput, formatReadOnly } from "./FieldInput";
import type { ChildColumnDef, ChildTableDef, LinkRecord } from "@/types";

export type Row = Record<string, any>;

interface ChildTableProps {
  def: ChildTableDef;
  cacheKeyBase: string;
  rows: Row[];
  parentValues: Record<string, any>;
  readOnly: boolean;
  /** row index -> column -> messages */
  errors?: Record<number, Record<string, string[]>>;
  tableErrors?: string[];
  onChange: (rows: Row[]) => void;
}

let rowCounter = 0;
export const newRowKey = () => `r${++rowCounter}`;

/** Value shown in a computed column: server value if its inputs are unchanged, else a client estimate. */
export function computedValue(col: ChildColumnDef, row: Row): string | null {
  // A server-owned read-only column (no client estimate) always shows the row's current value,
  // which is the saved value after a load and the dry-run value after a preview.
  if (!col.preview) return row[col.key] ?? null;
  const srv = row._srv as Row | undefined;
  if (srv && srv[col.key] !== undefined && srv[col.key] !== null) {
    const deps = col.dependsOn ?? [];
    if (deps.every((d) => String(srv[d] ?? "") === String(row[d] ?? ""))) return String(srv[col.key]);
  }
  return col.preview ? col.preview(row) : (row[col.key] ?? null);
}

const hasValue = (v: unknown) => v !== "" && v !== null && v !== undefined;

/** True when a conditional column is hidden for this row. */
export const isColumnHidden = (col: ChildColumnDef, row: Row, parentValues: Record<string, any>) =>
  !!col.hidden?.(row, parentValues);

/**
 * Clear the values of columns hidden for a row. Returns the same array when
 * nothing changed, so callers can cheaply detect "nothing to do".
 */
export function clearHiddenValues(def: ChildTableDef, rows: Row[], parentValues: Record<string, any>): Row[] {
  if (!def.columns.some((c) => c.hidden)) return rows;
  let changed = false;
  const out = rows.map((row) => {
    let next = row;
    def.columns.forEach((c) => {
      if (c.hidden && hasValue(next[c.key]) && isColumnHidden(c, next, parentValues)) {
        next = { ...next, [c.key]: "" };
        changed = true;
      }
    });
    return next;
  });
  return changed ? out : rows;
}

export const ChildTable: React.FC<ChildTableProps> = ({
  def,
  cacheKeyBase,
  rows,
  parentValues,
  readOnly,
  errors = {},
  tableErrors = [],
  onChange,
}) => {
  const { t } = useTranslation();

  // Columns hidden for every row (or for the blank row when the table is empty).
  const visibleColumns = def.columns.filter(
    (c) => !(rows.length ? rows : [{}]).every((r) => isColumnHidden(c, r, parentValues))
  );

  // A hidden column must not keep a stale value (e.g. after the entry type changes).
  useEffect(() => {
    if (readOnly) return;
    const cleared = clearHiddenValues(def, rows, parentValues);
    if (cleared !== rows) onChange(cleared);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, parentValues, readOnly]);

  const setCell = (idx: number, patch: Row) => {
    onChange(rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  };

  // Latest rows for async fills that resolve after further edits.
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  const runFillAsync = (col: ChildColumnDef, rowKey: unknown, picked: unknown, rec: LinkRecord, row: Row) => {
    col.link
      ?.fillAsync?.(rec, row, parentValues)
      .then((patch) => {
        const current = rowsRef.current;
        const target = current.find((r) => r._key === rowKey);
        if (!target || target[col.key] !== picked) return;
        // Never overwrite what the user has entered in the meantime.
        const fresh = Object.fromEntries(
          Object.entries(patch).filter(([k]) => target[k] === undefined || target[k] === null || target[k] === "")
        );
        if (Object.keys(fresh).length === 0) return;
        onChangeRef.current(current.map((r) => (r === target ? { ...r, ...fresh } : r)));
      })
      .catch(() => {
        /* a missing rate is not an error: the server fills it on save */
      });
  };

  const addRow = () => {
    const prev = rows[rows.length - 1];
    const inherited: Row = {};
    (def.inheritFromPrevious ?? []).forEach((k) => {
      if (prev && prev[k] !== undefined) inherited[k] = prev[k];
    });
    const fresh = { ...(def.newRow?.() ?? {}), ...inherited, _key: newRowKey() };
    onChange([...rows, ...clearHiddenValues(def, [fresh], parentValues)]);
  };

  const removeRow = (idx: number) => onChange(rows.filter((_, i) => i !== idx));

  /** One column's editor/read-only value. Shared by the desktop table and phone cards. */
  const renderCellContent = (col: ChildColumnDef, row: Row, idx: number, mobile = false) => {
    const colErrors = errors[idx]?.[col.key];
    const numeric = col.type === "number" || col.type === "decimal";
    const isComputed = !!col.preview || col.readOnly;
    return (
      <>
        {readOnly || isComputed ? (
          <div
            className={`text-xs tabular-nums ${
              mobile ? "py-1 text-left" : `px-2 py-1.5 ${numeric ? "text-right" : ""}`
            }`}
          >
            {col.type === "link"
              ? (col.link?.display?.(row) ?? String(row[col.key] ?? ""))
              : formatReadOnly(col, isComputed ? computedValue(col, row) : row[col.key])}
          </div>
        ) : (
          <FieldInput
            def={col}
            compact
            cacheKey={`${cacheKeyBase}.${col.key}`}
            value={row[col.key]}
            values={row}
            parentValues={parentValues}
            invalid={!!colErrors}
            placeholder={col.placeholderKey ? t(col.placeholderKey) : undefined}
            onChange={(v) => setCell(idx, { [col.key]: v })}
            onLinkSelect={(v, rec: LinkRecord | null) => {
              const patch = {
                [col.key]: v,
                ...(rec && col.link?.fill ? col.link.fill(rec, row) : {}),
              };
              setCell(idx, patch);
              if (rec && col.link?.fillAsync) runFillAsync(col, row._key, v, rec, { ...row, ...patch });
            }}
          />
        )}
        {col.emptyHint &&
          !colErrors &&
          (row[col.key] === undefined || row[col.key] === null || row[col.key] === "") &&
          col.emptyHint(row, parentValues)}
        {colErrors && <p className="mt-1 text-[11px] text-rose-400">{colErrors.join(" ")}</p>}
      </>
    );
  };

  return (
    <section className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3 sm:px-5">
        <h3 className="text-sm font-semibold text-slate-200">{t(def.titleKey)}</h3>
        <span className="text-xs text-slate-500">{t("common.row_count", { count: rows.length })}</span>
      </div>

      {tableErrors.length > 0 && (
        <div className="border-b border-rose-900/60 bg-rose-950/40 px-4 py-2 text-xs text-rose-300 sm:px-5">
          {tableErrors.join(" ")}
        </div>
      )}

      {/* Phone: one card per row, with every column stacked and labelled. */}
      <div className="space-y-2 p-3 md:hidden">
        {rows.length === 0 ? (
          <p className="px-2 py-6 text-center text-xs text-slate-500">{t("common.no_rows")}</p>
        ) : (
          rows.map((row, idx) => (
            <div key={row._key ?? idx} className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="text-[11px] font-semibold text-slate-500">#{idx + 1}</span>
                {!readOnly && (
                  <button
                    type="button"
                    onClick={() => removeRow(idx)}
                    className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md p-1.5 text-slate-500 hover:bg-slate-800 hover:text-rose-400"
                    aria-label={t("common.remove_row")}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </div>
              <div className="space-y-2.5">
                {visibleColumns.map((col) => {
                  if (isColumnHidden(col, row, parentValues)) return null;
                  const computed = !!col.preview || col.readOnly;
                  const displayValue = computed ? computedValue(col, row) : row[col.key];
                  // Don't stack empty server/computed columns on a phone; they add
                  // six blank labelled rows to every new child row.
                  if (computed && (displayValue === null || displayValue === undefined || displayValue === "")) {
                    return null;
                  }
                  return (
                    <div key={col.key} className="min-w-0">
                      <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                        {t(col.labelKey)}
                        {col.required && !readOnly && <span className="text-rose-400"> *</span>}
                      </label>
                      {renderCellContent(col, row, idx, true)}
                    </div>
                  );
                })}
              </div>
            </div>
          ))
        )}
      </div>

      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-sm text-slate-300">
          <thead className="bg-slate-950/70 text-[11px] uppercase text-slate-400 font-semibold">
            <tr>
              <th className="px-3 py-2.5 w-10 text-left">#</th>
              {visibleColumns.map((col) => (
                <th
                  key={col.key}
                  className={`px-2 py-2.5 ${col.type === "number" || col.type === "decimal" ? "text-right" : "text-left"} ${
                    col.widthClass ?? ""
                  }`}
                >
                  {t(col.labelKey)}
                  {col.required && !readOnly && <span className="text-rose-400"> *</span>}
                </th>
              ))}
              {!readOnly && <th className="w-10" />}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {rows.length === 0 && (
              <tr>
                <td colSpan={visibleColumns.length + 2} className="px-4 py-6 text-center text-slate-500 text-xs">
                  {t("common.no_rows")}
                </td>
              </tr>
            )}
            {rows.map((row, idx) => (
              <tr key={row._key ?? idx} className="align-top">
                <td className="px-3 py-2 text-xs text-slate-500">{idx + 1}</td>
                {visibleColumns.map((col) => {
                  if (isColumnHidden(col, row, parentValues)) {
                    return <td key={col.key} className={`px-2 py-1.5 ${col.widthClass ?? ""}`} />;
                  }
                  return (
                    <td key={col.key} className={`px-2 py-1.5 ${col.widthClass ?? ""}`}>
                      {renderCellContent(col, row, idx)}
                    </td>
                  );
                })}
                {!readOnly && (
                  <td className="px-1 py-1.5">
                    <button
                      type="button"
                      onClick={() => removeRow(idx)}
                      className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center p-1.5 rounded-md text-slate-500 hover:text-rose-400 hover:bg-slate-800"
                      aria-label={t("common.remove_row")}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!readOnly && (
        <div className="border-t border-slate-800 px-4 py-3">
          <button
            type="button"
            onClick={addRow}
            className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-indigo-500/30 bg-indigo-600/10 px-3 py-2 text-xs font-medium text-indigo-300 hover:bg-indigo-600/20 sm:w-auto sm:py-1.5"
          >
            <Plus className="w-3.5 h-3.5" /> {t("common.add_row")}
          </button>
        </div>
      )}
    </section>
  );
};
