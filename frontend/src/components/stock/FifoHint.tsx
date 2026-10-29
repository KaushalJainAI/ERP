import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiClient } from "@/api/client";
import { useDebounced } from "@/hooks/useDebounced";

interface FifoPlan {
  allocations: { batch: number; batch_no: string; allocate_qty: string }[];
  shortfall: string;
}

interface Props {
  item: number | string | null | undefined;
  warehouse: number | string | null | undefined;
  qty: string | number | null | undefined;
  postingDate?: string;
}

const trimQty = (v: string) => String(Number(v));

/**
 * Preview of the automatic FIFO pick under an empty batch cell: which lots (oldest
 * manufactured first) the server will take the line's quantity from. Read-only
 * and unreserved; the binding allocation happens when the document is submitted.
 */
export const FifoHint: React.FC<Props> = ({ item, warehouse, qty, postingDate }) => {
  const { t } = useTranslation();
  const q = useDebounced(String(qty ?? ""), 400);
  const ready = !!item && !!warehouse && Number(q) > 0;
  const { data } = useQuery({
    queryKey: ["fifo-preview", item, warehouse, q, postingDate],
    enabled: ready,
    queryFn: async () => {
      const res = await apiClient.get<FifoPlan>("/stock/batches/fifo/", {
        params: { item, warehouse, qty: q, posting_date: postingDate || undefined },
      });
      return res.data;
    },
  });

  if (!ready) return <p className="text-[11px] text-slate-500 mt-1">{t("fifo.hint")}</p>;
  if (!data) return null;
  if (Number(data.shortfall) > 0) {
    return (
      <p className="text-[11px] text-amber-400 mt-1">
        {t("fifo.short", { qty: trimQty(data.shortfall) })}
      </p>
    );
  }
  return (
    <p className="text-[11px] text-slate-400 mt-1">
      {t("fifo.picks")}{" "}
      {data.allocations.map((a) => `${a.batch_no} (${trimQty(a.allocate_qty)})`).join(", ")}
    </p>
  );
};
