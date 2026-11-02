import React, { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
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

const DOCSTATUS_FILTERS = [
  { value: "", labelKey: "common.all_statuses" },
  { value: "0", labelKey: "draft" },
  { value: "1", labelKey: "submitted" },
  { value: "2", labelKey: "cancelled" },
];

export const ListPage: React.FC<{ config: ResourceConfig }> = ({ config }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // The status filter lives in the URL (?docstatus=0|1|2) so home cards can deep-link to it.
  // ?search= is the list filter: the search bar under the top bar writes it, and the GST
  // reports link invoices here by document number.
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get("search") ?? "";
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
  // Exact filters named by the resource (ResourceConfig.urlFilters), e.g. ?employee=12.
  const urlFilterValues: Record<string, string> = {};
  (config.urlFilters ?? []).forEach((k) => {
    const v = searchParams.get(k);
    if (v) urlFilterValues[k] = v;
  });
  const urlFilterKey = JSON.stringify(urlFilterValues);

  // Back to page 1 whenever the query changes.
  useEffect(() => setPage(1), [debounced, docstatus, config.endpoint, urlFilterKey]);
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
    queryKey: ["list", config.endpoint, { page, search: debounced, docstatus, urlFilterKey }],
    enabled: !treeOn,
    queryFn: () =>
      listRecords(config.endpoint, {
        page,
        search: debounced,
        ordering: config.ordering ?? "-created_at",
        docstatus: config.submittable ? docstatus : undefined,
        ...urlFilterValues,
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

  /* ---- phone cards: a wide desktop table is unreadable on a phone ---- */
  const titleColumn = config.columns.find((c) => c.key === config.titleField) ?? config.columns[0];
  const statusColumn = config.columns.find((c) => c.type === "status");
  const subtitleColumn = config.columns.find(
    (c) => c.key !== titleColumn.key && c.key !== statusColumn?.key && c.type !== "status"
  );
  const hasValue = (row: ApiRecord, col: ListColumnDef) => {
    const v = row[col.key];
    return v !== null && v !== undefined && v !== "" && !(col.type === "boolean" && v === false);
  };
  const detailColumns = (() => {
    const rest = config.columns.filter(
      (c) => c.key !== titleColumn.key && c.key !== statusColumn?.key && c.key !== subtitleColumn?.key
    );
    const priority = rest.filter((c) => c.type === "money" || c.type === "number" || c.type === "date");
    const text = rest.filter((c) => !priority.includes(c) && c.type !== "boolean");
    const booleans = rest.filter((c) => c.type === "boolean");
    return [...priority, ...text, ...booleans].slice(0, 4);
  })();

  const mobileCard = (
    row: ApiRecord,
    opts?: { depth?: number; hasChildren?: boolean; expanded?: boolean; onToggle?: () => void }
  ) => {
    const clickable = !config.listOnly;
    const content = (
      <>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div
              className="flex min-w-0 items-center gap-1.5"
              style={{ paddingInlineStart: `${(opts?.depth ?? 0) * 12}px` }}
            >
              {opts?.hasChildren && (
                <button
                  type="button"
                  aria-expanded={opts.expanded}
                  aria-label={t(opts.expanded ? "common.collapse_node" : "common.expand_node")}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    opts.onToggle?.();
                  }}
                  className="rounded p-0.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
                >
                  {opts.expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4 rtl:rotate-180" />}
                </button>
              )}
              {opts?.hasChildren === false && <span className="w-5" />}
              {row[groupKey] ? (
                opts?.expanded ? (
                  <FolderOpen className="h-4 w-4 shrink-0 text-amber-400/80" />
                ) : (
                  <Folder className="h-4 w-4 shrink-0 text-amber-400/80" />
                )
              ) : null}
              <p className={`min-w-0 truncate text-sm font-semibold ${row[groupKey] ? "text-slate-100" : "text-slate-200"}`}>
                {renderCell(titleColumn, row)}
              </p>
            </div>
            {subtitleColumn &&
              hasValue(row, subtitleColumn) &&
              row[subtitleColumn.key] !== row[titleColumn.key] && (
                <p className="mt-0.5 truncate text-[11px] text-slate-500">{renderCell(subtitleColumn, row)}</p>
              )}
          </div>
          {statusColumn && <StatusBadge docstatus={row[statusColumn.key]} />}
        </div>

        {detailColumns.some((col) => hasValue(row, col)) && (
          <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2">
            {detailColumns.filter((col) => hasValue(row, col)).map((col) => (
              <div key={col.key} className="min-w-0">
                <dt className="truncate text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                  {t(col.labelKey)}
                </dt>
                <dd className={`mt-0.5 truncate text-xs text-slate-300 ${alignRight(col) ? "tabular-nums" : ""}`}>
                  {renderCell(col, row)}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </>
    );

    const cls = "rounded-xl border border-slate-800 bg-slate-900 p-3 shadow-sm transition-colors";
    if (!clickable) return <div key={String(row.id)} className={cls}>{content}</div>;
    if (opts) {
      return (
        <div
          key={String(row.id)}
          role="link"
          tabIndex={0}
          onClick={() => navigate(`${base}/${row.id}`)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") navigate(`${base}/${row.id}`);
          }}
          className={`${cls} cursor-pointer hover:border-slate-700`}
        >
          {content}
        </div>
      );
    }
    return (
      <Link key={String(row.id)} to={`${base}/${row.id}`} className={`${cls} block hover:border-slate-700`}>
        {content}
      </Link>
    );
  };

  const empty = treeOn ? treeRows.length === 0 : rows.length === 0;

  return (
    <Layout>
      <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-xl overflow-hidden animate-fade-up">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 p-4 sm:gap-4 sm:p-5">
          <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-slate-100 sm:text-xl">
            {t(config.titleKey)}
            {isFetching && <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}
          </h1>
          <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:gap-3">
            {config.submittable && (
              <Select
                value={docstatus}
                onChange={setDocstatus}
                aria-label={t("status")}
                className="w-full sm:w-40"
                options={DOCSTATUS_FILTERS.map((o) => ({ value: o.value, label: t(o.labelKey) }))}
              />
            )}
            {tree && (
              <div className="flex w-full items-center gap-1 sm:w-auto">
                {treeOn && (
                  <>
                    <button
                      type="button"
                      onClick={expandAll}
                      className="flex-1 rounded-lg border border-slate-800 bg-slate-900 px-2.5 py-2 text-xs text-slate-300 shadow-sm hover:bg-slate-800 sm:flex-none"
                    >
                      {t("common.expand_all")}
                    </button>
                    <button
                      type="button"
                      onClick={collapseAll}
                      className="flex-1 rounded-lg border border-slate-800 bg-slate-900 px-2.5 py-2 text-xs text-slate-300 shadow-sm hover:bg-slate-800 sm:flex-none"
                    >
                      {t("common.collapse_all")}
                    </button>
                  </>
                )}
                <button
                  type="button"
                  onClick={() => setTreeMode((m) => !m)}
                  aria-pressed={treeOn}
                  className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-indigo-500/30 bg-indigo-600/10 px-2.5 py-2 text-xs text-indigo-300 hover:bg-indigo-600/20 sm:flex-none"
                >
                  {treeOn ? <List className="w-4 h-4" /> : <FolderTree className="w-4 h-4" />}
                  {treeOn ? t("common.list_view") : t("common.tree_view")}
                </button>
              </div>
            )}
            {!config.listOnly && (
              <button
                type="button"
                onClick={() => navigate(`${base}/new`)}
                className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-indigo-600/20 hover:bg-indigo-500 sm:w-auto"
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
            <div className="space-y-2 p-3 sm:hidden">
              {treeOn
                ? treeRows.map(({ row, depth, hasChildren, expanded: isOpen }) => (
                    <React.Fragment key={String(row.id)}>
                      {mobileCard(row, {
                        depth,
                        hasChildren,
                        expanded: isOpen,
                        onToggle: () => toggleNode(String(row.id), isOpen),
                      })}
                    </React.Fragment>
                  ))
                : rows.map((row) => mobileCard(row))}
            </div>

            <div className="hidden overflow-x-auto sm:block">
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
                  {treeOn
                    ? treeRows.map(({ row, depth, hasChildren, expanded: isOpen }) => (
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
                    : rows.map((row) => (
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
                      ))}
                </tbody>
              </table>
            </div>
          </>
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
