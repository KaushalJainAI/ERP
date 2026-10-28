import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Select } from "@/components/ui/Select";
import { formatDate } from "@/lib/format";
import { absDec, downloadCsv, fmtMoney, isZeroDec, periodTag, slug } from "./lib";
import { DownloadButton, Grid, GridCol, GstFilterBar, GstReportFrame, SectionHead, gridCsv, useGstFilters, useGstReport } from "./shared";

interface Entry {
  posting_date: string;
  voucher_type: string;
  voucher_no: string;
  party: string | null;
  debit: string;
  credit: string;
  balance: string;
}
interface LedgerAccount {
  account_id: number;
  account_name: string;
  direction: string;
  component: string;
  opening_balance: string;
  total_debit: string;
  total_credit: string;
  closing_balance: string;
  entries?: Entry[];
}
interface Net {
  output: string;
  input: string;
  rcm: string;
  net_payable: string;
}
interface TaxLedger {
  company: { id: number; name: string; gstin: string; state: string };
  period: { from_date: string | null; to_date: string | null };
  accounts: LedgerAccount[];
  net_by_component: Record<string, Net>;
}

const COMPONENTS = ["CGST", "SGST", "IGST", "CESS"];
const DIRECTIONS = ["Input", "Output", "Reverse Charge"];

/** Balances are debit minus credit: positive is Dr, negative is Cr (Output / RCM accounts run Cr). */
const Bal: React.FC<{ v: string }> = ({ v }) => {
  const { t } = useTranslation();
  const zero = isZeroDec(v);
  return (
    <span className="tabular-nums whitespace-nowrap">
      {fmtMoney(absDec(v))}
      {!zero && <span className="ms-1 text-xs text-slate-400">{v.startsWith("-") ? t("gstreports.cr") : t("gstreports.dr")}</span>}
    </span>
  );
};

/** Voucher types that have a list page to search in. */
const VOUCHER_LISTS: Record<string, string> = {
  "selling.SalesInvoice": "/selling/sales-invoices",
  "buying.PurchaseInvoice": "/buying/purchase-invoices",
};

const csvBal = (v: string) => `${absDec(v)}${isZeroDec(v) ? "" : v.startsWith("-") ? " Cr" : " Dr"}`;

