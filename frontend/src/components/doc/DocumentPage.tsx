import React, { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AlertCircle, ArrowLeft, Ban, CheckCircle2, Loader2, RotateCcw, Save, ShieldAlert, Trash2 } from "lucide-react";
import { useDebounced } from "@/hooks/useDebounced";
import { Layout } from "@/components/Layout";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { useToast } from "@/components/ui/Toast";
import { ChildTable, isColumnHidden, newRowKey, type Row } from "./ChildTable";
import { FieldInput, formatReadOnly } from "./FieldInput";
import {
  createRecord,
  deleteRecord,
  docAction,
  getRecord,
  normaliseError,
  summariseError,
  updateRecord,
  type NormalisedError,
} from "@/api/endpoints";
import { apiClient } from "@/api/client";
import {
  DocStatus,
  type ApiRecord,
  type DocAction,
  type FieldCheckResult,
  type FieldDef,
  type LinkRecord,
  type PreviewState,
  type ResourceConfig,
} from "@/types";

type Values = Record<string, any>;
type Tables = Record<string, Row[]>;

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

const resolveDefault = (f: FieldDef) =>
  typeof f.defaultValue === "function" ? f.defaultValue() : f.defaultValue;

const emptyOf = (f: FieldDef) => (f.type === "boolean" ? false : "");

function buildInitial(config: ResourceConfig, doc: ApiRecord | undefined, seed: Values | undefined) {
  const values: Values = {};
  const tables: Tables = {};
  if (doc) {
    Object.assign(values, doc);
    (config.childTables ?? []).forEach((t) => {
      tables[t.key] = ((doc[t.key] as Row[]) ?? []).map((r) => ({ ...r, _key: newRowKey(), _srv: { ...r } }));
    });
    return { values, tables };
  }
  config.fields.forEach((f) => {
    const d = resolveDefault(f);
    values[f.key] = d !== undefined && d !== null ? d : emptyOf(f);
  });
  Object.assign(values, seed ?? {});
  (config.childTables ?? []).forEach((t) => {
    const seeded = (seed?.[t.key] as Row[] | undefined) ?? [];
    tables[t.key] = seeded.map((r) => ({ ...r, _key: newRowKey() }));
    delete values[t.key];
  });
  return { values, tables };
}

const isEmpty = (v: unknown) => v === "" || v === null || v === undefined;

function cleanValue(f: { type: string }, v: any, isNew: boolean) {
  if (f.type === "boolean") return !!v;
  if (isEmpty(v)) {
    if (isNew) return undefined; // let the serializer default / required-check apply
    return f.type === "text" || f.type === "textarea" || f.type === "email" ? "" : null;
  }
  return v;
}

function buildPayload(
  config: ResourceConfig,
  values: Values,
  tables: Tables,
  isNew: boolean,
  /** Server-derived tables left out of the payload (the server re-derives them). */
  omit: ReadonlySet<string> = new Set()
): ApiRecord {
  const payload: ApiRecord = {};
  config.fields.forEach((f) => {
    if (f.readOnly || f.preview) return;
    if (f.readOnlyAfterCreate && !isNew) return;
    const v = cleanValue(f, values[f.key], isNew);
    if (v !== undefined) payload[f.key] = v;
  });
  (config.childTables ?? []).forEach((t) => {
    if (omit.has(t.key)) return;
    payload[t.key] = (tables[t.key] ?? []).map((row) => {
      const out: ApiRecord = {};
      t.columns.forEach((c) => {
        if (c.readOnly || c.preview) return;
        if (isColumnHidden(c, row, values)) return; // conditional column hidden: never send a stale value
        const v = cleanValue(c, row[c.key], true);
        if (v !== undefined) out[c.key] = v;
      });
      return out;
    });
  });
  return payload;
}

const snapshot = (values: Values, tables: Tables) =>
  JSON.stringify([
    values,
    Object.fromEntries(
      Object.entries(tables).map(([k, rows]) => [k, rows.map(({ _key, _srv, ...rest }) => rest)])
    ),
  ]);

