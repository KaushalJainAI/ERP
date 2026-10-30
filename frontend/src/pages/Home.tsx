import React, { useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowRight, ArrowUpRight, Building2, CheckCircle2, ChevronRight, Clock3, ShoppingCart } from "lucide-react";
import { Layout } from "@/components/Layout";
import { useAuth } from "@/hooks/useAuth";
import { useCompany, type CompanySummary } from "@/context/CompanyContext";
import { apiClient } from "@/api/client";
import {
  hasReadAccess,
  PENDING_WORK,
  visibleWorkspaces,
  type WorkspaceDef,
} from "@/resources/workspaces";
import type { DashboardData } from "@/types/dashboard";

/** The home page is a launcher: it shows only the workspaces the caller's
 * roles can read and the drafts/open documents waiting in them. */

const WorkspaceCard: React.FC<{ workspace: WorkspaceDef; pending: number }> = ({ workspace, pending }) => {
  const { t } = useTranslation();
  const Icon = workspace.icon;
  return (
    <article className="flex flex-col justify-between rounded-xl sm:rounded-2xl border border-slate-800 bg-slate-900 p-3 sm:p-5 shadow-sm transition-colors hover:border-slate-700">
      <div>
        <div className="flex items-start justify-between gap-2">
          <span className={`flex h-8 w-8 sm:h-11 sm:w-11 items-center justify-center rounded-lg sm:rounded-xl border ${workspace.color}`}>
            <Icon className="h-4 w-4 sm:h-5 sm:w-5" />
          </span>
          {pending > 0 && (
            <span className="rounded-full border border-amber-800/50 bg-amber-950/40 px-1.5 sm:px-2.5 py-0.5 text-[10px] sm:text-xs font-semibold tabular-nums text-amber-300">
              {pending}
            </span>
          )}
        </div>

        <h3 className="mt-2.5 sm:mt-4 text-xs sm:text-base font-semibold text-slate-100 truncate">
          {t(workspace.titleKey)}
        </h3>
        <p className="hidden sm:block mt-1 text-xs leading-relaxed text-slate-400 line-clamp-2">
          {t(workspace.descriptionKey)}
        </p>

        <ul className="mt-2 sm:mt-4 space-y-1 sm:space-y-1.5">
          {workspace.links.map((link) => {
            const LinkIcon = link.icon;
            return (
              <li key={link.path}>
                <Link
                  to={link.path}
                  className="group/link flex items-center gap-1.5 sm:gap-2 rounded-lg px-1.5 py-1 sm:px-2 sm:py-1.5 text-xs sm:text-[13px] text-slate-300 hover:bg-slate-800/60 hover:text-white"
                >
                  <LinkIcon className="h-3 w-3 sm:h-3.5 sm:w-3.5 shrink-0 text-slate-500 group-hover/link:text-indigo-400" />
                  <span className="truncate">{t(link.labelKey)}</span>
                  <ArrowUpRight className="ms-auto h-3 w-3 sm:h-3.5 sm:w-3.5 shrink-0 text-slate-600 opacity-0 transition-opacity group-hover/link:opacity-100" />
                </Link>
              </li>
            );
          })}
        </ul>
      </div>

      <Link
        to={`/${workspace.key}`}
        className="mt-3 sm:mt-4 flex items-center gap-1 sm:gap-1.5 border-t border-slate-800/80 pt-2 sm:pt-3 text-[11px] sm:text-xs font-medium text-indigo-400 hover:text-indigo-300"
      >
        <span className="truncate">{t("home.open_workspace", { name: t(workspace.titleKey) })}</span>
        <ArrowRight className="h-3 w-3 sm:h-3.5 sm:w-3.5 shrink-0 rtl:rotate-180" />
      </Link>
    </article>
  );
};

