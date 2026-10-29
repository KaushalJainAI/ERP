import React, { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Building2,
  CheckCircle2,
  Loader2,
  Mail,
  MapPin,
  Phone,
  Save,
  ShieldCheck,
  Globe2,
} from "lucide-react";
import { Layout } from "@/components/Layout";
import { Select } from "@/components/ui/Select";
import { useToast } from "@/components/ui/Toast";
import { apiClient } from "@/api/client";
import { listAllRecords, normaliseError, summariseError } from "@/api/endpoints";
import { useCompany } from "@/context/CompanyContext";
import { CompanyDocumentsPanel } from "@/components/drive/CompanyDocumentsPanel";
import type { CompanyProfile } from "@/types/organisation";

interface StateRow {
  code: string;
  name: string;
}

const inputClass =
  "w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 disabled:cursor-not-allowed disabled:opacity-60";

const Field: React.FC<{
  label: string;
  error?: string;
  help?: string;
  children: React.ReactNode;
  className?: string;
}> = ({ label, error, help, children, className = "" }) => (
  <label className={`block space-y-1.5 ${className}`}>
    <span className="block text-[11px] font-semibold uppercase tracking-wider text-slate-500">{label}</span>
    {children}
    {help && !error && <span className="block text-[11px] text-slate-600">{help}</span>}
    {error && <span className="block text-[11px] text-rose-400">{error}</span>}
  </label>
);

const Section: React.FC<{
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}> = ({ title, icon: Icon, children }) => (
  <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-sm">
    <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-100">
      <Icon className="h-4 w-4 text-slate-400" />
      {title}
    </h2>
    <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">{children}</div>
  </section>
);

const FORM_KEYS = [
  "name",
  "legal_name",
  "abbr",
  "default_currency",
  "country",
  "gstin",
  "email",
  "phone_no",
  "website",
  "address_line1",
  "address_line2",
  "city",
  "pincode",
  "pan",
  "cin",
  "date_of_incorporation",
  "state",
  "gst_category",
  "hsn_min_digits",
  "enable_rounded_total",
] as const;

type FormValues = Record<string, any>;

function formFrom(profile: CompanyProfile): FormValues {
  const out: FormValues = {};
  for (const key of FORM_KEYS) out[key] = (profile as any)[key] ?? "";
  out.date_of_incorporation = profile.date_of_incorporation ?? "";
  out.state = profile.state ?? "";
  out.gstin = profile.gstin ?? "";
  out.hsn_min_digits = profile.hsn_min_digits ?? 6;
  out.enable_rounded_total = !!profile.enable_rounded_total;
  return out;
}

