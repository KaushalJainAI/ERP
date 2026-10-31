import React from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import { useCompany } from "@/context/CompanyContext";
import { Login } from "@/pages/Login";
import { Home } from "@/pages/Home";
import { Profile } from "@/pages/Profile";
import { Roles } from "@/pages/Roles";
import { useTranslation } from "react-i18next";
import { MusterRollPage } from "@/pages/hr/MusterRollPage";
import { GLEntriesPage } from "@/pages/GLEntriesPage";
import { TallyExportPage } from "@/pages/TallyExportPage";
import { PaymentReconciliationPage } from "@/pages/PaymentReconciliationPage";
import { CompanyPage } from "@/pages/organisation/CompanyPage";
import { DrivePage } from "@/pages/organisation/DrivePage";
import { TrialBalancePage } from "@/pages/reports/TrialBalancePage";
import { GeneralLedgerPage } from "@/pages/reports/GeneralLedgerPage";
import { ProfitLossPage } from "@/pages/reports/ProfitLossPage";
import { BalanceSheetPage } from "@/pages/reports/BalanceSheetPage";
import { BankImportPage } from "@/pages/bank/BankImportPage";
import { BankReconciliationPage } from "@/pages/bank/BankReconciliationPage";
import { BankReconciliationStatementPage } from "@/pages/bank/BankReconciliationStatementPage";
import { Gstr1Page } from "@/pages/reports/gst/Gstr1Page";
import { Gstr3bPage } from "@/pages/reports/gst/Gstr3bPage";
import { TaxLedgerPage } from "@/pages/reports/gst/TaxLedgerPage";
import { SalesRegisterPage, PurchaseRegisterPage } from "@/pages/reports/registers/RegisterPage";
import { DayBookPage } from "@/pages/reports/registers/DayBookPage";
import { CashBookPage } from "@/pages/reports/registers/CashBookPage";
import { PartyStatementPage } from "@/pages/reports/registers/PartyStatementPage";
import { ListPage } from "@/components/doc/ListPage";
import { PrintPage } from "@/print/PrintPage";
import { DocumentPage } from "@/components/doc/DocumentPage";
import { CreateVariantPage } from "@/pages/stock/CreateVariantPage";
import { ReportPage } from "@/components/doc/ReportPage";
import { MODULES, REPORTS, RESOURCES } from "@/resources/registry";
import { ModulePage } from "@/pages/ModulePage";
import { SalesAnalyticsPage } from "@/pages/selling/SalesAnalyticsPage";
import { PendingOrdersPage } from "@/pages/selling/PendingOrdersPage";
import { CollectionsPage } from "@/pages/accounts/CollectionsPage";
import { BillsToPayPage } from "@/pages/accounts/BillsToPayPage";
import { ExpensesPage } from "@/pages/accounts/ExpensesPage";
import { NewExpensePage } from "@/pages/accounts/NewExpensePage";
import { GstPositionPage } from "@/pages/accounts/GstPositionPage";
import { AlertSettingsPage } from "@/pages/notifications/AlertSettingsPage";

const CompanyRequired: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { t } = useTranslation();
  const { activeCompany, loading, error } = useCompany();
  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center text-slate-400 text-sm">
        {t("company.loading")}
      </div>
    );
  }
  if (error) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center text-rose-300 text-sm">
        {t("company.load_failed")}
      </div>
    );
  }
  if (!activeCompany) return <Navigate to="/" replace />;
  return <>{children}</>;
};

const ProtectedRoute: React.FC<{ children: React.ReactNode; requireCompany?: boolean }> = ({
  children,
  requireCompany = true,
}) => {
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
  if (requireCompany) return <CompanyRequired>{children}</CompanyRequired>;
  return <>{children}</>;
};

