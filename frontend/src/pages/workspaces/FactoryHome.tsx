import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Factory, Truck, Users, Warehouse } from "lucide-react";
import { apiClient } from "@/api/client";
import { listAllRecords } from "@/api/endpoints";
import { useCompany } from "@/context/CompanyContext";
import { EmptyWorkspace, Section, Tile, TileGrid, WorkspaceFrame, countText, useCanRead } from "./WorkspaceShell";

/** Factory workspace panels: dispatch, batches in progress, ready stock and workers. */
export const FactoryPanels: React.FC<{ title?: string }> = ({ title }) => {
  const { t } = useTranslation();
  const { activeCompany } = useCompany();
  const canSell = useCanRead(["selling.SalesOrder"]);
  const canBatches = useCanRead(["manufacturing.WorkOrder"]);
  const canStock = useCanRead(["stock.Bin"]);
  const canHr = useCanRead(["hr.Employee"]);
  const companyId = activeCompany?.id ?? null;

  const pendingQuery = useQuery({
    queryKey: ["home", "factory", "pending-dispatch", companyId],
    queryFn: async () => (await apiClient.get<unknown[]>("/selling/sales-orders/pending/")).data.length,
    enabled: !!activeCompany && canSell,
    staleTime: 30_000,
    retry: false,
  });

  const batchesQuery = useQuery({
    queryKey: ["home", "factory", "batches-in-progress", companyId],
    queryFn: async () => {
      const rows = (await listAllRecords("/manufacturing/batches/")) as { status: string }[];
      return rows.filter((row) => row.status === "Grinding" || row.status === "Packing").length;
    },
    enabled: !!activeCompany && canBatches,
    staleTime: 30_000,
    retry: false,
  });

  const readyQuery = useQuery({
    queryKey: ["home", "factory", "ready-stock", companyId],
    queryFn: async () => {
      const res = await apiClient.get<{ rows: { ready_qty: string }[] }>("/stock/reports/stages/", {
        params: { company: companyId },
      });
      return res.data.rows.filter((row) => Number(row.ready_qty) > 0).length;
    },
    enabled: !!activeCompany && canStock,
    staleTime: 30_000,
    retry: false,
  });

  if (!canSell && !canBatches && !canStock && !canHr) {
    return (
      <Section title={title}>
        <EmptyWorkspace />
      </Section>
    );
  }

  return (
    <Section title={title}>
      <TileGrid>
        {canSell && (
          <Tile
            to="/selling/pending-orders"
            icon={Truck}
            label={t("plain.dispatch")}
            value={countText(pendingQuery)}
            hint={t("home.ws.factory.dispatch_hint")}
          />
        )}
        {canBatches && (
          <Tile
            to="/manufacturing/batches"
            icon={Factory}
            label={t("batches.nav_title")}
            value={countText(batchesQuery)}
            hint={t("home.ws.factory.batches_hint")}
          />
        )}
        {canStock && (
          <Tile
            to="/stock/reports/stages"
            icon={Warehouse}
            label={t("home.ws.factory.ready")}
            value={countText(readyQuery)}
            hint={t("home.ws.factory.ready_hint")}
          />
        )}
        {canHr && (
          <Tile
            to="/hr/employees"
            icon={Users}
            label={t("home.ws.factory.workers")}
            hint={t("home.ws.factory.workers_hint")}
          />
        )}
      </TileGrid>
    </Section>
  );
};

export const FactoryHome: React.FC = () => {
  const { t } = useTranslation();
  return (
    <WorkspaceFrame title={t("home.ws.factory.title")} subtitle={t("home.ws.factory.subtitle")}>
      <FactoryPanels />
    </WorkspaceFrame>
  );
};
