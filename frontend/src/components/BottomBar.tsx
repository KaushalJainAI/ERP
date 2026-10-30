import React, { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Home,
  LayoutGrid,
  ClipboardList,
  BarChart3,
  Menu,
  X,
  ChevronRight,
  ArrowRight,
} from "lucide-react";
import { useNavBadges, badgeClass } from "@/resources/navBadges";
import { useModuleNav, type ModuleNav } from "@/resources/nav";
import { visibleWorkspaces, type WorkspaceDef } from "@/resources/workspaces";
import { useAuth } from "@/hooks/useAuth";

interface BottomBarProps {
  onOpenMenu: () => void;
}

export const BottomBar: React.FC<BottomBarProps> = ({ onOpenMenu }) => {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const { user } = useAuth();
  const badges = useNavBadges();
  const nav = useModuleNav();
  const [workspacesOpen, setWorkspacesOpen] = useState(false);

  // Total badges count for pending items
  const pendingTotal = Object.values(badges).reduce((sum, count) => sum + count, 0);

  const workspaces = React.useMemo(
    () => visibleWorkspaces(user?.readable_models),
    [user?.readable_models]
  );

  const isHome = pathname === "/" || pathname === "/home";
  const isReports = pathname.includes("/reports");
  const isOrders = pathname.includes("sales-orders") || pathname.includes("/orders");

  return (
    <>
      <nav
        aria-label={t("shell.main_navigation")}
        className="md:hidden fixed bottom-0 inset-x-0 z-40 bg-slate-900/95 backdrop-blur-md border-t border-slate-800 pb-[env(safe-area-inset-bottom)]"
      >
        <div className="grid h-16 grid-cols-5 px-1">
          {/* 1. Home */}
          <Link
            to="/"
            aria-current={isHome ? "page" : undefined}
            className={`flex min-w-0 flex-col items-center justify-center gap-0.5 px-1 py-1 text-center transition-colors ${
              isHome ? "text-indigo-400 font-semibold" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <div className="relative">
              <Home className="w-5 h-5" />
              {isHome && (
                <span className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-indigo-500" />
              )}
            </div>
            <span className="max-w-full truncate text-[9px] leading-tight tracking-tight min-[360px]:text-[10px]">{t("home.title")}</span>
          </Link>

          {/* 2. Workspaces */}
          <button
            type="button"
            onClick={() => setWorkspacesOpen(true)}
            className={`flex min-w-0 flex-col items-center justify-center gap-0.5 px-1 py-1 text-center transition-colors ${
              workspacesOpen ? "text-indigo-400 font-semibold" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <div className="relative">
              <LayoutGrid className="w-5 h-5" />
            </div>
            <span className="max-w-full truncate text-[9px] leading-tight tracking-tight min-[360px]:text-[10px]">{t("home.workspaces_short")}</span>
          </button>

          {/* 3. Pending / Orders */}
          <Link
            to="/selling/sales-orders?docstatus=1"
            aria-current={isOrders ? "page" : undefined}
            className={`flex min-w-0 flex-col items-center justify-center gap-0.5 px-1 py-1 text-center transition-colors ${
              isOrders ? "text-indigo-400 font-semibold" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <div className="relative">
              <ClipboardList className="w-5 h-5" />
              {pendingTotal > 0 && (
                <span className="absolute -top-1.5 -right-2 px-1 py-0.2 bg-amber-500 text-slate-950 text-[9px] font-bold rounded-full min-w-[14px] text-center leading-tight">
                  {pendingTotal > 99 ? "99+" : pendingTotal}
                </span>
              )}
              {isOrders && (
                <span className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-indigo-500" />
              )}
            </div>
            <span className="max-w-full truncate text-[9px] leading-tight tracking-tight min-[360px]:text-[10px]">
              {t("selling.salesorder.pluralKey", "Orders")}
            </span>
          </Link>

          {/* 4. Reports */}
          <Link
            to="/accounts/reports/general-ledger"
            aria-current={isReports ? "page" : undefined}
            className={`flex min-w-0 flex-col items-center justify-center gap-0.5 px-1 py-1 text-center transition-colors ${
              isReports ? "text-indigo-400 font-semibold" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <div className="relative">
              <BarChart3 className="w-5 h-5" />
              {isReports && (
                <span className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-indigo-500" />
              )}
            </div>
            <span className="max-w-full truncate text-[9px] leading-tight tracking-tight min-[360px]:text-[10px]">
              {t("reports.title", "Reports")}
            </span>
          </Link>

          {/* 5. Menu / More */}
          <button
            type="button"
            onClick={onOpenMenu}
            className="flex min-w-0 flex-col items-center justify-center gap-0.5 px-1 py-1 text-center text-slate-400 transition-colors hover:text-slate-200"
          >
            <Menu className="w-5 h-5" />
            <span className="max-w-full truncate text-[9px] leading-tight tracking-tight min-[360px]:text-[10px]">{t("shell.menu")}</span>
          </button>
        </div>
      </nav>

      {/* Quick Workspaces Bottom Sheet */}
      {workspacesOpen && (
        <div
          className="fixed inset-0 z-50 flex flex-col justify-end bg-black/60 backdrop-blur-sm md:hidden"
          onClick={() => setWorkspacesOpen(false)}
        >
          <div
            className="max-h-[75vh] overflow-y-auto rounded-t-2xl border-t border-slate-800 bg-slate-900 p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] shadow-2xl animate-fade-up"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="w-12 h-1.5 bg-slate-700 rounded-full mx-auto mb-3" />
            <div className="flex items-center justify-between pb-3 border-b border-slate-800 mb-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-100">{t("home.workspaces")}</h3>
                <p className="text-xs text-slate-400">{t("home.workspaces_hint")}</p>
              </div>
              <button
                type="button"
                onClick={() => setWorkspacesOpen(false)}
                className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2.5">
              {workspaces.map((ws) => {
                const Icon = ws.icon;
                return (
                  <Link
                    key={ws.key}
                    to={`/${ws.key}`}
                    onClick={() => setWorkspacesOpen(false)}
                    className="flex items-center gap-2.5 rounded-xl border border-slate-800 bg-slate-800/40 p-2.5 transition hover:border-slate-700 hover:bg-slate-800"
                  >
                    <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border ${ws.color}`}>
                      <Icon className="h-4 w-4" />
                    </span>
                    <span className="min-w-0 flex-1 text-xs font-semibold leading-tight text-slate-200">
                      {t(ws.titleKey)}
                    </span>
                  </Link>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </>
  );
};
