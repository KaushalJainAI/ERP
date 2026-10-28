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

### G4. Perpetual inventory — implemented (2026-10-27), with deviations
- With `Company.enable_perpetual_inventory` on, stock-moving documents post inventory GL
  from the SLE `stock_value_difference`, so GL == `Bin.stock_value`:
  `PurchaseInvoice` → `default_inventory_account Dr` (non-stock lines stay on the expense
  account); `SalesInvoice` → also `default_expense_account (COGS) Dr / inventory Cr`;
  `StockEntry` receipt / issue ↔ `stock_adjustment_account`; a transfer posts no GL.
  `verify_integrity` checks `perpetual_inventory_balance` per company.
- **Deviation — default off.** ERPNext defaults perpetual inventory to on; our model
  default is `False` because existing test fixtures build companies without the stock
  accounts. Real companies must turn it on (`seed_demo` does so on a fresh database).
  Flip the default once the fixtures carry the accounts.
- **Deviation — no Purchase Receipt / "Stock Received But Not Billed".** A
  `PurchaseInvoice` with `update_stock=False` still expenses the amount.
- **Gap — manual Journal Entries can still post to the inventory account.** ERPNext
  refuses them ("can only be updated via Stock Transactions"); `verify_integrity` will
  flag the resulting drift until that guard is added.
- Non-stock items (`is_stock_item=False`) no longer get SLEs on invoices.

### G4a. Amend copies child rows (fixed 2026-10-27)
- `SubmittableDocument.amend()` saves the draft and copies every child row set
  (CASCADE, non-submittable related models) in one transaction, then recomputes totals.
  Like ERPNext, it copies line fields as-is; fulfilment-style fields (delivered/billed
  qty) must be reset when such fields are added.

### G5. No tax / GST computation  ⚠️ core Indian-market feature
- `gstin`/`tax_id` are stored on company & parties, and an `AccountType.TAX` exists,
  but there are **no tax templates and no CGST/SGST/IGST lines**. `grand_total` is just
  `sum(qty × rate)` (net total == grand total). ERPNext computes taxes via Sales/Purchase
  Taxes and Charges. Until this lands, invoice totals are tax-exclusive.
  **In progress (2026-10-27):** the masters are in (see "GST masters" below); the tax
  engine, document fields and GL posting follow (docs/gst_design.md, Tasks 2-3).

### G6. No AR/AP outstanding or payment allocation
- Invoices have no `outstanding_amount`; `PaymentEntryReference` rows are stored but
  `PaymentEntry.on_submit` never allocates against an invoice, never sets
  `against_voucher_*`, and never reduces an outstanding balance. There is therefore no
  aging, no partial-settlement, and no paid/unpaid status. ERPNext tracks this through
  GL `against_voucher` + the Payment Ledger.

### G7. Fiscal year — enforced and stamped (2026-10-27)
- `gl.post()` / `ledger.post()` resolve `FiscalYear.for_date(posting_date)` and refuse a
  missing or closed year; every `GLEntry.fiscal_year` is stamped. Cancelling a document
  whose entries fall in a closed (or missing) year is refused, as in ERPNext.
- **Deviation:** `FiscalYear` is global, not per company (ERPNext links companies to a
  fiscal year). No Period Closing Voucher document exists yet.

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

## Document mapping defaults (2026-10-27)

`make-sales-invoice`, `make-purchase-invoice` and `make-payment-entry` (`selling/mapping.py`,
`buying/mapping.py`, `accounts/mapping.py`) mirror ERPNext's `make_sales_invoice` /
`make_purchase_invoice` / `get_payment_entry`, with these deliberate differences:

- **Accounts are resolved by type, not from Company defaults.** `Company` has no default-account
  fields, so within the document's company (non-group, active, lowest id, preferring a fitting
  name): debit_to = `Receivable`, credit_to = `Payable`, income = Income root type, purchase
  expense = a `Stock` account when `update_stock` else a non-COGS Expense account, payment
  counter-account = `Bank`, else `Cash`. A missing account is a 400, never a guess.
- **`update_stock` default** is true only when every mapped line is a stock item with a warehouse
  (a stock-updating invoice line without a warehouse cannot be submitted).
