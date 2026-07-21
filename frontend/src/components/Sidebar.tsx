import React from "react";
import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LayoutDashboard, ShoppingCart, ShoppingBag, Boxes, BookOpen, Users, ShieldCheck } from "lucide-react";

export const Sidebar: React.FC = () => {
  const { t } = useTranslation();
  const location = useLocation();

  const navItems = [
    { label: t("dashboard"), path: "/dashboard", icon: LayoutDashboard },
    { label: t("accounts"), path: "/accounts/journal-entries", icon: BookOpen },
    { label: t("stock"), path: "/stock/items", icon: Boxes },
    { label: t("selling"), path: "/selling/sales-orders", icon: ShoppingCart },
    { label: t("buying"), path: "/buying/purchase-orders", icon: ShoppingBag },
    { label: t("users"), path: "/users", icon: Users },
  ];

  return (
    <aside className="w-64 bg-slate-950 border-r border-slate-800 flex flex-col justify-between min-h-screen p-4">
      <div className="space-y-6">
        <div className="flex items-center space-x-3 px-2 py-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-indigo-600 to-purple-600 flex items-center justify-center font-bold text-white shadow-lg shadow-indigo-600/30">
            ERP
          </div>
          <div>
            <h1 className="font-bold text-slate-100 text-lg tracking-tight leading-tight">Enterprise ERP</h1>
            <span className="text-[10px] uppercase font-semibold tracking-widest text-indigo-400">Indian Market</span>
          </div>
        </div>

        <nav className="space-y-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = location.pathname.startsWith(item.path);
            return (
              <Link
                key={item.path}
                to={item.path}
                className={`flex items-center space-x-3 px-3.5 py-2.5 rounded-lg text-sm font-medium transition-all ${
                  active
                    ? "bg-indigo-600/20 text-indigo-300 border border-indigo-500/30 shadow-md shadow-indigo-500/10"
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-900"
                }`}
              >
                <Icon className={`w-4 h-4 ${active ? "text-indigo-400" : "text-slate-500"}`} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
      </div>

      <div className="p-3 bg-slate-900/60 border border-slate-800/80 rounded-xl text-xs text-slate-400 space-y-1">
        <div className="flex items-center space-x-2 text-indigo-400 font-semibold">
          <ShieldCheck className="w-4 h-4" />
          <span>Self-Healing Spine</span>
        </div>
        <p className="text-[11px] text-slate-500 leading-normal">
          Immutable ledgers active. Integrity checks enabled.
        </p>
      </div>
    </aside>
  );
};
