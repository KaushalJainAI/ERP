import React, { useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Lock, User as UserIcon, ArrowRight } from "lucide-react";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";

export const Login: React.FC = () => {
  const { t } = useTranslation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      navigate("/dashboard");
    } catch (err: any) {
      const status = err?.response?.status;
      if (!err?.response) setError(t("auth.network_error"));
      else if (status === 429) setError(t("auth.too_many_attempts"));
      else setError(t("auth.invalid_credentials"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 flex flex-col justify-between items-center p-6 relative overflow-hidden">
      {/* Background glow effects */}
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 bg-indigo-600/10 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-600/10 rounded-full blur-3xl pointer-events-none" />

      <div className="w-full max-w-md flex justify-between items-center z-10 pt-4">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center font-bold text-white shadow-lg shadow-indigo-600/30">
            ERP
          </div>
          <span className="font-bold text-slate-100 text-lg">{t("appName")}</span>
        </div>
        <LanguageSwitcher />
      </div>

      <div className="w-full max-w-md bg-slate-900/90 border border-slate-800 rounded-2xl p-8 shadow-2xl backdrop-blur-xl z-10 my-auto">
        <div className="text-center mb-8">
          <h2 className="text-2xl font-bold text-slate-100 tracking-tight">{t("auth.welcome")}</h2>
          <p className="text-slate-400 text-xs mt-1">{t("auth.subtitle")}</p>
        </div>

        {error && (
          <div className="mb-6 p-3.5 rounded-lg bg-rose-950/80 border border-rose-800 text-rose-300 text-xs font-medium">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5">
              {t("auth.username")}
            </label>
            <div className="relative">
              <UserIcon className="w-4 h-4 absolute start-3.5 top-3.5 text-slate-400" />
              <input
                type="text"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                className="w-full bg-slate-950 border border-slate-800 rounded-lg ps-10 pe-4 py-2.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5">
              {t("password")}
            </label>
            <div className="relative">
              <Lock className="w-4 h-4 absolute start-3.5 top-3.5 text-slate-400" />
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                className="w-full bg-slate-950 border border-slate-800 rounded-lg ps-10 pe-4 py-2.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-semibold py-3 rounded-lg text-sm transition-all shadow-lg shadow-indigo-600/30 flex items-center justify-center gap-2 disabled:opacity-50 mt-2"
          >
            <span>{submitting ? t("auth.signing_in") : t("auth.sign_in")}</span>
            <ArrowRight className="w-4 h-4" />
          </button>
        </form>
      </div>

      <div className="z-10 pb-4" aria-hidden="true" />
    </div>
  );
};
