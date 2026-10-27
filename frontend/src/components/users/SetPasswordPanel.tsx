import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { KeyRound, Loader2 } from "lucide-react";
import { apiClient } from "@/api/client";
import { normaliseError, summariseError } from "@/api/endpoints";
import { useToast } from "@/components/ui/Toast";
import { useAuth } from "@/hooks/useAuth";

const inputClass =
  "w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-60";

/**
 * Admin "set password" box on the user form (POST /auth/users/{id}/set-password/).
 * Hidden for a new user (the initial password is a form field), for your own
 * account (the server refuses; use My profile) and for superuser accounts when
 * you are not one yourself.
 */
export const SetPasswordPanel: React.FC<{ userId?: number | string; targetIsSuperuser?: boolean }> = ({
  userId,
  targetIsSuperuser,
}) => {
  const { t } = useTranslation();
  const toast = useToast();
  const { user } = useAuth();
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  if (userId === undefined || userId === null || userId === "") return null;
  if (user && String(user.id) === String(userId)) return null;
  if (targetIsSuperuser && !user?.is_superuser) return null;

  const mismatch = confirm !== "" && pw !== confirm;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pw || pw !== confirm) return;
    setBusy(true);
    setErrors([]);
    try {
      await apiClient.post(`/auth/users/${userId}/set-password/`, { new_password: pw });
      setPw("");
      setConfirm("");
      toast.success(t("usermgmt.set_password.done"));
    } catch (err) {
      const n = normaliseError(err);
      const msgs = [...n.general, ...Object.values(n.fields).flat()];
      setErrors(msgs.length ? msgs : [summariseError(n) || t("usermgmt.set_password.failed")]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
          <KeyRound className="w-4 h-4 text-slate-400" /> {t("usermgmt.set_password.title")}
        </h2>
        <p className="text-[11px] text-slate-500 mt-1">{t("usermgmt.set_password.help")}</p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label className="block space-y-1.5">
          <span className="block text-xs font-semibold uppercase tracking-wider text-slate-400">
            {t("usermgmt.set_password.new_password")}
          </span>
          <input
            type="password"
            autoComplete="new-password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            className={inputClass}
          />
        </label>
        <label className="block space-y-1.5">
          <span className="block text-xs font-semibold uppercase tracking-wider text-slate-400">
            {t("usermgmt.set_password.confirm_password")}
          </span>
          <input
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            className={inputClass}
          />
          {mismatch && <span className="block text-[11px] text-rose-400">{t("usermgmt.set_password.mismatch")}</span>}
        </label>
      </div>
      {errors.length > 0 && (
        <ul role="alert" className="text-[11px] text-rose-400 space-y-0.5">
          {errors.map((m, i) => (
            <li key={i}>{m}</li>
          ))}
        </ul>
      )}
      <button
        type="submit"
        disabled={busy || !pw || pw !== confirm}
        className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white font-medium px-4 py-2 rounded-lg text-sm disabled:opacity-50"
      >
        {busy && <Loader2 className="w-4 h-4 animate-spin" />}
        {t("usermgmt.set_password.submit")}
      </button>
    </form>
  );
};
