import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { BookOpen, FileText, Percent, Receipt, Wallet } from "lucide-react";
import { apiClient } from "@/api/client";
import { useCompany } from "@/context/CompanyContext";
import { formatDate, formatIndianCurrency, todayISO } from "@/lib/format";
import { EmptyWorkspace, Section, Tile, TileGrid, WorkspaceFrame, useCanRead } from "./WorkspaceShell";

interface CollectionsTotals {
  total_receivables: string;
  total_overdue: string;
}

interface BillsTotals {
  total_payables: string;
  total_overdue: string;
}

interface ExpenseSummary {
  grand_totals: { total: string; paid: string; due: string };
}

interface GstPosition {
  net_payable: string;
  status: "Paid" | "Due";
  due_date: string;
}

/** Accounts workspace panels: money in and out, this month's expenses, GST due. TDS is not built yet. */
export const AccountsPanels: React.FC<{ title?: string }> = ({ title }) => {
  const { t } = useTranslation();
  const { activeCompany } = useCompany();
  const canGl = useCanRead(["accounts.GLEntry"]);
  const canExpenses = useCanRead(["buying.PurchaseInvoice", "accounts.GLEntry"]);
  const today = todayISO();
  const month = today.slice(0, 7);
  const companyName = activeCompany?.name;
  const companyEnabled = !!activeCompany && canGl;

  const collectionsQuery = useQuery({
    queryKey: ["home", "accounts", "collections", companyName ?? null, today],
    queryFn: async () =>
      (
        await apiClient.get<{ totals: CollectionsTotals }>("/accounts/collections/", {
          params: { company: companyName, as_on: today },
        })
      ).data.totals,
    enabled: companyEnabled,
    staleTime: 30_000,
    retry: false,
  });

  const billsQuery = useQuery({
    queryKey: ["home", "accounts", "bills-to-pay", companyName ?? null, today],
    queryFn: async () =>
      (
        await apiClient.get<{ totals: BillsTotals }>("/accounts/bills-to-pay/", {
          params: { company: companyName, as_on: today },
        })
      ).data.totals,
    enabled: companyEnabled,
    staleTime: 30_000,
    retry: false,
  });

  const expenseQuery = useQuery({
    queryKey: ["home", "accounts", "expenses", month],
    queryFn: async () =>
      (
        await apiClient.get<ExpenseSummary>("/accounts/reports/expense-summary/", {
          params: { from_date: `${month}-01`, to_date: today },
        })
      ).data.grand_totals,
    enabled: !!activeCompany && canExpenses,
    staleTime: 30_000,
    retry: false,
  });

  const gstQuery = useQuery({
    queryKey: ["home", "accounts", "gst-position", companyName ?? null, month],
    queryFn: async () =>
      (
        await apiClient.get<GstPosition>("/accounts/reports/gst-position/", {
          params: { company: companyName, month },
        })
      ).data,
    enabled: companyEnabled,
    staleTime: 30_000,
    retry: false,
  });

  const amount = (value: string | undefined): string | undefined =>
    value === undefined ? undefined : formatIndianCurrency(value);

  const collections = collectionsQuery.data;
  const bills = billsQuery.data;
  const expenses = expenseQuery.data;
  const gst = gstQuery.data;

  if (!canGl && !canExpenses) {
    return (
      <Section title={title}>
        <EmptyWorkspace />
      </Section>
    );
  }

  return (
    <Section title={title}>
      <TileGrid>
        {canGl && (
          <Tile
            to="/accounts/collections"
            icon={Wallet}
            label={t("acc.nav.collections")}
            value={amount(collections?.total_receivables) ?? (collectionsQuery.isError ? "–" : "…")}
            hint={
              collections
                ? t("home.ws.accounts.overdue", { amount: amount(collections.total_overdue) })
                : undefined
            }
          />
        )}
        {canGl && (
          <Tile
            to="/accounts/bills-to-pay"
            icon={Receipt}
            label={t("acc.nav.bills_to_pay")}
            value={amount(bills?.total_payables) ?? (billsQuery.isError ? "–" : "…")}
            hint={bills ? t("home.ws.accounts.overdue", { amount: amount(bills.total_overdue) }) : undefined}
          />
        )}
        {canExpenses && (
          <Tile
            to="/accounts/expenses"
            icon={FileText}
            label={t("acc.nav.expenses")}
            value={amount(expenses?.total) ?? (expenseQuery.isError ? "–" : "…")}
            hint={expenses ? t("home.ws.accounts.due", { amount: amount(expenses.due) }) : undefined}
          />
        )}
        {canGl && (
          <Tile
            to="/accounts/gst-position"
            icon={Percent}
            label={t("acc.nav.gst_position")}
            value={amount(gst?.net_payable) ?? (gstQuery.isError ? "–" : "…")}
            hint={
              gst
                ? t("home.ws.accounts.gst_due", {
                    status: gst.status === "Paid" ? t("plain.paid") : t("plain.due"),
                    date: formatDate(gst.due_date),
                  })
                : undefined
            }
          />
        )}
        <Tile to="/accounts" icon={BookOpen} label={t("acc.nav.advanced")} />
      </TileGrid>
    </Section>
  );
};

export const AccountsHome: React.FC = () => {
  const { t } = useTranslation();
  return (
    <WorkspaceFrame title={t("home.ws.accounts.title")} subtitle={t("home.ws.accounts.subtitle")}>
      <AccountsPanels />
    </WorkspaceFrame>
  );
};
