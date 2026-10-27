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

  const renderCell = (col: ReportColumnDef, value: any): React.ReactNode => {
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

  return (
    <Layout>
      <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-xl overflow-hidden">
        <div className="p-5 border-b border-slate-800 space-y-4">
          <h1 className="text-xl font-bold text-slate-100 tracking-tight flex items-center gap-2">
            {t(config.titleKey)}
            {isFetching && <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}
          </h1>
          {config.filters && config.filters.length > 0 && (
            <div className="flex flex-wrap items-end gap-4">
              {config.filters.map((f) => (
                <div key={f.key} className="w-56 max-w-full space-y-1">
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
                className="bg-slate-800 hover:bg-slate-700 text-slate-200 px-3 py-2 rounded-lg text-sm flex items-center gap-1.5"
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
        ) : (
          <div className="overflow-x-auto">
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
                {isLoading ? (
                  <tr>
                    <td colSpan={config.columns.length} className="px-6 py-10 text-center text-slate-400">
                      {t("common.loading")}
                    </td>
                  </tr>
                ) : rows.length === 0 ? (
                  <tr>
                    <td colSpan={config.columns.length} className="px-6 py-12 text-center text-slate-500">
                      {t("no_records")}
                    </td>
                  </tr>
                ) : (
                  rows.map((row, i) => (
                    <tr key={String(row.id ?? i)} className="hover:bg-slate-800/40 transition-colors">
                      {config.columns.map((c) => (
                        <td key={c.key} className={`px-5 py-3 ${alignRight(c) ? "text-right" : ""}`}>
                          {renderCell(c, row[c.key])}
                        </td>
                      ))}
                    </tr>
                  ))
                )}
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
                              ? renderCell(c, (data as any)[src])
                              : ""}
                        </td>
                      );
                    })}
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        )}
      </div>
    </Layout>
  );
};
