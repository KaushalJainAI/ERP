import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, Download, FileOutput, Loader2, RefreshCw } from "lucide-react";
import { apiClient } from "@/api/client";
import { normaliseError } from "@/api/endpoints";
import { formatIndianCurrency } from "@/lib/format";
import { CompanySelect, DateInput, ReportShell, inputCls, useCompany } from "@/pages/reports/parts";

const BASE = "/tally-export";

interface VoucherRow {
  doctype: string;
  name: string;
  posting_date: string;
  tally_voucher_type: string;
  action: "Create" | "Cancel";
  debit: string;
  credit: string;
  balanced: boolean;
  unmapped: boolean;
  previously_exported: boolean;
}
interface UnmappedAccount {
  account: number;
  account_name: string;
  suggested_ledger: string;
  suggested_group: string;
  vouchers: number;
}
interface UnmappedParty {
  account: number;
  party_type: string;
  party: string;
  suggested_ledger: string;
  suggested_group: string;
  vouchers: number;
}
interface UnmappedItem {
  item: number;
  item_code: string;
  item_name: string;
  suggested_item: string;
  suggested_unit: string;
  vouchers: number;
}
interface Preflight {
  vouchers: VoucherRow[];
  voucher_count: number;
  cancel_count: number;
  unmapped: { accounts: UnmappedAccount[]; parties: UnmappedParty[]; items: UnmappedItem[] };
  unmapped_count: number;
  unbalanced_count: number;
  skipped: unknown[];
  can_export: boolean;
}
interface Pending {
  new: number;
  cancelled: number;
  total: number;
}
interface Batch {
  id: number;
  created_at: string;
  created_by_name: string | null;
  from_date: string | null;
  to_date: string | null;
  voucher_count: number;
  cancel_count: number;
  ledger_master_count: number;
  item_master_count: number;
  has_masters: boolean;
  kind: string;
}
interface Settings {
  id: number;
  tally_company_name: string;
  voucher_type_overrides: Record<string, string>;
}

/** Voucher keys whose Tally type name a company may override (backend constants.DEFAULT_VOUCHER_TYPES). */
const VOUCHER_KEYS: [string, string][] = [
  ["selling.SalesInvoice", "Sales"],
  ["selling.SalesInvoice:return", "Credit Note"],
  ["buying.PurchaseInvoice", "Purchase"],
  ["buying.PurchaseInvoice:return", "Debit Note"],
  ["accounts.PaymentEntry:Receive", "Receipt"],
  ["accounts.PaymentEntry:Pay", "Payment"],
  ["accounts.PaymentEntry:Internal Transfer", "Contra"],
  ["accounts.JournalEntry", "Journal"],
  ["stock.StockEntry", "Stock Journal"],
];

const money = (v: string) => <span className="tabular-nums">{formatIndianCurrency(v)}</span>;

const btn =
  "inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white font-medium px-4 py-2 rounded-lg text-sm disabled:opacity-50";
const btnGhost =
  "inline-flex items-center gap-1.5 border border-slate-700 hover:bg-slate-800 text-slate-200 px-3 py-1.5 rounded-lg text-xs disabled:opacity-50";

/** Save a blob response (filename from Content-Disposition) and return the batch id header. */
function saveBlob(res: { data: Blob; headers: Record<string, any> }, fallback: string): number | null {
  const disp: string = res.headers["content-disposition"] ?? "";
  const m = /filename="?([^";]+)"?/.exec(disp);
  const url = URL.createObjectURL(res.data);
  const a = document.createElement("a");
  a.href = url;
  a.download = m ? m[1] : fallback;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  const id = res.headers["x-tally-export-batch"];
  return id ? Number(id) : null;
}

/** Error code of a refused blob request (the JSON body arrives as a Blob). */
async function blobErrorCode(err: any): Promise<string> {
  const data = err?.response?.data;
  if (data instanceof Blob) {
    try {
      const body = JSON.parse(await data.text());
      return typeof body?.error === "string" ? body.error : "generic";
    } catch {
      return "generic";
    }
  }
  return normaliseError(err).general[0] ?? "generic";
}

const Section: React.FC<{ title: string; right?: React.ReactNode; children: React.ReactNode }> = ({
  title,
  right,
  children,
}) => (
  <section className="border-t border-slate-800">
    <div className="px-5 py-3 flex items-center justify-between gap-3 bg-slate-950/40">
      <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
      {right}
    </div>
    {children}
  </section>
);

