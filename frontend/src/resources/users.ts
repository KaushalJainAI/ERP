import { createElement } from "react";
import { Power, PowerOff, ShieldCheck, UserCircle, Users as UsersIcon } from "lucide-react";
import { SetPasswordPanel } from "@/components/users/SetPasswordPanel";
import type { NavLink, ResourceConfig, SelectOption, UserProfile } from "@/types";

/**
 * Hand-written pages of the "Users & Security" sidebar section, plus the
 * config-driven User resource (/users/users, backed by /auth/users/).
 */

/**
 * The Roles and Users pages read /auth/roles/, /auth/permissions/ and
 * /auth/users/, which are RBAC-gated on users.Role / users.RolePermission /
 * users.User (seeded System Manager, or superuser). Hide the links from
 * everyone else rather than showing pages that 403.
 */
export const canViewRoles = (user: UserProfile): boolean =>
  !!user.is_superuser || user.roles.includes("System Manager");

/** Same gate for user management. */
export const canManageUsers = canViewRoles;

// The 13 supported UI languages (backend settings.LANGUAGES); names live in en.users.ts.
const LANGUAGE_CODES = ["en", "hi", "ta", "pa", "gu", "or", "bn", "kn", "as", "te", "ml", "mr", "ur"];
const LANGUAGES: SelectOption[] = LANGUAGE_CODES.map((c) => ({ value: c, labelKey: `usermgmt.lang.${c}` }));

export const usersResources: ResourceConfig[] = [
  {
    key: "users",
    module: "users",
    endpoint: "/auth/users/",
    titleKey: "usermgmt.title",
    singularKey: "usermgmt.singular",
    icon: UsersIcon,
    titleField: "username",
    ordering: "username",
    // Users are deactivated, never deleted (the API has no DELETE).
    noDelete: true,
    // The sidebar link is added through usersLinks so it can be gated by role.
    hideInSidebar: true,
    columns: [
      { key: "username", labelKey: "usermgmt.username" },
      { key: "email", labelKey: "usermgmt.email" },
      {
        key: "full_name",
        labelKey: "usermgmt.full_name",
        render: (_v, row) => [row.first_name, row.last_name].filter(Boolean).join(" "),
      },
      {
        key: "roles",
        labelKey: "usermgmt.roles",
        render: (v) => (Array.isArray(v) ? v.join(", ") : ""),
      },
      { key: "is_active", labelKey: "usermgmt.active", type: "boolean" },
      { key: "last_login", labelKey: "usermgmt.last_login", type: "date" },
    ],
    fields: [
      {
        key: "username",
        labelKey: "usermgmt.username",
        type: "text",
        required: true,
        readOnlyAfterCreate: true,
        helpKey: "usermgmt.username_help",
      },
      { key: "email", labelKey: "usermgmt.email", type: "email", required: true },
      { key: "first_name", labelKey: "usermgmt.first_name", type: "text" },
      { key: "last_name", labelKey: "usermgmt.last_name", type: "text" },
      {
        key: "language",
        labelKey: "usermgmt.language",
        type: "select",
        required: true,
        options: LANGUAGES,
        defaultValue: "en",
      },
      {
        key: "password",
        labelKey: "usermgmt.password",
        type: "password",
        required: true,
        readOnlyAfterCreate: true,
        hidden: (_v, isNew) => !isNew,
        helpKey: "usermgmt.password_help",
      },
      // Shown read-only; toggled with the Deactivate / Activate actions.
      { key: "is_active", labelKey: "usermgmt.active", type: "boolean", readOnly: true, hidden: (_v, isNew) => isNew },
      {
        key: "role_ids",
        labelKey: "usermgmt.roles",
        type: "multiselect",
        span: 3,
        helpKey: "usermgmt.roles_help",
        link: { endpoint: "/auth/roles/", valueField: "id", labelField: "name" },
      },
    ],
    actions: [
      {
        key: "deactivate",
        labelKey: "usermgmt.deactivate",
        icon: PowerOff,
        kind: "request",
        showWhen: (doc) => !!doc.is_active,
        url: (doc) => `/auth/users/${doc.id}/deactivate/`,
      },
      {
        key: "activate",
        labelKey: "usermgmt.activate",
        icon: Power,
        kind: "request",
        showWhen: (doc) => !doc.is_active,
        url: (doc) => `/auth/users/${doc.id}/activate/`,
      },
    ],
    renderSummary: ({ values }) =>
      createElement(SetPasswordPanel, { userId: values.id, targetIsSuperuser: !!values.is_superuser }),
  },
];

export const usersLinks: NavLink[] = [
  { path: "/users/profile", labelKey: "profile.nav", icon: UserCircle },
  { path: "/users/users", labelKey: "usermgmt.nav", icon: UsersIcon, visible: canManageUsers },
  { path: "/users/roles", labelKey: "roles.nav", icon: ShieldCheck, visible: canViewRoles },
];
