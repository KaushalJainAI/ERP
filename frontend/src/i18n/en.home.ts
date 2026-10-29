/**
 * English strings for the signed-in home page (workspace launcher and pending
 * work). Merged into the English bundle in i18n.ts. Other languages fall back
 * to English until translated.
 */
export const enHome = {
  company: {
    eyebrow: "Company",
    choose_title: "Choose a company",
    choose_body:
      "Every list, document and report in this session will be limited to the company you choose.",
    choose_footer: "You can switch company from the home screen at any time.",
    working_in: "Working in",
    switch: "Switch company",
    switch_hint: "Switch company (currently {{name}})",
    loading: "Loading companies...",
    load_failed: "Your companies could not be loaded. Try again later.",
    none_title: "No company assigned",
    none_body:
      "Your account is not a member of any active company. Ask an administrator to add you to a company.",
  },
  home: {
    title: "Home",
    eyebrow: "Workspace",
    welcome: "Welcome back, {{name}}",
    subtitle: "Pick a workspace to go straight to the work that belongs to you.",
    roles: "Signed in as",
    administrator: "Administrator",
    workspaces: "Your workspaces",
    open_workspace: "Open {{name}}",
    admin_title: "Administration",
    empty_title: "No workspace assigned yet",
    empty_body:
      "Your account does not have access to any workspace yet. Ask an administrator to assign you a role.",
    workspace: {
      selling_desc: "Customers, orders and invoices.",
      buying_desc: "Suppliers, purchase orders and bills.",
      stock_desc: "Items, warehouses, stock entries and balances.",
      manufacturing_desc: "Bills of materials, work orders and production.",
      accounts_desc: "Journal, payments, ledgers and financial reports.",
      hr_desc: "Employees, attendance, payroll and requests.",
      admin_desc: "Users, roles and access control.",
    },
    pending: {
      title: "Pending work",
      subtitle: "Drafts and open documents that are waiting for you.",
      total_one: "{{count}} item",
      total_other: "{{count}} items",
      none: "You're all caught up. No drafts are waiting.",
      loading: "Checking for pending work...",
      error: "Pending work could not be loaded. Try again later.",
      draft_hint: "Draft - not submitted",
      no_company: "No company has been set up yet. Pending work will appear once one exists.",
      open_orders: "Open sales orders",
      open_orders_hint_one: "{{count}} order not fully invoiced",
      open_orders_hint_other: "{{count}} orders not fully invoiced",
    },
  },
};
