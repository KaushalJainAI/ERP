import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { formatIndianCurrency } from "@/lib/numberFormat";
import { apiClient } from "@/api/client";
import { Search, Plus, Eye, CheckCircle2, Clock, XCircle } from "lucide-react";

export interface ColumnDef {
  key: string;
  label: string;
  isCurrency?: boolean;
  isStatus?: boolean;
  render?: (value: any, row: any) => React.ReactNode;
}

interface DataTableProps {
  title: string;
  columns: ColumnDef[];
  endpoint?: string;
  data?: any[];
  onRowClick?: (item: any) => void;
  onCreateNew?: () => void;
  searchPlaceholder?: string;
}

export const DataTable: React.FC<DataTableProps> = ({
  title,
  columns,
  endpoint,
  data: providedData,
  onRowClick,
  onCreateNew,
  searchPlaceholder,
}) => {
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState("");

  const { data: fetchedData, isLoading } = useQuery({
    queryKey: [endpoint, searchQuery],
    queryFn: async () => {
      if (!endpoint) return [];
      const res = await apiClient.get(endpoint, {
        params: searchQuery ? { search: searchQuery } : {},
      });
      return Array.isArray(res.data) ? res.data : res.data.results || [];
    },
    enabled: !!endpoint,
  });

  const tableData = providedData || fetchedData || [];

  const renderStatusBadge = (docstatus?: number, statusText?: string) => {
    let badgeClass = "bg-slate-800 text-slate-300 border-slate-700";
    let icon = <Clock className="w-3 h-3 mr-1 inline text-amber-400" />;
    let text = statusText || t("draft");

    if (docstatus === 1 || statusText === "Paid" || statusText === "Submitted") {
      badgeClass = "bg-emerald-950/60 text-emerald-300 border-emerald-800/60";
      icon = <CheckCircle2 className="w-3 h-3 mr-1 inline text-emerald-400" />;
      text = statusText || t("submitted");
    } else if (docstatus === 2 || statusText === "Cancelled") {
      badgeClass = "bg-rose-950/60 text-rose-300 border-rose-800/60";
      icon = <XCircle className="w-3 h-3 mr-1 inline text-rose-400" />;
      text = statusText || t("cancelled");
    }

    return (
      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${badgeClass}`}>
        {icon} {text}
      </span>
    );
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-xl overflow-hidden">
      {/* Header controls */}
      <div className="p-5 border-b border-slate-800 flex flex-wrap items-center justify-between gap-4 bg-slate-900/50 backdrop-blur-md">
        <div>
          <h2 className="text-xl font-bold text-slate-100 tracking-tight">{title}</h2>
        </div>
        <div className="flex items-center space-x-3">
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-3 text-slate-400" />
            <input
              type="text"
              placeholder={searchPlaceholder || t("search")}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="bg-slate-950 border border-slate-800 rounded-lg pl-9 pr-4 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 w-64"
            />
          </div>
          {onCreateNew && (
            <button
              onClick={onCreateNew}
              className="bg-indigo-600 hover:bg-indigo-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition-all shadow-lg shadow-indigo-600/20 flex items-center space-x-1.5"
            >
              <Plus className="w-4 h-4" />
              <span>Create New</span>
            </button>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm text-slate-300">
          <thead className="bg-slate-950/80 text-xs uppercase text-slate-400 font-semibold border-b border-slate-800">
            <tr>
              {columns.map((col) => (
                <th key={col.key} className="px-6 py-3.5">
                  {col.label}
                </th>
              ))}
              <th className="px-6 py-3.5 text-right">{t("actions")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {isLoading ? (
              <tr>
                <td colSpan={columns.length + 1} className="px-6 py-8 text-center text-slate-400">
                  Loading records...
                </td>
              </tr>
            ) : tableData.length === 0 ? (
              <tr>
                <td colSpan={columns.length + 1} className="px-6 py-12 text-center text-slate-500">
                  {t("no_records")}
                </td>
              </tr>
            ) : (
              tableData.map((row: any, idx: number) => (
                <tr
                  key={row.id || row.name || idx}
                  onClick={() => onRowClick && onRowClick(row)}
                  className="hover:bg-slate-800/40 transition-colors cursor-pointer group"
                >
                  {columns.map((col) => (
                    <td key={col.key} className="px-6 py-4 font-medium text-slate-200 whitespace-nowrap">
                      {col.render ? (
                        col.render(row[col.key], row)
                      ) : col.isCurrency ? (
                        formatIndianCurrency(row[col.key])
                      ) : col.isStatus || col.key === "docstatus" ? (
                        renderStatusBadge(row.docstatus, row.status)
                      ) : (
                        row[col.key] || "—"
                      )}
                    </td>
                  ))}
                  <td className="px-6 py-4 text-right whitespace-nowrap">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onRowClick && onRowClick(row);
                      }}
                      className="text-slate-400 hover:text-indigo-400 p-1 rounded-md transition-colors"
                    >
                      <Eye className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
