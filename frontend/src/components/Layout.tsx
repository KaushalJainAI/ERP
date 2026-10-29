import React from "react";
import { TopBar } from "./TopBar";
import { Sidebar } from "./Sidebar";
import { useSyncUserLanguage } from "@/hooks/useSyncUserLanguage";
import { useActiveModule, useModuleNav } from "@/resources/nav";

interface LayoutProps {
  children: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  useSyncUserLanguage();
  const active = useActiveModule(useModuleNav());
  return (
    <div className="min-h-screen flex flex-col bg-slate-950 text-slate-200">
      <TopBar />
      <div className="flex-1 flex min-w-0">
        {active && <Sidebar active={active} />}
        <main className="flex-1 min-w-0 w-full mx-auto max-w-[1440px] px-4 sm:px-6 py-6">{children}</main>
      </div>
    </div>
  );
};
