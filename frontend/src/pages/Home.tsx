import React, { useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  ClipboardCheck,
  Factory,
  FileText,
  Mic,
  Percent,
  Receipt,
  Truck,
  Users,
  Wallet,
  Warehouse,
} from "lucide-react";
import { Layout } from "@/components/Layout";
import { openAssistant } from "@/components/assistant/AssistantLauncher";
import { useAuth } from "@/hooks/useAuth";
import { useCompany } from "@/context/CompanyContext";
import { formatDate, formatIndianCompact } from "@/lib/format";
import { hasReadAccess, PENDING_WORK, visibleWorkspaces } from "@/resources/workspaces";
import type { WorkspaceType } from "@/types";
import { CompanyGate } from "./workspaces/WorkspaceShell";
import { AgeingChart, ReadyStockChart, SalesChart } from "./home/HomeCharts";
import {
  AppGrid,
  AttentionList,
  FigureStrip,
  NotificationFeed,
  type AttentionRow,
  type HomeApp,
  type HomeFigure,
} from "./home/HomePanels";
import { useHomeData, type HomeNeeds } from "./home/useHomeData";

type FigureKey =
  | "sales" | "owed_to_us" | "we_owe" | "stock_value" | "expenses"
  | "today_orders" | "open_orders" | "dispatch" | "batches" | "ready_items";
type AttentionKey =
  | "approve" | "dispatch" | "batches" | "low_stock"
  | "overdue_in" | "overdue_out" | "expenses" | "gst" | "drafts";
type ChartKey = "sales" | "ageing" | "ready";
type ShortcutKey =
  | "speak" | "dispatch" | "batches" | "ready" | "workers"
  | "collections" | "bills" | "expenses" | "gst";

/** What each person's home shows, picked by `default_workspace`. Access still comes from their roles. */
const ROLE_VIEW: Record<
  WorkspaceType,
  { shortcuts: ShortcutKey[]; figures: FigureKey[]; attention: AttentionKey[]; charts: ChartKey[] }
> = {
  admin: {
    shortcuts: [],
    figures: ["sales", "owed_to_us", "we_owe", "stock_value"],
    attention: ["low_stock", "overdue_in", "overdue_out", "approve", "dispatch", "gst", "expenses", "drafts"],
    charts: ["sales", "ageing", "ready"],
  },
  orders: {
    shortcuts: ["speak"],
    figures: ["today_orders", "open_orders", "sales"],
    attention: ["approve"],
    charts: ["sales"],
  },
  factory: {
    shortcuts: ["dispatch", "batches", "ready", "workers"],
    figures: ["dispatch", "batches", "ready_items", "stock_value"],
    attention: ["low_stock", "dispatch", "batches"],
    charts: ["ready"],
  },
  accounts: {
    shortcuts: ["collections", "bills", "expenses", "gst"],
    figures: ["owed_to_us", "we_owe", "expenses", "sales"],
    attention: ["overdue_in", "overdue_out", "gst", "expenses", "drafts"],
    charts: ["ageing", "sales"],
  },
};

/** Text for a figure: a dash on error and "…" while loading, never a fake zero. */
function figureText<T>(
  query: { isError: boolean; data?: T },
  format: (data: T) => string
): string {
  if (query.isError) return "–";
  return query.data === undefined ? "…" : format(query.data);
}

