import { BookText, FileSpreadsheet, FileText } from "lucide-react";
import type { NavLink } from "@/types";

/**
 * Sidebar links to the hand-written GST report pages (src/pages/reports/gst/).
 * The "/reports/" path segment files them under the module's Reports section.
 */
export const gstReportLinks: NavLink[] = [
  { path: "/gst/reports/gstr1", labelKey: "gstreports.nav_gstr1", icon: FileSpreadsheet, hideInSidebar: true },
  { path: "/gst/reports/gstr3b", labelKey: "gstreports.nav_gstr3b", icon: FileText },
  { path: "/gst/reports/tax-ledger", labelKey: "gstreports.nav_tax_ledger", icon: BookText },
];
