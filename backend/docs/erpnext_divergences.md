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

### G6. Invoice outstanding, payment allocation and status (implemented 2026-10-28)
- Sales / Purchase Invoices carry a **derived** `outstanding_amount` (CHECK `>= 0`) and
  `status` (Draft / Unpaid / Partly Paid / Paid / Cancelled). On submit the invoice's own
  party GL line is tagged `against_voucher_type/no` = itself (ERPNext), and a Payment Entry
  splits its party leg into one line per `PaymentEntryReference` tagged against that invoice
  plus an untagged remainder (advance / on account). Outstanding = Σ of the live tagged rows
  on the party account (`accounts/services/outstanding.py`), refreshed inside the posting
  transaction under a row lock; `repost_outstanding` rebuilds it and `verify_integrity`
  (`invoice_outstanding`) detects drift. An invoice with a submitted payment cannot be
  cancelled (ERPNext default); `is_overdue` / `display_status = "Overdue"` are computed, not stored.
- **Deviation:** no Payment Ledger Entry table; the GL `against_voucher` tags are the only
  link. Allocation lives on `PaymentEntryReference` only (Sales / Purchase Invoice).
- **Deviation:** no Journal Entry / credit-note / return allocation, no unallocated-advance
  matching to later invoices (the untagged remainder stays on account), no write-off or
  exchange-gain/loss, no payment terms schedule; single-currency.
- **Deviation:** the invoice and the payment must use the same party account (ERPNext nets per
  party across accounts less strictly), and the allocated amount may not exceed the invoice's
  current outstanding (ERPNext allows over-allocation to be flagged as unallocated).
- **Deviation:** legacy invoices (submitted before tagging) have an untagged own row; they use
  the document formula `rounded_total - submitted allocations` and are not re-tagged (GL is immutable).
- **Deviation (Accounts Receivable / Payable ageing, `reports/ageing/`):** `as_on` uses each
  invoice's *current* outstanding, not its historical balance on that date; invoices not yet due
  fall in the 0-30 bucket; ageing is by due date (posting date if none); fixed buckets 30/60/90.

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
- No serial/batch tracking on delivery notes, no multi-currency exchange rates, no item
  discounts (price lists are now implemented), no delivery-note ↔ invoice ↔ order
  fulfilment linkage. Noted for scope, not yet planned.

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

## GST reports (2026-10-28, docs/gst_design.md Task 4a)

- **Not the GSTN JSON**: field names are ours (a `?shape=gstn` variant is a later, additive change). No CDNR /
  CDNUR / amendment tables (no credit notes in v1), no cess, no e-invoice or portal upload.
- **Invoice-bearing GSTR-1 sections list Taxable lines only** (rates, including a 0% row for zero-rated
  exports / SEZ without payment); Nil-Rated / Exempted / Non-GST lines go to `nil_exempt_non_gst` (Table 8),
  and exports' non-taxable lines are not reported there. An invoice with no taxable line has no invoice row.
- **B2CL** needs `rounded_total` strictly above the threshold in force on the posting date (a boundary value is
  B2CS); SEZ and exports are never B2CL. **HSN** skips Non-GST lines; a blank HSN is excluded and counted in
  `warnings.lines_without_hsn`; an empty UQC is reported as `OTH`; exports count in the `b2c` half.
- **GSTR-3B**: SEZ and export taxable lines (with or without payment) are 3.1(b); nil + exempt lines are 3.1(c).
  Table 5 is inter / intra totals of non-taxable inward lines plus composition and non-RCM unregistered supplier
  lines (the supplier State is the GSTIN prefix, else the master). ITC (4A(3) RCM, 4A(5) other) is the line
  columns of the period's invoices, booked on the invoice date (design 11.6). 6.1 is informational: output =
  3.1(a)+(b)+(d), ITC = 4C, no cash / credit ledger offset order.
- **Tax ledger** `party` is the voucher's receivable / payable party (tax GL rows carry none).

## Batches (2026-10-28, docs/manufacturing_design.md section 1, phase-3 Task 1a)

- **No Serial and Batch Bundle document and no serial numbers.** `batch` is a direct nullable FK on
  `StockLedgerEntry` and `StockEntryItem`; one batch per line (a multi-batch issue is several lines).
- **Batch balance is derived, not stored**: `SUM(qty)` over the batch's non-cancelled SLE in a warehouse
  (`stock/services/batches.py::batch_balance`). There is no per-batch Bin (ERPNext's `Batch.batch_qty` is also
  only a cache). The "no negative batch" guard in `ledger.post()` / `reverse()` runs under the item+warehouse
  Bin lock, so it is race-free; there is no DB constraint for it (it is derived), and
  `batches.integrity_errors()` detects it.
