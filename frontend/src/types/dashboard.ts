/**
 * Response of GET /api/core/dashboard/ (backend/core/views.py).
 * Money values are decimal STRINGS. Every section is `null` when the caller
 * lacks `can_read` on the models it is built from - hide it, never fake it.
 */

export interface DashboardCompany {
  id: number;
  name: string;
  currency: string;
}

export interface DashboardOpenOrders {
  count: number;
  value: string;
}

export type DraftKey =
  | "sales_orders"
  | "sales_invoices"
  | "purchase_orders"
  | "purchase_invoices"
  | "stock_entries"
  | "journal_entries"
  | "payment_entries";

/** Counts per draft document type (only the types the caller may read) + total. */
export type DashboardDrafts = Partial<Record<DraftKey, number>> & { total: number };

/** SystemHealthCheck.status: ok | drift | healed | alert, or "never_run" (no row yet). */
export type IntegrityStatus = "ok" | "drift" | "healed" | "alert" | "never_run" | (string & {});

export interface DashboardIntegrity {
  status: IntegrityStatus;
  check_name: string;
  /** ISO timestamp, null when never run. */
  checked_at: string | null;
  details: {
    message?: string;
    error_count?: number;
    errors?: unknown[];
    [key: string]: unknown;
  };
}

export interface DashboardData {
  /** null when no company exists yet. */
  company: DashboardCompany | null;
  total_sales: string | null;
  total_purchases: string | null;
  receivables_outstanding: string | null;
  payables_outstanding: string | null;
  open_sales_orders: DashboardOpenOrders | null;
  stock_value: string | null;
  draft_documents: DashboardDrafts | null;
  integrity: DashboardIntegrity | null;
}
