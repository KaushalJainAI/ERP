import { Archive, History } from "lucide-react";
import i18n from "i18next";
import type { ResourceConfig } from "@/types";

/* Read-only Tally history (written only by manage.py import_tally). */

const kindCell = (value: any): string =>
  value ? i18n.t(`lg.kind.${value}`, { defaultValue: String(value) }) : "";

const directionCell = (value: any): string => i18n.t(value ? "lg.inward" : "lg.outward");

const erpLinksCell = (value: any): string =>
  Array.isArray(value) ? value.map((l: { name: string }) => l.name).join(", ") : "";

const tallyRecords: ResourceConfig = {
  key: "tally-records",
  module: "history",
  endpoint: "/legacy/tally-records/",
  titleKey: "lg.res.tally_records",
  singularKey: "lg.res.tally_record",
  icon: Archive,
  listOnly: true,
  ordering: "-posting_date,-id",
  columns: [
    { key: "fiscal_year", labelKey: "lg.fields.fiscal_year" },
    { key: "kind", labelKey: "lg.fields.kind", render: kindCell },
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "voucher_type", labelKey: "lg.fields.voucher_type" },
    { key: "voucher_no", labelKey: "lg.fields.voucher_no" },
    { key: "name", labelKey: "lg.fields.name" },
    { key: "party", labelKey: "lg.fields.party" },
    { key: "amount", labelKey: "lg.fields.amount", type: "money" },
    { key: "erp_links", labelKey: "lg.fields.erp_links", render: erpLinksCell },
    { key: "is_cancelled", labelKey: "lg.fields.is_cancelled", type: "boolean" },
  ],
  fields: [],
};

const tallyItemLines: ResourceConfig = {
  key: "tally-item-lines",
  module: "history",
  endpoint: "/legacy/tally-item-lines/",
  titleKey: "lg.res.tally_item_lines",
  singularKey: "lg.res.tally_item_line",
  icon: History,
  listOnly: true,
  ordering: "-posting_date,-id",
  columns: [
    { key: "posting_date", labelKey: "fields.posting_date", type: "date" },
    { key: "voucher_type", labelKey: "lg.fields.voucher_type" },
    { key: "voucher_no", labelKey: "lg.fields.voucher_no" },
    { key: "party", labelKey: "lg.fields.party" },
    { key: "item_name", labelKey: "lg.fields.item_name" },
    { key: "is_inward", labelKey: "lg.fields.direction", render: directionCell },
    { key: "qty", labelKey: "fields.qty", type: "number" },
    { key: "uom", labelKey: "lg.fields.uom" },
    { key: "rate", labelKey: "fields.rate", type: "number" },
    { key: "amount", labelKey: "lg.fields.amount", type: "money" },
    { key: "ledger", labelKey: "lg.fields.ledger" },
  ],
  fields: [],
};

export const legacyResources: ResourceConfig[] = [tallyItemLines, tallyRecords];
