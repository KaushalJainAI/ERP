import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { formatDate, todayISO } from "@/lib/format";
import { DateInput, ReportShell, StatusPill, isZero, useReport } from "../reports/parts";
import { fmtMoney } from "../reports/gst/lib";
import { td, tdR, th, thR, theadCls } from "../reports/registers/shared";
import { BankAccountSelect, useBankAccounts } from "./shared";

interface Line {
  voucher_type: string;
  voucher_no: string;
  posting_date: string;
  reference_no: string;
  party: string;
  amount: string;
}
interface Brs {
  gl_balance: string;
  uncleared_receipts: Line[];
  uncleared_receipts_total: string;
  uncleared_payments: Line[];
  uncleared_payments_total: string;
  bank_balance: string;
  statement_balance: string | null;
  statement_balance_date: string | null;
  difference: string | null;
}

const Total: React.FC<{ label: string; value: string; strong?: boolean }> = ({ label, value, strong }) => (
  <div className={`flex items-baseline justify-between gap-4 px-5 py-3 ${strong ? "bg-slate-950/60 font-semibold text-slate-100" : "text-slate-300"}`}>
    <dt className="text-sm">{label}</dt>
    <dd className="tabular-nums text-sm">{fmtMoney(value)}</dd>
  </div>
);

const Lines: React.FC<{ rows: Line[] }> = ({ rows }) => {
  const { t } = useTranslation();
  if (rows.length === 0) return <p className="px-5 py-3 text-xs text-slate-500">{t("bank.brs.none")}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">
        <thead className={theadCls}>
          <tr>
            <th className={th}>{t("fields.posting_date")}</th>
            <th className={th}>{t("bank.rec.voucher")}</th>
            <th className={th}>{t("bank.brs.reference")}</th>
            <th className={thR}>{t("bank.rec.amount")}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/60 text-slate-300">
          {rows.map((r) => (
            <tr key={`${r.voucher_type}|${r.voucher_no}`}>
              <td className={`${td} whitespace-nowrap`}>{formatDate(r.posting_date)}</td>
              <td className={td}>
                {r.voucher_no}
                {r.party && <div className="text-xs text-slate-500">{r.party}</div>}
              </td>
              <td className={td}>{r.reference_no}</td>
              <td className={tdR}>{fmtMoney(r.amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export const BankReconciliationStatementPage: React.FC = () => {
  const { t } = useTranslation();
  const { accounts, bankAccount, setBankAccount, loading } = useBankAccounts();
  const [asOn, setAsOn] = useState(todayISO());
  const { data, isFetching, error } = useReport<Brs>(
    "/accounts/reports/bank-reconciliation-statement/",
    { bank_account: bankAccount, as_on: asOn },
    !!bankAccount && !!asOn,
  );
  const tied = !!data && data.difference !== null && isZero(data.difference);

  return (
    <ReportShell
      titleKey="bank.nav.brs"
      loading={isFetching || loading}
      error={error}
      noCompany={!loading && accounts.length === 0}
      status={
        data?.difference != null && (
          <span
            className={`text-xs px-2.5 py-1 rounded-md border font-semibold ${
              tied ? "bg-emerald-950 text-emerald-400 border-emerald-800" : "bg-amber-950 text-amber-300 border-amber-800"
            }`}
          >
            {tied ? t("bank.brs.reconciled") : t("bank.brs.not_reconciled")}
          </span>
        )
      }
      filters={
        <>
          <BankAccountSelect accounts={accounts} value={bankAccount} onChange={setBankAccount} />
          <DateInput id="brs-as-on" labelKey="bank.as_on" value={asOn} onChange={setAsOn} />
        </>
      }
    >
      {data && (
        <div className="divide-y divide-slate-800">
          <dl>
            <Total label={t("bank.brs.gl_balance")} value={data.gl_balance} strong />
          </dl>
          <section>
            <h2 className="px-5 py-3 text-xs uppercase tracking-wider text-slate-400 bg-slate-950/80">{t("bank.brs.less_receipts")}</h2>
            <Lines rows={data.uncleared_receipts} />
            <dl>
              <Total label={t("bank.brs.total")} value={data.uncleared_receipts_total} />
            </dl>
          </section>
          <section>
            <h2 className="px-5 py-3 text-xs uppercase tracking-wider text-slate-400 bg-slate-950/80">{t("bank.brs.add_payments")}</h2>
            <Lines rows={data.uncleared_payments} />
            <dl>
              <Total label={t("bank.brs.total")} value={data.uncleared_payments_total} />
            </dl>
          </section>
          <dl>
            <Total label={t("bank.brs.bank_balance")} value={data.bank_balance} strong />
            {data.statement_balance !== null && (
              <>
                <Total
                  label={t("bank.brs.statement_balance", { date: formatDate(data.statement_balance_date) })}
                  value={data.statement_balance}
                />
                <Total label={t("bank.brs.difference")} value={data.difference ?? "0.00"} strong />
              </>
            )}
          </dl>
          <p className="px-5 py-3 text-xs text-slate-500">{t("bank.brs.note")}</p>
        </div>
      )}
    </ReportShell>
  );
};
