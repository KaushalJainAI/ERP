import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router-dom";
import { TopBar, MobileDrawer } from "./TopBar";
import { Sidebar } from "./Sidebar";
import { BottomBar } from "./BottomBar";
import { useSyncUserLanguage } from "@/hooks/useSyncUserLanguage";
import { useActiveModule, useModuleNav } from "@/resources/nav";
import { AssistantLauncher } from "./assistant/AssistantLauncher";

interface LayoutProps {
  children: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  useSyncUserLanguage();
  const { pathname } = useLocation();
  const nav = useModuleNav();
  const active = useActiveModule(nav);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    setMobileMenuOpen(false);
  }, [pathname]);

  // The drawer is a full-screen overlay: don't let the page scroll behind it.
  useEffect(() => {
    if (!mobileMenuOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [mobileMenuOpen]);

  return (
    <div className="min-h-screen flex flex-col bg-slate-950 text-slate-200">
      <TopBar onOpenMenu={() => setMobileMenuOpen(true)} />
      <div className="flex-1 flex min-w-0">
        {active && <Sidebar active={active} />}
        <main className="mx-auto w-full min-w-0 max-w-[1440px] flex-1 px-3 pt-4 pb-[calc(8rem_+_env(safe-area-inset-bottom))] sm:px-6 sm:pt-6 md:pb-6">
          {children}
        </main>
      </div>
      <BottomBar onOpenMenu={() => setMobileMenuOpen(true)} />
      <AssistantLauncher />
      {mobileMenuOpen &&
        createPortal(
          <MobileDrawer nav={nav} onClose={() => setMobileMenuOpen(false)} />,
          document.body
        )}
    </div>
  );
};
