import React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/hooks/useAuth";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { LogOut, User as UserIcon } from "lucide-react";

export const Header: React.FC = () => {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const displayName = [user?.first_name, user?.last_name].filter(Boolean).join(" ") || user?.username;

  return (
    <header className="h-16 bg-slate-950/80 backdrop-blur-md border-b border-slate-800 px-6 flex items-center justify-end sticky top-0 z-40">
      <div className="flex items-center gap-4">
        <LanguageSwitcher />

        {user && (
          <div className="flex items-center gap-3 ps-2 border-s border-slate-800">
            <Link
              to="/users/profile"
              title={t("shell.my_profile")}
              className="flex items-center gap-3 rounded-lg hover:bg-slate-900 px-1.5 py-1 transition-colors"
            >
              <div className="w-8 h-8 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center text-slate-300 font-bold text-xs">
                {displayName ? displayName[0].toUpperCase() : <UserIcon className="w-4 h-4 text-slate-400" />}
              </div>
              <div className="text-xs text-start">
                <p className="font-semibold text-slate-200">{displayName}</p>
                <p className="text-slate-400 text-[10px]">{user.email}</p>
              </div>
            </Link>
            <button
              type="button"
              onClick={logout}
              className="p-1.5 rounded-lg text-slate-400 hover:text-rose-400 hover:bg-slate-900 transition-colors"
              title={t("shell.sign_out")}
              aria-label={t("shell.sign_out")}
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>
    </header>
  );
};
