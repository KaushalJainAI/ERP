import { MapPin } from "lucide-react";
import i18n from "i18next";
import type { LinkDef, ResourceConfig, SelectOption } from "@/types";
import { stateLink } from "./gst";

/* Values are the backend's contacts.AddressType choices. */
const ADDRESS_TYPES: SelectOption[] = [
  "Billing",
  "Shipping",
  "Office",
  "Personal",
  "Plant",
  "Postal",
  "Shop",
  "Subsidiary",
  "Warehouse",
  "Current",
  "Permanent",
  "Other",
].map((value) => ({ value, labelKey: `lg.opt.${value.toLowerCase()}` }));

const typeCell = (value: any): string =>
  value ? i18n.t(`lg.opt.${String(value).toLowerCase()}`, { defaultValue: String(value) }) : "";

// An address is not company-scoped, so these links list every active party.
const addressCustomerLink: LinkDef = {
  endpoint: "/selling/customers/",
  labelField: "name",
  subLabel: (r) => r.gstin || undefined,
  display: (v) => v.customer_name,
  filters: () => ({ is_active: true }),
  fill: (r) => ({ customer_name: r.name }),
};

const addressSupplierLink: LinkDef = {
  endpoint: "/buying/suppliers/",
  labelField: "name",
  subLabel: (r) => r.gstin || undefined,
  display: (v) => v.supplier_name,
  filters: () => ({ is_active: true }),
  fill: (r) => ({ supplier_name: r.name }),
};

const addresses: ResourceConfig = {
  key: "addresses",
  module: "contacts",
  endpoint: "/contacts/addresses/",
  titleKey: "lg.res.addresses",
  singularKey: "lg.res.address",
  icon: MapPin,
  titleField: "address_title",
  ordering: "address_title",
  columns: [
    { key: "address_title", labelKey: "lg.fields.address_title" },
    { key: "address_type", labelKey: "lg.fields.address_type", render: typeCell },
    { key: "customer_name", labelKey: "fields.customer" },
    { key: "supplier_name", labelKey: "bs.fields.supplier" },
    { key: "address_line1", labelKey: "lg.fields.address_line1" },
    { key: "city", labelKey: "lg.fields.city" },
    { key: "state_name", labelKey: "gst.fields.state" },
    { key: "pincode", labelKey: "lg.fields.pincode" },
    { key: "is_primary_address", labelKey: "lg.fields.is_primary_address", type: "boolean" },
  ],
  fields: [
    { key: "address_title", labelKey: "lg.fields.address_title", type: "text", required: true },
    { key: "address_type", labelKey: "lg.fields.address_type", type: "select", options: ADDRESS_TYPES, defaultValue: "Billing" },
    { key: "customer", labelKey: "fields.customer", type: "link", link: addressCustomerLink, helpKey: "lg.help.one_party" },
    { key: "supplier", labelKey: "bs.fields.supplier", type: "link", link: addressSupplierLink },
    { key: "address_line1", labelKey: "lg.fields.address_line1", type: "text", required: true, span: 2 },
    { key: "address_line2", labelKey: "lg.fields.address_line2", type: "text", span: 2 },
    { key: "city", labelKey: "lg.fields.city", type: "text" },
    { key: "state", labelKey: "gst.fields.state", type: "link", link: stateLink("state_name") },
    { key: "pincode", labelKey: "lg.fields.pincode", type: "text" },
    { key: "country", labelKey: "lg.fields.country", type: "text", defaultValue: "India" },
    { key: "gstin", labelKey: "fields.gstin", type: "text" },
    { key: "phone", labelKey: "fields.phone", type: "text" },
    { key: "email", labelKey: "fields.email", type: "email" },
    { key: "is_primary_address", labelKey: "lg.fields.is_primary_address", type: "boolean", defaultValue: false },
    { key: "is_shipping_address", labelKey: "lg.fields.is_shipping_address", type: "boolean", defaultValue: false },
    { key: "is_active", labelKey: "fields.is_active", type: "boolean", defaultValue: true },
  ],
};

export const contactsResources: ResourceConfig[] = [addresses];
