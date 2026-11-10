import React, { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Loader2, Phone, Plus } from "lucide-react";
import { Layout } from "@/components/Layout";
import { RecordMoneySheet } from "@/components/accounts/RecordMoneySheet";
import { apiClient } from "@/api/client";
import { useCompany } from "@/context/CompanyContext";
import { formatDate, formatIndianCurrency, todayISO } from "@/lib/format";

type PartyKind = "customer" | "supplier";

interface PartyMaster {
  id: number;
  name: string;
  phone?: string | null;
}

interface StatementSummary {
  closing_balance: string;
  closing_dr_cr: string;
}

interface OpenBill {
  invoice?: string;
  name?: string;
  bill_no?: string | null;
  due_date: string | null;
  outstanding_amount: string;
  is_overdue?: boolean;
}

interface PaymentRow {
  id: number;
  name: string;
  posting_date: string;
  paid_amount: string;
  mode: string;
  party_name: string;
  party_type: string;
  docstatus: number;
}

interface OrderRow {
  id: number;
  name: string;
  transaction_date: string;
  grand_total: string;
  docstatus: number;
}

/** A list endpoint answers with a page ({results}) or a plain array. */
const rowsOf = <T,>(data: any): T[] => (Array.isArray(data) ? data : data?.results ?? []);

const DOC_STATUS_KEYS = ["draft", "submitted", "cancelled"] as const;

/**
 * One party (customer or supplier): balance, open bills, recent payments, the last five
 * orders, with Call and Record Money. Every figure comes from an existing endpoint; the
 * server computes the balance (party statement) and the bill outstanding.
 */