const HomeBody: React.FC = () => {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const { activeCompany, companies, clearActiveCompany } = useCompany();

  const view = ROLE_VIEW[user?.default_workspace ?? "admin"] ?? ROLE_VIEW.admin;
  const readableModels = user?.readable_models;

  const shows = {
    figure: (...keys: FigureKey[]) => keys.some((key) => view.figures.includes(key)),
    attention: (...keys: AttentionKey[]) => keys.some((key) => view.attention.includes(key)),
    chart: (key: ChartKey) => view.charts.includes(key),
  };

  const needs: HomeNeeds = {
    dashboard: true, // the draft counts behind the app badges
    todayOrders: shows.figure("today_orders"),
    dispatch: shows.figure("dispatch") || shows.attention("dispatch"),
    batches: shows.figure("batches") || shows.attention("batches"),
    stages: shows.figure("ready_items") || shows.attention("low_stock") || shows.chart("ready"),
    collections: shows.figure("owed_to_us") || shows.attention("overdue_in"),
    bills: shows.figure("we_owe") || shows.attention("overdue_out"),
    expenses: shows.figure("expenses") || shows.attention("expenses"),
    gst: shows.attention("gst"),
    sales: shows.figure("sales") || shows.chart("sales"),
    ageing: shows.chart("ageing"),
  };
  const data = useHomeData(needs);
  const { dashboard, stages, collections, bills, expenses, gst } = data;
  const drafts = dashboard.data?.draft_documents;

  const workspaces = useMemo(() => visibleWorkspaces(readableModels), [readableModels]);

  const shortcuts: Record<ShortcutKey, HomeApp> = {
    speak: { key: "speak", label: t("home.ws.orders.speak"), icon: Mic, fill: "bg-indigo-600", onClick: openAssistant },
    dispatch: { key: "dispatch", label: t("plain.dispatch"), icon: Truck, fill: "bg-indigo-600", to: "/selling/pending-orders", badge: data.dispatch.data },
    batches: { key: "batches", label: t("batches.nav_title"), icon: Factory, fill: "bg-violet-600", to: "/manufacturing/batches" },
    ready: { key: "ready", label: t("home.ws.factory.ready"), icon: Warehouse, fill: "bg-emerald-600", to: "/stock/reports/stages" },
    workers: { key: "workers", label: t("home.ws.factory.workers"), icon: Users, fill: "bg-rose-600", to: "/hr/employees" },
    collections: { key: "collections", label: t("acc.nav.collections"), icon: Wallet, fill: "bg-emerald-600", to: "/accounts/collections" },
    bills: { key: "bills", label: t("acc.nav.bills_to_pay"), icon: Receipt, fill: "bg-amber-600", to: "/accounts/bills-to-pay" },
    expenses: { key: "expenses", label: t("acc.nav.expenses"), icon: FileText, fill: "bg-rose-600", to: "/accounts/expenses" },
    gst: { key: "gst", label: t("acc.nav.gst_position"), icon: Percent, fill: "bg-sky-600", to: "/accounts/gst-position" },
  };

  const draftsIn = (workspace: string) =>
    PENDING_WORK.filter((row) => row.workspace === workspace && hasReadAccess(readableModels, row.access)).reduce(
      (sum, row) => sum + (drafts?.[row.key] ?? 0),
      0
    );

  const apps: HomeApp[] = [
    ...view.shortcuts.map((key) => shortcuts[key]),
    ...workspaces.map((workspace) => ({
      key: workspace.key,
      label: t(workspace.titleKey),
      icon: workspace.icon,
      fill: workspace.appColor,
      to: `/${workspace.key}`,
      badge: draftsIn(workspace.key),
    })),
  ];

  const stageRows = stages.data ?? [];
  const thisMonth = data.sales.data?.[data.sales.data.length - 1];

  const figures: Record<FigureKey, HomeFigure> = {
    sales: { key: "sales", label: t("home.dash.sales_month"), value: figureText(data.sales, () => formatIndianCompact(thisMonth?.amount)), to: "/selling/analytics" },
    owed_to_us: { key: "owed_to_us", label: t("home.dash.owed_to_us"), value: figureText(collections, (d) => formatIndianCompact(d.total_receivables)), to: "/accounts/collections" },
    we_owe: { key: "we_owe", label: t("home.dash.we_owe"), value: figureText(bills, (d) => formatIndianCompact(d.total_payables)), to: "/accounts/bills-to-pay" },
    stock_value: { key: "stock_value", label: t("dash.kpi.stock_value"), value: figureText(dashboard, (d) => (d.stock_value === null ? "–" : formatIndianCompact(d.stock_value))), to: "/stock/bins" },
    expenses: { key: "expenses", label: t("acc.nav.expenses"), value: figureText(expenses, (d) => formatIndianCompact(d.total)), to: "/accounts/expenses" },
    today_orders: { key: "today_orders", label: t("home.ws.orders.today"), value: figureText(data.todayOrders, String), to: "/selling/sales-orders" },
    open_orders: { key: "open_orders", label: t("home.ws.orders.open"), value: figureText(dashboard, (d) => String(d.open_sales_orders?.count ?? "–")), to: "/selling/sales-orders?docstatus=1" },
    dispatch: { key: "dispatch", label: t("home.dash.orders_to_dispatch"), value: figureText(data.dispatch, String), to: "/selling/pending-orders" },
    batches: { key: "batches", label: t("home.dash.batches_running"), value: figureText(data.batches, String), to: "/manufacturing/batches" },
    ready_items: { key: "ready_items", label: t("home.ws.factory.ready"), value: figureText(stages, (rows) => String(rows.filter((row) => Number(row.ready_qty) > 0).length)), to: "/stock/reports/stages" },
  };

  const lowStock = stageRows.filter((row) => row.is_critical).length;
  const otherDrafts = (drafts?.total ?? 0) - (drafts?.sales_orders ?? 0);
  const gstDue = gst.data?.status === "Due" ? Number(gst.data.net_payable) : 0;

  const attention: Record<AttentionKey, AttentionRow | null> = {
    low_stock: lowStock > 0 ? { key: "low_stock", label: t("home.dash.low_stock"), value: String(lowStock), to: "/stock/reports/stages", icon: Warehouse, urgent: true } : null,
    overdue_in: Number(collections.data?.total_overdue) > 0 ? { key: "overdue_in", label: t("home.dash.overdue_collect"), value: formatIndianCompact(collections.data?.total_overdue), to: "/accounts/collections", icon: Wallet, urgent: true } : null,
    overdue_out: Number(bills.data?.total_overdue) > 0 ? { key: "overdue_out", label: t("home.dash.overdue_bills"), value: formatIndianCompact(bills.data?.total_overdue), to: "/accounts/bills-to-pay", icon: Receipt, urgent: true } : null,
    approve: drafts?.sales_orders ? { key: "approve", label: t("home.dash.orders_to_approve"), value: String(drafts.sales_orders), to: "/selling/sales-orders?docstatus=0", icon: ClipboardCheck } : null,
    dispatch: data.dispatch.data ? { key: "dispatch", label: t("home.dash.orders_to_dispatch"), value: String(data.dispatch.data), to: "/selling/pending-orders", icon: Truck } : null,
    batches: data.batches.data ? { key: "batches", label: t("home.dash.batches_running"), value: String(data.batches.data), to: "/manufacturing/batches", icon: Factory } : null,
    gst: gstDue > 0 && gst.data ? { key: "gst", label: t("home.dash.gst_due", { date: formatDate(gst.data.due_date) }), value: formatIndianCompact(gstDue), to: "/accounts/gst-position", icon: Percent } : null,
    expenses: Number(expenses.data?.due) > 0 ? { key: "expenses", label: t("home.dash.expenses_unpaid"), value: formatIndianCompact(expenses.data?.due), to: "/accounts/expenses", icon: FileText } : null,
    drafts: otherDrafts > 0 ? { key: "drafts", label: t("home.dash.other_drafts"), value: String(otherDrafts), to: "/all-modules", icon: FileText } : null,
  };
  const attentionRows = view.attention.flatMap((key) => attention[key] ?? []);

  const queries = Object.values(data);
  const displayName = user?.first_name || user?.username || "";
  const dateText = new Intl.DateTimeFormat(i18n.language, {
    weekday: "long",
    day: "numeric",
    month: "long",
  }).format(new Date());

  const charts = {
    sales: <SalesChart key="sales" query={data.sales} />,
    ageing: <AgeingChart key="ageing" query={data.ageing} />,
    ready: <ReadyStockChart key="ready" query={stages} />,
  };
  const [leadChart, ...otherCharts] = view.charts;

  return (
    <Layout>
      <div className="space-y-6 px-1 sm:space-y-8 sm:px-0">
        <header className="flex flex-wrap items-end justify-between gap-2">
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-semibold tracking-tight text-slate-100">
              {t("home.welcome", { name: displayName })}
            </h1>
            <p className="mt-1 truncate text-sm text-slate-400">
              {activeCompany?.name} · {dateText}
            </p>
          </div>
          {companies.length > 1 && (
            <button
              type="button"
              onClick={clearActiveCompany}
              className="min-h-[44px] rounded-lg px-1 text-xs font-medium text-slate-400 transition-colors hover:text-indigo-400 sm:min-h-0"
            >
              {t("company.switch")}
            </button>
          )}
        </header>

        <AppGrid apps={apps} />

        <FigureStrip figures={view.figures.map((key) => figures[key])} />

        <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_22rem] lg:gap-10">
          <div className="order-first space-y-6 lg:order-last">
            <AttentionList
              rows={attentionRows}
              loading={queries.some((query) => query.isLoading)}
              failed={queries.some((query) => query.isError)}
            />
            <NotificationFeed />
          </div>
          <div className="min-w-0 space-y-8">
            {charts[leadChart]}
            {otherCharts.length > 0 && (
              <div className="grid gap-8 sm:grid-cols-2">{otherCharts.map((key) => charts[key])}</div>
            )}
          </div>
        </div>
      </div>
    </Layout>
  );
};

export const Home: React.FC = () => (
  <CompanyGate>
    <HomeBody />
  </CompanyGate>
);
