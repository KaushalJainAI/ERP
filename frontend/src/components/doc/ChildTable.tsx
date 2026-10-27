import React, { useEffect } from "react";
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

  return (
    <section className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-200">{t(def.titleKey)}</h3>
        <span className="text-xs text-slate-500">{t("common.row_count", { count: rows.length })}</span>
      </div>

      {tableErrors.length > 0 && (
        <div className="px-5 py-2 text-xs text-rose-300 bg-rose-950/40 border-b border-rose-900/60">
          {tableErrors.join(" ")}
        </div>
      )}

      <div className="overflow-x-auto">
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
                  if (isColumnHidden(col, row, parentValues)) return <td key={col.key} className={`px-2 py-1.5 ${col.widthClass ?? ""}`} />;
                  const colErrors = errors[idx]?.[col.key];
                  const numeric = col.type === "number" || col.type === "decimal";
                  const isComputed = !!col.preview || col.readOnly;
                  return (
                    <td key={col.key} className={`px-2 py-1.5 ${col.widthClass ?? ""}`}>
                      {readOnly || isComputed ? (
                        <div className={`px-2 py-1.5 text-xs tabular-nums ${numeric ? "text-right" : ""}`}>
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
                          onChange={(v) => setCell(idx, { [col.key]: v })}
                          onLinkSelect={(v, rec: LinkRecord | null) =>
                            setCell(idx, {
                              [col.key]: v,
                              ...(rec && col.link?.fill ? col.link.fill(rec, row) : {}),
                            })
                          }
                        />
                      )}
                      {colErrors && <p className="text-[11px] text-rose-400 mt-1">{colErrors.join(" ")}</p>}
                    </td>
                  );
                })}
                {!readOnly && (
                  <td className="px-1 py-1.5">
                    <button
                      type="button"
                      onClick={() => removeRow(idx)}
                      className="p-1.5 rounded-md text-slate-500 hover:text-rose-400 hover:bg-slate-800"
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
        <div className="px-4 py-3 border-t border-slate-800">
          <button
            type="button"
            onClick={addRow}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-indigo-300 bg-indigo-600/10 hover:bg-indigo-600/20 border border-indigo-500/30"
          >
            <Plus className="w-3.5 h-3.5" /> {t("common.add_row")}
          </button>
        </div>
      )}
    </section>
  );
};
