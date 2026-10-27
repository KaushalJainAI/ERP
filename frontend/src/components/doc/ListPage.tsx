import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight, Loader2, Plus, Search, ShieldAlert } from "lucide-react";
import { Layout } from "@/components/Layout";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { listRecords, normaliseError, PAGE_SIZE } from "@/api/endpoints";
import { useDebounced } from "@/hooks/useDebounced";
import { formatDate, formatIndianCurrency, formatIndianNumber } from "@/lib/format";
import type { ApiRecord, ListColumnDef, ResourceConfig } from "@/types";

const DOCSTATUS_FILTERS = [
  { value: "", labelKey: "common.all_statuses" },
  { value: "0", labelKey: "draft" },
  { value: "1", labelKey: "submitted" },
  { value: "2", labelKey: "cancelled" },
];

export const ListPage: React.FC<{ config: ResourceConfig }> = ({ config }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  // The status filter lives in the URL (?docstatus=0|1|2) so dashboard cards can deep-link to it.
  const [searchParams, setSearchParams] = useSearchParams();
  const rawStatus = searchParams.get("docstatus") ?? "";
  const docstatus = DOCSTATUS_FILTERS.some((f) => f.value === rawStatus) ? rawStatus : "";
  const setDocstatus = (value: string) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set("docstatus", value);
        else next.delete("docstatus");
        return next;
      },
      { replace: true }
    );
  const [page, setPage] = useState(1);
  const debounced = useDebounced(search, 300);
  const base = `/${config.module}/${config.key}`;

  // Back to page 1 whenever the query changes.
  useEffect(() => setPage(1), [debounced, docstatus, config.endpoint]);

  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ["list", config.endpoint, { page, search: debounced, docstatus }],
    queryFn: () =>
      listRecords(config.endpoint, {
        page,
        search: debounced,
        ordering: config.ordering ?? "-created_at",
        docstatus: config.submittable ? docstatus : undefined,
      }),
    placeholderData: keepPreviousData,
  });

  const err = error ? normaliseError(error) : null;
  const rows = data?.results ?? [];
  const totalPages = Math.max(1, Math.ceil((data?.count ?? 0) / PAGE_SIZE));

  const renderCell = (col: ListColumnDef, row: ApiRecord): React.ReactNode => {
    const value = row[col.key];
    if (col.render) return col.render(value, row);
    switch (col.type) {
      case "status":
        return <StatusBadge docstatus={value} />;
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

  const alignRight = (col: ListColumnDef) => col.type === "money" || col.type === "number";

  return (
    <Layout>
      <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-xl overflow-hidden">
        <div className="p-5 border-b border-slate-800 flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-xl font-bold text-slate-100 tracking-tight flex items-center gap-2">
            {t(config.titleKey)}
            {isFetching && <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}
          </h1>
          <div className="flex flex-wrap items-center gap-3">
            {config.submittable && (
              <select
                value={docstatus}
                onChange={(e) => setDocstatus(e.target.value)}
                aria-label={t("status")}
                className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
              >
                {DOCSTATUS_FILTERS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {t(o.labelKey)}
                  </option>
                ))}
              </select>
            )}
            <div className="relative">
              <Search className="w-4 h-4 absolute left-3 top-3 text-slate-400" />
              <input
                type="text"
                placeholder={t("search")}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="bg-slate-950 border border-slate-800 rounded-lg pl-9 pr-4 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 w-64 max-w-full"
              />
            </div>
            {!config.listOnly && (
            <button
              type="button"
              onClick={() => navigate(`${base}/new`)}
              className="bg-indigo-600 hover:bg-indigo-500 text-white font-medium px-4 py-2 rounded-lg text-sm shadow-lg shadow-indigo-600/20 flex items-center gap-1.5"
            >
              <Plus className="w-4 h-4" />
              {t("common.new_item", { item: t(config.singularKey) })}
            </button>
            )}
          </div>
        </div>

        {err ? (
          <div className="px-6 py-12 text-center space-y-2">
            <ShieldAlert className="w-8 h-8 mx-auto text-rose-400" />
            <p className="text-sm text-rose-300">
              {err.forbidden ? t("common.forbidden") : t("common.load_failed")}
            </p>
            {!err.forbidden && err.general[0] && <p className="text-xs text-slate-500">{err.general[0]}</p>}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-300">
              <thead className="bg-slate-950/80 text-xs uppercase text-slate-400 font-semibold border-b border-slate-800">
                <tr>
                  {config.columns.map((col) => (
                    <th key={col.key} className={`px-5 py-3.5 ${alignRight(col) ? "text-right" : ""}`}>
                      {t(col.labelKey)}
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
                  rows.map((row) => (
                    <tr
                      key={String(row.id)}
                      onClick={config.listOnly ? undefined : () => navigate(`${base}/${row.id}`)}
                      className={`hover:bg-slate-800/40 transition-colors ${config.listOnly ? "" : "cursor-pointer"}`}
                    >
                      {config.columns.map((col) => (
                        <td key={col.key} className={`px-5 py-3 ${alignRight(col) ? "text-right" : ""}`}>
                          {renderCell(col, row)}
                        </td>
                      ))}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}

        {!err && (data?.count ?? 0) > 0 && (
          <div className="px-5 py-3 border-t border-slate-800 flex items-center justify-between text-xs text-slate-400">
            <span>{t("common.total_records", { count: data?.count ?? 0 })}</span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                className="p-1.5 rounded-md bg-slate-800 hover:bg-slate-700 disabled:opacity-40"
                aria-label={t("common.previous")}
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span>{t("common.page_of", { page, total: totalPages })}</span>
              <button
                type="button"
                disabled={!data?.next}
                onClick={() => setPage((p) => p + 1)}
                className="p-1.5 rounded-md bg-slate-800 hover:bg-slate-700 disabled:opacity-40"
                aria-label={t("common.next")}
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
};
