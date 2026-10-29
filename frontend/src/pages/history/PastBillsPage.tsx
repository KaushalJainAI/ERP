import React, { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight, FileText, Loader2, Search, ShieldAlert, X } from "lucide-react";
import { Layout } from "@/components/Layout";
import { Select } from "@/components/ui/Select";
import { apiClient } from "@/api/client";
import { getRecord, listRecords, normaliseError, PAGE_SIZE } from "@/api/endpoints";
import { useDebounced } from "@/hooks/useDebounced";
import { formatDate, formatIndianCurrency, formatIndianNumber } from "@/lib/format";
import type { ApiRecord } from "@/types";

/*
 * Every Tally voucher of the last 10 years, shown as read-only bills.
 * The accounting of each one is already in the books (its TLY-* Journal Entry);
 * this page only reads legacy.TallyRecord / TallyItemLine.
 */

const ENDPOINT = "/legacy/tally-records/";

/** Tabs: URL value -> Tally base voucher types. */
export const BILL_TABS: { key: string; types: string[] }[] = [
  { key: "sales", types: ["Sales", "Credit Note"] },
  { key: "purchase", types: ["Purchase", "Debit Note"] },
  { key: "receipt", types: ["Receipt"] },
  { key: "payment", types: ["Payment"] },
  { key: "contra", types: ["Contra"] },
  { key: "journal", types: ["Journal", "Stock Journal"] },
];

interface Summary {
  by_type: Record<string, number>;
  fiscal_years: string[];
}

interface ItemLine {
  id: number;
  item_name: string;
  item_code: string | null;
  qty: string | null;
  billed_qty: string | null;
  uom: string;
  rate: string | null;
  discount_percent: string | null;
  amount: string;
  is_inward: boolean;
}

interface LedgerLine {
  account: string;
  account_type: string;
  debit: string;
  credit: string;
  party: string | null;
}

const absAmount = (v: string | null | undefined) => (v ? v.replace(/^-/, "") : "0");

