import React from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowRight, Building2, ChevronRight, Clock3, LayoutGrid } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Layout } from "@/components/Layout";
import { useAuth } from "@/hooks/useAuth";
import { useCompany, type CompanySummary } from "@/context/CompanyContext";
import { apiClient } from "@/api/client";
import { hasReadAccess } from "@/resources/workspaces";
import type { DashboardData } from "@/types/dashboard";

/** True when the caller may read at least one of the model labels (`app.Model`). */
export function useCanRead(labels: string[]): boolean {
  const { user } = useAuth();
  return hasReadAccess(user?.readable_models, labels);
}

/**
 * Draft and open-order counts from GET /core/dashboard/. Shares its query key
 * with the module launcher, so both screens read one cached response.
 */
export function useDashboardCounts(enabled: boolean) {
  const { activeCompany } = useCompany();
  return useQuery({
    queryKey: ["home", "pending-work", activeCompany?.id ?? null],
    queryFn: async () => (await apiClient.get<DashboardData>("/core/dashboard/")).data,
    staleTime: 30_000,
    retry: false,
    enabled: !!activeCompany && enabled,
  });
}

/** Text for a count tile: a dash while loading or on error, never a fake zero. */
export function countText(query: { isLoading: boolean; isError: boolean; data?: number }): string {
  if (query.isLoading) return "…";
  if (query.isError || query.data === undefined) return "–";
  return String(query.data);
}

const CompanyChooser: React.FC<{
  companies: CompanySummary[];
  onSelect: (company: CompanySummary) => void;
}> = ({ companies, onSelect }) => {
  const { t } = useTranslation();
  return (
    <Layout>
      <div className="mx-auto max-w-3xl space-y-6 py-6">
        <header className="text-center">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-indigo-400">
            {t("company.eyebrow")}
          </p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-100">
            {t("company.choose_title")}
          </h1>
          <p className="mx-auto mt-2 max-w-xl text-sm text-slate-400">
            {t("company.choose_body")}
          </p>
        </header>

        <div className="grid gap-3 sm:grid-cols-2">
          {companies.map((company) => (
            <button
              key={company.id}
              type="button"
              onClick={() => onSelect(company)}
              className="group flex items-center gap-4 rounded-2xl border border-slate-800 bg-slate-900 p-5 text-start shadow-sm transition-colors hover:border-indigo-700/70 hover:bg-slate-900/80 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
            >
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-indigo-900/50 bg-indigo-950/50 text-indigo-300">
                <Building2 className="h-5 w-5" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-semibold text-slate-100">{company.name}</span>
                <span className="mt-0.5 block truncate text-xs text-slate-400">
                  {[company.abbr, company.gstin].filter(Boolean).join(" · ")}
                </span>
              </span>
              <ChevronRight className="h-4 w-4 shrink-0 text-slate-600 transition-transform group-hover:translate-x-0.5 group-hover:text-indigo-400 rtl:rotate-180" />
            </button>
          ))}
        </div>

        <p className="text-center text-xs text-slate-500">{t("company.choose_footer")}</p>
      </div>
    </Layout>
  );
};

/**
 * Renders `children` once a company is active. Otherwise shows the loading,
 * error, company chooser or no-company state, so every home screen shares them.
 */
export const CompanyGate: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { t } = useTranslation();
  const { companies, activeCompany, loading, error, selectCompany } = useCompany();

  if (loading) {
    return (
      <Layout>
        <div className="flex min-h-[60vh] items-center justify-center gap-2 text-sm text-slate-400" role="status">
          <Clock3 className="h-4 w-4 animate-pulse text-slate-500" />
          {t("company.loading")}
        </div>
      </Layout>
    );
  }

  if (error) {
    return (
      <Layout>
        <div className="mx-auto mt-24 max-w-lg rounded-2xl border border-rose-900/50 bg-rose-950/20 p-6 text-center text-sm text-rose-300">
          {t("company.load_failed")}
        </div>
      </Layout>
    );
  }

  if (!activeCompany && companies.length > 0) {
    return <CompanyChooser companies={companies} onSelect={selectCompany} />;
  }

  if (!activeCompany) {
    return (
      <Layout>
        <div className="mx-auto mt-24 max-w-lg rounded-2xl border border-dashed border-slate-800 bg-slate-900/40 p-8 text-center">
          <Building2 className="mx-auto h-8 w-8 text-slate-500" />
          <h1 className="mt-3 text-base font-semibold text-slate-100">{t("company.none_title")}</h1>
          <p className="mt-1 text-sm text-slate-400">{t("company.none_body")}</p>
        </div>
      </Layout>
    );
  }

  return <>{children}</>;
};