/* ------------------------------------------------------------------ */
/* page: loads the document, then mounts the form                       */
/* ------------------------------------------------------------------ */

export const DocumentPage: React.FC<{ config: ResourceConfig }> = ({ config }) => {
  const { t } = useTranslation();
  const { id } = useParams();
  const location = useLocation();
  const isNew = id === undefined;

  const { data: doc, isLoading, error } = useQuery({
    queryKey: ["doc", config.endpoint, id],
    queryFn: () => getRecord(config.endpoint, id!),
    enabled: !isNew,
  });

  const seed = (location.state as { initialValues?: Values } | null)?.initialValues;
  const err = error ? normaliseError(error) : null;
  const backTo = `/${config.module}/${config.key}`;

  return (
    <Layout>
      {isLoading ? (
        <div className="flex items-center gap-2 text-slate-400 text-sm">
          <Loader2 className="w-4 h-4 animate-spin" /> {t("common.loading")}
        </div>
      ) : err ? (
        <div className="max-w-xl mx-auto mt-16 text-center space-y-3">
          <ShieldAlert className="w-9 h-9 mx-auto text-rose-400" />
          <p className="text-sm text-rose-300">
            {err.forbidden ? t("common.forbidden") : err.status === 404 ? t("common.not_found") : t("common.load_failed")}
          </p>
          <Link to={backTo} className="text-indigo-400 text-sm hover:underline">
            {t("common.back_to_list")}
          </Link>
        </div>
      ) : (
        <DocumentForm
          key={isNew ? `new-${location.key}` : `${id}-${doc?.modified_at}-${doc?.docstatus}`}
          config={config}
          doc={doc}
          seed={seed}
        />
      )}
    </Layout>
  );
};

/* ------------------------------------------------------------------ */
/* the form                                                            */
/* ------------------------------------------------------------------ */

type Confirm = null | "submit" | "cancel" | "delete" | "amend";