export const TaxLedgerPage: React.FC = () => {
  const { t } = useTranslation();
  const f = useGstFilters(true);
  const [component, setComponent] = useState("");
  const [direction, setDirection] = useState("");
  const [open, setOpen] = useState<Set<number>>(new Set());

  const params = { ...f.params, ...(component ? { component } : {}), ...(direction ? { direction } : {}) };
  const { data, isFetching, error } = useGstReport<TaxLedger>("/gst/reports/tax-ledger/", params, f.ready);

  const dirLabel = (d: string) => t(`gst.opt.${slug(d)}`, { defaultValue: d });
  const toggle = (id: number) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const tag = `${data?.company.gstin ?? ""}-${periodTag(data?.period)}`;

  const voucherType = (vt: string) => {
    const key = vt.split(".").pop() ?? vt;
    return t(`acc.opt.${key.replace(/([a-z])([A-Z])/g, "$1_$2").toLowerCase()}`, { defaultValue: vt });
  };

  const entryCols = (): GridCol<Entry>[] => [
    { head: t("fields.posting_date"), cell: (r) => formatDate(r.posting_date), csv: (r) => r.posting_date },
    { head: t("acc.fields.voucher_type"), cell: (r) => voucherType(r.voucher_type), csv: (r) => r.voucher_type },
    {
      head: t("acc.fields.voucher_no"),
      cell: (r) =>
        VOUCHER_LISTS[r.voucher_type] ? (
          <Link className="text-indigo-300 hover:underline" to={`${VOUCHER_LISTS[r.voucher_type]}?search=${encodeURIComponent(r.voucher_no)}`}>
            {r.voucher_no}
          </Link>
        ) : (
          r.voucher_no
        ),
      csv: (r) => r.voucher_no,
    },
    { head: t("acc.fields.party"), cell: (r) => r.party ?? "", csv: (r) => r.party ?? "" },
    { head: t("acc.fields.debit"), num: true, cell: (r) => fmtMoney(r.debit), csv: (r) => r.debit },
    { head: t("acc.fields.credit"), num: true, cell: (r) => fmtMoney(r.credit), csv: (r) => r.credit },
    { head: t("acc.fields.balance"), num: true, cell: (r) => <Bal v={r.balance} />, csv: (r) => r.balance },
  ];

  const netRows = data ? Object.entries(data.net_by_component) : [];
  const netCols: GridCol<[string, Net]>[] = [
    { head: t("gstreports.component"), cell: ([c]) => c },
    { head: t("gst.opt.output"), num: true, cell: ([, n]) => fmtMoney(n.output), csv: ([, n]) => n.output },
    { head: t("gstreports.col_input"), num: true, cell: ([, n]) => fmtMoney(n.input), csv: ([, n]) => n.input },
    { head: t("gstreports.col_rcm"), num: true, cell: ([, n]) => fmtMoney(n.rcm), csv: ([, n]) => n.rcm },
    {
      head: t("gstreports.col_net_payable"),
      num: true,
      cell: ([, n]) => <span className="font-semibold text-slate-100">{fmtMoney(n.net_payable)}</span>,
      csv: ([, n]) => n.net_payable,
    },
  ];

  const accountCsv = (a: LedgerAccount) => {
    const cols = entryCols();
    const rows = [
      [t("acc.fields.account"), a.account_name],
      [t("gstreports.component"), a.component],
      [t("gstreports.direction"), a.direction],
      [t("gstreports.col_opening"), csvBal(a.opening_balance)],
      [],
      ...gridCsv(cols, a.entries ?? []),
      [],
      [t("gstreports.col_closing"), csvBal(a.closing_balance)],
    ];
    downloadCsv(`gst-ledger-${slug(a.account_name)}-${tag}`, rows);
  };

  const allCsv = () => {
    if (!data) return;
    const head = [
      t("acc.fields.account"),
      t("gstreports.component"),
      t("gstreports.direction"),
      t("gstreports.col_opening"),
      t("acc.fields.total_debit"),
      t("acc.fields.total_credit"),
      t("gstreports.col_closing"),
    ];
    downloadCsv(`gst-ledger-summary-${tag}`, [
      head,
      ...data.accounts.map((a) => [a.account_name, a.component, a.direction, a.opening_balance, a.total_debit, a.total_credit, a.closing_balance]),
    ]);
  };

  return (
    <GstReportFrame
      titleKey="gstreports.title_tax_ledger"
      loading={isFetching || f.loadingCompanies}
      error={error}
      noCompany={!f.loadingCompanies && f.companies.length === 0}
      waiting={!f.ready || !data}
      meta={data && `${t("gstreports.gstin")}: ${data.company.gstin}`}
      filters={
        <GstFilterBar f={f} allowAll>
          <div className="space-y-1">
            <label htmlFor="gst-component" className="block text-xs font-medium text-slate-400">
              {t("gstreports.component")}
            </label>
            <Select
              id="gst-component"
              className="w-44"
              value={component}
              onChange={setComponent}
              options={[{ value: "", label: t("gstreports.all_components") }, ...COMPONENTS.map((c) => ({ value: c, label: c }))]}
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="gst-direction" className="block text-xs font-medium text-slate-400">
              {t("gstreports.direction")}
            </label>
            <Select
              id="gst-direction"
              className="w-48"
              value={direction}
              onChange={setDirection}
              options={[{ value: "", label: t("gstreports.all_directions") }, ...DIRECTIONS.map((d) => ({ value: d, label: dirLabel(d) }))]}
            />
          </div>
        </GstFilterBar>
      }
    >
      {data && (
        <>
          <section className="space-y-3" aria-label={t("gstreports.net_by_component")}>
            <SectionHead
              title={t("gstreports.net_by_component")}
              onDownload={() => downloadCsv(`gst-ledger-net-${tag}`, gridCsv(netCols, netRows))}
              downloadDisabled={netRows.length === 0}
            />
            <Grid cols={netCols} rows={netRows} rowKey={([c]) => c} />
          </section>

          <section className="space-y-3">
            <SectionHead title={t("acc.fields.account")} onDownload={allCsv} downloadDisabled={data.accounts.length === 0} />
            {data.accounts.length === 0 && (
              <p className="rounded-lg border border-slate-800 px-6 py-10 text-center text-sm text-slate-500">{t("gstreports.no_accounts")}</p>
            )}
            {data.accounts.map((a) => {
              const isOpen = open.has(a.account_id);
              const entries = a.entries ?? [];
              const panelId = `ledger-entries-${a.account_id}`;
              const glQuery = new URLSearchParams({ account: String(a.account_id), company: f.company });
              if (data.period.from_date) glQuery.set("from_date", data.period.from_date);
              if (data.period.to_date) glQuery.set("to_date", data.period.to_date);
              return (
                <div key={a.account_id} className="rounded-lg border border-slate-800 bg-slate-950">
                  <div className="flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      aria-controls={panelId}
                      onClick={() => toggle(a.account_id)}
                      className="flex items-center gap-2 text-start min-w-[14rem] flex-1"
                    >
                      {isOpen ? <ChevronDown className="w-4 h-4 text-slate-400" aria-hidden /> : <ChevronRight className="w-4 h-4 text-slate-400 rtl:-scale-x-100" aria-hidden />}
                      <span>
                        <span className="block text-sm font-semibold text-slate-100">{a.account_name}</span>
                        <span className="block text-xs text-slate-400">
                          {a.component} · {dirLabel(a.direction)} · {t("gstreports.entries_n", { count: entries.length })}
                        </span>
                      </span>
                    </button>
                    {[
                      [t("gstreports.col_opening"), <Bal key="o" v={a.opening_balance} />],
                      [t("acc.fields.total_debit"), <span key="d" className="tabular-nums">{fmtMoney(a.total_debit)}</span>],
                      [t("acc.fields.total_credit"), <span key="c" className="tabular-nums">{fmtMoney(a.total_credit)}</span>],
                      [t("gstreports.col_closing"), <span key="x" className="font-semibold text-slate-100"><Bal v={a.closing_balance} /></span>],
                    ].map(([label, value]) => (
                      <div key={String(label)} className="text-end">
                        <div className="text-xs text-slate-400">{label}</div>
                        <div className="text-sm text-slate-200">{value}</div>
                      </div>
                    ))}
                  </div>
                  {isOpen && (
                    <div id={panelId} className="border-t border-slate-800 p-4 space-y-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <Link className="text-xs text-indigo-300 hover:underline" to={`/accounts/reports/general-ledger?${glQuery.toString()}`}>
                          {t("gstreports.view_gl")}
                        </Link>
                        <DownloadButton onClick={() => accountCsv(a)} />
                      </div>
                      {entries.length === 0 ? (
                        <p className="text-sm text-slate-500">{t("gstreports.no_entries")}</p>
                      ) : (
                        <Grid cols={entryCols()} rows={entries} rowKey={(r, i) => `${r.voucher_no}-${i}`} />
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </section>
        </>
      )}
    </GstReportFrame>
  );
};
