import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  ArrowDownLeft,
  ArrowLeftRight,
  ArrowUpRight,
  Banknote,
  CheckCircle2,
  ChevronDown,
  FileText,
  Landmark,
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

/** Receipt / payment, or cash moved from the cash box into the bank. */
type Direction = "Receive" | "Pay" | "Deposit";
/** How the money moved (PaymentEntry.mode). */
type MoveMode = "Cash" | "Bank Transfer" | "Cheque";

const MOVE_MODES: MoveMode[] = ["Cash", "Bank Transfer", "Cheque"];
const DEFAULT_MOVE_MODE: MoveMode = "Bank Transfer";

const STORAGE_KEY = "last_used_bank_cash_account";
const MODE_STORAGE_KEY = "last_used_payment_mode";

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

function getStoredMode(): MoveMode {
  try {
    const stored = localStorage.getItem(MODE_STORAGE_KEY);
    if (stored && (MOVE_MODES as string[]).includes(stored)) {
      return stored as MoveMode;
    }
  } catch {
    // ignore
  }
  return DEFAULT_MOVE_MODE;
}

function setStoredMode(val: MoveMode): void {
  try {
    localStorage.setItem(MODE_STORAGE_KEY, val);
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

  const [direction, setDirection] = useState<Direction>(defaultMode);
  const [partyType, setPartyType] = useState<"Customer" | "Supplier">(defaultPartyType);
  const [party, setParty] = useState(defaultParty);
  const [amount, setAmount] = useState(defaultAmount ? String(defaultAmount) : "");
  const [postingDate, setPostingDate] = useState(todayISO());
  const [moveMode, setMoveMode] = useState<MoveMode>(getStoredMode());
  const [referenceNo, setReferenceNo] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const [paidAccount, setPaidAccount] = useState(getStoredAccount());
  const [proofFile, setProofFile] = useState<File | null>(null);

  const [accounts, setAccounts] = useState<AccountOption[]>([]);
  const [isLoadingAccounts, setIsLoadingAccounts] = useState(false);

  const [allocations, setAllocations] = useState<SuggestedAllocationRow[]>([]);
  const [isLoadingAllocations, setIsLoadingAllocations] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const isDeposit = direction === "Deposit";

  // Sync props when opened or defaulted
  useEffect(() => {
    if (open) {
      setDirection(defaultMode);
      setPartyType(defaultPartyType);
      setParty(defaultParty);
      setAmount(defaultAmount ? String(defaultAmount) : "");
      setPostingDate(todayISO());
      setMoveMode(getStoredMode());
      setReferenceNo("");
      setShowDetails(false);
      setProofFile(null);
      setPaidAccount(getStoredAccount());
    }
  }, [open, defaultMode, defaultPartyType, defaultParty, defaultAmount]);

  // Load Bank & Cash accounts for the "More details" picker
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
      })
      .catch((err) => {
        toast.error(summariseError(normaliseError(err)) || t("acc.record_money.error_load", { defaultValue: "Error" }));
      })
      .finally(() => setIsLoadingAccounts(false));
  }, [open, t, toast]);

  // Fetch suggested allocation when party or amount changes
  useEffect(() => {
    if (!open || isDeposit || !party || !amount || Number(amount) <= 0) {
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
  }, [open, isDeposit, party, partyType, amount]);

  // Remember the account picked under "More details"
  const handleAccountChange = (val: string) => {
    setPaidAccount(val);
    setStoredAccount(val);
  };

  // Remember the last way the money moved
  const handleMoveModeChange = (val: MoveMode) => {
    setMoveMode(val);
    setStoredMode(val);
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
  const isOverAllocated = !isDeposit && totalAllocated > enteredAmount;

  // Submit Handler
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isDeposit && !party) {
      toast.error(t("acc.record_money.missing_party", { defaultValue: "Party is required" }));
      return;
    }
    if (enteredAmount <= 0) {
      toast.error(t("acc.record_money.invalid_amount", { defaultValue: "Amount must be greater than zero" }));
      return;
    }
    if (!isDeposit && moveMode === "Cheque" && !referenceNo.trim()) {
      toast.error(t("acc.record_money.cheque_no_required", { defaultValue: "Enter the cheque number" }));
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

    // The picker applies only when "More details" is open and an account is chosen;
    // otherwise the server picks the account from how the money moved.
    const accountId = showDetails && paidAccount ? Number(paidAccount) : null;
    const common = {
      paid_amount: enteredAmount.toFixed(2),
      received_amount: enteredAmount.toFixed(2),
      posting_date: postingDate,
    };
    let payload: Record<string, any>;
    if (isDeposit) {
      payload = {
        ...common,
        payment_type: "Internal Transfer",
        mode: "Cash",
        paid_from: null,
        paid_to: null,
      };
    } else {
      payload = {
        ...common,
        payment_type: direction,
        party_type: partyType,
        party_name: party,
        mode: moveMode,
        reference_no: moveMode === "Cash" ? null : referenceNo.trim() || null,
        paid_from: direction === "Receive" ? null : accountId,
        paid_to: direction === "Receive" ? accountId : null,
        references: allocations
          .filter((a) => Number(a.allocated_amount) > 0)
          .map((a) => ({
            reference_doctype: a.reference_doctype,
            reference_name: a.reference_name,
            allocated_amount: Number(a.allocated_amount).toFixed(2),
          })),
      };
    }

    setIsSubmitting(true);
    try {
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

  const directionButtons: {
    value: Direction;
    label: string;
    icon: React.ReactNode;
    activeClass: string;
  }[] = [
    {
      value: "Receive",
      label: t("acc.record_money.title_receipt", { defaultValue: "Receipt (Money In)" }),
      icon: <ArrowDownLeft className="w-4 h-4" />,
      activeClass: "bg-emerald-600 text-white shadow-sm",
    },
    {
      value: "Pay",
      label: t("acc.record_money.title_payment", { defaultValue: "Payment (Money Out)" }),
      icon: <ArrowUpRight className="w-4 h-4" />,
      activeClass: "bg-rose-600 text-white shadow-sm",
    },
    {
      value: "Deposit",
      label: t("acc.record_money.deposit_title", { defaultValue: "Cash put in bank" }),
      icon: <Landmark className="w-4 h-4" />,
      activeClass: "bg-sky-600 text-white shadow-sm",
    },
  ];

  const moveModeButtons: { value: MoveMode; label: string; icon: React.ReactNode }[] = [
    {
      value: "Cash",
      label: t("acc.record_money.mode_cash", { defaultValue: "Cash" }),
      icon: <Banknote className="w-6 h-6" />,
    },
    {
      value: "Bank Transfer",
      label: t("acc.record_money.mode_bank", { defaultValue: "Bank transfer" }),
      icon: <ArrowLeftRight className="w-6 h-6" />,
    },
    {
      value: "Cheque",
      label: t("acc.record_money.mode_cheque", { defaultValue: "Cheque" }),
      icon: <FileText className="w-6 h-6" />,
    },
  ];

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={
        direction === "Receive"
          ? t("acc.record_money.title_receipt", { defaultValue: "Record Money In (Receipt)" })
          : direction === "Pay"
            ? t("acc.record_money.title_payment", { defaultValue: "Record Money Out (Payment)" })
            : t("acc.record_money.deposit_title", { defaultValue: "Cash put in bank" })
      }
      className="max-w-2xl max-h-[92vh] overflow-y-auto"
    >
      <form onSubmit={handleSubmit} className="space-y-4 pt-2">
        {/* Direction switch: receipt, payment or cash put in bank */}
        <div className="grid grid-cols-3 gap-1 rounded-lg bg-slate-800 p-1 border border-slate-700">
          {directionButtons.map((btn) => (
            <button
              key={btn.value}
              type="button"
              onClick={() => {
                setDirection(btn.value);
                if (btn.value === "Receive") setPartyType("Customer");
                if (btn.value === "Pay") setPartyType("Supplier");
              }}
              className={`flex items-center justify-center gap-1.5 py-2 px-1 text-xs sm:text-sm font-medium rounded-md transition-colors text-center ${
                direction === btn.value
                  ? btn.activeClass
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {btn.icon}
              <span>{btn.label}</span>
            </button>
          ))}
        </div>

        {isDeposit && (
          <p className="text-xs text-slate-400 rounded-lg border border-slate-800 bg-slate-800/40 p-3">
            {t("acc.record_money.deposit_hint", {
              defaultValue: "Move cash in hand into the bank. No party or invoice is involved.",
            })}
          </p>
        )}

        {/* How the money moved: three large buttons */}
        {!isDeposit && (
          <div>
            <span className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">
              {t("acc.record_money.mode_question", { defaultValue: "How was the money moved?" })}
            </span>
            <div className="grid grid-cols-3 gap-2">
              {moveModeButtons.map((btn) => (
                <button
                  key={btn.value}
                  type="button"
                  aria-pressed={moveMode === btn.value}
                  onClick={() => handleMoveModeChange(btn.value)}
                  className={`flex flex-col items-center justify-center gap-1.5 min-h-[72px] rounded-lg border px-2 py-3 text-sm font-semibold transition-colors ${
                    moveMode === btn.value
                      ? "border-indigo-500 bg-indigo-600 text-white shadow-sm"
                      : "border-slate-700 bg-slate-800 text-slate-300 hover:border-slate-500 hover:text-white"
                  }`}
                >
                  {btn.icon}
                  <span>{btn.label}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Party and Amount Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {!isDeposit && (
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
          )}

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

        {/* Date, reference (cheque number or UTR) and proof file */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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

          {!isDeposit && moveMode === "Cheque" && (
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">
                {t("acc.record_money.cheque_no", { defaultValue: "Cheque number (required)" })}
              </label>
              <input
                type="text"
                value={referenceNo}
                onChange={(e) => setReferenceNo(e.target.value)}
                required
                className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
          )}

          {!isDeposit && moveMode === "Bank Transfer" && (
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">
                {t("acc.record_money.reference_no", {
                  defaultValue: "Cheque / UTR / Reference No",
                })}
              </label>
              <input
                type="text"
                value={referenceNo}
                onChange={(e) => setReferenceNo(e.target.value)}
                placeholder="UTR-XXXXX"
                className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
          )}

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

        {/* More details: the bank / cash account picker (optional) */}
        {!isDeposit && (
          <div>
            <button
              type="button"
              onClick={() => setShowDetails((v) => !v)}
              aria-expanded={showDetails}
              className="flex items-center gap-1 text-xs font-semibold text-slate-400 hover:text-slate-200"
            >
              <ChevronDown
                className={`w-3.5 h-3.5 transition-transform ${showDetails ? "rotate-180" : ""}`}
              />
              {showDetails
                ? t("acc.record_money.less_details", { defaultValue: "Hide details" })
                : t("acc.record_money.more_details", { defaultValue: "More details" })}
            </button>
            {showDetails && (
              <div className="mt-2">
                <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">
                  {t("acc.record_money.bank_cash_account", {
                    defaultValue: "Bank / Cash Account",
                  })}
                </label>
                <select
                  value={paidAccount}
                  onChange={(e) => handleAccountChange(e.target.value)}
                  disabled={isLoadingAccounts}
                  className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                >
                  <option value="">
                    {t("acc.record_money.auto_account", {
                      defaultValue: "Automatic (from how it was moved)",
                    })}
                  </option>
                  {accounts.map((acc) => (
                    <option key={acc.id} value={acc.id}>
                      {acc.name} ({acc.account_type})
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>
        )}

        {/* Suggested Allocations Table */}
        {!isDeposit && (
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
        )}

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
                {isDeposit
                  ? t("acc.record_money.submit_deposit", { defaultValue: "Save & Submit Deposit" })
                  : t("acc.record_money.submit", { defaultValue: "Save & Submit Payment" })}
              </>
            )}
          </button>
        </div>
      </form>
    </Modal>
  );
};
