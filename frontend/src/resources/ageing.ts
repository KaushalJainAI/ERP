import { Clock } from "lucide-react";
import { todayISO } from "@/lib/format";
import type { SelectOption } from "@/types";
import type { ReportConfig } from "@/types/report";
import { companyLink } from "./selling";

/**
 * Accounts receivable / payable ageing (GET /api/accounts/reports/ageing/).
 * One row per submitted invoice with an outstanding amount, bucketed by days
 * past due. The buckets are nested under `buckets`, addressed with dotted keys.
 */

const PARTY_TYPES: SelectOption[] = [
  { value: "Customer", labelKey: "acc.opt.customer" },
  { value: "Supplier", labelKey: "acc.opt.supplier" },
];

const BUCKET_KEYS = ["0-30", "31-60", "61-90", "90+"] as const;

export const ageingReports: ReportConfig[] = [
  {
    key: "receivable-ageing",
    module: "accounts",
    endpoint: "/accounts/reports/ageing/",
    titleKey: "ageing.receivable_title",
    icon: Clock,
    filters: [
      { key: "party_type", labelKey: "ageing.party_type", type: "select", options: PARTY_TYPES, defaultValue: "Customer" },
      { key: "company", labelKey: "fields.company", type: "link", link: companyLink },
      { key: "as_on", labelKey: "ageing.as_on", type: "date", defaultValue: () => todayISO() },
      { key: "party", labelKey: "ageing.party", type: "text" },
    ],
    columns: [
      { key: "party", labelKey: "ageing.party" },
      { key: "invoice", labelKey: "ageing.invoice" },
      { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
      { key: "due_date", labelKey: "fields.due_date", type: "date" },
      { key: "days_overdue", labelKey: "ageing.days_overdue", type: "number" },
      { key: "rounded_total", labelKey: "fields.grand_total", type: "money" },
      { key: "outstanding", labelKey: "ageing.outstanding", type: "money" },
      ...BUCKET_KEYS.map((b) => ({ key: `buckets.${b}`, labelKey: `ageing.bucket.${b}`, type: "money" as const })),
    ],
    totals: {
      outstanding: "total.outstanding",
      ...Object.fromEntries(BUCKET_KEYS.map((b) => [`buckets.${b}`, `total.buckets.${b}`])),
    },
  },
];
