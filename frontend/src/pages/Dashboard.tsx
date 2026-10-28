import React, { useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  ArrowDownLeft,
  ArrowUpRight,
  Boxes,
  CheckCircle2,
  Clock,
  FilePlus2,
  Loader2,
  PackagePlus,
  RefreshCw,
  ShieldAlert,
  ShoppingBag,
  ShoppingCart,
  TrendingDown,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import { Layout } from "@/components/Layout";
import { apiClient } from "@/api/client";
import { fetchCompanies } from "@/api/lookups";
import { normaliseError } from "@/api/endpoints";
import { formatIndianCurrency, formatIndianNumber } from "@/lib/format";
import { firstListRoute, listRoute, newRoute } from "@/resources/links";
import type { DashboardData, DashboardIntegrity, DraftKey } from "@/types/dashboard";
import { Select } from "@/components/ui/Select";

/* ------------------------------------------------------------------ */
/* Data                                                                */
/* ------------------------------------------------------------------ */

async function fetchDashboard(company?: number): Promise<DashboardData> {
  const res = await apiClient.get<DashboardData>("/core/dashboard/", {
    params: company ? { company } : undefined,
  });
  return res.data;
}

/** Decimal string -> display. The API sends the company currency; INR uses the Indian grouping formatter. */
function formatMoney(value: string, currency: string | undefined): string {
  if (!currency || currency === "INR") return formatIndianCurrency(value);
  const body = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(
    Number(value)
  );
  return `${currency} ${body}`;
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" });
}

/** Where each draft count links to, and the i18n key of its label. */
const DRAFT_SOURCES: Array<{ key: DraftKey; module: string; resource: string }> = [
  { key: "sales_orders", module: "selling", resource: "sales-orders" },
  { key: "sales_invoices", module: "selling", resource: "sales-invoices" },
  { key: "purchase_orders", module: "buying", resource: "purchase-orders" },
  { key: "purchase_invoices", module: "buying", resource: "purchase-invoices" },
  { key: "stock_entries", module: "stock", resource: "stock-entries" },
  { key: "journal_entries", module: "accounts", resource: "journal-entries" },
  { key: "payment_entries", module: "accounts", resource: "payment-entries" },
];

const QUICK_CREATE: Array<{ labelKey: string; module: string; resource: string; icon: LucideIcon }> = [
  { labelKey: "dash.quick.sales_order", module: "selling", resource: "sales-orders", icon: ShoppingCart },
  { labelKey: "dash.quick.purchase_order", module: "buying", resource: "purchase-orders", icon: ShoppingBag },
  { labelKey: "dash.quick.stock_entry", module: "stock", resource: "stock-entries", icon: PackagePlus },
  { labelKey: "dash.quick.journal_entry", module: "accounts", resource: "journal-entries", icon: FilePlus2 },
];

/* ------------------------------------------------------------------ */
/* Pieces                                                              */
/* ------------------------------------------------------------------ */

interface KpiProps {
  title: string;
  value: string;
  hint: string;
  icon: LucideIcon;
  color: string;
  to: string | null;
}

const KpiCard: React.FC<KpiProps> = ({ title, value, hint, icon: Icon, color, to }) => {
  const { t } = useTranslation();
  const body = (
    <>
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">{title}</span>
        <div className="p-2 rounded-lg bg-slate-950 border border-slate-800 shrink-0">
          <Icon className={`w-4 h-4 ${color}`} />
        </div>
      </div>
      <div>
        <h3 className="text-xl font-semibold text-slate-100 tabular-nums break-words">{value}</h3>
        <span className="text-[11px] font-medium text-slate-400">{hint}</span>
      </div>
    </>
  );
  const base = "block bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-xl space-y-3";
  return to ? (
    <Link
      to={to}
      aria-label={`${title}: ${t("dash.view_list")}`}
      className={`${base} hover:border-indigo-500/60 hover:bg-slate-900/80 transition-colors focus:outline-none focus:border-indigo-500`}
    >
      {body}
    </Link>
  ) : (
    <div className={base}>{body}</div>
  );
};

const INTEGRITY_STYLE: Record<string, { box: string; icon: LucideIcon }> = {
  ok: { box: "bg-emerald-950/60 text-emerald-300 border-emerald-800/60", icon: CheckCircle2 },
  healed: { box: "bg-amber-950/40 text-amber-300 border-amber-800/50", icon: AlertTriangle },
  drift: { box: "bg-rose-950/60 text-rose-300 border-rose-800/60", icon: ShieldAlert },
  alert: { box: "bg-rose-950/60 text-rose-300 border-rose-800/60", icon: ShieldAlert },
  never_run: { box: "bg-slate-800/60 text-slate-300 border-slate-700", icon: Clock },
};

const IntegrityPanel: React.FC<{ integrity: DashboardIntegrity }> = ({ integrity }) => {
  const { t } = useTranslation();
  const known = integrity.status in INTEGRITY_STYLE;
  const style = INTEGRITY_STYLE[integrity.status] ?? {
    box: "bg-amber-950/40 text-amber-300 border-amber-800/50",
    icon: AlertTriangle,
  };
  const Icon = style.icon;
  const label = known
    ? t(`dash.integrity.status_${integrity.status}`)
    : t("dash.integrity.status_unknown", { status: integrity.status });

  const { message, error_count, errors } = integrity.details;
  const shownErrors = Array.isArray(errors) ? errors : [];

  return (
    <section className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
        <h2 className="text-base font-semibold text-slate-100">{t("dash.integrity.title")}</h2>
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${style.box}`}>
          <Icon className="w-3.5 h-3.5" />
          {label}
        </span>
      </div>

      <div className="text-xs text-slate-400 space-y-1">
        {integrity.checked_at ? (
          <p>{t("dash.integrity.last_checked", { when: formatDateTime(integrity.checked_at) })}</p>
        ) : (
          <p>{t("dash.integrity.never_run_hint")}</p>
        )}
        {integrity.checked_at && integrity.check_name && (
          <p className="text-slate-500">{t("dash.integrity.check_name", { name: integrity.check_name })}</p>
        )}
        {typeof message === "string" && message && <p>{t("dash.integrity.message", { message })}</p>}
      </div>

      {typeof error_count === "number" && error_count > 0 && (
        <div className="text-xs space-y-2">
          <p className="font-semibold text-rose-300">{t("dash.integrity.error_count", { count: error_count })}</p>
          {shownErrors.length > 0 && (
            <>
              <ul className="list-disc ps-5 space-y-1 text-slate-300 break-words">
                {shownErrors.map((e, i) => (
                  <li key={i}>{typeof e === "string" ? e : JSON.stringify(e)}</li>
                ))}
              </ul>
              {error_count > shownErrors.length && (
                <p className="text-slate-500">{t("dash.integrity.showing_first", { count: shownErrors.length })}</p>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
};

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export const Dashboard: React.FC = () => {
  const { t } = useTranslation();
  // undefined = let the server pick its default company.
  const [companyId, setCompanyId] = useState<number | undefined>(undefined);

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["dashboard", companyId ?? "default"],
    queryFn: () => fetchDashboard(companyId),
    placeholderData: keepPreviousData,
  });

  const { data: companies } = useQuery({
    queryKey: ["companies"],
    queryFn: () => fetchCompanies(),
    staleTime: 5 * 60 * 1000,
  });

  const err = error ? normaliseError(error) : null;
  const company = data?.company ?? null;
  const currency = company?.currency;
  const money = (v: string) => formatMoney(v, currency);

  const kpis: KpiProps[] = [];
  if (data) {
    if (data.total_sales !== null)
      kpis.push({
        title: t("dash.kpi.total_sales"),
        value: money(data.total_sales),
        hint: t("dash.kpi.total_sales_hint"),
        icon: TrendingUp,
        color: "text-emerald-400",
        to: listRoute("selling", "sales-invoices", "docstatus=1"),
      });
    if (data.total_purchases !== null)
      kpis.push({
        title: t("dash.kpi.total_purchases"),
        value: money(data.total_purchases),
        hint: t("dash.kpi.total_purchases_hint"),
        icon: TrendingDown,
        color: "text-sky-400",
        to: listRoute("buying", "purchase-invoices", "docstatus=1"),
      });
    if (data.receivables_outstanding !== null)
      kpis.push({
        title: t("dash.kpi.receivables"),
        value: money(data.receivables_outstanding),
        hint: t("dash.kpi.receivables_hint"),
        icon: ArrowDownLeft,
        color: "text-amber-400",
        to: listRoute("selling", "sales-invoices", "docstatus=1"),
      });
    if (data.payables_outstanding !== null)
      kpis.push({
        title: t("dash.kpi.payables"),
        value: money(data.payables_outstanding),
        hint: t("dash.kpi.payables_hint"),
        icon: ArrowUpRight,
        color: "text-rose-400",
        to: listRoute("buying", "purchase-invoices", "docstatus=1"),
      });
    if (data.open_sales_orders !== null)
      kpis.push({
        title: t("dash.kpi.open_orders"),
        value: money(data.open_sales_orders.value),
        hint: t("dash.kpi.open_orders_hint", { count: data.open_sales_orders.count }),
        icon: ShoppingCart,
        color: "text-indigo-400",
        to: listRoute("selling", "sales-orders", "docstatus=1"),
      });
    if (data.stock_value !== null)
      kpis.push({
        title: t("dash.kpi.stock_value"),
        value: money(data.stock_value),
        hint: t("dash.kpi.stock_value_hint"),
        icon: Boxes,
        color: "text-purple-400",
        to: firstListRoute([
          ["stock", "bins"],
          ["stock", "items"],
        ]),
      });
  }

  const drafts = data?.draft_documents ?? null;
  const draftRows = drafts
    ? DRAFT_SOURCES.filter((s) => (drafts[s.key] ?? 0) > 0).map((s) => ({
        ...s,
        count: drafts[s.key] as number,
        to: listRoute(s.module, s.resource, "docstatus=0"),
      }))
    : [];

  const quick = QUICK_CREATE.map((q) => ({ ...q, to: newRoute(q.module, q.resource) })).filter((q) => q.to);

  const hasAnySection = !!data && (kpis.length > 0 || drafts !== null || data.integrity !== null);

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-slate-100 tracking-tight">{t("dash.title")}</h1>
            <p className="text-slate-400 text-xs mt-1">{t("dash.subtitle")}</p>
          </div>
          <div className="flex items-center gap-3">
            {companies && companies.length > 1 ? (
              <label className="flex items-center gap-2 text-xs text-slate-400">
                <span>{t("dash.company")}</span>
                <Select
                  value={String(company?.id ?? companyId ?? "")}
                  onChange={(v) => setCompanyId(Number(v))}
                  className="w-56"
                  options={companies.map((c) => ({ value: String(c.id), label: c.name }))}
                />
              </label>
            ) : (
              company && (
                <span className="text-xs text-slate-400">
                  {t("dash.company")}: <span className="font-semibold text-slate-200">{company.name}</span>
                </span>
              )
            )}
            <button
              type="button"
              onClick={() => refetch()}
              disabled={isFetching}
              title={t("dash.refresh")}
              aria-label={t("dash.refresh")}
              className="p-2 rounded-lg border border-slate-800 bg-slate-900 text-slate-300 hover:text-white hover:border-slate-700 disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${isFetching ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>

        {isLoading && (
          <div className="flex items-center gap-2 text-sm text-slate-400" role="status">
            <Loader2 className="w-4 h-4 animate-spin" />
            {t("dash.loading")}
          </div>
        )}

        {err && !data && (
          <div className="p-4 rounded-xl bg-rose-950/60 border border-rose-800 text-rose-200 text-sm flex flex-wrap items-center justify-between gap-3" role="alert">
            <span>{err.forbidden ? t("common.forbidden") : t("dash.load_failed")}</span>
            {!err.forbidden && (
              <button
                type="button"
                onClick={() => refetch()}
                className="px-3 py-1.5 rounded-lg border border-rose-700 text-rose-100 hover:bg-rose-900/60 text-xs font-medium"
              >
                {t("dash.retry")}
              </button>
            )}
          </div>
        )}

        {data && !company && (
          <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 text-slate-300 text-sm">
            {t("dash.no_company")}
          </div>
        )}

        {data && company && !hasAnySection && (
          <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 text-slate-300 text-sm">
            {t("dash.no_sections")}
          </div>
        )}

        {/* KPI cards */}
        {kpis.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
            {kpis.map((kpi) => (
              <KpiCard key={kpi.title} {...kpi} />
            ))}
          </div>
        )}

        {/* Quick create */}
        {company && quick.length > 0 && (
          <section className="space-y-3">
            <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider">{t("dash.quick.title")}</h2>
            <div className="flex flex-wrap gap-3">
              {quick.map((q) => {
                const Icon = q.icon;
                return (
                  <Link
                    key={q.labelKey}
                    to={q.to as string}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-slate-800 bg-slate-900 hover:border-indigo-700 hover:text-indigo-300 text-slate-200 text-sm font-medium shadow-sm transition-colors"
                  >
                    <Icon className="w-4 h-4 text-indigo-400" />
                    {t(q.labelKey)}
                  </Link>
                );
              })}
            </div>
          </section>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {/* Draft documents */}
          {drafts !== null && (
            <section className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-xl space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
                <h2 className="text-base font-semibold text-slate-100">{t("dash.drafts.title")}</h2>
                {drafts.total > 0 && (
                  <span className="text-xs text-slate-400">
                    {t("dash.drafts.total", { count: drafts.total })}
                  </span>
                )}
              </div>
              {draftRows.length === 0 ? (
                <p className="text-sm text-slate-400">{t("dash.drafts.none")}</p>
              ) : (
                <ul className="divide-y divide-slate-800">
                  {draftRows.map((row) => {
                    const content = (
                      <>
                        <span className="text-sm text-slate-200">{t(`dash.drafts.${row.key}`)}</span>
                        <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold tabular-nums bg-amber-950/40 text-amber-300 border border-amber-800/50">
                          {formatIndianNumber(row.count)}
                        </span>
                      </>
                    );
                    return (
                      <li key={row.key}>
                        {row.to ? (
                          <Link to={row.to} className="flex items-center justify-between gap-3 py-2.5 hover:bg-slate-800/40 rounded-md px-1">
                            {content}
                          </Link>
                        ) : (
                          <div className="flex items-center justify-between gap-3 py-2.5 px-1">{content}</div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          )}

          {/* Integrity */}
          {data?.integrity && <IntegrityPanel integrity={data.integrity} />}
        </div>
      </div>
    </Layout>
  );
};
