import React, { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, X } from "lucide-react";
import { normaliseError, summariseError } from "@/api/endpoints";

/* Shared look for the HR panels and pages (same tokens as the document framework). */
export const inputCls =
  "w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 hover:border-slate-700 focus:outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 disabled:opacity-60";
export const labelCls = "block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5";
export const cardCls = "bg-slate-900 border border-slate-800 rounded-xl p-5";
export const btnCls =
  "inline-flex items-center justify-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium disabled:opacity-50 transition-colors";
export const btnSecondary = `${btnCls} bg-slate-900 hover:bg-slate-800 border border-slate-800 shadow-sm text-slate-100`;
export const btnPrimary = `${btnCls} bg-indigo-600 hover:bg-indigo-500 text-white`;
export const btnDanger = `${btnCls} bg-rose-600 hover:bg-rose-500 text-white`;

/** One line of text for an API error (validation dict, {error}, network). */
export function errorText(e: unknown, fallback: string): string {
  const n = normaliseError(e);
  if (n.forbidden) return fallback;
  return summariseError(n) || fallback;
}

/** All messages of an API error, for an inline list. */
export function errorLines(e: unknown): string[] {
  const n = normaliseError(e);
  return [...n.general, ...Object.entries(n.fields).flatMap(([k, m]) => m.map((x) => `${k}: ${x}`))];
}

/** Modal with a form body (ConfirmDialog is text-only). */
export const Modal: React.FC<{
  open: boolean;
  title: string;
  busy?: boolean;
  confirmLabel: string;
  tone?: "primary" | "danger";
  onConfirm: () => void;
  onClose: () => void;
  children: React.ReactNode;
}> = ({ open, title, busy = false, confirmLabel, tone = "primary", onConfirm, onClose, children }) => {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-[90] flex items-end sm:items-center justify-center p-0 sm:p-4 bg-black/40 backdrop-blur-[2px]"
      onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="w-full max-w-lg max-h-[92vh] overflow-y-auto bg-slate-900 border border-slate-700 rounded-t-2xl sm:rounded-xl shadow-2xl p-5 sm:p-6 space-y-4"
      >
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-lg font-semibold text-slate-100">{title}</h3>
          <button type="button" onClick={onClose} disabled={busy} aria-label={t("common.close")} className="p-1.5 rounded-lg text-slate-500 hover:bg-slate-800">
            <X className="w-4 h-4" />
          </button>
        </div>
        {children}
        <div className="flex justify-end gap-3 pt-2">
          <button type="button" onClick={onClose} disabled={busy} className={btnSecondary}>
            {t("common.close")}
          </button>
          <button type="button" onClick={onConfirm} disabled={busy} className={tone === "danger" ? btnDanger : btnPrimary}>
            {busy && <Loader2 className="w-4 h-4 animate-spin" />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
};

/** Inline error box. */
export const ErrorBox: React.FC<{ lines: string[] }> = ({ lines }) =>
  lines.length === 0 ? null : (
    <div role="alert" className="p-3 rounded-lg bg-rose-950/60 border border-rose-800 text-rose-200 text-sm space-y-0.5">
      {lines.map((l, i) => (
        <p key={i}>{l}</p>
      ))}
    </div>
  );
