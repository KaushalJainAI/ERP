import React, { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, XCircle, Clock, FileCheck, ArrowLeft, RefreshCw, Trash2 } from "lucide-react";

export interface FieldMetadata {
  name: string;
  label: string;
  type: "text" | "number" | "decimal" | "date" | "select" | "boolean";
  required?: boolean;
  options?: { value: string | number; label: string }[];
  readOnly?: boolean;
}

interface DocFormProps {
  title: string;
  fields: FieldMetadata[];
  initialData?: any;
  onSave?: (data: any) => Promise<void>;
  onSubmitDoc?: (data: any) => Promise<void>;
  onCancelDoc?: (data: any) => Promise<void>;
  onAmendDoc?: (data: any) => Promise<void>;
  onDeleteDoc?: (data: any) => Promise<void>;
  onBack?: () => void;
}

export const DocForm: React.FC<DocFormProps> = ({
  title,
  fields,
  initialData = {},
  onSave,
  onSubmitDoc,
  onCancelDoc,
  onAmendDoc,
  onDeleteDoc,
  onBack,
}) => {
  const { t } = useTranslation();
  const [formData, setFormData] = useState<any>(initialData);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    setFormData(initialData || {});
  }, [initialData]);

  const docstatus = formData.docstatus !== undefined ? formData.docstatus : 0;
  const isDraft = docstatus === 0;
  const isSubmitted = docstatus === 1;
  const isCancelled = docstatus === 2;

  const handleChange = (name: string, value: any) => {
    if (!isDraft) return; // Edit lock
    setFormData((prev: any) => ({ ...prev, [name]: value }));
  };

  const handleAction = async (actionFn?: (data: any) => Promise<void>) => {
    if (!actionFn) return;
    setLoading(true);
    setErrorMsg(null);
    try {
      await actionFn(formData);
    } catch (err: any) {
      const msg = err.response?.data?.error || err.message || "An error occurred";
      setErrorMsg(typeof msg === "object" ? JSON.stringify(msg) : msg);
    } finally {
      setLoading(false);
    }
  };

  const renderStatusBadge = () => {
    if (isSubmitted) {
      return (
        <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-emerald-950/80 text-emerald-400 border border-emerald-800">
          <CheckCircle2 className="w-3.5 h-3.5 mr-1.5" /> {t("submitted")}
        </span>
      );
    }
    if (isCancelled) {
      return (
        <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-rose-950/80 text-rose-400 border border-rose-800">
          <XCircle className="w-3.5 h-3.5 mr-1.5" /> {t("cancelled")}
        </span>
      );
    }
    return (
      <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-amber-950/80 text-amber-400 border border-amber-800">
        <Clock className="w-3.5 h-3.5 mr-1.5" /> {t("draft")}
      </span>
    );
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-2xl p-6 space-y-6">
      {/* Top Header */}
      <div className="flex flex-wrap items-center justify-between border-b border-slate-800 pb-5 gap-4">
        <div className="flex items-center space-x-3">
          {onBack && (
            <button
              onClick={onBack}
              className="p-2 rounded-lg bg-slate-950 border border-slate-800 hover:bg-slate-800 text-slate-400 hover:text-slate-100 transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
          )}
          <div>
            <h2 className="text-2xl font-bold text-slate-100 tracking-tight">{title}</h2>
            <p className="text-xs text-slate-400 mt-0.5">{formData.name ? `ID: ${formData.name}` : "New Document"}</p>
          </div>
        </div>
        <div className="flex items-center space-x-3">
          {renderStatusBadge()}

          {/* Action Buttons */}
          {isDraft && onSave && (
            <button
              onClick={() => handleAction(onSave)}
              disabled={loading}
              className="bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg text-sm font-medium transition-all shadow-md shadow-indigo-600/20 disabled:opacity-50"
            >
              {t("save")}
            </button>
          )}
          {isDraft && onSubmitDoc && (
            <button
              onClick={() => handleAction(onSubmitDoc)}
              disabled={loading}
              className="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg text-sm font-medium transition-all shadow-md shadow-emerald-600/20 flex items-center space-x-1.5 disabled:opacity-50"
            >
              <FileCheck className="w-4 h-4" />
              <span>{t("submit")}</span>
            </button>
          )}
          {isDraft && onDeleteDoc && formData.id && (
            <button
              onClick={() => handleAction(onDeleteDoc)}
              disabled={loading}
              className="bg-rose-950/60 hover:bg-rose-900 border border-rose-800 text-rose-300 px-3 py-2 rounded-lg text-sm font-medium transition-all disabled:opacity-50"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          )}
          {isSubmitted && onCancelDoc && (
            <button
              onClick={() => handleAction(onCancelDoc)}
              disabled={loading}
              className="bg-rose-600 hover:bg-rose-500 text-white px-4 py-2 rounded-lg text-sm font-medium transition-all shadow-md shadow-rose-600/20 flex items-center space-x-1.5 disabled:opacity-50"
            >
              <XCircle className="w-4 h-4" />
              <span>{t("cancel")}</span>
            </button>
          )}
          {isCancelled && onAmendDoc && (
            <button
              onClick={() => handleAction(onAmendDoc)}
              disabled={loading}
              className="bg-purple-600 hover:bg-purple-500 text-white px-4 py-2 rounded-lg text-sm font-medium transition-all shadow-md shadow-purple-600/20 flex items-center space-x-1.5 disabled:opacity-50"
            >
              <RefreshCw className="w-4 h-4" />
              <span>{t("amend")}</span>
            </button>
          )}
        </div>
      </div>

      {errorMsg && (
        <div className="p-4 rounded-lg bg-rose-950/70 border border-rose-800 text-rose-200 text-sm font-medium">
          {errorMsg}
        </div>
      )}

      {/* Form Fields Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {fields.map((field) => (
          <div key={field.name} className="space-y-1.5">
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider">
              {field.label} {field.required && <span className="text-rose-400">*</span>}
            </label>
            {field.type === "select" ? (
              <select
                value={formData[field.name] || ""}
                disabled={!isDraft || field.readOnly}
                onChange={(e) => handleChange(field.name, e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3.5 py-2.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                <option value="">Select option...</option>
                {field.options?.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            ) : field.type === "boolean" ? (
              <div className="pt-2">
                <input
                  type="checkbox"
                  checked={!!formData[field.name]}
                  disabled={!isDraft || field.readOnly}
                  onChange={(e) => handleChange(field.name, e.target.checked)}
                  className="w-4 h-4 rounded border-slate-800 bg-slate-950 text-indigo-600 focus:ring-indigo-500"
                />
              </div>
            ) : (
              <input
                type={field.type === "number" || field.type === "decimal" ? "number" : field.type === "date" ? "date" : "text"}
                step={field.type === "decimal" ? "0.01" : "1"}
                value={formData[field.name] || ""}
                disabled={!isDraft || field.readOnly}
                onChange={(e) => handleChange(field.name, e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3.5 py-2.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-60 disabled:cursor-not-allowed"
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
};
