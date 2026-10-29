import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { KeyRound, Loader2, PowerOff } from "lucide-react";
import { apiClient } from "@/api/client";
import { useAuth } from "@/hooks/useAuth";
import { useToast } from "@/components/ui/Toast";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { formatDate, formatIndianCurrency, formatIndianNumber, todayISO } from "@/lib/format";
import { optCell } from "@/resources/hrOptions";
import type { ApiRecord } from "@/types";
import { btnSecondary, cardCls, ErrorBox, errorLines, errorText, inputCls, labelCls, Modal } from "./hrUi";

const monthStart = () => `${todayISO().slice(0, 8)}01`;

const Stat: React.FC<{ label: string; value: string; tone?: "warn" | "good" }> = ({ label, value, tone }) => (
  <div className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2.5">
    <dt className="text-[11px] uppercase tracking-wider text-slate-500">{label}</dt>
    <dd
      className={`mt-0.5 text-lg font-semibold tabular-nums ${
        tone === "warn" ? "text-amber-400" : tone === "good" ? "text-emerald-400" : "text-slate-100"
      }`}
    >
      {value}
    </dd>
  </div>
);

/** Attendance / earnings / payments / balances of one employee for a period. */
const EmployeeSummaryPanel: React.FC<{ employeeId: number | string }> = ({ employeeId }) => {
  const { t } = useTranslation();
  const [from, setFrom] = useState(monthStart);
  const [to, setTo] = useState(todayISO);
  const valid = !!from && !!to && to >= from;

  const q = useQuery({
    queryKey: ["hr", "employee-summary", employeeId, from, to],
    queryFn: async () => (await apiClient.get(`/hr/employees/${employeeId}/summary/`, { params: { from, to } })).data as ApiRecord,
    enabled: valid,
    retry: false,
  });
  const s = q.data;
  const money = formatIndianCurrency;
  const num = formatIndianNumber;

  return (
    <section className={`${cardCls} space-y-4`}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 className="text-base font-semibold text-slate-100">{t("hrm.summary.title")}</h2>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor="sum-from" className={labelCls}>
              {t("hrm.summary.from")}
            </label>
            <input id="sum-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={inputCls} />
          </div>
          <div>
            <label htmlFor="sum-to" className={labelCls}>
              {t("hrm.summary.to")}
            </label>
            <input id="sum-to" type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} className={inputCls} />
          </div>
        </div>
      </div>

      {q.isLoading && (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Loader2 className="w-4 h-4 animate-spin" /> {t("common.loading")}
        </div>
      )}
      {q.error && <ErrorBox lines={errorLines(q.error)} />}

      {s && (
        <>
          <dl className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label={t("hrm.summary.days_present")} value={num(s.days_present)} />
            <Stat label={t("hrm.summary.days_half")} value={num(s.days_half_day)} />
            <Stat label={t("hrm.summary.days_absent")} value={num(s.days_absent)} />
            <Stat label={t("hrm.summary.days_leave")} value={num(s.days_paid_leave)} />
            <Stat label={t("hrm.summary.ot_hours")} value={num(s.ot_hours)} />
            <Stat label={t("hrm.summary.gross")} value={money(s.gross_earned)} />
            <Stat label={t("hrm.summary.deductions")} value={money(s.total_deductions)} />
            <Stat label={t("hrm.summary.net")} value={money(s.net_pay)} />
            <Stat label={t("hrm.summary.paid")} value={money(s.total_paid)} tone="good" />
            <Stat label={t("hrm.summary.payable")} value={money(s.wages_payable)} tone={Number(s.wages_payable) > 0 ? "warn" : undefined} />
            <Stat label={t("hrm.summary.advance")} value={money(s.advance_outstanding)} tone={Number(s.advance_outstanding) > 0 ? "warn" : undefined} />
          </dl>

          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">{t("hrm.summary.recent_slips")}</h3>
              {(s.recent_slips ?? []).length === 0 ? (
                <p className="text-sm text-slate-500">{t("hrm.summary.none")}</p>
              ) : (
                <ul className="divide-y divide-slate-800 text-sm">
                  {(s.recent_slips as ApiRecord[]).map((r) => (
                    <li key={r.name} className="py-2 flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <Link to={`/hr/salary-slips?search=${encodeURIComponent(r.name)}`} className="text-indigo-400 hover:underline">
                          {r.name}
                        </Link>
                        <p className="text-xs text-slate-500">
                          {t("hrm.summary.slip_period", { from: formatDate(r.start_date), to: formatDate(r.end_date) })}
                        </p>
                      </div>
                      <span className="tabular-nums text-slate-200">{money(r.net_pay)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">{t("hrm.summary.recent_payments")}</h3>
              {(s.recent_payments ?? []).length === 0 ? (
                <p className="text-sm text-slate-500">{t("hrm.summary.none")}</p>
              ) : (
                <ul className="divide-y divide-slate-800 text-sm">
                  {(s.recent_payments as ApiRecord[]).map((r) => (
                    <li key={r.name} className="py-2 flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <Link to={`/hr/employee-payments?search=${encodeURIComponent(r.name)}`} className="text-indigo-400 hover:underline">
                          {r.name}
                        </Link>
                        <p className="text-xs text-slate-500">
                          {formatDate(r.posting_date)} - {optCell(r.mode)}
                        </p>
                      </div>
                      <span className="tabular-nums text-slate-200">{money(r.amount)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </>
      )}
    </section>
  );
};

/** Set / reset the portal PIN or switch the portal off (HR Manager). */
const EmployeePortalPanel: React.FC<{ employee: ApiRecord; dirty: boolean }> = ({ employee, dirty }) => {
  const { t } = useTranslation();
  const toast = useToast();
  const [pinOpen, setPinOpen] = useState(false);
  const [pin, setPin] = useState("");
  const [pinErr, setPinErr] = useState<string[]>([]);
  const [disableOpen, setDisableOpen] = useState(false);

  const setPinMut = useMutation({
    mutationFn: () => apiClient.post(`/hr/employees/${employee.id}/set-portal-pin/`, { pin }),
    onSuccess: () => {
      toast.success(t("hrm.portal.pin_done"));
      setPinOpen(false);
      setPin("");
      setPinErr([]);
    },
    onError: (e) => setPinErr(errorLines(e).length ? errorLines(e) : [errorText(e, t("common.action_failed"))]),
  });

  const disableMut = useMutation({
    mutationFn: () => apiClient.post(`/hr/employees/${employee.id}/disable-portal/`),
    onSuccess: () => {
      toast.success(t("hrm.portal.disabled_done"));
      setDisableOpen(false);
    },
    onError: (e) => {
      setDisableOpen(false);
      toast.error(errorText(e, t("common.action_failed")));
    },
  });

  const submitPin = () => {
    if (!/^\d{4,6}$/.test(pin)) {
      setPinErr([t("hrm.portal.pin_invalid")]);
      return;
    }
    setPinErr([]);
    setPinMut.mutate();
  };

  const hasMobile = !!employee.mobile_no;

  return (
    <section className={`${cardCls} space-y-3`}>
      <div>
        <h2 className="text-base font-semibold text-slate-100">{t("hrm.portal.title")}</h2>
        <p className="text-sm text-slate-400 mt-1">{hasMobile ? t("hrm.portal.help") : t("hrm.portal.no_mobile")}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={btnSecondary} disabled={!hasMobile || dirty} onClick={() => setPinOpen(true)}>
          <KeyRound className="w-4 h-4" /> {t("hrm.portal.set_pin")}
        </button>
        <button type="button" className={btnSecondary} disabled={dirty} onClick={() => setDisableOpen(true)}>
          <PowerOff className="w-4 h-4" /> {t("hrm.portal.disable")}
        </button>
      </div>
      {dirty && <p className="text-xs text-amber-400">{t("hrm.actions.save_first")}</p>}

      <Modal
        open={pinOpen}
        title={t("hrm.portal.set_pin")}
        confirmLabel={t("common.save_short")}
        busy={setPinMut.isPending}
        onConfirm={submitPin}
        onClose={() => setPinOpen(false)}
      >
        <div>
          <label htmlFor="portal-pin" className={labelCls}>
            {t("hrm.portal.pin")}
          </label>
          <input
            id="portal-pin"
            type="password"
            inputMode="numeric"
            autoComplete="new-password"
            maxLength={6}
            value={pin}
            onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
            className={inputCls}
          />
          <p className="text-[11px] text-slate-500 mt-1">{t("hrm.portal.pin_help")}</p>
        </div>
        <ErrorBox lines={pinErr} />
      </Modal>

      <ConfirmDialog
        open={disableOpen}
        title={t("hrm.portal.confirm_disable")}
        message={t("hrm.portal.confirm_disable_msg")}
        confirmLabel={t("hrm.portal.disable")}
        tone="danger"
        busy={disableMut.isPending}
        onConfirm={() => disableMut.mutate()}
        onClose={() => setDisableOpen(false)}
      />
    </section>
  );
};

/** Panels under the Employee form (nothing on a not-yet-saved employee). */
export const EmployeePanels: React.FC<{ employee: ApiRecord; dirty: boolean }> = ({ employee, dirty }) => {
  const { user } = useAuth();
  if (employee.id === undefined || employee.id === null) return null;
  const mayManagePortal = !!user && (!!user.is_superuser || user.roles.includes("HR Manager") || user.roles.includes("System Manager"));
  return (
    <div className="space-y-5">
      <EmployeeSummaryPanel employeeId={employee.id} />
      {mayManagePortal && <EmployeePortalPanel employee={employee} dirty={dirty} />}
    </div>
  );
};
