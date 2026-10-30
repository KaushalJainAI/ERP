import { createElement } from "react";
import { PaymentStatusBadge } from "@/components/ui/PaymentStatusBadge";
import { formatIndianCurrency } from "@/lib/format";
import { DocStatus } from "@/types";
import type { FieldDef, ListColumnDef } from "@/types";

/**
 * Payment status / outstanding of Sales and Purchase Invoices. The server derives
 * `outstanding_amount`, `status`, `is_overdue` and `display_status` (read-only); a draft
 * or cancelled invoice has nothing outstanding, so these show only for submitted ones.
 */

const isSubmitted = (row: Record<string, any>) => row.docstatus === DocStatus.SUBMITTED;

/** List columns: payment status badge + outstanding amount (placed after the docstatus column). */
export const invoiceStatusColumns: ListColumnDef[] = [
  {
    key: "outstanding_amount",
    labelKey: "ageing.outstanding",
    type: "money",
    render: (v, row) => (isSubmitted(row) ? createElement("span", { className: "tabular-nums" }, formatIndianCurrency(v)) : ""),
  },
  {
    key: "display_status",
    labelKey: "ageing.payment_status",
    render: (v, row) => (isSubmitted(row) ? createElement(PaymentStatusBadge, { status: row.payment_status ?? v ?? row.status }) : ""),
  },
];

/** Read-only fields for the totals block of the invoice form (server values; never sent on save). */
export const invoiceStatusFields: FieldDef[] = [
  {
    key: "outstanding_amount",
    labelKey: "ageing.outstanding",
    type: "decimal",
    money: true,
    readOnly: true,
    preview: (v) => (v.outstanding_amount === "" || v.outstanding_amount == null ? null : String(v.outstanding_amount)),
    hidden: (v, isNew) => isNew || !isSubmitted(v),
  },
  {
    key: "display_status",
    labelKey: "ageing.payment_status",
    type: "text",
    readOnly: true,
    preview: (v) => v.display_status || v.status || null,
    renderValue: (value) => createElement(PaymentStatusBadge, { status: value }),
    hidden: (v, isNew) => isNew || !isSubmitted(v),
  },
];
