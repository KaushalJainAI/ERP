import React, { useMemo, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Loader2, RotateCcw, ShieldAlert } from "lucide-react";
import { Layout } from "@/components/Layout";
import { FieldInput } from "./FieldInput";
import { apiClient } from "@/api/client";
import { normaliseError } from "@/api/endpoints";
import { formatDate, formatIndianCurrency, formatIndianNumber } from "@/lib/format";
import type { ApiRecord } from "@/types";
import type { ReportColumnDef, ReportConfig, ReportFilterDef } from "@/types/report";

type Filters = Record<string, any>;

const initialFilters = (defs: ReportFilterDef[] = []): Filters => {
  const out: Filters = {};
  defs.forEach((f) => {
    const d = typeof f.defaultValue === "function" ? f.defaultValue() : f.defaultValue;
    out[f.key] = d ?? (f.type === "boolean" ? false : "");
  });
  return out;
};

const extractRows = (data: any, rowsKey?: string): ApiRecord[] => {
  if (Array.isArray(data)) return data;
  if (rowsKey && Array.isArray(data?.[rowsKey])) return data[rowsKey];
  if (Array.isArray(data?.rows)) return data.rows;
  if (Array.isArray(data?.results)) return data.results;
  return [];
};

/** Resolve a dotted property path; plain keys behave as before. */
const getPath = (obj: any, path: string): any =>
  path.split(".").reduce((acc, part) => (acc === null || acc === undefined ? undefined : acc[part]), obj);

const alignRight = (c: ReportColumnDef) => c.type === "money" || c.type === "number";

/**
 * Generic read-only report page: filter inputs + table (+ optional server totals).
 * Driven entirely by a ReportConfig (see src/types/report.ts and resources/stock.ts).
 */