- **Valuation stays Moving Average (G1).** For batch-tracked items the *outgoing* rate is the batch's own
  average in that warehouse (`SUM(value)/SUM(qty)`, ERPNext's default batch-wise valuation); the Bin remains the
  sum of its batches. If a batch has no positive cost the leg falls back to the Bin rate, then the passed rate
  (the non-batch behaviour). Issuing a batch's whole balance releases exactly its value (no stranded paisa).
  FIFO is not attempted.
- **Batch is mandatory on every leg of a batch-tracked item** (and forbidden on others): asserted centrally in
  `ledger.post()`, and at the Stock Entry submit gate with a FEFO suggestion ("Batch required for Item X. FEFO
  suggests B1 (400.000), B2 (100.000)."). ERPNext auto-picks batches; we do not, so the document records the lot
  actually issued. Allocation is read-only (`batches/fefo/`), nothing is reserved.
- **Expiry blocking**: any stock-out of a batch whose `expiry_date < posting_date` is refused, including
  Material Transfer, except **Material Issue** (the write-off path; `allow_expired`). No minimum-remaining-
  shelf-life rule. An expiry date is only allowed on items with `has_expiry_date`.
- **Batch numbering** `ITEMCODE-YYMMDD-NN` (NamingSeries, per posting date); a manual `batch_no` is always
  allowed. Auto numbering is refused when the prefix would not fit `NamingSeries.prefix` (40 chars): enter the
  number manually. User decision 2026-10-28.
- **Inward batches**: a Material Receipt line either picks an existing batch or carries the new-batch fields
  (`supplier_batch_no`, `manufacturing_date`, `expiry_date`); the batch is created at submit inside the posting
  transaction and written back to the line, with `reference_doctype/reference_name` naming the voucher
  (provenance; deliberately no Supplier FK). Expiry = manufacturing date (default: posting date) +
  `Item.shelf_life_in_days`; refused if an expiry-tracked item has no expiry source. Picking and entering is a
  400. `amend()` copies `batch`, so a re-submitted amendment reuses the same lot (the batch's reference keeps
  naming the first voucher).
- **Item flags frozen once stock exists**: `has_batch_no` / `has_expiry_date` cannot change after the item has any
  SLE (serializer 400, and `Item.save()` raises for the ORM/admin). There is no stock reconciliation document to
  batch-assign opening stock. DB CHECKs: expiry requires batch tracking; shelf life only with expiry and > 0.
- **Batch master**: `item` is immutable once the batch has SLEs, `batch_no` cannot be renamed, `expiry_date`
  stays editable; delete of a used batch is 409 (disable it instead); a disabled batch is refused on every leg.
  Provenance fields are server-owned (read-only in the API).
- **Reverse guard**: cancelling an inward leg that would leave its batch negative is refused ("cancel the later
  transactions first"), in addition to the Bin guard; reversal rows copy `batch`.
- New optional SLE keys on `ledger.post()`: `batch`, `incoming_value` (exact stock-in value; rate = value/qty)
  and `allow_expired`.

## Manufacturing (2026-10-28, docs/manufacturing_design.md)

- **BOM is a master, not a document.** It has no `docstatus`; a submitted Work Order or Stock Entry snapshots it and locks edits (409). Costs are derived on read from the live rows, never stored.
- **Work Order quantity is good output (design section 14.2 overrides 3/10).** `qty` is the desired saleable output; required raw material is grossed up by `1 / (1 - process_loss_percentage/100)`. `fg_completed_qty` is good output. `process_loss_qty` is server-owned actual loss: the BOM basis represented by the consumed components minus the good output. The old `process_loss_qty <= fg_completed_qty` CHECK is dropped; loss can exceed output above 50% loss.
- **WIP is its own balance-sheet line.** `Warehouse.account` is a nullable FK; a warehouse posts stock to its own account when set, otherwise to the company default inventory account. `seed_defaults` creates a `Work In Progress` Stock account. The design's "manufacturing vouchers post no GL" check is superseded: transfer and manufacture vouchers post through the same per-account perpetual path as every other stock document.
- **No serial numbers and no Serial and Batch Bundle.** One batch per line; multi-batch issues are multiple rows. `Manufacture` auto-creates a finished-goods batch with the item shelf life; `Repack` does the same. Consumption uses FEFO and refuses expired lots.
- **Repack** allocates consumed value by `cost_allocation_per` (must total 100) over its finished rows; it has no process loss and cannot reference a Work Order.
- **Production Yield** is reported per manufacturing voucher, with `process_loss_qty` and `loss_percent = loss / (output + loss) * 100`.

## Price lists and credit limits (2026-10-28)

- **Price lists are masters in `stock`.** `PriceList` and `ItemPrice` follow ERPNext's shape, including the UOM- and date-bounded rows and the partial unique constraints. v1 has no currency conversion: a list whose `currency` differs from the company currency is refused on use.
- **One resolver for every document.** The explicit header list wins, then the party's own list, then the nearest customer-group ancestor, then the company default. A supplied line `rate` is never overwritten; an omitted rate is filled from the list, then the item's `standard_rate`, otherwise 400. The line keeps a `price_list_rate` snapshot at 6 dp.
- **Credit exposure is GL-derived.** `credit_limit == 0` means no limit. Exposure = the customer's live Receivable balance (`accounts.services.outstanding.customer_outstanding`) + the remaining value of other submitted Sales Orders prorated by their `rounded_total` + the document being submitted. The gate is strictly greater-than, matching ERPNext.
- **Override is audited, not silent.** A superuser or a `Credit Controller` role member may submit past the limit; the audit log gets a `credit_limit_override` row with the limit, existing exposure, document total and resulting exposure. Without a user the check fails closed.
- **`update-rates` is draft-only.** It re-resolves every line and recalculates taxes and totals; submitted documents return **409**. The mapping from an order to its invoice copies the header list and each line's snapshot.

## Tally history, addresses, Journal Entry header (2026-10-28)

- **Address links to one party.** ERPNext links an `Address` to any number of parties through Dynamic Links. Here an address has a `customer` *or* a `supplier` FK (DB CHECK: exactly one). A party that is both gets one row per side. One primary address per party (partial UNIQUE). `city` is optional (ERPNext requires it): Tally's free-text address lines don't say which line is the town, so imported addresses leave it blank rather than guess.
- **Journal Entry `voucher_type` is informational.** It takes ERPNext's values. ERPNext requires `cheque_no` / `cheque_date` on a Bank Entry; this ERP does not, because 20,000+ imported Tally receipts and payments carry no instrument details (never extracted). `cheque_no` holds Tally's voucher reference for most types, `bill_no` the supplier bill reference for Purchase / Debit Note.
- **The `legacy` app (Tally archive) was removed on 2026-10-30.** Its tables (`TallyRecord` 63,004 rows, `TallyItemLine` 102,681) were dropped from the ERP database, with the API, UI and `import_tally` loader; the data stays in the external `tally-pg` database and in `backend/backups/legacy_tally_archive_2026-10-30.sql.gz` (git-ignored). Register reports now read Tally's voucher type/number from the `TLY-*` journal's `user_remark`; party-wise last rates live in `Rates - <party>` price lists; ledger/item name maps live in `tally_export`.
- **Item tax template from Tally.** The importer sets each item's `item_tax_template` from Tally's latest IGST rate (`GST n%`, `Nil Rated`, `Exempted`, `Non-GST`), including the legacy 12% / 28% slabs when Tally still has them.

## HR and payroll (2026-10-28)

ERPNext core only ships the employee masters (`setup/doctype`: Employee, Department, Designation, Branch, Holiday List); attendance, leave and payroll live in the separate hrms app, which is not in `erpnext/`. The `hr` app mirrors the masters (trimmed) and **designs payroll itself** for a daily-wage workforce.

- **Payroll is custom, not hrms.** There is no Salary Structure, Salary Structure Assignment, Payroll Period, Additional Salary, Employee Loan or Payment Entry-for-salary. Pay is set **per employee** in `EmployeeWageRate` (effective-dated: `from_date`, `daily_rate` for Daily or `monthly_salary` for Monthly staff, `ot_hourly_rate`). The rate for a date is the latest row with `from_date <= date`. A rate row is frozen (400) once a submitted slip of that employee ends on or after its `from_date`; pay changes are new, later-dated rows.
- **Pay any day.** A `SalarySlip` covers any `start_date..end_date` (max 366 days); there is no payroll frequency. Submitted slips of one employee may not overlap (checked under a row lock on the employee, and by `verify_integrity`). `PayrollEntry` is only a convenience to draft/submit many slips for a range and filters, not a period of record.
- **Day rules.** Present = 1 day, Half Day = 0.5, Paid Leave = 1, Absent / no record = 0. Daily: units × that day's `daily_rate`. Monthly: units × `monthly_salary / days in that calendar month`; a holiday in the employee's holiday list (weekly offs included) with no attendance record is one paid day, **Monthly staff only**. Overtime = `ot_hours × that day's ot_hourly_rate`. Days before joining / after relieving earn nothing. Paid Leave stands in for the leave module (checklist 7.3 deferred).
- **Attendance is tracked and locked.** `Attendance` is a plain record, UNIQUE(employee, date), that cannot be created, changed or deleted while a **submitted** slip of the employee covers its date (model-level `ValidationError`, API 400); cancelling the slip unlocks it. A draft slip stores generated Wages / Overtime rows that are rebuilt on every draft save and re-verified at submit ("attendance or rates changed, recompute"), so a posted slip always equals the attendance it locked.
- **Components, not hard-coded columns.** Earnings and deductions are `SalaryComponent` rows with a GL account. Wages, Overtime and Advance Recovery are seeded system components; PF / ESI will be added later as ordinary deduction components. No PF/ESI logic exists today (`uan`, `esic_no` are inert fields).
- **HRSettings instead of Company fields.** ERPNext keeps payroll accounts on `Company`; here a per-company `HRSettings` holds the wage expense, payroll payable, employee advance and default payment accounts (created by `ensure_hr_setup`, also called from `seed_defaults`; changing the payable/advance accounts is refused once anything is posted). A slip computed for a company without settings is a clear 400, never an implicit account creation.
- **Posting.** Slip submit: Dr each earning component's account (gross); Cr payroll payable (net, party `Employee`); Cr employee advances (party `Employee`, tagged against the advance) for recovery rows; Cr the component account for any other deduction. Cancel = `gl.reverse`; refused if the employee's wages payable would go negative (the wages were already paid).
- **`EmployeeAdvance` and `EmployeePayment` are their own documents**, not Payment Entry / Employee Advance-in-hrms. Advance: Dr employee advances (party `Employee`) / Cr cash or bank. Payment: Dr payroll payable (party `Employee`) / Cr cash or bank, refused when it exceeds the employee's wages payable (live GL balance). `PayrollEntry.make-payments` creates and submits one payment per slip in one transaction.
- **Advance outstanding is derived, not stored.** `recovered_amount` = recovery rows of submitted slips; `outstanding = amount - recovered`; status Paid / Partly Recovered / Recovered is computed on read. Nothing to repost; `verify_integrity` checks the sum of outstanding against the ledger balance of the advance account. A slip proposes recovery of the instalment (or the full balance) capped so net pay stays ≥ 0; the admin can edit the amount in draft. An advance with recoveries in submitted slips cannot be cancelled.
- **Aadhaar** is stored in full and returned masked (`XXXX-XXXX-1234`) unless the caller has `can_write` on `hr.Employee` (superusers included); the audit log always stores it masked. Reads of the PII are not audited (no per-field read audit in this codebase).
- **Employee number** is `EMP-####` from the naming series and cannot be set by the client; `mobile_no` is UNIQUE when set (future portal login); `user` (portal link) exists on the model but is not exposed yet.
- **Leave Management & Registers (Phase 7):** `LeaveType` master, `LeaveAllocation` (submittable quota grant), and `LeaveApplication` (submittable request that automatically marks Paid Leave attendance across its date range and reverts on cancel). Leave balance tracking prevents exceeding allocated quota. Salary register (`reports/salary-register/`) and Form B-compliant Wage register (`reports/wage-register/`) provide consolidated reporting for monthly payroll and daily wagers.
- **Not built (yet):** salary structures, minimum-wage warnings, piece-rate, PF, ESI, professional tax, bank transfer file (checklist 7.4 partly, 7.8-7.10).

## Stock Reconciliation (2026-10-29)

- **The SLE stores the delta, not a reset.** ERPNext's reconciliation SLE sets `qty_after_transaction` / valuation absolutely and reposts every later entry. This ledger keeps no running per-entry balance (the Bin and `verify_integrity` are plain sums over non-cancelled SLEs), so `ledger.post_reconciliation` resolves the absolute target against the live position *under the Bin lock* and writes the difference: `qty` = counted - current and `stock_value_difference` = counted value - current value. A rate-only change is an SLE with `qty = 0` and a non-zero value difference. `repost_stock`, `verify_integrity` and `reverse()` therefore need no special mode and a repost rebuilds identical Bins. Cancel is a plain negation of the delta (ERPNext re-posts the later entries from the pre-count state; the final qty is the same, a later moving-average rate can differ by rounding).
- **Backdating is blocked, not reposted.** The ledger does not reprice later entries, so a reconciliation dated before the latest live SLE of the same item + warehouse (date and time) is refused with a message to reconcile at or after the latest movement. ERPNext would accept it and repost. A date earlier than every movement of that item is fine. (This also makes the opening stock of an item with Tally-imported history a count dated on/after its last movement.)
- **Blank qty / rate keep the current value**, like ERPNext. Where ERPNext falls back to the Item Price list or `Item.valuation_rate` when no rate can be found, this ERP **refuses**: a count into a position with no current rate (current qty <= 0) needs an explicit rate > 0. A zero-valued stock position would silently understate the books.
- **Rows with no change are removed at submit** (ERPNext removes them at validate); the submit is refused when nothing is left. Snapshots (`current_qty`, `current_valuation_rate`, `current_amount`, `quantity_difference`, `amount`, `amount_difference`) are server-derived: refreshed on every draft save and re-computed against the live ledger at submit, which also writes the resolved qty / rate back to the row.
- **Opening-entry rule** follows ERPNext (`Opening Stock` purpose, or the company's first live SLE, needs a balance-sheet difference account), but the "first entry" test is per company, not global. Under perpetual inventory off no difference account is required and no GL is posted.
- **Batch-tracked items are counted per batch** (a row names the batch and states that batch's absolute qty / rate); the Bin moves by the batch's delta. Counting stock into a batch that does not exist yet is not possible: create the batch first. Serial numbers, inventory dimensions, putaway rules, reserved-stock checks and Item Standard Cost do not exist here and are not modelled. A disabled or expired batch can still be counted (the physical count is the truth).
- **Cancelling a count-up is refused** when the stock it created has since been consumed (the reversal would leave the Bin or a batch negative), exactly like cancelling a Material Receipt.
- **`fetch-items`** returns the Bins (and per-batch balances) of a leaf warehouse, or of the leaves under a group warehouse, optionally filtered by item or item group; empty positions only with `include_zero`. Negative balances are included so they can be counted. ERPNext's version also lists the item's default-warehouse items with no Bin; here only existing Bins appear (nothing to count if there never was a movement; add the row by hand for an opening line).
- **`verify_integrity`** adds `stock_reconciliation_integrity`: a submitted document's live SLEs must sum to its `difference_amount`, and no live reconciliation SLE may belong to a document that is not submitted.


## Attachments (2026-10-29)

ERPNext's `File` doctype (`attached_to_doctype` / `attached_to_name`) is the model. `core.Attachment` keeps that shape (`model_label` + `object_id`) but is stricter, because attachments here are audit proof:

- **Immutable.** An attachment row is never edited (model guard, queryset `update` refused, DB `UPDATE` trigger on SQLite and PostgreSQL). ERPNext lets users rename or re-point a File.
- **No deletion after submit.** ERPNext lets anyone with write access remove an attachment from a submitted document (it only blocks it for `Attach` fields marked read-only). Here, once the parent is submitted or cancelled the proof can be added to but never deleted, by anyone including superusers. On drafts and masters only the uploader or a manager (`can_delete` on the parent model, or superuser) may delete.
- **Type allow-list, checked by content.** PDF, JPEG, PNG, WebP, HEIC, XLSX, CSV only, verified by extension **and** magic bytes (the client content-type is ignored); 10 MB cap (`ATTACHMENT_MAX_BYTES`). ERPNext accepts almost anything and only restricts by an optional extension list.
- **No public files, no `is_private` flag.** Every file is private: bytes are served only through the authenticated `download` endpoint after a parent-record read check. ERPNext serves public Files from `/files/`.
- **Authority is inherited from the parent record** (read to list/download; write, submit or create to upload) instead of a separate `File` permission.
- **One copy per record.** The same bytes (SHA-256) cannot be attached twice to one record.
- **`verify_integrity`** adds `attachment_integrity`: file present, size and SHA-256 match, parent still exists.
- Not implemented (ERPNext has them): folders, thumbnails, attach-from-URL, "attach image" fields.

## Sales / Purchase returns (2026-10-29)

`is_return` credit notes (Sales Invoice) and debit notes (Purchase Invoice), ERPNext v15
semantics: negative quantities, lines, tax rows and totals; optional `return_against`;
`update_outstanding_for_self` default on. Code: `accounts/services/returns.py`.

- **Sign storage.** Stored negative as in ERPNext. The old rule "returns will be their own
  document types, not negative lines" (`core.mixins.PositiveQtyLineItemMixin` docstring) is
  superseded for **invoices only**; orders, delivery and stock documents still reject negative
  quantities. The DB CHECKs are per `is_return`: header `net_total` / `grand_total` /
  `rounded_total` and `outstanding_amount` are `>= 0` for a normal invoice and `<= 0` for a
  return (`<salesinvoice|purchaseinvoice>_amount_sign`, `_outstanding_sign`). The per-line / per-tax-row
  `>= 0` CHECKs on **invoice** lines and tax rows are relaxed to "all GST amounts of a line share
  one sign" (a child row cannot see its parent's `is_return`); the header CHECK is the real guarantee.
- **Tax engine.** A return is calculated as its positive twin and negated (`taxes.negate`), so its
  tax and round-off are the exact mirror of the original (half-up rounding is symmetric).
- **GL.** Built with the original's code from the negative amounts, then normalised
  (`gl.mirror_entries`): a negative debit becomes a credit of the same size. `gl.post` still refuses
  negative amounts. GST integrity (`gst_integrity`) therefore needs no special case.
- **Sales return stock** re-enters at the **original outgoing valuation** (pro rata of the original
  voucher's outgoing SLE value for the item / batch); standalone returns use the bin rate, else the
  item's standard rate. **Purchase return stock** goes out at the ledger's current batch / bin
  average (ERPNext uses the original incoming rate); the difference to the debit-note value is
  posted to the expense account so GL inventory still equals `Bin.stock_value`. Expired batches may
  be returned out to a supplier (`allow_expired`) and back in from a customer.
- **Stricter than ERPNext.** The return rate may not exceed the original line's rate (ERPNext skips
  this for Moving Average items); unlinked return lines are only accepted for items on the original
  and are capped per item as well as per linked line; a return of a return is refused; the party
  account must equal the original's (the outstanding tagging relies on it); a return posts no earlier
  than the original; batch-tracked lines must use the original's batch. A credit note skips the
  credit-limit check.
- **Statuses.** `Return`, `Credit Note Issued` / `Debit Note Issued` (a settled invoice with a
  submitted return) added to `InvoiceStatus`. A return's own `outstanding_amount` is negative
  (its credit / debit balance) and is settled by the **opposite Payment Entry flow** (refund:
  Pay/Customer, Receive/Supplier), also built by `make-payment-entry`. **Not done:** netting a
  standalone credit note against *other* invoices (ERPNext Payment Reconciliation / Journal Entry
  "Credit Note" allocation); use `update_outstanding_for_self` off on the return, or a refund.
- **Naming.** Returns are numbered `SRET-2026-` / `PRET-2026-` (GST caps the document number at 16
  characters, `SINV-RET-2026-0001` is 18), which also gives GSTR-1 table 13 a separate series.
- **GSTR-1.** New `cdnr` / `cdnur` sections (signed, negative values, `note_type` "C"); other
  unregistered credit notes net into `b2cs`; HSN, nil / exempt and the summary are net of returns.
  Debit notes to suppliers are not in GSTR-1; in GSTR-3B they reduce ITC through the negative lines
  (there is no separate 4(B) reversal row, see the zero sections of the 3B report).
- **Sales return against a non-stock invoice** (`update_stock` off) is refused when the return would
  update stock, as in ERPNext.

## Tally export (2026-10-29, app `tally_export`, docs/tally_export.md)

ERPNext has no Tally export (its Tally integration is a one-way *import* of a Tally
backup). This is new behaviour for the ERP-feeds-Tally direction.

- **Vouchers are derived, not stored.** A Tally voucher is rebuilt from the immutable
  `GLEntry` rows (and `StockLedgerEntry` for Stock Journals) every time; the only state is
  the ledger / item maps and the append-only `TallyExportBatch` / `TallyExportedVoucher`
  log. "Pending" is derived from the ledger and that log.
- **Stock-valuation legs are not exported.** Tally values stock itself (the Nidhi books are
  periodic), so the Stock In Hand / Cost of Goods Sold legs of an invoice are dropped;
  their net (purchase stock cost) is folded into the purchase ledger. Stock Entries go as
  quantity-only Stock Journals; their stock-adjustment GL (perpetual inventory) is not sent
  unless the voucher type is overridden to `Journal`.
- **Party lines map per party.** A Receivable / Payable line is never silently sent to its
  control account: an unmapped party blocks the export.
- **`TLY-*` documents and Opening Entry journals are never exported** (already in Tally).
- **Cancelled after export** is sent as `ACTION="Cancel"` with the same `REMOTEID`; a
  voucher cancelled before it was ever exported is simply not sent.

## NGU online-store catalogue (no ERPNext equivalent)

- **Crosswalk, not import.** `ngu.NguCatalogEntry` mirrors NGU `products_productvariant` /
  `products_productcombo` (identity: NGU id; SKUs are blank, names differ from Tally) and links
  a variant to a `stock.Item` by hand. The importer never creates or edits Items; it only
  auto-links an unambiguous exact name match.
- **Out of scope for now:** NGU invoices, payments, refunds, stock and invoice numbering.
- Combos are stored with their `components` and cannot be linked to an Item. A combo's
  `price` is NGU's `discount_price` (the combo table has no base price).

## Quick entry defaults (2026-10-29)

- **Line rate falls back to the party's last bill.** ERPNext fills a selling rate from the Item Price only (and buying from "last purchase rate" per item, not per supplier). Here, when no price-list row applies, `stock.services.pricing` uses the rate this customer / supplier was last billed for the item (newest of a submitted ERP invoice and the Tally item history, effective rate = amount / billed qty), then the item's standard rate. The item picker pre-fills it via `GET /api/stock/item-prices/rate/` (`rate_source: "last_bill"`). Nidhi has no price lists; its rates live in ten years of bills.
- **Invoice and payment accounts are optional on input.** As ERPNext `set_missing_values`: blank `debit_to` / `income_account` (Sales Invoice), `credit_to` / `expense_account` (Purchase Invoice) and the party / bank sides of a Payment Entry are filled from `Company.default_receivable_account` / `default_payable_account` / `default_income_account` and the default bank/cash account. The forms keep these under "More details".
- **The document number is never typed.** The server numbers every document on save; the form hides the field.

## Payment Reconciliation and GST go-live accounts (2026-10-29)

- **Reconciliation is a posted entry, not a rewrite of old vouchers.** ERPNext's Payment Reconciliation tool is not a stored document: "Reconcile" edits the *Payment Ledger Entry* / `against_voucher` of already submitted vouchers (and may create a Journal Entry for differences). Here submitted documents and the GL are immutable, so a reconciliation is its own submittable document (`payment_reconciliation.PaymentReconciliation`, voucher type of its GL rows). On submit it posts a **net-zero** voucher on the party control account through `gl.post`: Cr party tagged `against_voucher` = the invoice (releases its debit) and Dr party tagged `against_voucher` = the payment / note (consumes its credit); a supplier is the mirror. We chose this over a plain Journal Entry because `JournalEntryLine` carries no `against_voucher` tag and over editing GL rows (forbidden). Unreconcile = cancel (reversal rows). The party balance cannot change; invoice outstanding / status are rebuilt by `outstanding.refresh`; a payment's unallocated amount is derived (untagged payment-side rows plus rows tagged against it), never stored.
- **A reconciled document is locked.** ERPNext lets a payment be cancelled and unlinks the references; here a payment, journal or invoice with a live reconciliation entry cannot be cancelled until the entry is cancelled (same rule as an invoice with a submitted payment).
- **Untagged legacy invoices cannot be reconciled** (their outstanding comes from the document formula, not the ledger); allocate a Payment Entry instead. Imported `TLY-*` vouchers and Opening Entries are not offered as payments by default (`include_imported`).
- **Allocation is explicit pairs.** The tool pairs oldest-first like ERPNext (invoices outer loop, payments inner); there is no separate "difference amount" write-off row (use a Journal Entry).
- **Tally export of the entry** is a `Journal` voucher whose two party lines carry bill allocations: `Agst Ref <invoice>` on the credit side and `On Account` (or `Agst Ref <credit note no>` for a note) on the debit side, Dr = Cr. The receipt's unallocated remainder is `On Account` in Tally, so the entry nets it there.
- **Separate Input / Output GST accounts.** `manage.py setup_gst_accounts` tags six accounts (`Input|Output` x `CGST|SGST|IGST`, cess on request) under the company's `Duties & Taxes` group (ERPNext/India Compliance keeps Input under an asset group; Tally keeps both under Duties & Taxes, which we follow for the migrated company). It adds 5 / 12 / 18% Sales and Purchase templates (intra: CGST + SGST, inter: IGST) and makes the 5% ones the slot defaults (`--default-rate keep` leaves defaults alone). The Tally combined ledgers (`C Gst`, ...) are never edited. Company GSTIN / state problems are `verify_integrity` warnings, not errors (ORM and import paths may legitimately store them).

## Bank reconciliation (2026-10-29, app `accounts`)

- **Clearance is a separate append-only fact, not `clearance_date` on the voucher.** ERPNext writes `clearance_date` onto the submitted Payment / Journal Entry. Those are immutable here, so a clearance is a `BankClearance` row (voucher type + number, bank account, `clearance_date`, `set_by`, `set_at`, optional link to the statement line). The latest row per (bank account, voucher) wins; un-clearing adds a row with a null date. Nothing is updated or deleted; every clear / unclear is audited. A voucher that is cleared cannot be cancelled until it is un-cleared (ERPNext allows the cancel).
- **Statement lines are immutable and carry no state.** ERPNext's Bank Transaction stores `allocated_amount` / `unallocated_amount` / `status` and Payment Entries link back. Here a line's "reconciled" status and its vouchers are derived from the clearances that point at it. Matching is many-vouchers-to-one-line (their bank legs must add up exactly); one voucher against several lines (partial matching) is **not** implemented.
- **Dedupe.** ERPNext's import does not dedupe. Here a line is keyed by sha256(date, withdrawal, deposit, reference, description) plus its occurrence number within the file, unique per bank account (genuinely identical lines in one statement are kept; overlapping statements are safe).
- **CSV only.** `openpyxl` is not in `requirements.txt`, so the statement import reads CSV (delimiter / encoding sniffed, preamble rows skipped, user-confirmed column mapping). XLSX can be added by adding the dependency.
- **`reconciled_up_to` on the Bank Account** (not in ERPNext): vouchers on or before it are treated as already reconciled, so the ten years of Tally history are not listed as "uncleared" forever.
- **Auto-match** (ERPNext's rules engine is not copied): same signed amount, then cheque / reference number found in the line's reference or description, then date within a window of the voucher's cheque date or posting date; one-to-one and suggestions only. A bank-only line becomes a draft Bank Entry Journal Entry via `make-journal-entry` (the user picks the counter account, e.g. Bank Charges). A Payment Entry has no reference date in this ERP, so its posting date is used.
- **Bank Reconciliation Statement** follows ERPNext's report: balance per GL, less deposits not yet cleared, plus payments not yet cleared = balance per bank; it also compares with the last imported statement balance.

## Period Closing Voucher (2026-10-29, app `accounts`)

- **Same posting as ERPNext**: debit / credit every Income and Expense account to zero as of `period_end_date`, the difference on the closing account head (Liability / Equity). ERPNext's per-dimension (cost centre / project) splits, `Account Closing Balance` snapshots, the background "Process Period Closing Voucher" and partial-period vouchers are not implemented: one PCV closes one whole fiscal year (up to `period_end_date`) per company, enforced by a DB partial unique constraint on submitted vouchers.
- **P&L excludes, Balance Sheet includes the PCV GL** (ERPNext `is_period_closing_voucher_entry`), matched by `voucher_type`. The Balance Sheet additionally returns `provisional_profit` (unclosed Income - Expense); the Trial Balance includes the PCV and takes `exclude_closing=1` to hide it.
- **Previous year first** as ERPNext: the previous fiscal year (the one ending the day before) must have a submitted PCV unless it has no postings. Cancelling is refused while a later PCV exists.
- **Stricter than ERPNext: no posting into a closed period.** ERPNext lets ordinary entries be posted (or cancelled) before the last PCV date, which silently un-zeroes the closed P&L. Here a live PCV blocks posting or cancelling any non-closing entry dated on or before its `period_end_date` (cancel the PCV first). `verify_integrity` still warns about any residue.
- **Order with the fiscal-year lock:** post the PCV while the year is open, then set `FiscalYear.is_closed`. A locked year refuses both the PCV and its cancellation (reopen the year first).
- **Tally**: Period Closing Vouchers are never exported (`tally_export.constants.NEVER_EXPORTED_DOCTYPES`); they are reported in the plan's `skipped` list with reason `period_closing`. Tally closes its own years.
- **Closing account**: if none is given, the company's `Retained Earnings` / `Reserves & Surplus` account is used, created under the Equity group (and seeded by `seed_defaults` for the default company).

## Purchase bill number and extra charges (2026-10-29, app `buying`)

- **`bill_no` / `bill_date`** (ERPNext Supplier Invoice No / Date) are on the Purchase Invoice. ERPNext only warns when the number repeats; here the DB refuses it: a partial UNIQUE index on (company, supplier, lower(`bill_no`)) over non-cancelled invoices with a non-blank number, so a cancelled invoice frees the number and a debit note may leave it blank. `bill_no` is **required at submit** (not on a return), `bill_date` may not be after `posting_date` (CHECK). The Tally export sends them as `REFERENCE` / `REFERENCEDATE` of the purchase voucher; the purchase register, the tax ledger (`supplier_bill_no`) and the print data carry them.
- **Charges are a buying-side child table, not Valuation rows of the tax table.** ERPNext adds freight as a tax row with category *Valuation* / *Valuation and Total*. Our `TaxLine` (in `accounts`) is GST-shaped (On Net Total rates, per-line GST columns, integrity checks that tax rows add up to `total_taxes_and_charges`), so a category on it would have touched `accounts` models and every GST check. `PurchaseInvoiceCharge` (description, account, flat amount, `distribute_by` Amount\|Qty, `included_in_bill`) maps to ERPNext as: on bill = *Valuation and Total*, off bill = *Valuation*. ERPNext's *Total*-only (expensed, not capitalised) charge is not built; ERPNext charges can also be a percentage, here only flat amounts.
- **No GST on charges.** The tax engine never sees them; on-bill charges are added to `total_taxes_and_charges` / `grand_total` after it ran (and the rupee round-off re-derived), so `verify_integrity` adds them to the tax-row sum and the expected expense total. If the supplier charges GST on the freight (GTA), it must be entered some other way: this is not modelled.
- **Distribution** is per charge, in whole paise, rounded **down** line by line with the whole remainder on the last stock line that has a weight (ERPNext rounds each line and fixes the last line too, but half-up, which can leave a negative remainder). Non-stock lines get nothing. The stock line is posted with the exact `incoming_value` = `amount + share`, so the SLE rate is value / qty at 6 dp and `repost_stock` reproduces it from `stock_value_difference`.
- **GL**: party credit = `rounded_total` (it includes the on-bill charges). Each on-bill charge debits its own expense account (so the Tally voucher shows a Freight ledger line). With perpetual inventory the capitalised amount is Dr Stock In Hand and Cr **Expenses Included In Valuation** (ERPNext's clearing account, looked up by name because `Company` has no field for it; seeded by `ensure_charge_setup`), so an on-bill charge nets to nothing in P&L; an off-bill charge is just Dr Stock / Cr that account, and the later transporter payment or journal must debit it. Without perpetual inventory an off-bill charge has no GL at all and an on-bill one is an ordinary expense.
- **Tally export**: the exporter already folds the stock-valuation legs into the purchase ledger; it now folds *Expenses Included In Valuation* in with them, so Tally sees only what the supplier billed (Purchase, Freight, taxes, party) and Dr = Cr. An off-bill charge is absent from the purchase voucher: the accountant books the transporter's payment in Tally himself.
- **Not built (yet):** the standalone Landed Cost Voucher for a transporter bill that arrives after the purchase invoice is submitted. Workaround: cancel and amend the invoice (the charges are copied), or book the transporter bill as an expense.

## Item variants and batches dated today (2026-10-29, app `stock`)

- **Same model as ERPNext**: an `ItemAttribute` master with `ItemAttributeValue` rows (`value` + `abbr`), a *template* item (`has_variants`), variants pointing at it (`variant_of`, PROTECT) and a per-item attribute table (`ItemVariantAttribute`, ERPNext's "Item Variant Attribute"): on a template a row only *declares* the attribute (no value), on a variant it carries the chosen value. `create-variant` copies the template's fields and builds the code as template code + `-` + value abbreviations in the template's attribute order (`GARAM-500-M`); the name is `Template - 500g / Mild`.
- **A template can never hold stock, and the DB says so.** ERPNext lets a template be a stock item and only blocks transactions in code. Here `CHECK (has_variants = false OR is_stock_item = false)`, a template cannot itself be a variant (CHECK), and `ledger.post()` refuses a template item (`Item.save` also refuses to turn an item that already has ledger entries into a template). Draft document lines with a template are refused by `DraftNestedWriteMixin` (so Sales / Purchase / Stock Entry / Reconciliation), BOM and Work Order already require a stock item, `Batch.save` / `create_batch` refuse one. `verify_integrity` (batch check) reports any template that has SLEs or batches. Because the template is not a stock item the serializer forces `is_stock_item = false` when `has_variants` is on, and a variant is always created with `is_stock_item = true` (ERPNext copies the template's flag).
- **One variant per attribute combination, by a UNIQUE index.** ERPNext checks for a duplicate combination in Python. Here `Item.variant_signature` (server-derived `attr_id:value_id|...`, sorted) has `UNIQUE (variant_of, variant_signature)`; `verify_integrity` also recomputes it from the attribute rows.
- **Variants are created only through `POST items/{id}/create-variant/`.** `variant_of` is read-only on the Item API, so an item cannot be re-parented by a plain write. A template's attribute list is frozen once it has variants; `has_variants` cannot be switched off while variants exist (ERPNext: same).
- **Not built**: numeric (range / increment) attributes, variant-based-on Manufacturer, the Item Variant Settings "fields to copy" list (a fixed set of master fields is copied) and **propagating later template edits to existing variants** (ERPNext's `update_variants`): edit each variant, they are ordinary items. Multiple variants in one call (`enqueue_multiple_variant_creation`) is not built; call `create-variant` per combination. Translations of the template name are not copied.
- **Batches are dated today.** `manufacturing_date` defaults to the document's posting date when a document creates the batch (unchanged) and otherwise to the server's current date, for the API (`POST batches/`) and `batches.create_batch` / `resolve_dates` callers that pass nothing. ERPNext leaves the date empty. An expiry-tracked item therefore gets `expiry = today + shelf life` with no extra input. A batch of a template item is refused; the batch serializer exposes `item_variant_label` (`500g`, `500g, Mild`).

## Automatic FIFO batch picking (2026-10-29, apps `stock`, `manufacturing`, `selling`)

- **Same intent as ERPNext's "Pick Serial / Batch Based On = FIFO"**, but it is the only mode and it works at the ledger. ERPNext picks in a Serial and Batch Bundle created before submit (FIFO = oldest *creation*, LIFO optional, expiry-first via a separate setting). Here `ledger.post()` replaces a stock-out leg of a batch-tracked item that names no batch by one SLE per allocated batch, oldest `manufacturing_date` first (undated last), then creation order, then id. Disabled, empty and (unless the posting is a Material Issue write-off) expired batches are skipped; an explicitly named batch is honoured exactly as before; there is no LIFO / FEFO switch (`batches/fefo/` remains a suggestion endpoint). FEFO is no longer the pick order anywhere.
- **Allocation is under the Bin lock.** It runs after `select_for_update` on the (item, warehouse) Bin, the lock every posting of that item in that warehouse already takes, and each split leg is posted before the next line is allocated, so two lines of one voucher or two concurrent vouchers cannot claim the same quantity. Shortage (usable batch stock below the quantity) is refused with `Insufficient usable batch stock ...`, nothing is posted; Bin stock alone is not enough any more.
- **Transfers carry the batches.** A Material Transfer / Transfer for Manufacture incoming leg with no batch is posted as one leg per batch the matching outgoing line took, valued at exactly the value that batch released (`incoming_value`), so value is conserved batch by batch.
- **Manufacture / Repack consumption** uses the same path; `production.py` still sums the SLEs' value, which is correct for split legs, and the manufacturing value-conservation check is unchanged. `Work Order make-stock-entry` no longer pre-splits rows by FEFO: it creates one row per material with the batch left blank, so a draft never goes stale and the shortage is reported at submit.
- **The document keeps its blank batch.** The allocation is *not* written back onto the line (ERPNext's bundle child table is not built): what FIFO took is a ledger fact. `GET batches/for-voucher/?voucher_type=&voucher_no=` and `batches/{id}/trace/` / the batch-wise balance report read it from the SLEs; `batches/fifo/` previews it. Consequences handled: a Sales Return of a line sold without a batch may name any batch the invoice's SLEs took stock from (`returns._validate_lines`), and the return itself must name its batch (stock coming in has no FIFO).
- **Not auto-picked**: Purchase Returns (the debit note must name the purchase's lot) and Stock Reconciliation (counts name the batch). A stock-in of a batch-tracked item still needs a batch or new-batch details.
- **Batch valuation is unchanged**: each leg is costed at its batch's own average. The Item `valuation_method` FIFO flag still means Moving Average (divergence G1); FIFO *picking of batches* is a separate matter from FIFO *valuation layers*.

## Voice and chat ERP assistant (2026-10-29, app `assistant`)

- **ERPNext divergence**: ERPNext has no built-in voice or chat agent that pre-fills real forms.
- **Agent fills, human saves**: The assistant has strictly zero write tools. It only executes read tools (scoped strictly by `user_has_model_permission` and tenant scoping) and proposal builders. Proposals dry-run the target serializer (`is_valid()` only, never `save()`) and compute preview totals.
- **Human confirmation**: The agent navigates to the real frontend form pre-filled with the proposal data. The human user is the only one who saves (creating a Draft) and submits (posting to GL/SLE).
- **Save tagging**: On create, the frontend passes `X-Assistant-Proposal: <uuid>`. `AuditLogMixin` links the proposal to the saved document, transitions proposal status to `saved`, and tags `{"source": "assistant", "proposal": "<uuid>"}` in `AuditLog.changes`.
- **Multilingual voice & Indic vocabulary**: Built for Hindi, Hinglish, and Indian regional languages with speech-to-text biased by catalogue and party masters.

## Single access level for all staff (2026-10-31, app `users`)

- **Deliberate exception to least-privilege RBAC:** By owner decision (2026-10-31), all daily staff share a single all-access role (`Staff`) carrying full permissions over all business documents, ledgers, and masters. The role management plane (`users.Role`, `users.RolePermission`, `users.UserRole`) remains restricted to `System Manager` and superusers.
- **Trade-off accepted by owner:** Every staff member can view financial data, bank details, wages, and reports.
- **Safeguards preserved:** Database constraints (`CHECK`, `UNIQUE`, `ON DELETE PROTECT`), full audit logging (`AuditLog`), immutable submitted documents (`DocStatus.SUBMITTED`), and ledger posting services remain strictly enforced. Portal workers (`hr.EmployeePortalAccess`) remain role-less and restricted to their own records. The underlying RBAC infrastructure (`users/permissions.py`) remains intact so granular permissions can be re-applied without architectural rework.

## Production batches (2026-10-31, planning doc 03, Q2)

- **One batch, several Work Orders.** ERPNext makes one Work Order per finished item. Here a `Batch` owns one grinding Work Order (bulk item) and one packing Work Order per SKU. One grind is packed into several SKUs, so a batch has several `Manufacture` entries. The Work Order and Manufacture rules are unchanged.
- **Grinding posts at finish, not start.** `start_grinding` creates and submits the Work Order and posts nothing. `finish_grinding` posts one Manufacture with the bulk actually produced. Raw is consumed at the full BOM input, and the grinding loss is the gap, recorded as `process_loss_qty`. A submitted entry cannot be edited, so the posted output must be the real one.
- **Packing loss leaves In Process.** Bulk used minus bulk packed goes to the `Process Loss` warehouse (created on first use) by a Material Transfer. ERPNext has no equivalent. Its loss is absorbed into finished-goods value. Here the loss value stays on the books in `Process Loss` until the accountant writes it off, which is not built yet. The consumption gate is unchanged: each SKU consumes exactly its BOM.
- **Loss allowance (advisory), measured at grinding.** Raw material counts as less stock when it enters processing, so loss is measured at grinding. Grinding loss (raw consumed minus bulk produced, as a share of raw consumed) above the grinding BOM's existing `process_loss_percentage` is returned as a warning. It never blocks posting or changes a quantity. Packing does not warn. No new field on the item.
- **Name clash.** `manufacturing.Batch` (a production run) is unrelated to `stock.Batch` (a lot, `has_batch_no`). Lot tracking stays off for these items.

## Opening bills (2026-10-31, planning doc 09, app `accounts`)

- **Opening bills are a record, not a posting.** ERPNext carries pre-go-live open items as Sales / Purchase Invoices with an `is_opening` flag, or as Opening journal entries that post to the ledger. Here `accounts.OpeningBill` is a submittable document that **posts no GL**: the party's balance is already in the ledger (carried in from Tally up to `as_on`), so posting it again would double the balance. It says which bill a receipt or payment settles, and `as_on` records the ledger date it reconciles to.
- **Outstanding is a projection of allocations.** `outstanding_amount` = `amount` less the live allocations of submitted payment entries (not the ledger, which an opening bill does not have). It is refreshed under the bill's row lock by `accounts/services/opening.py`, rebuilt by `repost_outstanding`, and checked by `verify_integrity`. A payment cannot allocate more than it is owed. Cancelling a bill is refused while a live payment settles it.
- **Payments name the bill's party and control account.** An allocation against an opening bill must match the bill's party and account. Its direction (receipt or payment) follows its party type.
- **Ageing, Money to Collect and Bills to Pay include opening bills** (flagged `is_opening`). Bills to Pay is this app's payables list, not an ERPNext report. Opening supplier bills are included so that opening payables are not invisible. The payment suggestion merges open opening bills with invoices by due date, so receipts settle the oldest debt first.
- **Dates are estimated.** The generator `build_opening_bills` (planning 09, G-2) builds the bills from the ledger, not from ERPNext. For each party it takes the newest debit (customer) or credit (supplier) entries that add up to the balance and keeps their voucher dates. Due date is bill date plus credit days (`accounts.services.terms.calculate_due_date`). Any balance the entries do not cover becomes one bill at the oldest date. A credit balance is an advance and creates no bill. Dry run is the default. `--commit` refuses, creating nothing, unless every party's bills tie to its ledger balance and the total ties too. It also refuses when opening bills for that `as_on` date already exist.

