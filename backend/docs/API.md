# API Reference — Code Review Map

A per-endpoint index of the whole HTTP surface, built as a **starting point for
reviewing this codebase**. For every resource it records:

0. **URL** + methods
1. **What** it does
2. **Access** — permission (global default `IsAuthenticated` + `HasRolePermission` RBAC)
3. **Django app**
4. **Complexity** — rough cost of one call (see legend)
5. **Tested** — is there a test (see coverage note)
6. **Serializer**
7. **DB tables** — models read/written (posting side-effects noted)
8. **Atomic** — does the write run inside `transaction.atomic()`?
9. **Notes**

> Hand-derived from `config/urls.py`, each app's `urls.py` / `views.py` / `models.py`,
> `core/mixins.py`, and `test*.py` on 2026-07-24. `urls.py` remains the source of truth —
> **add a row whenever you add a route.** This is an ERPNext-style backend; read
> `docs/erpnext_divergences.md` and `docs/core_models.md` alongside this file.

## Legend

**Complexity**: `O(1)` single-row · `O(n)` linear · `Aggregate` report GROUP BY over the
ledger · `Posting` a submit/cancel that writes ledger entries + updates balances.

**Access**: every business route requires `IsAuthenticated` **and** `HasRolePermission`
(per-model RBAC via `users.RolePermission`). Auth is JWT or Django session. Only the
token endpoints differ. `HasRolePermission` **fails closed**: a view that cannot be tied
to a model is *denied*, so every viewset must expose a `queryset`/`model`, and
function-based views must declare their model via `require_model_permission(...)`. Only
`is_superuser` bypasses RBAC — `is_staff` does not (it means "may open the admin site").

**Tested**: `✅` covered by app tests · `~` thin/indirect · `—` none.

**Atomic**: **The submit/cancel/amend document lifecycle IS atomic** — the model's
`.submit()`/`.cancel()` methods (on `core.SubmittableDocument`) wrap ledger posting +
balance updates in `transaction.atomic()`. Plain master CRUD (create/update via the
ViewSet) is a single non-atomic write. So the column reads `Posting=Yes` for lifecycle
actions, `No` for ordinary CRUD.

## Global config

- **Auth**: `JWTAuthentication` + `SessionAuthentication`. **Default permission**:
  `IsAuthenticated`. **RBAC**: `users.permissions.HasRolePermission` on every business ViewSet.
- **Throttle**: hard cap for anon, generous for authenticated ERP users; `auth` scope on
  the token endpoint (brute-force guard).
- **Docs**: `/api/schema/`, `/api/docs/`, `/api/redoc/`.
- **Document status model** (`core.DocStatus`): `DRAFT → SUBMITTED → CANCELLED`. Only
  drafts are editable/deletable; submitted docs are immutable (edit → 409); cancel then
  `amend` to make a new draft. This is enforced in `SubmittableViewSetMixin`.
- **Audit**: every CRUD + submit/cancel/amend writes a `core.AuditLog` row (`AuditLogMixin`).
- **RBAC posture (2026-10-21)**: `HasRolePermission` resolves the view's model via
  `view.model_label` → `view.model` → `view.queryset` → `view.get_queryset()`, and
  **denies** when none resolve. Resolution uses `is None` checks, never truthiness — a
  Django `QuerySet` is falsy when empty, which previously made every viewset on an empty
  table fail open. `is_superuser` bypasses; `is_staff` does not. The seeded role matrix
  lives in `core/management/commands/seed_defaults.py` (`ROLE_PERMISSION_MATRIX`) and is
  authoritative for the six seeded roles: a re-seed grants/updates/revokes to match it,
  and validates every `model_label` against the app registry. Read on `accounts.GLEntry`
  / `stock.Bin` — i.e. access to the financial and stock reports — is reserved for the
  Accounts and Stock Managers.

## Test coverage by app (≈ #tests)

`core` ✅ (tests 18, test_security 10, test_e2e 12) · `accounts` ✅ (14) · `stock` ✅ (13) ·
`users` ✅ (tests 8, test_rbac 26) · `buying` ~ (6) · `selling` ~ (6). No app is untested.

---

## 1. Users / auth / RBAC — app: `users` — [users/views.py](../users/views.py)

Models: `Role`, `UserRole`, `RolePermission` (+ custom `User`).

