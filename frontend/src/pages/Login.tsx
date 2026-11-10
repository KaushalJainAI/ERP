import React, { useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { useLocation, useNavigate } from "react-router-dom";
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
  const location = useLocation();
  // Only a path inside this app ("/x", never "//host" or a full URL) is followed.
  const from = (location.state as { from?: unknown } | null)?.from;
  const returnTo = typeof from === "string" && from.startsWith("/") && !from.startsWith("//") ? from : "/";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      navigate(returnTo, { replace: true });
    } catch (err: any) {
      const status = err?.response?.status;
      if (!err?.response || (status && status >= 500)) setError(t("auth.network_error"));
      else if (status === 429) setError(t("auth.too_many_attempts"));
      else setError(t("auth.invalid_credentials"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 flex flex-col justify-between items-center p-6 relative overflow-hidden">
      {/* Soft saffron glow (NGU --backdrop-spice) */}
      <div className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[36rem] h-[36rem] bg-indigo-600/10 rounded-full blur-3xl pointer-events-none" />

      <div className="w-full max-w-md flex justify-between items-center z-10 pt-4">
        <div className="flex items-center gap-2">
          <img src="/logo.png" alt="" className="h-9 w-auto rounded-md bg-white p-0.5" />
          <span className="font-semibold text-slate-100 text-base tracking-tight">{t("appName")}</span>
        </div>
        <LanguageSwitcher />
      </div>

      <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-8 shadow-2xl z-10 my-auto">
        <div className="text-center mb-8">
          <h2 className="text-2xl font-semibold text-slate-100 tracking-tight">{t("auth.welcome")}</h2>
          <p className="text-slate-400 text-sm mt-1">{t("auth.subtitle")}</p>
        </div>

        {error && (
          <div className="mb-6 p-3.5 rounded-lg bg-rose-950/60 border border-rose-800 text-rose-300 text-xs font-medium">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label htmlFor="login-username" className="block text-xs font-medium text-slate-400 mb-1.5">
              {t("auth.username")}
            </label>
            <div className="relative">
              <UserIcon className="w-4 h-4 absolute start-3.5 top-3.5 text-slate-500" />
              <input
                id="login-username"
                type="text"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                className="w-full bg-slate-950 border border-slate-800 rounded-lg ps-10 pe-4 py-2.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
              />
            </div>
          </div>

          <div>
            <label htmlFor="login-password" className="block text-xs font-medium text-slate-400 mb-1.5">
              {t("password")}
            </label>
            <div className="relative">
              <Lock className="w-4 h-4 absolute start-3.5 top-3.5 text-slate-500" />
              <input
                id="login-password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                className="w-full bg-slate-950 border border-slate-800 rounded-lg ps-10 pe-4 py-2.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-semibold py-3 rounded-lg text-sm transition-all shadow-lg shadow-indigo-600/20 flex items-center justify-center gap-2 disabled:opacity-50 mt-2"
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
