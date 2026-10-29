import React, { useEffect } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

interface ModalProps {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  /** Extra classes for the panel (e.g. a wider share dialog). */
  className?: string;
}

/** Small accessible modal used by the drive and company pages. */
export const Modal: React.FC<ModalProps> = ({
  open,
  title,
  description,
  onClose,
  children,
  footer,
  className = "max-w-md",
}) => {
  const { t } = useTranslation();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[95] flex items-center justify-center bg-black/50 p-4 backdrop-blur-[2px] animate-fade-in"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="app-modal-title"
        className={`w-full ${className} rounded-2xl border border-slate-700 bg-slate-900 p-5 shadow-2xl animate-pop-in`}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 id="app-modal-title" className="text-base font-semibold text-slate-100">
              {title}
            </h2>
            {description && <p className="mt-1 text-xs leading-relaxed text-slate-400">{description}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("common.close")}
            className="shrink-0 rounded-lg p-1.5 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-4">{children}</div>
        {footer && <div className="mt-5 flex justify-end gap-2 border-t border-slate-800 pt-4">{footer}</div>}
      </div>
    </div>,
    document.body
  );
};
