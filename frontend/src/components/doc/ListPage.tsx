import React, { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Folder,
  FolderOpen,
  FolderTree,
  List,
  Loader2,
  Plus,
  Search,
  ShieldAlert,
} from "lucide-react";
import { Layout } from "@/components/Layout";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { listAllRecords, listRecords, normaliseError, PAGE_SIZE } from "@/api/endpoints";
import { useDebounced } from "@/hooks/useDebounced";
import { flattenTree, matchWithAncestors, parentIds } from "@/lib/tree";
import { formatDate, formatIndianCurrency, formatIndianNumber } from "@/lib/format";
import type { ApiRecord, ListColumnDef, ResourceConfig } from "@/types";
import { Select } from "@/components/ui/Select";
import { HistoryBanner } from "@/pages/history/HistoryBanner";

const DOCSTATUS_FILTERS = [
  { value: "", labelKey: "common.all_statuses" },
  { value: "0", labelKey: "draft" },
  { value: "1", labelKey: "submitted" },
  { value: "2", labelKey: "cancelled" },
];

export const ListPage: React.FC<{ config: ResourceConfig }> = ({ config }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // The status filter lives in the URL (?docstatus=0|1|2) so home cards can deep-link to it;
  // ?search= pre-fills the search box (the GST reports link invoices by document number).
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("search") ?? "");
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
  // A different resource starts from the default tree state.
  useEffect(() => {
    setExpanded(null);
    setTreeMode(!config.tree?.defaultFlat);
  }, [config.endpoint, config.tree?.defaultFlat]);

  // Tree mode (ERPNext tree view): load the whole hierarchy, search/expand client-side.
  const tree = config.tree;
  const [treeMode, setTreeMode] = useState(!config.tree?.defaultFlat);
  const treeOn = !!tree && treeMode;
  const [expanded, setExpanded] = useState<Set<string> | null>(null);

  const treeQuery = useQuery({
    queryKey: ["list", config.endpoint, { tree: true, docstatus }],
    queryFn: () =>
      listAllRecords(config.endpoint, {
        ordering: config.ordering ?? "-created_at",
        docstatus: config.submittable ? docstatus : undefined,
      }),
    enabled: treeOn,
    placeholderData: keepPreviousData,
  });

  const flatQuery = useQuery({
    queryKey: ["list", config.endpoint, { page, search: debounced, docstatus }],
    enabled: !treeOn,
    queryFn: () =>
      listRecords(config.endpoint, {
        page,
        search: debounced,
        ordering: config.ordering ?? "-created_at",
        docstatus: config.submittable ? docstatus : undefined,
      }),
    placeholderData: keepPreviousData,
  });

  const data = flatQuery.data;
  const { isLoading, isFetching, error } = treeOn ? treeQuery : flatQuery;
  const err = error ? normaliseError(error) : null;
  const rows = data?.results ?? [];

  const allTreeRows = treeQuery.data;
  const treeRows = useMemo(() => {
    if (!treeOn || !tree || !allTreeRows) return [];
    const q = debounced.trim().toLowerCase();
    const visible = q
      ? matchWithAncestors(allTreeRows, tree.parentKey, (r) =>
          config.columns.some((c) => {
            const v = r[c.key];
            return (typeof v === "string" || typeof v === "number") && String(v).toLowerCase().includes(q);
          })
        )
      : null;
    return flattenTree(allTreeRows, tree.parentKey, expanded, visible);
  }, [treeOn, tree, allTreeRows, debounced, expanded, config.columns]);

  const toggleNode = (id: string, isOpen: boolean) =>
    setExpanded((prev) => {
      const base = prev ?? new Set(treeRows.filter((r) => r.depth === 0 && r.expanded).map((r) => String(r.row.id)));
      const next = new Set(base);
      if (isOpen) next.delete(id);
      else next.add(id);
      return next;
    });
  const expandAll = () => allTreeRows && tree && setExpanded(parentIds(allTreeRows, tree.parentKey));
  const collapseAll = () => setExpanded(new Set());
  const labelColumn = tree?.labelColumn ?? config.columns[0]?.key;
  const groupKey = tree?.groupKey ?? "is_group";
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
      {config.history && <HistoryBanner tab={config.history} />}
      <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-xl overflow-hidden animate-fade-up">
        <div className="p-5 border-b border-slate-800 flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-xl font-semibold text-slate-100 tracking-tight flex items-center gap-2">
            {t(config.titleKey)}
            {isFetching && <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}
          </h1>
          <div className="flex flex-wrap items-center gap-3">
            {config.submittable && (
              <Select
                value={docstatus}
                onChange={setDocstatus}
                aria-label={t("status")}
                className="w-40"
                options={DOCSTATUS_FILTERS.map((o) => ({ value: o.value, label: t(o.labelKey) }))}
              />
            )}
            {tree && (
              <div className="flex items-center gap-1">
                {treeOn && (
                  <>
                    <button
                      type="button"
                      onClick={expandAll}
                      className="px-2.5 py-2 rounded-lg text-xs text-slate-300 bg-slate-900 hover:bg-slate-800 border border-slate-800 shadow-sm"
                    >
                      {t("common.expand_all")}
                    </button>
                    <button
                      type="button"
                      onClick={collapseAll}
                      className="px-2.5 py-2 rounded-lg text-xs text-slate-300 bg-slate-900 hover:bg-slate-800 border border-slate-800 shadow-sm"
                    >
                      {t("common.collapse_all")}
                    </button>
                  </>
                )}
                <button
                  type="button"
                  onClick={() => setTreeMode((m) => !m)}
                  aria-pressed={treeOn}
                  className="px-2.5 py-2 rounded-lg text-xs text-indigo-300 bg-indigo-600/10 hover:bg-indigo-600/20 border border-indigo-500/30 inline-flex items-center gap-1.5"
                >
                  {treeOn ? <List className="w-4 h-4" /> : <FolderTree className="w-4 h-4" />}
                  {treeOn ? t("common.list_view") : t("common.tree_view")}
                </button>
              </div>
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
              <tbody
                className={`divide-y divide-slate-800/60 transition-opacity duration-200 ${
                  flatQuery.isPlaceholderData ? "opacity-60" : "opacity-100"
                }`}
              >
                {isLoading ? (
                  <tr>
                    <td colSpan={config.columns.length} className="px-6 py-10 text-center text-slate-400">
                      {t("common.loading")}
                    </td>
                  </tr>
                ) : treeOn ? (
                  treeRows.length === 0 ? (
                    <tr>
                      <td colSpan={config.columns.length} className="px-6 py-12 text-center text-slate-500">
                        {t("no_records")}
                      </td>
                    </tr>
                  ) : (
                    treeRows.map(({ row, depth, hasChildren, expanded: isOpen }) => (
                      <tr
                        key={String(row.id)}
                        onClick={config.listOnly ? undefined : () => navigate(`${base}/${row.id}`)}
                        className={`hover:bg-slate-800/40 transition-colors ${config.listOnly ? "" : "cursor-pointer"}`}
                      >
                        {config.columns.map((col) =>
                          col.key === labelColumn ? (
                            <td key={col.key} className="px-5 py-2">
                              <div className="flex items-center gap-1.5" style={{ paddingInlineStart: `${depth * 20}px` }}>
                                {hasChildren ? (
                                  <button
                                    type="button"
                                    aria-expanded={isOpen}
                                    aria-label={t(isOpen ? "common.collapse_node" : "common.expand_node")}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      toggleNode(String(row.id), isOpen);
                                    }}
                                    className="p-0.5 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800"
                                  >
                                    {isOpen ? (
                                      <ChevronDown className="w-4 h-4" />
                                    ) : (
                                      <ChevronRight className="w-4 h-4 rtl:rotate-180" />
                                    )}
                                  </button>
                                ) : (
                                  <span className="w-5 inline-block" />
                                )}
                                {row[groupKey] ? (
                                  isOpen ? (
                                    <FolderOpen className="w-4 h-4 text-amber-400/80 shrink-0" />
                                  ) : (
                                    <Folder className="w-4 h-4 text-amber-400/80 shrink-0" />
                                  )
                                ) : null}
                                <span className={row[groupKey] ? "font-medium text-slate-100" : ""}>
                                  {renderCell(col, row)}
                                </span>
                              </div>
                            </td>
                          ) : (
                            <td key={col.key} className={`px-5 py-2 ${alignRight(col) ? "text-right" : ""}`}>
                              {renderCell(col, row)}
                            </td>
                          )
                        )}
                      </tr>
                    ))
                  )
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

        {!err && treeOn && (allTreeRows?.length ?? 0) > 0 && (
          <div className="px-5 py-3 border-t border-slate-800 text-xs text-slate-400">
            {t("common.total_records", { count: allTreeRows?.length ?? 0 })}
          </div>
        )}

        {!err && !treeOn && (data?.count ?? 0) > 0 && (
          <div className="px-5 py-3 border-t border-slate-800 flex items-center justify-between text-xs text-slate-400">
            <span>{t("common.total_records", { count: data?.count ?? 0 })}</span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                className="p-1.5 rounded-md bg-slate-900 hover:bg-slate-800 border border-slate-800 shadow-sm disabled:opacity-40"
                aria-label={t("common.previous")}
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span>{t("common.page_of", { page, total: totalPages })}</span>
              <button
                type="button"
                disabled={!data?.next}
                onClick={() => setPage((p) => p + 1)}
                className="p-1.5 rounded-md bg-slate-900 hover:bg-slate-800 border border-slate-800 shadow-sm disabled:opacity-40"
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
