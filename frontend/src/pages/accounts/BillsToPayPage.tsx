import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  AlertTriangle,
  ArrowUpRight,
  Calendar,
  Clock,
  FileSpreadsheet,
  Filter,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
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
  supplier_id: number;
  supplier_name: string;
  supplier: string;
  bill_no: string | null;
  bill_date: string | null;
  posting_date: string;
  due_date: string | null;
  rounded_total: string;
  outstanding_amount: string;
  days_left: number;
  days_since_posting: number;
  is_overdue: boolean;
  gst_180_warning: boolean;
  is_expense: boolean;
}

interface BillsToPayData {
  bills: BillItem[];
  totals: {
    total_payables: string;
    total_overdue: string;
    due_this_week: string;
    due_this_month: string;
    bill_count: number;
    filtered_count: number;
    gst_warning_count: number;
  };
}

type FilterType = "" | "overdue" | "week" | "month";

export const BillsToPayPage: React.FC = () => {
  const { t } = useTranslation();
  const { activeCompany } = useCompany();
  const [filterType, setFilterType] = useState<FilterType>("");
  const [searchTerm, setSearchTerm] = useState("");

  // Record Money modal state
  const [sheetOpen, setSheetOpen] = useState(false);
  const [selectedParty, setSelectedParty] = useState("");
  const [selectedAmount, setSelectedAmount] = useState<string | number>("");

  const { data, isLoading, isFetching, error, refetch } = useQuery<BillsToPayData>({
    queryKey: ["accounts", "bills-to-pay", activeCompany?.name, filterType],
    queryFn: async () => {
      const res = await apiClient.get("/accounts/bills-to-pay/", {
        params: {
          company: activeCompany?.name,
          as_on: todayISO(),
          filter: filterType || undefined,
        },
      });
      return res.data;
    },
    enabled: !!activeCompany,
  });

  const handleOpenPayment = (supplierName?: string, amount?: string | number) => {
    setSelectedParty(supplierName || "");
    setSelectedAmount(amount || "");
    setSheetOpen(true);
  };

  const bills = data?.bills || [];
  const totals = data?.totals || {
    total_payables: "0.00",
    total_overdue: "0.00",
    due_this_week: "0.00",
    due_this_month: "0.00",
    bill_count: 0,
    filtered_count: 0,
    gst_warning_count: 0,
  };

  const filteredBills = useMemo(() => {
    if (!searchTerm.trim()) return bills;
    const q = searchTerm.toLowerCase();
    return bills.filter(
      (b) =>
        b.supplier_name.toLowerCase().includes(q) ||
        (b.bill_no && b.bill_no.toLowerCase().includes(q)) ||
        b.invoice.toLowerCase().includes(q)
    );
  }, [bills, searchTerm]);

  return (
    <Layout>
      <div className="space-y-6">
        {/* Page Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
              {t("acc.bills_to_pay.title", { defaultValue: "Bills to Pay" })}
              {isFetching && <Loader2 className="w-5 h-5 animate-spin text-amber-400" />}
            </h1>
            <p className="text-sm text-slate-400 mt-1">
              {t("acc.bills_to_pay.subtitle", {
                defaultValue:
                  "Track pending supplier bills, payment schedules, and 180-day GST credit warnings",
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
              onClick={() => handleOpenPayment()}
              className="inline-flex items-center gap-2 px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white text-sm font-semibold rounded-lg shadow-sm transition-colors"
            >
              <Plus className="w-4 h-4" />
              {t("acc.bills_to_pay.record_payment", { defaultValue: "Record Payment" })}
            </button>
          </div>
        </div>

        {/* Summary Metric Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
            <div className="text-xs font-medium text-slate-400 uppercase tracking-wider">
              {t("acc.bills_to_pay.total_payables", { defaultValue: "Total Payables" })}
            </div>
            <div className="mt-2 text-2xl font-bold text-white tabular-nums">
              {formatIndianCurrency(totals.total_payables)}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {totals.bill_count}{" "}
              {t("acc.bills_to_pay.bills_count", { defaultValue: "open bills" })}
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
            <div className="text-xs font-medium text-rose-400 uppercase tracking-wider flex items-center justify-between">
              <span>{t("acc.bills_to_pay.total_overdue", { defaultValue: "Total Overdue" })}</span>
              <AlertCircle className="w-4 h-4 text-rose-400" />
            </div>
            <div className="mt-2 text-2xl font-bold text-rose-300 tabular-nums">
              {formatIndianCurrency(totals.total_overdue)}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {t("acc.bills_to_pay.needs_immediate_payment", {
                defaultValue: "Past due date",
              })}
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
            <div className="text-xs font-medium text-amber-400 uppercase tracking-wider flex items-center justify-between">
              <span>{t("acc.bills_to_pay.due_this_week", { defaultValue: "Due This Week" })}</span>
              <Clock className="w-4 h-4 text-amber-400" />
            </div>
            <div className="mt-2 text-2xl font-bold text-amber-300 tabular-nums">
              {formatIndianCurrency(totals.due_this_week)}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {t("acc.bills_to_pay.next_7_days", { defaultValue: "Next 7 days" })}
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
            <div className="text-xs font-medium text-blue-400 uppercase tracking-wider flex items-center justify-between">
              <span>{t("acc.bills_to_pay.due_this_month", { defaultValue: "Due This Month" })}</span>
              <Calendar className="w-4 h-4 text-blue-400" />
            </div>
            <div className="mt-2 text-2xl font-bold text-blue-300 tabular-nums">
              {formatIndianCurrency(totals.due_this_month)}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {t("acc.bills_to_pay.next_30_days", { defaultValue: "Next 30 days" })}
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
            <div className="text-xs font-medium text-orange-400 uppercase tracking-wider flex items-center justify-between">
              <span>{t("acc.bills_to_pay.gst_warning_count", { defaultValue: "180-Day GST Risks" })}</span>
              <ShieldAlert className="w-4 h-4 text-orange-400" />
            </div>
            <div className="mt-2 text-2xl font-bold text-orange-300 tabular-nums">
              {totals.gst_warning_count}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {t("acc.bills_to_pay.itc_reversal_risk", { defaultValue: "ITC Reversal risk" })}
            </div>
          </div>
        </div>

        {/* Filter Tabs & Search Controls */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
          <div className="flex items-center gap-1.5 p-1 bg-slate-900 border border-slate-800 rounded-lg">
            <button
              type="button"
              onClick={() => setFilterType("")}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                filterType === ""
                  ? "bg-slate-800 text-white shadow-sm font-semibold"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {t("acc.bills_to_pay.filter_all", { defaultValue: "All Bills" })}
            </button>
            <button
              type="button"
              onClick={() => setFilterType("overdue")}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                filterType === "overdue"
                  ? "bg-rose-500/20 text-rose-300 border border-rose-500/30 font-semibold"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {t("acc.bills_to_pay.filter_overdue", { defaultValue: "Overdue" })}
            </button>
            <button
              type="button"
              onClick={() => setFilterType("week")}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                filterType === "week"
                  ? "bg-amber-500/20 text-amber-300 border border-amber-500/30 font-semibold"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {t("acc.bills_to_pay.filter_week", { defaultValue: "This Week" })}
            </button>
            <button
              type="button"
              onClick={() => setFilterType("month")}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                filterType === "month"
                  ? "bg-blue-500/20 text-blue-300 border border-blue-500/30 font-semibold"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {t("acc.bills_to_pay.filter_month", { defaultValue: "This Month" })}
            </button>
          </div>

          <div className="relative flex-1 max-w-sm">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder={t("acc.bills_to_pay.search_placeholder", {
                defaultValue: "Search supplier or bill number...",
              })}
              className="w-full bg-slate-900 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-amber-500"
            />
          </div>
        </div>

        {/* Bills List / Table */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
          {error && (
            <div className="p-6 text-center text-rose-400 text-sm">
              {summariseError(normaliseError(error))}
            </div>
          )}

          {isLoading ? (
            <div className="p-12 flex flex-col items-center justify-center text-slate-400 gap-3">
              <Loader2 className="w-6 h-6 animate-spin text-amber-400" />
              <span className="text-sm">
                {t("common.loading", { defaultValue: "Loading bills to pay..." })}
              </span>
            </div>
          ) : filteredBills.length === 0 ? (
            <div className="p-12 text-center text-slate-400 text-sm">
              {t("acc.bills_to_pay.no_bills", {
                defaultValue: "No pending bills to pay found.",
              })}
            </div>
          ) : (
            <div className="divide-y divide-slate-800/80">
              {filteredBills.map((bill) => {
                return (
                  <div
                    key={bill.name}
                    className="p-4 sm:p-5 flex flex-col md:flex-row md:items-center justify-between gap-4 hover:bg-slate-800/30 transition-colors"
                  >
                    {/* Bill Info */}
                    <div className="flex-1 space-y-1.5">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Link
                          to={`/selling/party/supplier/${bill.supplier_id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="font-semibold text-white text-base hover:underline"
                        >
                          {bill.supplier_name}
                        </Link>
                        {bill.bill_no && (
                          <span className="px-2 py-0.5 rounded text-xs font-mono bg-slate-800 text-slate-300 border border-slate-700">
                            Bill #{bill.bill_no}
                          </span>
                        )}
                        <span className="text-xs text-slate-500 font-mono">
                          {bill.invoice}
                        </span>
                        {bill.gst_180_warning && (
                          <span
                            title={t("acc.bills_to_pay.gst_180_tooltip", {
                              defaultValue:
                                "Invoice has GST and is approaching or past 180 days unpaid. ITC must be reversed if not paid within 180 days.",
                            })}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-semibold bg-orange-500/15 text-orange-400 border border-orange-500/30 cursor-help"
                          >
                            <AlertTriangle className="w-3 h-3" />
                            {t("acc.bills_to_pay.gst_180_badge", {
                              defaultValue: "180-Day GST Risk",
                            })}
                            {" ("}
                            {bill.days_since_posting}d{")"}
                          </span>
                        )}
                      </div>

                      <div className="flex items-center gap-4 text-xs text-slate-400 flex-wrap">
                        <span className="flex items-center gap-1">
                          <Calendar className="w-3.5 h-3.5 text-slate-500" />
                          {t("acc.bills_to_pay.bill_date", { defaultValue: "Bill Date" })}:{" "}
                          {formatDate(bill.bill_date || bill.posting_date)}
                        </span>
                        <span className="flex items-center gap-1">
                          <Clock className="w-3.5 h-3.5 text-slate-500" />
                          {t("acc.bills_to_pay.due_date", { defaultValue: "Due Date" })}:{" "}
                          {bill.due_date ? formatDate(bill.due_date) : "-"}
                        </span>
                        <span>
                          {bill.is_overdue ? (
                            <span className="text-rose-400 font-semibold">
                              {Math.abs(bill.days_left)}{" "}
                              {t("acc.bills_to_pay.days_overdue", { defaultValue: "days overdue" })}
                            </span>
                          ) : bill.days_left === 0 ? (
                            <span className="text-amber-400 font-semibold">
                              {t("acc.bills_to_pay.due_today", { defaultValue: "Due Today" })}
                            </span>
                          ) : (
                            <span className="text-emerald-400 font-medium">
                              {bill.days_left}{" "}
                              {t("acc.bills_to_pay.days_left", { defaultValue: "days left" })}
                            </span>
                          )}
                        </span>
                      </div>
                    </div>

                    {/* Amount & Actions */}
                    <div className="flex items-center justify-between md:justify-end gap-5 flex-wrap">
                      <div className="text-end">
                        <div className="text-xs text-slate-400">
                          {t("acc.bills_to_pay.outstanding", { defaultValue: "Outstanding" })}
                        </div>
                        <div className="text-base font-bold text-white tabular-nums">
                          {formatIndianCurrency(bill.outstanding_amount)}
                        </div>
                        {bill.rounded_total !== bill.outstanding_amount && (
                          <div className="text-xs text-slate-500 tabular-nums">
                            {t("acc.bills_to_pay.bill_total", { defaultValue: "Total" })}:{" "}
                            {formatIndianCurrency(bill.rounded_total)}
                          </div>
                        )}
                      </div>

                      <button
                        type="button"
                        onClick={() =>
                          handleOpenPayment(bill.supplier_name, bill.outstanding_amount)
                        }
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-amber-600/90 hover:bg-amber-600 text-white text-xs font-semibold rounded-lg shadow-sm transition-colors"
                      >
                        <ArrowUpRight className="w-3.5 h-3.5" />
                        {t("acc.bills_to_pay.record_payment", { defaultValue: "Record Payment" })}
                      </button>
                    </div>
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
        defaultMode="Pay"
        defaultPartyType="Supplier"
        defaultParty={selectedParty}
        defaultAmount={selectedAmount}
        onSuccess={() => refetch()}
      />
    </Layout>
  );
};