/**
 * Page chrome for a workspace: the working company, a link to the module
 * launcher, and the workspace title. Content sits in the 16px phone gutter
 * (Layout already adds 12px).
 */
export const WorkspaceFrame: React.FC<{
  title: string;
  subtitle: string;
  children: React.ReactNode;
}> = ({ title, subtitle, children }) => {
  const { t } = useTranslation();
  const { activeCompany, companies, clearActiveCompany } = useCompany();
  return (
    <Layout>
      <div className="space-y-6 px-1 sm:space-y-8 sm:px-0">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="min-w-0 truncate text-xs text-slate-400">
            {t("company.working_in")}{" "}
            <span className="font-semibold text-slate-200">{activeCompany?.name}</span>
          </p>
          <div className="flex items-center gap-2">
            {companies.length > 1 && (
              <button
                type="button"
                onClick={clearActiveCompany}
                className="min-h-[44px] rounded-lg border border-slate-700 px-3 text-xs font-medium text-slate-300 transition-colors hover:border-indigo-700 hover:text-indigo-300"
              >
                {t("company.switch")}
              </button>
            )}
            <Link
              to="/all-modules"
              className="inline-flex min-h-[44px] items-center gap-1.5 rounded-lg border border-slate-700 px-3 text-xs font-medium text-slate-300 transition-colors hover:border-indigo-700 hover:text-indigo-300"
            >
              <LayoutGrid className="h-4 w-4" />
              {t("home.ws.all_modules")}
            </Link>
          </div>
        </div>

        <header>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-100">{title}</h1>
          <p className="mt-1 text-sm text-slate-400">{subtitle}</p>
        </header>

        {children}
      </div>
    </Layout>
  );
};

/** A titled group of tiles. Renders nothing when `show` is false. */
export const Section: React.FC<{ title?: string; show?: boolean; children: React.ReactNode }> = ({
  title,
  show = true,
  children,
}) => {
  if (!show) return null;
  return (
    <section className="space-y-3">
      {title && <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500">{title}</h2>}
      {children}
    </section>
  );
};

/** Grid of large tap targets: two columns on a phone, three from 640px. */
export const TileGrid: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">{children}</div>
);

/** A link card with a large tap target, an optional value and a short hint. */
export const Tile: React.FC<{
  to: string;
  icon: LucideIcon;
  label: string;
  value?: string;
  hint?: string;
}> = ({ to, icon: Icon, label, value, hint }) => (
  <Link
    to={to}
    className="group flex min-h-[120px] min-w-0 flex-col justify-between rounded-2xl border border-slate-800 bg-slate-900 p-4 shadow-sm transition-colors hover:border-indigo-700 active:bg-slate-800"
  >
    <div className="flex items-start justify-between gap-2">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-950 text-indigo-400">
        <Icon className="h-5 w-5" />
      </span>
      <ArrowRight className="h-4 w-4 shrink-0 text-slate-600 transition-transform group-hover:translate-x-0.5 group-hover:text-indigo-400 rtl:rotate-180" />
    </div>
    <div className="mt-3 min-w-0">
      <p className="truncate text-sm font-medium text-slate-300">{label}</p>
      {value !== undefined && (
        <p className="mt-0.5 truncate text-xl font-semibold tabular-nums text-slate-100 sm:text-2xl">{value}</p>
      )}
      {hint && <p className="mt-0.5 truncate text-xs text-slate-500">{hint}</p>}
    </div>
  </Link>
);

/** Shown when a workspace has nothing the caller may read. */
export const EmptyWorkspace: React.FC = () => {
  const { t } = useTranslation();
  return (
    <p className="rounded-xl border border-dashed border-slate-800 bg-slate-900/40 px-4 py-8 text-center text-sm text-slate-500">
      {t("home.ws.nothing")}
    </p>
  );
};