export const PartyPage: React.FC = () => {
  const { kind: rawKind, id: rawId } = useParams();
  const { t } = useTranslation();
  const { activeCompany } = useCompany();
  const queryClient = useQueryClient();
  const [sheetOpen, setSheetOpen] = useState(false);

  const kind: PartyKind | null = rawKind === "customer" || rawKind === "supplier" ? rawKind : null;
  const partyId = rawId && /^\d+$/.test(rawId) ? Number(rawId) : null;
  const valid = kind !== null && partyId !== null;
  const isCustomer = kind === "customer";
  const partyType = isCustomer ? "Customer" : "Supplier";
  const companyName = activeCompany?.name;
  const companyId = activeCompany?.id;
  const today = todayISO();
  const keyBase = ["party-page", kind, partyId, companyId];

  const master = useQuery<PartyMaster>({
    queryKey: [...keyBase, "master"],
    enabled: valid && !!activeCompany,
    queryFn: async () => {
      const path = isCustomer ? "/selling/customers/" : "/buying/suppliers/";
      const res = await apiClient.get(`${path}${partyId}/`);
      return res.data;
    },
  });
  const party = master.data;

  const balance = useQuery<StatementSummary>({
    queryKey: [...keyBase, "balance"],
    enabled: valid && !!party,
    queryFn: async () => {
      const res = await apiClient.get("/accounts/reports/party-statement/", {
        params: {
          company: companyName,
          party_type: partyType,
          party: partyId,
          from_date: "2000-01-01",
          to_date: today,
          page_size: 1,
        },
      });
      return res.data;
    },
  });

  const bills = useQuery<OpenBill[]>({
    queryKey: [...keyBase, "bills"],
    enabled: valid && !!party,
    queryFn: async () => {
      if (isCustomer) {
        const res = await apiClient.get("/accounts/collections/", {
          params: { company: companyName, as_on: today },
        });
        const row = (res.data?.customers ?? []).find((c: any) => c.customer_id === partyId);
        return row?.bills ?? [];
      }
      const res = await apiClient.get("/accounts/bills-to-pay/", {
        params: { company: companyName, as_on: today },
      });
      return (res.data?.bills ?? []).filter((b: any) => b.supplier_id === partyId);
    },
  });

  const payments = useQuery<PaymentRow[]>({
    queryKey: [...keyBase, "payments"],
    enabled: valid && !!party,
    queryFn: async () => {
      // The endpoint searches by name; keep only this party's own entries.
      const res = await apiClient.get("/accounts/payment-entries/", {
        params: { company: companyId, search: party?.name, ordering: "-posting_date", page_size: 20 },
      });
      return rowsOf<PaymentRow>(res.data)
        .filter((p) => p.party_name === party?.name && p.party_type === partyType && p.docstatus !== 2)
        .slice(0, 5);
    },
  });

  const orders = useQuery<OrderRow[]>({
    queryKey: [...keyBase, "orders"],
    enabled: valid && !!party,
    queryFn: async () => {
      const path = isCustomer ? "/selling/sales-orders/" : "/buying/purchase-orders/";
      const filter = isCustomer ? { customer: partyId } : { supplier: partyId };
      const res = await apiClient.get(path, {
        params: { company: companyId, ...filter, ordering: "-transaction_date", page_size: 5 },
      });
      return rowsOf<OrderRow>(res.data).slice(0, 5);
    },
  });

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["party-page", kind, partyId] });
  };

  if (!valid) {
    return (
      <Layout>
        <p role="alert" className="text-sm text-rose-300">
          {t("ease.party.not_found")}
        </p>
      </Layout>
    );
  }

  const notFound = (master.error as any)?.response?.status === 404;
  const phoneDigits = party?.phone ? party.phone.replace(/[^\d+]/g, "") : "";
  const statement = balance.data;

  return (
    <Layout>
      <div className="mx-auto max-w-2xl space-y-5">
        {master.isLoading ? (
          <p className="flex items-center gap-2 text-sm text-slate-400">
            <Loader2 className="w-4 h-4 animate-spin" />
          </p>
        ) : master.error ? (
          <p role="alert" className="text-sm text-rose-300">
            {notFound ? t("ease.party.not_found") : t("ease.party.load_failed")}
          </p>
        ) : party ? (
          <>
            {/* Name, kind and phone */}
            <div className="space-y-1">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                {t(`ease.party.${kind}`)}
              </p>
              <h1 className="text-2xl font-bold text-white tracking-tight">{party.name}</h1>
              <p className="text-sm text-slate-400">
                {party.phone ? (
                  <>
                    {t("ease.party.phone")}: <span className="text-slate-200">{party.phone}</span>
                  </>
                ) : (
                  t("ease.party.no_phone")
                )}
              </p>
            </div>

            {/* Actions: a thumb-sized Call and Record Money */}
            <div className="grid grid-cols-2 gap-3">
              {phoneDigits ? (
                <a
                  href={`tel:${phoneDigits}`}
                  className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-4 text-sm font-semibold text-slate-100 hover:bg-slate-800"
                >
                  <Phone className="w-4 h-4" /> {t("ease.party.call")}
                </a>
              ) : (
                <span />
              )}
              <button
                type="button"
                onClick={() => setSheetOpen(true)}
                className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 text-sm font-semibold text-white hover:bg-emerald-500"
              >
                <Plus className="w-4 h-4" /> {t("ease.party.record_money")}
              </button>
            </div>

            {/* Balance: computed by the server from the party's ledger */}
            <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
              <p className="text-xs text-slate-400">{t("ease.party.balance")}</p>
              <p className="mt-1 text-2xl font-bold text-white">
                {statement
                  ? `${formatIndianCurrency(Number(statement.closing_balance))} ${statement.closing_dr_cr}`
                  : balance.isError
                    ? "-"
                    : "..."}
              </p>
            </div>

            {/* Open bills */}
            <section className="space-y-2">
              <h2 className="text-sm font-semibold text-slate-200">{t("ease.party.open_bills")}</h2>
              {bills.data && bills.data.length === 0 && (
                <p className="text-sm text-slate-500">{t("ease.party.no_open_bills")}</p>
              )}
              {(bills.data ?? []).map((bill, index) => (
                <div
                  key={`${bill.invoice ?? bill.name ?? bill.bill_no ?? index}`}
                  className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2.5 text-sm"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium text-slate-100">{bill.invoice ?? bill.name ?? bill.bill_no}</p>
                    <p className="text-xs text-slate-500">
                      {t("ease.party.due")}: {bill.due_date ? formatDate(bill.due_date) : "-"}
                    </p>
                  </div>
                  <span className={`shrink-0 font-semibold ${bill.is_overdue ? "text-rose-300" : "text-slate-100"}`}>
                    {formatIndianCurrency(Number(bill.outstanding_amount))}
                  </span>
                </div>
              ))}
            </section>

            {/* Recent payments */}
            <section className="space-y-2">
              <h2 className="text-sm font-semibold text-slate-200">{t("ease.party.recent_payments")}</h2>
              {payments.data && payments.data.length === 0 && (
                <p className="text-sm text-slate-500">{t("ease.party.no_payments")}</p>
              )}
              {(payments.data ?? []).map((p) => (
                <div
                  key={p.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2.5 text-sm"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium text-slate-100">{p.name}</p>
                    <p className="text-xs text-slate-500">
                      {formatDate(p.posting_date)} · {p.mode}
                    </p>
                  </div>
                  <span className="shrink-0 font-semibold text-slate-100">
                    {formatIndianCurrency(Number(p.paid_amount))}
                  </span>
                </div>
              ))}
            </section>

            {/* Last five orders */}
            <section className="space-y-2">
              <h2 className="text-sm font-semibold text-slate-200">{t("ease.party.last_orders")}</h2>
              {orders.data && orders.data.length === 0 && (
                <p className="text-sm text-slate-500">{t("ease.party.no_orders")}</p>
              )}
              {(orders.data ?? []).map((o) => (
                <div
                  key={o.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2.5 text-sm"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium text-slate-100">{o.name}</p>
                    <p className="text-xs text-slate-500">
                      {formatDate(o.transaction_date)} ·{" "}
                      {t(`ease.party.${DOC_STATUS_KEYS[o.docstatus] ?? "draft"}`)}
                    </p>
                  </div>
                  <span className="shrink-0 font-semibold text-slate-100">
                    {formatIndianCurrency(Number(o.grand_total))}
                  </span>
                </div>
              ))}
            </section>

            <Link
              to={isCustomer ? "/accounts/collections" : "/accounts/bills-to-pay"}
              className="inline-flex min-h-[44px] items-center text-sm text-indigo-300 hover:text-indigo-200"
            >
              {isCustomer
                ? t("acc.collections.title", { defaultValue: "Money to Collect" })
                : t("acc.bills_to_pay.title", { defaultValue: "Bills to Pay" })}
            </Link>
          </>
        ) : null}
      </div>

      {party && (
        <RecordMoneySheet
          open={sheetOpen}
          onClose={() => setSheetOpen(false)}
          defaultMode={isCustomer ? "Receive" : "Pay"}
          defaultPartyType={partyType}
          defaultParty={party.name}
          onSuccess={refreshAll}
        />
      )}
    </Layout>
  );
};
