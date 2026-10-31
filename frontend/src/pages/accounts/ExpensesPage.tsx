import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import {
  Calendar,
  CheckCircle2,
  Clock,
  Filter,
  Loader2,
  Plus,
  Receipt,
  Search,
  Wallet,
} from "lucide-react";
import { Layout } from "@/components/Layout";
import { apiClient } from "@/api/client";
import { formatDate, formatIndianCurrency, todayISO } from "@/lib/format";

interface ExpenseItem {
  id: number;
  name: string;
  posting_date: string;
  due_date: string | null;
  bill_no: string | null;
  expense_category: number | null;
  expense_category_name: string | null;
  supplier: number;
  supplier_name: string;
  grand_total: string;
  outstanding_amount: string;
  payment_status: string;
  payment_label: string;
  is_expense: boolean;
}

interface ExpenseCategoryItem {
  id: number;
  name: string;
  is_active: boolean;
}

interface ExpenseSummaryReport {
  grand_totals: {
    total: string;
    paid: string;
    due: string;
    last_month_total: string;
  };
}

export const ExpensesPage: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();

  // Current month default: "YYYY-MM"
  const currentMonthStr = useMemo(() => todayISO().slice(0, 7), []);
  const [selectedMonth, setSelectedMonth] = useState<string>(currentMonthStr);
  const [selectedCategory, setSelectedCategory] = useState<string>("");
  const [selectedStatus, setSelectedStatus] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState<string>("");

  // Derived date bounds for the summary endpoint
  const { fromDate, toDate } = useMemo(() => {
    if (!selectedMonth) {
      const today = todayISO();
      return { fromDate: `${today.slice(0, 7)}-01`, toDate: today };
    }
    const [y, m] = selectedMonth.split("-").map(Number);
    const lastDay = new Date(y, m, 0).getDate();
    return {
      fromDate: `${selectedMonth}-01`,
      toDate: `${selectedMonth}-${String(lastDay).padStart(2, "0")}`,
    };
  }, [selectedMonth]);

  // Fetch summary report for the month totals
  const { data: summaryData, isLoading: summaryLoading } =
    useQuery<ExpenseSummaryReport>({
      queryKey: ["expense-summary", fromDate, toDate],
      queryFn: async () => {
        const res = await apiClient.get<ExpenseSummaryReport>(
          "/api/accounts/reports/expense-summary/",
          {
            params: { from_date: fromDate, to_date: toDate },
          }
        );
        return res.data;
      },
    });

  // Fetch categories for the filter
  const { data: categories = [] } = useQuery<ExpenseCategoryItem[]>({
    queryKey: ["expense-categories"],
    queryFn: async () => {
      const res = await apiClient.get<
        ExpenseCategoryItem[] | { results: ExpenseCategoryItem[] }
      >("/api/buying/expense-categories/", {
        params: { is_active: true },
      });
      return Array.isArray(res.data) ? res.data : res.data.results || [];
    },
  });

  // Fetch expenses list
  const {
    data: expensesData,
    isLoading: expensesLoading,
    refetch,
  } = useQuery<ExpenseItem[]>({
    queryKey: ["expenses-list", selectedMonth, selectedCategory, selectedStatus],
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (selectedMonth) params.month = selectedMonth;
      if (selectedCategory) params.category = selectedCategory;
      if (selectedStatus) params.payment_status = selectedStatus;

      const res = await apiClient.get<
        ExpenseItem[] | { results: ExpenseItem[] }
      >("/api/buying/expenses/", { params });
      return Array.isArray(res.data) ? res.data : res.data.results || [];
    },
  });

  const expenses = expensesData || [];

  // Filter in client by search query
  const filteredExpenses = useMemo(() => {
    if (!searchQuery.trim()) return expenses;
    const q = searchQuery.toLowerCase().trim();
    return expenses.filter(
      (e) =>
        e.supplier_name?.toLowerCase().includes(q) ||
        e.expense_category_name?.toLowerCase().includes(q) ||
        e.bill_no?.toLowerCase().includes(q) ||
        e.name?.toLowerCase().includes(q)
    );
  }, [expenses, searchQuery]);

  const totals = summaryData?.grand_totals || {
    total: "0.00",
    paid: "0.00",
    due: "0.00",
    last_month_total: "0.00",
  };

  return (
    <Layout>
      <div className="space-y-6 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Page Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-2 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <div className="p-2 bg-indigo-500/10 text-indigo-400 rounded-lg">
                <Receipt className="w-6 h-6" />
              </div>
              <h1 className="text-2xl font-bold text-white tracking-tight">
                {t("acc.expenses.title", { defaultValue: "Expenses" })}
              </h1>
            </div>
            <p className="mt-1 text-sm text-slate-400">
              {t("acc.expenses.subtitle", {
                defaultValue:
                  "Track, record and categorize all business spends with proof",
              })}
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => navigate("/accounts/expenses/new")}
              className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium rounded-lg shadow-sm transition-colors cursor-pointer"
            >
              <Plus className="w-4 h-4" />
              <span>
                {t("acc.expenses.new_expense", {
                  defaultValue: "New Expense",
                })}
              </span>
            </button>
          </div>
        </div>

        {/* Summary Metric Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
            <div className="text-xs font-medium text-slate-400 uppercase tracking-wider flex items-center justify-between">
              <span>
                {t("acc.expenses.month_total", {
                  defaultValue: "Month Total",
                })}
              </span>
              <Calendar className="w-4 h-4 text-slate-500" />
            </div>
            <div className="mt-2 text-2xl font-bold text-white tabular-nums">
              {summaryLoading ? (
                <span className="text-slate-500 text-lg">...</span>
              ) : (
                formatIndianCurrency(totals.total)
              )}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {selectedMonth || currentMonthStr}
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
            <div className="text-xs font-medium text-emerald-400 uppercase tracking-wider flex items-center justify-between">
              <span>{t("acc.expenses.paid", { defaultValue: "Paid" })}</span>
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            </div>
            <div className="mt-2 text-2xl font-bold text-emerald-300 tabular-nums">
              {summaryLoading ? (
                <span className="text-slate-500 text-lg">...</span>
              ) : (
                formatIndianCurrency(totals.paid)
              )}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {t("acc.expenses.paid_status", { defaultValue: "Settled" })}
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
            <div className="text-xs font-medium text-amber-400 uppercase tracking-wider flex items-center justify-between">
              <span>{t("acc.expenses.due", { defaultValue: "Due" })}</span>
              <Clock className="w-4 h-4 text-amber-400" />
            </div>
            <div className="mt-2 text-2xl font-bold text-amber-300 tabular-nums">
              {summaryLoading ? (
                <span className="text-slate-500 text-lg">...</span>
              ) : (
                formatIndianCurrency(totals.due)
              )}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {t("acc.expenses.due_status", {
                defaultValue: "Pending payment",
              })}
            </div>
          </div>
        </div>

        {/* Filter Bar */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-3 sm:space-y-0 sm:flex sm:items-center sm:justify-between gap-4">
          <div className="flex flex-wrap items-center gap-3">
            {/* Month Picker */}
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-slate-400">
                {t("acc.expenses.filter_month", { defaultValue: "Month" })}:
              </span>
              <input
                type="month"
                value={selectedMonth}
                onChange={(e) => setSelectedMonth(e.target.value)}
                className="bg-slate-950 border border-slate-700 text-slate-200 text-xs rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>

            {/* Category Dropdown */}
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-slate-400">
                {t("acc.expenses.category", { defaultValue: "Category" })}:
              </span>
              <select
                value={selectedCategory}
                onChange={(e) => setSelectedCategory(e.target.value)}
                className="bg-slate-950 border border-slate-700 text-slate-200 text-xs rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500 max-w-[160px]"
              >
                <option value="">
                  {t("acc.expenses.filter_category", {
                    defaultValue: "All Categories",
                  })}
                </option>
                {categories.map((cat) => (
                  <option key={cat.id} value={cat.id}>
                    {cat.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Status Pills */}
            <div className="flex items-center gap-1 bg-slate-950 p-1 border border-slate-800 rounded-lg">
              <button
                type="button"
                onClick={() => setSelectedStatus("")}
                className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                  selectedStatus === ""
                    ? "bg-slate-800 text-white"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {t("acc.expenses.all_bills", { defaultValue: "All" })}
              </button>
              <button
                type="button"
                onClick={() => setSelectedStatus("Paid")}
                className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                  selectedStatus === "Paid"
                    ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {t("acc.expenses.paid", { defaultValue: "Paid" })}
              </button>
              <button
                type="button"
                onClick={() => setSelectedStatus("Due")}
                className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                  selectedStatus === "Due"
                    ? "bg-amber-500/20 text-amber-300 border border-amber-500/30"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {t("acc.expenses.due", { defaultValue: "Due" })}
              </button>
            </div>
          </div>

          {/* Search Box */}
          <div className="relative min-w-[200px]">
            <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              placeholder={t("acc.expenses.search_placeholder", {
                defaultValue: "Search expenses, suppliers, bills...",
              })}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>
        </div>

        {/* Expenses List */}
        {expensesLoading ? (
          <div className="flex flex-col items-center justify-center py-16 bg-slate-900 border border-slate-800 rounded-xl">
            <Loader2 className="w-8 h-8 text-indigo-400 animate-spin" />
            <span className="mt-3 text-sm text-slate-400">Loading expenses...</span>
          </div>
        ) : filteredExpenses.length === 0 ? (
          <div className="text-center py-16 bg-slate-900 border border-slate-800 rounded-xl px-4">
            <Receipt className="w-10 h-10 text-slate-600 mx-auto mb-3" />
            <h3 className="text-sm font-semibold text-white">
              {t("acc.expenses.no_expenses", {
                defaultValue: "No expenses found for this period.",
              })}
            </h3>
            <p className="mt-1 text-xs text-slate-400 max-w-sm mx-auto">
              Click &quot;New Expense&quot; above to record your first spend for this period.
            </p>
          </div>
        ) : (
          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
            {/* Desktop Table View */}
            <div className="hidden sm:block overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="bg-slate-950/70 border-b border-slate-800 text-slate-400">
                    <th className="py-3 px-4 font-semibold">
                      {t("acc.expenses.date", { defaultValue: "Date" })}
                    </th>
                    <th className="py-3 px-4 font-semibold">
                      {t("acc.expenses.category", { defaultValue: "Category" })}
                    </th>
                    <th className="py-3 px-4 font-semibold">
                      {t("acc.expenses.paid_to", { defaultValue: "Paid To" })}
                    </th>
                    <th className="py-3 px-4 font-semibold">
                      {t("acc.expenses.bill_no", { defaultValue: "Bill No" })}
                    </th>
                    <th className="py-3 px-4 font-semibold text-right">
                      {t("acc.expenses.amount", { defaultValue: "Amount" })}
                    </th>
                    <th className="py-3 px-4 font-semibold text-center">
                      {t("acc.expenses.status", { defaultValue: "Status" })}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {filteredExpenses.map((exp) => {
                    const isPaid =
                      exp.payment_status === "Paid" ||
                      exp.payment_label === "Paid";
                    return (
                      <tr
                        key={exp.id}
                        className="hover:bg-slate-800/40 transition-colors"
                      >
                        <td className="py-3 px-4 text-slate-300 font-medium whitespace-nowrap">
                          {formatDate(exp.posting_date)}
                        </td>
                        <td className="py-3 px-4">
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-slate-800 text-slate-300 border border-slate-700">
                            {exp.expense_category_name || "—"}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-slate-200 font-medium max-w-[200px] truncate">
                          {exp.supplier_name || "—"}
                        </td>
                        <td className="py-3 px-4 text-slate-400 whitespace-nowrap">
                          {exp.bill_no || "—"}
                        </td>
                        <td className="py-3 px-4 text-right font-semibold text-white tabular-nums whitespace-nowrap">
                          {formatIndianCurrency(exp.grand_total)}
                        </td>
                        <td className="py-3 px-4 text-center whitespace-nowrap">
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                              isPaid
                                ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"
                                : "bg-amber-500/15 text-amber-400 border border-amber-500/30"
                            }`}
                          >
                            {isPaid ? "Paid" : "Due"}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Mobile Cards View */}
            <div className="sm:hidden divide-y divide-slate-800">
              {filteredExpenses.map((exp) => {
                const isPaid =
                  exp.payment_status === "Paid" ||
                  exp.payment_label === "Paid";
                return (
                  <div key={exp.id} className="p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-slate-400 font-medium">
                        {formatDate(exp.posting_date)}
                      </span>
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                          isPaid
                            ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"
                            : "bg-amber-500/15 text-amber-400 border border-amber-500/30"
                        }`}
                      >
                        {isPaid ? "Paid" : "Due"}
                      </span>
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="font-semibold text-white text-sm">
                        {exp.supplier_name}
                      </div>
                      <div className="font-bold text-white text-sm tabular-nums">
                        {formatIndianCurrency(exp.grand_total)}
                      </div>
                    </div>

                    <div className="flex items-center justify-between text-xs text-slate-400 pt-1">
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">
                        {exp.expense_category_name || "—"}
                      </span>
                      <span>
                        {exp.bill_no
                          ? `Bill: ${exp.bill_no}`
                          : exp.name}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
};
