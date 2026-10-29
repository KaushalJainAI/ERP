import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Loader2, Scale, Undo2 } from "lucide-react";
import { Layout } from "@/components/Layout";
import { apiClient } from "@/api/client";
import { normaliseError, summariseError } from "@/api/endpoints";
import { LinkField } from "@/components/doc/LinkField";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Select } from "@/components/ui/Select";
import { useToast } from "@/components/ui/Toast";
import { isValidDecimalInput, sumDec } from "@/lib/decimal";
import { formatDate, formatIndianCurrency, todayISO } from "@/lib/format";
import type { LinkDef } from "@/types";
import { customerLink } from "@/resources/selling";
import { supplierLink } from "@/resources/buying";
import { inputCls, isZero, subDec } from "@/pages/reports/parts";

const BASE = "/payment-reconciliation/entries/";

interface Source {
  doctype: string;
  name: string;
  kind: string;
  posting_date: string;
  unallocated: string;
}
interface Invoice {
  doctype: string;
  name: string;
  posting_date: string;
  due_date: string | null;
  outstanding: string;
}
interface Unreconciled {
  payments: Source[];
  notes: Source[];
  invoices: Invoice[];
}
interface AllocationRow {
  source_doctype: string;
  source_name: string;
  invoice_doctype: string;
  invoice_name: string;
  amount: string;
}
interface Preview {
  allocations: AllocationRow[];
  total_allocated: string;
  unallocated_sources: string;
  unpaid_invoices: string;
}
interface Entry {
  id: number;
  name: string;
  docstatus: number;
  posting_date: string;
  party_type: string;
  party_name: string;
  total_allocated: string;
}

const btn =
  "inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium border border-slate-700 text-slate-200 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed";
const btnPrimary =
  "inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-40 disabled:cursor-not-allowed";
const th = "px-3 py-2 text-left text-xs font-semibold text-slate-400";
const thR = "px-3 py-2 text-right text-xs font-semibold text-slate-400";
const td = "px-3 py-2 text-sm text-slate-200";
const tdR = "px-3 py-2 text-sm text-slate-200 text-right tabular-nums";

const partyLink = (type: string): LinkDef => {
  const base = type === "Supplier" ? supplierLink : customerLink;
  return { ...base, valueField: "name", fill: undefined, filters: () => ({ is_active: true }) };
};

const key = (r: { doctype: string; name: string }) => `${r.doctype}|${r.name}`;
const kindKey = (kind: string) => `payrecon.kind_${kind.toLowerCase().replace(/ /g, "_")}`;
const money = (v: string) => formatIndianCurrency(v);