/** Company profile and its stored statutory documents. */
export const CompanyPage: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const { activeCompany, refresh } = useCompany();
  const [form, setForm] = useState<FormValues>({});
  const [initial, setInitial] = useState<FormValues>({});
  const [saving, setSaving] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  const profileKey = ["company-profile", activeCompany?.id ?? null];
  const { data, isLoading, isError } = useQuery<CompanyProfile>({
    queryKey: profileKey,
    queryFn: async () => (await apiClient.get("/core/companies/current/")).data,
    enabled: !!activeCompany,
  });

  const { data: states } = useQuery<StateRow[]>({
    queryKey: ["gst-states"],
    queryFn: async () => (await listAllRecords("/core/states/", { is_active: true })) as unknown as StateRow[],
    enabled: !!activeCompany,
    staleTime: 10 * 60_000,
  });

  useEffect(() => {
    if (!data) return;
    const next = formFrom(data);
    setForm(next);
    setInitial(next);
    setFieldErrors({});
  }, [data]);

  const canWrite = !!data?.can_write;
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);

  const stateOptions = useMemo(
    () => [
      { value: "", label: "" },
      ...(states ?? []).map((state) => ({ value: state.code, label: `${state.code} - ${state.name}` })),
    ],
    [states]
  );

  const categoryOptions = useMemo(
    () => [
      { value: "Registered Regular", label: t("gst.opt.registered_regular") },
      { value: "Registered Composition", label: t("gst.opt.registered_composition") },
      { value: "Unregistered", label: t("gst.opt.unregistered") },
      { value: "SEZ", label: t("gst.opt.sez") },
      { value: "Overseas", label: t("gst.opt.overseas") },
    ],
    [t]
  );

  const set = (key: string, value: any) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setFieldErrors((prev) => {
      if (!prev[key]) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!data || !canWrite) return;
    setSaving(true);
    setFieldErrors({});
    const payload: Record<string, any> = {};
    for (const key of FORM_KEYS) payload[key] = form[key];
    payload.date_of_incorporation = form.date_of_incorporation || null;
    payload.gstin = form.gstin || null;
    payload.state = form.state || null;
    payload.hsn_min_digits = Number(form.hsn_min_digits || 6);
    payload.enable_rounded_total = !!form.enable_rounded_total;

    try {
      const res = await apiClient.patch<CompanyProfile>(`/core/companies/${data.id}/`, payload);
      const next = formFrom(res.data);
      setForm(next);
      setInitial(next);
      toast.success(t("org.company.saved"));
      qc.invalidateQueries({ queryKey: profileKey });
      qc.invalidateQueries({ queryKey: ["company-profile"] });
      void refresh();
    } catch (err) {
      const normalized = normaliseError(err);
      setFieldErrors(normalized.fields);
      toast.error(summariseError(normalized) || t("org.company.save_failed"));
    } finally {
      setSaving(false);
    }
  };

  if (isLoading) {
    return (
      <Layout>
        <div className="flex min-h-[50vh] items-center justify-center gap-2 text-sm text-slate-400">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("common.loading")}
        </div>
      </Layout>
    );
  }

  if (isError || !data) {
    return (
      <Layout>
        <div className="mx-auto mt-20 max-w-lg rounded-2xl border border-rose-900/50 bg-rose-950/20 p-6 text-center text-sm text-rose-300">
          {t("org.company.load_failed")}
        </div>
      </Layout>
    );
  }

  const companyName = data.name || activeCompany?.name || "";
  const subtitle = t("org.company.subtitle", { name: companyName });

  return (
    <Layout>
      <form onSubmit={save} className="space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 items-start gap-4">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-indigo-900/50 bg-indigo-950/50 text-indigo-300">
              <Building2 className="h-6 w-6" />
            </span>
            <div className="min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-indigo-400">
                {t("org.company.eyebrow")}
              </p>
              <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-100">
                {t("org.company.title")}
              </h1>
              <p className="mt-1 max-w-2xl text-sm text-slate-400">{subtitle}</p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className="rounded-full border border-emerald-800/60 bg-emerald-950/40 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300">
                  <CheckCircle2 className="me-1 inline h-3 w-3" />
                  {t("org.company.active")}
                </span>
                {data.is_default && (
                  <span className="rounded-full border border-indigo-800/60 bg-indigo-950/40 px-2.5 py-0.5 text-[11px] font-medium text-indigo-300">
                    {t("org.company.default_company")}
                  </span>
                )}
                {!canWrite && (
                  <span className="rounded-full border border-slate-700 bg-slate-800/50 px-2.5 py-0.5 text-[11px] font-medium text-slate-400">
                    {t("org.company.readonly_note")}
                  </span>
                )}
              </div>
            </div>
          </div>

          {canWrite && (
            <div className="flex items-center gap-3">
              {dirty && !saving && <span className="text-xs text-amber-300">{t("org.company.unsaved")}</span>}
              <button
                type="submit"
                disabled={!dirty || saving}
                className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-indigo-600/20 hover:bg-indigo-500 disabled:opacity-50"
              >
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                {saving ? t("org.company.saving") : t("org.company.save")}
              </button>
            </div>
          )}
        </header>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="space-y-5">
            <Section title={t("org.company.identity")} icon={Building2}>
              <Field label={t("org.company.name")} error={fieldErrors.name?.[0]}>
                <input
                  value={form.name ?? ""}
                  onChange={(e) => set("name", e.target.value)}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
              <Field label={t("org.company.legal_name")} error={fieldErrors.legal_name?.[0]}>
                <input
                  value={form.legal_name ?? ""}
                  onChange={(e) => set("legal_name", e.target.value)}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
              <Field label={t("org.company.abbr")} error={fieldErrors.abbr?.[0]}>
                <input
                  value={form.abbr ?? ""}
                  maxLength={10}
                  onChange={(e) => set("abbr", e.target.value)}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
              <Field label={t("org.company.date_of_incorporation")} error={fieldErrors.date_of_incorporation?.[0]}>
                <input
                  type="date"
                  value={form.date_of_incorporation ?? ""}
                  onChange={(e) => set("date_of_incorporation", e.target.value)}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
            </Section>

            <Section title={t("org.company.contact")} icon={Phone}>
              <Field label={t("org.company.email")} error={fieldErrors.email?.[0]}>
                <input
                  type="email"
                  value={form.email ?? ""}
                  onChange={(e) => set("email", e.target.value)}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
              <Field label={t("org.company.phone_no")} error={fieldErrors.phone_no?.[0]}>
                <input
                  value={form.phone_no ?? ""}
                  onChange={(e) => set("phone_no", e.target.value)}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
              <Field label={t("org.company.website")} error={fieldErrors.website?.[0]} className="sm:col-span-2">
                <input
                  type="url"
                  placeholder="https://"
                  value={form.website ?? ""}
                  onChange={(e) => set("website", e.target.value)}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
            </Section>

            <Section title={t("org.company.address")} icon={MapPin}>
              <Field label={t("org.company.address_line1")} error={fieldErrors.address_line1?.[0]} className="sm:col-span-2">
                <input
                  value={form.address_line1 ?? ""}
                  onChange={(e) => set("address_line1", e.target.value)}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
              <Field label={t("org.company.address_line2")} error={fieldErrors.address_line2?.[0]} className="sm:col-span-2">
                <input
                  value={form.address_line2 ?? ""}
                  onChange={(e) => set("address_line2", e.target.value)}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
              <Field label={t("org.company.city")} error={fieldErrors.city?.[0]}>
                <input
                  value={form.city ?? ""}
                  onChange={(e) => set("city", e.target.value)}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
              <Field label={t("org.company.state")} error={fieldErrors.state?.[0]}>
                <Select
                  value={String(form.state ?? "")}
                  onChange={(value) => set("state", value)}
                  options={stateOptions}
                  disabled={!canWrite}
                  placeholder={t("common.select")}
                />
              </Field>
              <Field label={t("org.company.pincode")} error={fieldErrors.pincode?.[0]}>
                <input
                  inputMode="numeric"
                  maxLength={6}
                  value={form.pincode ?? ""}
                  onChange={(e) => set("pincode", e.target.value.replace(/\D/g, ""))}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
              <Field label={t("org.company.country")} error={fieldErrors.country?.[0]}>
                <input
                  value={form.country ?? ""}
                  onChange={(e) => set("country", e.target.value)}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
            </Section>

            <Section title={t("org.company.statutory")} icon={ShieldCheck}>
              <Field label={t("org.company.gstin")} error={fieldErrors.gstin?.[0]}>
                <input
                  value={form.gstin ?? ""}
                  maxLength={20}
                  onChange={(e) => set("gstin", e.target.value.toUpperCase())}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
              <Field label={t("org.company.pan")} error={fieldErrors.pan?.[0]}>
                <input
                  value={form.pan ?? ""}
                  maxLength={10}
                  onChange={(e) => set("pan", e.target.value.toUpperCase())}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
              <Field label={t("org.company.cin")} error={fieldErrors.cin?.[0]} className="sm:col-span-2">
                <input
                  value={form.cin ?? ""}
                  maxLength={21}
                  onChange={(e) => set("cin", e.target.value.toUpperCase())}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
            </Section>

            <Section title={t("org.company.preferences")} icon={Globe2}>
              <Field label={t("org.company.default_currency")} error={fieldErrors.default_currency?.[0]}>
                <input
                  value={form.default_currency ?? ""}
                  maxLength={3}
                  onChange={(e) => set("default_currency", e.target.value.toUpperCase())}
                  disabled={!canWrite}
                  className={inputClass}
                />
              </Field>
              <Field label={t("org.company.gst_category")} error={fieldErrors.gst_category?.[0]}>
                <Select
                  value={String(form.gst_category ?? "Unregistered")}
                  onChange={(value) => set("gst_category", value)}
                  options={categoryOptions}
                  disabled={!canWrite}
                />
              </Field>
              <Field label={t("org.company.hsn_min_digits")} error={fieldErrors.hsn_min_digits?.[0]}>
                <Select
                  value={String(form.hsn_min_digits ?? 6)}
                  onChange={(value) => set("hsn_min_digits", Number(value))}
                  options={[
                    { value: "4", label: "4" },
                    { value: "6", label: "6" },
                    { value: "8", label: "8" },
                  ]}
                  disabled={!canWrite}
                />
              </Field>
              <label className="flex items-center gap-2.5 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-sm text-slate-300">
                <input
                  type="checkbox"
                  checked={!!form.enable_rounded_total}
                  onChange={(e) => set("enable_rounded_total", e.target.checked)}
                  disabled={!canWrite}
                  className="h-4 w-4 accent-indigo-500"
                />
                {t("org.company.enable_rounded_total")}
              </label>
            </Section>
          </div>

          <aside className="space-y-5">
            <section className="rounded-2xl border border-slate-800 bg-gradient-to-br from-slate-900 to-slate-950 p-5 shadow-sm">
              <div className="flex items-center gap-3">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-indigo-900/50 bg-indigo-950/50 text-lg font-semibold text-indigo-200">
                  {(data.abbr || data.name || "?").slice(0, 2).toUpperCase()}
                </span>
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-slate-100">{data.name}</p>
                  <p className="truncate text-xs text-slate-400">{data.legal_name || data.abbr}</p>
                </div>
              </div>
              <dl className="mt-4 space-y-2.5 text-xs">
                {data.gstin && (
                  <div className="flex justify-between gap-3">
                    <dt className="text-slate-500">{t("org.company.gstin")}</dt>
                    <dd className="font-mono text-slate-300">{data.gstin}</dd>
                  </div>
                )}
                {data.email && (
                  <div className="flex items-center gap-2 text-slate-400">
                    <Mail className="h-3.5 w-3.5 shrink-0 text-slate-600" />
                    <span className="truncate">{data.email}</span>
                  </div>
                )}
                {data.phone_no && (
                  <div className="flex items-center gap-2 text-slate-400">
                    <Phone className="h-3.5 w-3.5 shrink-0 text-slate-600" />
                    <span className="truncate">{data.phone_no}</span>
                  </div>
                )}
                {(data.city || data.state_name) && (
                  <div className="flex items-center gap-2 text-slate-400">
                    <MapPin className="h-3.5 w-3.5 shrink-0 text-slate-600" />
                    <span className="truncate">{[data.city, data.state_name].filter(Boolean).join(", ")}</span>
                  </div>
                )}
                {data.website && (
                  <div className="flex items-center gap-2 text-slate-400">
                    <Globe2 className="h-3.5 w-3.5 shrink-0 text-slate-600" />
                    <a href={data.website} target="_blank" rel="noreferrer" className="truncate hover:text-indigo-300">
                      {data.website}
                    </a>
                  </div>
                )}
              </dl>
            </section>

            <CompanyDocumentsPanel />
          </aside>
        </div>
      </form>
    </Layout>
  );
};
