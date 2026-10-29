import { BookMarked, Boxes, FileOutput } from "lucide-react";
import type { LinkDef, NavLink, ResourceConfig, SelectOption } from "@/types";
import { companyField, itemLink } from "./selling";
import { leafAccountLink } from "./accounts";

/* Maps between ERP objects and their names in Tally Prime (Accounts Manager only). */

const PARTY_TYPES: SelectOption[] = [
  { value: "Customer", labelKey: "fields.customer" },
  { value: "Supplier", labelKey: "bs.fields.supplier" },
];

const partyTypeCell = (value: any): string => String(value ?? "");

const mapItemLink: LinkDef = { ...itemLink, fill: undefined };

const tallyLedgerMaps: ResourceConfig = {
  key: "tally-ledgers",
  module: "accounts",
  endpoint: "/tally-export/ledger-maps/",
  titleKey: "tx.res.ledger_maps",
  singularKey: "tx.res.ledger_map",
  icon: BookMarked,
  titleField: "tally_ledger",
  ordering: "account__name,party",
  noAttachments: true,
  columns: [
    { key: "account_name", labelKey: "tx.fields.account" },
    { key: "party_type", labelKey: "tx.fields.party_type", render: partyTypeCell },
    { key: "party", labelKey: "tx.fields.party" },
    { key: "tally_ledger", labelKey: "tx.fields.tally_ledger" },
    { key: "tally_group", labelKey: "tx.fields.tally_group" },
    { key: "source", labelKey: "tx.fields.source" },
  ],
  fields: [
    { ...companyField, clears: ["account"] },
    {
      key: "account",
      labelKey: "tx.fields.account",
      type: "link",
      required: true,
      link: leafAccountLink,
    },
    { key: "party_type", labelKey: "tx.fields.party_type", type: "select", options: PARTY_TYPES, helpKey: "tx.help.party" },
    { key: "party", labelKey: "tx.fields.party", type: "text" },
    { key: "tally_ledger", labelKey: "tx.fields.tally_ledger", type: "text", required: true, span: 2 },
    { key: "tally_group", labelKey: "tx.fields.tally_group", type: "text", helpKey: "tx.help.tally_group" },
  ],
};

const tallyItemMaps: ResourceConfig = {
  key: "tally-items",
  module: "accounts",
  endpoint: "/tally-export/item-maps/",
  titleKey: "tx.res.item_maps",
  singularKey: "tx.res.item_map",
  icon: Boxes,
  titleField: "tally_item",
  ordering: "item__code",
  noAttachments: true,
  columns: [
    { key: "item_code", labelKey: "tx.fields.item" },
    { key: "item_name", labelKey: "lg.fields.item_name" },
    { key: "tally_item", labelKey: "tx.fields.tally_item" },
    { key: "tally_unit", labelKey: "tx.fields.tally_unit" },
    { key: "source", labelKey: "tx.fields.source" },
  ],
  fields: [
    companyField,
    { key: "item", labelKey: "tx.fields.item", type: "link", required: true, link: mapItemLink },
    { key: "tally_item", labelKey: "tx.fields.tally_item", type: "text", required: true, span: 2 },
    { key: "tally_unit", labelKey: "tx.fields.tally_unit", type: "text" },
  ],
};

export const tallyExportResources: ResourceConfig[] = [tallyLedgerMaps, tallyItemMaps];

/** Sidebar link to the hand-written export page (under Accounts). */
export const tallyExportLinks: NavLink[] = [{ path: "/accounts/tally-export", labelKey: "tx.nav", icon: FileOutput }];
