import React from "react";
import { useTranslation } from "react-i18next";

/** Server `display_status` values that carry a payment meaning (Draft / Cancelled are covered by the docstatus badge). */
const STYLES: Record<string, { key: string; cls: string }> = {
  Overdue: { key: "ageing.status.overdue", cls: "bg-rose-950/60 text-rose-300 border-rose-800/60" },
  Unpaid: { key: "ageing.status.unpaid", cls: "bg-amber-950/40 text-amber-300 border-amber-800/50" },
  "Partly Paid": { key: "ageing.status.partly_paid", cls: "bg-sky-950/60 text-sky-300 border-sky-800/60" },
  Paid: { key: "ageing.status.paid", cls: "bg-emerald-950/60 text-emerald-300 border-emerald-800/60" },
  // Returns (credit / debit notes) and the invoices they settle.
  Return: { key: "ret.status.return", cls: "bg-fuchsia-950/50 text-fuchsia-300 border-fuchsia-800/50" },
  "Credit Note Issued": { key: "ret.status.credit_note_issued", cls: "bg-fuchsia-950/50 text-fuchsia-300 border-fuchsia-800/50" },
  "Debit Note Issued": { key: "ret.status.debit_note_issued", cls: "bg-fuchsia-950/50 text-fuchsia-300 border-fuchsia-800/50" },
};

/** Payment status of a Sales / Purchase Invoice (from the API's `display_status`, falling back to `status`). */
export const PaymentStatusBadge: React.FC<{ status: string | null | undefined }> = ({ status }) => {
  const { t } = useTranslation();
  const style = status ? STYLES[status] : undefined;
  if (!style) return null;
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border whitespace-nowrap ${style.cls}`}>
      {t(style.key)}
    </span>
  );
};
