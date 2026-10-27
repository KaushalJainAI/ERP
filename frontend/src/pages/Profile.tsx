import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { Layout } from "@/components/Layout";
import { useToast } from "@/components/ui/Toast";
import { apiClient } from "@/api/client";
import { normaliseError, summariseError } from "@/api/endpoints";
import { useAuth } from "@/hooks/useAuth";
import { useChangeLanguage } from "@/hooks/useChangeLanguage";
import { ChangePasswordSection } from "@/components/users/ChangePasswordSection";
import { SUPPORTED_LANGUAGES } from "@/i18n/i18n";

const inputClass =
  "w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-60";

const Field: React.FC<{ label: string; help?: string; children: React.ReactNode; error?: string }> = ({
  label,
  help,
  children,
  error,
}) => (
  <label className="block space-y-1.5">
    <span className="block text-xs font-semibold uppercase tracking-wider text-slate-400">{label}</span>
    {children}
    {help && !error && <span className="block text-[11px] text-slate-500">{help}</span>}
    {error && <span className="block text-[11px] text-rose-400">{error}</span>}
  </label>
);

/** "My profile": account details from GET/PATCH /auth/me/. */
export const Profile: React.FC = () => {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const { user, refetchUser } = useAuth();
  const changeLanguage = useChangeLanguage();

  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [saving, setSaving] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  // (Re)seed the form whenever the server copy of the user changes.
  useEffect(() => {
    if (!user) return;
    setFirstName(user.first_name ?? "");
    setLastName(user.last_name ?? "");
    setEmail(user.email ?? "");
  }, [user]);

  if (!user) {
    return (
      <Layout>
        <p className="text-sm text-rose-300" role="alert">
          {t("profile.load_failed")}
        </p>
      </Layout>
    );
  }

  const dirty =
    firstName !== (user.first_name ?? "") || lastName !== (user.last_name ?? "") || email !== (user.email ?? "");

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setFieldErrors({});
    try {
      await apiClient.patch("/auth/me/", { first_name: firstName, last_name: lastName, email });
      await refetchUser();
      toast.success(t("profile.saved"));
    } catch (err) {
      const n = normaliseError(err);
      setFieldErrors(n.fields);
      toast.error(summariseError(n) || t("profile.save_failed"));
    } finally {
      setSaving(false);
    }
  };

  const onLanguage = async (code: string) => {
    const saved = await changeLanguage(code);
    if (!saved) toast.info(t("lang.saved_locally"));
  };

  const currentLang = i18n.language?.split("-")[0] ?? user.language;

  return (
    <Layout>
      <div className="max-w-2xl space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-100 tracking-tight">{t("profile.title")}</h1>
          <p className="text-slate-400 text-xs mt-1">{t("profile.subtitle")}</p>
        </div>

        <form onSubmit={save} className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl space-y-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <Field label={t("profile.username")}>
              <input value={user.username} disabled className={inputClass} />
            </Field>
            <Field label={t("profile.email")} error={fieldErrors.email?.[0]}>
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} />
            </Field>
            <Field label={t("profile.first_name")} error={fieldErrors.first_name?.[0]}>
              <input value={firstName} onChange={(e) => setFirstName(e.target.value)} className={inputClass} />
            </Field>
            <Field label={t("profile.last_name")} error={fieldErrors.last_name?.[0]}>
              <input value={lastName} onChange={(e) => setLastName(e.target.value)} className={inputClass} />
            </Field>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="submit"
              disabled={!dirty || saving}
              className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white font-medium px-4 py-2 rounded-lg text-sm disabled:opacity-50"
            >
              {saving && <Loader2 className="w-4 h-4 animate-spin" />}
              {saving ? t("profile.saving") : t("profile.save")}
            </button>
            {dirty && !saving && <span className="text-xs text-amber-300">{t("profile.unsaved")}</span>}
          </div>
        </form>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl space-y-5">
          <Field label={t("profile.language")} help={t("profile.language_help")}>
            <select value={currentLang} onChange={(e) => onLanguage(e.target.value)} className={inputClass}>
              {SUPPORTED_LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.name}
                </option>
              ))}
            </select>
          </Field>

          <div className="space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-slate-400">
              {t("profile.roles")}
            </span>
            {user.roles.length === 0 && !user.is_superuser ? (
              <p className="text-sm text-slate-400">{t("profile.no_roles")}</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {user.is_superuser && (
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-medium border bg-purple-950/60 text-purple-300 border-purple-800/60">
                    {t("profile.superuser")}
                  </span>
                )}
                {user.roles.map((r) => (
                  <span
                    key={r}
                    className="px-2.5 py-0.5 rounded-full text-xs font-medium border bg-indigo-950/60 text-indigo-300 border-indigo-800/60"
                  >
                    {r}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>

        <ChangePasswordSection />
      </div>
    </Layout>
  );
};
