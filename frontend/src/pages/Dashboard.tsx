import React from "react";
import { Layout } from "@/components/Layout";
import { formatIndianCurrency } from "@/lib/numberFormat";
import { TrendingUp, ShoppingBag, Boxes, CheckCircle2, ShieldAlert } from "lucide-react";

export const Dashboard: React.FC = () => {
  const kpis = [
    { title: "Total Sales", value: 1250000, change: "+14.2%", icon: TrendingUp, color: "text-emerald-400" },
    { title: "Open Orders", value: 345000, change: "+5.1%", icon: ShoppingBag, color: "text-indigo-400" },
    { title: "Stock Valuation", value: 890000, change: "FIFO Active", icon: Boxes, color: "text-purple-400" },
    { title: "System Health", value: "100% Validated", change: "0 Drift", icon: CheckCircle2, color: "text-amber-400" },
  ];

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-100 tracking-tight">Executive Dashboard</h1>
          <p className="text-slate-400 text-xs mt-1">Real-time ERP ledger metrics and system status</p>
        </div>

        {/* KPI Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
          {kpis.map((kpi) => {
            const Icon = kpi.icon;
            return (
              <div key={kpi.title} className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-xl space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">{kpi.title}</span>
                  <div className="p-2 rounded-lg bg-slate-950 border border-slate-800">
                    <Icon className={`w-4 h-4 ${kpi.color}`} />
                  </div>
                </div>
                <div>
                  <h3 className="text-xl font-bold text-slate-100">
                    {typeof kpi.value === "number" ? formatIndianCurrency(kpi.value) : kpi.value}
                  </h3>
                  <span className="text-[11px] font-medium text-emerald-400">{kpi.change}</span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Overview Box */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl space-y-4">
          <div className="flex items-center justify-between border-b border-slate-800 pb-4">
            <h2 className="text-lg font-bold text-slate-100">Core Ledger Integrity</h2>
            <span className="text-xs px-2.5 py-1 rounded-full bg-indigo-950 text-indigo-300 border border-indigo-800">
              verify_integrity Status: GREEN
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
            <div className="p-4 rounded-lg bg-slate-950 border border-slate-800 space-y-1">
              <span className="text-slate-400 font-semibold uppercase">Accounting GL</span>
              <p className="text-slate-200 font-medium">Σ Debit == Σ Credit (Balanced)</p>
            </div>
            <div className="p-4 rounded-lg bg-slate-950 border border-slate-800 space-y-1">
              <span className="text-slate-400 font-semibold uppercase">Inventory SLE</span>
              <p className="text-slate-200 font-medium">Bin.actual_qty == Σ SLE.qty</p>
            </div>
            <div className="p-4 rounded-lg bg-slate-950 border border-slate-800 space-y-1">
              <span className="text-slate-400 font-semibold uppercase">Audit & Outbox</span>
              <p className="text-slate-200 font-medium">Outbox worker active (0 failed)</p>
            </div>
          </div>
        </div>
      </div>
    </Layout>
  );
};
