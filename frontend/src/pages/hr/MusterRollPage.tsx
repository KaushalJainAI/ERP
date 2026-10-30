import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CheckCheck, ChevronLeft, ChevronRight, Loader2, Lock, MessageSquare, Save, Search } from "lucide-react";
import { Layout } from "@/components/Layout";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Select } from "@/components/ui/Select";
import { useToast } from "@/components/ui/Toast";
import { apiClient } from "@/api/client";
import { fetchCompanies } from "@/api/lookups";
import { normaliseError } from "@/api/endpoints";
import { formatDate, todayISO } from "@/lib/format";
import { optKey } from "@/resources/hrOptions";
import { btnPrimary, btnSecondary, ErrorBox, inputCls } from "@/components/hr/hrUi";
import type { ApiRecord } from "@/types";

const STATUSES = [
  { value: "Present", on: "bg-emerald-600 border-emerald-600 text-white", off: "text-emerald-400 border-emerald-800/70 hover:bg-emerald-950/50" },
  { value: "Absent", on: "bg-rose-600 border-rose-600 text-white", off: "text-rose-400 border-rose-800/70 hover:bg-rose-950/50" },
  { value: "Half Day", on: "bg-amber-600 border-amber-600 text-white", off: "text-amber-400 border-amber-800/70 hover:bg-amber-950/50" },
  { value: "Paid Leave", on: "bg-sky-600 border-sky-600 text-white", off: "text-sky-400 border-sky-800/70 hover:bg-sky-950/50" },
];

interface Edit {
  status?: string;
  ot?: string;
  remarks?: string;
}

const shiftDay = (iso: string, delta: number) => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + delta);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
};

const serverOt = (row: ApiRecord) => String(Number(row.attendance?.ot_hours ?? 0));

