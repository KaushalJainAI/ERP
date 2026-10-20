# Architectural Comparison & ERPNext Divergences

## Summary
This document outlines key technical decisions and architectural enhancements made when replicating ERPNext's business domain onto a Django 5 + PostgreSQL + React Vite stack.

## Key Architecture Divergences

### 1. Modern Stack vs Frappe Framework
- **ERPNext**: Built on the Frappe framework (Python backend + custom JS frontend with DocTypes).
- **Custom ERP**: Built on standard Django 5 + Django REST Framework + React 18 Vite + TypeScript + TanStack Query. Provides full type safety, OpenAPI standard documentation (`drf-spectacular`), and standard REST endpoints.

### 2. Immutable Ledger Enforcement
- **ERPNext**: Uses DocType submit/cancel mechanics, updating GL and Stock entry rows.
- **Custom ERP**: Enforces strict append-only immutability on `GLEntry` and `StockLedgerEntry`. Any attempt to update or delete existing ledger rows via API or ORM raises a `ValidationError`. Reversals are recorded as new audit rows with swapped debit/credit or negative quantities and `is_cancelled=True`.

### 3. Self-Healing & Invariant Engine
- Includes automated self-healing tools (`verify_integrity`, `repost_stock`, `repost_gl`) that verify 5 key ledger invariants and automatically rebuild derived projections like `Bin` from `StockLedgerEntry` history.

### 4. Indian Financial & Multilingual Support
- Built-in support for Indian numbering system (Lakh/Crore grouping: `₹12,34,567.89`), GSTIN validation, and 13 supported languages including Indic scripts and RTL layout for Urdu (`ur`).

## Known Gaps (tracked, not yet at ERPNext parity)

### G1. Stock valuation is Moving Average only
- `Item.valuation_method` exposes both `FIFO` and `Moving Average`, but the stock
  ledger currently values every item using **Moving Average** (a single
  `valuation_rate`/`stock_value` per `Bin`). Items flagged `FIFO` are silently
  valued as Moving Average.
- True FIFO requires per-layer remaining-quantity tracking (a stock queue), which
  is a schema-level feature. Until it lands, do not rely on FIFO COGS matching
  ERPNext. Tracked as a dedicated checklist item.

### G2. Ledger immutability is enforced at the application layer
- `GLEntry`/`StockLedgerEntry` immutability and the submitted-document lock are
  enforced in the model `save()`/`delete()` overrides, not by DB triggers. Raw
  `QuerySet.update()`/`bulk_update()`/SQL can still bypass them.
- A blanket DB-level "no UPDATE/DELETE" trigger is incompatible with the current
  cancellation design, which flips `is_cancelled` on the original row. A future
  trigger must permit that single-column toggle only. Tracked separately.

### G3. Rounding
- Money/qty/rate quantization uses `ROUND_HALF_UP` (Indian invoicing convention),
  applied consistently in `accounts.services.gl` and `stock.services.ledger`.

### G4. No COGS / perpetual-inventory GL on stock-moving invoices  ⚠️ accounting integrity
- A `SalesInvoice` with `update_stock=True` posts **revenue** (`Debtors Dr / Income Cr`)
  and a stock-out SLE, but **no cost entry** (`COGS Dr / Stock-in-Hand Cr`). Inventory
  value leaves `Bin.stock_value` with nothing recorded in the GL.
- A `PurchaseInvoice` books `Expense Dr / Creditors Cr` (periodic style) while *also*
  posting a stock-in SLE — the value lands in a P&L expense account, not a
  Stock-in-Hand asset.
- Net effect: the GL's inventory asset / COGS do **not** reconcile with the stock
  ledger's `stock_value`. ERPNext keeps perpetual inventory in sync (Stock-in-Hand
  movements, Stock Received But Not Billed). Tracked as a dedicated checklist item.

### G5. No tax / GST computation  ⚠️ core Indian-market feature
- `gstin`/`tax_id` are stored on company & parties, and an `AccountType.TAX` exists,
  but there are **no tax templates and no CGST/SGST/IGST lines**. `grand_total` is just
  `sum(qty × rate)` (net total == grand total). ERPNext computes taxes via Sales/Purchase
  Taxes and Charges. Until this lands, invoice totals are tax-exclusive.

