# Users, Auth & RBAC Documentation (`users/`)

## Overview
The `users` app handles authentication and Role-Based Access Control (RBAC).

## Architecture & Data Schema

### 1. Custom User Model (`User`)
- Extends `AbstractUser`.
- `email`: Unique, automatically lowercased on `save()`.
- `language`: User's preferred language code (default `'en'`).

### 2. Role-Based Access Control (RBAC)
- `Role`: Represents a role (`name`, `description`).
- `UserRole`: M2M mapping table between `User` and `Role` with `UNIQUE(user, role)`.
- `RolePermission`: Granular model-level permissions for each role:
  - `model_label`: e.g. `'stock.Item'` or `'accounts.SalesInvoice'`.
  - Flags: `can_read`, `can_create`, `can_write`, `can_submit`, `can_cancel`, `can_delete`.
  - `UNIQUE(role, model_label)`.

### 3. Permission Enforcement (`HasRolePermission`)
`users.permissions.HasRolePermission` checks requested HTTP action against `RolePermission` for the requesting user's assigned roles:
- `GET`, `HEAD`, `OPTIONS` -> `can_read`
- `POST` (standard) -> `can_create`
- `POST` `/submit/` -> `can_submit`
- `POST` `/cancel/` -> `can_cancel`
- `PUT`, `PATCH` -> `can_write`
- `DELETE` -> `can_delete`

**Bypass rules.** `is_superuser` bypasses RBAC. `is_staff` does **not** — in Django
`is_staff` only means "may sign in to the admin site" (`ModelAdmin` still honours it);
API authority comes from roles.

**Model resolution (fail closed).** The view's model is resolved in this order:
`view.model_label` -> `view.model` -> `view.queryset` -> `view.get_queryset()`. If none
resolve, the request is **denied** — a view with no declared model has no declared
authority. Function-based views (the report endpoints) expose no queryset, so they
declare their model explicitly with `require_model_permission(model_label, flag)`.

> Resolution uses `is None` checks, never truthiness. A Django `QuerySet` evaluates its
> query to decide truthiness, so an *empty* queryset is falsy; a truthiness check
> discarded it and the model was never found — which, before the 2026-10-21 fix, made
> every viewset on an empty table fail **open**.

### 4. Seeded role matrix
`core/management/commands/seed_defaults.py` holds `ROLE_PERMISSION_MATRIX`, the single
source of truth for the six seeded roles. Each role is scoped to its own domain:

| Role | Scope |
|------|-------|
| `Default Role` | read masters, create draft documents; no submit/cancel/delete, **no ledger read** |
| `Stock Manager` | full `stock.*` (incl. `stock.Bin`, `stock.StockLedgerEntry`) — reads the stock balance report |
| `Accounts Manager` | full `accounts.*` (incl. `accounts.GLEntry`) — reads Trial Balance / General Ledger / P&L / Balance Sheet |
| `Sales Manager` | full `selling.*`, read the catalogue it sells from |
| `Purchase Manager` | full `buying.*`, read the catalogue it buys |
| `System Manager` | everything, plus `users.Role` / `users.RolePermission` (the control plane) |

The seed is **authoritative** for the roles it owns: a re-seed makes their permission
rows match the matrix exactly, granting, updating and revoking as needed (it reports
the counts). It validates every `model_label` against the app registry and aborts on a
label that resolves to no model. Roles the operator created themselves are never
touched.

### 5. Auth & JWT Endpoints
- `POST /api/auth/token/`: Obtain JWT access token & refresh token.
- `POST /api/auth/token/refresh/`: Refresh expired access token.
- `GET/PATCH /api/auth/me/`: Current user profile & role list.
- `GET/POST /api/auth/roles/`, `/api/auth/permissions/`: the RBAC control plane. Both
  require `HasRolePermission` on `users.Role` / `users.RolePermission` — i.e. the
  *System Manager* role or a superuser. They were previously gated on `IsAdminUser`,
  which let any staff user grant themselves ledger read.

## Tests & Verification
`users/tests.py` and `users/test_rbac.py` cover email normalization, RBAC constraints,
permission evaluation, JWT authentication, and the 2026-10-21 hardening: `is_staff` no
longer bypasses, the check fails closed on an unresolvable view, an empty queryset
still resolves its model, the seed grants no phantom labels, the seed is idempotent and
authoritative, and the seeded roles are correctly scoped.
