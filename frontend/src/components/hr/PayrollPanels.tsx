import React, { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { HandCoins, Loader2, ListPlus, ReceiptText, Wallet } from "lucide-react";
import { apiClient } from "@/api/client";
import { useToast } from "@/components/ui/Toast";
import { LinkField } from "@/components/doc/LinkField";
import { Select } from "@/components/ui/Select";
import { formatIndianCurrency, todayISO } from "@/lib/format";
import { DocStatus, type ApiRecord, type LinkDef } from "@/types";
import { optKey } from "@/resources/hrOptions";
import { btnPrimary, btnSecondary, cardCls, ErrorBox, errorLines, errorText, inputCls, labelCls, Modal } from "./hrUi";

/* ------------------------------------------------------------------ */
/* Salary slip: recompute hint + "Pay now"                              */
/* ------------------------------------------------------------------ */

export const SalarySlipPanel: React.FC<{ slip: ApiRecord; dirty: boolean }> = ({ slip }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const submitted = slip.docstatus === DocStatus.SUBMITTED;

  // Wages still owed to the employee (GL balance, as of now).
  const owed = useQuery({
    queryKey: ["hr", "slip-payable", slip.employee],
    queryFn: async () => (await apiClient.get(`/hr/employees/${slip.employee}/summary/`)).data as ApiRecord,
    enabled: submitted && !!slip.employee,
    retry: false,
  });
  const settings = useQuery({
    queryKey: ["hr", "settings", slip.company],
    queryFn: async () => {
      const res = await apiClient.get("/hr/settings/", { params: { company: slip.company } });
      const rows: ApiRecord[] = Array.isArray(res.data) ? res.data : (res.data.results ?? []);
      return rows[0] ?? null;
    },
    enabled: submitted && !!slip.company,
    retry: false,
  });

  if (slip.id === undefined || slip.id === null) return null;

  if (!submitted) {
    return slip.docstatus === DocStatus.DRAFT ? (
      <p className="text-sm text-slate-400 bg-slate-900 border border-slate-800 rounded-xl p-4">{t("hrm.help_generated")}</p>
    ) : null;
  }

  const payable = Number(owed.data?.wages_payable ?? 0);
  if (!(payable > 0)) return null;

  const net = Number(slip.net_pay ?? 0);
  const amount = (net > 0 && net < payable ? net : payable).toFixed(2);

  const payNow = () =>
    navigate("/hr/employee-payments/new", {
      state: {
        initialValues: {
          company: slip.company,
          employee: slip.employee,
          employee_name: slip.employee_name,
          posting_date: todayISO(),
          amount,
          mode: "Cash",
          paid_from: settings.data?.default_payment_account ?? "",
          remarks: slip.name,
        },
      },
    });

  return (
    <section className={`${cardCls} flex flex-wrap items-center justify-between gap-4`}>
      <div>
        <h2 className="text-base font-semibold text-slate-100">{t("hrm.slip.payable_now")}</h2>
        <p className="text-2xl font-semibold tabular-nums text-amber-400 mt-1">{formatIndianCurrency(payable)}</p>
        <p className="text-sm text-slate-400 mt-1">{t("hrm.slip.pay_now_help")}</p>
      </div>
      <button type="button" className={btnPrimary} onClick={payNow}>
        <Wallet className="w-4 h-4" /> {t("hrm.actions.pay_now")}
      </button>
    </section>
  );
};

/* ------------------------------------------------------------------ */
/* Payroll entry: get employees / create slips / make payments          */
/* ------------------------------------------------------------------ */

interface Skipped {
  employee: number | string;
  employee_name?: string;
  reason: string;
}

const PAYMENT_MODES = ["Cash", "Bank", "UPI"];

export const PayrollEntryPanel: React.FC<{ entry: ApiRecord; dirty: boolean }> = ({ entry, dirty }) => {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const id = entry.id;
  const skippedKey = ["hr", "payroll-skipped", id];
  // Kept in the query cache so the list survives the form reloading after the action.
  const [skipped, setSkippedState] = useState<Skipped[]>(() => (qc.getQueryData(skippedKey) as Skipped[] | undefined) ?? []);
  const setSkipped = (rows: Skipped[]) => {
    qc.setQueryData(skippedKey, rows);
    setSkippedState(rows);
  };

  const [payOpen, setPayOpen] = useState(false);
  const [paidFrom, setPaidFrom] = useState<string | number>("");
  const [mode, setMode] = useState("Cash");
  const [postingDate, setPostingDate] = useState(todayISO);
  const [reference, setReference] = useState("");
  const [payErr, setPayErr] = useState<string[]>([]);
  const [lastPayments, setLastPayments] = useState<ApiRecord[]>([]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["doc", "/hr/payroll-entries/", String(id)] });
    qc.invalidateQueries({ queryKey: ["list", "/hr/payroll-entries/"] });
  };

  const getEmployees = useMutation({
    mutationFn: () => apiClient.post(`/hr/payroll-entries/${id}/get-employees/`),
    onSuccess: () => {
      toast.success(t("hrm.payroll.employees_added"));
      refresh();
    },
    onError: (e) => toast.error(errorText(e, t("common.action_failed"))),
  });

  const createSlips = useMutation({
    mutationFn: async () => (await apiClient.post(`/hr/payroll-entries/${id}/create-slips/`)).data as ApiRecord,
    onSuccess: (res) => {
      setSkipped((res.skipped ?? []) as Skipped[]);
      toast.success(t("hrm.payroll.slips_created", { count: (res.created ?? []).length }));
      refresh();
    },
    onError: (e) => toast.error(errorText(e, t("common.action_failed"))),
  });

  const accountLink = useMemo<LinkDef>(
    () => ({
      endpoint: "/accounts/accounts/",
      labelField: "name",
      subLabel: (r) => r.account_type || r.root_type,
      filters: () => ({ company: entry.company, root_type: "Asset", is_group: false, is_active: true }),
    }),
    [entry.company]
  );

  const makePayments = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = { mode, posting_date: postingDate, reference_no: reference };
      if (paidFrom !== "") body.paid_from = paidFrom;
      return (await apiClient.post(`/hr/payroll-entries/${id}/make-payments/`, body)).data as ApiRecord[];
    },
    onSuccess: (rows) => {
      setPayOpen(false);
      setPayErr([]);
      setLastPayments(rows);
      toast.success(rows.length ? t("hrm.payroll.paid_n", { count: rows.length }) : t("hrm.payroll.nothing_to_pay"));
      qc.invalidateQueries({ queryKey: ["list", "/hr/employee-payments/"] });
      if (rows.length) navigate(`/hr/employee-payments?search=${encodeURIComponent(String(entry.name))}`);
    },
    onError: (e) => setPayErr(errorLines(e).length ? errorLines(e) : [errorText(e, t("common.action_failed"))]),
  });

  if (id === undefined || id === null) return null;
  const draft = entry.docstatus === DocStatus.DRAFT;
  const submitted = entry.docstatus === DocStatus.SUBMITTED;
  if (!draft && !submitted) return null;

  const busy = getEmployees.isPending || createSlips.isPending;

  return (
    <section className={`${cardCls} space-y-4`}>
      {draft && (
        <>
          <p className="text-sm text-slate-400">{t("hrm.payroll.steps_help")}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={btnSecondary} disabled={busy || dirty} onClick={() => getEmployees.mutate()}>
              {getEmployees.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <ListPlus className="w-4 h-4" />}
              {t("hrm.actions.get_employees")}
            </button>
            <button type="button" className={btnSecondary} disabled={busy || dirty} onClick={() => createSlips.mutate()}>
              {createSlips.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <ReceiptText className="w-4 h-4" />}
              {t("hrm.actions.create_slips")}
            </button>
          </div>
          {dirty && <p className="text-xs text-amber-400">{t("hrm.actions.save_first")}</p>}
          {skipped.length > 0 && (
            <div className="rounded-lg border border-amber-800/60 bg-amber-950/30 p-3 space-y-2">
              <p className="text-sm font-medium text-amber-300">{t("hrm.payroll.skipped_title")}</p>
              <p className="text-xs text-slate-400">{t("hrm.payroll.skipped_help")}</p>
              <ul className="divide-y divide-amber-900/40 text-sm">
                {skipped.map((s, i) => (
                  <li key={`${s.employee}-${i}`} className="py-1.5 flex flex-wrap gap-x-3">
                    <span className="font-medium text-slate-200">{s.employee_name ?? s.employee}</span>
                    <span className="text-slate-400">{s.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {submitted && (
        <>
          <p className="text-sm text-slate-400">{t("hrm.payroll.pay_help")}</p>
          <div>
            <button type="button" className={btnPrimary} disabled={dirty} onClick={() => setPayOpen(true)}>
              <HandCoins className="w-4 h-4" /> {t("hrm.actions.make_payments")}
            </button>
          </div>
          {lastPayments.length > 0 && (
            <ul className="text-sm divide-y divide-slate-800">
              {lastPayments.map((p) => (
                <li key={p.name} className="py-1.5 flex justify-between gap-3">
                  <Link to={`/hr/employee-payments/${p.id}`} className="text-indigo-400 hover:underline">
                    {p.name} - {p.employee_name}
                  </Link>
                  <span className="tabular-nums">{formatIndianCurrency(p.amount)}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <Modal
        open={payOpen}
        title={t("hrm.payroll.pay_title")}
        confirmLabel={t("hrm.payroll.confirm")}
        busy={makePayments.isPending}
        onConfirm={() => {
          setPayErr([]);
          makePayments.mutate();
        }}
        onClose={() => setPayOpen(false)}
      >
        <p className="text-sm text-slate-400">{t("hrm.payroll.pay_help")}</p>
        <div>
          <label className={labelCls}>{t("hrm.payroll.paid_from")}</label>
          <LinkField
            link={accountLink}
            cacheKey="payroll-entries.make-payments.paid_from"
            value={paidFrom === "" ? null : paidFrom}
            values={{}}
            parentValues={{}}
            onSelect={(v) => setPaidFrom(v ?? "")}
          />
          <p className="text-[11px] text-slate-500 mt-1">{t("hrm.payroll.paid_from_default")}</p>
        </div>
        <div>
          <label className={labelCls}>{t("hrm.payroll.mode")}</label>
          <Select
            value={mode}
            options={PAYMENT_MODES.map((m) => ({ value: m, label: t(optKey(m)) }))}
            onChange={setMode}
            aria-label={t("hrm.payroll.mode")}
          />
        </div>
        <div>
          <label htmlFor="pay-date" className={labelCls}>
            {t("hrm.payroll.posting_date")}
          </label>
          <input id="pay-date" type="date" value={postingDate} onChange={(e) => setPostingDate(e.target.value)} className={inputCls} />
        </div>
        <div>
          <label htmlFor="pay-ref" className={labelCls}>
            {t("hrm.payroll.reference_no")}
          </label>
          <input id="pay-ref" type="text" value={reference} onChange={(e) => setReference(e.target.value)} className={inputCls} />
        </div>
        <ErrorBox lines={payErr} />
      </Modal>
    </section>
  );
};

/* ------------------------------------------------------------------ */
/* HR settings: one-click default accounts                              */
/* ------------------------------------------------------------------ */

export const HrSettingsPanel: React.FC<{ values: ApiRecord }> = ({ values }) => {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const isNew = values.id === undefined || values.id === null;

  const ensure = useMutation({
    mutationFn: async () => (await apiClient.post("/hr/settings/ensure-setup/", { company: values.company })).data as ApiRecord,
    onSuccess: (row) => {
      toast.success(t("hrm.actions.ensure_done"));
      qc.invalidateQueries({ queryKey: ["list", "/hr/settings/"] });
      navigate(`/hr/settings/${row.id}`);
    },
    onError: (e) => toast.error(errorText(e, t("common.action_failed"))),
  });

  if (!isNew || !values.company) return null;
  return (
    <section className={`${cardCls} space-y-3`}>
      <p className="text-sm text-slate-400">{t("hrm.actions.setup_defaults_help")}</p>
      <button type="button" className={btnSecondary} disabled={ensure.isPending} onClick={() => ensure.mutate()}>
        {ensure.isPending && <Loader2 className="w-4 h-4 animate-spin" />} {t("hrm.actions.setup_defaults")}
      </button>
    </section>
  );
};