### G6. No AR/AP outstanding or payment allocation
- Invoices have no `outstanding_amount`; `PaymentEntryReference` rows are stored but
  `PaymentEntry.on_submit` never allocates against an invoice, never sets
  `against_voucher_*`, and never reduces an outstanding balance. There is therefore no
  aging, no partial-settlement, and no paid/unpaid status. ERPNext tracks this through
  GL `against_voucher` + the Payment Ledger.

### G7. Fiscal year neither enforced nor stamped
- `FiscalYear.is_closed` exists but **nothing blocks posting into a closed or missing
  period**, and `gl.post()` never derives `fiscal_year`, so every `GLEntry.fiscal_year`
  is `NULL`. No period-closing voucher exists.

### G8. Negative stock is always hard-blocked
- Any stock-out that would drive a `Bin` negative raises `ValidationError`
  unconditionally. ERPNext exposes an **"Allow Negative Stock"** setting. Treat ours as a
  deliberate stricter default, or make it configurable — decide and record here.

### G9. Larger absent modules
- No serial/batch tracking, no multi-currency exchange rates, no price lists / discounts,
  no delivery-note ↔ invoice ↔ order fulfilment linkage. Noted for scope, not yet planned.

## Fixed / hardened (2026-07-23)

- **Report endpoints were world-readable (was F1).** `trial_balance`,
  `general_ledger`, `profit_and_loss`, `balance_sheet` and `stock_balance` were
  `IsAuthenticated`-only, and `HasRolePermission` *fails open* for function-based
  views (no inferable `model_label` ⇒ returns `True`). Any authenticated user
  could read every company's financials. Added
  `users.permissions.require_model_permission(model_label, flag)`; the accounting
  reports now require `can_read` on `accounts.GLEntry` and the stock report on
  `stock.Bin`. Covered by `core/test_security.py`.
- **Cancelling a receipt could drive stock negative.** `stock.services.ledger.reverse()`
  subtracted quantity with no floor, so cancelling a receipt whose units were
  later issued/sold left a `Bin` at a negative quantity — silently breaking the
  No-Negative-Stock invariant on the reverse path (G8 only held on the forward
  path). It now raises `ValidationError` and, being atomic, leaves the document
  submitted (clean 400 over the API). Aligns cancellation with G8's strict stance.
- **Valuation drift was invisible to self-healing.** `verify_integrity` checked
  `Bin` qty and `stock_value` but not `valuation_rate`; it now also asserts
  `valuation_rate == stock_value / actual_qty`.
- **Unordered pagination silently dropped/duplicated rows.** Every list endpoint
  used `PageNumberPagination` with no default `ordering`, so pages came back in
  undefined DB order. New `core.pagination.StablePageNumberPagination` applies a
  stable `pk` tie-breaker to any unordered queryset (explicit ordering untouched).
- **Line-item and cross-company validation.** Transactional lines reject
  `qty <= 0` and negative `rate`/`valuation_rate`; `SalesInvoice`/`PurchaseInvoice`/
  `PaymentEntry`/`JournalEntry` reject accounts, parties, and lines from another
  company, and `PaymentEntry` rejects `paid_from == paid_to`. Enforced at the
  submit gate (the real guarantee) plus serializer field validation for fast
  feedback.
- **Atomic nested create.** Document + nested lines + totals are now created
  inside `transaction.atomic()`, so a mid-create failure can't leave an orphaned
  header with partial lines.
- **Audit ordering & honesty.** `perform_destroy` records the audit row after a
  successful delete (no phantom entries on refused deletes); the `gl.repost()`
  docstring now states it validates rather than rebuilds (balances are derived on
  read).

## Fixed / hardened (2026-10-21) — RBAC

A full pass over `users/permissions.py`, `users/views.py` and
`core/management/commands/seed_defaults.py` closed three defects in the
authorization layer. ERPNext reference: Frappe's `Permissions` + `Role` /
`User Role` use the same *role → per-DocType → per-action* shape, but
`frappe.permissions.has_permission` **fails closed** (`permlevel` unset ⇒
deny) and `is_admin` is an explicit role rather than the `is_staff` flag. We
now match that posture.

