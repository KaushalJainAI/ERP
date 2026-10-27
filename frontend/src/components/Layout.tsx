import React from "react";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { useSyncUserLanguage } from "@/hooks/useSyncUserLanguage";

interface LayoutProps {
  children: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  useSyncUserLanguage();
  return (
    <div className="flex min-h-screen bg-slate-950 text-slate-100">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <Header />
        <main className="flex-1 p-6 overflow-y-auto bg-slate-950">{children}</main>
      </div>
    </div>
  );
};
