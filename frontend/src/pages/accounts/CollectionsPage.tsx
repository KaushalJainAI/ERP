import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  ArrowDownLeft,
  Calendar,
  ChevronDown,
  ChevronUp,
  Clock,
  FileText,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Users,
} from "lucide-react";
import { Layout } from "@/components/Layout";
import { apiClient } from "@/api/client";
import { normaliseError, summariseError } from "@/api/endpoints";
import { useCompany } from "@/context/CompanyContext";
import { formatDate, formatIndianCurrency, todayISO } from "@/lib/format";
import { RecordMoneySheet } from "@/components/accounts/RecordMoneySheet";

interface BillItem {
  invoice: string;
  name: string;
  posting_date: string;
  due_date: string | null;
  rounded_total: string;
  outstanding_amount: string;
  days_late: number;
  is_overdue: boolean;
}

interface CustomerRow {
  customer_id: number;
  customer_name: string;
  customer: string;
  total_outstanding: string;
  total_overdue: string;
  oldest_bill_date: string | null;
  days_late: number;
  bills: BillItem[];
}

interface CollectionsData {
  customers: CustomerRow[];
  totals: {
    total_receivables: string;
    total_overdue: string;
    customer_count: number;
    invoice_count: number;
  };
}

export const CollectionsPage: React.FC = () => {
  const { t } = useTranslation();
  const { activeCompany } = useCompany();
  const [searchTerm, setSearchTerm] = useState("");
  const [expandedCustomers, setExpandedCustomers] = useState<Record<number, boolean>>({});

  // Record Money modal state
  const [sheetOpen, setSheetOpen] = useState(false);
  const [selectedParty, setSelectedParty] = useState("");
  const [selectedAmount, setSelectedAmount] = useState<string | number>("");

  const { data, isLoading, isFetching, error, refetch } = useQuery<CollectionsData>({
    queryKey: ["accounts", "collections", activeCompany?.name],
    queryFn: async () => {
      const res = await apiClient.get("/accounts/collections/", {
        params: {
          company: activeCompany?.name,
          as_on: todayISO(),
        },
      });
      return res.data;
    },
    enabled: !!activeCompany,
  });

  const toggleExpand = (customerId: number) => {
    setExpandedCustomers((prev) => ({
      ...prev,
      [customerId]: !prev[customerId],
    }));
  };

  const handleOpenReceipt = (customerName?: string, amount?: string | number) => {
    setSelectedParty(customerName || "");
    setSelectedAmount(amount || "");
    setSheetOpen(true);
  };

  const customers = data?.customers || [];
  const totals = data?.totals || {
    total_receivables: "0.00",
    total_overdue: "0.00",
    customer_count: 0,
    invoice_count: 0,
  };

  const filteredCustomers = useMemo(() => {
    if (!searchTerm.trim()) return customers;
    const q = searchTerm.toLowerCase();
    return customers.filter(
      (c) =>
        c.customer_name.toLowerCase().includes(q) ||
        c.bills.some((b) => b.invoice.toLowerCase().includes(q))
    );
  }, [customers, searchTerm]);

  return (
    <Layout>
      <div className="space-y-6">
        {/* Page Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
              {t("acc.collections.title", { defaultValue: "Money to Collect" })}
              {isFetching && <Loader2 className="w-5 h-5 animate-spin text-emerald-400" />}
            </h1>
            <p className="text-sm text-slate-400 mt-1">
              {t("acc.collections.subtitle", {
                defaultValue: "Track open customer balances, overdue amounts, and record receipts",
              })}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => refetch()}
              className="p-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 border border-slate-700/60 transition-colors"
              title={t("selling_kpi.refresh", { defaultValue: "Refresh" })}
            >
              <RefreshCw className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={() => handleOpenReceipt()}
              className="inline-flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold rounded-lg shadow-sm transition-colors"
            >
              <Plus className="w-4 h-4" />
              {t("acc.collections.record_receipt", { defaultValue: "Record Receipt" })}
            </button>
          </div>
        </div>

        {/* Summary Metric Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
            <div className="text-xs font-medium text-slate-400 uppercase tracking-wider">
              {t("acc.collections.total_receivables", { defaultValue: "Total Receivables" })}
            </div>
            <div className="mt-2 text-2xl font-bold text-white tabular-nums">
              {formatIndianCurrency(totals.total_receivables)}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {totals.invoice_count}{" "}
              {t("acc.collections.open_invoices", { defaultValue: "open invoices" })}
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
            <div className="text-xs font-medium text-rose-400 uppercase tracking-wider flex items-center justify-between">
              <span>{t("acc.collections.total_overdue", { defaultValue: "Total Overdue" })}</span>
              <AlertCircle className="w-4 h-4 text-rose-400" />
            </div>
            <div className="mt-2 text-2xl font-bold text-rose-300 tabular-nums">
              {formatIndianCurrency(totals.total_overdue)}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {totals.customer_count}{" "}
              {t("acc.collections.customers_owing", { defaultValue: "customers owing" })}
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
            <div className="text-xs font-medium text-slate-400 uppercase tracking-wider flex items-center justify-between">
              <span>{t("acc.collections.customers_owing", { defaultValue: "Customers Owing" })}</span>
              <Users className="w-4 h-4 text-slate-400" />
            </div>
            <div className="mt-2 text-2xl font-bold text-slate-200 tabular-nums">
              {totals.customer_count}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {t("acc.collections.as_on_today", { defaultValue: "As of today" })}
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
            <div className="text-xs font-medium text-slate-400 uppercase tracking-wider flex items-center justify-between">
              <span>{t("acc.collections.open_invoices", { defaultValue: "Open Invoices" })}</span>
              <FileText className="w-4 h-4 text-slate-400" />
            </div>
            <div className="mt-2 text-2xl font-bold text-slate-200 tabular-nums">
              {totals.invoice_count}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {t("acc.collections.posted_unpaid", { defaultValue: "Submitted & unpaid" })}
            </div>
          </div>
        </div>

        {/* Search & Customer List Container */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
          {/* Search bar */}
          <div className="p-4 border-b border-slate-800 flex items-center gap-3">
            <Search className="w-4 h-4 text-slate-400" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder={t("acc.collections.search_placeholder", {
                defaultValue: "Search customer name or invoice number...",
              })}
              className="bg-transparent border-0 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-0 w-full"
            />
          </div>

          {/* Error Message */}
          {error && (
            <div className="p-6 text-center text-rose-400 text-sm">
              {summariseError(normaliseError(error))}
            </div>
          )}

          {/* Loading state */}
          {isLoading ? (
            <div className="p-12 flex flex-col items-center justify-center text-slate-400 gap-3">
              <Loader2 className="w-6 h-6 animate-spin text-emerald-400" />
              <span className="text-sm">
                {t("common.loading", { defaultValue: "Loading collections data..." })}
              </span>
            </div>
          ) : filteredCustomers.length === 0 ? (
            <div className="p-12 text-center text-slate-400 text-sm">
              {t("acc.collections.no_customers", {
                defaultValue: "No outstanding customer balances found.",
              })}
            </div>
          ) : (
            <div className="divide-y divide-slate-800/80">
              {filteredCustomers.map((cust) => {
                const isExpanded = !!expandedCustomers[cust.customer_id];
                const hasOverdue = Number(cust.total_overdue) > 0;

                return (
                  <div key={cust.customer_id} className="transition-colors hover:bg-slate-800/30">
                    {/* Main Customer Row */}
                    <div className="p-4 sm:p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
                      <div
                        onClick={() => toggleExpand(cust.customer_id)}
                        className="cursor-pointer flex-1 space-y-1.5"
                      >
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-semibold text-white text-base">
                            {cust.customer_name}
                          </span>
                          {cust.days_late > 0 && (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-semibold bg-rose-500/15 text-rose-400 border border-rose-500/30">
                              <Clock className="w-3 h-3" />
                              {cust.days_late} {t("acc.collections.days_late", { defaultValue: "days late" })}
                            </span>
                          )}
                          <span className="text-xs text-slate-500 font-mono">
                            {cust.bills.length}{" "}
                            {cust.bills.length === 1
                              ? t("acc.collections.bill_one", { defaultValue: "bill" })
                              : t("acc.collections.bill_other", { defaultValue: "bills" })}
                          </span>
                        </div>
                        <div className="flex items-center gap-4 text-xs text-slate-400 flex-wrap">
                          {cust.oldest_bill_date && (
                            <span className="flex items-center gap-1">
                              <Calendar className="w-3.5 h-3.5 text-slate-500" />
                              {t("acc.collections.oldest_bill", { defaultValue: "Oldest Bill" })}:{" "}
                              {formatDate(cust.oldest_bill_date)}
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Right Side Balances & Action */}
                      <div className="flex items-center justify-between md:justify-end gap-5 flex-wrap">
                        <div className="text-end">
                          <div className="text-xs text-slate-400">
                            {t("acc.collections.outstanding", { defaultValue: "Total Due" })}
                          </div>
                          <div className="text-base font-bold text-white tabular-nums">
                            {formatIndianCurrency(cust.total_outstanding)}
                          </div>
                          {hasOverdue && (
                            <div className="text-xs font-semibold text-rose-400 tabular-nums">
                              {t("acc.collections.overdue", { defaultValue: "Overdue" })}:{" "}
                              {formatIndianCurrency(cust.total_overdue)}
                            </div>
                          )}
                        </div>

                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() =>
                              handleOpenReceipt(cust.customer_name, cust.total_outstanding)
                            }
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600/90 hover:bg-emerald-600 text-white text-xs font-semibold rounded-lg shadow-sm transition-colors"
                          >
                            <ArrowDownLeft className="w-3.5 h-3.5" />
                            {t("acc.collections.record_receipt", { defaultValue: "Record Receipt" })}
                          </button>
                          <button
                            type="button"
                            onClick={() => toggleExpand(cust.customer_id)}
                            className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition-colors"
                            aria-label={t("acc.collections.toggle_bills")}
                          >
                            {isExpanded ? (
                              <ChevronUp className="w-4 h-4" />
                            ) : (
                              <ChevronDown className="w-4 h-4" />
                            )}
                          </button>
                        </div>
                      </div>
                    </div>

                    {/* Expandable Bills Breakdown */}
                    {isExpanded && (
                      <div className="px-4 pb-4 sm:px-6 sm:pb-5">
                        <div className="bg-slate-950/60 rounded-lg border border-slate-800/80 overflow-hidden">
                          <div className="px-3 py-2 bg-slate-800/40 border-b border-slate-800 text-xs font-semibold text-slate-300">
                            {t("acc.collections.open_bills_for", {
                              defaultValue: "Open Bills",
                            })}
                          </div>
                          <div className="overflow-x-auto">
                            <table className="w-full text-xs text-slate-300">
                              <thead className="bg-slate-900/60 text-slate-400 border-b border-slate-800/60">
                                <tr>
                                  <th className="py-2 px-3 text-start">
                                    {t("acc.collections.invoice", { defaultValue: "Invoice" })}
                                  </th>
                                  <th className="py-2 px-3 text-start">
                                    {t("acc.collections.date", { defaultValue: "Date" })}
                                  </th>
                                  <th className="py-2 px-3 text-start">
                                    {t("acc.collections.due_date", { defaultValue: "Due Date" })}
                                  </th>
                                  <th className="py-2 px-3 text-end">
                                    {t("acc.collections.amount", { defaultValue: "Total" })}
                                  </th>
                                  <th className="py-2 px-3 text-end">
                                    {t("acc.collections.balance", { defaultValue: "Outstanding" })}
                                  </th>
                                  <th className="py-2 px-3 text-center">
                                    {t("acc.collections.status", { defaultValue: "Status" })}
                                  </th>
                                  <th className="py-2 px-3 text-end">
                                    {t("acc.collections.action", { defaultValue: "Action" })}
                                  </th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-slate-800/50">
                                {cust.bills.map((bill) => (
                                  <tr
                                    key={bill.name}
                                    className="hover:bg-slate-800/20 transition-colors"
                                  >
                                    <td className="py-2 px-3 font-mono font-medium text-slate-200">
                                      {bill.invoice}
                                    </td>
                                    <td className="py-2 px-3 text-slate-400">
                                      {formatDate(bill.posting_date)}
                                    </td>
                                    <td className="py-2 px-3 text-slate-400">
                                      {bill.due_date ? formatDate(bill.due_date) : "-"}
                                    </td>
                                    <td className="py-2 px-3 text-end tabular-nums text-slate-400">
                                      {formatIndianCurrency(bill.rounded_total)}
                                    </td>
                                    <td className="py-2 px-3 text-end tabular-nums font-semibold text-white">
                                      {formatIndianCurrency(bill.outstanding_amount)}
                                    </td>
                                    <td className="py-2 px-3 text-center">
                                      {bill.is_overdue ? (
                                        <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-rose-500/15 text-rose-400 border border-rose-500/30">
                                          {bill.days_late}d late
                                        </span>
                                      ) : (
                                        <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                                          Due
                                        </span>
                                      )}
                                    </td>
                                    <td className="py-2 px-3 text-end">
                                      <button
                                        type="button"
                                        onClick={() =>
                                          handleOpenReceipt(cust.customer_name, bill.outstanding_amount)
                                        }
                                        className="text-xs text-emerald-400 hover:text-emerald-300 font-semibold"
                                      >
                                        {t("acc.collections.pay_bill", { defaultValue: "Pay" })}
                                      </button>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Record Money Sheet / Modal */}
      <RecordMoneySheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        defaultMode="Receive"
        defaultPartyType="Customer"
        defaultParty={selectedParty}
        defaultAmount={selectedAmount}
        onSuccess={() => refetch()}
      />
    </Layout>
  );
};
