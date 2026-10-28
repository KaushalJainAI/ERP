import type { LinkDef } from "@/types";

/**
 * Manufacturing links shared by the manufacturing module and the Stock Entry
 * form (which also creates manufacturing vouchers). Kept dependency-free so
 * neither resource file has to import the other.
 */

export const bomLink: LinkDef = {
  endpoint: "/manufacturing/boms/",
  label: (r) => `${r.name} - ${r.item_code ?? ""} ${r.item_name ?? ""}`.trim(),
  subLabel: (r) => r.company_name,
  filters: (_row, form) => ({ company: form.company, is_active: 1 }),
};

export const workOrderLink: LinkDef = {
  endpoint: "/manufacturing/work-orders/",
  label: (r) => `${r.name} - ${r.production_item_code ?? ""}`.trim(),
  subLabel: (r) => [r.status, r.bom_name].filter(Boolean).join(" - ") || undefined,
  // Only submitted Work Orders can produce stock entries.
  filters: (_row, form) => ({ company: form.company, docstatus: 1 }),
};