export const PaymentReconciliationPage: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const [partyType, setPartyType] = useState("Customer");
  const [party, setParty] = useState<string>("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [imported, setImported] = useState(false);
  const [pickedSrc, setPickedSrc] = useState<Set<string>>(new Set());
  const [pickedInv, setPickedInv] = useState<Set<string>>(new Set());
  const [rows, setRows] = useState<AllocationRow[] | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [postingDate, setPostingDate] = useState(todayISO());
  const [remarks, setRemarks] = useState("");
  const [error, setError] = useState("");
  const [undo, setUndo] = useState<Entry | null>(null);

  const ready = !!party;
  const params = { party_type: partyType, party, from_date: from, to_date: to, include_imported: imported ? "true" : "" };
  const { data, isFetching, error: loadError } = useQuery({
    queryKey: ["payrecon", "open", params],
    enabled: ready,
    queryFn: async () =>
      (
        await apiClient.get<Unreconciled>(`${BASE}unreconciled/`, {
          params: Object.fromEntries(Object.entries(params).filter(([, v]) => v !== "")),
        })
      ).data,
  });
  const entries = useQuery({
    queryKey: ["payrecon", "entries", partyType, party],
    queryFn: async () =>
      (await apiClient.get<{ results: Entry[] }>(BASE, { params: { party_type: partyType, ...(party ? { party_name: party } : {}) } }))
        .data.results,
  });

  useEffect(() => {
    setPickedSrc(new Set());
    setPickedInv(new Set());
    setRows(null);
    setPreview(null);
    setError("");
  }, [partyType, party, from, to, imported]);
  useEffect(() => setParty(""), [partyType]);

  const sources = useMemo(() => [...(data?.payments ?? []), ...(data?.notes ?? [])], [data]);
  const invoices = data?.invoices ?? [];
  const selSrcTotal = sumDec(sources.filter((s) => pickedSrc.has(key(s))).map((s) => s.unallocated), 2);
  const selInvTotal = sumDec(invoices.filter((i) => pickedInv.has(key(i))).map((i) => i.outstanding), 2);

  const refresh = () => qc.invalidateQueries({ queryKey: ["payrecon"] });
  const onError = (e: unknown) => setError(summariseError(normaliseError(e)));

  const allocate = useMutation({
    mutationFn: async () =>
      (
        await apiClient.post<Preview>(`${BASE}allocate/`, {
          party_type: partyType,
          party,
          payments: sources.filter((s) => pickedSrc.has(key(s))).map((s) => ({ doctype: s.doctype, name: s.name })),
          invoices: invoices.filter((i) => pickedInv.has(key(i))).map((i) => ({ doctype: i.doctype, name: i.name })),
        })
      ).data,
    onSuccess: (p) => {
      setError("");
      setPreview(p);
      setRows(p.allocations);
    },
    onError,
  });

  const reconcile = useMutation({
    mutationFn: async () =>
      (
        await apiClient.post<Entry>(`${BASE}reconcile/`, {
          party_type: partyType,
          party,
          posting_date: postingDate || null,
          remarks,
          allocations: rows,
        })
      ).data,
    onSuccess: (entry) => {
      toast.success(t("payrecon.reconciled", { name: entry.name }));
      setRows(null);
      setPreview(null);
      setPickedSrc(new Set());
      setPickedInv(new Set());
      setRemarks("");
      setError("");
      refresh();
    },
    onError,
  });

  const unreconcile = useMutation({
    mutationFn: async (entry: Entry) => (await apiClient.post<Entry>(`${BASE}${entry.id}/unreconcile/`)).data,
    onSuccess: (entry) => {
      toast.success(t("payrecon.unreconciled_toast", { name: entry.name }));
      setUndo(null);
      refresh();
    },
    onError: (e) => {
      setUndo(null);
      onError(e);
    },
  });

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, k: string) => {
    const next = new Set(set);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    setter(next);
  };

  const setAmount = (idx: number, value: string) => {
    if (!isValidDecimalInput(value, 2)) return;
    setRows((cur) => (cur ? cur.map((r, i) => (i === idx ? { ...r, amount: value } : r)) : cur));
  };
  const rowsTotal = rows ? sumDec(rows.map((r) => r.amount || "0"), 2) : "0.00";
  const canReconcile = !!rows && rows.length > 0 && rows.every((r) => r.amount && !isZero(r.amount));
  const loadMsg = loadError ? summariseError(normaliseError(loadError)) : "";

  return (
    <Layout>
      <div className="max-w-6xl mx-auto space-y-5">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
          <div>
            <h1 className="text-xl font-semibold text-slate-100 flex items-center gap-2">
              <Scale className="w-5 h-5 text-indigo-400" />
              {t("payrecon.title")}
              {isFetching && <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}
            </h1>
            <p className="text-sm text-slate-400 mt-1">{t("payrecon.subtitle")}</p>
          </div>
          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1">
              <label htmlFor="pr-type" className="block text-xs font-medium text-slate-400">
                {t("payrecon.party_type")}
              </label>
              <Select
                id="pr-type"
                value={partyType}
                onChange={setPartyType}
                className="w-40"
                options={[
                  { value: "Customer", label: t("payrecon.customer") },
                  { value: "Supplier", label: t("payrecon.supplier") },
                ]}
              />
            </div>
            <div className="space-y-1 w-72">
              <label htmlFor="pr-party" className="block text-xs font-medium text-slate-400">
                {t("payrecon.party")}
              </label>
              <LinkField
                id="pr-party"
                link={partyLink(partyType)}
                cacheKey={`payrecon.party.${partyType}`}
                value={party || null}
                values={{}}
                parentValues={{}}
                placeholder={t("payrecon.choose_party")}
                onSelect={(v) => setParty(v ? String(v) : "")}
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="pr-from" className="block text-xs font-medium text-slate-400">
                {t("payrecon.from_date")}
              </label>
              <input id="pr-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={inputCls} />
            </div>
            <div className="space-y-1">
              <label htmlFor="pr-to" className="block text-xs font-medium text-slate-400">
                {t("payrecon.to_date")}
              </label>
              <input id="pr-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} className={inputCls} />
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-300 pb-2">
              <input type="checkbox" checked={imported} onChange={(e) => setImported(e.target.checked)} />
              {t("payrecon.include_imported")}
            </label>
          </div>
          {(error || loadMsg) && (
            <div role="alert" className="text-sm text-rose-300 bg-rose-950/50 border border-rose-900 rounded-lg px-3 py-2">
              {error || loadMsg}
            </div>
          )}
        </div>

        {ready && data && (
          <>
            <div className="grid md:grid-cols-2 gap-5">
              <section className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <h2 className="px-4 py-3 border-b border-slate-800 text-sm font-semibold text-slate-100">
                  {t("payrecon.payments_title")}
                </h2>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-slate-800">
                        <th className={th} />
                        <th className={th}>{t("payrecon.col_type")}</th>
                        <th className={th}>{t("payrecon.col_number")}</th>
                        <th className={th}>{t("payrecon.col_date")}</th>
                        <th className={thR}>{t("payrecon.col_unallocated")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sources.map((s) => (
                        <tr key={key(s)} className="border-b border-slate-800/60">
                          <td className={td}>
                            <input
                              type="checkbox"
                              aria-label={s.name}
                              checked={pickedSrc.has(key(s))}
                              onChange={() => toggle(pickedSrc, setPickedSrc, key(s))}
                            />
                          </td>
                          <td className={td}>{t(kindKey(s.kind), s.kind)}</td>
                          <td className={td}>{s.name}</td>
                          <td className={td}>{formatDate(s.posting_date)}</td>
                          <td className={tdR}>{money(s.unallocated)}</td>
                        </tr>
                      ))}
                      {sources.length === 0 && (
                        <tr>
                          <td colSpan={5} className="px-3 py-6 text-center text-sm text-slate-500">
                            {t("payrecon.none_payments")}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <div className="px-4 py-2 text-xs text-slate-400">
                  {t("payrecon.sel_payments", { amount: money(selSrcTotal) })}
                </div>
              </section>

              <section className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <h2 className="px-4 py-3 border-b border-slate-800 text-sm font-semibold text-slate-100">
                  {t("payrecon.invoices_title")}
                </h2>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-slate-800">
                        <th className={th} />
                        <th className={th}>{t("payrecon.col_number")}</th>
                        <th className={th}>{t("payrecon.col_date")}</th>
                        <th className={th}>{t("payrecon.col_due")}</th>
                        <th className={thR}>{t("payrecon.col_outstanding")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {invoices.map((i) => (
                        <tr key={key(i)} className="border-b border-slate-800/60">
                          <td className={td}>
                            <input
                              type="checkbox"
                              aria-label={i.name}
                              checked={pickedInv.has(key(i))}
                              onChange={() => toggle(pickedInv, setPickedInv, key(i))}
                            />
                          </td>
                          <td className={td}>{i.name}</td>
                          <td className={td}>{formatDate(i.posting_date)}</td>
                          <td className={td}>{i.due_date ? formatDate(i.due_date) : ""}</td>
                          <td className={tdR}>{money(i.outstanding)}</td>
                        </tr>
                      ))}
                      {invoices.length === 0 && (
                        <tr>
                          <td colSpan={5} className="px-3 py-6 text-center text-sm text-slate-500">
                            {t("payrecon.none_invoices")}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <div className="px-4 py-2 text-xs text-slate-400">
                  {t("payrecon.sel_invoices", { amount: money(selInvTotal) })}
                </div>
              </section>
            </div>

            <div className="flex items-center gap-3">
              <button
                type="button"
                className={btnPrimary}
                disabled={pickedSrc.size === 0 || pickedInv.size === 0 || allocate.isPending}
                onClick={() => allocate.mutate()}
              >
                {allocate.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
                {t("payrecon.allocate")}
              </button>
              {!rows && <span className="text-xs text-slate-500">{t("payrecon.allocate_hint")}</span>}
            </div>

            {rows && preview && (
              <section className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <h2 className="px-4 py-3 border-b border-slate-800 text-sm font-semibold text-slate-100">
                  {t("payrecon.allocations_title")}
                </h2>
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-slate-800">
                      <th className={th}>{t("payrecon.col_source")}</th>
                      <th className={th}>{t("payrecon.col_invoice")}</th>
                      <th className={thR}>{t("payrecon.col_allocated")}</th>
                      <th className={th} />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, idx) => (
                      <tr key={`${r.source_name}|${r.invoice_name}`} className="border-b border-slate-800/60">
                        <td className={td}>{r.source_name}</td>
                        <td className={td}>{r.invoice_name}</td>
                        <td className={tdR}>
                          <input
                            aria-label={`${r.source_name} ${r.invoice_name}`}
                            inputMode="decimal"
                            value={r.amount}
                            onChange={(e) => setAmount(idx, e.target.value)}
                            className={`${inputCls} w-32 text-right tabular-nums`}
                          />
                        </td>
                        <td className={td}>
                          <button
                            type="button"
                            className="text-xs text-rose-300 hover:underline"
                            onClick={() => setRows(rows.filter((_, i) => i !== idx))}
                          >
                            {t("payrecon.remove")}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="px-4 py-3 border-t border-slate-800 grid sm:grid-cols-3 gap-2 text-xs text-slate-400">
                  <div>
                    {t("payrecon.total_allocated")}: <span className="text-slate-100 tabular-nums">{money(rowsTotal)}</span>
                  </div>
                  <div>
                    {t("payrecon.unused_payments")}:{" "}
                    <span className="text-slate-100 tabular-nums">
                      {money(subDec(sumDec([preview.total_allocated, preview.unallocated_sources], 2), rowsTotal))}
                    </span>
                  </div>
                  <div>
                    {t("payrecon.unpaid_invoices")}:{" "}
                    <span className="text-slate-100 tabular-nums">
                      {money(subDec(sumDec([preview.total_allocated, preview.unpaid_invoices], 2), rowsTotal))}
                    </span>
                  </div>
                </div>
                <div className="px-4 py-3 border-t border-slate-800 flex flex-wrap items-end gap-4">
                  <div className="space-y-1">
                    <label htmlFor="pr-date" className="block text-xs font-medium text-slate-400">
                      {t("payrecon.posting_date")}
                    </label>
                    <input
                      id="pr-date"
                      type="date"
                      value={postingDate}
                      onChange={(e) => setPostingDate(e.target.value)}
                      className={inputCls}
                    />
                  </div>
                  <div className="space-y-1 flex-1 min-w-[12rem]">
                    <label htmlFor="pr-remarks" className="block text-xs font-medium text-slate-400">
                      {t("payrecon.remarks")}
                    </label>
                    <input
                      id="pr-remarks"
                      value={remarks}
                      onChange={(e) => setRemarks(e.target.value)}
                      className={`${inputCls} w-full`}
                    />
                  </div>
                  <button
                    type="button"
                    className={btnPrimary}
                    disabled={!canReconcile || reconcile.isPending}
                    onClick={() => reconcile.mutate()}
                  >
                    {reconcile.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
                    {t("payrecon.reconcile")}
                  </button>
                </div>
              </section>
            )}
          </>
        )}

        <section className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <h2 className="px-4 py-3 border-b border-slate-800 text-sm font-semibold text-slate-100">
            {t("payrecon.history_title")}
          </h2>
          <table className="w-full">
            <thead>
              <tr className="border-b border-slate-800">
                <th className={th}>{t("payrecon.col_entry")}</th>
                <th className={th}>{t("payrecon.col_date")}</th>
                <th className={th}>{t("payrecon.party")}</th>
                <th className={thR}>{t("payrecon.col_allocated")}</th>
                <th className={th}>{t("payrecon.col_status")}</th>
                <th className={th} />
              </tr>
            </thead>
            <tbody>
              {(entries.data ?? []).map((e) => (
                <tr key={e.id} className="border-b border-slate-800/60">
                  <td className={td}>{e.name}</td>
                  <td className={td}>{formatDate(e.posting_date)}</td>
                  <td className={td}>{e.party_name}</td>
                  <td className={tdR}>{money(e.total_allocated)}</td>
                  <td className={td}>
                    {e.docstatus === 1 ? t("payrecon.status_submitted") : t("payrecon.status_cancelled")}
                  </td>
                  <td className={td}>
                    {e.docstatus === 1 && (
                      <button type="button" className={btn} onClick={() => setUndo(e)}>
                        <Undo2 className="w-4 h-4" />
                        {t("payrecon.unreconcile")}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {(entries.data ?? []).length === 0 && (
                <tr>
                  <td colSpan={6} className="px-3 py-6 text-center text-sm text-slate-500">
                    {t("payrecon.no_entries")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      </div>

      <ConfirmDialog
        open={!!undo}
        title={t("payrecon.unreconcile_title", { name: undo?.name ?? "" })}
        message={t("payrecon.unreconcile_message")}
        confirmLabel={t("payrecon.unreconcile")}
        tone="danger"
        busy={unreconcile.isPending}
        onConfirm={() => undo && unreconcile.mutate(undo)}
        onClose={() => setUndo(null)}
      />
    </Layout>
  );
};
