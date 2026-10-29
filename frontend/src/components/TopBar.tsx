import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Building2, Home, LogOut, Menu, Moon, Sun, User as UserIcon, X } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { useCompany } from "@/context/CompanyContext";
import { useTheme } from "@/hooks/useTheme";
import { isActivePath, useActiveModule, useModuleNav, type ModuleNav } from "@/resources/nav";
import type { NavLink } from "@/types";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { badgeClass, useNavBadges } from "@/resources/navBadges";

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
        className="flex items-center gap-2 rounded-full p-0.5 pe-2 hover:bg-slate-800/70 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
      >
        <span className="w-8 h-8 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs font-semibold">
          {displayName ? displayName[0].toUpperCase() : <UserIcon className="w-4 h-4" />}
        </span>
        <span className="hidden lg:block text-sm font-medium text-slate-200 max-w-[9rem] truncate">{displayName}</span>
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
      className="p-2 rounded-lg text-slate-500 hover:text-slate-100 hover:bg-slate-800/70 transition-colors"
    >
      {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
    </button>
  );
};

/** Pill link in the mobile drawer. */
const SubLink: React.FC<{ link: NavLink; active: boolean }> = ({ link, active }) => {
  const { t } = useTranslation();
  const Icon = link.icon;
  return (
    <Link
      to={link.path}
      aria-current={active ? "page" : undefined}
      className={`shrink-0 inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium whitespace-nowrap transition-colors ${
        active ? "bg-indigo-950 text-indigo-200" : "text-slate-400 hover:text-slate-100 hover:bg-slate-800/60"
      }`}
    >
      {Icon && <Icon className={`w-3.5 h-3.5 ${active ? "text-indigo-400" : "text-slate-500"}`} />}
      {t(link.labelKey)}
    </Link>
  );
};

const MobileDrawer: React.FC<{ nav: ModuleNav[]; onClose: () => void }> = ({ nav, onClose }) => {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const navRef = useRef<HTMLElement>(null);
  // Open with the current page in view rather than at the top of a long list.
  useEffect(() => {
    navRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "center" });
  }, []);
  return (
    <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label={t("shell.main_navigation")}>
      <div className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" onClick={onClose} />
      <nav ref={navRef} className="absolute inset-y-0 start-0 w-[85%] max-w-xs bg-slate-900 border-e border-slate-800 shadow-2xl overflow-y-auto p-4 space-y-5">
        <div className="flex items-center justify-between">
          <img src="/logo.png" alt="" className="h-8 w-auto rounded-md bg-white p-0.5" />
          <button type="button" onClick={onClose} aria-label={t("common.close")} className="p-2 rounded-lg text-slate-500 hover:bg-slate-800/70">
            <X className="w-5 h-5" />
          </button>
        </div>
        <SubLink
          link={{ path: "/", labelKey: "home.title", icon: Home }}
          active={pathname === "/" || pathname === "/home"}
        />
        {nav.map((n) => (
          <div key={n.module.key} className="space-y-1">
            <Link to={n.path} className="block px-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500 hover:text-indigo-300">
              {t(n.module.titleKey)}
            </Link>
            <div className="flex flex-col">
              {[...n.records, ...n.reports].map((l) => (
                <SubLink key={l.path} link={l} active={isActivePath(pathname, l.path)} />
              ))}
            </div>
          </div>
        ))}
        <div className="pt-4 border-t border-slate-800">
          <LanguageSwitcher />
        </div>
      </nav>
    </div>
  );
};

/** App chrome: brand, module tabs, language / theme / user. The module's own links live in <Sidebar>. */
export const TopBar: React.FC = () => {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { activeCompany, companies, clearActiveCompany } = useCompany();
  const nav = useModuleNav();
  const badges = useNavBadges();
  const active = useActiveModule(nav);
  const [drawer, setDrawer] = useState(false);
  useEffect(() => setDrawer(false), [pathname]);

  const tabCls = (on: boolean) =>
    `relative shrink-0 inline-flex items-center gap-2 h-14 px-3 text-sm font-medium whitespace-nowrap transition-colors ${
      on
        ? "text-slate-100 after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:rounded-full after:bg-indigo-600"
        : "text-slate-400 hover:text-slate-100"
    }`;

  return (
    <header className="sticky top-0 z-40 bg-slate-900/90 backdrop-blur-md border-b border-slate-800">
      <div className="mx-auto max-w-[1440px] px-4 sm:px-6 h-14 flex items-center gap-3">
        <button
          type="button"
          onClick={() => setDrawer(true)}
          className="md:hidden p-2 -ms-2 rounded-lg text-slate-400 hover:bg-slate-800/70"
          aria-label={t("shell.menu")}
        >
          <Menu className="w-5 h-5" />
        </button>

        <Link to="/" className="flex items-center gap-2.5 shrink-0 me-2" aria-label={t("appName")}>
          <img src="/logo.png" alt="" className="h-8 w-auto rounded-md bg-white p-0.5" />
          <span className="hidden sm:block h-5 w-px bg-slate-800" />
          <span className="hidden sm:block text-sm font-semibold tracking-tight text-slate-100">{t("appName")}</span>
        </Link>

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
            className={`hidden lg:flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900/70 px-2.5 py-1.5 text-xs font-medium text-slate-300 ${
              companies.length > 1 ? "hover:border-indigo-700/70 hover:text-indigo-200" : "cursor-default"
            }`}
          >
            <Building2 className="h-3.5 w-3.5 shrink-0 text-slate-500" />
            <span className="max-w-[9rem] truncate">{activeCompany.name}</span>
          </button>
        )}
        <nav className="hidden md:flex items-center min-w-0 overflow-x-auto no-scrollbar" aria-label={t("shell.main_navigation")}>
          <Link to="/" className={tabCls(pathname === "/" || pathname === "/home")}>
            {t("home.title")}
          </Link>
          {nav.map((n) => (
            <Link key={n.module.key} to={n.path} className={tabCls(active?.module.key === n.module.key)}>
              {t(n.module.titleKey)}
              {(() => {
                const c = [...n.records, ...n.reports].reduce((sum, l) => sum + (badges[l.badge ?? ""] ?? 0), 0);
                return c > 0 ? <span className={badgeClass}>{c}</span> : null;
              })()}
            </Link>
          ))}
        </nav>

        <div className="ms-auto flex items-center gap-1">
          <div className="hidden sm:block">
            <LanguageSwitcher />
          </div>
          <ThemeToggle />
          <UserMenu />
        </div>
      </div>

      {/* Portalled: the header's backdrop-filter would otherwise trap the drawer's `fixed` inside the 56px bar. */}
      {drawer && createPortal(<MobileDrawer nav={nav} onClose={() => setDrawer(false)} />, document.body)}
    </header>
  );
};