const Th: React.FC<{ children?: React.ReactNode; right?: boolean }> = ({ children, right }) => (
  <th className={`px-5 py-3 ${right ? "text-right" : ""}`}>{children}</th>
);
const headCls = "bg-slate-950/80 text-xs uppercase text-slate-400 font-semibold border-b border-slate-800";

/** One unmapped row: editable Tally name (+ unit) with a Save button. */
const MapRow: React.FC<{
  label: string;
  sub?: string;
  group?: string;
  used: number;
  initialName: string;
  initialUnit?: string;
  withUnit?: boolean;
  onSave: (name: string, unit: string) => Promise<void>;
}> = ({ label, sub, group, used, initialName, initialUnit = "", withUnit, onSave }) => {
  const { t } = useTranslation();
  const [name, setName] = useState(initialName);
  const [unit, setUnit] = useState(initialUnit);
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const save = async () => {
    setState("busy");
    try {
      await onSave(name.trim(), unit.trim());
      setState("done");
    } catch {
      setState("error");
    }
  };
  return (
    <tr>
      <td className="px-5 py-2.5">
        <div className="text-slate-200">{label}</div>
        {sub && <div className="text-xs text-slate-500">{sub}</div>}
      </td>
      <td className="px-5 py-2.5">
        <input
          aria-label={t("tx.col.tally_name")}
          className={`${inputCls} w-full min-w-[14rem]`}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </td>
      {withUnit && (
        <td className="px-5 py-2.5">
          <input
            aria-label={t("tx.col.unit")}
            className={`${inputCls} w-24`}
            value={unit}
            onChange={(e) => setUnit(e.target.value)}
          />
        </td>
      )}
      <td className="px-5 py-2.5 text-xs text-slate-400">{group}</td>
      <td className="px-5 py-2.5 text-right tabular-nums text-slate-400">{used}</td>
      <td className="px-5 py-2.5 text-right">
        <button type="button" className={btnGhost} disabled={!name.trim() || state === "busy"} onClick={save}>
          {state === "busy" && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          {state === "done" ? t("tx.saved") : t("tx.save")}
        </button>
      </td>
    </tr>
  );
};

const SettingsPanel: React.FC<{ company: string; onSeeded: () => void }> = ({ company, onSeeded }) => {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["tx-settings", company],
    queryFn: async () =>
      (await apiClient.get<{ results: Settings[] }>(`${BASE}/settings/`, { params: { company } })).data.results[0],
    enabled: !!company,
  });
  const [name, setName] = useState("");
  const [types, setTypes] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  useEffect(() => {
    if (data) {
      setName(data.tally_company_name);
      setTypes(data.voucher_type_overrides ?? {});
    }
  }, [data]);

  const save = useMutation({
    mutationFn: async () => {
      const overrides = Object.fromEntries(Object.entries(types).filter(([, v]) => v.trim()));
      await apiClient.patch(`${BASE}/settings/${data!.id}/`, {
        tally_company_name: name.trim(),
        voucher_type_overrides: overrides,
      });
    },
    onSuccess: () => {
      setNote(t("tx.settings_saved"));
      void qc.invalidateQueries({ queryKey: ["tx-settings"] });
      void qc.invalidateQueries({ queryKey: ["tx-preflight"] });
    },
  });
  const seed = useMutation({
    mutationFn: async () =>
      (await apiClient.post<{ accounts?: number; parties?: number; items?: number }>(`${BASE}/ledger-maps/seed-from-legacy/`, { company })).data,
    onSuccess: (r) => {
      setNote(t("tx.seed_done", { accounts: r.accounts ?? 0, parties: r.parties ?? 0, items: r.items ?? 0 }));
      void qc.invalidateQueries({ queryKey: ["tx-settings"] });
      onSeeded();
    },
  });

  return (
    <div className="px-5 py-4 space-y-4">
      <div className="space-y-1 max-w-md">
        <label htmlFor="tx-company-name" className="block text-xs font-medium text-slate-400">
          {t("tx.company_name")}
        </label>
        <input id="tx-company-name" className={`${inputCls} w-full`} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="space-y-2">
        <p className="text-xs font-medium text-slate-400">{t("tx.voucher_types")}</p>
        <p className="text-xs text-slate-500">{t("tx.voucher_types_help")}</p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {VOUCHER_KEYS.map(([key, std]) => (
            <div key={key} className="space-y-1">
              <label htmlFor={`tx-vt-${key}`} className="block text-xs text-slate-500">
                {key}
              </label>
              <input
                id={`tx-vt-${key}`}
                className={`${inputCls} w-full`}
                placeholder={std}
                value={types[key] ?? ""}
                onChange={(e) => setTypes({ ...types, [key]: e.target.value })}
              />
            </div>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className={btn} disabled={!data || save.isPending} onClick={() => save.mutate()}>
          {t("tx.save")}
        </button>
        <button type="button" className={btnGhost} disabled={seed.isPending} onClick={() => seed.mutate()}>
          {seed.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          {t("tx.seed_btn")}
        </button>
        {note && <span className="text-xs text-emerald-400">{note}</span>}
      </div>
    </div>
  );
};

export const TallyExportPage: React.FC = () => {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { companies, company, setCompany, loadingCompanies } = useCompany();
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [includeExported, setIncludeExported] = useState(false);
  const [withMasters, setWithMasters] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const params = {
    company,
    ...(fromDate ? { from_date: fromDate } : {}),
    ...(toDate ? { to_date: toDate } : {}),
    include_exported: includeExported ? "true" : "false",
  };
  const pre = useQuery({
    queryKey: ["tx-preflight", params],
    queryFn: async () => (await apiClient.get<Preflight>(`${BASE}/batches/preflight/`, { params })).data,
    enabled: !!company,
  });
  const pending = useQuery({
    queryKey: ["tx-pending", company],
    queryFn: async () => (await apiClient.get<Pending>(`${BASE}/batches/pending/`, { params: { company } })).data,
    enabled: !!company,
  });
  const history = useQuery({
    queryKey: ["tx-batches", company],
    queryFn: async () =>
      (await apiClient.get<{ results: Batch[] }>(`${BASE}/batches/`, { params: { company, page_size: 20 } })).data.results,
    enabled: !!company,
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["tx-preflight"] });
    void qc.invalidateQueries({ queryKey: ["tx-pending"] });
    void qc.invalidateQueries({ queryKey: ["tx-batches"] });
  };

  const run = useMutation({
    mutationFn: async () => {
      const body = {
        company: Number(company),
        from_date: fromDate || null,
        to_date: toDate || null,
        include_exported: includeExported,
        create_masters: withMasters,
      };
      const res = await apiClient.post(`${BASE}/batches/`, body, { responseType: "blob" });
      saveBlob(res as any, "tally-export.xml");
    },
    onSuccess: () => {
      setMessage({
        ok: true,
        text: t("tx.exported_ok", { vouchers: pre.data?.voucher_count ?? 0, cancels: pre.data?.cancel_count ?? 0 }),
      });
      refresh();
    },
    onError: async (err) => {
      const code = await blobErrorCode(err);
      setMessage({ ok: false, text: t(`tx.err.${code}`, { defaultValue: t("tx.err.generic") }) });
      refresh();
    },
  });

  const download = async (id: number, part?: string) => {
    const res = await apiClient.get(`${BASE}/batches/${id}/download/`, {
      params: part ? { part } : {},
      responseType: "blob",
    });
    saveBlob(res as any, `tally-export-${id}.xml`);
  };
  const reexport = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiClient.post(`${BASE}/batches/${id}/reexport/`, {}, { responseType: "blob" });
      saveBlob(res as any, "tally-export.xml");
    },
    onSuccess: refresh,
    onError: async (err) => {
      const code = await blobErrorCode(err);
      setMessage({ ok: false, text: t(`tx.err.${code}`, { defaultValue: t("tx.err.generic") }) });
    },
  });

  const mapLedger = (accountId: number, partyType: string, party: string, group: string) => async (name: string) => {
    await apiClient.post(`${BASE}/ledger-maps/`, {
      company: Number(company),
      account: accountId,
      party_type: partyType,
      party,
      tally_ledger: name,
      tally_group: group,
    });
    refresh();
  };
  const mapItem = (itemId: number) => async (name: string, unit: string) => {
    await apiClient.post(`${BASE}/item-maps/`, {
      company: Number(company),
      item: itemId,
      tally_item: name,
      tally_unit: unit,
    });
    refresh();
  };

  const d = pre.data;
  const hasUnmapped = !!d && d.unmapped_count > 0;
  const canRun = !!d && d.vouchers.length > 0 && d.unbalanced_count === 0 && (!hasUnmapped || withMasters);
  const statusOf = (v: VoucherRow) =>
    v.unmapped ? (
      <span className="text-amber-300">{t("tx.st_unmapped")}</span>
    ) : !v.balanced ? (
      <span className="text-rose-300">{t("tx.st_unbalanced")}</span>
    ) : v.previously_exported && v.action === "Create" ? (
      <span className="text-slate-400">{t("tx.st_again")}</span>
    ) : (
      <span className="text-emerald-400">{t("tx.st_ready")}</span>
    );

  return (
    <ReportShell
      titleKey="tx.title"
      loading={pre.isFetching || loadingCompanies}
      error={pre.error}
      noCompany={!loadingCompanies && companies.length === 0}
      status={
        pending.data && (
          <span
            className={`inline-flex items-center gap-2 text-xs px-2.5 py-1 rounded-md border font-semibold ${
              pending.data.total
                ? "bg-amber-950 text-amber-300 border-amber-800"
                : "bg-emerald-950 text-emerald-400 border-emerald-800"
            }`}
          >
            {pending.data.total ? <AlertTriangle className="w-3.5 h-3.5" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
            {t("tx.pending")}:{" "}
            {pending.data.total
              ? [
                  pending.data.new ? t("tx.pending_new", { count: pending.data.new }) : "",
                  pending.data.cancelled ? t("tx.pending_cancel", { count: pending.data.cancelled }) : "",
                ]
                  .filter(Boolean)
                  .join(", ")
              : t("tx.pending_none")}
          </span>
        )
      }
      filters={
        <>
          <CompanySelect companies={companies} value={company} onChange={setCompany} />
          <DateInput id="tx-from" labelKey="acc.fields.from_date" value={fromDate} onChange={setFromDate} />
          <DateInput id="tx-to" labelKey="acc.fields.to_date" value={toDate} onChange={setToDate} />
          <label className="flex items-center gap-2 text-sm text-slate-300 pb-2">
            <input type="checkbox" checked={includeExported} onChange={(e) => setIncludeExported(e.target.checked)} />
            {t("tx.include_exported")}
          </label>
        </>
      }
    >
      <p className="px-5 py-3 text-xs text-slate-400">{t("tx.intro")}</p>

      <Section title={t("tx.vouchers_title")}>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-300">
            <thead className={headCls}>
              <tr>
                <Th>{t("tx.col.voucher")}</Th>
                <Th>{t("tx.col.date")}</Th>
                <Th>{t("tx.col.type")}</Th>
                <Th>{t("tx.col.action")}</Th>
                <Th right>{t("acc.fields.debit")}</Th>
                <Th right>{t("acc.fields.credit")}</Th>
                <Th>{t("tx.col.status")}</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {!d ? (
                <tr>
                  <td colSpan={7} className="px-6 py-8 text-center text-slate-400">
                    {t("common.loading")}
                  </td>
                </tr>
              ) : d.vouchers.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-6 py-8 text-center text-slate-500">
                    {t("tx.vouchers_none")}
                  </td>
                </tr>
              ) : (
                d.vouchers.map((v) => (
                  <tr key={`${v.doctype}/${v.name}`}>
                    <td className="px-5 py-2.5 font-medium text-slate-200">{v.name}</td>
                    <td className="px-5 py-2.5">{v.posting_date}</td>
                    <td className="px-5 py-2.5">{v.tally_voucher_type}</td>
                    <td className="px-5 py-2.5">{v.action === "Cancel" ? t("tx.action_cancel") : t("tx.action_create")}</td>
                    <td className="px-5 py-2.5 text-right">{money(v.debit)}</td>
                    <td className="px-5 py-2.5 text-right">{money(v.credit)}</td>
                    <td className="px-5 py-2.5">{statusOf(v)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {d && d.skipped.length > 0 && (
          <p className="px-5 py-2 text-xs text-slate-500">{t("tx.skipped", { count: d.skipped.length })}</p>
        )}
      </Section>

      {hasUnmapped && d && (
        <Section title={t("tx.unmapped_title")}>
          {d.unmapped.accounts.length > 0 && (
            <UnmappedTable title={t("tx.unmapped_accounts")}>
              {d.unmapped.accounts.map((a) => (
                <MapRow
                  key={`a${a.account}`}
                  label={a.account_name}
                  group={a.suggested_group}
                  used={a.vouchers}
                  initialName={a.suggested_ledger}
                  onSave={mapLedger(a.account, "", "", a.suggested_group)}
                />
              ))}
            </UnmappedTable>
          )}
          {d.unmapped.parties.length > 0 && (
            <UnmappedTable title={t("tx.unmapped_parties")}>
              {d.unmapped.parties.map((p) => (
                <MapRow
                  key={`p${p.party_type}${p.party}`}
                  label={p.party}
                  sub={p.party_type}
                  group={p.suggested_group}
                  used={p.vouchers}
                  initialName={p.suggested_ledger}
                  onSave={mapLedger(p.account, p.party_type, p.party, p.suggested_group)}
                />
              ))}
            </UnmappedTable>
          )}
          {d.unmapped.items.length > 0 && (
            <UnmappedTable title={t("tx.unmapped_items")} withUnit>
              {d.unmapped.items.map((i) => (
                <MapRow
                  key={`i${i.item}`}
                  label={i.item_name}
                  sub={i.item_code}
                  used={i.vouchers}
                  withUnit
                  initialName={i.suggested_item}
                  initialUnit={i.suggested_unit}
                  onSave={mapItem(i.item)}
                />
              ))}
            </UnmappedTable>
          )}
        </Section>
      )}

      <div className="px-5 py-4 border-t border-slate-800 space-y-3">
        {hasUnmapped && (
          <label className="flex items-start gap-2 text-sm text-slate-300">
            <input type="checkbox" className="mt-1" checked={withMasters} onChange={(e) => setWithMasters(e.target.checked)} />
            <span>
              {t("tx.masters_label")}
              <span className="block text-xs text-slate-500">{t("tx.masters_help")}</span>
            </span>
          </label>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className={btn} disabled={!canRun || run.isPending} onClick={() => run.mutate()}>
            {run.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileOutput className="w-4 h-4" />}
            {run.isPending ? t("tx.exporting") : t("tx.export_btn")}
          </button>
          {d && hasUnmapped && !withMasters && <span className="text-xs text-amber-300">{t("tx.blocked")}</span>}
          {d && d.unbalanced_count > 0 && <span className="text-xs text-rose-300">{t("tx.unbalanced_blocked")}</span>}
          {message && (
            <span className={`text-xs ${message.ok ? "text-emerald-400" : "text-rose-300"}`}>{message.text}</span>
          )}
        </div>
      </div>

      <Section title={t("tx.history_title")}>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-300">
            <thead className={headCls}>
              <tr>
                <Th>{t("tx.col.when")}</Th>
                <Th>{t("tx.col.by")}</Th>
                <Th>{t("tx.col.range")}</Th>
                <Th right>{t("tx.col.vouchers")}</Th>
                <Th right>{t("tx.col.cancels")}</Th>
                <Th right>{t("tx.col.masters")}</Th>
                <Th />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {!history.data || history.data.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-6 py-8 text-center text-slate-500">
                    {t("tx.history_none")}
                  </td>
                </tr>
              ) : (
                history.data.map((b) => (
                  <tr key={b.id}>
                    <td className="px-5 py-2.5">{new Date(b.created_at).toLocaleString()}</td>
                    <td className="px-5 py-2.5">{b.created_by_name ?? ""}</td>
                    <td className="px-5 py-2.5">{[b.from_date, b.to_date].filter(Boolean).join(" - ")}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{b.voucher_count}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{b.cancel_count}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{b.ledger_master_count + b.item_master_count}</td>
                    <td className="px-5 py-2.5 text-right whitespace-nowrap space-x-2">
                      <button type="button" className={btnGhost} onClick={() => void download(b.id, "vouchers")}>
                        <Download className="w-3.5 h-3.5" /> {t("tx.download")}
                      </button>
                      {b.has_masters && (
                        <button type="button" className={btnGhost} onClick={() => void download(b.id, "masters")}>
                          <Download className="w-3.5 h-3.5" /> {t("tx.download_masters")}
                        </button>
                      )}
                      <button type="button" className={btnGhost} disabled={reexport.isPending} onClick={() => reexport.mutate(b.id)}>
                        <RefreshCw className="w-3.5 h-3.5" /> {t("tx.reexport")}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title={t("tx.settings_title")}>
        <SettingsPanel company={company} onSeeded={refresh} />
      </Section>
    </ReportShell>
  );
};

const UnmappedTable: React.FC<{ title: string; withUnit?: boolean; children: React.ReactNode }> = ({
  title,
  withUnit,
  children,
}) => {
  const { t } = useTranslation();
  return (
    <div className="overflow-x-auto">
      <p className="px-5 pt-3 text-xs font-semibold uppercase tracking-wider text-slate-400">{title}</p>
      <table className="w-full text-left text-sm text-slate-300">
        <thead className={headCls}>
          <tr>
            <Th>{t("tx.col.erp")}</Th>
            <Th>{t("tx.col.tally_name")}</Th>
            {withUnit && <Th>{t("tx.col.unit")}</Th>}
            <Th>{t("tx.col.group")}</Th>
            <Th right>{t("tx.col.used")}</Th>
            <Th />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/60">{children}</tbody>
      </table>
    </div>
  );
};
