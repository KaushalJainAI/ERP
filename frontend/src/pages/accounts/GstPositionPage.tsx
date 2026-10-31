import React, { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Calendar,
  CheckCircle2,
  Clock,
  CreditCard,
  FileCheck,
  FileSpreadsheet,
  FileText,
  Loader2,
  Plus,
  RefreshCw,
  ShieldAlert,
  Upload,
  Wallet,
  X,
} from "lucide-react";
import { Layout } from "@/components/Layout";
import { apiClient } from "@/api/client";
import { normaliseError, summariseError } from "@/api/endpoints";
import { useCompany } from "@/context/CompanyContext";
import { formatDate, formatIndianCurrency, todayISO } from "@/lib/format";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";

interface PaymentRecord {
  id: number;
  name: string;
  challan_no: string;
  posting_date: string;
  cash: string;
  credit_used: string;
}

interface GstPositionData {
  company: number;
  company_name: string;
  month: string;
  period_start: string;
  period_end: string;
  output_tax: string;
  output_igst: string;
  output_cgst: string;
  output_sgst: string;
  input_credit: string;
  input_igst: string;
  input_cgst: string;
  input_sgst: string;
  net_payable: string;
  paid: string;
  paid_cash: string;
  paid_credit: string;
  status: "Paid" | "Due";
  due_date: string;
  payments: PaymentRecord[];
}

interface CreditAtRiskBill {
  id: number;
  name: string;
  supplier_name: string;
  bill_date: string | null;
  posting_date: string;
  days_unpaid: number;
  gst_amount: string;
  outstanding_amount: string;
  status: "Approaching 180 days" | "Reversed";
  days_left: number;
}

interface AccountItem {
  id: number;
  name: string;
  account_type?: string;
}

