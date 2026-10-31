import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CalendarDays, ClipboardCheck, Mic, ShoppingCart } from "lucide-react";
import { apiClient } from "@/api/client";
import { useCompany } from "@/context/CompanyContext";
import { openAssistant } from "@/components/assistant/AssistantLauncher";
import { todayISO } from "@/lib/format";
import type { Paginated } from "@/types";
import {
  EmptyWorkspace,
  Section,
  Tile,
  TileGrid,
  WorkspaceFrame,
  countText,
  useCanRead,
  useDashboardCounts,
} from "./WorkspaceShell";

/** Orders workspace panels: the voice button and today's order counts. */
export const OrdersPanels: React.FC<{ title?: string }> = ({ title }) => {
  const { t } = useTranslation();
  const { activeCompany } = useCompany();
  const canOrders = useCanRead(["selling.SalesOrder"]);
  const today = todayISO();

  const todayQuery = useQuery({
    queryKey: ["home", "orders", "today", activeCompany?.id ?? null, today],
    queryFn: async () =>
      (
        await apiClient.get<Paginated<unknown>>("/selling/sales-orders/", {
          params: { transaction_date: today },
        })
      ).data.count,
    enabled: !!activeCompany && canOrders,
    staleTime: 30_000,
    retry: false,
  });

  const dashboard = useDashboardCounts(canOrders);
  const drafts = dashboard.data?.draft_documents?.sales_orders;
  const openOrders = dashboard.data?.open_sales_orders?.count;

  return (
    <Section title={title}>
      <button
        type="button"
        onClick={openAssistant}
        className="flex min-h-[120px] w-full items-center gap-4 rounded-2xl bg-gradient-to-r from-indigo-600 to-indigo-500 p-5 text-start text-white shadow-lg shadow-indigo-600/20 transition-colors hover:from-indigo-500 hover:to-indigo-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300"
      >
        <span className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-white/20">
          <Mic className="h-8 w-8" />
        </span>
        <span className="min-w-0">
          <span className="block text-lg font-semibold">{t("home.ws.orders.speak")}</span>
          <span className="mt-0.5 block text-sm text-indigo-100">{t("home.ws.orders.speak_hint")}</span>
        </span>
      </button>

      {canOrders ? (
        <TileGrid>
          <Tile
            to="/selling/sales-orders"
            icon={CalendarDays}
            label={t("home.ws.orders.today")}
            value={countText(todayQuery)}
            hint={t("home.ws.orders.today_hint")}
          />
          <Tile
            to="/selling/sales-orders?docstatus=0"
            icon={ClipboardCheck}
            label={t("plain.approve")}
            value={countText({ isLoading: dashboard.isLoading, isError: dashboard.isError, data: drafts })}
            hint={t("home.ws.orders.drafts_hint")}
          />
          <Tile
            to="/selling/sales-orders?docstatus=1"
            icon={ShoppingCart}
            label={t("home.ws.orders.open")}
            value={countText({ isLoading: dashboard.isLoading, isError: dashboard.isError, data: openOrders })}
            hint={t("home.ws.orders.open_hint")}
          />
        </TileGrid>
      ) : (
        <EmptyWorkspace />
      )}
    </Section>
  );
};

export const OrdersHome: React.FC = () => {
  const { t } = useTranslation();
  return (
    <WorkspaceFrame title={t("home.ws.orders.title")} subtitle={t("home.ws.orders.subtitle")}>
      <OrdersPanels />
    </WorkspaceFrame>
  );
};
