import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  ExternalLink,
  Loader2,
  PackageCheck,
  Printer,
  RefreshCw,
  Search,
  Truck,
  Warehouse,
  X,
} from "lucide-react";
import { Layout } from "@/components/Layout";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/components/ui/Toast";
import { apiClient } from "@/api/client";
import { normaliseError } from "@/api/endpoints";
import { printPath } from "@/print/printTargets";
import { formatDate } from "@/lib/format";

export interface PendingOrderLine {
  order_item_id: number;
  item: number;
  item_id: number;
  item_code: string;
  item_name: string;
  warehouse: number | null;
  warehouse_name: string | null;
  qty: string | number;
  remaining_qty: string | number;
  ready_qty: string | number;
  short_qty: string | number;
}

export interface PendingOrder {
  id: number;
  name: string;
  customer: number;
  customer_name: string;
  transaction_date: string;
  delivery_date: string | null;
  grand_total: string | number;
  dispatch_status: string;
  lines: PendingOrderLine[];
}

interface DispatchedInvoiceResult {
  orderName: string;
  invoiceId: number | string;
  invoiceName: string;
}

/** Formats a numeric value to a clean string without trailing zero clutter. */
function formatNumber(val: string | number): string {
  const n = Number(val);
  if (isNaN(n)) return String(val);
  return String(n);
}

