import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import { listAllRecords } from "@/api/endpoints";
import { useCompany } from "@/context/CompanyContext";
import { todayISO } from "@/lib/format";
import type { Paginated } from "@/types";
import type { DashboardData } from "@/types/dashboard";
import type { RevenueAndVolume } from "@/types/analytics";
import { useCanRead } from "../workspaces/WorkspaceShell";
import { useOpenQuickNotes } from "@/components/assistant/quickNotes";

export interface SetupItem {
  code: string;
  message: string;
  fix_path: string;
}

export interface StageRow {
  item_name: string;
  ready_qty: string;
  is_critical: boolean;
}

export const AGEING_BUCKETS = ["0-30", "31-60", "61-90", "90+"] as const;
export type AgeingBucket = (typeof AGEING_BUCKETS)[number];

interface AgeingReport {
  total: { outstanding: string; buckets: Record<AgeingBucket, string> };
}

/** The six calendar months ending this one, oldest first, as "YYYY-MM". */
export function lastSixMonths(today: string): string[] {
  const [year, month] = today.split("-").map(Number);
  return Array.from({ length: 6 }, (_, i) => {
    const date = new Date(Date.UTC(year, month - 1 - (5 - i), 1));
    return date.toISOString().slice(0, 7);
  });
}

/** Which home figures to fetch for this role, so a screen only asks for what it shows. */
export interface HomeNeeds {
  dashboard: boolean;
  todayOrders: boolean;
  dispatch: boolean;
  batches: boolean;
  stages: boolean;
  collections: boolean;
  bills: boolean;
  expenses: boolean;
  gst: boolean;
  sales: boolean;
  ageing: boolean;
  cash: boolean;
  setup: boolean;
  names: boolean;
  notes: boolean;
}

