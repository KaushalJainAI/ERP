/**
 * Which print formats each document resource offers, and where the print data lives.
 * `kind` is the URL slug of the print page (/print/<kind>/<id>); the backend endpoints are
 * in backend/printing/urls.py (GET /api/print/...).
 */
export type PrintKind =
  | "sales-invoice"
  | "challan-invoice"
  | "challan-order"
  | "purchase-order"
  | "purchase-invoice"
  | "payment-entry";

export const PRINT_ENDPOINT: Record<PrintKind, (id: string) => string> = {
  "sales-invoice": (id) => `/print/sales-invoice/${id}/`,
  "challan-invoice": (id) => `/print/delivery-challan/sales-invoice/${id}/`,
  "challan-order": (id) => `/print/delivery-challan/sales-order/${id}/`,
  "purchase-order": (id) => `/print/purchase-order/${id}/`,
  "purchase-invoice": (id) => `/print/purchase-invoice/${id}/`,
  "payment-entry": (id) => `/print/payment-entry/${id}/`,
};

/** `<module>/<resource key>` -> the formats offered on that document's page. */
export const PRINT_TARGETS: Record<string, PrintKind[]> = {
  "selling/sales-invoices": ["sales-invoice", "challan-invoice"],
  "selling/sales-orders": ["challan-order"],
  "buying/purchase-orders": ["purchase-order"],
  "buying/purchase-invoices": ["purchase-invoice"],
  "accounts/payment-entries": ["payment-entry"],
};

export const isPrintKind = (v: string | undefined): v is PrintKind => !!v && v in PRINT_ENDPOINT;
export const isChallan = (k: PrintKind) => k === "challan-invoice" || k === "challan-order";
export const printPath = (kind: PrintKind, id: string | number) => `/print/${kind}/${id}`;
