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

## Document write semantics (all submittable resources)

Applies to `sales-orders`, `sales-invoices`, `purchase-orders`, `purchase-invoices`,
`journal-entries`, `payment-entries` (`references`) and `stock-entries`, via
`core.mixins.DraftNestedWriteMixin`:

- **`name` is optional on create** (auto-numbered from `NamingSeries`); a client may still send one.
- **`PUT`/`PATCH` on a draft** replaces the nested child set atomically (when the child list is sent;
  omitted children are left untouched) and recomputes the derived totals (`grand_total`,
  JE `total_debit`/`total_credit`) server-side. A `PATCH` that sends children must send complete rows.
  Invalid rows return 400 and change nothing.
- **`PUT`/`PATCH` on a submitted or cancelled document returns 409.**
- Line serializers expose read-only `item_name`, `warehouse_name` (null when unset); JE lines `account_name`.

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

`core` ✅ (tests 18, test_security 10, test_e2e 12, test_dashboard 13) · `accounts` ✅ (14) · `stock` ✅ (13) ·
`users` ✅ (tests 8, test_rbac 26) · `buying` ~ (6) · `selling` ~ (6). No app is untested.

---

## 1. Users / auth / RBAC — app: `users` — [users/views.py](../users/views.py)

Models: `Role`, `UserRole`, `RolePermission` (+ custom `User`).

