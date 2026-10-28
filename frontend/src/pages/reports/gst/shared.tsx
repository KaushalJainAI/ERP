import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Download, Loader2, ShieldAlert } from "lucide-react";
import { Layout } from "@/components/Layout";
import { Select } from "@/components/ui/Select";
import { apiClient } from "@/api/client";
import { fetchStates } from "@/api/lookups";
import { normaliseError } from "@/api/endpoints";
import { CompanySelect, DateInput, useCompany } from "@/pages/reports/parts";
import { fmtMoney } from "./lib";

/* ------------------------------------------------------------ filters */

export type PeriodMode = "month" | "range" | "all";

const pad2 = (n: number) => String(n).padStart(2, "0");

export interface GstFilters {
  companies: Record<string, any>[];
  company: string;
  setCompany: (v: string) => void;
  loadingCompanies: boolean;
  mode: PeriodMode;
  setMode: (m: PeriodMode) => void;
  year: number;
  setYear: (y: number) => void;
  month: number;
  setMonth: (m: number) => void;
  from: string;
  setFrom: (v: string) => void;
  to: string;
  setTo: (v: string) => void;
  /** Query params for the endpoint (company + period). */
  params: Record<string, string>;
  /** Company chosen and the period complete. */
  ready: boolean;
}

/**
 * Company (auto-selected: the first one, which is the only one for single-company
 * installs) + period state. The month picker defaults to the current month.
 * `allowAll` adds an "All dates" mode that sends no period (tax ledger only).
 */
export function useGstFilters(allowAll = false): GstFilters {
  const { companies, company, setCompany, loadingCompanies } = useCompany();
  const now = new Date();
  const [mode, setMode] = useState<PeriodMode>("month");
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const params = useMemo(() => {
    const p: Record<string, string> = { company };
    if (mode === "month") p.month = `${year}-${pad2(month)}`;
    else if (mode === "range") {
      if (from) p.from_date = from;
      if (to) p.to_date = to;
    }
    return p;
  }, [company, mode, year, month, from, to]);

  const ready = !!company && (mode === "month" || mode === "all" || (allowAll ? true : !!from && !!to));
  return {
    companies,
    company,
    setCompany,
    loadingCompanies,
    mode,
    setMode,
    year,
    setYear,
    month,
    setMonth,
    from,
    setFrom,
    to,
    setTo,
    params,
    ready,
  };
}

/** GET a GST report. Client errors (400 / 403) are not retried (see lib/queryClient.ts). */
export function useGstReport<T>(path: string, params: Record<string, string>, enabled: boolean) {
  return useQuery({
    queryKey: ["gst-report", path, params],
    queryFn: async () => (await apiClient.get<T>(path, { params })).data,
    enabled,
  });
}

/** State code -> "27-Maharashtra" (GSTN style); falls back to the bare code. */
export function usePlaceOfSupply(): (code: string | null | undefined) => string {
  const { data = [] } = useQuery({ queryKey: ["states-lookup"], queryFn: () => fetchStates(""), staleTime: 30 * 60 * 1000 });
  const names = useMemo(() => new Map(data.map((s) => [String(s.code), String(s.name)])), [data]);
  return (code) => {
    if (!code) return "";
    const name = names.get(code);
    return name ? `${code}-${name}` : code;
  };
}

const MODE_ORDER: PeriodMode[] = ["month", "range", "all"];

