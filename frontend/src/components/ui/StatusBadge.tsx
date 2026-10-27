import React from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, Clock, XCircle } from "lucide-react";
import { DocStatus } from "@/types";

/** Draft / Submitted / Cancelled badge for core.DocStatus. */
export const StatusBadge: React.FC<{ docstatus: number | undefined | null }> = ({ docstatus }) => {
  const { t } = useTranslation();
  if (docstatus === DocStatus.SUBMITTED) {
    return (
      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border bg-emerald-950/60 text-emerald-300 border-emerald-800/60">
        <CheckCircle2 className="w-3 h-3" /> {t("submitted")}
      </span>
    );
  }
  if (docstatus === DocStatus.CANCELLED) {
    return (
      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border bg-rose-950/60 text-rose-300 border-rose-800/60">
        <XCircle className="w-3 h-3" /> {t("cancelled")}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border bg-amber-950/40 text-amber-300 border-amber-800/50">
      <Clock className="w-3 h-3" /> {t("draft")}
    </span>
  );
};
