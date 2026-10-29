import React from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, Loader2, Sparkles, X, XCircle } from "lucide-react";

export interface AgentStep {
  key: string;
  label_key: string;
  args_preview: string;
  status: "running" | "done" | "failed";
  result_preview: string;
}

interface AgentWorkingOverlayProps {
  isOpen: boolean;
  steps: AgentStep[];
  error?: string | null;
  onStop: () => void;
}

export const AgentWorkingOverlay: React.FC<AgentWorkingOverlayProps> = ({
  isOpen,
  steps,
  error,
  onStop,
}) => {
  const { t } = useTranslation();

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm">
      <div className="w-full max-w-md bg-slate-900 border border-slate-700/80 rounded-2xl shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800 bg-slate-900/50">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-indigo-500/20 text-indigo-400 flex items-center justify-center">
              <Sparkles className="w-4 h-4 animate-pulse" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-100">
                {t("assistant.processing")}
              </h3>
              <p className="text-xs text-slate-400">
                {steps.length > 0
                  ? t(steps[steps.length - 1].label_key, steps[steps.length - 1].key)
                  : t("assistant.listening")}
              </p>
            </div>
          </div>
          <button
            onClick={onStop}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition"
            title={t("assistant.stop")}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Steps List */}
        <div className="p-5 max-h-72 overflow-y-auto space-y-3">
          {steps.map((step, idx) => {
            const isLast = idx === steps.length - 1;
            const label = t(step.label_key, step.key);

            return (
              <div
                key={`${step.key}-${idx}`}
                className="flex items-start gap-3 text-sm animate-in fade-in slide-in-from-bottom-2 duration-150"
              >
                <div className="mt-0.5 shrink-0">
                  {step.status === "running" ? (
                    <Loader2 className="w-4 h-4 text-amber-400 animate-spin" />
                  ) : step.status === "done" ? (
                    <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  ) : (
                    <XCircle className="w-4 h-4 text-rose-400" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <p
                    className={`font-medium ${
                      step.status === "running"
                        ? "text-slate-200"
                        : step.status === "done"
                        ? "text-slate-300"
                        : "text-rose-300"
                    }`}
                  >
                    {label}
                  </p>
                  {step.result_preview && (
                    <p className="text-xs text-slate-400 truncate mt-0.5">
                      {step.result_preview}
                    </p>
                  )}
                </div>
              </div>
            );
          })}

          {error && (
            <div className="p-3 bg-rose-950/40 border border-rose-800/60 rounded-xl text-xs text-rose-300">
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3.5 bg-slate-900/80 border-t border-slate-800 flex justify-end">
          <button
            type="button"
            onClick={onStop}
            className="px-4 py-1.5 text-xs font-medium rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition"
          >
            {t("assistant.stop")}
          </button>
        </div>
      </div>
    </div>
  );
};
