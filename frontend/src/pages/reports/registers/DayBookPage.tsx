import React from "react";
import { useTranslation } from "react-i18next";
import { formatDate } from "@/lib/format";
import { ReportShell, useReport } from "../parts";
import { fmtMoney, isZeroDec } from "../gst/lib";
import {
  CsvButton,
  Hint,
  MessageRow,
  PAGE_SIZE,
  Pager,
  PeriodFilters,
  downloadCsv,
  fetchAllPages,
  td,
  tdR,
  tfootCls,
  th,
  theadCls,
  thR,
  useRegisterFilters,
  type PageInfo,
} from "./shared";

interface Line {
  account_id: number;
  account: string;
  party: string | null;
  debit: string;
  credit: string;
  dr_cr: string;
}
interface Voucher {
  posting_date: string;
  voucher_type: string;
  voucher_no: string;
  party: string | null;
  remarks: string | null;
  total_debit: string;
  total_credit: string;
  lines: Line[];
}
interface Daily {
  date: string;
  vouchers: number;
  debit: string;
  credit: string;
}
interface DayBook {
  vouchers: Voucher[];
  daily_totals: Daily[];
  totals: { vouchers: number; debit: string; credit: string };
  pagination: PageInfo;
}

export const DayBookPage: React.FC = () => {
  const { t } = useTranslation();
  const f = useRegisterFilters();
  const base = { company: f.company, from_date: f.from, to_date: f.to };
  const { data, isFetching, error } = useReport<DayBook>(
    "/accounts/reports/day-book/",
    { ...base, page: String(f.page), page_size: String(PAGE_SIZE) },
    f.ready,
  );
  const daily = new Map((data?.daily_totals ?? []).map((d) => [d.date, d]));
  const lastPage = !data || data.pagination.page >= data.pagination.num_pages;

  const exportCsv = async () => {
    const { items } = await fetchAllPages<DayBook>("/accounts/reports/day-book/", base, "vouchers");
    const head = [
      t("fields.posting_date"),
      t("acc.fields.voucher_type"),
      t("acc.fields.voucher_no"),
      t("acc.fields.party"),
      t("acc.fields.account"),
      t("acc.fields.debit"),
      t("acc.fields.credit"),
    ];
    const rows = (items as Voucher[]).flatMap((v) =>
      v.lines.map((l) => [v.posting_date, v.voucher_type, v.voucher_no, l.party ?? v.party ?? "", l.account, l.debit, l.credit]),
    );
    downloadCsv(`day-book_${f.from}_${f.to}`, [head, ...rows]);
  };

  return (
    <ReportShell
      titleKey="registers.nav.day_book"
      loading={isFetching || f.loadingCompanies}
      error={error}
      noCompany={!f.loadingCompanies && f.companies.length === 0}
      status={<CsvButton onExport={exportCsv} disabled={!data || data.vouchers.length === 0} />}
      filters={<PeriodFilters f={f} prefix="db" />}
    >
      {!f.ready ? (
        <Hint textKey="registers.pick_period" />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-300">
              <thead className={theadCls}>
                <tr>
                  <th className={th}>{t("fields.posting_date")}</th>
                  <th className={th}>{t("acc.fields.voucher_type")}</th>
                  <th className={th}>{t("acc.fields.voucher_no")}</th>
                  <th className={th}>{t("acc.fields.account")}</th>
                  <th className={th}>{t("acc.fields.party")}</th>
                  <th className={thR}>{t("acc.fields.debit")}</th>
                  <th className={thR}>{t("acc.fields.credit")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {!data || data.vouchers.length === 0 ? (
                  <MessageRow cols={7} loading={!data} />
                ) : (
                  data.vouchers.map((v, i) => {
                    const next = data.vouchers[i + 1];
                    const dayEnds = next ? next.posting_date !== v.posting_date : lastPage;
                    const day = daily.get(v.posting_date);
                    return (
                      <React.Fragment key={`${v.voucher_type}:${v.voucher_no}`}>
                        {v.lines.map((l, j) => (
                          <tr key={j} className={`hover:bg-slate-800/40 ${j === 0 ? "border-t border-slate-700/70" : ""}`}>
                            <td className={`${td} whitespace-nowrap`}>{j === 0 ? formatDate(v.posting_date) : ""}</td>
                            <td className={td}>{j === 0 ? v.voucher_type : ""}</td>
                            <td className={td}>{j === 0 ? v.voucher_no : ""}</td>
                            <td className={`${td} ${isZeroDec(l.debit) ? "ps-8" : ""}`}>{l.account}</td>
                            <td className={td}>{l.party ?? ""}</td>
                            <td className={tdR}>{isZeroDec(l.debit) ? "" : fmtMoney(l.debit)}</td>
                            <td className={tdR}>{isZeroDec(l.credit) ? "" : fmtMoney(l.credit)}</td>
                          </tr>
                        ))}
                        {dayEnds && day && (
                          <tr className="bg-slate-950/50 text-slate-100 font-semibold">
                            <td className={td} colSpan={5}>
                              {t("registers.daily_total")} {formatDate(day.date)} ({t("registers.vouchers_count", { count: day.vouchers })})
                            </td>
                            <td className={tdR}>{fmtMoney(day.debit)}</td>
                            <td className={tdR}>{fmtMoney(day.credit)}</td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })
                )}
              </tbody>
              {data && data.vouchers.length > 0 && (
                <tfoot className={tfootCls}>
                  <tr>
                    <td className={td} colSpan={5}>
                      {t("acc.report.total")} ({t("registers.vouchers_count", { count: data.totals.vouchers })})
                    </td>
                    <td className={tdR}>{fmtMoney(data.totals.debit)}</td>
                    <td className={tdR}>{fmtMoney(data.totals.credit)}</td>
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
          <Pager info={data?.pagination} onPage={f.setPage} />
        </>
      )}
    </ReportShell>
  );
};