export const PendingOrdersPage: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();

  const [search, setSearch] = useState("");
  // orderId -> { [order_item_id]: stringQty }
  const [lineQtys, setLineQtys] = useState<Record<number, Record<number, string>>>({});
  // Target order to dispatch in ConfirmDialog
  const [confirmOrder, setConfirmOrder] = useState<PendingOrder | null>(null);
  // Recently created invoice for banner display
  const [recentInvoice, setRecentInvoice] = useState<DispatchedInvoiceResult | null>(null);

  // Fetch pending orders (oldest first as returned by backend)
  const {
    data: pendingOrders = [],
    isLoading,
    isRefetching,
    refetch,
    error: loadError,
  } = useQuery<PendingOrder[]>({
    queryKey: ["selling", "pending-orders"],
    queryFn: async () => {
      const res = await apiClient.get<PendingOrder[]>("/selling/sales-orders/pending/");
      return res.data;
    },
  });

  // Read current input qty for a line (defaults to ready_qty or 0 if out of stock)
  const getLineQty = (orderId: number, line: PendingOrderLine): string => {
    const orderInputs = lineQtys[orderId];
    if (orderInputs && orderInputs[line.order_item_id] !== undefined) {
      return orderInputs[line.order_item_id];
    }
    const ready = Number(line.ready_qty);
    const rem = Number(line.remaining_qty);
    const defaultVal = Math.max(0, Math.min(rem, ready));
    return formatNumber(defaultVal);
  };

  const setLineQty = (orderId: number, lineId: number, value: string) => {
    setLineQtys((prev) => ({
      ...prev,
      [orderId]: {
        ...(prev[orderId] || {}),
        [lineId]: value,
      },
    }));
  };

  // Dispatch mutation
  const dispatchMutation = useMutation({
    mutationFn: async ({
      order,
      linesPayload,
    }: {
      order: PendingOrder;
      linesPayload: { order_item_id: number; qty: string }[];
    }) => {
      const res = await apiClient.post(`/selling/sales-orders/${order.id}/dispatch/`, {
        lines: linesPayload,
      });
      return { order, invoice: res.data };
    },
    onSuccess: ({ order, invoice }) => {
      // Refresh list
      queryClient.invalidateQueries({ queryKey: ["selling", "pending-orders"] });

      // Clean local edits for this order
      setLineQtys((prev) => {
        const next = { ...prev };
        delete next[order.id];
        return next;
      });

      const invName = invoice.name || `INV-${invoice.id}`;
      toast.success(
        t("pending_orders.success_toast", {
          order: order.name,
          invoice: invName,
        })
      );

      setRecentInvoice({
        orderName: order.name,
        invoiceId: invoice.id,
        invoiceName: invName,
      });

      setConfirmOrder(null);
    },
    onError: (err) => {
      const norm = normaliseError(err);
      const msg = norm.general.join(" ") || t("common.action_failed");
      toast.error(msg);
      setConfirmOrder(null);
    },
  });

  // Calculate lines with qty > 0 to dispatch
  const getPreparedLines = (order: PendingOrder) => {
    const payload: { order_item_id: number; qty: string }[] = [];
    for (const line of order.lines) {
      const rawVal = getLineQty(order.id, line);
      const num = Number(rawVal);
      if (!isNaN(num) && num > 0) {
        payload.push({
          order_item_id: line.order_item_id,
          qty: String(num),
        });
      }
    }
    return payload;
  };

  // Handle clicking "Dispatch" on an order card
  const handleInitiateDispatch = (order: PendingOrder) => {
    const payload = getPreparedLines(order);
    if (payload.length === 0) {
      toast.error(t("pending_orders.no_dispatchable_qty"));
      return;
    }

    // Validate that no line exceeds remaining qty
    for (const p of payload) {
      const line = order.lines.find((l) => l.order_item_id === p.order_item_id);
      if (line && Number(p.qty) > Number(line.remaining_qty)) {
        toast.error(
          t("pending_orders.qty_exceeds_remaining", {
            max: formatNumber(line.remaining_qty),
          })
        );
        return;
      }
    }

    setConfirmOrder(order);
  };

  const handleConfirmDispatch = () => {
    if (!confirmOrder) return;
    const linesPayload = getPreparedLines(confirmOrder);
    dispatchMutation.mutate({ order: confirmOrder, linesPayload });
  };

  // Filter orders by search
  const filteredOrders = useMemo(() => {
    if (!search.trim()) return pendingOrders;
    const q = search.toLowerCase();
    return pendingOrders.filter(
      (o) =>
        o.name.toLowerCase().includes(q) ||
        o.customer_name.toLowerCase().includes(q) ||
        o.lines.some(
          (l) =>
            l.item_name.toLowerCase().includes(q) ||
            l.item_code.toLowerCase().includes(q)
        )
    );
  }, [pendingOrders, search]);

  return (
    <Layout>
      <div className="space-y-6 pb-12">
        {/* Header */}
        <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-indigo-900/50 bg-indigo-950/60 text-indigo-400 shadow-sm">
              <Truck className="h-6 w-6" />
            </span>
            <div className="min-w-0">
              <h1 className="text-xl font-bold tracking-tight text-slate-100 sm:text-2xl">
                {t("pending_orders.title")}
              </h1>
              <p className="mt-1 text-xs text-slate-400 sm:text-sm">
                {t("pending_orders.subtitle")}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 self-start sm:self-auto">
            <button
              type="button"
              onClick={() => refetch()}
              disabled={isRefetching || isLoading}
              className="inline-flex min-h-[44px] items-center gap-2 rounded-xl border border-slate-800 bg-slate-900 px-4 py-2 text-sm font-medium text-slate-200 shadow-sm transition hover:bg-slate-800 disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${isRefetching ? "animate-spin text-indigo-400" : ""}`} />
              <span>{t("pending_orders.refresh")}</span>
            </button>
          </div>
        </header>

        {/* Recently Dispatched Invoice Notification Banner */}
        {recentInvoice && (
          <div className="flex flex-col gap-3 rounded-2xl border border-emerald-800/80 bg-emerald-950/50 p-4 shadow-lg sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" />
              <div>
                <p className="text-sm font-semibold text-emerald-200">
                  {t("pending_orders.banner_title", {
                    order: recentInvoice.orderName,
                  })}
                </p>
                <p className="text-xs text-emerald-400/90">
                  {t("pending_orders.banner_subtitle", {
                    invoice: recentInvoice.invoiceName,
                  })}
                </p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 ms-8 sm:ms-0">
              <Link
                to={printPath("sales-invoice", recentInvoice.invoiceId)}
                target="_blank"
                rel="noreferrer"
                className="inline-flex min-h-[40px] items-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow hover:bg-emerald-500"
              >
                <Printer className="h-4 w-4" />
                {t("pending_orders.print_bill")}
              </Link>

              <Link
                to={`/selling/sales-invoices/${recentInvoice.invoiceId}`}
                className="inline-flex min-h-[40px] items-center gap-1.5 rounded-lg border border-emerald-700 bg-emerald-900/60 px-3 py-1.5 text-xs font-medium text-emerald-200 hover:bg-emerald-800/60"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                {t("pending_orders.view_invoice")}
              </Link>

              <button
                type="button"
                onClick={() => setRecentInvoice(null)}
                className="rounded-lg p-2 text-emerald-400 hover:bg-emerald-900/50"
                aria-label={t("pending_orders.dismiss")}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}

        {/* Search Bar */}
        <div className="relative">
          <Search className="pointer-events-none absolute start-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("pending_orders.search_placeholder")}
            className="w-full rounded-xl border border-slate-800 bg-slate-900/80 py-3 pe-4 ps-10 text-sm text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              className="absolute end-3.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* Loading & Error States */}
        {isLoading && (
          <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-slate-800 bg-slate-900/40 p-12 text-center">
            <Loader2 className="h-8 w-8 animate-spin text-indigo-400" />
            <p className="text-sm text-slate-400">{t("common.loading")}</p>
          </div>
        )}

        {loadError && !isLoading && (
          <div className="rounded-2xl border border-rose-900/50 bg-rose-950/30 p-6 text-center">
            <AlertTriangle className="mx-auto h-8 w-8 text-rose-400" />
            <p className="mt-2 text-sm font-semibold text-rose-200">
              {t("common.load_failed")}
            </p>
          </div>
        )}

        {/* Empty State */}
        {!isLoading && !loadError && filteredOrders.length === 0 && (
          <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-slate-800/80 bg-slate-900/40 p-12 text-center">
            <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-slate-800/80 text-slate-400">
              <PackageCheck className="h-7 w-7" />
            </span>
            <div className="max-w-md">
              <h3 className="text-base font-semibold text-slate-200">
                {t("pending_orders.empty_title")}
              </h3>
              <p className="mt-1 text-xs text-slate-400 sm:text-sm">
                {t("pending_orders.empty_subtitle")}
              </p>
            </div>
          </div>
        )}

        {/* Orders Card List (Oldest First) */}
        {!isLoading && filteredOrders.length > 0 && (
          <div className="space-y-5">
            {filteredOrders.map((order) => {
              const preparedLines = getPreparedLines(order);
              const totalDispatchQty = preparedLines.reduce(
                (sum, l) => sum + Number(l.qty),
                0
              );
              const canDispatch = totalDispatchQty > 0;

              return (
                <div
                  key={order.id}
                  className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 shadow-md transition-shadow hover:shadow-lg"
                >
                  {/* Card Header */}
                  <div className="border-b border-slate-800/90 bg-slate-950/40 p-4 sm:p-5">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <Link
                            to={`/selling/sales-orders/${order.id}`}
                            className="group inline-flex items-center gap-1.5 text-base font-bold text-slate-100 hover:text-indigo-400 sm:text-lg"
                          >
                            <span>{order.name}</span>
                            <ExternalLink className="h-3.5 w-3.5 text-slate-500 transition group-hover:text-indigo-400" />
                          </Link>

                          {/* Status Badge */}
                          <span
                            className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
                              order.dispatch_status === "Part Dispatched"
                                ? "border border-sky-800/70 bg-sky-950/60 text-sky-300"
                                : "border border-amber-800/70 bg-amber-950/60 text-amber-300"
                            }`}
                          >
                            {order.dispatch_status === "Part Dispatched"
                              ? t("pending_orders.status_part_dispatched")
                              : t("pending_orders.status_pending")}
                          </span>
                        </div>

                        {/* Customer & Date */}
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400 sm:text-sm">
                          <span className="font-medium text-slate-200">
                            {order.customer_name}
                          </span>
                          <span className="flex items-center gap-1 text-slate-400">
                            <Clock className="h-3.5 w-3.5 text-slate-500" />
                            <span>
                              {t("pending_orders.order_date")}:{" "}
                              {formatDate(order.transaction_date)}
                            </span>
                          </span>
                        </div>
                      </div>

                      {/* Line Count */}
                      <span className="self-start rounded-lg border border-slate-800 bg-slate-800/50 px-2.5 py-1 text-xs font-medium text-slate-400">
                        {t("pending_orders.line_count", {
                          count: order.lines.length,
                        })}
                      </span>
                    </div>
                  </div>

                  {/* Line Items */}
                  <div className="divide-y divide-slate-800/70 p-2 sm:p-4">
                    {order.lines.map((line) => {
                      const rem = Number(line.remaining_qty);
                      const ready = Number(line.ready_qty);
                      const short = Number(line.short_qty);
                      const inputVal = getLineQty(order.id, line);

                      // Ready Status Indicator
                      const isFullyReady = ready >= rem && rem > 0;
                      const isPartial = ready > 0 && ready < rem;

                      return (
                        <div
                          key={line.order_item_id}
                          className="flex flex-col gap-4 p-3 transition hover:bg-slate-800/30 sm:flex-row sm:items-center sm:justify-between sm:gap-6 sm:p-4"
                        >
                          {/* Item Info */}
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="font-semibold text-slate-100">
                                {line.item_name}
                              </span>
                              <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-mono text-slate-400">
                                {line.item_code}
                              </span>
                            </div>

                            {line.warehouse_name && (
                              <div className="mt-1 flex items-center gap-1 text-[11px] text-slate-400">
                                <Warehouse className="h-3 w-3 text-slate-500" />
                                <span>{line.warehouse_name}</span>
                              </div>
                            )}

                            {/* Qty Indicators */}
                            <div className="mt-2.5 flex flex-wrap items-center gap-2 text-xs">
                              {/* Remaining */}
                              <span className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-800/60 px-2 py-0.5 text-slate-300">
                                <span className="text-slate-400">
                                  {t("pending_orders.remaining")}:
                                </span>
                                <strong className="font-bold text-slate-100">
                                  {formatNumber(line.remaining_qty)}
                                </strong>
                              </span>

                              {/* Ready Qty (Green if >= remaining, Yellow/Amber if partial, Red if Short) */}
                              <span
                                className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-medium ${
                                  isFullyReady
                                    ? "border-emerald-700 bg-emerald-950/70 text-emerald-300"
                                    : isPartial
                                      ? "border-amber-700 bg-amber-950/70 text-amber-300"
                                      : "border-rose-800 bg-rose-950/70 text-rose-300"
                                }`}
                              >
                                <span>{t("pending_orders.ready_stock")}:</span>
                                <strong className="font-bold">{formatNumber(line.ready_qty)}</strong>
                              </span>

                              {/* Short Qty */}
                              {short > 0 && (
                                <span className="inline-flex items-center gap-1 rounded-md border border-rose-900 bg-rose-950/40 px-2 py-0.5 text-rose-400 font-medium">
                                  <span>{t("pending_orders.short_stock")}:</span>
                                  <strong className="font-bold">{formatNumber(short)}</strong>
                                </span>
                              )}
                            </div>
                          </div>

                          {/* Line Quantity Input & Quick Controls */}
                          <div className="flex flex-col gap-1.5 sm:w-56 sm:shrink-0 sm:items-end">
                            <label className="text-[11px] font-medium text-slate-400 sm:text-end">
                              {t("pending_orders.dispatch_qty")}
                            </label>

                            <div className="flex w-full items-center gap-1.5">
                              <input
                                type="number"
                                min="0"
                                max={Number(line.remaining_qty)}
                                step="any"
                                value={inputVal}
                                onChange={(e) =>
                                  setLineQty(order.id, line.order_item_id, e.target.value)
                                }
                                className="min-h-[44px] w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-end text-sm font-semibold text-slate-100 placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                              />
                            </div>

                            {/* Quick Presets */}
                            <div className="flex flex-wrap items-center gap-1 text-[11px]">
                              <button
                                type="button"
                                onClick={() =>
                                  setLineQty(
                                    order.id,
                                    line.order_item_id,
                                    formatNumber(Math.min(rem, ready))
                                  )
                                }
                                className="rounded px-1.5 py-0.5 text-indigo-400 hover:bg-indigo-950/60 hover:text-indigo-300"
                              >
                                {t("pending_orders.max_ready")}
                              </button>
                              <span className="text-slate-600">·</span>
                              <button
                                type="button"
                                onClick={() =>
                                  setLineQty(
                                    order.id,
                                    line.order_item_id,
                                    formatNumber(rem)
                                  )
                                }
                                className="rounded px-1.5 py-0.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                              >
                                {t("pending_orders.all_remaining")}
                              </button>
                              <span className="text-slate-600">·</span>
                              <button
                                type="button"
                                onClick={() =>
                                  setLineQty(order.id, line.order_item_id, "0")
                                }
                                className="rounded px-1.5 py-0.5 text-slate-500 hover:bg-slate-800 hover:text-rose-400"
                              >
                                {t("pending_orders.zero_qty")}
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {/* Order Card Footer */}
                  <div className="flex flex-col gap-3 border-t border-slate-800 bg-slate-950/30 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5">
                    <div className="text-xs text-slate-400">
                      {canDispatch ? (
                        <span className="text-slate-300">
                          {t("pending_orders.total_dispatch_units", {
                            units: formatNumber(totalDispatchQty),
                          })}
                        </span>
                      ) : (
                        <span className="text-amber-400/90">
                          {t("pending_orders.no_dispatchable_qty")}
                        </span>
                      )}
                    </div>

                    <button
                      type="button"
                      disabled={!canDispatch || dispatchMutation.isPending}
                      onClick={() => handleInitiateDispatch(order)}
                      className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-6 py-2.5 text-sm font-semibold text-white shadow-lg shadow-indigo-600/30 transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
                    >
                      {dispatchMutation.isPending && confirmOrder?.id === order.id ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Truck className="h-4 w-4" />
                      )}
                      <span>
                        {dispatchMutation.isPending && confirmOrder?.id === order.id
                          ? t("pending_orders.dispatching")
                          : t("pending_orders.dispatch_action")}
                      </span>
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Confirmation Modal */}
        <ConfirmDialog
          open={confirmOrder !== null}
          title={t("pending_orders.confirm_title", {
            name: confirmOrder?.name,
          })}
          message={
            <div className="space-y-3 text-sm text-slate-300">
              <p>
                {t("pending_orders.confirm_message", {
                  customer: confirmOrder?.customer_name,
                })}
              </p>
              {confirmOrder && (
                <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                  <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">
                    {t("pending_orders.dispatch_qty")}
                  </div>
                  <div className="space-y-1 text-xs">
                    {getPreparedLines(confirmOrder).map((line) => {
                      const item = confirmOrder.lines.find(
                        (l) => l.order_item_id === line.order_item_id
                      );
                      return (
                        <div
                          key={line.order_item_id}
                          className="flex items-center justify-between text-slate-300"
                        >
                          <span>{item?.item_name || `Line #${line.order_item_id}`}</span>
                          <strong className="font-semibold text-indigo-300">
                            {line.qty}
                          </strong>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          }
          confirmLabel={t("pending_orders.confirm_submit")}
          tone="primary"
          busy={dispatchMutation.isPending}
          onConfirm={handleConfirmDispatch}
          onClose={() => setConfirmOrder(null)}
        />
      </div>
    </Layout>
  );
};
