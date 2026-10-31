import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Calendar,
  CreditCard,
  FileText,
  Loader2,
  Receipt,
  Upload,
  UserCheck,
} from "lucide-react";
import { Layout } from "@/components/Layout";
import { apiClient } from "@/api/client";
import { useToast } from "@/components/ui/Toast";
import { todayISO } from "@/lib/format";

interface ExpenseCategoryItem {
  id: number;
  name: string;
  is_active: boolean;
}

interface SupplierItem {
  id: number;
  name: string;
  is_cash_expense: boolean;
}

interface TaxTemplateItem {
  id: number;
  title: string;
  rate?: string;
  template_type?: string;
}

interface AccountItem {
  id: number;
  name: string;
  account_type?: string;
}

export const NewExpensePage: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();

  // An expense bill proposed by the assistant arrives as router state (propose_expense).
  const seed = (location.state as { initialValues?: Record<string, unknown> } | null)?.initialValues;
  const seedSupplier = seed?.supplier ? String(seed.supplier) : "";
  const seedPaidNow = seed?.paid_now;

  // Form states
  const [categoryId, setCategoryId] = useState<string>(seed?.category ? String(seed.category) : "");
  const [isCashExpense, setIsCashExpense] = useState<boolean>(true);
  const [supplierId, setSupplierId] = useState<string>("");
  const [supplierSeeded, setSupplierSeeded] = useState<boolean>(seedSupplier === "");
  const [amount, setAmount] = useState<string>(seed?.amount ? String(seed.amount) : "");
  const [hasGst, setHasGst] = useState<boolean>(false);
  const [billNo, setBillNo] = useState<string>(seed?.bill_no ? String(seed.bill_no) : "");
  const [taxTemplateId, setTaxTemplateId] = useState<string>("");
  const [date, setDate] = useState<string>(seed?.date ? String(seed.date) : todayISO());
  const [paidNow, setPaidNow] = useState<boolean>(typeof seedPaidNow === "boolean" ? seedPaidNow : true);
  const [paidFromId, setPaidFromId] = useState<string>(seed?.paid_from ? String(seed.paid_from) : "");
  const [dueDate, setDueDate] = useState<string>(seed?.due_date ? String(seed.due_date) : todayISO());
  const [proofFile, setProofFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState<boolean>(false);

  // Fetch active expense categories
  const { data: categories = [], isLoading: catLoading } = useQuery<
    ExpenseCategoryItem[]
  >({
    queryKey: ["expense-categories-form"],
    queryFn: async () => {
      const res = await apiClient.get<
        ExpenseCategoryItem[] | { results: ExpenseCategoryItem[] }
      >("/buying/expense-categories/", {
        params: { is_active: true },
      });
      return Array.isArray(res.data) ? res.data : res.data.results || [];
    },
  });

  // Fetch suppliers
  const { data: suppliers = [], isLoading: suppLoading, isSuccess: suppliersLoaded } = useQuery<
    SupplierItem[]
  >({
    queryKey: ["suppliers-form"],
    queryFn: async () => {
      const res = await apiClient.get<
        SupplierItem[] | { results: SupplierItem[] }
      >("/buying/suppliers/", {
        params: { is_active: true },
      });
      return Array.isArray(res.data) ? res.data : res.data.results || [];
    },
    enabled: !isCashExpense || seedSupplier !== "",
  });

  // A proposed bill names its supplier. Switch to Supplier only when that
  // supplier is a named one; a cash-expense supplier stays on Cash Expense.
  useEffect(() => {
    if (supplierSeeded || !suppliersLoaded) return;
    setSupplierSeeded(true);
    const match = suppliers.find((s) => String(s.id) === seedSupplier);
    if (match && !match.is_cash_expense) {
      setIsCashExpense(false);
      setSupplierId(String(match.id));
    }
  }, [supplierSeeded, suppliersLoaded, suppliers, seedSupplier]);

  // Fetch tax templates for GST
  const { data: taxTemplates = [] } = useQuery<TaxTemplateItem[]>({
    queryKey: ["tax-templates-form"],
    queryFn: async () => {
      const res = await apiClient.get<
        TaxTemplateItem[] | { results: TaxTemplateItem[] }
      >("/accounts/tax-templates/");
      const all = Array.isArray(res.data) ? res.data : res.data.results || [];
      return all.filter((t) => !t.template_type || t.template_type === "Purchase");
    },
    enabled: hasGst,
  });

  // Fetch cash/bank accounts for Paid Now
  const { data: payAccounts = [] } = useQuery<AccountItem[]>({
    queryKey: ["pay-accounts-form"],
    queryFn: async () => {
      const res = await apiClient.get<
        AccountItem[] | { results: AccountItem[] }
      >("/accounts/accounts/", {
        params: { is_group: false },
      });
      const all = Array.isArray(res.data) ? res.data : res.data.results || [];
      return all.filter(
        (a) => a.account_type === "Cash" || a.account_type === "Bank"
      );
    },
    enabled: paidNow,
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!categoryId) {
      toast.error(
        t("acc.new_expense.category_error", {
          defaultValue: "Please select an expense category",
        })
      );
      return;
    }

    const numAmount = parseFloat(amount);
    if (isNaN(numAmount) || numAmount <= 0) {
      toast.error(
        t("acc.new_expense.amount_error", {
          defaultValue: "Please enter a valid amount greater than 0",
        })
      );
      return;
    }

    if (!isCashExpense && !supplierId) {
      toast.error(
        t("acc.new_expense.supplier_error", {
          defaultValue: "Please select a supplier or enable Cash Expense",
        })
      );
      return;
    }

    if (hasGst && !billNo.trim()) {
      toast.error(
        t("acc.new_expense.bill_no_error", {
          defaultValue: "Bill number is required when GST is enabled",
        })
      );
      return;
    }

    if (paidNow && !paidFromId) {
      toast.error(
        t("acc.new_expense.paid_from_error", {
          defaultValue: "Please select the account paid from",
        })
      );
      return;
    }

    setSubmitting(true);
    try {
      const formData = new FormData();
      formData.append("category", categoryId);
      if (!isCashExpense && supplierId) {
        formData.append("supplier", supplierId);
      }
      formData.append("amount", amount);
      formData.append("date", date);
      formData.append("has_gst", String(hasGst));
      if (hasGst) {
        formData.append("bill_no", billNo);
        if (taxTemplateId) {
          formData.append("tax_template", taxTemplateId);
        }
      } else if (billNo.trim()) {
        formData.append("bill_no", billNo);
      }

      formData.append("paid_now", String(paidNow));
      if (paidNow && paidFromId) {
        formData.append("paid_from", paidFromId);
      } else if (!paidNow && dueDate) {
        formData.append("due_date", dueDate);
      }

      if (proofFile) {
        formData.append("proof", proofFile);
      }

      await apiClient.post("/buying/expenses/", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });

      toast.success(
        t("acc.new_expense.success_message", {
          defaultValue: "Expense recorded successfully",
        })
      );
      navigate("/accounts/expenses");
    } catch (err: unknown) {
      let msg = t("acc.new_expense.error_message", {
        defaultValue: "Failed to record expense",
      });
      if (err && typeof err === "object" && "response" in err) {
        const resp = (err as { response?: { data?: Record<string, unknown> } })
          .response;
        if (resp?.data) {
          const detail = Object.values(resp.data)
            .flat()
            .join(" ");
          if (detail) msg = detail;
        }
      }
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Layout>
      <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
        {/* Back navigation & Header */}
        <div>
          <button
            type="button"
            onClick={() => navigate("/accounts/expenses")}
            className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-white transition-colors mb-3 cursor-pointer"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>
              {t("acc.new_expense.back_to_list", {
                defaultValue: "Back to Expenses",
              })}
            </span>
          </button>

          <div className="flex items-center gap-2">
            <div className="p-2 bg-indigo-500/10 text-indigo-400 rounded-lg">
              <Receipt className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-white tracking-tight">
                {t("acc.new_expense.title", {
                  defaultValue: "Record Expense",
                })}
              </h1>
              <p className="text-xs text-slate-400">
                {t("acc.new_expense.subtitle", {
                  defaultValue:
                    "Add every spend with category, payment status and proof",
                })}
              </p>
            </div>
          </div>
        </div>

        {/* Form Container */}
        <form
          onSubmit={handleSubmit}
          className="bg-slate-900 border border-slate-800 rounded-xl p-5 sm:p-6 shadow-sm space-y-6"
        >
          {/* 1. Category */}
          <div className="space-y-1.5">
            <label className="block text-xs font-semibold text-slate-200">
              {t("acc.new_expense.category_label", {
                defaultValue: "Expense Category",
              })}{" "}
              <span className="text-rose-400">*</span>
            </label>
            <select
              value={categoryId}
              onChange={(e) => setCategoryId(e.target.value)}
              required
              className="w-full bg-slate-950 border border-slate-800 text-slate-100 text-sm rounded-lg px-3 py-2.5 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              <option value="">
                {t("acc.new_expense.category_placeholder", {
                  defaultValue: "Select category...",
                })}
              </option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          {/* 2. Paid To */}
          <div className="space-y-2">
            <label className="block text-xs font-semibold text-slate-200">
              {t("acc.new_expense.paid_to_label", {
                defaultValue: "Paid To",
              })}{" "}
              <span className="text-rose-400">*</span>
            </label>

            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setIsCashExpense(true)}
                className={`flex-1 py-2 px-3 text-xs font-semibold rounded-lg border transition-colors ${
                  isCashExpense
                    ? "bg-indigo-600/20 text-indigo-300 border-indigo-500/50"
                    : "bg-slate-950 text-slate-400 border-slate-800 hover:text-slate-200"
                }`}
              >
                {t("acc.new_expense.cash_expense_toggle", {
                  defaultValue: "Cash Expense",
                })}
              </button>
              <button
                type="button"
                onClick={() => setIsCashExpense(false)}
                className={`flex-1 py-2 px-3 text-xs font-semibold rounded-lg border transition-colors ${
                  !isCashExpense
                    ? "bg-indigo-600/20 text-indigo-300 border-indigo-500/50"
                    : "bg-slate-950 text-slate-400 border-slate-800 hover:text-slate-200"
                }`}
              >
                {t("acc.new_expense.named_supplier", {
                  defaultValue: "Supplier",
                })}
              </button>
            </div>

            {!isCashExpense && (
              <div className="pt-1">
                <select
                  value={supplierId}
                  onChange={(e) => setSupplierId(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 text-slate-100 text-sm rounded-lg px-3 py-2.5 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                >
                  <option value="">
                    {t("acc.new_expense.supplier_placeholder", {
                      defaultValue: "Select supplier...",
                    })}
                  </option>
                  {suppliers
                    .filter((s) => !s.is_cash_expense)
                    .map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                </select>
              </div>
            )}
          </div>

          {/* 3. Amount & Date */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="block text-xs font-semibold text-slate-200">
                {t("acc.new_expense.amount_label", {
                  defaultValue: "Amount (₹)",
                })}{" "}
                <span className="text-rose-400">*</span>
              </label>
              <input
                type="number"
                step="0.01"
                min="0.01"
                placeholder="0.00"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                required
                className="w-full bg-slate-950 border border-slate-800 text-slate-100 text-sm font-semibold rounded-lg px-3 py-2.5 focus:outline-none focus:ring-1 focus:ring-indigo-500 tabular-nums"
              />
            </div>

            <div className="space-y-1.5">
              <label className="block text-xs font-semibold text-slate-200">
                {t("acc.new_expense.date_label", {
                  defaultValue: "Expense Date",
                })}
              </label>
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                required
                className="w-full bg-slate-950 border border-slate-800 text-slate-100 text-sm rounded-lg px-3 py-2.5 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
          </div>

          {/* 4. GST on bill? */}
          <div className="pt-2 border-t border-slate-800/80 space-y-3">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={hasGst}
                onChange={(e) => setHasGst(e.target.checked)}
                className="w-4 h-4 rounded bg-slate-950 border-slate-700 text-indigo-600 focus:ring-indigo-500"
              />
              <span className="text-xs font-semibold text-slate-200">
                {t("acc.new_expense.gst_toggle", {
                  defaultValue: "GST on bill?",
                })}
              </span>
            </label>

            {hasGst && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 bg-slate-950/60 p-3.5 border border-slate-800/60 rounded-lg">
                <div className="space-y-1.5">
                  <label className="block text-xs font-medium text-slate-300">
                    {t("acc.new_expense.bill_no_label", {
                      defaultValue: "Bill Number",
                    })}{" "}
                    <span className="text-rose-400">*</span>
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. INV-9872"
                    value={billNo}
                    onChange={(e) => setBillNo(e.target.value)}
                    required={hasGst}
                    className="w-full bg-slate-950 border border-slate-800 text-slate-100 text-xs rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="block text-xs font-medium text-slate-300">
                    {t("acc.new_expense.tax_template_label", {
                      defaultValue: "GST Rate / Template",
                    })}
                  </label>
                  <select
                    value={taxTemplateId}
                    onChange={(e) => setTaxTemplateId(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 text-slate-100 text-xs rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                  >
                    <option value="">
                      {t("acc.new_expense.tax_template_placeholder", {
                        defaultValue: "Select tax template...",
                      })}
                    </option>
                    {taxTemplates.map((tmpl) => (
                      <option key={tmpl.id} value={tmpl.id}>
                        {tmpl.title}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )}
          </div>

          {/* 5. Payment Mode */}
          <div className="pt-2 border-t border-slate-800/80 space-y-3">
            <label className="block text-xs font-semibold text-slate-200">
              {t("acc.new_expense.payment_mode_label", {
                defaultValue: "Payment Mode",
              })}
            </label>

            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setPaidNow(true)}
                className={`flex-1 py-2 px-3 text-xs font-semibold rounded-lg border transition-colors ${
                  paidNow
                    ? "bg-emerald-600/20 text-emerald-300 border-emerald-500/50"
                    : "bg-slate-950 text-slate-400 border-slate-800 hover:text-slate-200"
                }`}
              >
                {t("acc.new_expense.paid_now", {
                  defaultValue: "Paid Now",
                })}
              </button>
              <button
                type="button"
                onClick={() => setPaidNow(false)}
                className={`flex-1 py-2 px-3 text-xs font-semibold rounded-lg border transition-colors ${
                  !paidNow
                    ? "bg-amber-600/20 text-amber-300 border-amber-500/50"
                    : "bg-slate-950 text-slate-400 border-slate-800 hover:text-slate-200"
                }`}
              >
                {t("acc.new_expense.pay_later", {
                  defaultValue: "Pay Later",
                })}
              </button>
            </div>

            {paidNow ? (
              <div className="space-y-1.5 pt-1">
                <label className="block text-xs font-medium text-slate-300">
                  {t("acc.new_expense.paid_from_label", {
                    defaultValue: "Paid From (Bank / Cash)",
                  })}{" "}
                  <span className="text-rose-400">*</span>
                </label>
                <select
                  value={paidFromId}
                  onChange={(e) => setPaidFromId(e.target.value)}
                  required={paidNow}
                  className="w-full bg-slate-950 border border-slate-800 text-slate-100 text-sm rounded-lg px-3 py-2.5 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                >
                  <option value="">
                    {t("acc.new_expense.paid_from_placeholder", {
                      defaultValue: "Select account...",
                    })}
                  </option>
                  {payAccounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} ({a.account_type})
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <div className="space-y-1.5 pt-1">
                <label className="block text-xs font-medium text-slate-300">
                  {t("acc.new_expense.due_date_label", {
                    defaultValue: "Payment Due Date",
                  })}
                </label>
                <input
                  type="date"
                  value={dueDate}
                  onChange={(e) => setDueDate(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 text-slate-100 text-sm rounded-lg px-3 py-2.5 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </div>
            )}
          </div>

          {/* 6. Proof Upload */}
          <div className="pt-2 border-t border-slate-800/80 space-y-1.5">
            <label className="block text-xs font-semibold text-slate-200">
              {t("acc.new_expense.proof_label", {
                defaultValue: "Proof (Receipt / Bill Photo or PDF)",
              })}
            </label>
            <div className="flex items-center gap-3">
              <label className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-slate-950 border border-dashed border-slate-700 hover:border-slate-500 text-slate-300 text-xs rounded-lg cursor-pointer transition-colors">
                <Upload className="w-4 h-4 text-slate-400" />
                <span>
                  {proofFile
                    ? proofFile.name
                    : t("acc.new_expense.proof_hint", {
                        defaultValue: "Choose photo or PDF...",
                      })}
                </span>
                <input
                  type="file"
                  accept="image/*,application/pdf"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0] || null;
                    setProofFile(file);
                  }}
                />
              </label>
              {proofFile && (
                <button
                  type="button"
                  onClick={() => setProofFile(null)}
                  className="text-xs text-rose-400 hover:text-rose-300 p-2"
                >
                  Clear
                </button>
              )}
            </div>
          </div>

          {/* Action Buttons */}
          <div className="pt-4 border-t border-slate-800 flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={() => navigate("/accounts/expenses")}
              className="px-4 py-2 text-xs font-medium text-slate-400 hover:text-white transition-colors"
            >
              {t("acc.record_money.cancel", { defaultValue: "Cancel" })}
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="inline-flex items-center gap-2 px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-semibold rounded-lg shadow-sm transition-colors cursor-pointer"
            >
              {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              <span>
                {submitting
                  ? t("acc.new_expense.submitting", {
                      defaultValue: "Recording...",
                    })
                  : t("acc.new_expense.submit", {
                      defaultValue: "Save & Submit Expense",
                    })}
              </span>
            </button>
          </div>
        </form>
      </div>
    </Layout>
  );
};