- **Billed qty** counts only **submitted** invoices of the order (as ERPNext), matched per item
  because invoice lines carry no link to the order line.
- **Outstanding** (payment mapping) = invoice grand total less `allocated_amount` on references of
  **submitted** Payment Entries (`reference_doctype` = `selling.SalesInvoice` / `buying.PurchaseInvoice`),
  computed on read; there is no stored outstanding field yet. Posting date defaults to today.
- Permissions: `can_read` on the source and `can_create` on the target (ERPNext requires create
  permission on the mapped doctype).

## Report parameters and GL opening balance (2026-10-27)

- **Profit & Loss** takes an optional period `from_date`..`to_date`; **Balance Sheet** takes an
  as-of `to_date` only (it counts every posting up to and including it, `from_date` is ignored).
  No parameters = all time. ERPNext additionally rolls closing-period/period-closing vouchers and
  shows unclosed P&L inside Equity on the balance sheet; we do not (yet): `total_equity` is the
  Equity-root ledger balance only.
- **General Ledger** returns `opening_balance` (net debit-credit before `from_date`) only when an
  `account` filter is given, and the running `balance` starts from it (ERPNext shows an "Opening"
  row; we expose it as a value rather than a synthetic row so `entries` stay real GL facts).
  Without an `account` the running balance would mix accounts, so `opening_balance`, per-row
  `balance` and `closing_balance` are `null` (ERPNext's ungrouped GL has a running total column
  too; we omit it deliberately).
- Malformed `from_date`/`to_date` on the reports is a 400, not ignored.

## User management (2026-10-27)

- **Users are deactivated, never deleted** (`DELETE /api/auth/users/{id}/` is 405), as in ERPNext
  (`enabled = 0`); audit rows and documents reference them.
- ERPNext's User doctype also carries `Has Role` child rows edited on the form; ours is the flat
  `role_ids` list on the user. `username` is immutable after creation (ERPNext keys the User on
  its email and also forbids renaming without the rename tool).
- `is_superuser` / `is_staff` can **not** be set over the API (ERPNext lets a System Manager grant
  System Manager and the Administrator user is fixed): superuser is only granted from the Django
  admin / shell. A non-superuser (even a System Manager) cannot modify, reset the password of or
  deactivate a superuser account, so the System Manager role cannot be used to take one over.
- Nobody can deactivate themselves or change their own roles; the admin `set-password` action is
  refused on your own account (own password: `me/change-password`, which needs the old password
  and is throttled). ERPNext allows an admin to do all of these to themselves.
- Role assignment is its own right (`can_write` on `users.UserRole`), separate from editing the
  profile (`users.User`).
- Changing a password does not invalidate already-issued JWTs (they expire on their own);
  deactivation does take effect immediately.

## GST masters (2026-10-27, docs/gst_design.md Task 1)

- **Percentage fields are `DecimalField(7, 3)`** (tax and GST rates, `0..100` CHECK), so 0.125 and
  2.5 are representable. CLAUDE.md declares no precision for percentages; this is the rule until it
  does (CLAUDE.md itself is deliberately not edited here).
- **Item Tax Templates are global and keyed by GST component** (CGST/SGST/IGST/CESS), not
  per-company and keyed by account as in ERPNext, because our `Item` has no company. The engine
  maps a component to the company's tagged account at calculation time.
- **One `TaxTemplate` model for Sales and Purchase** (`template_type`) instead of ERPNext's two
  doctypes. v1 allows `charge_type = On Net Total` and `category = Total` only (DB CHECKs).
  Added a `UNIQUE(template, account)` guard on template rows (not in ERPNext; mirrors the
  per-document tax-row rule so a template can never expand into an invalid document).
- **GST accounts are identified by two nullable tags on `Account`** (`gst_direction`,
  `gst_component`; both-or-neither, Tax type only, unique per company) instead of India
  Compliance's "GST Settings -> GST Accounts" table. The legacy bare `CGST`/`SGST`/`IGST`
  liability accounts are tagged Output by migration `accounts.0006` and keep their names;
  `seed_defaults` no longer creates them for new databases (it seeds `Output/Input/RCM Payable
  CGST/SGST/IGST` and `Round Off` instead).
