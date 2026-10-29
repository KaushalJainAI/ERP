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
