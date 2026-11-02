import React, { useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, Loader2, ShieldAlert } from "lucide-react";
import { Layout } from "@/components/Layout";
import { apiClient } from "@/api/client";
import { normaliseError } from "@/api/endpoints";
import { formatIndianCurrency } from "@/lib/format";

/** One row of GET /api/hr/expense-claims/owed/ (money arrives as strings). */
interface OwedRow {
  employee: number;
  employee_name: string;
  claimed: string;
  sanctioned: string;
  reimbursed: string;
  outstanding: string;
}

const COLUMNS = ["claimed", "sanctioned", "reimbursed", "outstanding"] as const;

/** Sum money strings in whole paise, so the total never drifts by float rounding. */
const sumPaise = (values: string[]): string => {
  const paise = values.reduce((acc, v) => acc + Math.round(Number(v) * 100), 0);
  return (paise / 100).toFixed(2);
};

/**
 * Owed to Staff: what the company still owes each employee for expenses they paid
 * (submitted expense claims less their reimbursements). Each row links to that
 * employee's claims. The API returns no totals, so the total row adds up the rows.
 */
export const ExpenseClaimsOwedPage: React.FC = () => {
  const { t } = useTranslation();

  const owed = useQuery({
    queryKey: ["hr", "expense-claims", "owed"],
    queryFn: async () => (await apiClient.get<OwedRow[]>("/hr/expense-claims/owed/")).data,
  });

  const rows = owed.data ?? [];
  const totals = useMemo(
    () =>
      Object.fromEntries(COLUMNS.map((c) => [c, sumPaise(rows.map((r) => r[c]))])) as Record<
        (typeof COLUMNS)[number],
        string
      >,
    [rows]
  );

  return (
    <Layout>
      <div className="space-y-4">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight text-slate-100 sm:text-2xl">
            {t("hrm.exp.owed_title")}
            {owed.isFetching && <Loader2 className="h-4 w-4 animate-spin text-slate-500" />}
          </h1>
          <p className="mt-1 text-sm text-slate-400">{t("hrm.exp.owed_subtitle")}</p>
        </div>

        <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900 shadow-xl">
          {owed.error ? (
            <div className="space-y-2 px-6 py-12 text-center">
              <ShieldAlert className="mx-auto h-8 w-8 text-rose-400" />
              <p className="text-sm text-rose-300">
                {normaliseError(owed.error).forbidden ? t("common.forbidden") : t("common.load_failed")}
              </p>
            </div>
          ) : owed.isLoading ? (
            <div className="flex items-center justify-center gap-2 px-6 py-16 text-sm text-slate-400" role="status">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t("common.loading")}
            </div>
          ) : rows.length === 0 ? (
            <div className="px-6 py-16 text-center text-sm text-slate-500" role="status">
              {t("hrm.exp.owed_empty")}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm text-slate-300">
                <thead className="border-b border-slate-800 bg-slate-950/80 text-xs font-semibold uppercase text-slate-400">
                  <tr>
                    <th className="px-5 py-3.5">{t("hrm.fields.employee")}</th>
                    <th className="px-5 py-3.5 text-right">{t("hrm.exp.col.claimed")}</th>
                    <th className="px-5 py-3.5 text-right">{t("hrm.exp.fields.sanctioned")}</th>
                    <th className="px-5 py-3.5 text-right">{t("hrm.exp.col.reimbursed")}</th>
                    <th className="px-5 py-3.5 text-right">{t("hrm.fields.outstanding")}</th>
                    <th className="px-5 py-3.5">
                      <span className="sr-only">{t("hrm.exp.owed_view")}</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {rows.map((row) => (
                    <tr key={row.employee} className="transition-colors hover:bg-slate-800/40">
                      <td className="px-5 py-3 font-medium text-slate-100">{row.employee_name}</td>
                      <td className="px-5 py-3 text-right tabular-nums">{formatIndianCurrency(row.claimed)}</td>
                      <td className="px-5 py-3 text-right tabular-nums">{formatIndianCurrency(row.sanctioned)}</td>
                      <td className="px-5 py-3 text-right tabular-nums">{formatIndianCurrency(row.reimbursed)}</td>
                      <td className="px-5 py-3 text-right font-semibold tabular-nums text-amber-300">
                        {formatIndianCurrency(row.outstanding)}
                      </td>
                      <td className="px-5 py-3 text-right">
                        <Link
                          to={`/hr/expense-claims?employee=${row.employee}`}
                          className="inline-flex items-center gap-1 text-xs font-medium text-indigo-400 hover:underline"
                        >
                          {t("hrm.exp.owed_view")}
                          <ArrowUpRight className="h-3.5 w-3.5" />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot className="border-t border-slate-700 bg-slate-950/60 font-semibold text-slate-100">
                  <tr>
                    <td className="px-5 py-3">{t("hrm.exp.owed_total")}</td>
                    <td className="px-5 py-3 text-right tabular-nums">{formatIndianCurrency(totals.claimed)}</td>
                    <td className="px-5 py-3 text-right tabular-nums">{formatIndianCurrency(totals.sanctioned)}</td>
                    <td className="px-5 py-3 text-right tabular-nums">{formatIndianCurrency(totals.reimbursed)}</td>
                    <td className="px-5 py-3 text-right tabular-nums">{formatIndianCurrency(totals.outstanding)}</td>
                    <td />
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </div>
      </div>
    </Layout>
  );
};