export const PastBillsPage: React.FC = () => {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const tabKey = BILL_TABS.some((b) => b.key === params.get("type")) ? params.get("type")! : "sales";
  const tab = BILL_TABS.find((b) => b.key === tabKey)!;
  const fy = params.get("fy") ?? "";
  const openId = params.get("open");
  const [search, setSearch] = useState(params.get("search") ?? "");
  const debounced = useDebounced(search, 300);
  const [page, setPage] = useState(1);

  const setParam = (key: string, value: string | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: key !== "open" }
    );

  useEffect(() => setPage(1), [tabKey, fy, debounced]);

  const summary = useQuery({
    queryKey: ["past-bills", "summary"],
    queryFn: async () => (await apiClient.get<Summary>(`${ENDPOINT}summary/`)).data,
    staleTime: 10 * 60 * 1000,
  });

  const list = useQuery({
    queryKey: ["past-bills", tabKey, fy, debounced, page],
    queryFn: () =>
      listRecords(ENDPOINT, {
        kind: "voucher",
        base_voucher_type__in: tab.types.join(","),
        fiscal_year: fy,
        search: debounced,
        page,
        ordering: "-posting_date,-id",
      }),
    placeholderData: keepPreviousData,
  });

  const counts = summary.data?.by_type ?? {};
  const tabCount = (types: string[]) => types.reduce((n, ty) => n + (counts[ty] ?? 0), 0);
  const err = list.error ? normaliseError(list.error) : null;
  const rows = list.data?.results ?? [];
  const totalPages = Math.max(1, Math.ceil((list.data?.count ?? 0) / PAGE_SIZE));
  const years = summary.data?.fiscal_years ?? [];

  return (
    <Layout>
      <div className="space-y-4 animate-fade-up">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-100 tracking-tight flex items-center gap-2">
              {t("pb.title")}
              {list.isFetching && <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}
            </h1>
            <p className="text-sm text-slate-400 mt-1">
              {years.length > 0
                ? t("pb.subtitle_years", { from: years[years.length - 1], to: years[0] })
                : t("pb.subtitle")}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Select
              value={fy}
              onChange={(v) => setParam("fy", v)}
              aria-label={t("lg.fields.fiscal_year")}
              className="w-40"
              options={[{ value: "", label: t("pb.all_years") }, ...years.map((y) => ({ value: y, label: y }))]}
            />
            <div className="relative">
              <Search className="w-4 h-4 absolute start-3 top-3 text-slate-400" />
              <input
                type="text"
                placeholder={t("pb.search")}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="bg-slate-950 border border-slate-800 rounded-lg ps-9 pe-4 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 w-72 max-w-full transition-colors"
              />
            </div>
          </div>
        </div>

        <div className="flex gap-1 overflow-x-auto no-scrollbar border-b border-slate-800" role="tablist">
          {BILL_TABS.map((b) => {
            const active = b.key === tabKey;
            return (
              <button
                key={b.key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setParam("type", b.key)}
                className={`relative px-3.5 py-2.5 text-sm whitespace-nowrap transition-colors ${
                  active ? "text-indigo-300" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {t(`pb.tab.${b.key}`)}
                {summary.data && (
                  <span className="ms-2 rounded-full bg-slate-800 px-2 py-0.5 text-[11px] tabular-nums text-slate-300">
                    {formatIndianNumber(tabCount(b.types))}
                  </span>
                )}
                <span
                  className={`absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-indigo-400 transition-transform duration-200 ${
                    active ? "scale-x-100" : "scale-x-0"
                  }`}
                />
              </button>
            );
          })}
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-xl overflow-hidden">
          {err ? (
            <div className="px-6 py-12 text-center space-y-2">
              <ShieldAlert className="w-8 h-8 mx-auto text-rose-400" />
              <p className="text-sm text-rose-300">{err.forbidden ? t("common.forbidden") : t("common.load_failed")}</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm text-slate-300">
                <thead className="bg-slate-950/80 text-xs uppercase text-slate-400 font-semibold border-b border-slate-800">
                  <tr>
                    <th className="px-5 py-3.5">{t("fields.posting_date")}</th>
                    <th className="px-5 py-3.5">{t("pb.bill_no")}</th>
                    <th className="px-5 py-3.5">{t("lg.fields.party")}</th>
                    <th className="px-5 py-3.5">{t("lg.fields.voucher_type")}</th>
                    <th className="px-5 py-3.5 text-right">{t("lg.fields.amount")}</th>
                    <th className="px-5 py-3.5">{t("pb.narration")}</th>
                  </tr>
                </thead>
                <tbody
                  className={`divide-y divide-slate-800/60 transition-opacity duration-200 ${
                    list.isPlaceholderData ? "opacity-60" : "opacity-100"
                  }`}
                >
                  {list.isLoading ? (
                    Array.from({ length: 8 }).map((_, i) => (
                      <tr key={i}>
                        <td colSpan={6} className="px-5 py-3">
                          <div className="h-4 rounded bg-slate-800/70 animate-pulse" style={{ width: `${60 + ((i * 13) % 35)}%` }} />
                        </td>
                      </tr>
                    ))
                  ) : rows.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-6 py-12 text-center text-slate-500">
                        {t("no_records")}
                      </td>
                    </tr>
                  ) : (
                    rows.map((row) => (
                      <tr
                        key={String(row.id)}
                        onClick={() => setParam("open", String(row.id))}
                        className="hover:bg-slate-800/40 transition-colors cursor-pointer"
                      >
                        <td className="px-5 py-3 tabular-nums">{formatDate(row.posting_date)}</td>
                        <td className="px-5 py-3 font-medium text-slate-100">
                          {row.voucher_no || "—"}
                          {row.is_cancelled && (
                            <span className="ms-2 rounded bg-rose-500/15 px-1.5 py-0.5 text-[11px] text-rose-300">
                              {t("cancelled")}
                            </span>
                          )}
                        </td>
                        <td className="px-5 py-3 max-w-[22rem] truncate" title={row.party}>
                          {row.party || "—"}
                        </td>
                        <td className="px-5 py-3 text-slate-400">{row.voucher_type}</td>
                        <td className="px-5 py-3 text-right tabular-nums text-slate-100">
                          {formatIndianCurrency(absAmount(row.amount))}
                        </td>
                        <td className="px-5 py-3 max-w-[18rem] truncate text-slate-500" title={row.narration}>
                          {row.narration}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}

          {!err && (list.data?.count ?? 0) > 0 && (
            <div className="px-5 py-3 border-t border-slate-800 flex items-center justify-between text-xs text-slate-400">
              <span>{t("common.total_records", { count: list.data?.count ?? 0 })}</span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                  className="p-1.5 rounded-md bg-slate-900 hover:bg-slate-800 border border-slate-800 shadow-sm disabled:opacity-40 transition-colors"
                  aria-label={t("common.previous")}
                >
                  <ChevronLeft className="w-4 h-4 rtl:rotate-180" />
                </button>
                <span>{t("common.page_of", { page, total: totalPages })}</span>
                <button
                  type="button"
                  disabled={!list.data?.next}
                  onClick={() => setPage((p) => p + 1)}
                  className="p-1.5 rounded-md bg-slate-900 hover:bg-slate-800 border border-slate-800 shadow-sm disabled:opacity-40 transition-colors"
                  aria-label={t("common.next")}
                >
                  <ChevronRight className="w-4 h-4 rtl:rotate-180" />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {openId && <BillDrawer id={openId} onClose={() => setParam("open", null)} />}
    </Layout>
  );
};

/** Slide-over panel: one old voucher as a bill (items, tax, accounting). */
const BillDrawer: React.FC<{ id: string; onClose: () => void }> = ({ id, onClose }) => {
  const { t } = useTranslation();
  const [closing, setClosing] = useState(false);
  const close = () => {
    setClosing(true);
    window.setTimeout(onClose, 180);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const q = useQuery({ queryKey: ["past-bills", "detail", id], queryFn: () => getRecord(ENDPOINT, id) });
  const bill = q.data as (ApiRecord & { item_lines: ItemLine[]; ledger_lines: LedgerLine[] }) | undefined;

  const itemsTotal = useMemo(
    () => (bill?.item_lines ?? []).reduce((s, l) => s + Number(l.amount), 0),
    [bill]
  );

  return createPortal(
    <div
      className={`fixed inset-0 z-[90] flex justify-end bg-black/40 backdrop-blur-[2px] ${
        closing ? "animate-fade-out" : "animate-fade-in"
      }`}
      onMouseDown={(e) => e.target === e.currentTarget && close()}
    >
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={t("pb.bill")}
        className={`h-full w-full max-w-3xl overflow-y-auto border-s border-slate-800 bg-slate-900 shadow-2xl ${
          closing ? "animate-slide-out" : "animate-slide-in"
        }`}
      >
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-slate-800 bg-slate-900/95 px-6 py-4 backdrop-blur">
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-wide text-slate-500">
              {bill ? `${bill.voucher_type} · ${bill.fiscal_year}` : t("common.loading")}
            </p>
            <h2 className="mt-0.5 flex items-center gap-2 text-lg font-semibold text-slate-100">
              <FileText className="h-5 w-5 text-indigo-400" />
              {bill?.voucher_no || "—"}
            </h2>
          </div>
          <button
            type="button"
            onClick={close}
            aria-label={t("common.close")}
            className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-800 hover:text-slate-200 transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {q.isLoading || !bill ? (
          <div className="space-y-3 p-6">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-5 rounded bg-slate-800/70 animate-pulse" />
            ))}
          </div>
        ) : (
          <div className="space-y-6 p-6 animate-fade-up">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
              <Info label={t("fields.posting_date")} value={formatDate(bill.posting_date)} />
              <Info label={t("lg.fields.party")} value={bill.party || "—"} wide />
              <Info label={t("lg.fields.amount")} value={formatIndianCurrency(absAmount(bill.amount))} strong />
              {bill.reference && <Info label={t("pb.reference")} value={bill.reference} />}
              {bill.is_cancelled && <Info label={t("status")} value={t("cancelled")} />}
            </dl>

            {bill.item_lines.length > 0 && (
              <section>
                <h3 className="mb-2 text-sm font-semibold text-slate-200">{t("pb.items")}</h3>
                <div className="overflow-x-auto rounded-lg border border-slate-800">
                  <table className="w-full text-sm text-slate-300">
                    <thead className="bg-slate-950/70 text-xs uppercase text-slate-400">
                      <tr>
                        <th className="px-3 py-2 text-start">{t("lg.fields.item_name")}</th>
                        <th className="px-3 py-2 text-right">{t("fields.qty")}</th>
                        <th className="px-3 py-2 text-right">{t("fields.rate")}</th>
                        <th className="px-3 py-2 text-right">{t("lg.fields.amount")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/60">
                      {bill.item_lines.map((l) => (
                        <tr key={l.id}>
                          <td className="px-3 py-2">{l.item_name}</td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {l.qty ? `${formatIndianNumber(l.qty)} ${l.uom}` : "—"}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">{l.rate ? formatIndianCurrency(l.rate) : "—"}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{formatIndianCurrency(l.amount)}</td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot className="border-t border-slate-700 text-slate-200">
                      <tr>
                        <td className="px-3 py-2 font-medium" colSpan={3}>
                          {t("pb.items_total")}
                        </td>
                        <td className="px-3 py-2 text-right font-medium tabular-nums">
                          {formatIndianCurrency(itemsTotal.toFixed(2))}
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              </section>
            )}

            {bill.ledger_lines.length > 0 && (
              <section>
                <h3 className="mb-2 text-sm font-semibold text-slate-200">{t("pb.accounting")}</h3>
                <div className="overflow-x-auto rounded-lg border border-slate-800">
                  <table className="w-full text-sm text-slate-300">
                    <thead className="bg-slate-950/70 text-xs uppercase text-slate-400">
                      <tr>
                        <th className="px-3 py-2 text-start">{t("pb.account")}</th>
                        <th className="px-3 py-2 text-right">{t("registers.dr")}</th>
                        <th className="px-3 py-2 text-right">{t("registers.cr")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/60">
                      {bill.ledger_lines.map((l, i) => (
                        <tr key={i}>
                          <td className="px-3 py-2">{l.account}</td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {Number(l.debit) ? formatIndianCurrency(l.debit) : ""}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {Number(l.credit) ? formatIndianCurrency(l.credit) : ""}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {bill.narration && (
              <section>
                <h3 className="mb-1 text-sm font-semibold text-slate-200">{t("pb.narration")}</h3>
                <p className="whitespace-pre-wrap text-sm text-slate-400">{bill.narration}</p>
              </section>
            )}

            <p className="text-xs text-slate-500">
              {t("pb.read_only_note")}{" "}
              {bill.erp_name && (
                <Link
                  to={`/accounts/journal-entries?search=${encodeURIComponent(bill.erp_name)}`}
                  className="text-indigo-300 hover:underline"
                >
                  {bill.erp_name}
                </Link>
              )}
            </p>
          </div>
        )}
      </aside>
    </div>,
    document.body
  );
};

const Info: React.FC<{ label: string; value: string; wide?: boolean; strong?: boolean }> = ({
  label,
  value,
  wide,
  strong,
}) => (
  <div className={wide ? "col-span-2" : ""}>
    <dt className="text-xs text-slate-500">{label}</dt>
    <dd className={`mt-0.5 ${strong ? "text-base font-semibold text-slate-100 tabular-nums" : "text-slate-200"}`}>
      {value}
    </dd>
  </div>
);
