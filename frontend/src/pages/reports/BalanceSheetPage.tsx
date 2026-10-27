import React from "react";
import { useTranslation } from "react-i18next";
import { sumDec } from "@/lib/decimal";
import { formatIndianCurrency } from "@/lib/format";
import { CompanySelect, ReportShell, StatusPill, isZero, subDec, useCompany, useReport } from "./parts";

interface BsResponse {
  total_assets: string;
  total_liabilities: string;
  total_equity: string;
}
interface PnlResponse {
  net_profit: string;
}

const Line: React.FC<{ label: string; value: string; strong?: boolean }> = ({ label, value, strong }) => (
  <div className={`flex items-baseline justify-between gap-4 px-5 py-3.5 ${strong ? "bg-slate-950/60 font-semibold text-slate-100" : ""}`}>
    <dt className="text-sm">{label}</dt>
    <dd className={`tabular-nums ${strong ? "text-lg" : "text-sm"}`}>{formatIndianCurrency(value)}</dd>
  </div>
);

export const BalanceSheetPage: React.FC = () => {
  const { t } = useTranslation();
  const { companies, company, setCompany, loadingCompanies } = useCompany();
  const enabled = !!company;
  const bs = useReport<BsResponse>("/accounts/reports/balance-sheet/", { company }, enabled);
  // Income/expense are not closed into equity by a period-closing voucher, so the
  // accounting equation only holds once the running P&L is included.
  const pnl = useReport<PnlResponse>("/accounts/reports/profit-and-loss/", { company }, enabled);

  const ready = !!bs.data && !!pnl.data;
  const unclosed = pnl.data?.net_profit ?? "0.00";
  const rightSide = ready
    ? sumDec([bs.data!.total_liabilities, bs.data!.total_equity, unclosed], 2)
    : "0.00";
  const diff = ready ? subDec(bs.data!.total_assets, rightSide) : "0.00";
  const balanced = ready && isZero(diff);

  return (
    <ReportShell
      titleKey="acc.nav.balance_sheet"
      loading={bs.isFetching || pnl.isFetching || loadingCompanies}
      error={bs.error ?? pnl.error}
      noCompany={!loadingCompanies && companies.length === 0}
      status={ready && <StatusPill ok={balanced} />}
      filters={<CompanySelect companies={companies} value={company} onChange={setCompany} />}
    >
      {!ready ? (
        <div className="px-6 py-10 text-center text-slate-400 text-sm">{t("common.loading")}</div>
      ) : (
        <div className="grid md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-slate-800">
          <section>
            <h2 className="px-5 py-3 text-xs uppercase tracking-wider text-slate-400 bg-slate-950/80 border-b border-slate-800">
              {t("acc.report.assets_side")}
            </h2>
            <dl className="text-slate-300">
              <Line strong label={t("acc.report.total_assets")} value={bs.data!.total_assets} />
            </dl>
          </section>
          <section>
            <h2 className="px-5 py-3 text-xs uppercase tracking-wider text-slate-400 bg-slate-950/80 border-b border-slate-800">
              {t("acc.report.liabilities_side")}
            </h2>
            <dl className="divide-y divide-slate-800/60 text-slate-300">
              <Line label={t("acc.report.total_liabilities")} value={bs.data!.total_liabilities} />
              <Line label={t("acc.report.total_equity")} value={bs.data!.total_equity} />
              <Line label={t("acc.report.unclosed_pnl")} value={unclosed} />
              <Line strong label={t("acc.report.liabilities_equity")} value={rightSide} />
            </dl>
          </section>
        </div>
      )}
      {ready && (
        <div className="px-5 py-3 border-t border-slate-800 text-xs flex flex-wrap items-center justify-between gap-2">
          <span className="text-slate-500">{t("acc.report.equation")}</span>
          <span className={`tabular-nums ${balanced ? "text-emerald-400" : "text-rose-300"}`}>
            {t("acc.report.td_diff")}: {formatIndianCurrency(diff)}
          </span>
        </div>
      )}
      <p className="px-5 py-3 text-xs text-slate-500 border-t border-slate-800">{t("acc.report.all_posted")}</p>
    </ReportShell>
  );
};
