/** Shapes of the print-data payloads (backend/printing/builders.py). Money and quantities are strings. */
export interface StateRef {
  code: string;
  name: string;
}

export interface CompanyBlock {
  id: number;
  name: string;
  legal_name: string;
  gstin: string;
  pan: string;
  cin: string;
  state: StateRef | null;
  address_line1: string;
  address_line2: string;
  city: string;
  pincode: string;
  country: string;
  phone: string;
  email: string;
  website: string;
  fssai_license_no?: string;
  terms?: string;
  authorised_signatory?: string;
  logo_url?: string;
  bank?: {
    bank_name?: string;
    account_holder?: string;
    account_no?: string;
    ifsc?: string;
    branch?: string;
    upi_id?: string;
  };
}

export interface PartyBlock {
  party_type: string;
  name: string;
  gstin?: string;
  gst_category?: string;
  phone?: string;
  email?: string;
  state?: StateRef | null;
  same_as_billing?: boolean;
  address?: {
    title: string;
    line1: string;
    line2: string;
    city: string;
    pincode: string;
    country: string;
    state: StateRef | null;
    gstin: string;
    phone: string;
  } | null;
}

export interface PrintLine {
  idx: number;
  item_code: string;
  item_name: string;
  description: string;
  hsn: string;
  batch_no: string;
  expiry_date: string | null;
  qty: string;
  uom: string;
  rate?: string;
  price_list_rate?: string | null;
  discount_amount?: string | null;
  discount_percent?: string | null;
  amount?: string;
  gst_rate?: string;
  cgst_amount?: string;
  sgst_amount?: string;
  igst_amount?: string;
}

export interface HsnRow {
  hsn: string;
  gst_rate: string;
  qty: string;
  uom: string;
  taxable_value: string;
  cgst: string;
  sgst: string;
  igst: string;
  total_tax: string;
}

export interface TaxRow {
  description: string;
  component: string;
  rate: string;
  net_amount: string;
  tax_amount: string;
  add_deduct: string;
}

export interface Totals {
  total_qty: string;
  net_total: string;
  total_taxes_and_charges: string;
  grand_total: string;
  rounding_adjustment: string;
  rounded_total: string;
  in_words: string;
  words_lang: string;
}

export interface Meta {
  kind: "sales_invoice" | "purchase_invoice" | "purchase_order" | "delivery_challan" | "payment_entry";
  title_key: string;
  title: string;
  name: string;
  date: string | null;
  docstatus: number;
  is_draft: boolean;
  is_cancelled: boolean;
  copy: { key: string; options: string[] };
}

export interface DocPrint extends Meta {
  company: CompanyBlock;
  bill_to: PartyBlock;
  ship_to: PartyBlock;
  place_of_supply: StateRef | null;
  due_date?: string | null;
  required_by?: string | null;
  delivery_date?: string | null;
  is_return?: boolean;
  return_against?: { name: string; date: string | null } | null;
  supplier_invoice?: { no: string; date: string | null };
  source?: { type: string; name: string; date: string | null };
  with_prices?: boolean;
  gst?: { company_gstin: string; party_gstin: string; is_export_with_gst: boolean; is_reverse_charge: boolean };
  lines: PrintLine[];
  hsn_summary?: { mode: "cgst_sgst" | "igst" | "none"; rows: HsnRow[]; totals: Record<string, string> };
  taxes?: TaxRow[];
  totals?: Totals;
  total_qty?: string;
  balances?: {
    outstanding_amount: string | null;
    status: string | null;
    previous_balance: string | null;
    current_balance: string | null;
  };
}

export interface PaymentPrint extends Meta {
  company: CompanyBlock;
  payment_type: string;
  party: PartyBlock | null;
  payment: {
    mode: "cash" | "bank" | "other";
    account: string;
    reference_no: string;
    reference_date: string | null;
    paid_from: string;
    paid_to: string;
  };
  allocations: {
    reference_doctype: string;
    reference_name: string;
    invoice_date: string | null;
    invoice_total: string | null;
    allocated_amount: string;
    outstanding_after: string | null;
  }[];
  totals: { amount: string; allocated: string; unallocated: string; in_words: string; words_lang: string };
}

export type PrintData = DocPrint | PaymentPrint;
