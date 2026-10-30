import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Building2,
  ChevronDown,
  ChevronRight,
  Home,
  LogOut,
  Menu,
  Moon,
  Search,
  Sun,
  User as UserIcon,
  X,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { useCompany } from "@/context/CompanyContext";
import { useTheme } from "@/hooks/useTheme";
import { useActiveModule, useModuleNav, type ModuleNav } from "@/resources/nav";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { GlobalSearch } from "./GlobalSearch";
import { badgeClass, useNavBadges } from "@/resources/navBadges";

/** Daily operational modules kept in the main top bar. Everything else moves into "More ▾". */
const DAILY_REQUIRED_KEYS = ["stock", "selling", "buying", "manufacturing"];

/** Closes a popover on outside click or Escape. */
function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

const UserMenu: React.FC = () => {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useDismiss(open, () => setOpen(false));
  const { pathname } = useLocation();
  useEffect(() => setOpen(false), [pathname]);

  if (!user) return null;
  const displayName = [user.first_name, user.last_name].filter(Boolean).join(" ") || user.username;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={displayName}
        className="flex items-center gap-1.5 rounded-full p-0.5 hover:bg-slate-800/70 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
      >
        <span className="w-8 h-8 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs font-semibold shrink-0 shadow-sm">
          {displayName ? displayName[0].toUpperCase() : <UserIcon className="w-3.5 h-3.5" />}
        </span>
      </button>

      {open && (
        <div
          role="menu"
          className="absolute end-0 mt-2 w-60 rounded-xl border border-slate-800 bg-slate-900 p-1 shadow-2xl animate-pop-in z-50"
        >
          <div className="px-3 py-2.5 border-b border-slate-800 mb-1">
            <p className="text-sm font-semibold text-slate-100 truncate">{displayName}</p>
            <p className="text-xs text-slate-500 truncate">{user.email}</p>
          </div>
          <Link
            to="/users/profile"
            role="menuitem"
            className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-slate-300 hover:bg-slate-800/70 hover:text-slate-100"
          >
            <UserIcon className="w-4 h-4 text-slate-500" />
            {t("shell.my_profile")}
          </Link>
          <button
            type="button"
            role="menuitem"
            onClick={logout}
            className="w-full flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-rose-400 hover:bg-rose-950/60"
          >
            <LogOut className="w-4 h-4" />
            {t("shell.sign_out")}
          </button>
        </div>
      )}
    </div>
  );
};

