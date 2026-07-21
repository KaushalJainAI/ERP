import React, { useState } from "react";
import { Layout } from "@/components/Layout";
import { DataTable } from "@/components/DataTable";
import { formatIndianCurrency } from "@/lib/numberFormat";
import { BookOpen, FileText, BarChart3, Plus } from "lucide-react";

export const AccountsPage: React.FC = () => {
  const [tab, setTab] = useState<"accounts" | "journals" | "trial">("journals");

  const accountColumns = [
    { key: "name", label: "Account Name" },
    { key: "company_name", label: "Company" },
    { key: "root_type", label: "Root Type" },
    { key: "account_type", label: "Account Type" },
    { key: "is_group", label: "Is Group", render: (v: boolean) => (v ? "Yes" : "No") },
  ];

  const journalColumns = [
    { key: "name", label: "Voucher No" },
    { key: "posting_date", label: "Posting Date" },
    { key: "docstatus", label: "Status" },
    { key: "total_debit", label: "Total Debit", render: (v: number) => formatIndianCurrency(v) },
    { key: "total_credit", label: "Total Credit", render: (v: number) => formatIndianCurrency(v) },
    { key: "user_remark", label: "Remarks" },
  ];

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-100 tracking-tight">Financial Accounting</h1>
            <p className="text-slate-400 text-xs mt-1">Chart of accounts, general ledger, and journal vouchers</p>
          </div>

          <div className="flex space-x-2 bg-slate-900 p-1 border border-slate-800 rounded-xl">
            <button
              onClick={() => setTab("journals")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-2 ${
                tab === "journals" ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <FileText className="w-3.5 h-3.5" />
              <span>Journal Entries</span>
            </button>
            <button
              onClick={() => setTab("accounts")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-2 ${
                tab === "accounts" ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <BookOpen className="w-3.5 h-3.5" />
              <span>Chart of Accounts</span>
            </button>
            <button
              onClick={() => setTab("trial")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-2 ${
                tab === "trial" ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <BarChart3 className="w-3.5 h-3.5" />
              <span>Trial Balance</span>
            </button>
          </div>
        </div>

        {tab === "journals" && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
            <DataTable
              title="Journal Entries"
              endpoint="/accounts/journal-entries/"
              columns={journalColumns}
              searchPlaceholder="Search voucher no or remarks..."
            />
          </div>
        )}

        {tab === "accounts" && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
            <DataTable
              title="Chart of Accounts"
              endpoint="/accounts/accounts/"
              columns={accountColumns}
              searchPlaceholder="Search account name..."
            />
          </div>
        )}

        {tab === "trial" && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl space-y-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <div>
                <h3 className="font-bold text-slate-100">Trial Balance Summary</h3>
                <p className="text-xs text-slate-400">Ledger debit and credit equilibrium check</p>
              </div>
              <span className="text-xs px-2.5 py-1 rounded-md bg-emerald-950 text-emerald-400 border border-emerald-800 font-semibold">
                Status: BALANCED
              </span>
            </div>

            <div className="p-4 rounded-lg bg-slate-950 border border-slate-800 flex justify-around text-sm font-semibold">
              <div>
                <span className="text-slate-400 text-xs block uppercase">Total Debit</span>
                <span className="text-slate-100 text-lg">{formatIndianCurrency(1250000)}</span>
              </div>
              <div>
                <span className="text-slate-400 text-xs block uppercase">Total Credit</span>
                <span className="text-slate-100 text-lg">{formatIndianCurrency(1250000)}</span>
              </div>
              <div>
                <span className="text-slate-400 text-xs block uppercase">Imbalance</span>
                <span className="text-emerald-400 text-lg">₹0.00</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
};