export const AppRoutes: React.FC = () => {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <ProtectedRoute requireCompany={false}>
            <Home />
          </ProtectedRoute>
        }
      />
      <Route
        path="/home"
        element={
          <ProtectedRoute requireCompany={false}>
            <Home />
          </ProtectedRoute>
        }
      />
      {/* Old bookmarks land on the home launcher. The API endpoint is kept. */}
      <Route path="/dashboard" element={<Navigate to="/" replace />} />

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

      {/* Print formats: a plain A4 view of one document, no app chrome (src/print/). */}
      <Route
        path="/print/:kind/:id"
        element={
          <ProtectedRoute>
            <PrintPage />
          </ProtectedRoute>
        }
      />

      {/* ERPNext "Create Variant" of a template item. */}
      <Route
        path="/stock/items/:id/create-variant"
        element={
          <ProtectedRoute>
            <CreateVariantPage />
          </ProtectedRoute>
        }
      />

      {/* Accounts: Collections, Bills to Pay, GL Entries, etc. */}
      <Route
        path="/accounts/collections"
        element={
          <ProtectedRoute>
            <CollectionsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/bills-to-pay"
        element={
          <ProtectedRoute>
            <BillsToPayPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/expenses"
        element={
          <ProtectedRoute>
            <ExpensesPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/expenses/new"
        element={
          <ProtectedRoute>
            <NewExpensePage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/gst-position"
        element={
          <ProtectedRoute>
            <GstPositionPage />
          </ProtectedRoute>
        }
      />
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
        path="/accounts/payment-reconciliation"
        element={
          <ProtectedRoute>
            <PaymentReconciliationPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/tally-export"
        element={
          <ProtectedRoute>
            <TallyExportPage />
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
      {/* Registers and party statement (pages/reports/registers/). */}
      <Route
        path="/accounts/reports/sales-register"
        element={
          <ProtectedRoute>
            <SalesRegisterPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/reports/purchase-register"
        element={
          <ProtectedRoute>
            <PurchaseRegisterPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/reports/day-book"
        element={
          <ProtectedRoute>
            <DayBookPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/reports/cash-book"
        element={
          <ProtectedRoute>
            <CashBookPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/reports/party-statement"
        element={
          <ProtectedRoute>
            <PartyStatementPage />
          </ProtectedRoute>
        }
      />
      {/* GST reports (hand-written pages, see pages/reports/gst/). */}
      <Route
        path="/gst/reports/gstr1"
        element={
          <ProtectedRoute>
            <Gstr1Page />
          </ProtectedRoute>
        }
      />
      <Route
        path="/gst/reports/gstr3b"
        element={
          <ProtectedRoute>
            <Gstr3bPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/gst/reports/tax-ledger"
        element={
          <ProtectedRoute>
            <TaxLedgerPage />
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
      {/* Bank reconciliation: statement import, two-pane tool and the BRS report. */}
      <Route
        path="/accounts/bank-import"
        element={
          <ProtectedRoute>
            <BankImportPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/bank-reconciliation"
        element={
          <ProtectedRoute>
            <BankReconciliationPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts/reports/bank-reconciliation-statement"
        element={
          <ProtectedRoute>
            <BankReconciliationStatementPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/organisation/company"
        element={
          <ProtectedRoute>
            <CompanyPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/organisation/drive"
        element={
          <ProtectedRoute>
            <DrivePage />
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
      <Route
        path="/hr/muster-roll"
        element={
          <ProtectedRoute>
            <MusterRollPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/selling/pending-orders"
        element={
          <ProtectedRoute>
            <PendingOrdersPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/selling/analytics"
        element={
          <ProtectedRoute>
            <SalesAnalyticsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/settings/alerts"
        element={
          <ProtectedRoute>
            <AlertSettingsPage />
          </ProtectedRoute>
        }
      />
      {/* Module landing pages: /accounts, /stock, ... (one per MODULES entry). */}
      {MODULES.map((m) => (
        <Route
          key={`module-${m.key}`}
          path={`/${m.key}`}
          element={
            <ProtectedRoute>
              <ModulePage key={m.key} moduleKey={m.key} />
            </ProtectedRoute>
          }
        />
      ))}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
};
