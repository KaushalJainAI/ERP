import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { apiClient } from "@/api/client";
import { sumDec } from "@/lib/decimal";
import { formatIndianCurrency } from "@/lib/format";

interface CreditStatus {
  customer: number;
  customer_name: string;
  company: number;
  credit_limit: string;
  bypass: boolean;
  receivable_balance: string;
  unbilled_orders: string;
  exposure: string;
  available: string | null;
  source: string;
}

interface Props {
  /** Customer FK on the document; no request is made until it is set. */
  customerId?: string | number | null;
}

/**
 * Credit exposure for the selected customer, rendered under a Sales Order or
 * Sales Invoice through `renderSummary`.
 *
 * The server is authoritative (the submit gate re-checks), this is a heads-up:
 * it shows the limit, the live exposure and whether the document would cross it.
 * A customer with no limit (0) has nothing to warn about.
 */
export const CreditBanner: React.FC<Props> = ({ customerId }) => {
  const { t } = useTranslation();
  const { data } = useQuery({
    queryKey: ["credit-status", String(customerId ?? "")],
    queryFn: async () =>
      (await apiClient.get(`/selling/customers/${customerId}/credit-status/`)).data as CreditStatus,
    enabled: !!customerId,
    staleTime: 30_000,
    retry: false,
  });

  if (!customerId || !data || Number(data.credit_limit) === 0) return null;

  const overBy = sumDec([data.exposure, `-${data.credit_limit}`], 2);
  const over = !overBy.startsWith("-") && !/^0+(\.0+)?$/.test(overBy);
  const tone = data.bypass
    ? "bg-amber-950/40 border-amber-800/60 text-amber-200"
    : over
      ? "bg-rose-950/50 border-rose-800/60 text-rose-200"
      : "bg-emerald-950/35 border-emerald-800/60 text-emerald-200";
  const Icon = over ? ShieldAlert : ShieldCheck;

  return (
    <div role="status" className={`flex gap-2 p-3.5 rounded-lg border text-sm ${tone}`}>
      <Icon className="w-4 h-4 mt-0.5 shrink-0" />
      <div className="space-y-1 min-w-0">
        <p className="font-semibold">
          {data.bypass
            ? t("credit.bypass")
            : over
              ? t("credit.over", { amount: formatIndianCurrency(overBy) })
              : t("credit.within")}
        </p>
        <p className="text-xs opacity-80">
          {t("credit.limit")}: {formatIndianCurrency(data.credit_limit)} |{" "}
          {t("credit.exposure")}: {formatIndianCurrency(data.exposure)}
          {data.available !== null && ` | ${t("credit.available")}: ${formatIndianCurrency(data.available)}`}
        </p>
      </div>
    </div>
  );
};
