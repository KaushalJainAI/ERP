import type { ComponentType } from "react";
import {
  BarChart3,
  BookOpen,
  Boxes,
  Building2,
  ClipboardList,
  Factory,
  FileText,
  HardDrive,
  Layers,
  Package,
  Receipt,
  ShieldCheck,
  ShoppingBag,
  ShoppingCart,
  UserRound,
  Users,
  Wallet,
  Warehouse,
} from "lucide-react";
import type { DraftKey } from "@/types/dashboard";

/**
 * Home-page workspaces. Visibility is derived from the caller's readable model
 * labels (`UserProfile.readable_models`), never from role names, so custom
 * roles work without a second mapping. Each link declares the labels its page
 * reads; a workspace is shown only when at least one of its links is readable.
 */
export interface WorkspaceLink {
  path: string;
  /** i18n key */
  labelKey: string;
  /** Model labels whose `can_read` makes this link accessible (any-of). */
  access: string[];
  icon: ComponentType<{ className?: string }>;
}

export interface WorkspaceDef {
  /** Module key; the card's "open workspace" link goes to `/<key>`. */
  key: string;
  /** i18n key */
  titleKey: string;
  /** i18n key */
  descriptionKey: string;
  icon: ComponentType<{ className?: string }>;
  /** Tailwind classes for the icon tile. */
  color: string;
  links: WorkspaceLink[];
}

export const WORKSPACES: WorkspaceDef[] = [
  {
    key: "selling",
    titleKey: "selling",
    descriptionKey: "home.workspace.selling_desc",
    icon: ShoppingCart,
    color: "bg-sky-950/60 text-sky-300 border-sky-800/50",
    links: [
      { path: "/selling/sales-orders", labelKey: "res.sales_orders", access: ["selling.SalesOrder"], icon: FileText },
      { path: "/selling/sales-invoices", labelKey: "res.sales_invoices", access: ["selling.SalesInvoice"], icon: Receipt },
      { path: "/selling/customers", labelKey: "res.customers", access: ["selling.Customer"], icon: Users },
    ],
  },
  {
    key: "buying",
    titleKey: "buying",
    descriptionKey: "home.workspace.buying_desc",
    icon: ShoppingBag,
    color: "bg-amber-950/60 text-amber-300 border-amber-800/50",
    links: [
      { path: "/buying/purchase-orders", labelKey: "bs.res.purchase_orders", access: ["buying.PurchaseOrder"], icon: FileText },
      { path: "/buying/purchase-invoices", labelKey: "bs.res.purchase_invoices", access: ["buying.PurchaseInvoice"], icon: Receipt },
      { path: "/buying/suppliers", labelKey: "bs.res.suppliers", access: ["buying.Supplier"], icon: Users },
    ],
  },
  {
    key: "stock",
    titleKey: "stock",
    descriptionKey: "home.workspace.stock_desc",
    icon: Boxes,
    color: "bg-emerald-950/60 text-emerald-300 border-emerald-800/50",
    links: [
      { path: "/stock/stock-entries", labelKey: "bs.res.stock_entries", access: ["stock.StockEntry"], icon: Package },
      { path: "/stock/bins", labelKey: "bs.res.bins", access: ["stock.Bin"], icon: Warehouse },
      { path: "/stock/items", labelKey: "bs.res.items", access: ["stock.Item"], icon: Layers },
    ],
  },
  {
    key: "manufacturing",
    titleKey: "manufacturing",
    descriptionKey: "home.workspace.manufacturing_desc",
    icon: Factory,
    color: "bg-violet-950/60 text-violet-300 border-violet-800/50",
    links: [
      { path: "/manufacturing/boms", labelKey: "mf.res.boms", access: ["manufacturing.BOM"], icon: ClipboardList },
      { path: "/manufacturing/work-orders", labelKey: "mf.res.work_orders", access: ["manufacturing.WorkOrder"], icon: Factory },
      { path: "/manufacturing/production-yield", labelKey: "mf.res.production_yield", access: ["stock.Bin"], icon: BarChart3 },
    ],
  },
  {
    key: "accounts",
    titleKey: "accounts",
    descriptionKey: "home.workspace.accounts_desc",
    icon: BookOpen,
    color: "bg-indigo-950/60 text-indigo-300 border-indigo-800/50",
    links: [
      { path: "/accounts/journal-entries", labelKey: "acc.res.journal_entries", access: ["accounts.JournalEntry"], icon: FileText },
      { path: "/accounts/payment-entries", labelKey: "acc.res.payment_entries", access: ["accounts.PaymentEntry"], icon: Wallet },
      { path: "/accounts/reports/trial-balance", labelKey: "acc.nav.trial_balance", access: ["accounts.GLEntry"], icon: BarChart3 },
      { path: "/accounts/accounts", labelKey: "acc.res.accounts", access: ["accounts.Account"], icon: BookOpen },
    ],
  },
  {
    key: "hr",
    titleKey: "hrm.title",
    descriptionKey: "home.workspace.hr_desc",
    icon: UserRound,
    color: "bg-rose-950/60 text-rose-300 border-rose-800/50",
    links: [
      { path: "/hr/employees", labelKey: "hrm.res.employees", access: ["hr.Employee"], icon: Users },
      { path: "/hr/attendance", labelKey: "hrm.res.attendance", access: ["hr.Attendance"], icon: ClipboardList },
      { path: "/hr/salary-slips", labelKey: "hrm.res.salary_slips", access: ["hr.SalarySlip"], icon: Wallet },
    ],
  },
  {
    key: "organisation",
    titleKey: "organisation.title",
    descriptionKey: "org.workspace_desc",
    icon: Building2,
    color: "bg-teal-950/60 text-teal-300 border-teal-800/50",
    links: [
      { path: "/organisation/company", labelKey: "organisation.company", access: ["core.Company"], icon: Building2 },
      { path: "/organisation/drive", labelKey: "organisation.drive", access: ["core.Company"], icon: HardDrive },
    ],
  },
  {
    key: "users",
    titleKey: "home.admin_title",
    descriptionKey: "home.workspace.admin_desc",
    icon: ShieldCheck,
    color: "bg-slate-800 text-slate-200 border-slate-700",
    links: [
      { path: "/users/users", labelKey: "usermgmt.nav", access: ["users.User"], icon: Users },
      { path: "/users/roles", labelKey: "roles.nav", access: ["users.Role"], icon: ShieldCheck },
    ],
  },
];

