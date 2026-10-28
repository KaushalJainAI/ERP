import React, { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message?: React.ReactNode;
  confirmLabel: string;
  /** "danger" paints the confirm button red (cancel / delete). */
  tone?: "primary" | "danger";
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

/** In-app modal replacement for window.confirm. */
export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open,
  title,
  message,
  confirmLabel,
  tone = "primary",
  busy = false,
  onConfirm,
  onClose,
}) => {
  const { t } = useTranslation();
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  const confirmClass =
    tone === "danger"
      ? "bg-rose-600 hover:bg-rose-500 shadow-rose-600/20"
      : "bg-indigo-600 hover:bg-indigo-500 shadow-indigo-600/20";

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center p-4 bg-black/40 backdrop-blur-[2px]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        className="w-full max-w-md bg-slate-900 border border-slate-700 rounded-xl shadow-2xl p-6 space-y-4"
      >
        <h3 id="confirm-title" className="text-lg font-semibold text-slate-100">
          {title}
        </h3>
        {message && <div className="text-sm text-slate-400 leading-relaxed">{message}</div>}
        <div className="flex justify-end gap-3 pt-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="px-4 py-2 rounded-lg text-sm font-medium text-slate-300 bg-slate-900 hover:bg-slate-800 border border-slate-800 shadow-sm disabled:opacity-50"
          >
            {t("common.close")}
          </button>
          <button
            ref={confirmRef}
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={`px-4 py-2 rounded-lg text-sm font-medium text-white shadow-lg disabled:opacity-60 flex items-center gap-2 ${confirmClass}`}
          >
            {busy && <Loader2 className="w-4 h-4 animate-spin" />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
};