| URL (`/api/auth/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `token/` | POST | JWT obtain | Public (throttled `auth`) | O(1) | ✅ | SimpleJWT | User | No | Brute-force throttle |
| `token/refresh/` | POST | Refresh JWT | Public (throttled) | O(1) | ✅ | SimpleJWT | — | No | |
| `me/` | GET | Current user + roles/permissions | Auth | O(1) | ✅ | `CurrentUserSerializer` | User, UserRole, RolePermission | N/A | |
| `roles/` | GET/POST | Role registry | Auth + RBAC `users.Role` | O(n) | ✅ | `RoleSerializer` | Role | No | Was `IsAdminUser`; now RBAC-gated on `users.Role` (seeded *System Manager* role, or superuser) |
| `roles/{pk}/` | GET/PUT/PATCH/DELETE | Role CRUD | Auth + RBAC `users.Role` | O(1) | ✅ | `RoleSerializer` | Role, UserRole | No | |
| `permissions/` | GET/POST | Role→permission grants | Auth + RBAC `users.RolePermission` | O(n) | ✅ | `RolePermissionSerializer` | RolePermission | No | Governs every other endpoint's access. Was `IsAdminUser` — any staff user could grant themselves `can_read` on `accounts.GLEntry` and read the whole ledger |
| `permissions/{pk}/` | GET/PUT/PATCH/DELETE | Permission CRUD | Auth + RBAC `users.RolePermission` | O(1) | ✅ | `RolePermissionSerializer` | RolePermission | No | |

---

## 2. Core — app: `core` — [core/urls.py](../core/urls.py)

`urls.py` is **empty** — no routes. `core` provides the shared base: `TimeStampedModel`,
`SubmittableDocument`, `Company`, `FiscalYear`, `NamingSeries`, `AuditLog`, `Outbox`,
`Language`, `FieldTranslation`, plus the ViewSet/serializer mixins in
[core/mixins.py](../core/mixins.py). Review these first — they define document status,
audit, i18n, and posting behaviour used everywhere below.

---

## 3. Accounts / ledger (GL) — app: `accounts` — [accounts/views.py](../accounts/views.py)

Models: `Account`, `GLEntry`, `JournalEntry`(+`JournalEntryLine`), `PaymentEntry`(+`PaymentEntryReference`).

| URL (`/api/accounts/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `accounts/` `accounts/{pk}/` | CRUD | Chart of accounts | Auth + RBAC | O(1) | ✅ | `AccountSerializer` | Account | No | Tree master |
| `gl-entries/` `gl-entries/{pk}/` | GET only | General-ledger entries | Auth + RBAC | O(n) | ✅ | `GLEntrySerializer` | GLEntry | N/A | **Read-only** — written by posting |
| `journal-entries/` `…/{pk}/` | CRUD | Journal entries | Auth + RBAC | O(1) | ✅ | `JournalEntrySerializer` | JournalEntry, JournalEntryLine | No (draft) | Submittable |
| `journal-entries/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Post / reverse / re-draft | Auth + RBAC | Posting | ✅ | `JournalEntrySerializer` | JournalEntry, GLEntry | **Yes** | Balanced-Dr/Cr enforced; posts GL |
| `payment-entries/` `…/{pk}/` | CRUD | Payments (against invoices) | Auth + RBAC | O(1) | ✅ | `PaymentEntrySerializer` | PaymentEntry, PaymentEntryReference | No (draft) | Submittable |
| `payment-entries/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Post / reverse / re-draft | Auth + RBAC | Posting | ✅ | `PaymentEntrySerializer` | PaymentEntry, GLEntry, (invoice outstanding) | **Yes** | Allocates against references |
| `reports/trial-balance/` | GET | Trial balance | Auth + RBAC | Aggregate | ✅ | — | GLEntry, Account | N/A | Function view |
| `reports/general-ledger/` | GET | GL report (filters) | Auth + RBAC | Aggregate | ✅ | — | GLEntry, Account | N/A | |
| `reports/profit-and-loss/` | GET | P&L | Auth + RBAC | Aggregate | ✅ | — | GLEntry, Account | N/A | |
| `reports/balance-sheet/` | GET | Balance sheet | Auth + RBAC | Aggregate | ✅ | — | GLEntry, Account | N/A | |

---

## 4. Stock / inventory — app: `stock` — [stock/views.py](../stock/views.py)

Models: `UOM`, `ItemGroup`, `Item`, `Warehouse`, `StockLedgerEntry`, `Bin`, `StockEntry`(+`StockEntryItem`).

| URL (`/api/stock/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `uoms/` · `item-groups/` · `items/` · `warehouses/` | CRUD | Inventory masters | Auth + RBAC | O(1) | ✅ | resp. serializers | UOM / ItemGroup / Item / Warehouse | No | |
| `sle/` `sle/{pk}/` | GET only | Stock ledger entries | Auth + RBAC | O(n) | ✅ | `StockLedgerEntrySerializer` | StockLedgerEntry | N/A | **Read-only** — written by posting |
| `bins/` `bins/{pk}/` | GET only | Per-item-per-warehouse balances | Auth + RBAC | O(n) | ✅ | `BinSerializer` | Bin | N/A | **Read-only** — maintained by posting |
| `stock-entries/` `…/{pk}/` | CRUD | Stock movements (receipt/issue/transfer) | Auth + RBAC | O(1) | ✅ | `StockEntrySerializer` | StockEntry, StockEntryItem | No (draft) | Submittable |
| `stock-entries/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Post / reverse movement | Auth + RBAC | Posting | ✅ | `StockEntrySerializer` | StockEntry, StockLedgerEntry, Bin | **Yes** | Updates SLE + Bin qty/valuation |
| `reports/stock-balance/` | GET | Stock balance report | Auth + RBAC | Aggregate | ✅ | — | Bin, StockLedgerEntry, Item | N/A | Function view |

---

## 5. Selling — app: `selling` — [selling/views.py](../selling/views.py)

Models: `CustomerGroup`, `Customer`, `SalesOrder`(+`SalesOrderItem`), `SalesInvoice`(+`SalesInvoiceItem`).

| URL (`/api/selling/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `customer-groups/` · `customers/` | CRUD | Selling masters | Auth + RBAC | O(1) | ~ | resp. serializers | CustomerGroup / Customer | No | Customer search: name/gstin/email/phone |
| `sales-orders/` `…/{pk}/` | CRUD | Sales orders | Auth + RBAC | O(1) | ~ | `SalesOrderSerializer` | SalesOrder, SalesOrderItem | No (draft) | Submittable; positive-qty / non-neg-rate line validators |
| `sales-orders/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Confirm / cancel order | Auth + RBAC | Posting | ~ | `SalesOrderSerializer` | SalesOrder | **Yes** | |
| `sales-invoices/` `…/{pk}/` | CRUD | Sales invoices | Auth + RBAC | O(1) | ~ | `SalesInvoiceSerializer` | SalesInvoice, SalesInvoiceItem | No (draft) | Submittable; `update_stock` flag |
| `sales-invoices/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Post invoice (GL + optional stock) | Auth + RBAC | Posting | ~ | `SalesInvoiceSerializer` | SalesInvoice, GLEntry, (SLE/Bin if `update_stock`) | **Yes** | Posts receivable + income; stock if flagged |

---

## 6. Buying — app: `buying` — [buying/views.py](../buying/views.py)

Models: `SupplierGroup`, `Supplier`, `PurchaseOrder`(+`PurchaseOrderItem`), `PurchaseInvoice`(+`PurchaseInvoiceItem`).

| URL (`/api/buying/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `supplier-groups/` · `suppliers/` | CRUD | Buying masters | Auth + RBAC | O(1) | ~ | resp. serializers | SupplierGroup / Supplier | No | |
| `purchase-orders/` `…/{pk}/` | CRUD | Purchase orders | Auth + RBAC | O(1) | ~ | `PurchaseOrderSerializer` | PurchaseOrder, PurchaseOrderItem | No (draft) | Submittable |
| `purchase-orders/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Confirm / cancel PO | Auth + RBAC | Posting | ~ | `PurchaseOrderSerializer` | PurchaseOrder | **Yes** | |
| `purchase-invoices/` `…/{pk}/` | CRUD | Purchase invoices | Auth + RBAC | O(1) | ~ | `PurchaseInvoiceSerializer` | PurchaseInvoice, PurchaseInvoiceItem | No (draft) | Submittable |
| `purchase-invoices/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Post invoice (GL + optional stock) | Auth + RBAC | Posting | ~ | `PurchaseInvoiceSerializer` | PurchaseInvoice, GLEntry, (SLE/Bin) | **Yes** | Posts payable + expense/asset |

---

## Contacts — app: `contacts`

`contacts/urls.py` is **empty** — no routes mounted. The app holds shared address/contact
models used by selling/buying. Flag as models-only when reviewing.

---

## Cross-cutting notes for reviewers

- **Atomicity is at the model layer, not the view.** No `views.py` calls
  `transaction.atomic`; the guarantee comes from `SubmittableDocument.submit()/cancel()`
  in `core/models.py`. Review those methods (and their `select_for_update` on Bin/Account
  balances) for the real posting correctness — the views are thin.
- **Immutability is enforced twice:** the model refuses to edit a non-draft, and
  `SubmittableViewSetMixin.update/destroy` translate that into a clean **409**. A regression
  here (letting a submitted doc be edited) is the highest-value bug to guard against.
- **Ledgers are read-only over HTTP:** `GLEntry`, `StockLedgerEntry`, `Bin` are
  `ReadOnlyModelViewSet` — they can only be written through document posting. Verify no
  new route exposes them for write.
- **Every business route is RBAC-gated** (`HasRolePermission`) on top of `IsAuthenticated`.
  When reviewing a new endpoint, confirm the ViewSet keeps `HasRolePermission` and that a
  matching `RolePermission` grant exists in the seed (`docs/seed_defaults.md`).
- **i18n**: translatable fields are swapped at serialization via `TranslatedSerializerMixin`
  + `FieldTranslation` — responses vary by `User.language` / `Accept-Language`.
