import React, { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import { AlertTriangle, Check, Info, LayoutGrid, Loader2, PackageCheck, Plus, Trash2, X } from "lucide-react";
import { Layout } from "@/components/Layout";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Modal } from "@/components/ui/Modal";
import { Select } from "@/components/ui/Select";
import { useToast } from "@/components/ui/Toast";
import { apiClient } from "@/api/client";
import { listAllRecords, normaliseError } from "@/api/endpoints";
import { useCompany } from "@/context/CompanyContext";
import { isValidDecimalInput } from "@/lib/decimal";
import { todayISO } from "@/lib/format";

/* ---- API shapes (manufacturing/serializers.py BatchSerializer, BOMSerializer) ---- */

type BatchStatus = "Grinding" | "Packing" | "Done" | "Cancelled";

type BatchRow = {
  id: number;
  name: string;
  bulk_item: number;
  bulk_item_code: string;
  bulk_item_name: string;
  status: BatchStatus;
  grinding_planned_qty: string;
  grinding_produced_qty: string;
  grinding_process_loss_qty: string;
  created_at: string;
};

type BomLine = {
  item: number;
  item_code: string;
  item_name: string;
  stock_uom: string;
  qty: string;
};

type ActiveBom = {
  id: number;
  item: number;
  item_code: string;
  item_name: string;
  quantity: string;
  process_loss_percentage: string;
  good_qty: string | null;
  items: BomLine[];
};

type FinishResult = { batch: BatchRow; grinding_loss: string; warnings: string[] };
type PackResult = { batch: BatchRow; bulk_used: string; bulk_packed: string; packing_loss: string };

type Notice = { id: number; tone: "info" | "warn"; label: string; text: string };

type PackLine = { key: number; sku: string; packets: string };

/** Product and quantity for the New batch form, from an assistant proposal. */
type BatchSeed = { product: string; qty: string };

/* ---- helpers ---- */

const inputCls =
  "w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2.5 text-base text-slate-100 placeholder:text-slate-500 hover:border-slate-700 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20";
const primaryBtn =
  "inline-flex min-h-[48px] w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow hover:bg-indigo-500 disabled:opacity-50 sm:w-auto";
const secondaryBtn =
  "inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl border border-rose-900/60 px-4 py-2 text-sm font-medium text-rose-300 hover:bg-rose-950/40 disabled:opacity-50";

/** A quantity as the user reads it: no trailing zeros. */
const qtyText = (value: string | number | null | undefined): string => {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? String(n) : "0";
};

/** A positive decimal typed by the user (up to `places` digits), or null while blank or invalid. */
const positive = (value: string, places = 3): number | null => {
  if (!isValidDecimalInput(value, places)) return null;
  const n = Number(value);
  return value.trim() !== "" && Number.isFinite(n) && n > 0 ? n : null;
};

/** A whole, positive packet count, or null. */
const whole = (value: string): number | null => (/^\d+$/.test(value.trim()) && Number(value) > 0 ? Number(value) : null);

const goodOutput = (bom: ActiveBom): number => {
  if (bom.good_qty !== null && bom.good_qty !== undefined) return Number(bom.good_qty);
  return Number(bom.quantity) * (1 - Number(bom.process_loss_percentage || 0) / 100);
};

/** Raw quantity for `planned` good output: row qty x planned / good output, 3 dp (same rule as the server). */
const rawFor = (line: BomLine, planned: number, bom: ActiveBom): string | null => {
  const good = goodOutput(bom);
  if (!(good > 0)) return null;
  return ((Number(line.qty) * planned) / good).toFixed(3);
};

