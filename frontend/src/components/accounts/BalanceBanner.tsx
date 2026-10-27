import React from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { sumDec } from "@/lib/decimal";
import { formatIndianCurrency } from "@/lib/format";

type Line = Record<string, any>;

/** "-"-prefix toggle on a decimal string (exact; no float). */
export const negDec = (v: string): string => (v.startsWith("-") ? v.slice(1) : `-${v}`);

/** True when a decimal string/value is strictly greater than zero (digit check, no float). */
const isPositive = (v: unknown): boolean => {
  const s = String(v ?? "").trim();
  return !s.startsWith("-") && /[1-9]/.test(s);
};

export const totalsOf = (lines: Line[]) => {
  const debit = sumDec(lines.map((l) => l.debit), 2);
  const credit = sumDec(lines.map((l) => l.credit), 2);
  const diff = sumDec([debit, negDec(credit)], 2);
  return { debit, credit, diff, balanced: /^-?0+(\.0+)?$/.test(diff) };
};

/**
 * Client-side PREVIEW of Debit = Credit for a Journal Entry. The server is the
 * authority and re-checks on submit (accounts.JournalEntry.validate_before_submit).
 */
export const JournalBalanceBanner: React.FC<{ lines: Line[] }> = ({ lines }) => {
  const { t } = useTranslation();
  const tot = totalsOf(lines);
  const mixed = lines.filter((l) => isPositive(l.debit) && isPositive(l.credit)).length;
  const empty = lines.length === 0;

  const ok = !empty && tot.balanced && mixed === 0;
  const tone = ok
    ? "bg-emerald-950/40 border-emerald-800/60 text-emerald-200"
    : "bg-amber-950/40 border-amber-800/60 text-amber-200";
  const Icon = ok ? CheckCircle2 : AlertTriangle;

  return (
    <div role="status" className={`flex gap-2 p-3.5 rounded-lg border text-sm ${tone}`}>
      <Icon className="w-4 h-4 mt-0.5 shrink-0" />
      <div className="space-y-0.5">
        {empty ? (
          <p>{t("acc.balance.no_lines")}</p>
        ) : tot.balanced ? (
          <p className="font-medium">{t("acc.balance.balanced")}</p>
        ) : (
          <p className="font-medium">
            {t("acc.balance.unbalanced", { amount: formatIndianCurrency(tot.diff.replace(/^-/, "")) })}
          </p>
        )}
        {mixed > 0 && <p>{t("acc.balance.mixed_lines", { count: mixed })}</p>}
        <p className="text-xs opacity-70">{t("acc.balance.server_note")}</p>
      </div>
    </div>
  );
};
