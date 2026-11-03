import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { apiClient } from "@/api/client";
import { normaliseError, summariseError } from "@/api/endpoints";
import { useToast } from "@/components/ui/Toast";

const inputClass =
  "w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-60";

const PwField: React.FC<{
  label: string;
  value: string;
  onChange: (v: string) => void;
  error?: string;
  autoComplete: string;
}> = ({ label, value, onChange, error, autoComplete }) => (
  <label className="block space-y-1.5">
    <span className="block text-xs font-semibold uppercase tracking-wider text-slate-400">{label}</span>
    <input
      type="password"
      autoComplete={autoComplete}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={inputClass}
    />
    {error && <span className="block text-[11px] text-rose-400">{error}</span>}
  </label>
);

/** "Change password" card on My profile (POST /auth/me/change-password/). */
export const ChangePasswordSection: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [general, setGeneral] = useState<string[]>([]);

  const mismatch = confirm !== "" && newPw !== confirm;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!oldPw || !newPw || newPw !== confirm) return;
    setBusy(true);
    setFieldErrors({});
    setGeneral([]);
    try {
      const res = await apiClient.post("/auth/me/change-password/", {
        old_password: oldPw,
        new_password: newPw,
      });
      // The server signs out every other device and returns a fresh pair for this one.
      if (res.data?.access) localStorage.setItem("access_token", res.data.access);
      if (res.data?.refresh) localStorage.setItem("refresh_token", res.data.refresh);
      setOldPw("");
      setNewPw("");
      setConfirm("");
      toast.success(t("usermgmt.change_password.done"));
    } catch (err) {
      const n = normaliseError(err);
      setFieldErrors(n.fields);
      const hasFieldMsgs = Object.values(n.fields).some((m) => m.length);
      setGeneral(
        n.general.length ? n.general : hasFieldMsgs ? [] : [summariseError(n) || t("usermgmt.change_password.failed")]
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl space-y-5">
      <div>
        <h2 className="text-sm font-semibold text-slate-100">{t("usermgmt.change_password.title")}</h2>
        <p className="text-[11px] text-slate-500 mt-1">{t("usermgmt.change_password.help")}</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
        <div className="sm:col-span-2">
          <PwField
            label={t("usermgmt.change_password.old_password")}
            value={oldPw}
            onChange={setOldPw}
            error={fieldErrors.old_password?.[0]}
            autoComplete="current-password"
          />
        </div>
        <PwField
          label={t("usermgmt.change_password.new_password")}
          value={newPw}
          onChange={setNewPw}
          error={fieldErrors.new_password?.join(" ")}
          autoComplete="new-password"
        />
        <PwField
          label={t("usermgmt.change_password.confirm_password")}
          value={confirm}
          onChange={setConfirm}
          error={mismatch ? t("usermgmt.change_password.mismatch") : undefined}
          autoComplete="new-password"
        />
      </div>
      {general.length > 0 && (
        <ul role="alert" className="text-[11px] text-rose-400 space-y-0.5">
          {general.map((m, i) => (
            <li key={i}>{m}</li>
          ))}
        </ul>
      )}
      <button
        type="submit"
        disabled={busy || !oldPw || !newPw || newPw !== confirm}
        className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white font-medium px-4 py-2 rounded-lg text-sm disabled:opacity-50"
      >
        {busy && <Loader2 className="w-4 h-4 animate-spin" />}
        {busy ? t("usermgmt.change_password.submitting") : t("usermgmt.change_password.submit")}
      </button>
    </form>
  );
};
