import React from "react";
import { useAuth } from "@/hooks/useAuth";
import type { WorkspaceType } from "@/types";
import { CompanyGate } from "./workspaces/WorkspaceShell";
import { OrdersHome } from "./workspaces/OrdersHome";
import { FactoryHome } from "./workspaces/FactoryHome";
import { AccountsHome } from "./workspaces/AccountsHome";
import { AdminHome } from "./workspaces/AdminHome";

/** Each user's landing page, picked by `default_workspace`. It only chooses the
 * screen; access still comes from the user's roles. */
const WORKSPACE_HOME: Record<WorkspaceType, React.FC> = {
  orders: OrdersHome,
  factory: FactoryHome,
  accounts: AccountsHome,
  admin: AdminHome,
};

export const Home: React.FC = () => {
  const { user } = useAuth();
  const Page = WORKSPACE_HOME[user?.default_workspace ?? "admin"] ?? AdminHome;
  return (
    <CompanyGate>
      <Page />
    </CompanyGate>
  );
};
