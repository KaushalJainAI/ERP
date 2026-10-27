import React from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import { Login } from "@/pages/Login";
import { Dashboard } from "@/pages/Dashboard";
import { Profile } from "@/pages/Profile";
import { Roles } from "@/pages/Roles";
import { useTranslation } from "react-i18next";
import { GLEntriesPage } from "@/pages/GLEntriesPage";
import { TrialBalancePage } from "@/pages/reports/TrialBalancePage";
import { GeneralLedgerPage } from "@/pages/reports/GeneralLedgerPage";
import { ProfitLossPage } from "@/pages/reports/ProfitLossPage";
import { BalanceSheetPage } from "@/pages/reports/BalanceSheetPage";
import { ListPage } from "@/components/doc/ListPage";
import { DocumentPage } from "@/components/doc/DocumentPage";
import { ReportPage } from "@/components/doc/ReportPage";
import { REPORTS, RESOURCES } from "@/resources/registry";

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { t } = useTranslation();
  const { isAuthenticated, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center text-slate-400 text-sm">
        {t("auth.loading_session")}
      </div>
    );
  }
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
};

export const AppRoutes: React.FC = () => {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute>
            <Dashboard />
          </ProtectedRoute>
        }
      />

      {/* Config-driven resources: /<module>/<key>, /new, /:id (see resources/registry.ts). */}
      {RESOURCES.flatMap((config) => {
        const base = `/${config.module}/${config.key}`;
        const listRoute = (
          <Route
            key={`${base}`}
            path={base}
            element={
              <ProtectedRoute>
                <ListPage key={config.key} config={config} />
              </ProtectedRoute>
            }
          />
        );
        if (config.listOnly) return [listRoute]; // ledgers / projections: no create or detail pages
        return [
          listRoute,
          <Route
            key={`${base}/new`}
            path={`${base}/new`}
            element={
              <ProtectedRoute>
                <DocumentPage key={`${config.key}-new`} config={config} />
              </ProtectedRoute>
            }
          />,
          <Route
            key={`${base}/:id`}
            path={`${base}/:id`}
            element={
              <ProtectedRoute>
                <DocumentPage key={`${config.key}-doc`} config={config} />
              </ProtectedRoute>
            }
          />,
        ];
      })}

      {/* Read-only reports: /<module>/reports/<key> (see ReportPage / resources/registry.ts REPORTS). */}
      {REPORTS.map((report) => (
        <Route
          key={`report-${report.module}-${report.key}`}
          path={`/${report.module}/reports/${report.key}`}
          element={
            <ProtectedRoute>
              <ReportPage key={report.key} config={report} />
            </ProtectedRoute>
          }
        />
      ))}

      {/* Accounts: GL Entries list and the four financial reports (hand-written pages). */}
      <Route
        path="/accounts/gl-entries"
        element={
          <ProtectedRoute>
            <GLEntriesPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/reports/trial-balance"
        element={
          <ProtectedRoute>
            <TrialBalancePage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/reports/general-ledger"
        element={
          <ProtectedRoute>
            <GeneralLedgerPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/reports/profit-and-loss"
        element={
          <ProtectedRoute>
            <ProfitLossPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/reports/balance-sheet"
        element={
          <ProtectedRoute>
            <BalanceSheetPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/users/profile"
        element={
          <ProtectedRoute>
            <Profile />
          </ProtectedRoute>
        }
      />
      <Route
        path="/users/roles"
        element={
          <ProtectedRoute>
            <Roles />
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
};