- **`is_staff` no longer bypasses RBAC (was P0.3).** In Django `is_staff` only
  means "may sign in to the admin site". `HasRolePermission` and
  `user_has_model_permission` treated it as an all-power bypass, so every
  staff-flagged operator could read every company's books, post documents, and
  cancel them. Only `is_superuser` bypasses now. Staff keep admin-site access
  through `ModelAdmin`; API authority comes from roles.
- **The permission check now fails closed (was P0.4).** When a view could not
  be tied to a model, `HasRolePermission` returned `True` — allow. It now
  returns `False` — deny. Function-based report endpoints, which expose no
  queryset, keep working because they declare their model explicitly through
  `require_model_permission(...)`.
- **The fail-open was worse than it looked: it also hit empty tables.** Model
  resolution used truthiness checks (`if not model`), and a Django `QuerySet`
  is *falsy when it has no rows*. On a fresh install — or any table the user
  had not written to yet — every ViewSet failed to resolve its model and was
  allowed through. Resolution now uses `is None` checks throughout
  (`users/permissions.resolve_model_label`).
- **Role management moved off `IsAdminUser` onto RBAC.** `RoleViewSet` and
  `RolePermissionViewSet` were gated on `is_staff`, which — combined with the
  bypass above — let any staff user grant themselves `can_read` on
  `accounts.GLEntry` and read the whole ledger. They now require
  `HasRolePermission` on `users.Role` / `users.RolePermission`, granted to the
  seeded *System Manager* role (and to superusers).
- **The seeded permission matrix was wrong in three ways, and one of them made
  every report unreachable.** The hand-maintained label list pointed
  `accounts.SalesInvoice` at a model that lives in the `selling` app (and
  `accounts.PurchaseInvoice` at `buying`), listed three labels for models that
  do not exist (`selling.Quotation`, `selling.DeliveryNote`,
  `buying.PurchaseReceipt`), and **omitted `accounts.GLEntry` and `stock.Bin`**
  — the exact two labels the report endpoints are gated on. No seeded role
  could read a single report; only `is_staff` could get in. The seed now
  derives its labels from a single matrix, **validates every label against the
  app registry** (a bad label aborts the seed with a clear error instead of
  silently granting authority on nothing), and prunes rows that no longer
  resolve.
- **Every manager role held every permission on every model.** "Sales Manager"
  and "System Manager" were the same account wearing different hats. The seed
  is now *authoritative and scoped* per role: each role carries only its own
  domain, and read access to `accounts.GLEntry` / `stock.Bin` — i.e. to the
  financial and stock reports — is reserved for the Accounts and Stock
  Managers. Re-running `seed_defaults` repairs a database seeded by the older
  version (it reports granted/updated/revoked counts); roles the operator
  created themselves are never touched.

## Fixed / hardened (2026-07-22)

- **Submitted-document write over the API returned HTTP 500.** A `PATCH`/`DELETE` on a
  submitted or cancelled document raised Django's `ValidationError`, which DRF's generic
  views do not translate — leaking a 500 (and, under `DEBUG`, a stack trace). Now
  `core.mixins.SubmittableViewSetMixin.update()/destroy()` return a clean **409 Conflict**.
- **API rate limiting added.** DRF `AnonRateThrottle` / `UserRateThrottle` /
  `ScopedRateThrottle` are configured (`config/settings.py`), and the JWT login & refresh
  endpoints run under a tight `auth` scope (default `10/min`) to blunt credential
  stuffing — see `users.views.ThrottledTokenObtainPairView`. Rates are env-overridable
  (`THROTTLE_ANON` / `THROTTLE_USER` / `THROTTLE_AUTH`). **Note:** throttle counters use
  the Django cache; in multi-worker production set a shared cache (Redis) or the limit is
  enforced per-worker.

## Open findings (not yet fixed)

- **F2. Auto-naming is dead code.** The serializers list `name` as writable (it is *not*
  in `read_only_fields`), so DRF marks it **required** and the `NamingSeries.next(...)`
  fallback inside each `create()` never runs. Either make `name` read-only and always
  auto-generate, or drop the fallback. Today every document must be named by the client.

> A broader, prioritised improvement backlog (perpetual inventory/COGS, fiscal-period
> lock, GST, AR/AP, the `is_staff` RBAC bypass, and more) lives in the repo-root
> **`improvements.md`**. F1 above was fixed on 2026-07-23 — see the fix log.