/** Every number on the home page. Each stays a query, so a failure shows as a dash, never a zero. */
export function useHomeData(needs: HomeNeeds) {
  const { activeCompany } = useCompany();
  const companyId = activeCompany?.id ?? null;
  const companyName = activeCompany?.name;
  const today = todayISO();
  const month = today.slice(0, 7);
  const months = lastSixMonths(today);

  const canOrders = useCanRead(["selling.SalesOrder"]);
  const canBatches = useCanRead(["manufacturing.WorkOrder"]);
  const canStock = useCanRead(["stock.Bin"]);
  const canGl = useCanRead(["accounts.GLEntry"]);
  const canExpenses = useCanRead(["buying.PurchaseInvoice", "accounts.GLEntry"]);
  const canSales = useCanRead(["selling.SalesOrder", "selling.SalesInvoice", "accounts.GLEntry"]);
  const canDashboard = canOrders || canStock || canGl || canSales;

  const options = { staleTime: 30_000, retry: false };
  const on = (need: boolean, can: boolean) => !!activeCompany && need && can;

  const dashboard = useQuery({
    ...options,
    queryKey: ["home", "pending-work", companyId],
    queryFn: async () => (await apiClient.get<DashboardData>("/core/dashboard/")).data,
    enabled: on(needs.dashboard, canDashboard),
  });

  const todayOrders = useQuery({
    ...options,
    queryKey: ["home", "orders", "today", companyId, today],
    queryFn: async () =>
      (
        await apiClient.get<Paginated<unknown>>("/selling/sales-orders/", {
          params: { transaction_date: today },
        })
      ).data.count,
    enabled: on(needs.todayOrders, canOrders),
  });

  const dispatch = useQuery({
    ...options,
    queryKey: ["home", "factory", "pending-dispatch", companyId],
    queryFn: async () => (await apiClient.get<unknown[]>("/selling/sales-orders/pending/")).data.length,
    enabled: on(needs.dispatch, canOrders),
  });

  const batches = useQuery({
    ...options,
    queryKey: ["home", "factory", "batches-in-progress", companyId],
    queryFn: async () => {
      const rows = (await listAllRecords("/manufacturing/batches/")) as { status: string }[];
      return rows.filter((row) => row.status === "Grinding" || row.status === "Packing").length;
    },
    enabled: on(needs.batches, canBatches),
  });

  const stages = useQuery({
    ...options,
    queryKey: ["home", "factory", "stages", companyId],
    queryFn: async () =>
      (
        await apiClient.get<{ rows: StageRow[] }>("/stock/reports/stages/", {
          params: { company: companyId },
        })
      ).data.rows,
    enabled: on(needs.stages, canStock),
  });

  const collections = useQuery({
    ...options,
    queryKey: ["home", "accounts", "collections", companyName ?? null, today],
    queryFn: async () =>
      (
        await apiClient.get<{ totals: { total_receivables: string; total_overdue: string } }>(
          "/accounts/collections/",
          { params: { company: companyName, as_on: today } }
        )
      ).data.totals,
    enabled: on(needs.collections, canGl),
  });

  const bills = useQuery({
    ...options,
    queryKey: ["home", "accounts", "bills-to-pay", companyName ?? null, today],
    queryFn: async () =>
      (
        await apiClient.get<{ totals: { total_payables: string; total_overdue: string } }>(
          "/accounts/bills-to-pay/",
          { params: { company: companyName, as_on: today } }
        )
      ).data.totals,
    enabled: on(needs.bills, canGl),
  });

  const expenses = useQuery({
    ...options,
    queryKey: ["home", "accounts", "expenses", companyId, month],
    queryFn: async () =>
      (
        await apiClient.get<{ grand_totals: { total: string; paid: string; due: string } }>(
          "/accounts/reports/expense-summary/",
          { params: { from_date: `${month}-01`, to_date: today } }
        )
      ).data.grand_totals,
    enabled: on(needs.expenses, canExpenses),
  });

  const gst = useQuery({
    ...options,
    queryKey: ["home", "accounts", "gst-position", companyName ?? null, month],
    queryFn: async () =>
      (
        await apiClient.get<{ net_payable: string; status: "Paid" | "Due"; due_date: string }>(
          "/accounts/reports/gst-position/",
          { params: { month } }
        )
      ).data,
    enabled: on(needs.gst, canGl),
  });

  // The API omits months with no bills; the chart needs all six.
  const sales = useQuery({
    ...options,
    queryKey: ["home", "sales", "months", companyId, today],
    queryFn: async () => {
      const res = await apiClient.get<RevenueAndVolume>("/selling/analytics/revenue/", {
        params: { from_date: `${months[0]}-01`, to_date: today },
      });
      const byPeriod = new Map(res.data.monthly_trends.map((row) => [row.period, row.net_revenue]));
      return months.map((period) => ({ period, amount: Number(byPeriod.get(period) ?? 0) }));
    },
    enabled: on(needs.sales, canSales),
  });

  // The accounts ageing, not the analytics one: it includes opening bills, so it matches Collections.
  const ageing = useQuery({
    ...options,
    queryKey: ["home", "accounts", "ageing", companyId, today],
    queryFn: async () =>
      (
        await apiClient.get<AgeingReport>("/accounts/reports/ageing/", {
          params: { party_type: "Customer", as_on: today },
        })
      ).data.total,
    enabled: on(needs.ageing, canGl),
  });

  const cash = useQuery({
    ...options,
    queryKey: ["home", "accounts", "cash-in-hand", companyId],
    queryFn: async () => (await apiClient.get<{ amount: string }>("/accounts/cash-in-hand/")).data,
    enabled: on(needs.cash, canGl),
  });

  const setup = useQuery({
    ...options,
    queryKey: ["home", "setup-health", companyId],
    queryFn: async () => (await apiClient.get<{ items: SetupItem[] }>("/core/setup-health/")).data.items,
    enabled: on(needs.setup, true),
  });

  const names = useQuery({
    ...options,
    queryKey: ["home", "contacts", "names-to-complete", companyId],
    queryFn: async () =>
      (await apiClient.get<{ count: number }>("/contacts/names-to-complete/")).data.count,
    enabled: on(needs.names, true),
  });

  const notes = useOpenQuickNotes(needs.notes);

  return {
    dashboard,
    todayOrders,
    dispatch,
    batches,
    stages,
    collections,
    bills,
    expenses,
    gst,
    sales,
    ageing,
    cash,
    setup,
    names,
    notes,
  };
}

export type HomeData = ReturnType<typeof useHomeData>;
