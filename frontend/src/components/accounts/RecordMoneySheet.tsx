import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  ArrowDownLeft,
  ArrowUpRight,
  CheckCircle2,
  FileText,
  Loader2,
  Upload,
  X,
} from "lucide-react";
import { apiClient } from "@/api/client";
import { normaliseError, summariseError } from "@/api/endpoints";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { formatIndianCurrency, todayISO } from "@/lib/format";

export interface SuggestedAllocationRow {
  reference_doctype: string;
  reference_name: string;
  total_amount: string;
  outstanding_amount: string;
  allocated_amount: string;
  due_date: string | null;
  posting_date: string;
}

export interface RecordMoneySheetProps {
  open: boolean;
  onClose: () => void;
  defaultMode?: "Receive" | "Pay";
  defaultPartyType?: "Customer" | "Supplier";
  defaultParty?: string;
  defaultAmount?: string | number;
  onSuccess?: () => void;
}

interface AccountOption {
  id: number;
  name: string;
  account_type: string;
}

const STORAGE_KEY = "last_used_bank_cash_account";

function getStoredAccount(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

function setStoredAccount(val: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, val);
  } catch {
    // ignore
  }
}

export const RecordMoneySheet: React.FC<RecordMoneySheetProps> = ({
  open,
  onClose,
  defaultMode = "Receive",
  defaultPartyType = "Customer",
  defaultParty = "",
  defaultAmount = "",
  onSuccess,
}) => {
  const { t } = useTranslation();
  const toast = useToast();

  const [mode, setMode] = useState<"Receive" | "Pay">(defaultMode);
  const [partyType, setPartyType] = useState<"Customer" | "Supplier">(defaultPartyType);
  const [party, setParty] = useState(defaultParty);
  const [amount, setAmount] = useState(defaultAmount ? String(defaultAmount) : "");
  const [postingDate, setPostingDate] = useState(todayISO());
  const [referenceNo, setReferenceNo] = useState("");
  const [paidAccount, setPaidAccount] = useState(getStoredAccount());
  const [proofFile, setProofFile] = useState<File | null>(null);

  const [accounts, setAccounts] = useState<AccountOption[]>([]);
  const [isLoadingAccounts, setIsLoadingAccounts] = useState(false);

  const [allocations, setAllocations] = useState<SuggestedAllocationRow[]>([]);
  const [isLoadingAllocations, setIsLoadingAllocations] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Sync props when opened or defaulted
  useEffect(() => {
    if (open) {
      setMode(defaultMode);
      setPartyType(defaultPartyType);
      setParty(defaultParty);
      setAmount(defaultAmount ? String(defaultAmount) : "");
      setPostingDate(todayISO());
      setReferenceNo("");
      setProofFile(null);
      setPaidAccount(getStoredAccount());
    }
  }, [open, defaultMode, defaultPartyType, defaultParty, defaultAmount]);

  // Load Bank & Cash accounts
  useEffect(() => {
    if (!open) return;
    setIsLoadingAccounts(true);
    apiClient
      .get("/accounts/accounts/", {
        params: { is_group: false, is_active: true, page_size: 100 },
      })
      .then((res) => {
        const results = Array.isArray(res.data) ? res.data : res.data.results || [];
        const bankCash = results.filter(
          (a: any) => a.account_type === "Bank" || a.account_type === "Cash"
        );
        setAccounts(bankCash);
        const stored = getStoredAccount();
        if (stored && bankCash.some((a: any) => String(a.id) === stored)) {
          setPaidAccount(stored);
        } else if (bankCash.length > 0 && !paidAccount) {
          const firstId = String(bankCash[0].id);
          setPaidAccount(firstId);
          setStoredAccount(firstId);
        }
      })
      .catch((err) => {
        toast.error(summariseError(normaliseError(err)) || t("acc.record_money.error_load", { defaultValue: "Error" }));
      })
      .finally(() => setIsLoadingAccounts(false));
  }, [open, paidAccount, t, toast]);

  // Fetch suggested allocation when party or amount changes
  useEffect(() => {
    if (!open || !party || !amount || Number(amount) <= 0) {
      setAllocations([]);
      return;
    }

    const timer = setTimeout(() => {
      setIsLoadingAllocations(true);
      apiClient
        .get("/accounts/payment-entries/suggest-allocation/", {
          params: {
            party_type: partyType,
            party,
            amount,
          },
        })
        .then((res) => {
          setAllocations(res.data.references || []);
        })
        .catch(() => {
          // non-fatal, fallback to empty references
          setAllocations([]);
        })
        .finally(() => setIsLoadingAllocations(false));
    }, 300);

    return () => clearTimeout(timer);
  }, [open, party, partyType, amount]);

  // Handle account change and remember in localStorage
  const handleAccountChange = (val: string) => {
    setPaidAccount(val);
    setStoredAccount(val);
  };

  // Handle line allocation editing
  const handleAllocChange = (index: number, val: string) => {
    setAllocations((prev) => {
      const copy = [...prev];
      copy[index] = { ...copy[index], allocated_amount: val };
      return copy;
    });
  };

  // Computations
  const totalAllocated = allocations.reduce(
    (sum, a) => sum + (Number(a.allocated_amount) || 0),
    0
  );
  const enteredAmount = Number(amount) || 0;
  const unallocatedAmount = Math.max(0, enteredAmount - totalAllocated);
  const isOverAllocated = totalAllocated > enteredAmount;

  // Submit Handler
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!party) {
      toast.error(t("acc.record_money.missing_party", { defaultValue: "Party is required" }));
      return;
    }
    if (enteredAmount <= 0) {
      toast.error(t("acc.record_money.invalid_amount", { defaultValue: "Amount must be greater than zero" }));
      return;
    }
    if (!paidAccount) {
      toast.error(t("acc.record_money.select_account", { defaultValue: "Select Bank or Cash account" }));
      return;
    }
    if (isOverAllocated) {
      toast.error(
        t("acc.record_money.error_exceeds_paid", {
          defaultValue: "Total allocated cannot exceed paid amount",
        })
      );
      return;
    }

    setIsSubmitting(true);
    try {
      const selectedAccountNum = Number(paidAccount);
      const payload: Record<string, any> = {
        payment_type: mode,
        party_type: partyType,
        party_name: party,
        paid_amount: enteredAmount.toFixed(2),
        received_amount: enteredAmount.toFixed(2),
        posting_date: postingDate,
        reference_no: referenceNo || null,
        paid_from: mode === "Receive" ? null : selectedAccountNum,
        paid_to: mode === "Receive" ? selectedAccountNum : null,
        references: allocations
          .filter((a) => Number(a.allocated_amount) > 0)
          .map((a) => ({
            reference_doctype: a.reference_doctype,
            reference_name: a.reference_name,
            allocated_amount: Number(a.allocated_amount).toFixed(2),
          })),
      };

      // 1. Create Draft Payment Entry
      const peRes = await apiClient.post("/accounts/payment-entries/", payload);
      const pe = peRes.data;

      // 2. Upload proof attachment if selected
      if (proofFile && pe.id) {
        const formData = new FormData();
        formData.append("file", proofFile);
        formData.append("model", "accounts.PaymentEntry");
        formData.append("object_id", String(pe.id));
        formData.append("description", `Proof for ${pe.name || "Payment Entry"}`);
        await apiClient.post("/core/attachments/", formData, {
          headers: { "Content-Type": "multipart/form-data" },
        });
      }

      // 3. Submit Payment Entry
      await apiClient.post(`/accounts/payment-entries/${pe.id}/submit/`);

      toast.success(
        t("acc.record_money.success_recorded", {
          defaultValue: "Payment recorded and submitted successfully",
        })
      );

      onSuccess?.();
      onClose();
    } catch (err: any) {
      toast.error(
        summariseError(normaliseError(err)) ||
          t("acc.record_money.error_save", { defaultValue: "Could not record payment" })
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={
        mode === "Receive"
          ? t("acc.record_money.title_receipt", { defaultValue: "Record Money In (Receipt)" })
          : t("acc.record_money.title_payment", { defaultValue: "Record Money Out (Payment)" })
      }
      className="max-w-2xl max-h-[92vh] overflow-y-auto"
    >
      <form onSubmit={handleSubmit} className="space-y-4 pt-2">
        {/* Mode Toggle */}
        <div className="flex rounded-lg bg-slate-800 p-1 border border-slate-700">
          <button
            type="button"
            onClick={() => {
              setMode("Receive");
              setPartyType("Customer");
            }}
            className={`flex-1 flex items-center justify-center gap-2 py-2 text-sm font-medium rounded-md transition-colors ${
              mode === "Receive"
                ? "bg-emerald-600 text-white shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <ArrowDownLeft className="w-4 h-4" />
            {t("acc.record_money.title_receipt", { defaultValue: "Receipt (Money In)" })}
          </button>
          <button
            type="button"
            onClick={() => {
              setMode("Pay");
              setPartyType("Supplier");
            }}
            className={`flex-1 flex items-center justify-center gap-2 py-2 text-sm font-medium rounded-md transition-colors ${
              mode === "Pay"
                ? "bg-rose-600 text-white shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <ArrowUpRight className="w-4 h-4" />
            {t("acc.record_money.title_payment", { defaultValue: "Payment (Money Out)" })}
          </button>
        </div>

        {/* Party and Amount Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">
              {partyType === "Customer"
                ? t("acc.record_money.customer", { defaultValue: "Customer" })
                : t("acc.record_money.supplier", { defaultValue: "Supplier" })}
            </label>
            <input
              type="text"
              value={party}
              onChange={(e) => setParty(e.target.value)}
              placeholder={t("acc.record_money.party", { defaultValue: "Party Name" })}
              required
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">
              {t("acc.record_money.amount", { defaultValue: "Amount (₹)" })}
            </label>
            <input
              type="number"
              step="0.01"
              min="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="0.00"
              required
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
            />
          </div>
        </div>

        {/* Account and Date Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">
              {t("acc.record_money.bank_cash_account", {
                defaultValue: "Bank / Cash Account",
              })}
            </label>
            <select
              value={paidAccount}
              onChange={(e) => handleAccountChange(e.target.value)}
              required
              disabled={isLoadingAccounts}
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              <option value="">
                {t("acc.record_money.select_account", { defaultValue: "Select Account..." })}
              </option>
              {accounts.map((acc) => (
                <option key={acc.id} value={acc.id}>
                  {acc.name} ({acc.account_type})
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">
              {t("acc.record_money.posting_date", { defaultValue: "Date" })}
            </label>
            <input
              type="date"
              value={postingDate}
              onChange={(e) => setPostingDate(e.target.value)}
              required
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>
        </div>

        {/* Reference and Proof File Upload */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">
              {t("acc.record_money.reference_no", {
                defaultValue: "Reference # / Cheque # / UTR",
              })}
            </label>
            <input
              type="text"
              value={referenceNo}
              onChange={(e) => setReferenceNo(e.target.value)}
              placeholder="UTR-XXXXX or Cheque #"
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">
              {t("acc.record_money.proof_file", { defaultValue: "Proof Attachment" })}
            </label>
            <div className="relative flex items-center">
              <input
                type="file"
                id="proof-upload-input"
                className="hidden"
                accept="image/*,application/pdf"
                onChange={(e) => {
                  if (e.target.files && e.target.files[0]) {
                    setProofFile(e.target.files[0]);
                  }
                }}
              />
              <label
                htmlFor="proof-upload-input"
                className="flex-1 flex items-center justify-between gap-2 rounded-lg border border-dashed border-slate-700 bg-slate-800/80 px-3 py-2 text-sm text-slate-300 cursor-pointer hover:border-slate-500 transition-colors"
              >
                <div className="flex items-center gap-2 truncate">
                  <Upload className="w-4 h-4 text-slate-400 flex-shrink-0" />
                  <span className="truncate">
                    {proofFile ? proofFile.name : t("acc.record_money.proof_hint", { defaultValue: "Choose file..." })}
                  </span>
                </div>
                {proofFile && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.preventDefault();
                      setProofFile(null);
                    }}
                    className="p-1 hover:text-rose-400"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </label>
            </div>
          </div>
        </div>

        {/* Suggested Allocations Table */}
        <div className="mt-4 border-t border-slate-800 pt-3">
          <div className="flex items-center justify-between mb-2">
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300">
                {t("acc.record_money.allocations_title", {
                  defaultValue: "Suggested Invoice Allocation (Oldest First)",
                })}
              </h3>
              <p className="text-[11px] text-slate-500">
                {t("acc.record_money.allocations_hint", {
                  defaultValue: "Allocations can be adjusted below. Remainder is kept as unallocated advance.",
                })}
              </p>
            </div>
            {isLoadingAllocations && (
              <Loader2 className="w-4 h-4 text-indigo-400 animate-spin" />
            )}
          </div>

          {allocations.length > 0 ? (
            <div className="rounded-lg border border-slate-800 overflow-hidden bg-slate-900/50">
              <div className="max-h-48 overflow-y-auto">
                <table className="w-full text-xs text-start">
                  <thead className="bg-slate-800/90 text-slate-400 uppercase text-[10px] tracking-wider sticky top-0">
                    <tr>
                      <th className="py-2 px-3 text-start">{t("acc.record_money.invoice", { defaultValue: "Invoice" })}</th>
                      <th className="py-2 px-3 text-start">{t("acc.record_money.due_date", { defaultValue: "Due Date" })}</th>
                      <th className="py-2 px-3 text-end">{t("acc.record_money.outstanding", { defaultValue: "Outstanding" })}</th>
                      <th className="py-2 px-3 text-end">{t("acc.record_money.allocated", { defaultValue: "Allocated (₹)" })}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800">
                    {allocations.map((alloc, idx) => (
                      <tr key={alloc.reference_name} className="hover:bg-slate-800/40">
                        <td className="py-2 px-3 font-medium text-slate-200">
                          {alloc.reference_name}
                        </td>
                        <td className="py-2 px-3 text-slate-400 font-mono">
                          {alloc.due_date || alloc.posting_date}
                        </td>
                        <td className="py-2 px-3 text-end text-slate-300 font-mono">
                          {formatIndianCurrency(Number(alloc.outstanding_amount))}
                        </td>
                        <td className="py-2 px-3 text-end">
                          <input
                            type="number"
                            step="0.01"
                            min="0"
                            max={alloc.outstanding_amount}
                            value={alloc.allocated_amount}
                            onChange={(e) => handleAllocChange(idx, e.target.value)}
                            className="w-24 rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-end text-slate-100 font-mono focus:border-indigo-500 focus:outline-none"
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Allocation Summary Bar */}
              <div className="bg-slate-800/60 p-2.5 px-3 flex flex-wrap items-center justify-between text-xs border-t border-slate-800 gap-2">
                <div className="flex items-center gap-4">
                  <span className="text-slate-400">
                    {t("acc.record_money.total_allocated", { defaultValue: "Total Allocated" })}:{" "}
                    <strong className="text-slate-200 font-mono">
                      {formatIndianCurrency(totalAllocated)}
                    </strong>
                  </span>
                  <span className="text-slate-400">
                    {t("acc.record_money.unallocated_advance", { defaultValue: "Advance Remainder" })}:{" "}
                    <strong className="text-emerald-400 font-mono">
                      {formatIndianCurrency(unallocatedAmount)}
                    </strong>
                  </span>
                </div>
                {isOverAllocated && (
                  <span className="text-rose-400 flex items-center gap-1 font-semibold text-[11px]">
                    <AlertCircle className="w-3.5 h-3.5" />
                    {t("acc.record_money.error_exceeds_paid", { defaultValue: "Over-allocated!" })}
                  </span>
                )}
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-3 text-center text-xs text-slate-400">
              {party && enteredAmount > 0
                ? t("acc.record_money.no_open_bills", {
                    defaultValue: "No open bills found for this party. Total amount will be recorded as advance on account.",
                  })
                : t("acc.record_money.enter_party_amount", {
                    defaultValue: "Enter party and amount to view open invoice suggestions.",
                  })}
            </div>
          )}
        </div>

        {/* Buttons */}
        <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-800">
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-medium text-slate-300 hover:bg-slate-800 hover:text-white transition-colors"
          >
            {t("acc.record_money.cancel", { defaultValue: "Cancel" })}
          </button>
          <button
            type="submit"
            disabled={isSubmitting || isOverAllocated}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50 transition-colors"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {t("acc.record_money.submitting", { defaultValue: "Submitting..." })}
              </>
            ) : (
              <>
                <CheckCircle2 className="w-4 h-4" />
                {t("acc.record_money.submit", { defaultValue: "Save & Submit Payment" })}
              </>
            )}
          </button>
        </div>
      </form>
    </Modal>
  );
};
