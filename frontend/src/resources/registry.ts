import {
  BookOpen,
  Boxes,
  FileClock,
  Building2,
  Contact,
  Factory,
  HardDrive,
  History,
  Percent,
  ShoppingBag,
  ShoppingCart,
  Users,
  UserRound,
} from "lucide-react";
import type { ModuleDef, ResourceConfig } from "@/types";
import type { ReportConfig } from "@/types/report";
import { sellingLinks, sellingResources } from "./selling";
import { accountsLinks, accountsResources } from "./accounts";
import { bankLinks, bankResources } from "./bank";
import { buyingResources } from "./buying";
import { gstResources } from "./gstMasters";
import { gstReportLinks } from "./gstReports";
import { stockReports, stockResources } from "./stock";
import { manufacturingReports, manufacturingResources } from "./manufacturing";
import { ageingReports } from "./ageing";
import { usersLinks, usersResources } from "./users";
import { contactsResources } from "./contacts";
import { legacyResources } from "./legacy";
import { tallyExportLinks, tallyExportResources } from "./tallyExport";
import { paymentReconciliationLinks } from "./paymentReconciliation";
import { hrLinks, hrResources } from "./hr";
import { hasReadAccess } from "./workspaces";

/**
 * Central registry. To add a module's documents:
 *   1. write src/resources/<module>.ts exporting ResourceConfig[]
 *   2. spread it into RESOURCES below
 *   3. (first time only) make sure the module exists in MODULES
 * Routes (/<module>/<key>, /new, /:id) and sidebar entries are generated from here.
 */
export const RESOURCES: ResourceConfig[] = [
  ...sellingResources,
  ...stockResources,
  ...manufacturingResources,
  ...buyingResources,
  ...accountsResources,
  ...bankResources,
  ...gstResources,
  ...usersResources,
  ...contactsResources,
  ...legacyResources,
  ...tallyExportResources,
  ...hrResources,
];

/**
 * Sidebar sections, in display order. A module with no resources and no
 * extraLinks is hidden. `extraLinks` points at hand-written (legacy) pages that
 * have not been migrated to the framework yet; remove them as you migrate.
 */
export const MODULES: ModuleDef[] = [
  {
    key: "accounts",
    titleKey: "accounts",
    icon: BookOpen,
    extraLinks: [...accountsLinks, ...bankLinks, ...paymentReconciliationLinks, ...tallyExportLinks],
  },
  {
    key: "stock",
    titleKey: "stock",
    icon: Boxes,
  },
  { key: "selling", titleKey: "selling", icon: ShoppingCart, extraLinks: sellingLinks },
  { key: "buying", titleKey: "buying", icon: ShoppingBag },
  { key: "manufacturing", titleKey: "manufacturing", icon: Factory },
  { key: "contacts", titleKey: "contacts", icon: Contact },
  {
    key: "organisation",
    titleKey: "organisation.title",
    icon: Building2,
    extraLinks: [
      {
        path: "/organisation/company",
        labelKey: "organisation.company",
        icon: Building2,
        visible: (user) => hasReadAccess(user.readable_models, ["core.Company"]),
      },
      { path: "/organisation/drive", labelKey: "organisation.drive", icon: HardDrive },
    ],
  },
  {
    key: "history",
    titleKey: "tally_history",
    icon: History,
    extraLinks: [
      {
        path: "/history/bills",
        labelKey: "pb.nav",
        icon: FileClock,
        visible: (user) => hasReadAccess(user.readable_models, ["legacy.TallyRecord"]),
      },
    ],
  },
  { key: "gst", titleKey: "gst.title", icon: Percent, extraLinks: gstReportLinks },
  { key: "hr", titleKey: "hrm.title", icon: UserRound, extraLinks: hrLinks },
  { key: "users", titleKey: "users", icon: Users, extraLinks: usersLinks },
];

/** Read-only report pages (routes /<module>/reports/<key>, shown in the sidebar). Append other modules' reports here. */
export const REPORTS: ReportConfig[] = [...stockReports, ...manufacturingReports, ...ageingReports];

export const reportsOf = (moduleKey: string) => REPORTS.filter((r) => r.module === moduleKey && !r.hideInSidebar);

export const resourcesOf = (moduleKey: string) =>
  RESOURCES.filter((r) => r.module === moduleKey && !r.hideInSidebar);
