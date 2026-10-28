import React from "react";
import { TopBar } from "./TopBar";
import { useSyncUserLanguage } from "@/hooks/useSyncUserLanguage";

interface LayoutProps {
  children: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  useSyncUserLanguage();
  return (
    <div className="min-h-screen flex flex-col bg-slate-950 text-slate-200">
      <TopBar />
      <main className="flex-1 w-full mx-auto max-w-[1440px] px-4 sm:px-6 py-6">{children}</main>
    </div>
  );
};
