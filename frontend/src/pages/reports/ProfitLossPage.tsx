import React from "react";
import { useTranslation } from "react-i18next";
import { formatIndianCurrency } from "@/lib/format";
import { CompanySelect, ReportShell, subDec, useCompany, useReport } from "./parts";

interface PnlResponse {
  total_income: string;
  total_expense: string;
  net_profit: string;
}

const Row: React.FC<{ label: string; value: string; strong?: boolean; tone?: string }> = ({ label, value, strong, tone }) => (
  <div className={`flex items-baseline justify-between gap-4 px-5 py-3.5 ${strong ? "bg-slate-950/60 font-semibold text-slate-100" : ""}`}>
    <dt className="text-sm">{label}</dt>
    <dd className={`tabular-nums ${strong ? "text-lg" : "text-sm"} ${tone ?? ""}`}>{formatIndianCurrency(value)}</dd>
  </div>
);

export const ProfitLossPage: React.FC = () => {
  const { t } = useTranslation();
  const { companies, company, setCompany, loadingCompanies } = useCompany();
  const { data, isFetching, error } = useReport<PnlResponse>("/accounts/reports/profit-and-loss/", { company }, !!company);

  // net = income - expense; recomputed exactly here and compared to the server figure.
  const net = data ? subDec(data.total_income, data.total_expense) : "0.00";
  const isLoss = net.startsWith("-");

  return (
    <ReportShell
      titleKey="acc.nav.profit_loss"
      loading={isFetching || loadingCompanies}
      error={error}
      noCompany={!loadingCompanies && companies.length === 0}
      filters={<CompanySelect companies={companies} value={company} onChange={setCompany} />}
    >
      {!data ? (
        <div className="px-6 py-10 text-center text-slate-400 text-sm">{t("common.loading")}</div>
      ) : (
        <dl className="divide-y divide-slate-800/60 text-slate-300">
          <Row label={t("acc.report.total_income")} value={data.total_income} />
          <Row label={t("acc.report.total_expense")} value={data.total_expense} />
          <Row
            strong
            label={isLoss ? t("acc.report.net_loss") : t("acc.report.net_profit")}
            value={net}
            tone={isLoss ? "text-rose-300" : "text-emerald-400"}
          />
        </dl>
      )}
      <p className="px-5 py-3 text-xs text-slate-500 border-t border-slate-800">{t("acc.report.all_posted")}</p>
    </ReportShell>
  );
};
