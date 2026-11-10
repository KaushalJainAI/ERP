import React from "react";
import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Home, Mic, MoreHorizontal, Wallet } from "lucide-react";
import { openAssistant } from "@/components/assistant/AssistantLauncher";
import { useAuth } from "@/hooks/useAuth";
import type { WorkspaceType } from "@/types";

/** Where the Money tab goes: the day's money work for this person's workspace. */
function moneyPath(workspace: WorkspaceType | undefined): string {
  if (workspace === "factory") return "/selling/pending-orders";
  if (workspace === "orders") return "/selling/sales-orders";
  return "/accounts/collections";
}

const TAB_BASE =
  "flex min-h-[44px] min-w-0 flex-col items-center justify-center gap-0.5 px-1 py-1 text-center transition-colors";
const TAB_IDLE = "text-slate-400 hover:text-slate-200";
const TAB_ACTIVE = "text-indigo-400 font-semibold";
const LABEL = "max-w-full truncate text-[9px] leading-tight tracking-tight min-[360px]:text-[10px]";

/** Phone tabs: Home, Speak, Money, More. Props are unused now; More opens All modules. */
export const BottomBar: React.FC<{ onOpenMenu?: () => void }> = () => {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const { user } = useAuth();

  const isHome = pathname === "/" || pathname === "/home";
  const money = moneyPath(user?.default_workspace);
  const isMoney = pathname.startsWith(money);
  const isMore = pathname.startsWith("/all-modules");

  return (
    <nav
      aria-label={t("shell.main_navigation")}
      className="md:hidden fixed bottom-0 inset-x-0 z-40 bg-slate-900/95 backdrop-blur-md border-t border-slate-800 pb-[env(safe-area-inset-bottom)]"
    >
      <div className="grid h-16 grid-cols-4 px-1">
        {/* Home */}
        <Link
          to="/"
          aria-current={isHome ? "page" : undefined}
          className={`${TAB_BASE} ${isHome ? TAB_ACTIVE : TAB_IDLE}`}
        >
          <div className="relative">
            <Home className="w-5 h-5" />
            {isHome && <span className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-indigo-500" />}
          </div>
          <span className={LABEL}>{t("home.title")}</span>
        </Link>

        {/* Speak: opens the assistant sheet */}
        <button type="button" onClick={openAssistant} className={`${TAB_BASE} ${TAB_IDLE}`}>
          <div className="relative">
            <Mic className="w-5 h-5" />
          </div>
          <span className={LABEL}>{t("home.bottom.speak")}</span>
        </button>

        {/* Money */}
        <Link
          to={money}
          aria-current={isMoney ? "page" : undefined}
          className={`${TAB_BASE} ${isMoney ? TAB_ACTIVE : TAB_IDLE}`}
        >
          <div className="relative">
            <Wallet className="w-5 h-5" />
            {isMoney && <span className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-indigo-500" />}
          </div>
          <span className={LABEL}>{t("home.bottom.money")}</span>
        </Link>

        {/* More: every module */}
        <Link
          to="/all-modules"
          aria-current={isMore ? "page" : undefined}
          className={`${TAB_BASE} ${isMore ? TAB_ACTIVE : TAB_IDLE}`}
        >
          <div className="relative">
            <MoreHorizontal className="w-5 h-5" />
          </div>
          <span className={LABEL}>{t("home.bottom.more")}</span>
        </Link>
      </div>
    </nav>
  );
};