- **GST 2.0 slabs seeded** (effective 22 Sep 2025): 0, 0.25, 3, 5, 18, 40. 12 and 28 are
  seeded with `is_legacy = true` for invoices raised under the old structure. Also `Nil Rated`,
  `Exempted`, `Non-GST` zero-rate templates.
- **State master** is `core.State` with the 2-digit GST code as primary key (40 rows, incl. retired
  25 and 28, which remain valid inside older GSTINs). GSTIN validation checks the prefix against
  the constant `core/gst_states.py`, not the table, so it needs no query.
- **GSTIN is validated at the API layer only** (field validator + `validate_party_gst`), never in
  `save()` and never by a DB constraint: ORM/import writers (Tally importer, fixtures) may store
  legacy values that fail the checksum. Documents re-validate the snapshot GSTIN at submit
  (Task 2). Customer/Supplier default `gst_category = Unregistered` even if an importer stores a
  GSTIN; migrations `selling.0004` / `buying.0004` backfill existing rows (GSTIN present =>
  Registered Regular, state from the prefix).
- **"Consumer" is a UI-only alias of Unregistered**: identical in every report and rule.
- **`Company.enable_rounded_total` defaults False** (ERPNext defaults rounding on), like
  `enable_perpetual_inventory` (G4); the seeded ACME company is switched on the first time its
  GST masters are seeded and an operator's later choice is not overwritten.
- **B2CL thresholds** are constants in `gst/constants.py` (1,00,000 from 2024-08-01, 2,50,000
  before), taken from memory of the notification, not verifiable in this repo.
- The seeds never invent a company GSTIN or state (production data); they come from `seed_demo`.

## GST tax engine and document model (2026-10-28, docs/gst_design.md Task 2)

- **`grand_total` is now tax-inclusive** on SO/SI/PO/PI, with `net_total` (sum of line amounts),
  `total_taxes_and_charges`, `rounding_adjustment` and `rounded_total`. DB CHECKs enforce
  `grand_total = net_total + total_taxes_and_charges` and `rounded_total = grand_total +
  rounding_adjustment`, with a half-paisa tolerance because SQLite evaluates DECIMAL arithmetic in
  floating point (exact on PostgreSQL). Existing documents are backfilled `net_total =
  rounded_total = grand_total`, zero tax, `gst_category = ""` (legacy marker).
- **`rounded_total` is always populated** (ERPNext zeroes it when rounding is off); rounding is
  to the nearest rupee, `ROUND_HALF_UP`, when `Company.enable_rounded_total` is on.
- **Document-level rounding as in ERPNext**: per-line tax is kept unrounded, each tax row is
  rounded once. The per-line `cgst/sgst/igst_amount` columns are rounded per line and the residual
  goes to the last taxed line (a negative residual spills backwards so no line goes below 0).
- **Item tax rates are snapshotted on the line** (`item_tax_rate`, `gst_treatment`, `gst_hsn_code`):
  re-read from the Item master on every draft save, frozen at submit and amend (ERPNext keeps an
  `item_tax_rate` JSON on the line too). A never-calculated draft (ORM-created / legacy) is
  resolved once at submit.
- **Tax rows (`taxes`) validate at draft time**, not only at submit: an edit that leaves the rows
  inconsistent with the supply kind is a 400 telling the user to re-apply the template; the server
  never rewrites rows silently. GST-tagged rows are refused when the company has no GSTIN (or is a
  composition taxpayer), for composition suppliers, for unregistered / overseas suppliers without
  reverse charge, for SEZ / overseas customers without `is_export_with_gst`, and as Deduct rows.
- **Reverse charge** (purchase only): GST rows are memo rows -- computed and shown, excluded from
  `grand_total`; `total_taxes_and_charges` is 0. Sales `is_reverse_charge` is rejected.
- **Legacy / ORM-imported parties**: a party stored as Unregistered / Consumer with a GSTIN that
  passes the checksum is treated as **Registered Regular** on documents; one with an invalid GSTIN
  is treated as unregistered (GSTIN ignored). Party location = `state`, else the GSTIN prefix, else
  the company state (for B2C place of supply and for purchases).