export const ReportPage: React.FC<{ config: ReportConfig }> = ({ config }) => {
  const { t } = useTranslation();
  const [filters, setFilters] = useState<Filters>(() => initialFilters(config.filters));

  const params = useMemo(() => {
    const p: Record<string, string | number | boolean> = {};
    Object.entries(filters).forEach(([k, v]) => {
      if (v !== "" && v !== null && v !== undefined && v !== false) p[k] = v;
    });
    return p;
  }, [filters]);

  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ["report", config.endpoint, params],
    queryFn: async () => (await apiClient.get(config.endpoint, { params })).data,
    placeholderData: keepPreviousData,
  });

  const err = error ? normaliseError(error) : null;
  const rows = extractRows(data, config.rowsKey);

  const renderCell = (col: ReportColumnDef, value: any, row?: ApiRecord): React.ReactNode => {
    if (col.render) return col.render(value, row ?? {});
    switch (col.type) {
      case "money":
        return <span className="tabular-nums">{formatIndianCurrency(value)}</span>;
      case "number":
        return <span className="tabular-nums">{formatIndianNumber(value)}</span>;
      case "date":
        return formatDate(value);
      case "boolean":
        return value ? t("common.yes") : t("common.no");
      default:
        return value === null || value === undefined ? "" : String(value);
    }
  };

  const hasTotals = config.totals && data && !Array.isArray(data);
  const titleColumn = config.columns[0];
  const detailColumns = config.columns.slice(1, 6);
  const empty = rows.length === 0;

  return (
    <Layout>
      <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-xl overflow-hidden">
        <div className="space-y-3 border-b border-slate-800 p-4 sm:space-y-4 sm:p-5">
          <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-slate-100 sm:text-xl">
            {t(config.titleKey)}
            {isFetching && <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}
          </h1>
          {config.filters && config.filters.length > 0 && (
            <div className="flex flex-wrap items-end gap-3 sm:gap-4">
              {config.filters.map((f) => (
                <div key={f.key} className="w-full space-y-1 sm:w-56">
                  <label htmlFor={`rf-${f.key}`} className="block text-xs font-medium text-slate-400">
                    {t(f.labelKey)}
                  </label>
                  <FieldInput
                    id={`rf-${f.key}`}
                    def={{ key: f.key, type: f.type, options: f.options, link: f.link }}
                    cacheKey={`report.${config.key}.${f.key}`}
                    value={filters[f.key]}
                    values={filters}
                    parentValues={filters}
                    onChange={(v) => setFilters((s) => ({ ...s, [f.key]: v }))}
                  />
                </div>
              ))}
              <button
                type="button"
                onClick={() => setFilters(initialFilters(config.filters))}
                className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-slate-200 shadow-sm hover:bg-slate-800 sm:w-auto"
              >
                <RotateCcw className="w-4 h-4" />
                {t("bs.report.reset")}
              </button>
            </div>
          )}
        </div>

        {err ? (
          <div className="px-6 py-12 text-center space-y-2">
            <ShieldAlert className="w-8 h-8 mx-auto text-rose-400" />
            <p className="text-sm text-rose-300">{err.forbidden ? t("common.forbidden") : t("common.load_failed")}</p>
            {!err.forbidden && err.general[0] && <p className="text-xs text-slate-500">{err.general[0]}</p>}
          </div>
        ) : isLoading ? (
          <div className="flex items-center justify-center gap-2 px-6 py-16 text-sm text-slate-400" role="status">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t("common.loading")}
          </div>
        ) : empty ? (
          <div className="px-6 py-16 text-center text-sm text-slate-500" role="status">
            {t("no_records")}
          </div>
        ) : (
          <>
            <div className="space-y-2 p-3 md:hidden">
              {rows.map((row, i) => (
                <div key={String(row.id ?? i)} className="rounded-xl border border-slate-800 bg-slate-900 p-3.5 shadow-sm">
                  <p className="truncate text-sm font-semibold text-slate-100">
                    {renderCell(titleColumn, getPath(row, titleColumn.key), row)}
                  </p>
                  {detailColumns.length > 0 && (
                    <dl className="mt-2.5 grid grid-cols-2 gap-x-3 gap-y-2">
                      {detailColumns.map((c) => (
                        <div key={c.key} className="min-w-0">
                          <dt className="truncate text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                            {t(c.labelKey)}
                          </dt>
                          <dd className={`mt-0.5 truncate text-xs text-slate-300 ${alignRight(c) ? "tabular-nums" : ""}`}>
                            {renderCell(c, getPath(row, c.key), row)}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </div>
              ))}
            </div>

            <div className="hidden overflow-x-auto md:block">
              <table className="w-full text-left text-sm text-slate-300">
                <thead className="bg-slate-950/80 text-xs uppercase text-slate-400 font-semibold border-b border-slate-800">
                  <tr>
                    {config.columns.map((c) => (
                      <th key={c.key} className={`px-5 py-3.5 ${alignRight(c) ? "text-right" : ""}`}>
                        {t(c.labelKey)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {rows.map((row, i) => (
                    <tr key={String(row.id ?? i)} className="hover:bg-slate-800/40 transition-colors">
                      {config.columns.map((c) => (
                        <td key={c.key} className={`px-5 py-3 ${alignRight(c) ? "text-right" : ""}`}>
                          {renderCell(c, getPath(row, c.key), row)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
                {hasTotals && rows.length > 0 && (
                  <tfoot className="bg-slate-950/60 border-t border-slate-700 font-semibold text-slate-100">
                    <tr>
                      {config.columns.map((c, idx) => {
                        const src = config.totals![c.key];
                        return (
                          <td key={c.key} className={`px-5 py-3 ${alignRight(c) ? "text-right" : ""}`}>
                            {idx === 0 && !src
                              ? t(config.totalsLabelKey ?? "bs.report.total")
                              : src
                                ? renderCell(c, getPath(data, src))
                                : ""}
                          </td>
                        );
                      })}
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>

            {hasTotals && rows.length > 0 && (
              <div className="border-t border-slate-700 bg-slate-950/60 p-4 md:hidden">
                <dl className="space-y-2">
                  {config.columns.map((c, idx) => {
                    const src = config.totals![c.key];
                    const isLabel = idx === 0 && !src;
                    const value = isLabel ? null : src ? renderCell(c, getPath(data, src)) : null;
                    if (!isLabel && (value === null || value === "")) return null;
                    return (
                      <div key={c.key} className="flex items-baseline justify-between gap-3 text-xs">
                        <dt className={isLabel ? "font-semibold text-slate-100" : "text-slate-400"}>
                          {isLabel ? t(config.totalsLabelKey ?? "bs.report.total") : t(c.labelKey)}
                        </dt>
                        {!isLabel && <dd className="font-semibold tabular-nums text-slate-100">{value}</dd>}
                      </div>
                    );
                  })}
                </dl>
              </div>
            )}
          </>
        )}
      </div>
    </Layout>
  );
};