/** Compact phone tile: the workspace page is the second level, so the home grid stays scannable. */
const WorkspaceTile: React.FC<{ workspace: WorkspaceDef; pending: number }> = ({ workspace, pending }) => {
  const { t } = useTranslation();
  const Icon = workspace.icon;
  return (
    <Link
      to={`/${workspace.key}`}
      className="group flex min-h-[96px] min-w-0 flex-col justify-between rounded-xl border border-slate-800 bg-slate-900 p-3 shadow-sm transition-colors hover:border-slate-700"
    >
      <div className="flex items-start justify-between gap-2">
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border ${workspace.color}`}>
          <Icon className="h-4 w-4" />
        </span>
        {pending > 0 && (
          <span className="rounded-full border border-amber-800/50 bg-amber-950/40 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-amber-300">
            {pending}
          </span>
        )}
      </div>
      <div className="mt-2 flex min-w-0 items-center justify-between gap-1">
        <span className="min-w-0 truncate text-xs font-semibold text-slate-100">{t(workspace.titleKey)}</span>
        <ArrowRight className="h-3.5 w-3.5 shrink-0 text-slate-600 transition-transform group-hover:translate-x-0.5 group-hover:text-indigo-400 rtl:rotate-180" />
      </div>
    </Link>
  );
};

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

export const Home: React.FC = () => {
  const { t } = useTranslation();
  const { user } = useAuth();
  const {
    companies,
    activeCompany,
    loading: companyLoading,
    error: companyError,
    selectCompany,
    clearActiveCompany,
  } = useCompany();

  const readableModels = user?.readable_models;
  const workspaces = useMemo(() => visibleWorkspaces(readableModels), [readableModels]);

  const canSeeDrafts = PENDING_WORK.some((row) => hasReadAccess(readableModels, row.access));
  const canSeeOpenOrders = hasReadAccess(readableModels, ["selling.SalesOrder"]);
  const canSeePendingWork = canSeeDrafts || canSeeOpenOrders;

  // The dashboard endpoint stays as the single source of pending counts; the
  // home page ignores its KPI fields. Users with only master-data access are
  // not allowed to call it, so they skip the request and hide the section.
  const { data, isLoading, isError } = useQuery({
    queryKey: ["home", "pending-work", activeCompany?.id ?? null],
    queryFn: async () => (await apiClient.get<DashboardData>("/core/dashboard/")).data,
    staleTime: 30_000,
    retry: false,
    enabled: !!activeCompany && canSeePendingWork,
  });

  const drafts = data?.draft_documents;
  const openOrders = data?.open_sales_orders;

  const pendingRows = PENDING_WORK.filter((row) => hasReadAccess(readableModels, row.access))
    .map((row) => ({ ...row, count: drafts?.[row.key] ?? 0 }))
    .filter((row) => row.count > 0);
  const openOrdersCount = canSeeOpenOrders ? (openOrders?.count ?? 0) : 0;
  const pendingTotal = pendingRows.reduce((sum, row) => sum + row.count, 0) + openOrdersCount;

  const pendingFor = (workspaceKey: string) => {
    let count = 0;
    for (const row of PENDING_WORK) {
      if (row.workspace === workspaceKey && hasReadAccess(readableModels, row.access)) {
        count += drafts?.[row.key] ?? 0;
      }
    }
    if (workspaceKey === "selling") count += openOrdersCount;
    return count;
  };

  const displayName = [user?.first_name, user?.last_name].filter(Boolean).join(" ") || user?.username || "";
  const roleLabels = [
    ...(user?.is_superuser ? [t("home.administrator")] : []),
    ...(user?.roles ?? []),
  ];

  if (companyLoading) {
    return (
      <Layout>
        <div className="flex min-h-[60vh] items-center justify-center gap-2 text-sm text-slate-400" role="status">
          <Clock3 className="h-4 w-4 animate-pulse text-slate-500" />
          {t("company.loading")}
        </div>
      </Layout>
    );
  }

  if (companyError) {
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

  return (
    <Layout>
      <div className="space-y-5 sm:space-y-8">
        <div className="flex flex-wrap items-center justify-between gap-2.5 sm:gap-3 rounded-xl sm:rounded-2xl border border-slate-800 bg-slate-900 px-3.5 py-3 sm:px-5 sm:py-4">
          <div className="flex min-w-0 items-center gap-2.5 sm:gap-3">
            <span className="flex h-8 w-8 sm:h-10 sm:w-10 shrink-0 items-center justify-center rounded-lg sm:rounded-xl border border-indigo-900/50 bg-indigo-950/50 text-indigo-300">
              <Building2 className="h-4 w-4 sm:h-5 sm:w-5" />
            </span>
            <div className="min-w-0">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                {t("company.working_in")}
              </p>
              <p className="truncate text-xs sm:text-sm font-semibold text-slate-100">{activeCompany.name}</p>
              <p className="truncate text-[10px] sm:text-[11px] text-slate-400">
                {[activeCompany.abbr, activeCompany.gstin].filter(Boolean).join(" · ")}
              </p>
            </div>
          </div>
          {companies.length > 1 && (
            <button
              type="button"
              onClick={clearActiveCompany}
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:border-indigo-700 hover:text-indigo-300"
            >
              {t("company.switch")}
            </button>
          )}
        </div>

        <header>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-indigo-400">
            {t("home.eyebrow")}
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-100">
            {t("home.welcome", { name: displayName })}
          </h1>
          <p className="mt-1 text-sm text-slate-400">{t("home.subtitle")}</p>

          {roleLabels.length > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                {t("home.roles")}
              </span>
              {roleLabels.map((role) => (
                <span
                  key={role}
                  className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-0.5 text-[11px] font-medium text-slate-300"
                >
                  {role}
                </span>
              ))}
            </div>
          )}
        </header>

        {workspaces.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-800 bg-slate-900/40 p-8 text-center">
            <h2 className="text-base font-semibold text-slate-100">{t("home.empty_title")}</h2>
            <p className="mx-auto mt-1 max-w-lg text-sm text-slate-400">{t("home.empty_body")}</p>
          </div>
        ) : (
          <section className="space-y-3">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              {t("home.workspaces")}
            </h2>
            <div className="grid grid-cols-2 gap-2.5 sm:hidden">
              {workspaces.map((workspace) => (
                <WorkspaceTile key={workspace.key} workspace={workspace} pending={pendingFor(workspace.key)} />
              ))}
            </div>
            <div className="hidden gap-4 sm:grid sm:grid-cols-2 xl:grid-cols-3">
              {workspaces.map((workspace) => (
                <WorkspaceCard
                  key={workspace.key}
                  workspace={workspace}
                  pending={pendingFor(workspace.key)}
                />
              ))}
            </div>
          </section>
        )}

        {canSeePendingWork && (
          <section className="overflow-hidden rounded-xl sm:rounded-2xl border border-slate-800 bg-slate-900 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2.5 sm:gap-3 border-b border-slate-800 px-3.5 py-3 sm:px-5 sm:py-4">
              <div>
                <h2 className="text-sm sm:text-base font-semibold text-slate-100">{t("home.pending.title")}</h2>
                <p className="mt-0.5 text-[11px] sm:text-xs text-slate-400">{t("home.pending.subtitle")}</p>
              </div>
              {pendingTotal > 0 && (
                <span className="rounded-full border border-amber-800/50 bg-amber-950/40 px-2 sm:px-2.5 py-0.5 text-[10px] sm:text-xs font-semibold tabular-nums text-amber-300">
                  {t("home.pending.total", { count: pendingTotal })}
                </span>
              )}
            </div>

            {isLoading ? (
              <div className="flex items-center gap-2 px-4 py-4 sm:px-5 sm:py-5 text-xs sm:text-sm text-slate-400" role="status">
                <Clock3 className="h-4 w-4 animate-pulse text-slate-500" />
                {t("home.pending.loading")}
              </div>
            ) : isError ? (
              <p className="px-4 py-4 sm:px-5 sm:py-5 text-xs sm:text-sm text-slate-400">{t("home.pending.error")}</p>
            ) : data && data.company === null ? (
              <p className="px-4 py-4 sm:px-5 sm:py-5 text-xs sm:text-sm text-slate-400">{t("home.pending.no_company")}</p>
            ) : pendingRows.length === 0 && openOrdersCount === 0 ? (
              <div className="flex items-center gap-2 px-4 py-4 sm:px-5 sm:py-5 text-xs sm:text-sm text-slate-400">
                <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                {t("home.pending.none")}
              </div>
            ) : (
              <ul className="divide-y divide-slate-800">
                {pendingRows.map((row) => {
                  const Icon = row.icon;
                  return (
                    <li key={row.key}>
                      <Link
                        to={row.path}
                        className="flex items-center gap-2.5 sm:gap-3 px-3.5 py-2.5 sm:px-5 sm:py-3 transition-colors hover:bg-slate-800/40"
                      >
                        <span className="flex h-7 w-7 sm:h-9 sm:w-9 shrink-0 items-center justify-center rounded-lg border border-slate-800 bg-slate-950 text-slate-400">
                          <Icon className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-xs sm:text-sm font-medium text-slate-200">
                            {t(row.labelKey)}
                          </span>
                          <span className="block text-[10px] sm:text-[11px] text-slate-500 truncate">{t("home.pending.draft_hint")}</span>
                        </span>
                        <span className="rounded-full border border-amber-800/50 bg-amber-950/40 px-2 py-0.5 text-[10px] sm:text-xs font-semibold tabular-nums text-amber-300">
                          {row.count}
                        </span>
                        <ArrowUpRight className="h-3.5 w-3.5 sm:h-4 sm:w-4 shrink-0 text-slate-600" />
                      </Link>
                    </li>
                  );
                })}

                {openOrdersCount > 0 && (
                  <li>
                    <Link
                      to="/selling/sales-orders?docstatus=1"
                      className="flex items-center gap-2.5 sm:gap-3 px-3.5 py-2.5 sm:px-5 sm:py-3 transition-colors hover:bg-slate-800/40"
                    >
                      <span className="flex h-7 w-7 sm:h-9 sm:w-9 shrink-0 items-center justify-center rounded-lg border border-slate-800 bg-slate-950 text-slate-400">
                        <ShoppingCart className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs sm:text-sm font-medium text-slate-200">
                          {t("home.pending.open_orders")}
                        </span>
                        <span className="block text-[10px] sm:text-[11px] text-slate-500 truncate">
                          {t("home.pending.open_orders_hint", { count: openOrdersCount })}
                        </span>
                      </span>
                      <ArrowUpRight className="h-3.5 w-3.5 sm:h-4 sm:w-4 shrink-0 text-slate-600" />
                    </Link>
                  </li>
                )}
              </ul>
            )}
          </section>
        )}
      </div>
    </Layout>
  );
};
