import React from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, FileText, MessageSquareQuote, Paperclip, Sparkles } from "lucide-react";

export interface AssistantHandoffState {
  proposalId: string;
  summary: string;
  aiFilledKeys: string[];
  warnings: string[];
  target?: string;
}

interface AssistantReviewBannerProps {
  assistantState: AssistantHandoffState;
  onTellMeWhatToChange?: () => void;
}

export const AssistantReviewBanner: React.FC<AssistantReviewBannerProps> = ({
  assistantState,
  onTellMeWhatToChange,
}) => {
  const { t } = useTranslation();
  const { summary, warnings, target } = assistantState;
  const isPurchase = target?.toLowerCase().includes("purchase");

  return (
    <div className="mb-6 rounded-2xl border border-indigo-500/30 bg-gradient-to-r from-indigo-950/40 via-slate-900/60 to-purple-950/30 p-4 sm:p-5 shadow-lg backdrop-blur-sm">
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
        <div className="space-y-2 flex-1">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
              <Sparkles className="w-3.5 h-3.5" />
              {t("assistant.review_banner_title")}
            </span>
          </div>

          <p className="text-sm font-medium text-slate-100 leading-relaxed">
            {summary}
          </p>

          {isPurchase && (
            <div className="flex items-center gap-2 text-xs text-amber-300/90 bg-amber-950/30 border border-amber-800/40 px-3 py-1.5 rounded-lg w-fit">
              <Paperclip className="w-3.5 h-3.5 shrink-0" />
              <span>{t("assistant.attach_bill")}</span>
            </div>
          )}

          {warnings && warnings.length > 0 && (
            <div className="space-y-1 pt-1">
              {warnings.map((w, idx) => (
                <div
                  key={idx}
                  className="flex items-start gap-1.5 text-xs text-amber-400 bg-amber-950/20 px-2.5 py-1 rounded-md"
                >
                  <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  <span>{w}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {onTellMeWhatToChange && (
          <button
            type="button"
            onClick={onTellMeWhatToChange}
            className="shrink-0 inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-medium rounded-xl bg-slate-800/80 hover:bg-slate-700 text-indigo-300 hover:text-indigo-200 border border-indigo-500/30 transition shadow-sm"
          >
            <MessageSquareQuote className="w-4 h-4" />
            {t("assistant.not_right")}
          </button>
        )}
      </div>
    </div>
  );
};
