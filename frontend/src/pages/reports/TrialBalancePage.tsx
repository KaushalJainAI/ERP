import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { formatIndianCurrency } from "@/lib/format";
import { CompanySelect, DateInput, ReportShell, StatusPill, isZero, subDec, useCompany, useReport } from "./parts";

interface TbRow {
  account_id: number;
  account_name: string;
  root_type: string;
  debit: string;
  credit: string;
  net_balance: string;
}
interface TbResponse {
  rows: TbRow[];
  total_debit: string;
  total_credit: string;
  is_balanced: boolean;
}

const money = (v: string) => <span className="tabular-nums">{formatIndianCurrency(v)}</span>;

export const TrialBalancePage: React.FC = () => {
  const { t } = useTranslation();
  const { companies, company, setCompany, loadingCompanies } = useCompany();
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");

  const params = { company, from_date: fromDate, to_date: toDate };
  const { data, isFetching, error } = useReport<TbResponse>("/accounts/reports/trial-balance/", params, !!company);

  const diff = data ? subDec(data.total_debit, data.total_credit) : "0.00";
  // Both the server flag and the displayed totals must agree for "balanced".
  const balanced = !!data && data.is_balanced && isZero(diff);

  const glLink = (accountId: number) => {
    const q = new URLSearchParams({ account: String(accountId), company });
    if (fromDate) q.set("from_date", fromDate);
    if (toDate) q.set("to_date", toDate);
    return `/accounts/reports/general-ledger?${q.toString()}`;
  };

  return (
    <ReportShell
      titleKey="acc.nav.trial_balance"
      loading={isFetching || loadingCompanies}
      error={error}
      noCompany={!loadingCompanies && companies.length === 0}
      status={data && <StatusPill ok={balanced} />}
      filters={
        <>
          <CompanySelect companies={companies} value={company} onChange={setCompany} />
          <DateInput id="tb-from" labelKey="acc.fields.from_date" value={fromDate} onChange={setFromDate} />
          <DateInput id="tb-to" labelKey="acc.fields.to_date" value={toDate} onChange={setToDate} />
        </>
      }
    >
      {/* Phone: account cards with the amounts stacked, instead of a five-column scroll. */}
      <div className="space-y-2 p-3 md:hidden">
        {!data ? (
          <p className="px-2 py-8 text-center text-sm text-slate-400">{t("common.loading")}</p>
        ) : data.rows.length === 0 ? (
          <p className="px-2 py-8 text-center text-sm text-slate-500">{t("acc.report.no_data")}</p>
        ) : (
          data.rows.map((r) => (
            <div key={r.account_id} className="rounded-xl border border-slate-800 bg-slate-900 p-3.5 shadow-sm">
              <Link
                className="block truncate text-sm font-semibold text-indigo-300 hover:underline"
                to={glLink(r.account_id)}
                title={t("acc.report.view_gl")}
              >
                {r.account_name}
              </Link>
              <p className="mt-0.5 text-[11px] text-slate-500">
                {t(`acc.opt.${r.root_type.toLowerCase()}`, { defaultValue: r.root_type })}
              </p>
              <dl className="mt-2.5 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
                <div>
                  <dt className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{t("acc.fields.debit")}</dt>
                  <dd className="mt-0.5 text-slate-200">{money(r.debit)}</dd>
                </div>
                <div>
                  <dt className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{t("acc.fields.credit")}</dt>
                  <dd className="mt-0.5 text-slate-200">{money(r.credit)}</dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{t("acc.fields.net_balance")}</dt>
                  <dd className="mt-0.5 font-semibold text-slate-100">{money(r.net_balance)}</dd>
                </div>
              </dl>
            </div>
          ))
        )}
      </div>

      {data && (
        <div className="border-t border-slate-700 bg-slate-950/60 p-4 md:hidden">
          <dl className="space-y-2 text-xs">
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-slate-400">{t("acc.fields.debit")}</dt>
              <dd className="font-semibold text-slate-100">{money(data.total_debit)}</dd>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-slate-400">{t("acc.fields.credit")}</dt>
              <dd className="font-semibold text-slate-100">{money(data.total_credit)}</dd>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-slate-400">{t("acc.report.td_diff")}</dt>
              <dd className={`font-semibold ${balanced ? "text-emerald-400" : "text-rose-300"}`}>{money(diff)}</dd>
            </div>
          </dl>
        </div>
      )}

      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-left text-sm text-slate-300">
          <thead className="bg-slate-950/80 text-xs uppercase text-slate-400 font-semibold border-b border-slate-800">
            <tr>
              <th className="px-5 py-3.5">{t("acc.fields.account")}</th>
              <th className="px-5 py-3.5">{t("acc.fields.root_type")}</th>
              <th className="px-5 py-3.5 text-right">{t("acc.fields.debit")}</th>
              <th className="px-5 py-3.5 text-right">{t("acc.fields.credit")}</th>
              <th className="px-5 py-3.5 text-right">{t("acc.fields.net_balance")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {!data ? (
              <tr>
                <td colSpan={5} className="px-6 py-10 text-center text-slate-400">
                  {t("common.loading")}
                </td>
              </tr>
            ) : data.rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-6 py-12 text-center text-slate-500">
                  {t("acc.report.no_data")}
                </td>
              </tr>
            ) : (
              data.rows.map((r) => (
                <tr key={r.account_id} className="hover:bg-slate-800/40">
                  <td className="px-5 py-3">
                    <Link className="text-indigo-300 hover:underline" to={glLink(r.account_id)} title={t("acc.report.view_gl")}>
                      {r.account_name}
                    </Link>
                  </td>
                  <td className="px-5 py-3">{t(`acc.opt.${r.root_type.toLowerCase()}`, { defaultValue: r.root_type })}</td>
                  <td className="px-5 py-3 text-right">{money(r.debit)}</td>
                  <td className="px-5 py-3 text-right">{money(r.credit)}</td>
                  <td className="px-5 py-3 text-right">{money(r.net_balance)}</td>
                </tr>
              ))
            )}
          </tbody>
          {data && (
            <tfoot className="bg-slate-950/60 border-t border-slate-700 font-semibold text-slate-100">
              <tr>
                <td className="px-5 py-3" colSpan={2}>
                  {t("acc.report.grand_total")}
                </td>
                <td className="px-5 py-3 text-right">{money(data.total_debit)}</td>
                <td className="px-5 py-3 text-right">{money(data.total_credit)}</td>
                <td className={`px-5 py-3 text-right ${balanced ? "text-emerald-400" : "text-rose-300"}`}>
                  {t("acc.report.td_diff")}: {money(diff)}
                </td>
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </ReportShell>
  );
};
