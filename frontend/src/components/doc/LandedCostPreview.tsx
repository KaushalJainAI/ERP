import React, { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Info } from "lucide-react";
import { allocateCharges, landedRate } from "@/lib/landedCost";
import { formatIndianCurrency } from "@/lib/format";
import { isDecimal, mulDec } from "@/lib/decimal";

interface Props {
  /** The form values with the child tables merged in by key (items, charges). */
  values: Record<string, any>;
}

/**
 * Live "landed cost per line" for a purchase bill with extra charges: what each
 * stock line really costs once freight / hamali are spread over it. A display-only
 * mirror of the server's `buying.services.charges` (the server books the real
 * numbers on submit).
 */
export const LandedCostPreview: React.FC<Props> = ({ values }) => {
  const { t } = useTranslation();
  const charges: Record<string, any>[] = (values.charges as Record<string, any>[]) ?? [];
  const items: Record<string, any>[] = (values.items as Record<string, any>[]) ?? [];

  const usable = charges.filter((c) => isDecimal(c.amount) && /[1-9]/.test(String(c.amount)));
  const stockLines = useMemo(
    () =>
      items
        .map((r, i) => ({ r, i }))
        .filter(({ r }) => r.item && r.is_stock_item !== false && isDecimal(r.qty) && isDecimal(r.rate)),
    [items]
  );

  const rows = useMemo(() => {
    const lines = stockLines.map(({ r, i }) => ({
      key: String(i),
      amount: mulDec(r.qty, r.rate, 2) ?? "0.00",
      qty: String(r.qty),
    }));
    const shares = allocateCharges(
      usable.map((c) => ({ amount: String(c.amount), distribute_by: c.distribute_by || "Amount" })),
      lines
    );
    return stockLines.map(({ r }, k) => {
      const line = lines[k];
      const share = shares[line.key] ?? "0.00";
      return {
        key: line.key,
        name: r.item_code ? `${r.item_code}${r.item_name ? " - " + r.item_name : ""}` : String(r.item),
        qty: line.qty,
        billRate: r.rate as string,
        share,
        landed: landedRate(line.amount, share, line.qty),
      };
    });
  }, [stockLines, usable]);

  if (usable.length === 0) return null;
  if (values.update_stock === false || rows.length === 0) {
    return (
      <div className="flex items-start gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
        <Info className="w-4 h-4 mt-0.5 shrink-0" />
        <span>{t("chg.need_stock")}</span>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white overflow-x-auto">
      <div className="px-3 py-2 text-xs font-semibold text-slate-600 border-b border-slate-100">
        {t("chg.preview_title")}
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-slate-500">
            <th className="text-left font-medium px-3 py-1.5">{t("fields.item")}</th>
            <th className="text-right font-medium px-3 py-1.5">{t("fields.qty")}</th>
            <th className="text-right font-medium px-3 py-1.5">{t("chg.bill_rate")}</th>
            <th className="text-right font-medium px-3 py-1.5">{t("chg.share")}</th>
            <th className="text-right font-medium px-3 py-1.5">{t("chg.landed_rate")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-slate-100">
              <td className="px-3 py-1.5">{r.name}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{r.qty}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{r.billRate}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{formatIndianCurrency(r.share)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums font-semibold">{r.landed ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