const ThemeToggle: React.FC = () => {
  const { t } = useTranslation();
  const { theme, toggle } = useTheme();
  const label = theme === "dark" ? t("shell.theme_light") : t("shell.theme_dark");
  return (
    <button
      type="button"
      onClick={toggle}
      title={label}
      aria-label={label}
      className="p-1.5 sm:p-2 rounded-lg text-slate-500 hover:text-slate-100 hover:bg-slate-800/70 transition-colors"
    >
      {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
    </button>
  );
};

/** "More ▾" dropdown tab grouping secondary modules on desktop. */
const MoreModulesMenu: React.FC<{
  modules: ModuleNav[];
  activeModule?: ModuleNav;
  badges: Record<string, number>;
}> = ({ modules, activeModule, badges }) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const ref = useDismiss(open, () => setOpen(false));
  const { pathname } = useLocation();
  useEffect(() => setOpen(false), [pathname]);

  const isMoreActive = modules.some((n) => n.module.key === activeModule?.module.key);

  const totalMoreBadges = modules.reduce((sum, n) => {
    return sum + [...n.records, ...n.reports].reduce((s, l) => s + (badges[l.badge ?? ""] ?? 0), 0);
  }, 0);

  return (
    <div ref={ref} className="relative shrink-0 flex items-center h-full">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={`relative inline-flex items-center gap-1 h-12 px-3 text-[13px] font-medium whitespace-nowrap transition-colors ${
          isMoreActive
            ? "text-slate-100 font-semibold after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 after:rounded-full after:bg-indigo-600"
            : "text-slate-400 hover:text-slate-100"
        }`}
      >
        <span>
          {isMoreActive && activeModule
            ? t(activeModule.module.titleKey)
            : t("common.more", "More")}
        </span>
        <ChevronDown className={`w-3.5 h-3.5 transition-transform duration-150 ${open ? "rotate-180" : ""}`} />
        {totalMoreBadges > 0 && <span className={badgeClass}>{totalMoreBadges}</span>}
      </button>

      {open && (
        <div
          role="menu"
          className="absolute start-0 top-full mt-1 w-52 rounded-xl border border-slate-800 bg-slate-900/95 backdrop-blur-md p-1.5 shadow-2xl animate-pop-in z-50 max-h-[75vh] overflow-y-auto"
        >
          {modules.map((n) => {
            const Icon = n.module.icon;
            const on = activeModule?.module.key === n.module.key;
            const c = [...n.records, ...n.reports].reduce((sum, l) => sum + (badges[l.badge ?? ""] ?? 0), 0);
            return (
              <Link
                key={n.module.key}
                to={n.path}
                role="menuitem"
                className={`flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs font-medium transition-colors ${
                  on
                    ? "bg-indigo-950 text-indigo-200"
                    : "text-slate-300 hover:bg-slate-800/70 hover:text-slate-100"
                }`}
              >
                {Icon && <Icon className={`w-3.5 h-3.5 shrink-0 ${on ? "text-indigo-400" : "text-slate-500"}`} />}
                <span className="flex-1 truncate">{t(n.module.titleKey)}</span>
                {c > 0 && <span className={badgeClass}>{c}</span>}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
};

/**
 * Mobile navigation: a compact module switcher plus search. The module page
 * itself is the second level, so the drawer never dumps every link into one list.
 */
export const MobileDrawer: React.FC<{ nav: ModuleNav[]; onClose: () => void }> = ({ nav, onClose }) => {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const active = useActiveModule(nav);
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const results = useMemo(() => {
    if (!q) return [];
    return nav.flatMap((n) => {
      const moduleMatches = t(n.module.titleKey).toLowerCase().includes(q);
      return n.groups.flatMap((g) =>
        g.links
          .filter((l) => moduleMatches || t(l.labelKey).toLowerCase().includes(q))
          .map((link) => ({ link, module: n }))
      );
    });
  }, [nav, q, t]);

  const isHome = pathname === "/" || pathname === "/home";

  return (
    <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label={t("shell.main_navigation")}>
      <div className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" onClick={onClose} />
      <nav className="absolute inset-y-0 start-0 flex w-[85%] max-w-xs flex-col border-e border-slate-800 bg-slate-900 pt-[env(safe-area-inset-top)] shadow-2xl">
        <div className="flex items-center justify-between p-4 pb-2">
          <img src="/logo.png" alt="" className="h-8 w-auto rounded-md bg-white p-0.5" />
          <button
            type="button"
            onClick={onClose}
            aria-label={t("common.close")}
            className="rounded-lg p-2 text-slate-500 hover:bg-slate-800/70 hover:text-slate-200"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="px-4 pb-3">
          <div className="relative">
            <Search className="pointer-events-none absolute start-3 top-3 h-4 w-4 text-slate-500" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("search")}
              aria-label={t("search")}
              className="w-full rounded-xl border border-slate-800 bg-slate-950 py-2.5 pe-9 ps-9 text-sm text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label={t("common.clear")}
                className="absolute end-2 top-2 rounded-md p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-3 pb-4">
          {q ? (
            results.length === 0 ? (
              <p className="px-3 py-8 text-center text-sm text-slate-500">{t("common.no_results")}</p>
            ) : (
              <div className="space-y-0.5">
                {results.map(({ link, module }) => {
                  const Icon = link.icon;
                  return (
                    <Link
                      key={`${module.module.key}:${link.path}`}
                      to={link.path}
                      onClick={onClose}
                      className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-slate-300 transition-colors hover:bg-slate-800/70 hover:text-slate-100"
                    >
                      {Icon ? <Icon className="h-4 w-4 shrink-0 text-slate-500" /> : <span className="h-4 w-4" />}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{t(link.labelKey)}</span>
                        <span className="block truncate text-[10px] text-slate-500">{t(module.module.titleKey)}</span>
                      </span>
                    </Link>
                  );
                })}
              </div>
            )
          ) : (
            <>
              <Link
                to="/"
                onClick={onClose}
                aria-current={isHome ? "page" : undefined}
                className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                  isHome ? "bg-indigo-950 text-indigo-200" : "text-slate-300 hover:bg-slate-800/70 hover:text-slate-100"
                }`}
              >
                <Home className={`h-4 w-4 shrink-0 ${isHome ? "text-indigo-400" : "text-slate-500"}`} />
                <span className="flex-1">{t("home.title")}</span>
              </Link>

              <p className="px-3 pb-1 pt-5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                {t("common.section_modules")}
              </p>
              <div className="space-y-0.5">
                {nav.map((n) => {
                  const Icon = n.module.icon;
                  const on = active?.module.key === n.module.key;
                  const count = n.groups.reduce((sum, g) => sum + g.links.length, 0);
                  return (
                    <Link
                      key={n.module.key}
                      to={n.path}
                      onClick={onClose}
                      aria-current={on ? "page" : undefined}
                      className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                        on ? "bg-indigo-950 text-indigo-200" : "text-slate-300 hover:bg-slate-800/70 hover:text-slate-100"
                      }`}
                    >
                      <Icon className={`h-4 w-4 shrink-0 ${on ? "text-indigo-400" : "text-slate-500"}`} />
                      <span className="min-w-0 flex-1 truncate">{t(n.module.titleKey)}</span>
                      <span className="text-[10px] tabular-nums text-slate-600">{count}</span>
                      <ChevronRight className="h-4 w-4 shrink-0 text-slate-600 rtl:rotate-180" />
                    </Link>
                  );
                })}
              </div>
            </>
          )}
        </div>

        <div className="border-t border-slate-800 p-4 pb-[calc(1rem_+_env(safe-area-inset-bottom))]">
          <LanguageSwitcher />
        </div>
      </nav>
    </div>
  );
};

