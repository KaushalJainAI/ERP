import React, { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ChevronLeft, ChevronRight, Download, Printer } from "lucide-react";
import { apiClient } from "@/api/client";
import { todayISO } from "@/lib/format";
import { CompanySelect, DateInput, useCompany } from "../parts";
import { downloadCsv, type CsvCell } from "../gst/lib";

/** Rows per request: the server maximum, so one page covers an ordinary period. */
export const PAGE_SIZE = 500;

export interface PageInfo {
  page: number;
  page_size: number;
  count: number;
  num_pages: number;
}

/**
 * Tally's own identity of an imported voucher (`TLY-*` Journal Entry). The server sends it
 * as `tally` on a register row, or null for every other row.
 */
export interface TallyRef {
  id: number;
  voucher_type: string;
  voucher_no: string;
}
interface VoucherLike {
  voucher_type: string;
  voucher_no: string;
  tally?: TallyRef | null;
}

/** The voucher type the business knows: Tally's ("Receipt"), else the ERP's. */
export const voucherTypeText = (r: VoucherLike): string => r.tally?.voucher_type ?? r.voucher_type;
/** The voucher number the business knows: Tally's ("2526/1"), else the ERP's. */
export const voucherNoText = (r: VoucherLike): string => r.tally?.voucher_no ?? r.voucher_no;

/**
 * Voucher number cell content. A Tally voucher links to its bill in Past Bills (the ERP
 * `TLY-*` name stays as the tooltip); `plain` (print views) renders text only.
 */
export const VoucherNo: React.FC<{ row: VoucherLike; plain?: boolean }> = ({ row, plain }) => {
  if (!row.tally) return <>{row.voucher_no}</>;
  if (plain) return <>{row.tally.voucher_no}</>;
  return (
    <Link
      to={`/history/bills?open=${row.tally.id}`}
      title={row.voucher_no}
      className="text-sky-400 hover:underline"
    >
      {row.tally.voucher_no}
    </Link>
  );
};

/** First day of the current month .. today, as YYYY-MM-DD. */
export function defaultPeriod(): { from: string; to: string } {
  const to = todayISO();
  return { from: `${to.slice(0, 8)}01`, to };
}

/** Company + period state shared by all the register pages. */
export function useRegisterFilters() {
  const { companies, company, setCompany, loadingCompanies } = useCompany();
  const initial = useMemo(defaultPeriod, []);
  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);
  const [page, setPage] = useState(1);
  const ready = !!company && !!from && !!to && from <= to;
  return {
    companies,
    company,
    setCompany: (v: string) => {
      setCompany(v);
      setPage(1);
    },
    loadingCompanies,
    from,
    setFrom: (v: string) => {
      setFrom(v);
      setPage(1);
    },
    to,
    setTo: (v: string) => {
      setTo(v);
      setPage(1);
    },
    page,
    setPage,
    ready,
  };
}

export type RegisterFilters = ReturnType<typeof useRegisterFilters>;

/** Company + From / To inputs. */
export const PeriodFilters: React.FC<{ f: RegisterFilters; prefix: string }> = ({ f, prefix }) => (
  <>
    <CompanySelect companies={f.companies} value={f.company} onChange={f.setCompany} />
    <DateInput id={`${prefix}-from`} labelKey="acc.fields.from_date" value={f.from} onChange={f.setFrom} />
    <DateInput id={`${prefix}-to`} labelKey="acc.fields.to_date" value={f.to} onChange={f.setTo} />
  </>
);

/** Previous / next page controls (hidden when there is a single page). */
export const Pager: React.FC<{ info: PageInfo | undefined; onPage: (p: number) => void }> = ({ info, onPage }) => {
  const { t } = useTranslation();
  if (!info || info.num_pages <= 1) return null;
  return (
    <div className="no-print flex items-center justify-end gap-3 px-5 py-3 text-xs text-slate-400">
      <button
        type="button"
        disabled={info.page <= 1}
        onClick={() => onPage(info.page - 1)}
        className="p-1.5 rounded-md border border-slate-700 disabled:opacity-40 hover:bg-slate-800 rtl:rotate-180"
        aria-label={t("registers.prev")}
      >
        <ChevronLeft className="w-4 h-4" />
      </button>
      <span>{t("registers.page_of", { page: info.page, num: info.num_pages, total: info.count })}</span>
      <button
        type="button"
        disabled={info.page >= info.num_pages}
        onClick={() => onPage(info.page + 1)}
        className="p-1.5 rounded-md border border-slate-700 disabled:opacity-40 hover:bg-slate-800 rtl:rotate-180"
        aria-label={t("registers.next")}
      >
        <ChevronRight className="w-4 h-4" />
      </button>
    </div>
  );
};

/** Read every page of a paged report (used by CSV export). Returns the first page's body and all `listKey` rows. */
export async function fetchAllPages<T extends Record<string, any>>(
  path: string,
  params: Record<string, string>,
  listKey: string,
): Promise<{ first: T; items: any[] }> {
  const get = async (page: number) =>
    (await apiClient.get<T>(path, { params: { ...params, page, page_size: PAGE_SIZE } })).data;
  const first = await get(1);
  const items: any[] = [...(first[listKey] as any[])];
  const pages = (first.pagination as PageInfo).num_pages;
  for (let p = 2; p <= pages; p++) items.push(...(((await get(p))[listKey] as any[]) ?? []));
  return { first, items };
}

const btn =
  "no-print inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold border border-slate-700 text-slate-200 hover:bg-slate-800 disabled:opacity-50";

export const CsvButton: React.FC<{ onExport: () => Promise<void> | void; disabled?: boolean }> = ({ onExport, disabled }) => {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className={btn}
      disabled={disabled || busy}
      onClick={async () => {
        setBusy(true);
        try {
          await onExport();
        } finally {
          setBusy(false);
        }
      }}
    >
      <Download className="w-3.5 h-3.5" /> {t("registers.export_csv")}
    </button>
  );
};

export const PrintButton: React.FC<{ disabled?: boolean }> = ({ disabled }) => {
  const { t } = useTranslation();
  return (
    <button type="button" className={btn} disabled={disabled} onClick={() => window.print()}>
      <Printer className="w-3.5 h-3.5" /> {t("registers.print")}
    </button>
  );
};

export const th = "px-4 py-3 whitespace-nowrap";
export const thR = `${th} text-right`;
export const td = "px-4 py-2.5";
export const tdR = `${td} text-right tabular-nums whitespace-nowrap`;
export const theadCls = "bg-slate-950/80 text-xs uppercase text-slate-400 font-semibold border-b border-slate-800";
export const tfootCls = "bg-slate-950/60 border-t border-slate-700 font-semibold text-slate-100";

export type { CsvCell };
export { downloadCsv };

/** Empty / loading body row. */
export const MessageRow: React.FC<{ cols: number; loading: boolean }> = ({ cols, loading }) => {
  const { t } = useTranslation();
  return (
    <tr>
      <td colSpan={cols} className="px-6 py-10 text-center text-slate-500">
        {loading ? t("common.loading") : t("acc.report.no_data")}
      </td>
    </tr>
  );
};

/** Prompt shown instead of a report until a required filter is chosen. */
export const Hint: React.FC<{ textKey: string }> = ({ textKey }) => {
  const { t } = useTranslation();
  return <div className="px-6 py-12 text-center text-sm text-slate-500">{t(textKey)}</div>;
};