- **Submit gate** (company has a GSTIN): place of supply, HSN/SAC (digits only, 4/6/8, at least
  `Company.hsn_min_digits`, Non-GST lines exempt), valid party GSTIN + state-prefix match for
  Registered / Composition / SEZ parties, valid company GSTIN, SI number at most 16 characters.
  Applied to orders too, not only invoices.
- ~~Temporary posting guard~~ removed by Task 3 (below).

## GST GL posting, mapping, integrity (2026-10-28, docs/gst_design.md Task 3)

- **Sales Invoice voucher**: Dr party `rounded_total` (one line, so payment allocation can set
  `against_voucher` on it); Cr income `net_total`; Cr each Output tax row (a Deduct row is a Dr);
  `rounding_adjustment` on `Company.round_off_account` (Cr when positive, Dr when negative);
  perpetual-inventory COGS / inventory pair unchanged (tax never touches the stock legs).
- **Purchase Invoice voucher**: Dr expense / inventory `net_total - stock_value` + `stock_value`
  (recoverable input tax is excluded from the stock value by construction: the SLE rate is the
  basic rate); Dr each Input tax row (Cr a Deduct row); Cr party `rounded_total`; round-off Dr when
  positive, Cr when negative. `verify_integrity.perpetual_inventory_balance` stays exact.
- **Reverse charge**: GST rows are memo rows (not in `grand_total`); the voucher adds
  `Dr Input X / Cr RCM Payable X` per row (the RCM Payable account is looked up as
  `(company, "Reverse Charge", component)`; submit is refused before any posting if it is missing).
  The supplier is credited the net value only. ITC is booked on the invoice date (design 11.6).
- **Submit refuses early** (before stock or GL is touched) when a non-zero rounding adjustment has
  no round-off account on the company, or the RCM Payable account is missing.
- **Mapping**: SO/PO -> SI/PI copies `taxes_and_charges`, the tax rows (input fields only), the
  place of supply, the export / RCM flags and the lines' GST snapshots (HSN, treatment, item tax
  template, `item_tax_rate`, as ERPNext copies `item_tax_template` / `item_tax_rate`), then runs
  the engine on the remaining quantity. Party snapshots are refreshed from the master.
- **Outstanding** (`accounts.mapping.invoice_outstanding`, `make-payment-entry`) is
  `rounded_total` less submitted allocations. Dashboard sales / purchase totals and the open-order
  comparison use `net_total` (GST is a pass-through); receivables / payables are GL-derived.
- **Line `amount` rounding is now half up** (`TaxableLine.compute_amount`), like the tax engine
  and the stock ledger (G3), so `sum(line.amount)` equals the SLE stock value of the same lines at
  exact ties (e.g. 0.5 x 0.01). The previous Decimal-default half-even differed only at those ties.
  ERPNext's `flt()` follows the System Settings "Rounding Method" (Banker's by default in recent
  versions); the clone has no frappe core to confirm, so this is a deliberate G3-consistent choice.
- **Stale tax rows are re-derived, not refused** (supersedes the Task 2 400): a draft update that
  changes the party, `place_of_supply` or export / RCM flag without sending `taxes` and without a
  template, and whose stored GST rows no longer fit the supply kind, drops the rows and re-selects
  the company default template for the new kind (none when GST does not apply). ERPNext likewise
  re-applies taxes when the party changes. Other structural errors stay 400. Unrelated edits leave
  the stored rows alone.
- **Dry run** (`taxes/calculate/`) also returns `taxes_and_charges_name`, `place_of_supply` and
  `place_of_supply_name`; the tax-template list takes `?supply_kind=INTRA|INTER` (that type or blank).
- **`verify_integrity` GST checks** (`gst/services/integrity.py`, recorded as `gst_integrity`):
  hard errors for the totals identity, tax rows vs line GST columns, GL voucher vs document (party
  line = `rounded_total`, per-row tax lines, RCM pair, round-off side, income/expense = `net_total`),
  kind consistency, account tagging and non-taxable lines carrying GST; warnings (never failing)
  for invalid GSTINs on masters / snapshots, missing place of supply or HSN, and the count of
  legacy documents. Kind consistency for purchases uses the snapshot GSTIN prefix, else the
  supplier's current state.
