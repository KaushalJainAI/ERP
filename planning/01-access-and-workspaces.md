# 1. Access and workspaces

## Goal

Each person opens the ERP and sees only their day's work, with no module tree to
learn. Access stays simple: all staff are trusted and share one access level.

## Access

- Seed one all-access role, `Staff`, and give it to every staff user.
- Keep the permission code (`users/permissions.py`) and the `Role` tables, so the
  system can be tightened later without a rebuild.
- Set `CREDIT_CONTROLLER_ROLE` to `Staff`. Overrides stay in the audit log.
- Only Admin can create users. Worker portal logins remain role-less and see only
  their own records (`hr.services.portal`).
- These stay on: audit log, DB constraints, immutable submitted documents, and the
  draft/cancel/amend lifecycle.

**Trade-off accepted by the owner:** every staff login can see wages, bank details
and all financial data.

This is a deliberate exception to mandate 2's least privilege. Update `CLAUDE.md`,
`goals.md` and `backend/docs/erpnext_divergences.md` in the same commit.

## Landing pages

Add `User.default_workspace`: `orders`, `factory`, `accounts` or `admin`. It only
selects the landing page; it grants nothing.

| Workspace | Home screen shows |
|---|---|
| Orders | Big mic button, today's orders, drafts awaiting approval |
| Factory | Pending orders to dispatch, batches in progress, Ready stock, Workers |
| Accounts | Money to Collect, Bills to Pay, Expenses, GST and TDS due, proof missing |
| Admin | All three, plus reminders and a cash summary |

Build these as a new home page, reusing `Home.tsx` data calls where they fit.
Mobile comes first, because `BottomBar` and the assistant sheet already exist.

## Wording

Use plain words in the three workspaces. Approve instead of Submit; Bill instead of
Sales Invoice; Dispatch instead of Make Invoice; Paid and Due for payment status.
Do this through translation keys, not by renaming fields.

## Hide from daily menus, keep the code

| Hide | Reason |
|---|---|
| Item variants and attributes | 0 in use |
| Batches, batch-wise balance, expiry reports | No lot tracking |
| Purchase Orders | Not in the working day |
| Leave types, allocations and applications | Workers are mostly daily wage |
| Salary components, wage-rate history as a separate menu | Reached from Workers |
| Work Order and Stock Entry menus | Replaced by the batch screen |
| Period close, GSTR-1 detail | Accountant's advanced menu only |

An **Advanced** link in Accounts keeps full reports reachable. Implement this with
the existing `hideInSidebar` flag where possible.

## Also clean up

- Remove the Acme Corp demo company from the real database, after the owner confirms.
- Defer the rest of company isolation until a second real company exists.

## Done when

- A new user lands on the right workspace and needs no sidebar for daily work.
- All workspace text exists in English and Hindi.
- Portal worker tests still prove workers see only their own data.

## Task cards

### A-1 One all-access role
- **Files:** `backend/core/management/commands/seed_defaults.py`, `config/settings.py`.
- **Do:** add `("Staff", "All daily work")` to `ROLES_SEED`. In `ROLE_PERMISSION_MATRIX`, give
  `Staff` `FULL` on every label in the module's list of API-exposed models (read the file;
  it is defined near line 201), excluding `users.Role`, `users.RolePermission` and
  `users.UserRole`. Keep all other roles. Ensure `CREDIT_CONTROLLER_ROLE` reads from env and
  defaults to `Staff`. Do not change `users/permissions.py`.
- **Test:** a `Staff` user can submit a Sales Invoice and read HR; a `Staff` user cannot
  edit roles; a portal user still has no roles.

### A-2 Record the exception
- **Files:** `CLAUDE.md`, `goals.md`, `backend/docs/erpnext_divergences.md`.
- **Do:** in mandate 2 and the goals' RBAC line, state: currently one access level by owner
  decision, 2026-10-31; constraints, audit log and immutability still apply. Add a divergence
  entry. Change nothing else in `CLAUDE.md`.

### A-3 Landing workspace
- **Files:** `backend/users/models.py`, `users/serializers.py`,
  `frontend/src/resources/users.ts`, `frontend/src/types`.
- **Do:** add `User.default_workspace`, a `CharField` with TextChoices `orders`, `factory`,
  `accounts`, `admin`, default `admin`. Add it to `UserSerializer` and `UserAdminSerializer`,
  and to the user form. Update `API.md`.
- **Test:** an invalid value is refused; `/auth/me/` returns it.

### A-4 Workspace home pages
- **Depends:** A-3, O-5, M-4, M-5, E-5, T-4, P-4. Build each panel when its target exists; the
  first version may only hold links.
- **Files:** new `frontend/src/pages/workspaces/{OrdersHome,FactoryHome,AccountsHome,AdminHome}.tsx`,
  `frontend/src/pages/Home.tsx`.
- **Do:** `Home.tsx` picks the page from `user.default_workspace`. Keep the current module
  launcher behind an **All modules** link. Show the content listed under Landing pages. Use
  cards with large tap targets, mobile first. Fetch counts with `useQuery`.
- **Test:** `npm run build`; check each workspace at phone width.

### A-5 Hide menus
- **Files:** `frontend/src/resources/*.ts`.
- **Do:** set `hideInSidebar: true` on the resources in the Hide table. Routes must keep
  working. Keep them reachable from an Advanced link in Accounts.
- **Test:** the sidebar omits them; their URLs still open.

### A-6 Plain words
- **Do:** in `en.*.ts` and `hi.ts`, add new workspace-specific keys for Approve, Bill,
  Dispatch, Paid and Due. Use them on new screens, and on the Sales Order and Purchase Invoice
  action buttons. Do not change field names or API values.
- **Test:** `npm run lint`.

### A-7 Remove demo company (OWNER)
The owner confirms, and a person removes Acme Corp from the real database after a backup.