const localDay = (iso: string): string => {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

const byCreatedAsc = (a: BatchRow, b: BatchRow) =>
  a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : a.id - b.id;

/** Readable lines for a failed request. */
const errorLines = (err: unknown, forbidden: string, fallback: string): string[] => {
  const n = normaliseError(err);
  if (n.forbidden) return [forbidden];
  const lines = [...n.general, ...Object.values(n.fields).flat(), ...Object.values(n.tables).flat()];
  return lines.length ? lines : [fallback];
};

const ErrorList: React.FC<{ lines: string[] }> = ({ lines }) => (
  <ul role="alert" className="space-y-1 rounded-lg border border-rose-900/60 bg-rose-950/40 p-3 text-sm text-rose-200">
    {lines.map((line, i) => (
      <li key={i}>{line}</li>
    ))}
  </ul>
);

/* ---- New batch ---- */

const NewBatchForm: React.FC<{
  companyId: number | null;
  boms: ActiveBom[];
  seed: BatchSeed | null;
  onSaved: (batch: BatchRow) => void;
}> = ({ companyId, boms, seed, onSaved }) => {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [product, setProduct] = useState(seed?.product ?? "");
  const [qty, setQty] = useState(seed?.qty ?? "");
  const [errors, setErrors] = useState<string[]>([]);

  const bom = boms.find((b) => String(b.item) === product) ?? null;
  const planned = positive(qty);
  const options = boms.map((b) => ({ value: String(b.item), label: `${b.item_code} - ${b.item_name}` }));
  const ready = bom !== null && planned !== null && companyId !== null;

  const create = useMutation({
    mutationFn: async () => {
      const res = await apiClient.post("/manufacturing/batches/", {
        company: companyId,
        bulk_item: Number(product),
        bulk_qty: qty.trim(),
      });
      return res.data as BatchRow;
    },
    onSuccess: (batch) => {
      qc.invalidateQueries({ queryKey: ["manufacturing", "batches"] });
      toast.success(t("batches.toast.started", { name: batch.name }));
      onSaved(batch);
    },
    onError: (err) => setErrors(errorLines(err, t("common.forbidden"), t("common.action_failed"))),
  });

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready && !create.isPending) {
          setErrors([]);
          create.mutate();
        }
      }}
    >
      {errors.length > 0 && <ErrorList lines={errors} />}

      <div className="space-y-1.5">
        <label htmlFor="new-batch-product" className="text-sm font-medium text-slate-300">
          {t("batches.new.product")}
        </label>
        {boms.length === 0 ? (
          <p className="text-sm text-amber-300">{t("batches.new.no_products")}</p>
        ) : (
          <Select
            id="new-batch-product"
            value={product}
            options={options}
            onChange={setProduct}
            placeholder={t("batches.new.pick_product")}
            aria-label={t("batches.new.product")}
            className="w-full"
          />
        )}
      </div>

      <div className="space-y-1.5">
        <label htmlFor="new-batch-qty" className="text-sm font-medium text-slate-300">
          {t("batches.new.quantity")}
        </label>
        <input
          id="new-batch-qty"
          type="text"
          inputMode="decimal"
          value={qty}
          onChange={(e) => isValidDecimalInput(e.target.value, 3) && setQty(e.target.value)}
          className={inputCls}
        />
        {qty !== "" && planned === null && <p className="text-xs text-rose-300">{t("batches.validation.quantity")}</p>}
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
        <p className="text-sm font-semibold text-slate-200">{t("batches.new.raw_title")}</p>
        {bom && planned !== null ? (
          <>
            <ul className="mt-2 space-y-1.5 text-sm">
              {bom.items.map((line) => (
                <li key={line.item} className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 truncate text-slate-300">{line.item_name}</span>
                  <span className="shrink-0 tabular-nums text-slate-100">
                    {rawFor(line, planned, bom)} {line.stock_uom}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[11px] text-slate-500">{t("batches.new.raw_note")}</p>
          </>
        ) : (
          <p className="mt-2 text-sm text-slate-500">{t("batches.new.raw_empty")}</p>
        )}
      </div>

      <button type="submit" disabled={!ready || create.isPending} className={primaryBtn}>
        {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
        {t("batches.new.submit")}
      </button>
    </form>
  );
};

/* ---- Finish grinding ---- */

const FinishForm: React.FC<{
  batch: BatchRow;
  bom: ActiveBom | null;
  onSaved: (result: FinishResult) => void;
}> = ({ batch, bom, onSaved }) => {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [produced, setProduced] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const value = positive(produced);

  // Raw consumed is the BOM input for the planned bulk; loss is that minus what came out.
  const planned = Number(batch.grinding_planned_qty);
  const rawTotal = bom ? bom.items.reduce((sum, line) => sum + Number(rawFor(line, planned, bom) ?? 0), 0) : null;
  const loss = rawTotal !== null && value !== null ? rawTotal - value : null;

  const finish = useMutation({
    mutationFn: async () => {
      const res = await apiClient.post(`/manufacturing/batches/${batch.id}/finish-grinding/`, {
        bulk_qty: produced.trim(),
      });
      return res.data as FinishResult;
    },
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["manufacturing", "batches"] });
      onSaved(result);
    },
    onError: (err) => setErrors(errorLines(err, t("common.forbidden"), t("common.action_failed"))),
  });

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (value !== null && !finish.isPending) {
          setErrors([]);
          finish.mutate();
        }
      }}
    >
      {errors.length > 0 && <ErrorList lines={errors} />}

      <div className="flex items-baseline justify-between gap-3 rounded-xl border border-slate-800 bg-slate-950/60 p-3 text-sm">
        <span className="text-slate-400">{t("batches.planned")}</span>
        <span className="tabular-nums text-slate-100">{t("batches.qty_kg", { qty: qtyText(batch.grinding_planned_qty) })}</span>
      </div>

      <div className="space-y-1.5">
        <label htmlFor="finish-produced" className="text-sm font-medium text-slate-300">
          {t("batches.finish.produced")}
        </label>
        <input
          id="finish-produced"
          type="text"
          inputMode="decimal"
          value={produced}
          onChange={(e) => isValidDecimalInput(e.target.value, 3) && setProduced(e.target.value)}
          className={inputCls}
        />
        {produced !== "" && value === null && <p className="text-xs text-rose-300">{t("batches.validation.quantity")}</p>}
        {loss !== null && (
          <p className="text-sm text-slate-300">{t("batches.finish.loss_result", { qty: qtyText(loss.toFixed(3)) })}</p>
        )}
      </div>

      <button type="submit" disabled={value === null || finish.isPending} className={primaryBtn}>
        {finish.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
        {t("batches.finish.submit")}
      </button>
    </form>
  );
};

