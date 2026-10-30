import React, { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LinkField } from "@/components/doc/LinkField";
import { sumDec } from "@/lib/decimal";
import { formatDate, formatIndianCurrency } from "@/lib/format";
import { VoucherNo, voucherTypeText, type TallyRef } from "./registers/shared";
import { CompanySelect, DateInput, ReportShell, useCompany, useReport } from "./parts";
import type { LinkDef } from "@/types";

interface GlRow {
  id: number;
  posting_date: string;
  account_name: string;
  voucher_type: string;
  voucher_no: string;
  tally: TallyRef | null;
  party: string | null;
  debit: string;
  credit: string;
  balance: string;
}
interface GlResponse {
  entries: GlRow[];
  closing_balance: string;
}

const accountFilterLink: LinkDef = {
  endpoint: "/accounts/accounts/",
  labelField: "name",
  filters: () => ({ is_group: false }),
};

const money = (v: string) => <span className="tabular-nums">{formatIndianCurrency(v)}</span>;

export const GeneralLedgerPage: React.FC = () => {
  const { t } = useTranslation();
  const [sp] = useSearchParams();
  const { companies, company: defaultCompany, setCompany, loadingCompanies } = useCompany();
  // A deep link (?company=) from the Trial Balance wins over the default.
  const [companyOverride, setCompanyOverride] = useState<string>(sp.get("company") ?? "");
  const company = companyOverride || defaultCompany;
  const [account, setAccount] = useState<string>(sp.get("account") ?? "");
  const [fromDate, setFromDate] = useState(sp.get("from_date") ?? "");
  const [toDate, setToDate] = useState(sp.get("to_date") ?? "");

  const params = { company, account, from_date: fromDate, to_date: toDate };
  const { data, isFetching, error } = useReport<GlResponse>("/accounts/reports/general-ledger/", params, !!company);
  // The server's running balance spans every row returned, so it is only
  // meaningful for a single account.
  const showBalance = !!account;
  const cols = showBalance ? 8 : 7;

  return (
    <ReportShell
      titleKey="acc.nav.general_ledger"
      loading={isFetching || loadingCompanies}
      error={error}
      noCompany={!loadingCompanies && companies.length === 0}
      filters={
        <>
          <CompanySelect
            companies={companies}
            value={company}
            onChange={(v) => {
              setCompany(v);
              setCompanyOverride(v);
              setAccount("");
            }}
          />
          <div className="space-y-1 w-64 max-w-full">
            <label htmlFor="gl-account" className="block text-xs font-medium text-slate-400">
              {t("acc.fields.account")}
            </label>
            <LinkField
              id="gl-account"
              link={{ ...accountFilterLink, filters: () => ({ is_group: false, company }) }}
              cacheKey="acc-report.gl.account"
              value={account}
              values={{}}
              parentValues={{}}
              onSelect={(v) => setAccount(v === null ? "" : String(v))}
            />
          </div>
          <DateInput id="gl-from" labelKey="acc.fields.from_date" value={fromDate} onChange={setFromDate} />
          <DateInput id="gl-to" labelKey="acc.fields.to_date" value={toDate} onChange={setToDate} />
        </>
      }
    >
      {/* Phone: voucher cards with the amounts stacked, instead of a seven-column scroll. */}
      <div className="space-y-2 p-3 md:hidden">
        {!data ? (
          <p className="px-2 py-8 text-center text-sm text-slate-400">{t("common.loading")}</p>
        ) : data.entries.length === 0 ? (
          <p className="px-2 py-8 text-center text-sm text-slate-500">{t("acc.report.no_data")}</p>
        ) : (
          data.entries.map((e) => (
            <div key={e.id} className="rounded-xl border border-slate-800 bg-slate-900 p-3.5 shadow-sm">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-slate-100">{e.account_name}</p>
                  <p className="mt-0.5 text-[11px] text-slate-500">
                    {formatDate(e.posting_date)} · {voucherTypeText(e)}
                  </p>
                </div>
                <VoucherNo row={e} />
              </div>
              <dl className="mt-2.5 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
                <div>
                  <dt className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{t("acc.fields.debit")}</dt>
                  <dd className="mt-0.5 text-slate-200">{money(e.debit)}</dd>
                </div>
                <div>
                  <dt className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{t("acc.fields.credit")}</dt>
                  <dd className="mt-0.5 text-slate-200">{money(e.credit)}</dd>
                </div>
                {showBalance && (
                  <div className="col-span-2">
                    <dt className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{t("acc.fields.balance")}</dt>
                    <dd className="mt-0.5 font-semibold text-slate-100">{money(e.balance)}</dd>
                  </div>
                )}
                {e.party && (
                  <div className="col-span-2">
                    <dt className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{t("acc.fields.party")}</dt>
                    <dd className="mt-0.5 truncate text-slate-300">{e.party}</dd>
                  </div>
                )}
              </dl>
            </div>
          ))
        )}
      </div>

      {data && data.entries.length > 0 && (
        <div className="border-t border-slate-700 bg-slate-950/60 p-4 md:hidden">
          <dl className="space-y-2 text-xs">
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-slate-400">
                {t("acc.report.total")} ({t("acc.report.entries_count", { count: data.entries.length })})
              </dt>
              <dd />
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-slate-400">{t("acc.fields.debit")}</dt>
              <dd className="font-semibold text-slate-100">{money(sumDec(data.entries.map((e) => e.debit), 2))}</dd>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-slate-400">{t("acc.fields.credit")}</dt>
              <dd className="font-semibold text-slate-100">{money(sumDec(data.entries.map((e) => e.credit), 2))}</dd>
            </div>
            {showBalance && (
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-slate-400">{t("acc.report.closing_balance")}</dt>
                <dd className="font-semibold text-slate-100">{money(data.closing_balance)}</dd>
              </div>
            )}
          </dl>
        </div>
      )}

      <div className="hidden overflow-x-auto md:block">
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
              {showBalance && <th className="px-5 py-3.5 text-right">{t("acc.fields.balance")}</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {!data ? (
              <tr>
                <td colSpan={cols} className="px-6 py-10 text-center text-slate-400">
                  {t("common.loading")}
                </td>
              </tr>
            ) : data.entries.length === 0 ? (
              <tr>
                <td colSpan={cols} className="px-6 py-12 text-center text-slate-500">
                  {t("acc.report.no_data")}
                </td>
              </tr>
            ) : (
              data.entries.map((e) => (
                <tr key={e.id} className="hover:bg-slate-800/40">
                  <td className="px-5 py-3 whitespace-nowrap">{formatDate(e.posting_date)}</td>
                  <td className="px-5 py-3">{e.account_name}</td>
                  <td className="px-5 py-3">{voucherTypeText(e)}</td>
                  <td className="px-5 py-3">
                    <VoucherNo row={e} />
                  </td>
                  <td className="px-5 py-3">{e.party ?? ""}</td>
                  <td className="px-5 py-3 text-right">{money(e.debit)}</td>
                  <td className="px-5 py-3 text-right">{money(e.credit)}</td>
                  {showBalance && <td className="px-5 py-3 text-right">{money(e.balance)}</td>}
                </tr>
              ))
            )}
          </tbody>
          {data && data.entries.length > 0 && (
            <tfoot className="bg-slate-950/60 border-t border-slate-700 font-semibold text-slate-100">
              <tr>
                <td className="px-5 py-3" colSpan={5}>
                  {t("acc.report.total")} ({t("acc.report.entries_count", { count: data.entries.length })})
                </td>
                <td className="px-5 py-3 text-right">{money(sumDec(data.entries.map((e) => e.debit), 2))}</td>
                <td className="px-5 py-3 text-right">{money(sumDec(data.entries.map((e) => e.credit), 2))}</td>
                {showBalance && (
                  <td className="px-5 py-3 text-right" title={t("acc.report.closing_balance")}>
                    {money(data.closing_balance)}
                  </td>
                )}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      {!showBalance && <p className="px-5 py-3 text-xs text-slate-500">{t("acc.report.running_balance_hint")}</p>}
    </ReportShell>
  );
};