/** A draft document type, the workspace it belongs to, and where its list lives. */
export interface PendingWorkDef {
  key: DraftKey;
  workspace: string;
  path: string;
  labelKey: string;
  icon: ComponentType<{ className?: string }>;
  /** Model label whose `can_read` makes this pending row visible. */
  access: string[];
}

export const PENDING_WORK: PendingWorkDef[] = [
  { key: "sales_orders", workspace: "selling", path: "/selling/sales-orders?docstatus=0", labelKey: "res.sales_orders", icon: ShoppingCart, access: ["selling.SalesOrder"] },
  { key: "sales_invoices", workspace: "selling", path: "/selling/sales-invoices?docstatus=0", labelKey: "res.sales_invoices", icon: Receipt, access: ["selling.SalesInvoice"] },
  { key: "purchase_orders", workspace: "buying", path: "/buying/purchase-orders?docstatus=0", labelKey: "bs.res.purchase_orders", icon: ShoppingBag, access: ["buying.PurchaseOrder"] },
  { key: "purchase_invoices", workspace: "buying", path: "/buying/purchase-invoices?docstatus=0", labelKey: "bs.res.purchase_invoices", icon: Receipt, access: ["buying.PurchaseInvoice"] },
  { key: "stock_entries", workspace: "stock", path: "/stock/stock-entries?docstatus=0", labelKey: "bs.res.stock_entries", icon: Package, access: ["stock.StockEntry"] },
  { key: "journal_entries", workspace: "accounts", path: "/accounts/journal-entries?docstatus=0", labelKey: "acc.res.journal_entries", icon: FileText, access: ["accounts.JournalEntry"] },
  { key: "payment_entries", workspace: "accounts", path: "/accounts/payment-entries?docstatus=0", labelKey: "acc.res.payment_entries", icon: Wallet, access: ["accounts.PaymentEntry"] },
];

/**
 * True when the caller may read at least one of `labels`.
 *
 * `readable_models` is always sent by `/auth/me/`; the `undefined` fallback
 * keeps the page usable against an older backend (the API still enforces).
 */
export function hasReadAccess(readableModels: string[] | undefined, labels: string[]): boolean {
  if (!Array.isArray(readableModels)) return true;
  if (readableModels.includes("*")) return true;
  return labels.some((label) => readableModels.includes(label));
}

/**
 * Workspaces with their readable links, dropping workspaces that would be
 * empty. Derived from the links (not a separate label list) so a role that can
 * read only master data -- customers, items, the chart of accounts -- still
 * gets its workspace.
 */
export function visibleWorkspaces(readableModels: string[] | undefined): WorkspaceDef[] {
  return WORKSPACES.map((workspace) => ({
    ...workspace,
    links: workspace.links.filter((link) => hasReadAccess(readableModels, link.access)),
  })).filter((workspace) => workspace.links.length > 0);
}
