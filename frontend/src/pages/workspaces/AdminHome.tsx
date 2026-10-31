import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Bell, Building2, Users } from "lucide-react";
import { apiClient } from "@/api/client";
import { Section, Tile, TileGrid, WorkspaceFrame, countText, useCanRead } from "./WorkspaceShell";
import { OrdersPanels } from "./OrdersHome";
import { FactoryPanels } from "./FactoryHome";
import { AccountsPanels } from "./AccountsHome";

/** Admin tools: unread reminders, user management and company details. */
const AdminPanels: React.FC<{ title?: string }> = ({ title }) => {
  const { t } = useTranslation();
  const canUsers = useCanRead(["users.User"]);
  const canCompany = useCanRead(["core.Company"]);

  // Same endpoint and key as the bell in the top bar, so the count is shared.
  const unread = useQuery({
    queryKey: ["notifications", "unread-count"],
    queryFn: async () => (await apiClient.get<{ count: number }>("/notifications/unread-count/")).data,
    staleTime: 30_000,
    retry: false,
  });

  return (
    <Section title={title}>
      <TileGrid>
        <Tile
          to="/settings/alerts"
          icon={Bell}
          label={t("home.ws.admin.reminders")}
          value={countText({ isLoading: unread.isLoading, isError: unread.isError, data: unread.data?.count })}
          hint={t("home.ws.admin.reminders_hint")}
        />
        {canUsers && <Tile to="/users/users" icon={Users} label={t("usermgmt.nav")} />}
        {canCompany && <Tile to="/organisation/company" icon={Building2} label={t("organisation.company")} />}
      </TileGrid>
    </Section>
  );
};

export const AdminHome: React.FC = () => {
  const { t } = useTranslation();
  return (
    <WorkspaceFrame title={t("home.admin_title")} subtitle={t("home.ws.admin.subtitle")}>
      <OrdersPanels title={t("home.ws.orders.title")} />
      <FactoryPanels title={t("home.ws.factory.title")} />
      <AccountsPanels title={t("home.ws.accounts.title")} />
      <AdminPanels title={t("home.ws.admin.title")} />
    </WorkspaceFrame>
  );
};
