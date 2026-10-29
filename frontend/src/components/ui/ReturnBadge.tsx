import React from "react";
import { useTranslation } from "react-i18next";

/** "Credit Note" (sales) / "Debit Note" (purchase) marker for a return document. */
export const ReturnBadge: React.FC<{ kind: "credit" | "debit" }> = ({ kind }) => {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border whitespace-nowrap bg-fuchsia-950/50 text-fuchsia-300 border-fuchsia-800/50">
      {t(kind === "credit" ? "ret.credit_note" : "ret.debit_note")}
    </span>
  );
};