/* ---- Pack ---- */

const PackForm: React.FC<{
  batch: BatchRow;
  boms: ActiveBom[];
  onSaved: (result: PackResult) => void;
}> = ({ batch, boms, onSaved }) => {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const nextKey = useRef(2);
  const [bulkUsed, setBulkUsed] = useState("");
  const [lines, setLines] = useState<PackLine[]>([{ key: 1, sku: "", packets: "" }]);
  const [errors, setErrors] = useState<string[]>([]);

  // Pack sizes are the default BOMs that use this batch's bulk item.
  const sizes = boms.filter((b) => b.items.some((line) => line.item === batch.bulk_item));
  const options = sizes.map((b) => ({ value: String(b.item), label: `${b.item_code} - ${b.item_name}` }));

  const bulk = positive(bulkUsed);

  /** Bulk the packets of one line need, or null while the line is incomplete. */
  const bulkFor = (line: PackLine): number | null => {
    const bom = sizes.find((b) => String(b.item) === line.sku);
    const packets = whole(line.packets);
    const row = bom?.items.find((r) => r.item === batch.bulk_item);
    if (!bom || !row || packets === null) return null;
    const raw = rawFor(row, packets, bom);
    return raw === null ? null : Number(raw);
  };

  const issue = (() => {
    if (bulk === null) return "bulk_used";
    if (lines.some((l) => l.sku === "")) return "size_required";
    if (lines.some((l) => whole(l.packets) === null)) return "packets";
    const skus = lines.map((l) => l.sku);
    if (new Set(skus).size !== skus.length) return "duplicate_size";
    return null;
  })();

  const needTotal = lines.reduce((sum, l) => sum + (bulkFor(l) ?? 0), 0);

  const updateLine = (key: number, change: Partial<PackLine>) =>
    setLines((prev) => prev.map((l) => (l.key === key ? { ...l, ...change } : l)));

  const pack = useMutation({
    mutationFn: async () => {
      const res = await apiClient.post(`/manufacturing/batches/${batch.id}/pack/`, {
        bulk_used: bulkUsed.trim(),
        lines: lines.map((l) => ({ sku: Number(l.sku), packets: whole(l.packets) })),
      });
      return res.data as PackResult;
    },
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["manufacturing", "batches"] });
      onSaved(result);
    },
    onError: (err) => setErrors(errorLines(err, t("common.forbidden"), t("common.action_failed"))),
  });

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (issue === null && !pack.isPending) {
          setErrors([]);
          pack.mutate();
        }
      }}
    >
      {errors.length > 0 && <ErrorList lines={errors} />}

      <div className="space-y-1.5">
        <label htmlFor="pack-bulk-used" className="text-sm font-medium text-slate-300">
          {t("batches.pack.bulk_used")}
        </label>
        <input
          id="pack-bulk-used"
          type="text"
          inputMode="decimal"
          value={bulkUsed}
          onChange={(e) => isValidDecimalInput(e.target.value, 3) && setBulkUsed(e.target.value)}
          className={inputCls}
        />
      </div>

      {sizes.length === 0 ? (
        <p className="text-sm text-amber-300">{t("batches.pack.no_sizes")}</p>
      ) : (
        <ul className="space-y-3">
          {lines.map((line) => {
            const need = bulkFor(line);
            return (
              <li key={line.key} className="space-y-2 rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                <div className="flex items-end gap-2">
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <span className="block text-xs text-slate-400">{t("batches.pack.size")}</span>
                    <Select
                      value={line.sku}
                      options={options}
                      onChange={(v) => updateLine(line.key, { sku: v })}
                      placeholder={t("batches.pack.pick_size")}
                      aria-label={t("batches.pack.size")}
                      className="w-full"
                    />
                  </div>
                  <div className="w-28 shrink-0 space-y-1.5">
                    <label htmlFor={`pack-packets-${line.key}`} className="block text-xs text-slate-400">
                      {t("batches.pack.packets")}
                    </label>
                    <input
                      id={`pack-packets-${line.key}`}
                      type="text"
                      inputMode="numeric"
                      value={line.packets}
                      onChange={(e) => /^\d*$/.test(e.target.value) && updateLine(line.key, { packets: e.target.value })}
                      className={inputCls}
                    />
                  </div>
                  {lines.length > 1 && (
                    <button
                      type="button"
                      onClick={() => setLines((prev) => prev.filter((l) => l.key !== line.key))}
                      aria-label={t("batches.pack.remove_size")}
                      className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-slate-800 text-slate-400 hover:bg-slate-800"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </div>
                {need !== null && (
                  <p className="text-xs text-slate-400">{t("batches.pack.needs", { qty: qtyText(need.toFixed(3)) })}</p>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {sizes.length > 0 && (
        <button
          type="button"
          onClick={() => {
            const key = nextKey.current;
            nextKey.current += 1;
            setLines((prev) => [...prev, { key, sku: "", packets: "" }]);
          }}
          className="inline-flex min-h-[44px] items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800"
        >
          <Plus className="h-4 w-4" />
          {t("batches.pack.add_size")}
        </button>
      )}

      {needTotal > 0 && (
        <p className="text-sm text-slate-300">{t("batches.pack.total_needed", { qty: qtyText(needTotal.toFixed(3)) })}</p>
      )}
      {issue !== null && <p className="text-xs text-rose-300">{t(`batches.validation.${issue}`)}</p>}
      {issue === null && bulk !== null && bulk - needTotal >= 0 && (
        <p className="text-sm text-slate-300">{t("batches.pack.loss_result", { qty: qtyText((bulk - needTotal).toFixed(3)) })}</p>
      )}

      <button type="submit" disabled={issue !== null || pack.isPending} className={primaryBtn}>
        {pack.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <PackageCheck className="h-4 w-4" />}
        {t("batches.pack.submit")}
      </button>
    </form>
  );
};

/* ---- Board ---- */

type CardAction = { label: string; icon: React.ReactNode; onClick: () => void };

const BatchCard: React.FC<{ batch: BatchRow; action?: CardAction; onCancel?: () => void }> = ({
  batch,
  action,
  onCancel,
}) => {
  const { t } = useTranslation();
  const showResult = batch.status !== "Grinding";
  return (
    <li className="space-y-3 rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-sm">
      <div className="min-w-0">
        <p className="truncate font-semibold text-slate-100">{batch.bulk_item_name}</p>
        <p className="truncate text-xs text-slate-500">
          {batch.bulk_item_code} · {batch.name}
        </p>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
        <div>
          <dt className="text-slate-500">{t("batches.planned")}</dt>
          <dd className="tabular-nums text-slate-200">{t("batches.qty_kg", { qty: qtyText(batch.grinding_planned_qty) })}</dd>
        </div>
        {showResult && (
          <>
            <div>
              <dt className="text-slate-500">{t("batches.made")}</dt>
              <dd className="tabular-nums text-slate-200">{t("batches.qty_kg", { qty: qtyText(batch.grinding_produced_qty) })}</dd>
            </div>
            <div>
              <dt className="text-slate-500">{t("batches.grinding_loss")}</dt>
              <dd className="tabular-nums text-slate-200">
                {t("batches.qty_kg", { qty: qtyText(batch.grinding_process_loss_qty) })}
              </dd>
            </div>
          </>
        )}
      </dl>
      {action && (
        <button type="button" onClick={action.onClick} className={primaryBtn}>
          {action.icon}
          {action.label}
        </button>
      )}
      {onCancel && (
        <button type="button" onClick={onCancel} className={secondaryBtn}>
          <X className="h-4 w-4" />
          {t("batches.actions.cancel")}
        </button>
      )}
    </li>
  );
};

const BoardColumn: React.FC<{ title: string; count: number; empty: string; children: React.ReactNode }> = ({
  title,
  count,
  empty,
  children,
}) => (
  <section aria-label={title} className="space-y-3 rounded-2xl border border-slate-800 bg-slate-900/50 p-3">
    <header className="flex items-center justify-between px-1">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-300">{title}</h2>
      <span className="rounded-full bg-slate-800 px-2 py-0.5 text-xs tabular-nums text-slate-300">{count}</span>
    </header>
    {count === 0 ? (
      <p className="px-1 py-8 text-center text-xs text-slate-500">{empty}</p>
    ) : (
      <ul className="space-y-3">{children}</ul>
    )}
  </section>
);

/* ---- Page ---- */

export const BatchesPage: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const location = useLocation();
  const { activeCompany } = useCompany();
  const companyId = activeCompany?.id ?? null;

  // A batch proposed by the assistant opens the New batch form filled in (propose_batch).
  const proposed = (location.state as { initialValues?: { bulk_item?: unknown; bulk_qty?: unknown } } | null)
    ?.initialValues;
  const proposedSeed: BatchSeed | null = proposed
    ? { product: String(proposed.bulk_item ?? ""), qty: String(proposed.bulk_qty ?? "") }
    : null;
  const [newBatchSeed, setNewBatchSeed] = useState<BatchSeed | null>(proposedSeed);
  const [newOpen, setNewOpen] = useState<boolean>(proposedSeed !== null);
  const [finishing, setFinishing] = useState<BatchRow | null>(null);
  const [packing, setPacking] = useState<BatchRow | null>(null);
  const [cancelling, setCancelling] = useState<BatchRow | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);
  const nextNotice = useRef(1);

  const batchesQuery = useQuery({
    queryKey: ["manufacturing", "batches", companyId],
    queryFn: async () => (await listAllRecords("/manufacturing/batches/")) as BatchRow[],
    enabled: companyId !== null,
  });
  const bomsQuery = useQuery({
    queryKey: ["manufacturing", "active-boms", companyId],
    queryFn: async () =>
      (await listAllRecords("/manufacturing/boms/", { is_default: true, is_active: true })) as ActiveBom[],
    enabled: companyId !== null,
  });

  const batches = batchesQuery.data ?? [];
  const boms = bomsQuery.data ?? [];

  const addNotice = (notice: Omit<Notice, "id">) => {
    const id = nextNotice.current;
    nextNotice.current += 1;
    setNotices((prev) => [...prev, { ...notice, id }]);
  };

  const cancel = useMutation({
    mutationFn: async (batch: BatchRow) => {
      const res = await apiClient.post(`/manufacturing/batches/${batch.id}/cancel/`);
      return res.data as BatchRow;
    },
    onSuccess: (batch) => {
      setCancelling(null);
      qc.invalidateQueries({ queryKey: ["manufacturing", "batches"] });
      toast.success(t("batches.toast.cancelled", { name: batch.name }));
    },
    onError: (err) => {
      setCancelling(null);
      toast.error(errorLines(err, t("common.forbidden"), t("common.action_failed")).join(" "));
    },
  });

  const closeNew = () => {
    setNewOpen(false);
    setNewBatchSeed(null);
  };

  const today = todayISO();
  const grinding = batches.filter((b) => b.status === "Grinding").sort(byCreatedAsc);
  const packingList = batches.filter((b) => b.status === "Packing").sort(byCreatedAsc);
  const doneToday = batches
    .filter((b) => b.status === "Done" && localDay(b.created_at) === today)
    .sort((a, b) => byCreatedAsc(b, a));
  const openCount = grinding.length + packingList.length;

  return (
    <Layout>
      <div className="space-y-5 pb-12">
        <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-indigo-900/50 bg-indigo-950/60 text-indigo-400 shadow-sm">
              <LayoutGrid className="h-6 w-6" />
            </span>
            <div className="min-w-0">
              <h1 className="text-xl font-bold tracking-tight text-slate-100 sm:text-2xl">{t("batches.title")}</h1>
              <p className="mt-1 text-xs text-slate-400 sm:text-sm">{t("batches.subtitle")}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setNewOpen(true)}
            disabled={companyId === null}
            className={`${primaryBtn} self-start sm:self-auto`}
          >
            <Plus className="h-4 w-4" />
            {t("batches.new_batch")}
          </button>
        </header>

        {notices.length > 0 && (
          <section aria-live="polite" className="space-y-2">
            {notices.some((n) => n.tone === "warn") && (
              <p className="text-xs font-semibold uppercase tracking-wide text-amber-300">{t("batches.notice_title")}</p>
            )}
            {notices.map((n) => (
              <div
                key={n.id}
                className={`flex items-start gap-3 rounded-xl border p-3.5 text-sm ${
                  n.tone === "warn"
                    ? "border-amber-800/70 bg-amber-950/40 text-amber-100"
                    : "border-sky-800/70 bg-sky-950/40 text-sky-100"
                }`}
              >
                {n.tone === "warn" ? (
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
                ) : (
                  <Info className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" />
                )}
                <div className="min-w-0 flex-1">
                  <p className="font-semibold">{n.label}</p>
                  <p>{n.text}</p>
                </div>
                <button
                  type="button"
                  onClick={() => setNotices((prev) => prev.filter((x) => x.id !== n.id))}
                  aria-label={t("batches.dismiss")}
                  className="rounded-lg p-1.5 opacity-70 hover:opacity-100"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ))}
          </section>
        )}

        {batchesQuery.isLoading && (
          <div className="flex items-center gap-2 text-sm text-slate-400">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t("common.loading")}
          </div>
        )}
        {batchesQuery.isError && <p className="text-sm text-rose-300">{t("common.load_failed")}</p>}
        {batchesQuery.data && openCount === 0 && <p className="text-sm text-slate-400">{t("batches.empty_board")}</p>}

        <div className="grid gap-4 lg:grid-cols-3">
          <BoardColumn title={t("batches.column.grinding")} count={grinding.length} empty={t("batches.empty_column")}>
            {grinding.map((b) => (
              <BatchCard
                key={b.id}
                batch={b}
                action={{
                  label: t("batches.actions.finish_grinding"),
                  icon: <Check className="h-4 w-4" />,
                  onClick: () => setFinishing(b),
                }}
                onCancel={() => setCancelling(b)}
              />
            ))}
          </BoardColumn>

          <BoardColumn title={t("batches.column.packing")} count={packingList.length} empty={t("batches.empty_column")}>
            {packingList.map((b) => (
              <BatchCard
                key={b.id}
                batch={b}
                action={{
                  label: t("batches.actions.pack"),
                  icon: <PackageCheck className="h-4 w-4" />,
                  onClick: () => setPacking(b),
                }}
                onCancel={() => setCancelling(b)}
              />
            ))}
          </BoardColumn>

          <BoardColumn title={t("batches.column.done")} count={doneToday.length} empty={t("batches.empty_column")}>
            {doneToday.map((b) => (
              <BatchCard key={b.id} batch={b} />
            ))}
          </BoardColumn>
        </div>
      </div>

      <Modal
        open={newOpen}
        title={t("batches.new.title")}
        description={t("batches.new.description")}
        onClose={closeNew}
      >
        {newOpen && (
          <NewBatchForm
            companyId={companyId}
            boms={boms}
            seed={newBatchSeed}
            onSaved={closeNew}
          />
        )}
      </Modal>

      <Modal
        open={finishing !== null}
        title={t("batches.finish.title")}
        description={t("batches.finish.description")}
        onClose={() => setFinishing(null)}
      >
        {finishing && (
          <FinishForm
            batch={finishing}
            bom={boms.find((b) => b.item === finishing.bulk_item) ?? null}
            onSaved={(result) => {
              const label = finishing.bulk_item_name;
              setFinishing(null);
              toast.success(t("batches.toast.finished"));
              addNotice({ tone: "info", label, text: t("batches.finish.loss_result", { qty: qtyText(result.grinding_loss) }) });
              result.warnings.forEach((text) => addNotice({ tone: "warn", label, text }));
            }}
          />
        )}
      </Modal>

      <Modal
        open={packing !== null}
        title={t("batches.pack.title")}
        description={t("batches.pack.description")}
        onClose={() => setPacking(null)}
      >
        {packing && (
          <PackForm
            batch={packing}
            boms={boms}
            onSaved={(result) => {
              const label = packing.bulk_item_name;
              setPacking(null);
              toast.success(t("batches.toast.packed"));
              addNotice({ tone: "info", label, text: t("batches.pack.loss_result", { qty: qtyText(result.packing_loss) }) });
            }}
          />
        )}
      </Modal>

      <ConfirmDialog
        open={cancelling !== null}
        title={t("batches.cancel.title")}
        message={t("batches.cancel.message")}
        confirmLabel={t("batches.cancel.confirm")}
        tone="danger"
        busy={cancel.isPending}
        onConfirm={() => cancelling && cancel.mutate(cancelling)}
        onClose={() => setCancelling(null)}
      />
    </Layout>
  );
};
