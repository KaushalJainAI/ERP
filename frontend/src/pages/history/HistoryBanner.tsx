import React from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowRight, FileClock } from "lucide-react";
import { apiClient } from "@/api/client";
import { useAuth } from "@/hooks/useAuth";
import { formatIndianNumber } from "@/lib/format";
import { hasReadAccess } from "@/resources/workspaces";
import { BILL_TABS } from "./PastBillsPage";

/** "N older bills from Tally" strip above a document list (Sales Invoice, Payment Entry ...). */
export const HistoryBanner: React.FC<{ tab: string }> = ({ tab }) => {
  const { t } = useTranslation();
  const { user } = useAuth();
  const allowed = hasReadAccess(user?.readable_models, ["legacy.TallyRecord"]);
  const summary = useQuery({
    queryKey: ["past-bills", "summary"],
    queryFn: async () =>
      (await apiClient.get<{ by_type: Record<string, number>; fiscal_years: string[] }>("/legacy/tally-records/summary/"))
        .data,
    enabled: allowed,
    staleTime: 10 * 60 * 1000,
  });
  const types = BILL_TABS.find((b) => b.key === tab)?.types ?? [];
  const count = types.reduce((n, ty) => n + (summary.data?.by_type[ty] ?? 0), 0);
  if (!allowed || !summary.data || count === 0) return null;
  const years = summary.data.fiscal_years;

  return (
    <Link
      to={`/history/bills?type=${tab}`}
      className="group mb-4 flex items-center gap-3 rounded-xl border border-indigo-500/30 bg-indigo-950/60 px-4 py-3 text-sm text-slate-200 transition-colors hover:border-indigo-500/60 animate-fade-up"
    >
      <FileClock className="h-5 w-5 shrink-0 text-indigo-400" />
      <span className="flex-1">
        {t(`pb.banner.${tab}`, {
          n: formatIndianNumber(count),
          from: years[years.length - 1],
          to: years[0],
        })}
      </span>
      <span className="inline-flex items-center gap-1 font-medium text-indigo-300">
        {t("pb.view")}
        <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5 rtl:rotate-180" />
      </span>
    </Link>
  );
};