| URL (`/api/auth/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `token/` | POST | JWT obtain | Public (throttled `auth`) | O(1) | ✅ | SimpleJWT | User | No | Brute-force throttle |
| `token/refresh/` | POST | Refresh JWT | Public (throttled) | O(1) | ✅ | SimpleJWT | — | No | |
| `me/` | GET, PATCH | Current user + roles. PATCH may change only `first_name`, `last_name`, `email`, `language`; `username`, `is_active`, `is_superuser`, `is_staff`, `roles` are read-only (silently ignored) | Auth | O(1) | ✅ | `UserSerializer` | User, UserRole, RolePermission | N/A | |
| `roles/` | GET/POST | Role registry | Auth + RBAC `users.Role` | O(n) | ✅ | `RoleSerializer` | Role | No | Was `IsAdminUser`; now RBAC-gated on `users.Role` (seeded *System Manager* role, or superuser) |
| `roles/{pk}/` | GET/PUT/PATCH/DELETE | Role CRUD | Auth + RBAC `users.Role` | O(1) | ✅ | `RoleSerializer` | Role, UserRole | No | |
| `permissions/` | GET/POST | Role→permission grants | Auth + RBAC `users.RolePermission` | O(n) | ✅ | `RolePermissionSerializer` | RolePermission | No | Governs every other endpoint's access. Was `IsAdminUser` — any staff user could grant themselves `can_read` on `accounts.GLEntry` and read the whole ledger |
| `permissions/{pk}/` | GET/PUT/PATCH/DELETE | Permission CRUD | Auth + RBAC `users.RolePermission` | O(1) | ✅ | `RolePermissionSerializer` | RolePermission | No | |

---

## 2. Core — app: `core` — [core/urls.py](../core/urls.py)

`core` provides the shared base: `TimeStampedModel`, `SubmittableDocument`, `Company`,
`FiscalYear`, `NamingSeries`, `AuditLog`, `Outbox`, `Language`, `FieldTranslation`,
`SystemHealthCheck`, plus the ViewSet/serializer mixins in
[core/mixins.py](../core/mixins.py). Review these first — they define document status,
audit, i18n, and posting behaviour used everywhere below. Its routes are the read-only
dashboard summary and companies list in [core/views.py](../core/views.py).

| URL (`/api/core/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `dashboard/` | GET | Headline numbers for one company (`?company=<id>`, default first active): total sales/purchases, receivables/payables outstanding, open sales orders (count/value), stock value, draft counts, latest integrity check | Auth + RBAC: `can_read` on **any** of SalesInvoice / SalesOrder / PurchaseInvoice / PurchaseOrder / GLEntry / JournalEntry / PaymentEntry / Bin / StockEntry / SystemHealthCheck, else 403. Each section is `null` unless the caller can read its source model (receivables/payables need `accounts.GLEntry`, stock value `stock.Bin`, integrity `core.SystemHealthCheck`) | Aggregate | ✅ | — (plain dict; money as strings) | SalesInvoice, PurchaseInvoice, SalesOrder, GLEntry, Bin, draft docs, SystemHealthCheck | N/A | Function view; read-only, everything derived on read. Receivables/payables = live GL balance of Receivable/Payable accounts (cancelled rows excluded). Open order = submitted order whose submitted invoices cover less than its total. 400 on a non-integer `company`, 404 on unknown id |
| `companies/` `…/{pk}/` | GET (list/detail) | Read-only company list for pickers (`?search=` name/abbr, `?is_active=`) | Auth + RBAC: `can_read` on `core.Company` | O(1) | ✅ | `core.views.CompanySerializer` (id, name, abbr, default_currency, country, gstin, is_active, plus read-only enable_perpetual_inventory, default_inventory_account, stock_adjustment_account, default_expense_account) | Company | n/a (read) | Write verbs return 405; companies are managed in the admin site |

---

## 3. Accounts / ledger (GL) — app: `accounts` — [accounts/views.py](../accounts/views.py)

Models: `Account`, `GLEntry`, `JournalEntry`(+`JournalEntryLine`), `PaymentEntry`(+`PaymentEntryReference`).

| URL (`/api/accounts/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `accounts/` `accounts/{pk}/` | CRUD | Chart of accounts | Auth + RBAC | O(1) | ✅ | `AccountSerializer` | Account | No | Tree master |
| `gl-entries/` `gl-entries/{pk}/` | GET only | General-ledger entries (filters: company, account, voucher_type, voucher_no, is_cancelled, `posting_date` / `posting_date__gte` / `posting_date__lte`) | Auth + RBAC | O(n) | ✅ | `GLEntrySerializer` | GLEntry | N/A | **Read-only** — written by posting |
| `journal-entries/` `…/{pk}/` | CRUD | Journal entries | Auth + RBAC | O(1) | ✅ | `JournalEntrySerializer` | JournalEntry, JournalEntryLine | No (draft) | Submittable |
| `journal-entries/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Post / reverse / re-draft | Auth + RBAC | Posting | ✅ | `JournalEntrySerializer` | JournalEntry, GLEntry | **Yes** | Balanced-Dr/Cr enforced; posts GL |
| `payment-entries/` `…/{pk}/` | CRUD | Payments (against invoices) | Auth + RBAC | O(1) | ✅ | `PaymentEntrySerializer` (+ read-only `paid_from_name`, `paid_to_name`) | PaymentEntry, PaymentEntryReference | No (draft) | Submittable |
| `payment-entries/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Post / reverse / re-draft | Auth + RBAC | Posting | ✅ | `PaymentEntrySerializer` | PaymentEntry, GLEntry, (invoice outstanding) | **Yes** | Allocates against references |
| `reports/trial-balance/` | GET | Trial balance (`company`, `from_date`, `to_date`; malformed date = 400; money 2dp) | Auth + RBAC | Aggregate | ✅ | — | GLEntry, Account | N/A | Function view |
| `reports/general-ledger/` | GET | GL report (`company`, `account`, `from_date`, `to_date`). With `account`: `opening_balance` (net before `from_date`, 0.00 if none), running `balance` per row starting from it, `closing_balance`. Without `account`: `opening_balance`, row `balance` and `closing_balance` are `null` | Auth + RBAC | Aggregate | ✅ | — | GLEntry, Account | N/A | |
| `reports/profit-and-loss/` | GET | P&L for period (`company`, `from_date`, `to_date`; none = all time). Returns `total_income/total_expense/net_profit` + `accounts[]` (`account_id, account_name, root_type, balance`) | Auth + RBAC | Aggregate | ✅ | — | GLEntry, Account | N/A | |
| `reports/balance-sheet/` | GET | Balance sheet as of `to_date` (`company`, `to_date`; none = all time; `from_date` ignored). Returns `total_assets/total_liabilities/total_equity` + `accounts[]` (`account_id, account_name, root_type, balance`) | Auth + RBAC | Aggregate | ✅ | — | GLEntry, Account | N/A | |

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
| `reports/stock-balance/` | GET | Stock balance report (`stock_value` / `total_stock_value` quantized to 2dp) | Auth + RBAC | Aggregate | ✅ | — | Bin, StockLedgerEntry, Item | N/A | Function view |

---

## 5. Selling — app: `selling` — [selling/views.py](../selling/views.py)

Models: `CustomerGroup`, `Customer`, `SalesOrder`(+`SalesOrderItem`), `SalesInvoice`(+`SalesInvoiceItem`).

| URL (`/api/selling/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `customer-groups/` · `customers/` | CRUD | Selling masters | Auth + RBAC | O(1) | ~ | resp. serializers | CustomerGroup / Customer | No | Customer search: name/gstin/email/phone |
| `sales-orders/` `…/{pk}/` | CRUD | Sales orders | Auth + RBAC | O(1) | ~ | `SalesOrderSerializer` | SalesOrder, SalesOrderItem | No (draft) | Submittable; positive-qty / non-neg-rate line validators |
| `sales-orders/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Confirm / cancel order | Auth + RBAC | Posting | ~ | `SalesOrderSerializer` | SalesOrder | **Yes** | |
| `sales-orders/{pk}/make-sales-invoice/` | POST | Map a **submitted** SO to a **draft** Sales Invoice for the not-yet-invoiced qty (no body). 201 → `SalesInvoiceSerializer`; 400 `{"error"}` if not submitted / fully invoiced / no Receivable or Income account | Auth + RBAC: `can_read` on `selling.SalesOrder` **and** `can_create` on `selling.SalesInvoice` (else 403) | Aggregate | ✅ | `SalesInvoiceSerializer` | SalesOrder, SalesOrderItem (read) → SalesInvoice, SalesInvoiceItem (write), Account (read) | **Yes** | `selling/mapping.py`; billed qty counts **submitted** invoices only; accounts resolved by type (see divergences) |
| `sales-invoices/` `…/{pk}/` | CRUD | Sales invoices | Auth + RBAC | O(1) | ~ | `SalesInvoiceSerializer` (+ read-only `sales_order_name` (nullable), `debit_to_name`, `income_account_name`) | SalesInvoice, SalesInvoiceItem | No (draft) | Submittable; `update_stock` flag |
| `sales-invoices/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Post invoice (GL + optional stock) | Auth + RBAC | Posting | ~ | `SalesInvoiceSerializer` | SalesInvoice, GLEntry, (SLE/Bin if `update_stock`) | **Yes** | Posts receivable + income; stock if flagged |
| `sales-invoices/{pk}/make-payment-entry/` | POST | Map a **submitted** SI to a **draft** "Receive" Payment Entry for its outstanding amount with one references row (no body). 201 → `PaymentEntrySerializer`; 400 if draft/cancelled, fully paid, or no Bank/Cash account | Auth + RBAC: `can_read` on `selling.SalesInvoice` **and** `can_create` on `accounts.PaymentEntry` (else 403) | Aggregate | ✅ | `PaymentEntrySerializer` | SalesInvoice, PaymentEntryReference (read) → PaymentEntry, PaymentEntryReference (write), Account | **Yes** | Outstanding = grand total less allocations on **submitted** PEs |

---

## 6. Buying — app: `buying` — [buying/views.py](../buying/views.py)

Models: `SupplierGroup`, `Supplier`, `PurchaseOrder`(+`PurchaseOrderItem`), `PurchaseInvoice`(+`PurchaseInvoiceItem`).

| URL (`/api/buying/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `supplier-groups/` · `suppliers/` | CRUD | Buying masters | Auth + RBAC | O(1) | ~ | resp. serializers | SupplierGroup / Supplier | No | |
| `purchase-orders/` `…/{pk}/` | CRUD | Purchase orders | Auth + RBAC | O(1) | ~ | `PurchaseOrderSerializer` | PurchaseOrder, PurchaseOrderItem | No (draft) | Submittable |
| `purchase-orders/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Confirm / cancel PO | Auth + RBAC | Posting | ~ | `PurchaseOrderSerializer` | PurchaseOrder | **Yes** | |
| `purchase-orders/{pk}/make-purchase-invoice/` | POST | Map a **submitted** PO to a **draft** Purchase Invoice for the not-yet-invoiced qty (no body). 201 → `PurchaseInvoiceSerializer`; 400 `{"error"}` as for sales | Auth + RBAC: `can_read` on `buying.PurchaseOrder` **and** `can_create` on `buying.PurchaseInvoice` (else 403) | Aggregate | ✅ | `PurchaseInvoiceSerializer` | PurchaseOrder, PurchaseOrderItem (read) → PurchaseInvoice, PurchaseInvoiceItem (write), Account (read) | **Yes** | `buying/mapping.py`; expense account = Stock account when `update_stock`, else an Expense account |
| `purchase-invoices/` `…/{pk}/` | CRUD | Purchase invoices | Auth + RBAC | O(1) | ~ | `PurchaseInvoiceSerializer` (+ read-only `purchase_order_name` (nullable), `credit_to_name`, `expense_account_name`) | PurchaseInvoice, PurchaseInvoiceItem | No (draft) | Submittable |
| `purchase-invoices/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Post invoice (GL + optional stock) | Auth + RBAC | Posting | ~ | `PurchaseInvoiceSerializer` | PurchaseInvoice, GLEntry, (SLE/Bin) | **Yes** | Posts payable + expense/asset |
| `purchase-invoices/{pk}/make-payment-entry/` | POST | Map a **submitted** PI to a **draft** "Pay" Payment Entry for its outstanding amount with one references row (no body). 201 → `PaymentEntrySerializer`; 400 as for sales | Auth + RBAC: `can_read` on `buying.PurchaseInvoice` **and** `can_create` on `accounts.PaymentEntry` (else 403) | Aggregate | ✅ | `PaymentEntrySerializer` | PurchaseInvoice, PaymentEntryReference (read) → PaymentEntry, PaymentEntryReference (write), Account | **Yes** | Pay: `paid_from` = Bank/Cash, `paid_to` = `credit_to` |

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