const DocumentForm: React.FC<{ config: ResourceConfig; doc?: ApiRecord; seed?: Values }> = ({ config, doc, seed }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();

  const isNew = !doc;
  const submittable = !!config.submittable;
  const docstatus: number = doc?.docstatus ?? DocStatus.DRAFT;
  const editable = isNew || !submittable || docstatus === DocStatus.DRAFT;
  const base = `/${config.module}/${config.key}`;
  const childKeys = useMemo(() => (config.childTables ?? []).map((c) => c.key), [config]);

  const initial = useMemo(() => buildInitial(config, doc, seed), [config, doc, seed]);
  const [values, setValues] = useState<Values>(initial.values);
  const [tables, setTables] = useState<Tables>(initial.tables);
  const [initialSnap] = useState(() => snapshot(initial.values, initial.tables));
  const [serverErr, setServerErr] = useState<NormalisedError | null>(null);
  const [clientErr, setClientErr] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [checks, setChecks] = useState<Record<string, FieldCheckResult | undefined>>({});

  const dirty = snapshot(values, tables) !== initialSnap;

  /* ---- server-side preview (config.preview) ---- */
  const pv = config.preview;
  const derivedKeys = useMemo(() => (config.childTables ?? []).filter((c) => c.derived).map((c) => c.key), [config]);
  // Derived tables the user took over by hand. A saved document starts as saved ("manual").
  const [manual, setManual] = useState<ReadonlySet<string>>(() => new Set(isNew ? [] : derivedKeys));
  const previewActive = !!pv && editable && (isNew || dirty);
  const reqKey = useMemo(() => {
    if (!pv || !previewActive) return null;
    const body = pv.buildRequest({ ...values, ...tables }, manual);
    return body ? JSON.stringify(body) : null;
  }, [pv, previewActive, values, tables, manual]);
  const debouncedKey = useDebounced(reqKey, pv?.debounceMs ?? 500);
  const inSync = reqKey !== null && reqKey === debouncedKey;
  const previewQ = useQuery({
    queryKey: ["preview", config.key, debouncedKey],
    queryFn: async () => (await apiClient.post(pv!.endpoint, JSON.parse(debouncedKey!))).data,
    enabled: !!pv && debouncedKey !== null,
    retry: false,
    staleTime: 0,
  });
  const previewData = inSync && previewQ.data ? previewQ.data : null;

  // Merge a response that matches the CURRENT form (stale ones are dropped) into the form.
  useEffect(() => {
    if (!pv || !previewData) return;
    const out = pv.apply(previewData, { values, tables, manual });
    if (out.values) setValues((v) => ({ ...v, ...out.values }));
    const patch = out.tables;
    if (patch) {
      setTables((prev) => {
        const next = { ...prev };
        Object.entries(patch).forEach(([k, rows]) => {
          next[k] = rows.map((r) => (r._key ? (r as Row) : { ...r, _key: newRowKey() }));
        });
        return next;
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewData]);

  const previewErrors = (): string[] => {
    if (!inSync || !previewQ.error || previewQ.isFetching) return [];
    const n = normaliseError(previewQ.error, derivedKeys);
    if (n.forbidden) return [];
    return [
      ...n.general,
      ...Object.values(n.fields).flat(),
      ...Object.values(n.tables).flat(),
      ...Object.values(n.rows).flatMap((byRow) => Object.values(byRow).flatMap((c) => Object.values(c).flat())),
    ];
  };
  const previewState: PreviewState | undefined = pv
    ? { data: previewData, errors: previewErrors(), pending: reqKey !== null && (!inSync || previewQ.isFetching) }
    : undefined;

  // Auto-pick the only option of a required lookup-backed link (e.g. the single company).
  useEffect(() => {
    if (!isNew) return;
    config.fields.forEach((f) => {
      if (f.type !== "link" || !f.link?.fetcher || !isEmpty(values[f.key])) return;
      f.link.fetcher("").then((rows) => {
        if (rows.length === 1) {
          const rec = rows[0];
          setValues((v) =>
            isEmpty(v[f.key]) ? { ...v, [f.key]: rec[f.link!.valueField || "id"], ...(f.link!.fill?.(rec, v) ?? {}) } : v
          );
        }
      });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ---- field editing ---- */
  const setField = (f: FieldDef, value: any, record?: LinkRecord | null) => {
    if (pv?.rederiveOn?.includes(f.key)) setManual((m) => new Set([...m].filter((k) => !derivedKeys.includes(k))));
    if (f.check) setChecks((c) => ({ ...c, [f.key]: undefined }));
    setValues((prev) => {
      const next: Values = { ...prev, [f.key]: value };
      if (record && f.link?.fill) Object.assign(next, f.link.fill(record, next));
      (f.clears ?? []).forEach((k) => {
        const target = config.fields.find((x) => x.key === k);
        next[k] = target ? emptyOf(target) : "";
      });
      return next;
    });
    setClientErr((e) => {
      if (!e[f.key]) return e;
      const { [f.key]: _drop, ...rest } = e;
      return rest;
    });
    setServerErr((s) => (s && s.fields[f.key] ? { ...s, fields: { ...s.fields, [f.key]: [] } } : s));
  };

  /** Run a field's server-side check (on blur). Never blocks; the server re-validates on save. */
  const runCheck = async (f: FieldDef) => {
    const chk = f.check;
    const raw = values[f.key];
    if (!chk || isEmpty(raw)) return;
    try {
      const res = await apiClient.get(chk.endpoint, { params: { [chk.param]: String(raw).trim() } });
      const out = chk.interpret(res.data, values);
      setChecks((c) => ({ ...c, [f.key]: out }));
      if (out.fill) setValues((v) => ({ ...v, ...out.fill }));
    } catch {
      setChecks((c) => ({ ...c, [f.key]: undefined })); // a failed check is simply not shown
    }
  };

  /* ---- mutations ---- */
  const invalidate = (id?: number | string) => {
    qc.invalidateQueries({ queryKey: ["list", config.endpoint] });
    if (id !== undefined) qc.invalidateQueries({ queryKey: ["doc", config.endpoint, String(id)] });
  };

  const onError = (e: any) => {
    const n = normaliseError(e, childKeys);
    setServerErr(n);
    setConfirm(null);
    toast.error(
      n.forbidden ? t("common.forbidden") : summariseError(n) || t("common.action_failed")
    );
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const validateClient = (): boolean => {
    const errs: Record<string, string> = {};
    config.fields.forEach((f) => {
      if (f.readOnly || f.preview || (f.readOnlyAfterCreate && !isNew) || f.hidden?.(values, isNew)) return;
      if (f.required && f.type !== "boolean" && isEmpty(values[f.key])) errs[f.key] = t("common.required");
    });
    (config.childTables ?? []).forEach((tb) => {
      (tables[tb.key] ?? []).forEach((row, idx) => {
        tb.columns.forEach((c) => {
          if (c.required && !c.readOnly && !c.preview && !isColumnHidden(c, row, values) && isEmpty(row[c.key])) {
            errs[`${tb.key}.${idx}.${c.key}`] = t("common.required");
          }
        });
      });
    });
    setClientErr(errs);
    if (Object.keys(errs).length) {
      toast.error(t("common.fix_errors"));
      window.scrollTo({ top: 0, behavior: "smooth" });
      return false;
    }
    return true;
  };

  const persist = async (): Promise<ApiRecord> => {
    const omit = new Set(derivedKeys.filter((k) => !manual.has(k)));
    const payload = buildPayload(config, values, tables, isNew, omit);
    return isNew ? createRecord(config.endpoint, payload) : updateRecord(config.endpoint, doc!.id!, payload);
  };

  const saveMut = useMutation({
    mutationFn: persist,
    onSuccess: (saved) => {
      setServerErr(null);
      toast.success(t("common.saved"));
      qc.setQueryData(["doc", config.endpoint, String(saved.id)], saved);
      invalidate(saved.id);
      if (isNew) navigate(`${base}/${saved.id}`, { replace: true });
    },
    onError,
  });

  const submitMut = useMutation({
    mutationFn: async () => {
      let id = doc!.id!;
      if (dirty) id = (await persist()).id!; // never submit something other than what is on screen
      return docAction(config.endpoint, id, "submit");
    },
    onSuccess: (saved) => {
      setServerErr(null);
      setConfirm(null);
      toast.success(t("common.submitted_ok"));
      qc.setQueryData(["doc", config.endpoint, String(saved.id)], saved);
      invalidate(saved.id);
    },
    onError: (e) => {
      // A failed submit may still have saved the edits: refresh the doc.
      invalidate(doc?.id);
      onError(e);
    },
  });

  const cancelMut = useMutation({
    mutationFn: () => docAction(config.endpoint, doc!.id!, "cancel"),
    onSuccess: (saved) => {
      setServerErr(null);
      setConfirm(null);
      toast.success(t("common.cancelled_ok"));
      qc.setQueryData(["doc", config.endpoint, String(saved.id)], saved);
      invalidate(saved.id);
    },
    onError,
  });

  const amendMut = useMutation({
    mutationFn: () => docAction(config.endpoint, doc!.id!, "amend"),
    onSuccess: (saved) => {
      setConfirm(null);
      toast.success(t("common.amended_ok"));
      qc.setQueryData(["doc", config.endpoint, String(saved.id)], saved);
      invalidate(saved.id);
      navigate(`${base}/${saved.id}`);
    },
    onError,
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteRecord(config.endpoint, doc!.id!),
    onSuccess: () => {
      setConfirm(null);
      toast.success(t("common.deleted_ok"));
      invalidate();
      qc.removeQueries({ queryKey: ["doc", config.endpoint, String(doc!.id)] });
      navigate(base, { replace: true });
    },
    onError,
  });

  const requestMut = useMutation({
    mutationFn: (a: DocAction) =>
      apiClient.post(a.url!(doc!), a.body?.(doc!)).then((r) => ({ action: a, result: r.data as ApiRecord })),
    onSuccess: ({ action, result }) => {
      toast.success(t("common.action_done"));
      invalidate(doc?.id);
      qc.invalidateQueries({ queryKey: ["list"] });
      if (action.resultTo) navigate(action.resultTo(result));
    },
    onError,
  });

  const busy =
    saveMut.isPending || submitMut.isPending || cancelMut.isPending || amendMut.isPending || deleteMut.isPending;

  const onSave = () => {
    if (!validateClient()) return;
    setServerErr(null);
    saveMut.mutate();
  };

  const onAskSubmit = () => {
    if (!validateClient()) return;
    setConfirm("submit");
  };

  /* ---- error plumbing for rendering ---- */
  const fieldErr = (key: string): string | undefined =>
    clientErr[key] ?? (serverErr?.fields[key]?.length ? serverErr.fields[key].join(" ") : undefined);

  // Server errors on keys that have no visible field still need to be seen.
  const knownKeys = new Set([...config.fields.map((f) => f.key), ...childKeys]);
  const orphanFieldErrors = Object.entries(serverErr?.fields ?? {})
    .filter(([k, m]) => !knownKeys.has(k) && m.length)
    .map(([k, m]) => `${k}: ${m.join(" ")}`);
  const banner = [...(serverErr?.general ?? []), ...orphanFieldErrors];

  /* ---- render helpers ---- */
  const formValues: Values = { ...values, ...tables };
  const visible = (f: FieldDef) => !f.hidden?.(formValues, isNew);
  const editableField = (f: FieldDef) => editable && !f.readOnly && !f.preview && !(f.readOnlyAfterCreate && !isNew);

  const computedFields = config.fields.filter((f) => f.preview && visible(f));
  const formFields = config.fields.filter((f) => !f.preview && visible(f));

  const title = isNew ? t("common.new_item", { item: t(config.singularKey) }) : String(doc?.[config.titleField ?? "name"] ?? "");

  const visibleActions = (config.actions ?? []).filter((a) => !isNew && (!a.showWhen || a.showWhen(doc!)));
  const runAction = (a: (typeof visibleActions)[number]) => {
    if (a.kind === "navigate" && a.navigate) {
      const target = a.navigate(doc!);
      navigate(target.to, { state: { initialValues: target.initialValues } });
    } else if (a.kind === "request" && a.url) {
      requestMut.mutate(a);
    }
  };

  const spanClass = (f: FieldDef) => {
    const span = f.span ?? (f.type === "textarea" ? 3 : 1);
    return span === 3 ? "md:col-span-2 lg:col-span-3" : span === 2 ? "md:col-span-2" : "";
  };

  const btn = "inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium disabled:opacity-50 transition-colors";

  return (
    <div className="max-w-6xl mx-auto space-y-5">
      {/* header */}
      <div className="flex flex-wrap items-center justify-between gap-3 sticky top-16 z-30 bg-slate-950/90 backdrop-blur py-3 -mt-3">
        <div className="flex items-center gap-3 min-w-0">
          <Link to={base} className="p-2 rounded-lg text-slate-400 hover:text-slate-100 hover:bg-slate-800" aria-label={t("common.back_to_list")}>
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <h1 className="text-xl font-semibold text-slate-100 truncate">{title}</h1>
          {submittable && !isNew && <StatusBadge docstatus={docstatus} />}
          {submittable && isNew && <StatusBadge docstatus={DocStatus.DRAFT} />}
          {dirty && !isNew && (
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-950/50 text-amber-300 border border-amber-800/50">
              {t("common.not_saved")}
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {visibleActions.map((a) => {
            const Icon = a.icon;
            return (
              <button
                key={a.key}
                type="button"
                disabled={busy || requestMut.isPending}
                onClick={() => runAction(a)}
                className={`${btn} bg-slate-900 hover:bg-slate-800 border border-slate-800 shadow-sm text-slate-100`}
              >
                {Icon && <Icon className="w-4 h-4" />} {t(a.labelKey)}
              </button>
            );
          })}

          {!isNew && editable && !config.noDelete && (
            <button type="button" disabled={busy} onClick={() => setConfirm("delete")} className={`${btn} text-rose-300 hover:bg-rose-950/50 border border-rose-900/60`}>
              <Trash2 className="w-4 h-4" /> {submittable ? t("delete") : t("common.delete")}
            </button>
          )}

          {submittable && !isNew && docstatus === DocStatus.SUBMITTED && (
            <button type="button" disabled={busy} onClick={() => setConfirm("cancel")} className={`${btn} bg-rose-600 hover:bg-rose-500 text-white`}>
              <Ban className="w-4 h-4" /> {t("cancel")}
            </button>
          )}

          {submittable && !isNew && docstatus === DocStatus.CANCELLED && (
            <button type="button" disabled={busy} onClick={() => setConfirm("amend")} className={`${btn} bg-indigo-600 hover:bg-indigo-500 text-white`}>
              <RotateCcw className="w-4 h-4" /> {t("amend")}
            </button>
          )}

          {editable && (
            <button
              type="button"
              disabled={busy || (!isNew && !dirty)}
              onClick={onSave}
              className={`${btn} bg-slate-900 hover:bg-slate-800 border border-slate-800 shadow-sm text-slate-100`}
            >
              {saveMut.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              {submittable ? t("save") : t("common.save_short")}
            </button>
          )}

          {submittable && !isNew && docstatus === DocStatus.DRAFT && (
            <button type="button" disabled={busy} onClick={onAskSubmit} className={`${btn} bg-emerald-600 hover:bg-emerald-500 text-white`}>
              <CheckCircle2 className="w-4 h-4" /> {t("submit")}
            </button>
          )}
        </div>
      </div>

      {/* errors */}
      {banner.length > 0 && (
        <div role="alert" className="flex gap-2 p-3.5 rounded-lg bg-rose-950/60 border border-rose-800 text-rose-200 text-sm">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <ul className="space-y-0.5">
            {banner.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </div>
      )}

      {/* header fields */}
      <section className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-x-5 gap-y-4">
          {formFields.map((f) => {
            const err = fieldErr(f.key);
            const ro = !editableField(f);
            return (
              <div key={f.key} className={spanClass(f)}>
                {f.type === "boolean" ? (
                  <label className="flex items-center gap-2 text-sm text-slate-300 mt-6">
                    <FieldInput
                      def={f}
                      cacheKey={`${config.key}.${f.key}`}
                      value={values[f.key]}
                      values={formValues}
                      parentValues={formValues}
                      readOnly={ro}
                      onChange={(v) => setField(f, v)}
                    />
                    {t(f.labelKey)}
                  </label>
                ) : (
                  <>
                    <label htmlFor={`f-${f.key}`} className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
                      {t(f.labelKey)}
                      {f.required && editable && <span className="text-rose-400"> *</span>}
                    </label>
                    <FieldInput
                      id={`f-${f.key}`}
                      def={f}
                      cacheKey={`${config.key}.${f.key}`}
                      value={values[f.key]}
                      values={formValues}
                      parentValues={formValues}
                      readOnly={ro}
                      invalid={!!err || (!!f.check && checks[f.key]?.ok === false)}
                      onChange={(v) => setField(f, v)}
                      onLinkSelect={(v, rec) => setField(f, v, rec)}
                      onBlur={f.check ? () => void runCheck(f) : undefined}
                    />
                  </>
                )}
                {f.check && checks[f.key] && (
                  <p className={`text-[11px] mt-1 ${checks[f.key]!.ok ? "text-emerald-400" : "text-rose-400"}`}>
                    {checks[f.key]!.ok ? t(f.check.okKey) : checks[f.key]!.message}
                  </p>
                )}
                {f.helpKey && !err && editable && <p className="text-[11px] text-slate-500 mt-1">{t(f.helpKey)}</p>}
                {err && <p className="text-[11px] text-rose-400 mt-1">{err}</p>}
              </div>
            );
          })}
        </div>
      </section>

      {/* child tables */}
      {(config.childTables ?? []).map((tb) => {
        const rowErrs: Record<number, Record<string, string[]>> = { ...(serverErr?.rows[tb.key] ?? {}) };
        Object.entries(clientErr).forEach(([k, msg]) => {
          const m = new RegExp(`^${tb.key}\\.(\\d+)\\.(.+)$`).exec(k);
          if (m) rowErrs[+m[1]] = { ...(rowErrs[+m[1]] ?? {}), [m[2]]: [msg] };
        });
        return (
          <ChildTable
            key={tb.key}
            def={tb}
            cacheKeyBase={`${config.key}.${tb.key}`}
            rows={tables[tb.key] ?? []}
            parentValues={values}
            readOnly={!editable || !!tb.readOnly}
            errors={rowErrs}
            tableErrors={serverErr?.tables[tb.key]}
            onChange={(rows) => {
              if (tb.derived) setManual((m) => (m.has(tb.key) ? m : new Set([...m, tb.key])));
              setTables((prev) => ({ ...prev, [tb.key]: rows }));
              setServerErr((s) => (s ? { ...s, rows: { ...s.rows, [tb.key]: {} } } : s));
            }}
          />
        );
      })}

      {/* totals: server value when pristine, client estimate while editing */}
      {computedFields.length > 0 && (
        <div className="flex justify-end">
          <dl className="w-full max-w-sm bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-2">
            {computedFields.map((f) => {
              const live = dirty ? f.preview!(formValues) : (values[f.key] ?? f.preview!(formValues));
              return (
                <div key={f.key} className="flex items-baseline justify-between gap-4">
                  <dt className="text-xs uppercase tracking-wider text-slate-400">{t(f.labelKey)}</dt>
                  <dd className="text-lg font-semibold text-slate-100 tabular-nums">
                    {f.renderValue ? f.renderValue(live, formValues) : formatReadOnly(f, live)}
                    {previewState?.pending ? (
                      <Loader2 className="inline w-3 h-3 ml-2 animate-spin text-slate-500" aria-label={t("common.loading")} />
                    ) : (
                      dirty &&
                      !previewState?.data && (
                        <span className="ml-2 text-[10px] font-normal text-amber-400">{t("common.estimate")}</span>
                      )
                    )}
                  </dd>
                </div>
              );
            })}
          </dl>
        </div>
      )}

      {config.renderSummary?.({ values: formValues, dirty, editable, preview: previewState })}

      {/* confirmations */}
      <ConfirmDialog
        open={confirm === "submit"}
        title={t("doc.confirm_submit_title", { item: t(config.singularKey) })}
        message={dirty ? t("doc.confirm_submit_dirty") : t("doc.confirm_submit_message")}
        confirmLabel={t("submit")}
        busy={submitMut.isPending}
        onConfirm={() => submitMut.mutate()}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === "cancel"}
        title={t("doc.confirm_cancel_title", { item: t(config.singularKey) })}
        message={t("doc.confirm_cancel_message")}
        confirmLabel={t("cancel")}
        tone="danger"
        busy={cancelMut.isPending}
        onConfirm={() => cancelMut.mutate()}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === "amend"}
        title={t("doc.confirm_amend_title")}
        message={t("doc.confirm_amend_message")}
        confirmLabel={t("amend")}
        busy={amendMut.isPending}
        onConfirm={() => amendMut.mutate()}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === "delete"}
        title={t("doc.confirm_delete_title", { item: t(config.singularKey) })}
        message={t("doc.confirm_delete_message")}
        confirmLabel={submittable ? t("delete") : t("common.delete")}
        tone="danger"
        busy={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate()}
        onClose={() => setConfirm(null)}
      />
    </div>
  );
};