export const MusterRollPage: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const [date, setDate] = useState(todayISO);
  const [company, setCompany] = useState("");
  const [search, setSearch] = useState("");
  const [edits, setEdits] = useState<Record<number, Edit>>({});
  const [openRemarks, setOpenRemarks] = useState<Set<number>>(new Set());
  const [pendingDate, setPendingDate] = useState<string | null>(null);
  const [errorLines, setErrorLines] = useState<string[]>([]);
  const [rowErrors, setRowErrors] = useState<Record<number, string[]>>({});

  const companies = useQuery({ queryKey: ["hr", "companies"], queryFn: () => fetchCompanies("") });

  const roll = useQuery({
    queryKey: ["hr", "muster-roll", date, company],
    queryFn: async () =>
      (await apiClient.get("/hr/attendance/muster-roll/", { params: { date, company: company || undefined } })).data as {
        date: string;
        rows: ApiRecord[];
      },
  });
  const rows = roll.data?.rows ?? [];

  // Fresh server data replaces local edits.
  useEffect(() => {
    setEdits({});
    setRowErrors({});
    setErrorLines([]);
  }, [roll.dataUpdatedAt]);

  const view = (row: ApiRecord) => {
    const e = edits[row.employee] ?? {};
    return {
      status: (e.status ?? row.attendance?.status ?? null) as string | null,
      ot: e.ot ?? serverOt(row),
      remarks: e.remarks ?? (row.attendance?.remarks ?? ""),
    };
  };

  const patch = (row: ApiRecord, change: Edit) =>
    setEdits((prev) => ({ ...prev, [row.employee]: { ...prev[row.employee], ...change } }));

  const setStatus = (row: ApiRecord, status: string) =>
    patch(row, status === "Absent" ? { status, ot: "0" } : { status });

  const changed = useMemo(
    () =>
      rows.filter((r) => {
        if (r.locked || !edits[r.employee]) return false;
        const v = view(r);
        if (!v.status) return false;
        const s = r.attendance;
        return !s || s.status !== v.status || Number(s.ot_hours) !== Number(v.ot || 0) || (s.remarks ?? "") !== v.remarks;
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, edits]
  );
  const dirty = changed.length > 0;

  const markAll = () =>
    setEdits((prev) => {
      const next = { ...prev };
      rows.forEach((r) => {
        if (r.locked) return;
        const cur = prev[r.employee]?.status ?? r.attendance?.status;
        if (!cur) next[r.employee] = { ...prev[r.employee], status: "Present" };
      });
      return next;
    });

  const save = useMutation({
    mutationFn: async () => {
      const sent = changed.map((r) => {
        const v = view(r);
        return { employee: r.employee, status: v.status, ot_hours: v.ot === "" ? "0" : v.ot, remarks: v.remarks };
      });
      return { sent, res: await apiClient.post("/hr/attendance/muster-roll/", { date, company: company ? Number(company) : undefined, rows: sent }) };
    },
    onSuccess: () => {
      toast.success(t("hrm.muster.saved"));
      qc.invalidateQueries({ queryKey: ["hr", "muster-roll"] });
    },
    onError: (e: any) => {
      const n = normaliseError(e, ["rows"]);
      const sentIds = changed.map((r) => r.employee);
      const perRow: Record<number, string[]> = {};
      Object.entries(n.rows.rows ?? {}).forEach(([idx, cols]) => {
        const emp = sentIds[Number(idx)];
        if (emp !== undefined) perRow[emp] = Object.values(cols).flat();
      });
      setRowErrors(perRow);
      setErrorLines([...n.general, ...Object.entries(n.fields).flatMap(([k, m]) => m.map((x) => `${k}: ${x}`)), ...n.tables.rows ?? []]);
      if (n.forbidden) setErrorLines([t("common.forbidden")]);
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
  });

  const goDate = (next: string) => {
    if (!next || next === date) return;
    if (dirty) setPendingDate(next);
    else setDate(next);
  };

  const q = search.trim().toLowerCase();
  const shown = q
    ? rows.filter((r) => `${r.employee_id} ${r.employee_name} ${r.department ?? ""}`.toLowerCase().includes(q))
    : rows;

  const counts = useMemo(() => {
    const c: Record<string, number> = { Present: 0, Absent: 0, "Half Day": 0, "Paid Leave": 0, none: 0 };
    rows.forEach((r) => {
      const s = view(r).status;
      c[s ?? "none"] += 1;
    });
    return c;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, edits]);

  const companyOptions = [{ value: "", label: t("hrm.muster.all_companies") }, ...(companies.data ?? []).map((c) => ({ value: String(c.id), label: String(c.name) }))];

  return (
    <Layout>
      <div className="max-w-4xl mx-auto space-y-4 pb-28">
        <h1 className="text-xl font-semibold text-slate-100">{t("hrm.res.muster_roll")}</h1>

        {/* date bar */}
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={`${btnSecondary} h-11 w-11 !px-0`} onClick={() => goDate(shiftDay(date, -1))} aria-label={t("hrm.muster.prev_day")}>
            <ChevronLeft className="w-5 h-5 rtl:rotate-180" />
          </button>
          <input
            type="date"
            value={date}
            onChange={(e) => goDate(e.target.value)}
            aria-label={t("hrm.muster.date")}
            className={`${inputCls} !w-auto h-11 text-base`}
          />
          <button type="button" className={`${btnSecondary} h-11 w-11 !px-0`} onClick={() => goDate(shiftDay(date, 1))} aria-label={t("hrm.muster.next_day")}>
            <ChevronRight className="w-5 h-5 rtl:rotate-180" />
          </button>
          <button type="button" className={`${btnSecondary} h-11`} onClick={() => goDate(todayISO())} disabled={date === todayISO()}>
            {t("hrm.muster.today")}
          </button>
          {(companies.data?.length ?? 0) > 1 && (
            <Select value={company} options={companyOptions} onChange={(v) => (dirty ? setErrorLines([t("hrm.muster.discard_msg")]) : setCompany(v))} aria-label={t("hrm.muster.company")} className="min-w-[10rem]" />
          )}
        </div>
        <p className="text-sm text-slate-400">{formatDate(date)}</p>

        {/* counts + bulk */}
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {STATUSES.map((s) => (
            <span key={s.value} className={`px-2.5 py-1 rounded-full border ${s.off.split(" ").slice(0, 2).join(" ")}`}>
              {t(optKey(s.value))}: {counts[s.value]}
            </span>
          ))}
          <span className="px-2.5 py-1 rounded-full border border-slate-700 text-slate-400">
            {t("hrm.muster.count_unmarked")}: {counts.none}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className={`${btnSecondary} h-11`} onClick={markAll} disabled={roll.isLoading || counts.none === 0}>
            <CheckCheck className="w-4 h-4" /> {t("hrm.muster.mark_all")}
          </button>
          <div className="relative flex-1 min-w-[12rem]">
            <Search className="w-4 h-4 absolute start-3 top-3.5 text-slate-500" />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("hrm.muster.search")}
              aria-label={t("hrm.muster.search")}
              className={`${inputCls} ps-9 h-11`}
            />
          </div>
        </div>
        <p className="text-[11px] text-slate-500 -mt-2">{t("hrm.muster.mark_all_help")}</p>

        <ErrorBox lines={errorLines} />

        {roll.isLoading && (
          <div className="flex items-center gap-2 text-sm text-slate-400">
            <Loader2 className="w-4 h-4 animate-spin" /> {t("common.loading")}
          </div>
        )}
        {roll.error && <ErrorBox lines={[t("hrm.muster.load_failed")]} />}
        {roll.data && rows.length === 0 && <p className="text-sm text-slate-400">{t("hrm.muster.no_employees")}</p>}

        <ul className="space-y-3">
          {shown.map((r) => {
            const v = view(r);
            const locked = !!r.locked;
            const remarksOpen = openRemarks.has(r.employee) || !!v.remarks;
            const errs = rowErrors[r.employee];
            return (
              <li key={r.employee} className={`rounded-xl border bg-slate-900 p-3.5 space-y-3 ${errs ? "border-rose-600" : "border-slate-800"}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-medium text-slate-100 truncate">{r.employee_name}</p>
                    <p className="text-xs text-slate-500 truncate">
                      {r.employee_id}
                      {r.department ? ` - ${r.department}` : ""}
                    </p>
                  </div>
                  <div className="flex flex-wrap justify-end gap-1.5">
                    {r.is_holiday && (
                      <span className="text-[11px] px-2 py-0.5 rounded-full border border-sky-800/60 bg-sky-950/40 text-sky-300" title={r.holiday_description ?? undefined}>
                        {t("hrm.muster.holiday")}
                        {r.holiday_description ? `: ${r.holiday_description}` : ""}
                      </span>
                    )}
                    {locked && (
                      <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border border-slate-700 text-slate-400">
                        <Lock className="w-3 h-3" /> {t("hrm.muster.locked")}
                      </span>
                    )}
                  </div>
                </div>

                <div className="grid grid-cols-4 gap-1.5" role="radiogroup" aria-label={t("hrm.fields.attendance_status")}>
                  {STATUSES.map((s) => {
                    const selected = v.status === s.value;
                    return (
                      <button
                        key={s.value}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        disabled={locked}
                        onClick={() => setStatus(r, s.value)}
                        className={`min-h-[3rem] rounded-lg border px-1 py-1.5 text-xs sm:text-sm font-medium leading-tight transition-colors disabled:cursor-not-allowed ${
                          selected ? s.on : locked ? "border-slate-800 text-slate-600" : s.off
                        } ${locked && !selected ? "opacity-50" : ""}`}
                      >
                        {t(optKey(s.value))}
                      </button>
                    );
                  })}
                </div>

                {!v.status && !locked && <p className="text-xs text-slate-500">{t("hrm.muster.unmarked")}</p>}

                {locked ? (
                  <p className="text-xs text-amber-400 flex gap-1.5">
                    <Lock className="w-3.5 h-3.5 shrink-0 mt-0.5" /> {t("hrm.muster.locked_why")}
                  </p>
                ) : (
                  <div className="flex flex-wrap items-center gap-3">
                    <label className="flex items-center gap-2 text-xs text-slate-400">
                      {t("hrm.muster.ot")}
                      <input
                        type="number"
                        inputMode="decimal"
                        min={0}
                        max={24}
                        step={0.5}
                        value={v.ot}
                        disabled={v.status === "Absent" || !v.status}
                        onChange={(e) => patch(r, { ot: e.target.value })}
                        className={`${inputCls} !w-24 h-11 text-base`}
                      />
                    </label>
                    {!remarksOpen && (
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 text-xs text-indigo-400 hover:underline h-11"
                        onClick={() => setOpenRemarks((p) => new Set(p).add(r.employee))}
                      >
                        <MessageSquare className="w-3.5 h-3.5" /> {t("hrm.muster.remarks_add")}
                      </button>
                    )}
                    {remarksOpen && (
                      <input
                        type="text"
                        maxLength={240}
                        value={v.remarks}
                        onChange={(e) => patch(r, { remarks: e.target.value })}
                        placeholder={t("hrm.muster.remarks")}
                        aria-label={t("hrm.muster.remarks")}
                        className={`${inputCls} flex-1 min-w-[10rem] h-11`}
                      />
                    )}
                  </div>
                )}
                {locked && r.attendance && (
                  <p className="text-xs text-slate-500">
                    {t("hrm.muster.ot")}: {Number(r.attendance.ot_hours)}
                    {r.attendance.remarks ? ` - ${r.attendance.remarks}` : ""}
                  </p>
                )}

                {errs && (
                  <p className="text-xs text-rose-400">
                    {t("hrm.muster.row_error")} {errs.join(" ")}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      </div>

      {/* sticky save bar */}
      <div className="fixed-save-bar fixed inset-x-0 bottom-[calc(4rem_+_env(safe-area-inset-bottom))] z-30 flex justify-end border-t border-slate-800 bg-slate-900/95 px-4 py-3 backdrop-blur md:bottom-0 md:ps-64">
        <button type="button" className={`${btnPrimary} h-12 px-6 text-base w-full sm:w-auto`} disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {dirty ? t("hrm.muster.save_n", { count: changed.length }) : t("hrm.muster.save")}
        </button>
      </div>

      <ConfirmDialog
        open={pendingDate !== null}
        title={t("hrm.muster.discard_title")}
        message={t("hrm.muster.discard_msg")}
        confirmLabel={t("hrm.muster.discard")}
        tone="danger"
        onConfirm={() => {
          if (pendingDate) setDate(pendingDate);
          setPendingDate(null);
        }}
        onClose={() => setPendingDate(null)}
      />
    </Layout>
  );
};
