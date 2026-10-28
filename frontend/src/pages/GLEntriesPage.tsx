import React, { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight, Loader2, RotateCcw, ShieldAlert } from "lucide-react";
import { Layout } from "@/components/Layout";
import { LinkField } from "@/components/doc/LinkField";
import { PAGE_SIZE, listRecords, normaliseError } from "@/api/endpoints";
import { useDebounced } from "@/hooks/useDebounced";
import { formatDate, formatIndianCurrency } from "@/lib/format";
import { DateInput, inputCls } from "@/pages/reports/parts";
import type { LinkDef } from "@/types";

const accountLink: LinkDef = {
  endpoint: "/accounts/accounts/",
  labelField: "name",
  subLabel: (r) => r.company_name,
  filters: () => ({ is_group: false }),
};

const money = (v: string) => <span className="tabular-nums">{formatIndianCurrency(v)}</span>;

/**
 * Read-only ledger list. GL entries are immutable facts written only by posting
 * (submit / cancel); there is deliberately no New button and no detail page.
 */
export const GLEntriesPage: React.FC = () => {
  const { t } = useTranslation();
  const [account, setAccount] = useState("");
  const [voucherNo, setVoucherNo] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [page, setPage] = useState(1);
  const debouncedVoucher = useDebounced(voucherNo.trim(), 300);

  useEffect(() => setPage(1), [account, debouncedVoucher, fromDate, toDate]);

  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ["list", "/accounts/gl-entries/", { page, account, debouncedVoucher, fromDate, toDate }],
    queryFn: () =>
      listRecords("/accounts/gl-entries/", {
        page,
        ordering: "-posting_date,-created_at",
        account,
        voucher_no: debouncedVoucher,
        posting_date__gte: fromDate,
        posting_date__lte: toDate,
      }),
    placeholderData: keepPreviousData,
  });

  const err = error ? normaliseError(error) : null;
  const rows = data?.results ?? [];
  const totalPages = Math.max(1, Math.ceil((data?.count ?? 0) / PAGE_SIZE));
  const filtered = !!(account || voucherNo || fromDate || toDate);
  const reset = () => {
    setAccount("");
    setVoucherNo("");
    setFromDate("");
    setToDate("");
  };

  return (
    <Layout>
      <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-xl overflow-hidden">
        <div className="p-5 border-b border-slate-800 space-y-4">
          <div>
            <h1 className="text-xl font-semibold text-slate-100 tracking-tight flex items-center gap-2">
              {t("acc.gl.title")}
              {isFetching && <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}
            </h1>
            <p className="text-xs text-slate-500 mt-1">{t("acc.gl.read_only")}</p>
          </div>
          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1 w-64 max-w-full">
              <label htmlFor="gle-account" className="block text-xs font-medium text-slate-400">
                {t("acc.fields.account")}
              </label>
              <LinkField
                id="gle-account"
                link={accountLink}
                cacheKey="acc.gl-entries.account"
                value={account}
                values={{}}
                parentValues={{}}
                onSelect={(v) => setAccount(v === null ? "" : String(v))}
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="gle-voucher" className="block text-xs font-medium text-slate-400">
                {t("acc.fields.voucher_no")}
              </label>
              <input
                id="gle-voucher"
                type="text"
                value={voucherNo}
                placeholder={t("acc.gl.voucher_placeholder")}
                onChange={(e) => setVoucherNo(e.target.value)}
                className={`${inputCls} w-52`}
              />
            </div>
            <DateInput id="gle-from" labelKey="acc.fields.from_date" value={fromDate} onChange={setFromDate} />
            <DateInput id="gle-to" labelKey="acc.fields.to_date" value={toDate} onChange={setToDate} />
            {filtered && (
              <button
                type="button"
                onClick={reset}
                className="bg-slate-900 hover:bg-slate-800 border border-slate-800 shadow-sm text-slate-200 px-3 py-2 rounded-lg text-sm flex items-center gap-1.5"
              >
                <RotateCcw className="w-4 h-4" /> {t("acc.gl.reset")}
              </button>
            )}
          </div>
        </div>

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
                  <th className="px-5 py-3.5">{t("acc.fields.account")}</th>
                  <th className="px-5 py-3.5">{t("acc.fields.voucher_type")}</th>
                  <th className="px-5 py-3.5">{t("acc.fields.voucher_no")}</th>
                  <th className="px-5 py-3.5">{t("acc.fields.party")}</th>
                  <th className="px-5 py-3.5 text-right">{t("acc.fields.debit")}</th>
                  <th className="px-5 py-3.5 text-right">{t("acc.fields.credit")}</th>
                  <th className="px-5 py-3.5">{t("acc.fields.remarks")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {isLoading ? (
                  <tr>
                    <td colSpan={8} className="px-6 py-10 text-center text-slate-400">
                      {t("common.loading")}
                    </td>
                  </tr>
                ) : rows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-6 py-12 text-center text-slate-500">
                      {t("no_records")}
                    </td>
                  </tr>
                ) : (
                  rows.map((r) => (
                    <tr key={String(r.id)} className={`hover:bg-slate-800/40 ${r.is_cancelled ? "opacity-60" : ""}`}>
                      <td className="px-5 py-3 whitespace-nowrap">{formatDate(r.posting_date)}</td>
                      <td className="px-5 py-3">{r.account_name}</td>
                      <td className="px-5 py-3">{r.voucher_type}</td>
                      <td className="px-5 py-3">
                        {r.voucher_no}
                        {r.is_cancelled && (
                          <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-rose-950/60 text-rose-300 border border-rose-900/60">
                            {t("acc.fields.cancelled_entry")}
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-3">{r.party ?? ""}</td>
                      <td className="px-5 py-3 text-right">{money(r.debit)}</td>
                      <td className="px-5 py-3 text-right">{money(r.credit)}</td>
                      <td className="px-5 py-3 max-w-xs truncate" title={r.remarks ?? ""}>
                        {r.remarks ?? ""}
                      </td>
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
