import { Scale } from "lucide-react";
import type { NavLink } from "@/types";

/** Sidebar link to the hand-written Payment Reconciliation page (under Accounts). */
export const paymentReconciliationLinks: NavLink[] = [
  { path: "/accounts/payment-reconciliation", labelKey: "payrecon.nav", icon: Scale },
];