export const GstFilterBar: React.FC<{ f: GstFilters; allowAll?: boolean; children?: React.ReactNode }> = ({
  f,
  allowAll,
  children,
}) => {
  const { t, i18n } = useTranslation();
  const modes = allowAll ? MODE_ORDER : MODE_ORDER.filter((m) => m !== "all");

  const monthName = (m: number) => {
    try {
      return new Intl.DateTimeFormat(i18n.language, { month: "long" }).format(new Date(2000, m - 1, 1));
    } catch {
      return pad2(m);
    }
  };
  const thisYear = new Date().getFullYear();
  const years = Array.from({ length: 8 }, (_, i) => thisYear + 1 - i);
  if (!years.includes(f.year)) years.push(f.year);

  return (
    <>
      <CompanySelect companies={f.companies} value={f.company} onChange={f.setCompany} />
      <div className="space-y-1">
        <span className="block text-xs font-medium text-slate-400">{t("gstreports.period_mode")}</span>
        <div role="group" aria-label={t("gstreports.period_mode")} className="inline-flex rounded-lg border border-slate-800 bg-slate-950 p-0.5">
          {modes.map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={f.mode === m}
              onClick={() => f.setMode(m)}
              className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                f.mode === m ? "bg-indigo-600 text-white" : "text-slate-300 hover:bg-slate-800"
              }`}
            >
              {t(`gstreports.mode_${m}`)}
            </button>
          ))}
        </div>
      </div>
      {f.mode === "month" && (
        <>
          <div className="space-y-1">
            <label htmlFor="gst-month" className="block text-xs font-medium text-slate-400">
              {t("gstreports.month")}
            </label>
            <Select
              id="gst-month"
              className="w-40"
              value={String(f.month)}
              onChange={(v) => f.setMonth(Number(v))}
              options={Array.from({ length: 12 }, (_, i) => ({ value: String(i + 1), label: monthName(i + 1) }))}
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="gst-year" className="block text-xs font-medium text-slate-400">
              {t("gstreports.year")}
            </label>
            <Select
              id="gst-year"
              className="w-28"
              value={String(f.year)}
              onChange={(v) => f.setYear(Number(v))}
              options={years.sort((a, b) => b - a).map((y) => ({ value: String(y), label: String(y) }))}
            />
          </div>
        </>
      )}
      {f.mode === "range" && (
        <>
          <DateInput id="gst-from" labelKey="acc.fields.from_date" value={f.from} onChange={f.setFrom} />
          <DateInput id="gst-to" labelKey="acc.fields.to_date" value={f.to} onChange={f.setTo} />
        </>
      )}
      {children}
    </>
  );
};

/* -------------------------------------------------------------- frame */

export const WarningBanner: React.FC<{ messages: string[] }> = ({ messages }) => {
  const { t } = useTranslation();
  if (messages.length === 0) return null;
  return (
    <div role="alert" className="flex gap-3 rounded-lg border border-amber-800 bg-amber-950 px-4 py-3 text-sm text-amber-300">
      <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" aria-hidden />
      <div className="space-y-1">
        <p className="font-semibold">{t("gstreports.warn_title")}</p>
        <ul className="list-disc ps-5 space-y-0.5">
          {messages.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      </div>
    </div>
  );
};

/** 400 -> the server's per-parameter messages; 403 -> permission; anything else -> generic. */
const ErrorPanel: React.FC<{ error: unknown }> = ({ error }) => {
  const { t } = useTranslation();
  const err = normaliseError(error);
  if (err.forbidden) {
    return (
      <div className="px-6 py-12 text-center space-y-2">
        <ShieldAlert className="w-8 h-8 mx-auto text-rose-400" aria-hidden />
        <p className="text-sm text-rose-300">{t("common.forbidden")}</p>
      </div>
    );
  }
  const messages = [...Object.values(err.fields).flat(), ...err.general];
  const bad = err.status === 400;
  return (
    <div className="px-6 py-12 text-center space-y-2">
      <ShieldAlert className="w-8 h-8 mx-auto text-rose-400" aria-hidden />
      <p className="text-sm text-rose-300">{bad ? t("gstreports.bad_request") : t("common.load_failed")}</p>
      {messages.slice(0, 4).map((m) => (
        <p key={m} className="text-xs text-slate-400">
          {m}
        </p>
      ))}
    </div>
  );
};

/** Page chrome: title, filter bar, loading / error / empty states, then the report. */
export const GstReportFrame: React.FC<{
  titleKey: string;
  filters: React.ReactNode;
  loading: boolean;
  error: unknown;
  noCompany: boolean;
  waiting: boolean;
  warnings?: string[];
  /** Company GSTIN + period line, shown once data has loaded. */
  meta?: React.ReactNode;
  children: React.ReactNode;
}> = ({ titleKey, filters, loading, error, noCompany, waiting, warnings = [], meta, children }) => {
  const { t } = useTranslation();
  return (
    <Layout>
      <div className="max-w-7xl mx-auto bg-slate-900 border border-slate-800 rounded-xl shadow-xl overflow-hidden">
        <div className="p-5 border-b border-slate-800 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h1 className="text-xl font-semibold text-slate-100 tracking-tight flex items-center gap-2">
              {t(titleKey)}
              {loading && <Loader2 className="w-4 h-4 animate-spin text-slate-500" aria-hidden />}
            </h1>
            {meta && <div className="text-xs text-slate-400">{meta}</div>}
          </div>
          <div className="flex flex-wrap items-end gap-4">{filters}</div>
        </div>
        {noCompany ? (
          <div className="px-6 py-12 text-center text-sm text-slate-500">{t("acc.report.no_company")}</div>
        ) : error ? (
          <ErrorPanel error={error} />
        ) : waiting ? (
          <div className="px-6 py-12 text-center text-sm text-slate-500">{t("gstreports.select_period")}</div>
        ) : (
          <div className="p-5 space-y-5">
            <WarningBanner messages={warnings} />
            {children}
          </div>
        )}
      </div>
    </Layout>
  );
};

/* ------------------------------------------------------------ widgets */

export const DownloadButton: React.FC<{ onClick: () => void; disabled?: boolean }> = ({ onClick, disabled }) => {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 bg-slate-900 hover:bg-slate-800 border border-slate-800 shadow-sm text-slate-200 px-3 py-1.5 rounded-lg text-sm disabled:opacity-50 disabled:cursor-not-allowed"
    >
      <Download className="w-4 h-4" aria-hidden />
      {t("gstreports.download_csv")}
    </button>
  );
};

export const SectionHead: React.FC<{ title: string; onDownload?: () => void; downloadDisabled?: boolean; note?: string }> = ({
  title,
  onDownload,
  downloadDisabled,
  note,
}) => (
  <div className="flex flex-wrap items-center justify-between gap-2">
    <div>
      <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
      {note && <p className="text-xs text-slate-500 mt-0.5">{note}</p>}
    </div>
    {onDownload && <DownloadButton onClick={onDownload} disabled={downloadDisabled} />}
  </div>
);

export const StatTile: React.FC<{ label: string; value: string; money?: boolean }> = ({ label, value, money = true }) => (
  <div className="rounded-lg border border-slate-800 bg-slate-950 px-4 py-3">
    <div className="text-xs text-slate-400">{label}</div>
    <div className="mt-1 text-lg font-semibold text-slate-100 tabular-nums">{money ? fmtMoney(value, true) : value}</div>
  </div>
);

export interface GridCol<R> {
  /** Translated header text. */
  head: string;
  /** Right-align (numbers). */
  num?: boolean;
  /** What the table shows. */
  cell: (row: R, index: number) => React.ReactNode;
  /** CSV value (exact string from the response). Defaults to `cell` when it returns a string. */
  csv?: (row: R) => string;
}

/** A plain responsive table. CSV export uses `csv` of each column, never the formatted cell. */
export function Grid<R>({
  cols,
  rows,
  rowKey,
  foot,
}: {
  cols: GridCol<R>[];
  rows: R[];
  rowKey: (row: R, index: number) => string;
  foot?: React.ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-800">
      <table className="w-full text-start text-sm text-slate-300">
        <thead className="bg-slate-950/80 text-xs uppercase text-slate-400 font-semibold border-b border-slate-800">
          <tr>
            {cols.map((c, i) => (
              <th key={i} scope="col" className={`px-4 py-3 whitespace-nowrap ${c.num ? "text-end" : "text-start"}`}>
                {c.head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/60">
          {rows.length === 0 ? (
            <tr>
              <td colSpan={cols.length} className="px-6 py-10 text-center text-slate-500">
                {t("gstreports.no_rows")}
              </td>
            </tr>
          ) : (
            rows.map((r, i) => (
              <tr key={rowKey(r, i)} className="hover:bg-slate-800/40">
                {cols.map((c, j) => (
                  <td key={j} className={`px-4 py-2.5 ${c.num ? "text-end tabular-nums whitespace-nowrap" : ""}`}>
                    {c.cell(r, i)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
        {foot}
      </table>
    </div>
  );
}

/** Header + rows of CSV text from a Grid definition. */
export function gridCsv<R>(cols: GridCol<R>[], rows: R[]): string[][] {
  return [
    cols.map((c) => c.head),
    ...rows.map((r, i) =>
      cols.map((c) => {
        if (c.csv) return c.csv(r);
        const v = c.cell(r, i);
        return typeof v === "string" || typeof v === "number" ? String(v) : "";
      })
    ),
  ];
}