export interface TopBarProps {
  onOpenMenu?: () => void;
}

/** App chrome: brand, core tabs, NGU-style searchbar, "More ▾" dropdown, language / theme / user. */
export const TopBar: React.FC<TopBarProps> = ({ onOpenMenu }) => {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { activeCompany, companies, clearActiveCompany } = useCompany();
  const nav = useModuleNav();
  const badges = useNavBadges();
  const active = useActiveModule(nav);
  const [drawer, setDrawer] = useState(false);
  useEffect(() => setDrawer(false), [pathname]);

  const handleMenuClick = () => {
    if (onOpenMenu) {
      onOpenMenu();
    } else {
      setDrawer(true);
    }
  };

  // Keep strictly daily operational tabs visible; group all others into "More ▾"
  const { primaryNav, moreNav } = useMemo(() => {
    const primaryMap = new Map<string, ModuleNav>();
    const more: ModuleNav[] = [];

    nav.forEach((n) => {
      if (DAILY_REQUIRED_KEYS.includes(n.module.key)) {
        primaryMap.set(n.module.key, n);
      } else {
        more.push(n);
      }
    });

    const primary: ModuleNav[] = [];
    DAILY_REQUIRED_KEYS.forEach((key) => {
      const item = primaryMap.get(key);
      if (item) primary.push(item);
    });

    // Fallback if none matched
    if (primary.length === 0 && nav.length > 0) {
      return { primaryNav: nav.slice(0, 4), moreNav: nav.slice(4) };
    }

    return { primaryNav: primary, moreNav: more };
  }, [nav]);

  const tabCls = (on: boolean) =>
    `relative shrink-0 inline-flex items-center gap-1.5 h-12 px-3 text-[13px] font-medium whitespace-nowrap transition-colors ${
      on
        ? "text-slate-100 font-semibold after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 after:rounded-full after:bg-indigo-600"
        : "text-slate-400 hover:text-slate-100"
    }`;

  return (
    <header className="sticky top-0 z-40 border-b border-slate-800 bg-slate-900/90 pt-[env(safe-area-inset-top)] backdrop-blur-md">
      <div className="mx-auto max-w-[1440px] px-3 sm:px-6 h-12 flex items-center gap-2 sm:gap-4">
        {/* Mobile menu button */}
        <button
          type="button"
          onClick={handleMenuClick}
          className="md:hidden p-1.5 -ms-1 rounded-lg text-slate-400 hover:bg-slate-800/70"
          aria-label={t("shell.menu")}
        >
          <Menu className="w-5 h-5" />
        </button>

        {/* Brand logo & title */}
        <Link to="/" className="flex items-center gap-2 shrink-0 me-1" aria-label={t("appName")}>
          <img src="/logo.png" alt="" className="h-7 sm:h-8 w-auto rounded-md bg-white p-0.5" />
          <span className="hidden sm:block h-3.5 w-px bg-slate-800" />
          <span className="hidden sm:block text-sm font-semibold tracking-tight text-slate-100">{t("appName")}</span>
        </Link>

        {/* Company selector */}
        {activeCompany && (
          <button
            type="button"
            onClick={() => {
              if (companies.length > 1) {
                clearActiveCompany();
                navigate("/");
              }
            }}
            title={
              companies.length > 1
                ? t("company.switch_hint", { name: activeCompany.name })
                : activeCompany.name
            }
            className={`flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900/70 px-2.5 py-1.5 text-xs font-medium text-slate-300 max-w-[110px] sm:max-w-[150px] xl:max-w-[200px] shrink-0 ${
              companies.length > 1 ? "hover:border-indigo-700/70 hover:text-indigo-200" : "cursor-default"
            }`}
          >
            <Building2 className="h-3.5 w-3.5 shrink-0 text-slate-500" />
            <span className="truncate">{activeCompany.name}</span>
          </button>
        )}

        {/* Daily operational tabs */}
        <nav className="hidden md:flex items-center gap-0.5 shrink-0" aria-label={t("shell.main_navigation")}>
          <Link to="/" className={tabCls(pathname === "/" || pathname === "/home")}>
            {t("home.title")}
          </Link>
          {primaryNav.map((n) => (
            <Link key={n.module.key} to={n.path} className={tabCls(active?.module.key === n.module.key)}>
              {t(n.module.titleKey)}
              {(() => {
                const c = [...n.records, ...n.reports].reduce((sum, l) => sum + (badges[l.badge ?? ""] ?? 0), 0);
                return c > 0 ? <span className={badgeClass}>{c}</span> : null;
              })()}
            </Link>
          ))}
          {moreNav.length > 0 && (
            <MoreModulesMenu
              modules={moreNav}
              activeModule={active}
              badges={badges}
            />
          )}
        </nav>

        {/* NGU Admin Panel Style Global Searchbar with Ctrl+K */}
        <div className="hidden sm:flex items-center flex-1 min-w-[9rem] max-w-sm ms-auto">
          <GlobalSearch />
        </div>

        {/* Right side controls */}
        <div className="flex items-center gap-1 sm:gap-2 shrink-0 ms-auto sm:ms-0">
          <div className="hidden sm:block">
            <LanguageSwitcher compact />
          </div>
          <ThemeToggle />
          <UserMenu />
        </div>
      </div>

      {/* Portalled: mobile navigation drawer */}
      {drawer && createPortal(<MobileDrawer nav={nav} onClose={() => setDrawer(false)} />, document.body)}
    </header>
  );
};
