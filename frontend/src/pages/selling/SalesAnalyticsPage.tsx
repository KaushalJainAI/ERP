import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Award,
  BarChart3,
  Boxes,
  Calendar,
  CheckCircle2,
  Clock,
  DollarSign,
  Download,
  Flame,
  Layers,
  LineChart,
  Percent,
  RefreshCw,
  Search,
  ShieldCheck,
  ShoppingBag,
  TrendingDown,
  TrendingUp,
  UserCheck,
  UserMinus,
  UserPlus,
  Users,
} from "lucide-react";

import { apiClient } from "@/api/client";
import { Layout } from "@/components/Layout";
import { useCompany } from "@/context/CompanyContext";
import { formatIndianCurrency, formatIndianNumber } from "@/lib/format";
import type {
  PeriodFilterOption,
  ProductKPI,
  SalesAnalyticsResponse,
} from "@/types/analytics";

export const SalesAnalyticsPage: React.FC = () => {
  const { t } = useTranslation();
  const { activeCompany } = useCompany();

  const [period, setPeriod] = useState<PeriodFilterOption>("this_fy");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [appliedCustom, setAppliedCustom] = useState<{ from: string; to: string } | null>(null);
  const [activeTab, setActiveTab] = useState<"overview" | "trends" | "funnel" | "products" | "customers" | "receivables">("overview");
  const [spiceOnly, setSpiceOnly] = useState(false);
  const [productSearch, setProductSearch] = useState("");

  const queryParams: Record<string, string> = {
    company: String(activeCompany?.id ?? ""),
  };

  if (period === "custom" && appliedCustom) {
    queryParams.from_date = appliedCustom.from;
    queryParams.to_date = appliedCustom.to;
  } else {
    queryParams.period = period;
  }

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["sales-analytics", activeCompany?.id, period, appliedCustom],
    queryFn: async () => {
      const resp = await apiClient.get<SalesAnalyticsResponse>("/selling/analytics/", {
        params: queryParams,
      });
      return resp.data;
    },
    enabled: !!activeCompany,
    staleTime: 60_000,
  });

  const handleApplyCustom = (e: React.FormEvent) => {
    e.preventDefault();
    if (customFrom && customTo) {
      setAppliedCustom({ from: customFrom, to: customTo });
      setPeriod("custom");
    }
  };

  const summary = data?.summary;
  const conversion = data?.conversion_and_velocity;
  const revenue = data?.revenue_and_volume;
  const products = data?.top_products;
  const customers = data?.customer_analytics;
  const receivables = data?.receivables_and_dso;

  const filteredProducts = (spiceOnly ? products?.top_spice_blends : products?.top_products)?.filter(
    (p: ProductKPI) =>
      p.item_name.toLowerCase().includes(productSearch.toLowerCase()) ||
      p.item_code.toLowerCase().includes(productSearch.toLowerCase())
  ) ?? [];

  return (
    <Layout>
      <div className="space-y-6 pb-12">
        {/* Page Header */}
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b border-slate-800 pb-5">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-indigo-400">
              <BarChart3 className="h-4 w-4" />
              <span>{t("selling_kpi.module_subtitle")}</span>
            </div>
            <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-100">
              {t("selling_kpi.page_title")}
            </h1>
            <p className="mt-1 text-xs text-slate-400">
              {t("selling_kpi.page_subtitle")}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => refetch()}
              disabled={isFetching}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:border-slate-600 hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? "animate-spin text-indigo-400" : ""}`} />
              <span>{t("selling_kpi.refresh")}</span>
            </button>
          </div>
        </div>

        {/* Period Filter Bar */}
        <div className="flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/70 p-3.5 shadow-sm md:flex-row md:items-center md:justify-between">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="me-1 text-xs font-medium text-slate-400 flex items-center gap-1">
              <Calendar className="h-3.5 w-3.5 text-indigo-400" />
              {t("selling_kpi.filter_period")}:
            </span>
            {(
              [
                { key: "this_month", label: t("selling_kpi.period_this_month") },
                { key: "last_month", label: t("selling_kpi.period_last_month") },
                { key: "this_quarter", label: t("selling_kpi.period_this_quarter") },
                { key: "last_quarter", label: t("selling_kpi.period_last_quarter") },
                { key: "this_fy", label: t("selling_kpi.period_this_fy") },
                { key: "last_fy", label: t("selling_kpi.period_last_fy") },
                { key: "all_time", label: t("selling_kpi.period_all_time") },
              ] as const
            ).map((opt) => (
              <button
                key={opt.key}
                type="button"
                onClick={() => {
                  setPeriod(opt.key);
                  setAppliedCustom(null);
                }}
                className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                  period === opt.key
                    ? "bg-indigo-600 text-white shadow"
                    : "text-slate-300 hover:bg-slate-800 hover:text-white"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* Custom Date Form */}
          <form onSubmit={handleApplyCustom} className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-400">{t("selling_kpi.custom_range")}:</span>
            <input
              type="date"
              value={customFrom}
              onChange={(e) => setCustomFrom(e.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
            />
            <span className="text-slate-500">-</span>
            <input
              type="date"
              value={customTo}
              onChange={(e) => setCustomTo(e.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
            />
            <button
              type="submit"
              disabled={!customFrom || !customTo}
              className="rounded-lg bg-slate-800 px-3 py-1 text-xs font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50"
            >
              {t("selling_kpi.apply")}
            </button>
          </form>
        </div>

        {error && (
          <div className="rounded-xl border border-rose-800/60 bg-rose-950/20 p-4 text-xs text-rose-300 flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400" />
            <span>{t("selling_kpi.load_error")}</span>
          </div>
        )}

        {/* 6 Top Headline KPI Cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          {/* 1. Net Revenue */}
          <div className="flex flex-col justify-between rounded-xl border border-slate-800 bg-slate-900/90 p-4 shadow-sm hover:border-slate-700 transition-colors">
            <div className="flex items-center justify-between text-slate-400">
              <span className="text-xs font-medium uppercase tracking-wider">{t("selling_kpi.net_revenue")}</span>
              <DollarSign className="h-4 w-4 text-sky-400" />
            </div>
            <div className="mt-3">
              <div className="text-xl font-bold tracking-tight text-slate-100 tabular-nums">
                {formatIndianCurrency(summary?.net_revenue ?? "0.00")}
              </div>
              <div className="mt-1.5 flex items-center gap-1.5 text-xs">
                {summary?.revenue_growth_pct !== null && summary?.revenue_growth_pct !== undefined ? (
                  summary.revenue_growth_pct >= 0 ? (
                    <span className="inline-flex items-center font-semibold text-emerald-400 gap-0.5">
                      <TrendingUp className="h-3 w-3" />
                      +{summary.revenue_growth_pct}%
                    </span>
                  ) : (
                    <span className="inline-flex items-center font-semibold text-rose-400 gap-0.5">
                      <TrendingDown className="h-3 w-3" />
                      {summary.revenue_growth_pct}%
                    </span>
                  )
                ) : (
                  <span className="text-slate-500">—</span>
                )}
                <span className="text-slate-500">{t("selling_kpi.vs_prev_period")}</span>
              </div>
            </div>
            <div className="mt-2 text-[11px] text-slate-500 truncate">
              {t("selling_kpi.gross")}: {formatIndianCurrency(revenue?.current_period.gross_revenue ?? "0.00")}
            </div>
          </div>

          {/* 2. Volume Sold */}
          <div className="flex flex-col justify-between rounded-xl border border-slate-800 bg-slate-900/90 p-4 shadow-sm hover:border-slate-700 transition-colors">
            <div className="flex items-center justify-between text-slate-400">
              <span className="text-xs font-medium uppercase tracking-wider">{t("selling_kpi.sales_volume")}</span>
              <Boxes className="h-4 w-4 text-emerald-400" />
            </div>
            <div className="mt-3">
              <div className="text-xl font-bold tracking-tight text-slate-100 tabular-nums">
                {formatIndianNumber(summary?.volume ?? "0.000")}
              </div>
              <div className="mt-1.5 flex items-center gap-1.5 text-xs">
                {summary?.volume_growth_pct !== null && summary?.volume_growth_pct !== undefined ? (
                  summary.volume_growth_pct >= 0 ? (
                    <span className="inline-flex items-center font-semibold text-emerald-400 gap-0.5">
                      <TrendingUp className="h-3 w-3" />
                      +{summary.volume_growth_pct}%
                    </span>
                  ) : (
                    <span className="inline-flex items-center font-semibold text-rose-400 gap-0.5">
                      <TrendingDown className="h-3 w-3" />
                      {summary.volume_growth_pct}%
                    </span>
                  )
                ) : (
                  <span className="text-slate-500">—</span>
                )}
                <span className="text-slate-500">{t("selling_kpi.units_kg")}</span>
              </div>
            </div>
            <div className="mt-2 text-[11px] text-slate-500 truncate">
              {summary?.invoices_count ?? 0} {t("selling_kpi.invoices")}
            </div>
          </div>

          {/* 3. Conversion Rate */}
          <div className="flex flex-col justify-between rounded-xl border border-slate-800 bg-slate-900/90 p-4 shadow-sm hover:border-slate-700 transition-colors">
            <div className="flex items-center justify-between text-slate-400">
              <span className="text-xs font-medium uppercase tracking-wider">{t("selling_kpi.conversion_rate")}</span>
              <Percent className="h-4 w-4 text-violet-400" />
            </div>
            <div className="mt-3">
              <div className="text-xl font-bold tracking-tight text-slate-100 tabular-nums">
                {summary?.conversion_rate_pct ?? 0}%
              </div>
              <div className="mt-1.5 flex items-center gap-1 text-xs text-indigo-300">
                <Clock className="h-3 w-3" />
                <span>
                  {summary?.average_conversion_days ?? 0} {t("selling_kpi.days_velocity")}
                </span>
              </div>
            </div>
            <div className="mt-2 text-[11px] text-slate-500 truncate">
              {conversion?.converted_orders_count ?? 0}/{conversion?.total_orders_count ?? 0} {t("selling_kpi.orders_converted")}
            </div>
          </div>

          {/* 4. Average Order Value (AOV) */}
          <div className="flex flex-col justify-between rounded-xl border border-slate-800 bg-slate-900/90 p-4 shadow-sm hover:border-slate-700 transition-colors">
            <div className="flex items-center justify-between text-slate-400">
              <span className="text-xs font-medium uppercase tracking-wider">{t("selling_kpi.aov")}</span>
              <ShoppingBag className="h-4 w-4 text-amber-400" />
            </div>
            <div className="mt-3">
              <div className="text-xl font-bold tracking-tight text-slate-100 tabular-nums">
                {formatIndianCurrency(summary?.average_order_value ?? "0.00")}
              </div>
              <div className="mt-1.5 flex items-center gap-1.5 text-xs">
                {revenue?.growth.aov_growth_pct !== null && revenue?.growth.aov_growth_pct !== undefined ? (
                  revenue.growth.aov_growth_pct >= 0 ? (
                    <span className="inline-flex items-center font-semibold text-emerald-400 gap-0.5">
                      <TrendingUp className="h-3 w-3" />
                      +{revenue.growth.aov_growth_pct}%
                    </span>
                  ) : (
                    <span className="inline-flex items-center font-semibold text-rose-400 gap-0.5">
                      <TrendingDown className="h-3 w-3" />
                      {revenue.growth.aov_growth_pct}%
                    </span>
                  )
                ) : (
                  <span className="text-slate-500">—</span>
                )}
                <span className="text-slate-500">{t("selling_kpi.per_order")}</span>
              </div>
            </div>
            <div className="mt-2 text-[11px] text-slate-500 truncate">
              {t("selling_kpi.total_tax")}: {formatIndianCurrency(revenue?.current_period.total_tax ?? "0.00")}
            </div>
          </div>

          {/* 5. Receivables DSO */}
          <div className="flex flex-col justify-between rounded-xl border border-slate-800 bg-slate-900/90 p-4 shadow-sm hover:border-slate-700 transition-colors">
            <div className="flex items-center justify-between text-slate-400">
              <span className="text-xs font-medium uppercase tracking-wider">{t("selling_kpi.dso")}</span>
              <Clock className="h-4 w-4 text-rose-400" />
            </div>
            <div className="mt-3">
              <div className="text-xl font-bold tracking-tight text-slate-100 tabular-nums">
                {summary?.dso_days ?? 0} {t("selling_kpi.days")}
              </div>
              <div className="mt-1.5 flex items-center gap-1 text-xs">
                {(summary?.dso_days ?? 0) <= 45 ? (
                  <span className="text-emerald-400 font-medium flex items-center gap-0.5">
                    <CheckCircle2 className="h-3 w-3" />
                    {t("selling_kpi.dso_healthy")}
                  </span>
                ) : (
                  <span className="text-rose-400 font-medium flex items-center gap-0.5">
                    <AlertTriangle className="h-3 w-3" />
                    {t("selling_kpi.dso_high")}
                  </span>
                )}
              </div>
            </div>
            <div className="mt-2 text-[11px] text-slate-500 truncate">
              {t("selling_kpi.outstanding")}: {formatIndianCurrency(summary?.total_receivables ?? "0.00")}
            </div>
          </div>

          {/* 6. Customer Engagement */}
          <div className="flex flex-col justify-between rounded-xl border border-slate-800 bg-slate-900/90 p-4 shadow-sm hover:border-slate-700 transition-colors">
            <div className="flex items-center justify-between text-slate-400">
              <span className="text-xs font-medium uppercase tracking-wider">{t("selling_kpi.active_customers")}</span>
              <Users className="h-4 w-4 text-cyan-400" />
            </div>
            <div className="mt-3">
              <div className="text-xl font-bold tracking-tight text-slate-100 tabular-nums">
                {summary?.active_customers ?? 0}
              </div>
              <div className="mt-1.5 flex items-center gap-1.5 text-xs text-slate-300">
                <span className="text-emerald-400 font-medium">+{summary?.new_customers ?? 0}</span>
                <span className="text-slate-500">{t("selling_kpi.new")}</span>
                <span className="text-slate-600">·</span>
                <span className="text-indigo-400 font-medium">{summary?.repeat_rate_pct ?? 0}%</span>
                <span className="text-slate-500">{t("selling_kpi.repeat")}</span>
              </div>
            </div>
            <div className="mt-2 text-[11px] text-slate-500 truncate">
              {customers?.churned_customers_count ?? 0} {t("selling_kpi.churned_count")}
            </div>
          </div>
        </div>

        {/* Navigation Tabs */}
        <div className="flex border-b border-slate-800 overflow-x-auto space-x-1">
          {[
            { key: "overview", label: t("selling_kpi.tab_overview"), icon: Layers },
            { key: "trends", label: t("selling_kpi.tab_trends"), icon: LineChart },
            { key: "funnel", label: t("selling_kpi.tab_funnel"), icon: BarChart3 },
            { key: "products", label: t("selling_kpi.tab_products"), icon: Award },
            { key: "customers", label: t("selling_kpi.tab_customers"), icon: Users },
            { key: "receivables", label: t("selling_kpi.tab_receivables"), icon: Clock },
          ].map((tab) => {
            const Icon = tab.icon;
            const active = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key as any)}
                className={`flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-medium transition-colors whitespace-nowrap ${
                  active
                    ? "border-indigo-500 text-indigo-400"
                    : "border-transparent text-slate-400 hover:border-slate-700 hover:text-slate-200"
                }`}
              >
                <Icon className="h-4 w-4" />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>

        {/* Tab 1: Overview / Multi-card visual layout */}
        {(activeTab === "overview" || activeTab === "trends") && (
          <div className="space-y-6">
            <div className="rounded-xl border border-slate-800 bg-slate-900/90 p-5 shadow-sm">
              <div className="flex items-center justify-between pb-4 border-b border-slate-800">
                <div>
                  <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
                    <LineChart className="h-4 w-4 text-indigo-400" />
                    {t("selling_kpi.monthly_trends_title")}
                  </h3>
                  <p className="text-xs text-slate-400 mt-0.5">
                    {t("selling_kpi.monthly_trends_desc")}
                  </p>
                </div>
              </div>

              {/* Monthly Visual Bar Chart */}
              <div className="mt-6 space-y-4">
                {revenue?.monthly_trends && revenue.monthly_trends.length > 0 ? (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
                    {revenue.monthly_trends.map((m) => {
                      const net = parseFloat(m.net_revenue) || 0;
                      const maxNet = Math.max(
                        ...revenue.monthly_trends.map((r) => parseFloat(r.net_revenue) || 1),
                        1
                      );
                      const barHeight = Math.max(15, Math.round((net / maxNet) * 100));
                      return (
                        <div
                          key={m.period}
                          className="flex flex-col justify-between rounded-lg border border-slate-800 bg-slate-950/60 p-3"
                        >
                          <div className="flex items-center justify-between text-xs">
                            <span className="font-semibold text-slate-200">{m.label}</span>
                            {m.growth_pct !== null && (
                              <span
                                className={`text-[10px] font-medium ${
                                  m.growth_pct >= 0 ? "text-emerald-400" : "text-rose-400"
                                }`}
                              >
                                {m.growth_pct >= 0 ? `+${m.growth_pct}%` : `${m.growth_pct}%`}
                              </span>
                            )}
                          </div>

                          {/* Visual Bar Indicator */}
                          <div className="my-3 h-16 w-full rounded bg-slate-900 flex items-end p-1">
                            <div
                              style={{ height: `${barHeight}%` }}
                              className="w-full rounded bg-indigo-500/80 transition-all duration-500 hover:bg-indigo-400"
                            />
                          </div>

                          <div className="space-y-1 text-[11px]">
                            <div className="flex justify-between text-slate-400">
                              <span>{t("selling_kpi.net")}:</span>
                              <span className="font-semibold text-slate-200 tabular-nums">
                                {formatIndianCurrency(m.net_revenue)}
                              </span>
                            </div>
                            <div className="flex justify-between text-slate-500">
                              <span>{t("selling_kpi.volume")}:</span>
                              <span className="tabular-nums">{m.volume}</span>
                            </div>
                            <div className="flex justify-between text-slate-500">
                              <span>{t("selling_kpi.invoices")}:</span>
                              <span className="tabular-nums">{m.invoices_count}</span>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="py-8 text-center text-xs text-slate-500">
                    {t("selling_kpi.no_data_period")}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Tab 2: Sales Funnel & Conversion Velocity */}
        {(activeTab === "overview" || activeTab === "funnel") && (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {/* Sales Pipeline Funnel */}
            <div className="rounded-xl border border-slate-800 bg-slate-900/90 p-5 shadow-sm">
              <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
                <BarChart3 className="h-4 w-4 text-indigo-400" />
                {t("selling_kpi.pipeline_funnel_title")}
              </h3>
              <p className="text-xs text-slate-400 mt-0.5">
                {t("selling_kpi.pipeline_funnel_desc")}
              </p>

              <div className="mt-5 space-y-3">
                {conversion?.pipeline_funnel.map((stage, idx) => {
                  const maxCount = Math.max(
                    ...(conversion?.pipeline_funnel.map((s) => s.count) ?? [1]),
                    1
                  );
                  const pct = Math.max(8, Math.round((stage.count / maxCount) * 100));
                  return (
                    <div key={stage.stage} className="space-y-1">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-medium text-slate-300">
                          {idx + 1}. {stage.stage}
                        </span>
                        <div className="flex items-center gap-3">
                          <span className="font-bold text-slate-200">{stage.count} docs</span>
                          <span className="text-slate-400 font-mono text-[11px]">
                            {formatIndianCurrency(stage.amount)}
                          </span>
                        </div>
                      </div>
                      <div className="h-3 w-full rounded-full bg-slate-800 overflow-hidden">
                        <div
                          style={{ width: `${pct}%` }}
                          className={`h-full rounded-full ${
                            idx === 0
                              ? "bg-slate-500"
                              : idx === 1
                              ? "bg-sky-500"
                              : idx === 2
                              ? "bg-indigo-500"
                              : "bg-emerald-500"
                          } transition-all duration-500`}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Conversion Stats Breakdown */}
              <div className="mt-6 grid grid-cols-3 gap-3 border-t border-slate-800 pt-4 text-center">
                <div className="rounded-lg bg-slate-950/50 p-2.5">
                  <div className="text-xs text-slate-400">{t("selling_kpi.fully_converted")}</div>
                  <div className="mt-1 text-base font-bold text-emerald-400">
                    {conversion?.fully_converted_count ?? 0}
                  </div>
                </div>
                <div className="rounded-lg bg-slate-950/50 p-2.5">
                  <div className="text-xs text-slate-400">{t("selling_kpi.partially_converted")}</div>
                  <div className="mt-1 text-base font-bold text-amber-400">
                    {conversion?.partially_converted_count ?? 0}
                  </div>
                </div>
                <div className="rounded-lg bg-slate-950/50 p-2.5">
                  <div className="text-xs text-slate-400">{t("selling_kpi.unconverted_open")}</div>
                  <div className="mt-1 text-base font-bold text-slate-300">
                    {conversion?.unconverted_orders_count ?? 0}
                  </div>
                </div>
              </div>
            </div>

            {/* Conversion Velocity & Fulfillment Speed */}
            <div className="rounded-xl border border-slate-800 bg-slate-900/90 p-5 shadow-sm">
              <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
                <Clock className="h-4 w-4 text-violet-400" />
                {t("selling_kpi.velocity_title")}
              </h3>
              <p className="text-xs text-slate-400 mt-0.5">
                {t("selling_kpi.velocity_desc")}
              </p>

              <div className="mt-5 grid grid-cols-2 gap-3">
                <div className="rounded-xl border border-indigo-900/40 bg-indigo-950/20 p-4">
                  <div className="text-xs text-indigo-300 font-medium">{t("selling_kpi.avg_days_to_convert")}</div>
                  <div className="mt-2 text-2xl font-bold text-indigo-100">
                    {conversion?.average_conversion_days ?? 0} <span className="text-xs font-normal text-slate-400">{t("selling_kpi.days")}</span>
                  </div>
                  <div className="mt-1 text-[11px] text-indigo-400">
                    {t("selling_kpi.median_time")}: {conversion?.median_conversion_days ?? 0} {t("selling_kpi.days")}
                  </div>
                </div>

                <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                  <div className="text-xs text-slate-400 font-medium">{t("selling_kpi.order_conversion_rate")}</div>
                  <div className="mt-2 text-2xl font-bold text-emerald-400">
                    {conversion?.conversion_rate_pct ?? 0}%
                  </div>
                  <div className="mt-1 text-[11px] text-slate-500">
                    {conversion?.converted_orders_count ?? 0} {t("selling_kpi.of")} {conversion?.total_orders_count ?? 0} {t("selling_kpi.orders")}
                  </div>
                </div>
              </div>

              {/* Velocity Buckets Breakdown */}
              <div className="mt-5 space-y-2.5">
                <div className="text-xs font-medium text-slate-300">{t("selling_kpi.speed_distribution")}:</div>
                {[
                  { label: t("selling_kpi.vel_same_day"), count: conversion?.velocity_distribution.same_day ?? 0 },
                  { label: t("selling_kpi.vel_1_3"), count: conversion?.velocity_distribution["1_to_3_days"] ?? 0 },
                  { label: t("selling_kpi.vel_4_7"), count: conversion?.velocity_distribution["4_to_7_days"] ?? 0 },
                  { label: t("selling_kpi.vel_8_14"), count: conversion?.velocity_distribution["8_to_14_days"] ?? 0 },
                  { label: t("selling_kpi.vel_15_plus"), count: conversion?.velocity_distribution["15_plus_days"] ?? 0 },
                ].map((b) => (
                  <div key={b.label} className="flex items-center justify-between text-xs">
                    <span className="text-slate-400">{b.label}</span>
                    <span className="font-semibold text-slate-200 bg-slate-800 px-2 py-0.5 rounded text-[11px]">
                      {b.count} {t("selling_kpi.orders")}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Tab 3: Top Selling Products & Spice Blends */}
        {(activeTab === "overview" || activeTab === "products") && (
          <div className="rounded-xl border border-slate-800 bg-slate-900/90 p-5 shadow-sm space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between pb-3 border-b border-slate-800">
              <div>
                <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
                  <Award className="h-4 w-4 text-amber-400" />
                  {t("selling_kpi.top_products_title")}
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  {t("selling_kpi.top_products_desc")}
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                {/* Spice Filter Toggle */}
                <button
                  type="button"
                  onClick={() => setSpiceOnly(!spiceOnly)}
                  className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium border transition-colors ${
                    spiceOnly
                      ? "border-amber-500 bg-amber-950/60 text-amber-300 shadow"
                      : "border-slate-700 bg-slate-800/80 text-slate-300 hover:border-slate-600"
                  }`}
                >
                  <Flame className="h-3.5 w-3.5 text-amber-400" />
                  <span>{t("selling_kpi.spice_blends_only")}</span>
                  {spiceOnly && (
                    <span className="ms-1 rounded-full bg-amber-500/20 px-1.5 py-0.2 text-[10px] text-amber-300">
                      {products?.spice_blends_count ?? 0}
                    </span>
                  )}
                </button>

                {/* Search */}
                <div className="relative">
                  <Search className="absolute start-2.5 top-2.5 h-3.5 w-3.5 text-slate-500" />
                  <input
                    type="text"
                    placeholder={t("selling_kpi.search_product")}
                    value={productSearch}
                    onChange={(e) => setProductSearch(e.target.value)}
                    className="rounded-lg border border-slate-700 bg-slate-950 ps-8 pe-3 py-1 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none w-44 sm:w-56"
                  />
                </div>
              </div>
            </div>

            {/* Products Table */}
            <div className="overflow-x-auto">
              <table className="w-full text-start text-xs">
                <thead>
                  <tr className="border-b border-slate-800 text-slate-400">
                    <th className="py-2.5 text-start font-medium">{t("selling_kpi.rank")}</th>
                    <th className="py-2.5 text-start font-medium">{t("selling_kpi.product")}</th>
                    <th className="py-2.5 text-start font-medium">{t("selling_kpi.group")}</th>
                    <th className="py-2.5 text-end font-medium">{t("selling_kpi.qty_sold")}</th>
                    <th className="py-2.5 text-end font-medium">{t("selling_kpi.avg_rate")}</th>
                    <th className="py-2.5 text-end font-medium">{t("selling_kpi.revenue")}</th>
                    <th className="py-2.5 text-end font-medium">{t("selling_kpi.revenue_share")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {filteredProducts.map((p, idx) => (
                    <tr key={p.item_id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="py-2.5 font-bold text-slate-500">#{idx + 1}</td>
                      <td className="py-2.5 font-medium text-slate-100">
                        <div className="flex items-center gap-2">
                          {p.is_spice_blend && (
                            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-amber-950/60 text-amber-400 border border-amber-800/40" title="Spice / Masala blend">
                              <Flame className="h-3 w-3" />
                            </span>
                          )}
                          <div>
                            <span className="block">{p.item_name}</span>
                            <span className="text-[10px] text-slate-400 font-mono">{p.item_code}</span>
                          </div>
                        </div>
                      </td>
                      <td className="py-2.5 text-slate-400">{p.item_group || "—"}</td>
                      <td className="py-2.5 text-end font-mono text-slate-200">
                        {p.quantity_sold} <span className="text-slate-500 text-[10px]">{p.uom}</span>
                      </td>
                      <td className="py-2.5 text-end font-mono text-slate-300">
                        {formatIndianCurrency(p.average_rate)}
                      </td>
                      <td className="py-2.5 text-end font-bold font-mono text-slate-100">
                        {formatIndianCurrency(p.revenue)}
                      </td>
                      <td className="py-2.5 text-end">
                        <div className="flex items-center justify-end gap-2">
                          <div className="w-16 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                            <div
                              style={{ width: `${Math.min(100, p.contribution_pct)}%` }}
                              className="h-full rounded-full bg-amber-400"
                            />
                          </div>
                          <span className="font-mono text-slate-300 text-[11px] w-10 text-end">
                            {p.contribution_pct}%
                          </span>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {filteredProducts.length === 0 && (
                    <tr>
                      <td colSpan={7} className="py-8 text-center text-slate-500 text-xs">
                        {t("selling_kpi.no_products_found")}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Tab 4: Customer Acquisition, Churn & Concentration */}
        {(activeTab === "overview" || activeTab === "customers") && (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            {/* Customer Metrics & Concentration Cards */}
            <div className="space-y-4 lg:col-span-1">
              <div className="rounded-xl border border-slate-800 bg-slate-900/90 p-5 shadow-sm space-y-4">
                <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
                  <Users className="h-4 w-4 text-cyan-400" />
                  {t("selling_kpi.customer_dynamics")}
                </h3>

                <div className="space-y-3">
                  <div className="flex items-center justify-between rounded-lg bg-slate-950/60 p-3">
                    <div className="flex items-center gap-2.5">
                      <UserPlus className="h-4 w-4 text-emerald-400" />
                      <span className="text-xs text-slate-300">{t("selling_kpi.new_acquired")}</span>
                    </div>
                    <span className="text-sm font-bold text-emerald-400 tabular-nums">
                      {customers?.new_customers_count ?? 0}
                    </span>
                  </div>

                  <div className="flex items-center justify-between rounded-lg bg-slate-950/60 p-3">
                    <div className="flex items-center gap-2.5">
                      <UserCheck className="h-4 w-4 text-indigo-400" />
                      <span className="text-xs text-slate-300">{t("selling_kpi.repeat_customers")}</span>
                    </div>
                    <span className="text-sm font-bold text-indigo-400 tabular-nums">
                      {customers?.repeat_customers_count ?? 0}
                    </span>
                  </div>

                  <div className="flex items-center justify-between rounded-lg bg-slate-950/60 p-3">
                    <div className="flex items-center gap-2.5">
                      <UserMinus className="h-4 w-4 text-rose-400" />
                      <span className="text-xs text-slate-300">{t("selling_kpi.churned_customers")}</span>
                    </div>
                    <span className="text-sm font-bold text-rose-400 tabular-nums">
                      {customers?.churned_customers_count ?? 0}
                    </span>
                  </div>
                </div>

                {/* Pareto 80/20 & HHI Concentration Box */}
                <div className="rounded-xl border border-slate-800 bg-slate-950 p-4 space-y-3">
                  <div className="text-xs font-semibold text-slate-200 uppercase tracking-wider flex items-center justify-between">
                    <span>{t("selling_kpi.concentration_analysis")}</span>
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                        customers?.concentration.concentration_risk === "Low"
                          ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                          : customers?.concentration.concentration_risk === "Moderate"
                          ? "bg-amber-950 text-amber-400 border border-amber-800"
                          : "bg-rose-950 text-rose-400 border border-rose-800"
                      }`}
                    >
                      {customers?.concentration.concentration_risk} Risk
                    </span>
                  </div>

                  <div className="space-y-2 text-xs">
                    <div className="flex justify-between text-slate-400">
                      <span>{t("selling_kpi.top_5_share")}:</span>
                      <span className="font-bold text-slate-200">{customers?.concentration.top_5_share_pct}%</span>
                    </div>
                    <div className="flex justify-between text-slate-400">
                      <span>{t("selling_kpi.top_10_share")}:</span>
                      <span className="font-bold text-slate-200">{customers?.concentration.top_10_share_pct}%</span>
                    </div>
                    <div className="flex justify-between text-slate-400">
                      <span>{t("selling_kpi.pareto_rule")}:</span>
                      <span className="font-bold text-indigo-400">
                        {customers?.concentration.pareto_20_pct_customers_share}%
                      </span>
                    </div>
                    <div className="flex justify-between text-slate-400">
                      <span>{t("selling_kpi.hhi_index")}:</span>
                      <span className="font-mono text-slate-300">{customers?.concentration.hhi_index}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Top Customer Contributions Table */}
            <div className="rounded-xl border border-slate-800 bg-slate-900/90 p-5 shadow-sm lg:col-span-2 space-y-4">
              <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
                <Award className="h-4 w-4 text-cyan-400" />
                {t("selling_kpi.top_customers_title")}
              </h3>
              <p className="text-xs text-slate-400">
                {t("selling_kpi.top_customers_desc")}
              </p>

              <div className="overflow-x-auto">
                <table className="w-full text-start text-xs">
                  <thead>
                    <tr className="border-b border-slate-800 text-slate-400">
                      <th className="py-2.5 text-start font-medium">{t("selling_kpi.rank")}</th>
                      <th className="py-2.5 text-start font-medium">{t("selling_kpi.customer")}</th>
                      <th className="py-2.5 text-start font-medium">{t("selling_kpi.customer_group")}</th>
                      <th className="py-2.5 text-end font-medium">{t("selling_kpi.invoices")}</th>
                      <th className="py-2.5 text-end font-medium">{t("selling_kpi.revenue")}</th>
                      <th className="py-2.5 text-end font-medium">{t("selling_kpi.share")}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {customers?.top_customers.map((c, idx) => (
                      <tr key={c.customer_id} className="hover:bg-slate-800/30 transition-colors">
                        <td className="py-2.5 font-bold text-slate-500">#{idx + 1}</td>
                        <td className="py-2.5 font-semibold text-slate-200">{c.customer_name}</td>
                        <td className="py-2.5 text-slate-400">{c.customer_group || "—"}</td>
                        <td className="py-2.5 text-end font-mono text-slate-300">{c.invoices_count}</td>
                        <td className="py-2.5 text-end font-bold font-mono text-slate-100">
                          {formatIndianCurrency(c.revenue)}
                        </td>
                        <td className="py-2.5 text-end font-mono text-indigo-400 font-semibold">
                          {c.revenue_share_pct}%
                        </td>
                      </tr>
                    ))}
                    {(!customers?.top_customers || customers.top_customers.length === 0) && (
                      <tr>
                        <td colSpan={6} className="py-8 text-center text-slate-500 text-xs">
                          {t("selling_kpi.no_customers_found")}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* Tab 5: Receivables Ageing & DSO Analysis */}
        {(activeTab === "overview" || activeTab === "receivables") && (
          <div className="rounded-xl border border-slate-800 bg-slate-900/90 p-5 shadow-sm space-y-5">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between pb-3 border-b border-slate-800">
              <div>
                <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
                  <Clock className="h-4 w-4 text-rose-400" />
                  {t("selling_kpi.receivables_ageing_title")}
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  {t("selling_kpi.receivables_ageing_desc")}
                </p>
              </div>

              <div className="flex items-center gap-4 text-xs font-mono">
                <span className="text-slate-400">{t("selling_kpi.as_of")}: {receivables?.as_of_date}</span>
                <span className="text-slate-500">|</span>
                <span className="text-slate-300 font-bold">
                  {t("selling_kpi.total")}: {formatIndianCurrency(receivables?.total_outstanding_receivables ?? "0.00")}
                </span>
              </div>
            </div>

            {/* 4 Ageing Bucket Cards */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {/* 0 - 30 Days */}
              <div className="rounded-xl border border-emerald-900/40 bg-emerald-950/20 p-4">
                <div className="flex items-center justify-between text-xs text-emerald-400 font-semibold uppercase">
                  <span>{t("selling_kpi.ageing_0_30")}</span>
                  <span className="rounded-full bg-emerald-900/50 px-2 py-0.5 text-[10px]">
                    {receivables?.ageing_summary.current_0_30.count ?? 0} {t("selling_kpi.bills")}
                  </span>
                </div>
                <div className="mt-3 text-lg font-bold text-slate-100 tabular-nums">
                  {formatIndianCurrency(receivables?.ageing_summary.current_0_30.amount ?? "0.00")}
                </div>
                <div className="mt-2 text-xs text-emerald-300">
                  {receivables?.ageing_summary.current_0_30.share_pct ?? 0}% {t("selling_kpi.of_total")}
                </div>
              </div>

              {/* 31 - 60 Days */}
              <div className="rounded-xl border border-amber-900/40 bg-amber-950/20 p-4">
                <div className="flex items-center justify-between text-xs text-amber-400 font-semibold uppercase">
                  <span>{t("selling_kpi.ageing_31_60")}</span>
                  <span className="rounded-full bg-amber-900/50 px-2 py-0.5 text-[10px]">
                    {receivables?.ageing_summary.overdue_31_60.count ?? 0} {t("selling_kpi.bills")}
                  </span>
                </div>
                <div className="mt-3 text-lg font-bold text-slate-100 tabular-nums">
                  {formatIndianCurrency(receivables?.ageing_summary.overdue_31_60.amount ?? "0.00")}
                </div>
                <div className="mt-2 text-xs text-amber-300">
                  {receivables?.ageing_summary.overdue_31_60.share_pct ?? 0}% {t("selling_kpi.of_total")}
                </div>
              </div>

              {/* 61 - 90 Days */}
              <div className="rounded-xl border border-orange-900/40 bg-orange-950/20 p-4">
                <div className="flex items-center justify-between text-xs text-orange-400 font-semibold uppercase">
                  <span>{t("selling_kpi.ageing_61_90")}</span>
                  <span className="rounded-full bg-orange-900/50 px-2 py-0.5 text-[10px]">
                    {receivables?.ageing_summary.overdue_61_90.count ?? 0} {t("selling_kpi.bills")}
                  </span>
                </div>
                <div className="mt-3 text-lg font-bold text-slate-100 tabular-nums">
                  {formatIndianCurrency(receivables?.ageing_summary.overdue_61_90.amount ?? "0.00")}
                </div>
                <div className="mt-2 text-xs text-orange-300">
                  {receivables?.ageing_summary.overdue_61_90.share_pct ?? 0}% {t("selling_kpi.of_total")}
                </div>
              </div>

              {/* 91+ Days */}
              <div className="rounded-xl border border-rose-900/40 bg-rose-950/20 p-4">
                <div className="flex items-center justify-between text-xs text-rose-400 font-semibold uppercase">
                  <span>{t("selling_kpi.ageing_91_plus")}</span>
                  <span className="rounded-full bg-rose-900/50 px-2 py-0.5 text-[10px]">
                    {receivables?.ageing_summary.overdue_91_plus.count ?? 0} {t("selling_kpi.bills")}
                  </span>
                </div>
                <div className="mt-3 text-lg font-bold text-slate-100 tabular-nums">
                  {formatIndianCurrency(receivables?.ageing_summary.overdue_91_plus.amount ?? "0.00")}
                </div>
                <div className="mt-2 text-xs text-rose-300">
                  {receivables?.ageing_summary.overdue_91_plus.share_pct ?? 0}% {t("selling_kpi.of_total")}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
};