export const GstPositionPage: React.FC = () => {
  const { t } = useTranslation();
  const { activeCompany } = useCompany();
  const toast = useToast();
  const queryClient = useQueryClient();

  const currentMonthStr = useMemo(() => todayISO().slice(0, 7), []);
  const [selectedMonth, setSelectedMonth] = useState<string>(currentMonthStr);
  const [activeTab, setActiveTab] = useState<"overview" | "risk">("overview");

  // Modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [challanNo, setChallanNo] = useState("");
  const [postingDate, setPostingDate] = useState(todayISO());
  const [paidFromId, setPaidFromId] = useState<number | "">("");
  const [igstCash, setIgstCash] = useState("");
  const [cgstCash, setCgstCash] = useState("");
  const [sgstCash, setSgstCash] = useState("");
  const [igstCredit, setIgstCredit] = useState("");
  const [cgstCredit, setCgstCredit] = useState("");
  const [sgstCredit, setSgstCredit] = useState("");
  const [proofFile, setProofFile] = useState<File | null>(null);

  // 1. Fetch GST Position for selected month
  const {
    data: positionData,
    isLoading: positionLoading,
    isFetching: positionFetching,
    refetch: refetchPosition,
  } = useQuery<GstPositionData>({
    queryKey: ["gst-position", activeCompany?.name, selectedMonth],
    queryFn: async () => {
      const res = await apiClient.get<GstPositionData>(
        "/accounts/reports/gst-position/",
        {
          params: {
            company: activeCompany?.name,
            month: selectedMonth,
          },
        }
      );
      return res.data;
    },
    enabled: !!activeCompany?.name && !!selectedMonth,
  });

  // 2. Fetch Credit at Risk (180-day watch)
  const {
    data: riskBills = [],
    isLoading: riskLoading,
    refetch: refetchRisk,
  } = useQuery<CreditAtRiskBill[]>({
    queryKey: ["gst-credit-at-risk", activeCompany?.name],
    queryFn: async () => {
      const res = await apiClient.get<CreditAtRiskBill[]>(
        "/accounts/reports/gst-credit-at-risk/",
        {
          params: { company: activeCompany?.name },
        }
      );
      return res.data;
    },
    enabled: !!activeCompany?.name,
  });

  // 3. Fetch bank / cash accounts for payment modal
  const { data: bankAccounts = [] } = useQuery<AccountItem[]>({
    queryKey: ["bank-accounts-for-tax", activeCompany?.name],
    queryFn: async () => {
      const res = await apiClient.get<AccountItem[] | { results: AccountItem[] }>(
        "/accounts/accounts/",
        {
          params: {
            company: activeCompany?.name,
            is_group: false,
          },
        }
      );
      const all = Array.isArray(res.data) ? res.data : res.data.results || [];
      return all.filter(
        (a) => a.account_type === "Bank" || a.account_type === "Cash"
      );
    },
    enabled: modalOpen && !!activeCompany?.name,
  });

  const handleOpenModal = () => {
    setChallanNo("");
    setPostingDate(todayISO());
    setPaidFromId("");
    setIgstCash("");
    setCgstCash("");
    setSgstCash("");
    setIgstCredit("");
    setCgstCredit("");
    setSgstCredit("");
    setProofFile(null);
    setModalOpen(true);
  };

  const handleRecordPayment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!challanNo.trim()) {
      toast.error(
        t("acc.gst_position.challan_required", {
          defaultValue: "Challan Number is required",
        })
      );
      return;
    }

    const ic = parseFloat(igstCash) || 0;
    const cc = parseFloat(cgstCash) || 0;
    const sc = parseFloat(sgstCash) || 0;
    const totalCash = ic + cc + sc;

    const icr = parseFloat(igstCredit) || 0;
    const ccr = parseFloat(cgstCredit) || 0;
    const scr = parseFloat(sgstCredit) || 0;
    const totalCredit = icr + ccr + scr;

    if (totalCash + totalCredit <= 0) {
      toast.error(
        t("acc.gst_position.amount_required", {
          defaultValue: "Please enter at least one non-zero cash or credit amount",
        })
      );
      return;
    }

    if (totalCash > 0 && !paidFromId) {
      toast.error(
        t("acc.gst_position.account_required", {
          defaultValue: "Please select the Bank / Cash account paid from",
        })
      );
      return;
    }

    try {
      setSubmitting(true);
      const periodDate = `${selectedMonth}-01`;

      // 1. Create TaxPayment document
      const payload: Record<string, unknown> = {
        company: activeCompany?.id || activeCompany?.name,
        tax_type: "GST",
        period: periodDate,
        posting_date: postingDate,
        challan_no: challanNo.trim(),
        paid_from: paidFromId || null,
        igst_cash: ic.toFixed(2),
        cgst_cash: cc.toFixed(2),
        sgst_cash: sc.toFixed(2),
        igst_credit_used: icr.toFixed(2),
        cgst_credit_used: ccr.toFixed(2),
        sgst_credit_used: scr.toFixed(2),
      };

      const createRes = await apiClient.post("/accounts/tax-payments/", payload);
      const tp = createRes.data;

      // 2. Upload proof attachment if provided
      if (proofFile && tp.id) {
        const formData = new FormData();
        formData.append("file", proofFile);
        formData.append("model", "accounts.TaxPayment");
        formData.append("object_id", String(tp.id));
        formData.append("description", `Challan proof for ${challanNo.trim()}`);
        await apiClient.post("/core/attachments/", formData, {
          headers: { "Content-Type": "multipart/form-data" },
        });
      }

      // 3. Submit TaxPayment
      await apiClient.post(`/accounts/tax-payments/${tp.id}/submit/`);

      toast.success(
        t("acc.gst_position.success_message", {
          defaultValue: "GST payment recorded and submitted successfully.",
        })
      );

      setModalOpen(false);
      queryClient.invalidateQueries({ queryKey: ["gst-position"] });
      refetchPosition();
    } catch (err: unknown) {
      toast.error(
        summariseError(normaliseError(err)) ||
          t("acc.gst_position.error_save", {
            defaultValue: "Could not record GST payment",
          })
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Layout>
      <div className="space-y-6 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <div className="p-2.5 bg-blue-500/10 rounded-xl text-blue-400 border border-blue-500/20">
                <FileCheck className="w-6 h-6" />
              </div>
              <div>
                <h1 className="text-xl sm:text-2xl font-bold text-slate-100 tracking-tight">
                  {t("acc.gst_position.title", { defaultValue: "GST Position" })}
                </h1>
                <p className="text-xs sm:text-sm text-slate-400">
                  {t("acc.gst_position.subtitle", {
                    defaultValue: "Monthly GST liability, eligible ITC, and payment tracking",
                  })}
                </p>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3 flex-wrap">
            {/* Month Selector */}
            <div className="flex items-center gap-2 bg-slate-900 border border-slate-700/80 rounded-lg px-3 py-1.5 shadow-sm">
              <Calendar className="w-4 h-4 text-slate-400" />
              <input
                type="month"
                value={selectedMonth}
                onChange={(e) => setSelectedMonth(e.target.value)}
                className="bg-transparent text-sm text-slate-200 focus:outline-none cursor-pointer"
              />
            </div>

            <button
              onClick={() => {
                refetchPosition();
                refetchRisk();
              }}
              className="p-2 text-slate-400 hover:text-white bg-slate-800/80 hover:bg-slate-700/80 rounded-lg border border-slate-700/60 transition-colors"
              title="Refresh"
            >
              <RefreshCw
                className={`w-4 h-4 ${positionFetching ? "animate-spin" : ""}`}
              />
            </button>

            {/* Record GST Payment Action */}
            <button
              onClick={handleOpenModal}
              className="inline-flex items-center gap-2 px-3.5 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-medium shadow-sm transition-colors"
            >
              <Plus className="w-4 h-4" />
              <span>
                {t("acc.gst_position.record_payment", {
                  defaultValue: "Record GST Payment",
                })}
              </span>
            </button>
          </div>
        </div>

        {/* Informational portal disclaimer */}
        <div className="flex items-start gap-3 p-3.5 bg-blue-950/30 border border-blue-800/40 rounded-xl text-xs text-blue-300">
          <AlertCircle className="w-4 h-4 text-blue-400 shrink-0 mt-0.5" />
          <span>
            {t("acc.gst_position.portal_note", {
              defaultValue:
                "Note: These figures are computed from ERP invoices for planning cash. Final return figures and head-wise offset rules must be verified with the GST portal.",
            })}
          </span>
        </div>

        {/* Metric Cards Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* 1. Output Tax */}
          <div className="p-4 bg-slate-900/90 border border-slate-800 rounded-xl shadow-sm space-y-2">
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span>{t("acc.gst_position.output_tax", { defaultValue: "Output Tax" })}</span>
              <CreditCard className="w-4 h-4 text-rose-400" />
            </div>
            <div className="text-2xl font-bold text-slate-100">
              {positionLoading ? (
                <div className="h-8 w-24 bg-slate-800 animate-pulse rounded" />
              ) : (
                formatIndianCurrency(positionData?.output_tax || "0")
              )}
            </div>
            {positionData && (
              <div className="pt-2 border-t border-slate-800/60 flex items-center justify-between text-[11px] text-slate-400">
                <span>IGST: ₹{positionData.output_igst}</span>
                <span>CGST: ₹{positionData.output_cgst}</span>
                <span>SGST: ₹{positionData.output_sgst}</span>
              </div>
            )}
          </div>

          {/* 2. Input Tax Credit */}
          <div className="p-4 bg-slate-900/90 border border-slate-800 rounded-xl shadow-sm space-y-2">
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span>
                {t("acc.gst_position.input_credit", { defaultValue: "Input Credit (ITC)" })}
              </span>
              <FileSpreadsheet className="w-4 h-4 text-cyan-400" />
            </div>
            <div className="text-2xl font-bold text-slate-100">
              {positionLoading ? (
                <div className="h-8 w-24 bg-slate-800 animate-pulse rounded" />
              ) : (
                formatIndianCurrency(positionData?.input_credit || "0")
              )}
            </div>
            {positionData && (
              <div className="pt-2 border-t border-slate-800/60 flex items-center justify-between text-[11px] text-slate-400">
                <span>IGST: ₹{positionData.input_igst}</span>
                <span>CGST: ₹{positionData.input_cgst}</span>
                <span>SGST: ₹{positionData.input_sgst}</span>
              </div>
            )}
          </div>

          {/* 3. Net Payable & Status */}
          <div className="p-4 bg-slate-900/90 border border-slate-800 rounded-xl shadow-sm space-y-2">
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span>{t("acc.gst_position.net_payable", { defaultValue: "Net Payable" })}</span>
              {positionData?.status === "Paid" ? (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  <CheckCircle2 className="w-3 h-3" />
                  {t("acc.gst_position.paid_status", { defaultValue: "Paid" })}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20">
                  <Clock className="w-3 h-3" />
                  {t("acc.gst_position.due", { defaultValue: "Due" })}
                </span>
              )}
            </div>
            <div className="text-2xl font-bold text-slate-100">
              {positionLoading ? (
                <div className="h-8 w-24 bg-slate-800 animate-pulse rounded" />
              ) : (
                formatIndianCurrency(positionData?.net_payable || "0")
              )}
            </div>
            {positionData && (
              <div className="pt-2 border-t border-slate-800/60 flex items-center justify-between text-[11px] text-slate-400">
                <span>{t("acc.gst_position.due_date", { defaultValue: "Due Date" })}:</span>
                <span className="font-medium text-slate-300">
                  {formatDate(positionData.due_date)}
                </span>
              </div>
            )}
          </div>

          {/* 4. Paid */}
          <div className="p-4 bg-slate-900/90 border border-slate-800 rounded-xl shadow-sm space-y-2">
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span>{t("acc.gst_position.paid", { defaultValue: "Paid" })}</span>
              <Wallet className="w-4 h-4 text-emerald-400" />
            </div>
            <div className="text-2xl font-bold text-emerald-400">
              {positionLoading ? (
                <div className="h-8 w-24 bg-slate-800 animate-pulse rounded" />
              ) : (
                formatIndianCurrency(positionData?.paid || "0")
              )}
            </div>
            {positionData && (
              <div className="pt-2 border-t border-slate-800/60 flex items-center justify-between text-[11px] text-slate-400">
                <span>Cash: ₹{positionData.paid_cash}</span>
                <span>Credit: ₹{positionData.paid_credit}</span>
              </div>
            )}
          </div>
        </div>

        {/* Tabs for Navigation */}
        <div className="border-b border-slate-800">
          <nav className="flex space-x-6">
            <button
              onClick={() => setActiveTab("overview")}
              className={`pb-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === "overview"
                  ? "border-blue-500 text-blue-400"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              {t("acc.gst_position.payments_history", {
                defaultValue: "Payments History",
              })}
              {positionData?.payments && positionData.payments.length > 0 && (
                <span className="ms-2 px-1.5 py-0.5 text-xs bg-slate-800 text-slate-300 rounded-full">
                  {positionData.payments.length}
                </span>
              )}
            </button>

            <button
              onClick={() => setActiveTab("risk")}
              className={`pb-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
                activeTab === "risk"
                  ? "border-amber-500 text-amber-400"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              <ShieldAlert className="w-4 h-4 text-amber-400" />
              <span>
                {t("acc.gst_position.credit_at_risk_title", {
                  defaultValue: "180-Day Watch (Credit at Risk)",
                })}
              </span>
              {riskBills.length > 0 && (
                <span className="px-1.5 py-0.5 text-xs bg-rose-500/20 text-rose-400 border border-rose-500/30 rounded-full">
                  {riskBills.length}
                </span>
              )}
            </button>
          </nav>
        </div>

        {/* Tab 1: Payments Table */}
        {activeTab === "overview" && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
            <div className="p-4 border-b border-slate-800 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-200">
                {t("acc.gst_position.payments_history", {
                  defaultValue: "Challan Payments for This Month",
                })}
              </h2>
            </div>

            {positionLoading ? (
              <div className="p-12 text-center text-slate-400">
                <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2 text-blue-400" />
                <span>Loading payments...</span>
              </div>
            ) : !positionData?.payments || positionData.payments.length === 0 ? (
              <div className="p-12 text-center text-slate-400 space-y-3">
                <FileText className="w-8 h-8 text-slate-600 mx-auto" />
                <p className="text-sm">
                  {t("acc.gst_position.no_payments", {
                    defaultValue: "No GST payments recorded for this month.",
                  })}
                </p>
                <button
                  onClick={handleOpenModal}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg border border-slate-700 transition-colors"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>
                    {t("acc.gst_position.record_payment", {
                      defaultValue: "Record GST Payment",
                    })}
                  </span>
                </button>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs text-slate-300">
                  <thead className="bg-slate-800/60 text-slate-400 uppercase text-[10px] tracking-wider">
                    <tr>
                      <th className="px-4 py-3">Doc No</th>
                      <th className="px-4 py-3">Challan No</th>
                      <th className="px-4 py-3">Posting Date</th>
                      <th className="px-4 py-3 text-right">Cash Paid</th>
                      <th className="px-4 py-3 text-right">Credit Used</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800">
                    {positionData.payments.map((p) => (
                      <tr key={p.id} className="hover:bg-slate-800/30 transition-colors">
                        <td className="px-4 py-3 font-medium text-slate-200">{p.name}</td>
                        <td className="px-4 py-3 font-mono text-slate-300">{p.challan_no}</td>
                        <td className="px-4 py-3">{formatDate(p.posting_date)}</td>
                        <td className="px-4 py-3 text-right font-medium text-emerald-400">
                          {formatIndianCurrency(p.cash)}
                        </td>
                        <td className="px-4 py-3 text-right font-medium text-cyan-400">
                          {formatIndianCurrency(p.credit_used)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Tab 2: Credit at Risk (180-Day Watch) */}
        {activeTab === "risk" && (
          <div className="space-y-4">
            <div className="flex items-start gap-3 p-3.5 bg-amber-950/20 border border-amber-800/40 rounded-xl text-xs text-amber-300">
              <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
              <span>
                {t("acc.gst_position.credit_at_risk_info", {
                  defaultValue:
                    "Under Section 16(2) of the CGST Act, input credit on purchase bills unpaid beyond 180 days from invoice date must be reversed along with interest until paid.",
                })}
              </span>
            </div>

            <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
              <div className="p-4 border-b border-slate-800 flex items-center justify-between">
                <h2 className="text-sm font-semibold text-slate-200">
                  {t("acc.gst_position.credit_at_risk_title", {
                    defaultValue: "Unpaid Purchase Bills Nearing or Past 180 Days",
                  })}
                </h2>
              </div>

              {riskLoading ? (
                <div className="p-12 text-center text-slate-400">
                  <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2 text-amber-400" />
                  <span>Checking invoices...</span>
                </div>
              ) : riskBills.length === 0 ? (
                <div className="p-12 text-center text-slate-400 space-y-2">
                  <CheckCircle2 className="w-8 h-8 text-emerald-400 mx-auto" />
                  <p className="text-sm font-medium text-slate-300">
                    {t("acc.gst_position.no_risk_bills", {
                      defaultValue: "No purchase bills nearing 180 days unpaid.",
                    })}
                  </p>
                  <p className="text-xs text-slate-500">
                    All supplier bills with GST are either paid or within safe payment periods.
                  </p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs text-slate-300">
                    <thead className="bg-slate-800/60 text-slate-400 uppercase text-[10px] tracking-wider">
                      <tr>
                        <th className="px-4 py-3">Invoice</th>
                        <th className="px-4 py-3">Supplier</th>
                        <th className="px-4 py-3">Bill Date / Posting</th>
                        <th className="px-4 py-3 text-right">Days Unpaid</th>
                        <th className="px-4 py-3 text-right">GST at Risk</th>
                        <th className="px-4 py-3 text-right">Outstanding</th>
                        <th className="px-4 py-3">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800">
                      {riskBills.map((b) => (
                        <tr key={b.id} className="hover:bg-slate-800/30 transition-colors">
                          <td className="px-4 py-3 font-medium text-slate-200">{b.name}</td>
                          <td className="px-4 py-3">{b.supplier_name}</td>
                          <td className="px-4 py-3">
                            <div>{formatDate(b.bill_date || b.posting_date)}</div>
                            {b.bill_date && (
                              <div className="text-[10px] text-slate-500">
                                Posted: {formatDate(b.posting_date)}
                              </div>
                            )}
                          </td>
                          <td className="px-4 py-3 text-right font-medium text-slate-200">
                            {b.days_unpaid} days
                          </td>
                          <td className="px-4 py-3 text-right font-semibold text-rose-400">
                            {formatIndianCurrency(b.gst_amount)}
                          </td>
                          <td className="px-4 py-3 text-right">
                            {formatIndianCurrency(b.outstanding_amount)}
                          </td>
                          <td className="px-4 py-3">
                            {b.status === "Reversed" ? (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">
                                <AlertTriangle className="w-3 h-3" />
                                {t("acc.gst_position.reversed", { defaultValue: "Reversed" })}
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20">
                                <Clock className="w-3 h-3" />
                                {t("acc.gst_position.approaching_180", {
                                  defaultValue: `${b.days_left}d left`,
                                })}
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Modal: Record GST Payment */}
        <Modal
          open={modalOpen}
          onClose={() => !submitting && setModalOpen(false)}
          title={t("acc.gst_position.modal_title", {
            defaultValue: "Record GST Payment",
          })}
        >
          <form onSubmit={handleRecordPayment} className="space-y-4 text-xs text-slate-200">
            {/* Period and Posting Date */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-slate-400 mb-1 font-medium">
                  {t("acc.gst_position.period", { defaultValue: "Tax Period" })}
                </label>
                <input
                  type="text"
                  disabled
                  value={selectedMonth}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-700/80 rounded-lg text-slate-400 cursor-not-allowed"
                />
              </div>

              <div>
                <label className="block text-slate-400 mb-1 font-medium">
                  {t("acc.gst_position.posting_date", { defaultValue: "Payment Date" })}{" "}
                  <span className="text-rose-400">*</span>
                </label>
                <input
                  type="date"
                  required
                  value={postingDate}
                  onChange={(e) => setPostingDate(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-200 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>

            {/* Challan No */}
            <div>
              <label className="block text-slate-400 mb-1 font-medium">
                {t("acc.gst_position.challan_no", { defaultValue: "Challan No (CPIN / CIN)" })}{" "}
                <span className="text-rose-400">*</span>
              </label>
              <input
                type="text"
                required
                placeholder="e.g. 26102000000123"
                value={challanNo}
                onChange={(e) => setChallanNo(e.target.value)}
                className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500 font-mono"
              />
            </div>

            {/* Paid From Bank Account */}
            <div>
              <label className="block text-slate-400 mb-1 font-medium">
                {t("acc.gst_position.paid_from", { defaultValue: "Paid From (Bank Account)" })}
              </label>
              <select
                value={paidFromId}
                onChange={(e) => setPaidFromId(e.target.value ? Number(e.target.value) : "")}
                className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-200 focus:outline-none focus:border-blue-500"
              >
                <option value="">Select bank account...</option>
                {bankAccounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} ({a.account_type})
                  </option>
                ))}
              </select>
            </div>

            {/* Cash Paid section */}
            <div className="p-3 bg-slate-950/60 border border-slate-800 rounded-lg space-y-2">
              <span className="block text-[11px] font-semibold text-slate-300 uppercase tracking-wider">
                {t("acc.gst_position.cash_amounts", { defaultValue: "Cash Paid via Bank" })}
              </span>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="block text-[11px] text-slate-400 mb-0.5">IGST Cash (₹)</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    placeholder="0.00"
                    value={igstCash}
                    onChange={(e) => setIgstCash(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-800 border border-slate-700 rounded text-slate-200 focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-[11px] text-slate-400 mb-0.5">CGST Cash (₹)</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    placeholder="0.00"
                    value={cgstCash}
                    onChange={(e) => setCgstCash(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-800 border border-slate-700 rounded text-slate-200 focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-[11px] text-slate-400 mb-0.5">SGST Cash (₹)</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    placeholder="0.00"
                    value={sgstCash}
                    onChange={(e) => setSgstCash(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-800 border border-slate-700 rounded text-slate-200 focus:outline-none focus:border-blue-500"
                  />
                </div>
              </div>
            </div>

            {/* Credit Used section */}
            <div className="p-3 bg-slate-950/60 border border-slate-800 rounded-lg space-y-2">
              <span className="block text-[11px] font-semibold text-slate-300 uppercase tracking-wider">
                {t("acc.gst_position.credit_amounts", {
                  defaultValue: "Input Credit Offset (ITC Used)",
                })}
              </span>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="block text-[11px] text-slate-400 mb-0.5">IGST Credit (₹)</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    placeholder="0.00"
                    value={igstCredit}
                    onChange={(e) => setIgstCredit(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-800 border border-slate-700 rounded text-slate-200 focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-[11px] text-slate-400 mb-0.5">CGST Credit (₹)</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    placeholder="0.00"
                    value={cgstCredit}
                    onChange={(e) => setCgstCredit(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-800 border border-slate-700 rounded text-slate-200 focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-[11px] text-slate-400 mb-0.5">SGST Credit (₹)</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    placeholder="0.00"
                    value={sgstCredit}
                    onChange={(e) => setSgstCredit(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-800 border border-slate-700 rounded text-slate-200 focus:outline-none focus:border-blue-500"
                  />
                </div>
              </div>
            </div>

            {/* Proof Attachment */}
            <div>
              <label className="block text-slate-400 mb-1 font-medium">
                {t("acc.gst_position.proof_attachment", {
                  defaultValue: "Challan / Receipt Proof",
                })}
              </label>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-2 px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg border border-slate-700 cursor-pointer transition-colors text-xs">
                  <Upload className="w-3.5 h-3.5 text-slate-400" />
                  <span>Choose file</span>
                  <input
                    type="file"
                    accept=".pdf,image/*"
                    onChange={(e) => setProofFile(e.target.files?.[0] || null)}
                    className="hidden"
                  />
                </label>
                {proofFile ? (
                  <span className="text-xs text-emerald-400 truncate max-w-xs font-mono">
                    {proofFile.name}
                  </span>
                ) : (
                  <span className="text-xs text-slate-500">PDF or receipt image</span>
                )}
              </div>
            </div>

            {/* Action buttons */}
            <div className="pt-3 border-t border-slate-800 flex items-center justify-end gap-3">
              <button
                type="button"
                disabled={submitting}
                onClick={() => setModalOpen(false)}
                className="px-4 py-2 text-xs font-medium text-slate-400 hover:text-white transition-colors"
              >
                {t("acc.gst_position.cancel", { defaultValue: "Cancel" })}
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="inline-flex items-center gap-1.5 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white rounded-lg text-xs font-medium shadow-sm transition-colors"
              >
                {submitting ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    <span>{t("acc.gst_position.submitting", { defaultValue: "Recording..." })}</span>
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span>{t("acc.gst_position.submit", { defaultValue: "Submit Payment" })}</span>
                  </>
                )}
              </button>
            </div>
          </form>
        </Modal>
      </div>
    </Layout>
  );
};
