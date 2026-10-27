import { ShieldCheck, UserCircle } from "lucide-react";
import type { NavLink, UserProfile } from "@/types";

/**
 * Hand-written pages of the "Users & Security" sidebar section. Only pages the
 * API supports are listed (there is no user-list/management endpoint yet).
 */

/**
 * The Roles page reads /auth/roles/ and /auth/permissions/, which are RBAC-gated
 * on users.Role / users.RolePermission (seeded System Manager, or superuser).
 * Hide the link from everyone else rather than showing a page that 403s.
 */
export const canViewRoles = (user: UserProfile): boolean =>
  !!user.is_superuser || user.roles.includes("System Manager");

export const usersLinks: NavLink[] = [
  { path: "/users/profile", labelKey: "profile.nav", icon: UserCircle },
  { path: "/users/roles", labelKey: "roles.nav", icon: ShieldCheck, visible: canViewRoles },
];
