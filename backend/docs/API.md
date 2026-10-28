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
- **Taxable documents (SO / SI / PO / PI)** also nest a `taxes` child table (second child
  set, written after `items`). Every save recomputes `net_total`, `total_taxes_and_charges`,
  `grand_total` (**tax-inclusive**), `rounding_adjustment`, `rounded_total` and the per-line GST
  columns server-side (`accounts/services/taxes.py`, `gst/services/supply.py`); `taxes` omitted =
  rows left alone (PATCH) / template expanded or the company default auto-selected (create);
  `taxes: []` = no tax. Tax-input errors are **400** (never 409) and change nothing. A PATCH
  that changes the party / `place_of_supply` / export or RCM flag without sending `taxes` and
  whose stored GST rows no longer fit the supply kind **re-derives** them (default template for
  the new kind, or none when GST does not apply), as ERPNext re-applies taxes on party change;
  send `taxes_and_charges` to pick a template explicitly. Server-owned (ignored on input): `gst_category`, `party_gstin`, `company_gstin`, all
  totals, line `gst_*` / `*_amount` / `item_tax_rate`, row `idx` / `net_amount` / `tax_amount` / `total`.
  Submitting an SI / PI posts the tax, reverse-charge and round-off lines to the GL (see the
  `…/submit/` rows).

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
`users` ✅ (tests 8, test_rbac 26, test_user_management 40) · `buying` ~ (6) · `selling` ~ (6) · `gst` ✅ (77: validators, masters API, seeding, RBAC, backfill). No app is untested.

---

## 1. Users / auth / RBAC — app: `users` — [users/views.py](../users/views.py)

Models: `Role`, `UserRole`, `RolePermission` (+ custom `User`). User management seeds `users.User` / `users.UserRole` onto the *System Manager* role (`seed_defaults`).

| URL (`/api/auth/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `token/` | POST | JWT obtain | Public (throttled `auth`) | O(1) | ✅ | SimpleJWT | User | No | Brute-force throttle |
| `token/refresh/` | POST | Refresh JWT | Public (throttled) | O(1) | ✅ | SimpleJWT | — | No | |
| `me/` | GET, PATCH | Current user + roles. PATCH may change only `first_name`, `last_name`, `email`, `language`; `username`, `is_active`, `is_superuser`, `is_staff`, `roles` are read-only (silently ignored) | Auth | O(1) | ✅ | `UserSerializer` | User, UserRole, RolePermission | N/A | |
| `me/change-password/` | POST | Signed-in user changes own password. Body `{old_password, new_password}`; new password runs Django's validators and must differ from the old | Auth (own account only) | O(1) | ✅ | `ChangePasswordSerializer` | User, AuditLog | Yes | Throttled under the `auth` scope (10/min). Audit row records "changed", never the secret. Existing JWTs stay valid until they expire |
| `users/` | GET/POST | User list (`?search=` username/email/name, `?is_active=`, `?language=`, `?ordering=`) / create. Create takes `username, email, password, first_name, last_name, language, is_active, role_ids` | Auth + RBAC `users.User` (`can_read` / `can_create`); `role_ids` additionally needs `can_write` on `users.UserRole` | O(n) | ✅ | `UserAdminSerializer` | User, UserRole, AuditLog | Yes (user + roles + audit) | Password is write-only, validated with `AUTH_PASSWORD_VALIDATORS`, hashed. `is_superuser`/`is_staff` are read-only (never settable over the API). Email unique case-insensitively; language must be one of the 13 `LANGUAGES` |
| `users/{pk}/` | GET/PUT/PATCH | User detail / update (PUT is handled as PATCH). Response adds `roles` (names), `role_ids`, `last_login`, `date_joined`, `modified_at` (UI version token, bumped on every change) | Auth + RBAC `users.User` (`can_read` / `can_write`); role changes also `users.UserRole` `can_write` | O(1) | ✅ | `UserAdminSerializer` | User, UserRole, AuditLog | Yes | **No DELETE (405)** - deactivate instead (users are referenced by audit rows). `username` immutable, `password` rejected here. You cannot change your own roles or deactivate yourself (400). Non-superusers get 403 on any write to a superuser account. Audit `changes` = `{field: {from, to}}` |
| `users/{pk}/deactivate/` `…/activate/` | POST | Set `is_active` false/true (no body), returns the user | Auth + RBAC `users.User` `can_write` | O(1) | ✅ | `UserAdminSerializer` | User, AuditLog | Yes | Cannot deactivate yourself (400); superuser targets need a superuser. A deactivated user can no longer log in and existing JWTs stop working |
| `users/{pk}/set-password/` | POST | Admin resets another user's password. Body `{new_password}` (validated) | Auth + RBAC `users.User` `can_write` (System Manager / superuser) | O(1) | ✅ | `SetPasswordSerializer` | User, AuditLog | Yes | Refused on your own account (use `me/change-password`) and on superusers unless caller is a superuser |
| `roles/` | GET/POST | Role registry | Auth + RBAC `users.Role` | O(n) | ✅ | `RoleSerializer` | Role, AuditLog | No | Was `IsAdminUser`; now RBAC-gated on `users.Role` (seeded *System Manager* role, or superuser) |
| `roles/{pk}/` | GET/PUT/PATCH/DELETE | Role CRUD | Auth + RBAC `users.Role` | O(1) | ✅ | `RoleSerializer` | Role, UserRole | No | |
| `permissions/` | GET/POST | Role→permission grants | Auth + RBAC `users.RolePermission` | O(n) | ✅ | `RolePermissionSerializer` | RolePermission, AuditLog | No | System Managers can edit grants here (audited; `model_label` must be an installed model). Governs every other endpoint's access. Was `IsAdminUser` — any staff user could grant themselves `can_read` on `accounts.GLEntry` and read the whole ledger |
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
| `dashboard/` | GET | Headline numbers for one company (`?company=<id>`, default first active): total sales/purchases (**`net_total`, excluding GST**), receivables/payables outstanding (GL-derived, so tax-inclusive), open sales orders (count/value, compared on `net_total`), stock value, draft counts, latest integrity check | Auth + RBAC: `can_read` on **any** of SalesInvoice / SalesOrder / PurchaseInvoice / PurchaseOrder / GLEntry / JournalEntry / PaymentEntry / Bin / StockEntry / SystemHealthCheck, else 403. Each section is `null` unless the caller can read its source model (receivables/payables need `accounts.GLEntry`, stock value `stock.Bin`, integrity `core.SystemHealthCheck`) | Aggregate | ✅ | — (plain dict; money as strings) | SalesInvoice, PurchaseInvoice, SalesOrder, GLEntry, Bin, draft docs, SystemHealthCheck | N/A | Function view; read-only, everything derived on read. Receivables/payables = live GL balance of Receivable/Payable accounts (cancelled rows excluded). Open order = submitted order whose submitted invoices cover less than its total. 400 on a non-integer `company`, 404 on unknown id |
| `companies/` `…/{pk}/` | GET (list/detail) | Read-only company list for pickers (`?search=` name/abbr, `?is_active=`) | Auth + RBAC: `can_read` on `core.Company` | O(1) | ✅ | `core.views.CompanySerializer` (id, name, abbr, default_currency, country, gstin, is_active, plus read-only enable_perpetual_inventory, default_inventory_account, stock_adjustment_account, default_expense_account, and the GST fields state, gst_category, hsn_min_digits, enable_rounded_total, round_off_account, round_off_gst_values) | Company | n/a (read) | Write verbs return 405; companies are managed in the admin site (the admin form runs `Company.clean()`: GSTIN checksum, state prefix, category) |
| `states/` `…/{pk}/` | GET (list/detail) | Read-only GST state / UT master; `pk` = 2-digit GST code (`27`); `?search=` code/name/abbr, `?is_active=`, `?is_union_territory=`. 40 rows seeded by migration `core.0008` | Auth + RBAC: `can_read` on `core.State` (seeded to Default Role, all managers) | O(1) | ✅ | `core.views.StateSerializer` (code, name, abbr, is_union_territory, is_active) | State | n/a (read) | Write verbs return 405 |

---

## 3. Accounts / ledger (GL) — app: `accounts` — [accounts/views.py](../accounts/views.py)

Models: `Account`, `GLEntry`, `JournalEntry`(+`JournalEntryLine`), `PaymentEntry`(+`PaymentEntryReference`).

| URL (`/api/accounts/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `accounts/` `accounts/{pk}/` | CRUD | Chart of accounts (filters add `gst_direction`, `gst_component`) | Auth + RBAC | O(1) | ✅ | `AccountSerializer` (+ GST tags `gst_direction` Input/Output/Reverse Charge and `gst_component` CGST/SGST/IGST/CESS, both or neither, Tax type only, unique per company) | Account | No | Tree master; tags identify the company's GST accounts for the tax engine. `account_type` gains `Round Off` |
| `item-tax-templates/` `…/{pk}/` | CRUD | Global GST rate sets with nested `rows` (`gst_component`, `tax_rate` 0..100, 3 dp). Header-only PATCH keeps rows; sending `rows` replaces them. Filters `gst_treatment`, `is_active`, `is_legacy`; `?search=` name | Auth + RBAC: `accounts.ItemTaxTemplate` (Accounts Manager full; Sales/Purchase/Stock Manager and Default Role read) | O(n) rows | ✅ | `ItemTaxTemplateSerializer` / `ItemTaxTemplateRowSerializer` | ItemTaxTemplate, ItemTaxTemplateRow | No | 11 seeded (GST 0/0.25/3/5/18/40, legacy 12/28, Nil Rated, Exempted, Non-GST). Deleting a template used by an item is blocked (PROTECT) |
| `tax-templates/` `…/{pk}/` | CRUD | Per-company Sales/Purchase Taxes and Charges templates with nested `rows` (`idx` server-assigned from position, `account`, `rate`, `description`, `charge_type` On Net Total only, `add_deduct_tax`, `category` Total only; read-only `account_name`, `gst_direction`, `gst_component`). Filters `company`, `template_type`, `gst_supply_type`, `is_reverse_charge`, `is_default`, `disabled`, and `supply_kind=INTRA\|INTER` (that supply type **or blank**, so a picker also shows generic templates; 400 on other values). Validates: row accounts belong to the company, are leaf Tax accounts, Output-tagged on Sales / Input-tagged on Purchase, no repeats; one default per (company, type, supply type, reverse charge) | Auth + RBAC: `accounts.TaxTemplate` (as above) | O(n) rows | ✅ | `TaxTemplateSerializer` / `TaxTemplateRowSerializer` | TaxTemplate, TaxTemplateRow | No | 6 seeded per company with a chart (Output/Input GST In-state/Out-state, Input RCM variants) |
| `taxes/calculate/` | POST | Stateless dry run of the tax engine. Body `{doctype (selling.SalesOrder/SalesInvoice, buying.PurchaseOrder/PurchaseInvoice), company, party (Customer/Supplier id), posting_date?, place_of_supply?, is_export_with_gst?, is_reverse_charge?, taxes_and_charges?, items [{item, qty, rate}], taxes? [{account, rate, charge_type?, add_deduct_tax?, category?, description?}]}`. Returns `{supply {kind INTRA/INTER/null, place_of_supply, gst_category, party_gstin, company_gstin, allowed_components, reason}, taxes_and_charges, taxes_and_charges_name (template title\|null), place_of_supply, place_of_supply_name (state name\|null), items [per line amount/gst_treatment/gst_hsn_code/item_tax_rate/gst_rate/cgst_amount/sgst_amount/igst_amount], taxes [per row idx/account/…/net_amount/tax_amount/total], net_total, total_taxes_and_charges, grand_total, rounding_adjustment, rounded_total}` — exactly what a create would store (omitted `taxes` = expand `taxes_and_charges` or auto-select the default). Writes nothing. 400 on tax-input errors | Auth + `can_create` on the target document label (else 403) | O(lines × rows) | ✅ | `TaxCalculateSerializer` (request) | reads Item, ItemTaxTemplate, TaxTemplate, Account, Customer/Supplier | N/A | Function view; shares `gst.services.supply.compute` with the real save |
| `gl-entries/` `gl-entries/{pk}/` | GET only | General-ledger entries (filters: company, account, voucher_type, voucher_no, is_cancelled, `posting_date` / `posting_date__gte` / `posting_date__lte`) | Auth + RBAC | O(n) | ✅ | `GLEntrySerializer` | GLEntry | N/A | **Read-only** — written by posting |
| `journal-entries/` `…/{pk}/` | CRUD | Journal entries | Auth + RBAC | O(1) | ✅ | `JournalEntrySerializer` | JournalEntry, JournalEntryLine | No (draft) | Submittable |
| `journal-entries/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Post / reverse / re-draft | Auth + RBAC | Posting | ✅ | `JournalEntrySerializer` | JournalEntry, GLEntry | **Yes** | Balanced-Dr/Cr enforced; posts GL |
| `payment-entries/` `…/{pk}/` | CRUD | Payments (against invoices) | Auth + RBAC | O(1) | ✅ | `PaymentEntrySerializer` (+ read-only `paid_from_name`, `paid_to_name`; `references[].allocated_amount` must be > 0) | PaymentEntry, PaymentEntryReference | No (draft) | Submittable; DB CHECK `allocated_amount > 0` |
| `payment-entries/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Post / reverse / re-draft | Auth + RBAC | Posting | ✅ | `PaymentEntrySerializer` | PaymentEntry, GLEntry, SalesInvoice / PurchaseInvoice (`outstanding_amount`, `status` refreshed) | **Yes** | Submit locks each referenced invoice (`select_for_update`) and refuses (400): doctype other than `selling.SalesInvoice` (Receive/Customer) or `buying.PurchaseInvoice` (Pay/Supplier), invoice missing / not submitted / other company / other party / other party account, duplicate reference, allocation <= 0 or > the invoice's outstanding, Σ allocated > `paid_amount`. GL: the party leg is split into one line per reference tagged `against_voucher_*` plus one untagged advance line for the remainder; bank leg unchanged. Cancel reverses and refreshes the invoices |
| `reports/trial-balance/` | GET | Trial balance (`company`, `from_date`, `to_date`; malformed date = 400; money 2dp) | Auth + RBAC | Aggregate | ✅ | — | GLEntry, Account | N/A | Function view |
| `reports/general-ledger/` | GET | GL report (`company`, `account`, `from_date`, `to_date`). With `account`: `opening_balance` (net before `from_date`, 0.00 if none), running `balance` per row starting from it, `closing_balance`. Without `account`: `opening_balance`, row `balance` and `closing_balance` are `null` | Auth + RBAC | Aggregate | ✅ | — | GLEntry, Account | N/A | |
| `reports/profit-and-loss/` | GET | P&L for period (`company`, `from_date`, `to_date`; none = all time). Returns `total_income/total_expense/net_profit` + `accounts[]` (`account_id, account_name, root_type, balance`) | Auth + RBAC | Aggregate | ✅ | — | GLEntry, Account | N/A | |
| `reports/balance-sheet/` | GET | Balance sheet as of `to_date` (`company`, `to_date`; none = all time; `from_date` ignored). Returns `total_assets/total_liabilities/total_equity` + `accounts[]` (`account_id, account_name, root_type, balance`) | Auth + RBAC | Aggregate | ✅ | — | GLEntry, Account | N/A | |
| `reports/ageing/` | GET | Accounts receivable / payable ageing (`party_type` = `Customer` \| `Supplier` required, `company`, `as_on` default today, `party` exact name; 400 on bad input). One row per **submitted** invoice with outstanding > 0 posted on/before `as_on`: `invoice, party, posting_date, due_date, rounded_total, outstanding, days_overdue` (`as_on` − due date, or posting date if none; floored at 0) and `buckets` `0-30 / 31-60 / 61-90 / 90+`; plus `parties[]` and `total` (`rounded_total, outstanding, buckets`). Uses the invoice's **current** outstanding, not its balance on `as_on` | Auth + RBAC (`can_read` on `accounts.GLEntry`, like the other ledger reports) | Aggregate | ✅ | — | SalesInvoice / PurchaseInvoice, Customer / Supplier | N/A | Function view; `accounts.services.outstanding.ageing_report` |

---

## 4. Stock / inventory — app: `stock` — [stock/views.py](../stock/views.py)

Models: `UOM`, `ItemGroup`, `Item`, `Warehouse`, `StockLedgerEntry`, `Bin`, `StockEntry`(+`StockEntryItem`).

| URL (`/api/stock/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `uoms/` · `item-groups/` · `items/` · `warehouses/` | CRUD | Inventory masters | Auth + RBAC | O(1) | ✅ | resp. serializers (UOM + `gst_uqc`; Item + `gst_hsn_code` (digits, 4/6/8), `is_nil_exempt`, `is_non_gst` (mutually exclusive), `item_tax_template`, read-only `item_tax_template_name` and `item_tax_rate` dict e.g. `{"CGST":"9.000","SGST":"9.000","IGST":"18.000"}`) | UOM / ItemGroup / Item / Warehouse | No | Item filters add `item_tax_template`, `is_nil_exempt`, `is_non_gst`; search adds `gst_hsn_code` |
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
| `customer-groups/` · `customers/` | CRUD | Selling masters | Auth + RBAC | O(1) | ~ | resp. serializers (Customer + `gst_category`, `state` (GST state code), read-only `state_name`) | CustomerGroup / Customer | No | Customer search: name/gstin/email/phone; filters add `gst_category`, `state`. GSTIN is checksum-validated and upper-cased; Registered Regular/Composition/SEZ require a GSTIN, Unregistered/Consumer/Overseas must have none; `state` auto-fills from the GSTIN prefix (mismatch = 400 on `state`); Overseas forces `state` null |
| `sales-orders/` `…/{pk}/` | CRUD | Sales orders (with tax table) | Auth + RBAC | O(lines × tax rows) | ✅ | `SalesOrderSerializer` (+ writable `taxes_and_charges`, `place_of_supply`, `is_export_with_gst`, `is_reverse_charge`, nested `taxes` (`SalesOrderTaxSerializer`); read-only `gst_category`, `party_gstin`, `company_gstin`, `net_total`, `total_taxes_and_charges`, `grand_total`, `rounding_adjustment`, `rounded_total`, `place_of_supply_name`, `taxes_and_charges_name`; lines add read-only `gst_hsn_code`, `gst_treatment`, `item_tax_template`, `item_tax_rate`, `gst_rate`, `cgst_amount`, `sgst_amount`, `igst_amount`) | SalesOrder, SalesOrderItem, SalesOrderTax | No (draft) | Submittable; positive-qty / non-neg-rate line validators; tax semantics under "Document write semantics"; sales `is_reverse_charge` rejected |
| `sales-orders/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Confirm / cancel order | Auth + RBAC | Posting | ~ | `SalesOrderSerializer` | SalesOrder | **Yes** | |
| `sales-orders/{pk}/make-sales-invoice/` | POST | Map a **submitted** SO to a **draft** Sales Invoice for the not-yet-invoiced qty (no body); copies `taxes_and_charges`, the tax rows (inputs only), `place_of_supply`, the export/RCM flags and the line GST snapshots, then recomputes tax on the remaining qty. 201 → `SalesInvoiceSerializer`; 400 `{"error"}` if not submitted / fully invoiced / no Receivable or Income account | Auth + RBAC: `can_read` on `selling.SalesOrder` **and** `can_create` on `selling.SalesInvoice` (else 403) | Aggregate | ✅ | `SalesInvoiceSerializer` | SalesOrder, SalesOrderItem (read) → SalesInvoice, SalesInvoiceItem (write), Account (read) | **Yes** | `selling/mapping.py`; billed qty counts **submitted** invoices only; accounts resolved by type (see divergences) |
| `sales-invoices/` `…/{pk}/` | CRUD | Sales invoices (with tax table) | Auth + RBAC | O(lines × tax rows) | ✅ | `SalesInvoiceSerializer` (+ read-only `sales_order_name` (nullable), `debit_to_name`, `income_account_name`; the same tax fields as sales orders, nested `taxes` = `SalesInvoiceTaxSerializer`; read-only `outstanding_amount`, `status`, `is_overdue`, `display_status`) | SalesInvoice, SalesInvoiceItem, SalesInvoiceTax | No (draft) | Submittable; Read-only derived `outstanding_amount`, `status` (Draft / Unpaid / Partly Paid / Paid / Cancelled), `is_overdue`, `display_status` (`Overdue` when unpaid past `due_date`); client values are ignored. DB CHECK `outstanding_amount >= 0`. `update_stock` flag; submit gate (company GSTIN set): place of supply, HSN (digits, ≥ `Company.hsn_min_digits`), party GSTIN checksum / state prefix, number ≤ 16 chars |
| `sales-invoices/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Post invoice (GL + optional stock) | Auth + RBAC | Posting | ~ | `SalesInvoiceSerializer` | SalesInvoice, GLEntry, (SLE/Bin if `update_stock`) | **Yes** | Own party GL row tagged `against_voucher` = itself; `outstanding_amount`/`status` set (Unpaid). Cancel is refused (400) while a submitted Payment Entry references the invoice; amend starts Draft / 0. One voucher: Dr party `rounded_total`; Cr income `net_total`; Cr each Output tax row (Dr a Deduct row); round-off Cr (+) / Dr (-) on `Company.round_off_account`; COGS/inventory pair if perpetual. Refused before posting if a rounding adjustment has no round-off account. Cancel reverses every line |
| `sales-invoices/{pk}/make-payment-entry/` | POST | Map a **submitted** SI to a **draft** "Receive" Payment Entry for its outstanding amount with one references row (no body). 201 → `PaymentEntrySerializer`; 400 if draft/cancelled, fully paid, or no Bank/Cash account | Auth + RBAC: `can_read` on `selling.SalesInvoice` **and** `can_create` on `accounts.PaymentEntry` (else 403) | Aggregate | ✅ | `PaymentEntrySerializer` | SalesInvoice, PaymentEntryReference (read) → PaymentEntry, PaymentEntryReference (write), Account | **Yes** | Outstanding = `rounded_total` (tax-inclusive, rounded) less allocations on **submitted** PEs |

---

## 6. Buying — app: `buying` — [buying/views.py](../buying/views.py)

Models: `SupplierGroup`, `Supplier`, `PurchaseOrder`(+`PurchaseOrderItem`), `PurchaseInvoice`(+`PurchaseInvoiceItem`).

| URL (`/api/buying/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `supplier-groups/` · `suppliers/` | CRUD | Buying masters | Auth + RBAC | O(1) | ~ | resp. serializers (Supplier + `gst_category`, `state`, read-only `state_name`) | SupplierGroup / Supplier | No | Same GSTIN / category / state rules and extra filters (`gst_category`, `state`) as customers |
| `purchase-orders/` `…/{pk}/` | CRUD | Purchase orders (with tax table) | Auth + RBAC | O(lines × tax rows) | ✅ | `PurchaseOrderSerializer` (+ the tax fields of the sales order serializer; nested `taxes` = `PurchaseOrderTaxSerializer`) | PurchaseOrder, PurchaseOrderItem, PurchaseOrderTax | No (draft) | Submittable; purchase `is_export_with_gst` rejected; `is_reverse_charge` makes GST rows memo rows (excluded from `grand_total`) |
| `purchase-orders/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Confirm / cancel PO | Auth + RBAC | Posting | ~ | `PurchaseOrderSerializer` | PurchaseOrder | **Yes** | |
| `purchase-orders/{pk}/make-purchase-invoice/` | POST | Map a **submitted** PO to a **draft** Purchase Invoice for the not-yet-invoiced qty (no body); copies template, tax rows, place of supply and flags (incl. `is_reverse_charge`) and recomputes tax on the remaining qty. 201 → `PurchaseInvoiceSerializer`; 400 `{"error"}` as for sales | Auth + RBAC: `can_read` on `buying.PurchaseOrder` **and** `can_create` on `buying.PurchaseInvoice` (else 403) | Aggregate | ✅ | `PurchaseInvoiceSerializer` | PurchaseOrder, PurchaseOrderItem (read) → PurchaseInvoice, PurchaseInvoiceItem (write), Account (read) | **Yes** | `buying/mapping.py`; expense account = Stock account when `update_stock`, else an Expense account |
| `purchase-invoices/` `…/{pk}/` | CRUD | Purchase invoices (with tax table) | Auth + RBAC | O(lines × tax rows) | ✅ | `PurchaseInvoiceSerializer` (+ read-only `purchase_order_name` (nullable), `credit_to_name`, `expense_account_name`; tax fields as above, nested `taxes` = `PurchaseInvoiceTaxSerializer`; read-only `outstanding_amount`, `status`, `is_overdue`, `display_status`) | PurchaseInvoice, PurchaseInvoiceItem, PurchaseInvoiceTax | No (draft) | Submittable; Read-only derived `outstanding_amount`, `status` (Draft / Unpaid / Partly Paid / Paid / Cancelled), `is_overdue`, `display_status` (`Overdue` when unpaid past `due_date`); client values are ignored. DB CHECK `outstanding_amount >= 0`. same submit gate |
| `purchase-invoices/{pk}/submit/` `…/cancel/` `…/amend/` | POST | Post invoice (GL + optional stock) | Auth + RBAC | Posting | ~ | `PurchaseInvoiceSerializer` | PurchaseInvoice, GLEntry, (SLE/Bin) | **Yes** | One voucher: Dr expense / inventory `net_total` (tax excluded); Dr each Input tax row (Cr a Deduct row); Cr party `rounded_total`; round-off Dr (+) / Cr (-). Reverse charge: GST rows are memo rows (not in the totals) and post `Dr Input X / Cr RCM Payable X` (refused if the RCM Payable account is missing). Cancel reverses every line |
| `purchase-invoices/{pk}/make-payment-entry/` | POST | Map a **submitted** PI to a **draft** "Pay" Payment Entry for its outstanding amount with one references row (no body). 201 → `PaymentEntrySerializer`; 400 as for sales | Auth + RBAC: `can_read` on `buying.PurchaseInvoice` **and** `can_create` on `accounts.PaymentEntry` (else 403) | Aggregate | ✅ | `PaymentEntrySerializer` | PurchaseInvoice, PaymentEntryReference (read) → PaymentEntry, PaymentEntryReference (write), Account | **Yes** | Pay: `paid_from` = Bank/Cash, `paid_to` = `credit_to` |

---

## GST — app: `gst` — [gst/views.py](../gst/views.py)

`gst` has **no models**: it holds the India-specific services (GSTIN validators, seeding;
see [gst_design.md](gst_design.md)). The tax engine orchestration is `gst/services/supply.py`; the reports are in `gst/reports/` (GSTR-1, GSTR-3B, tax ledger).

| URL (`/api/gst/…`) | Methods | What | Access | Complexity | Tested | Serializer | DB tables | Atomic | Notes |
|-----|--------|------|--------|-----------|--------|-----------|-----------|--------|-------|
| `validate-gstin/?gstin=` | GET | Stateless GSTIN check for inline form feedback. Returns `{valid, reason, state_code, state_name, pan}` (state is returned whenever the prefix is a known state, even if the GSTIN is invalid; `pan` only when valid). Missing `gstin` = 400 | Auth only (`IsAuthenticated`; exposes no stored data) | O(1) | ✅ | — | none (state list is a constant) | N/A | Function view; input is stripped and upper-cased |
| `supply-context/?company&party&direction&place_of_supply&is_export_with_gst&is_reverse_charge` | GET | Form defaults for a taxable document: `{kind INTRA/INTER/null, place_of_supply, gst_category, party_gstin, allowed_components, reason, suggested_template {id, title}\|null}`. `direction` is `Sales` or `Purchase`; `place_of_supply` is a state code; 400 on bad ids / unknown state | Auth + `can_read` on `selling.Customer` (Sales) / `buying.Supplier` (Purchase) | O(1) | ✅ | — | reads Company, Customer/Supplier, TaxTemplate | N/A | Function view; same rules as the engine (`gst.services.supply.supply_context`) |
| `reports/gstr1/?company&month=YYYY-MM` (or `from_date`+`to_date`) | GET | GSTR-1 from **submitted** Sales Invoices (`posting_date` in period): `{company, period, summary{invoice_count, taxable_value, igst, cgst, sgst, invoice_value}, b2b[{party_gstin, party_name, invoices[{name, posting_date, invoice_value, place_of_supply, reverse_charge, invoice_type, rates[{rate, taxable_value, igst, cgst, sgst}]}]}], b2cl[{place_of_supply, invoices[]}], b2cs[{place_of_supply, supply_type INTRA/INTER, rate, taxable_value, igst, cgst, sgst}], exports[{export_type WPAY/WOPAY, invoices[]}], nil_exempt_non_gst[4 buckets {description, nil_rated, exempted, non_gst}], hsn{b2b[], b2c[]} (hsn, uqc, total_qty, rate, total_value, taxable_value, igst, cgst, sgst), docs_issued[{series, from, to, total, cancelled}], warnings{legacy_invoices_without_gst_data, lines_without_hsn}}`. Money = 2dp strings, rate = 3dp. Aggregates the per-line GST columns; legacy (blank `gst_category`) invoices are excluded with a warning; cancelled invoices appear only in `docs_issued`. 400: missing / malformed `month` or dates, `month` with explicit dates, `from_date` > `to_date`, unknown `company`, company without GSTIN | Auth + `can_read` on `selling.SalesInvoice` | Aggregate (Python over one period's lines) | ✅ | — | SalesInvoice, SalesInvoiceItem, Customer, Item, UOM (read) | N/A (read-only) | Function view; B2CL = unregistered, inter-State, `rounded_total` **strictly above** `gst.constants.b2cl_threshold(posting_date)` |
| `reports/gstr3b/?company&month=YYYY-MM` (or `from_date`+`to_date`) | GET | GSTR-3B from submitted Sales **and** Purchase Invoices: `{company, period, "3.1"{a_outward_taxable, b_outward_zero_rated, c_other_nil_exempt, d_inward_rcm, e_non_gst_outward}, "3.2"[{place_of_supply, category, taxable_value, igst}], "4"{A{import_goods, import_services, inward_rcm, isd, all_other_itc}, B{reversed}, C_net_itc, D_ineligible}, "5"{inter_state, intra_state}, "6.1"{igst, cgst, sgst: {output, itc, net_payable}}, warnings{legacy_sales_invoices_without_gst_data, legacy_purchase_invoices_without_gst_data}}`. RCM tax is read from the line columns (the memo tax rows are outside the totals); 6.1 output = 3.1(a)+(b)+(d), itc = 4C; informational only (no cash/credit-ledger offset order). Same 400s as GSTR-1 | Auth + `can_read` on **both** `selling.SalesInvoice` and `buying.PurchaseInvoice` | Aggregate | ✅ | — | SalesInvoice(+Item), PurchaseInvoice(+Item), Supplier (read) | N/A (read-only) | Function view; sections the v1 model cannot produce (4A(1),(2),(4), 4B, 4D, cess) return zeros |
| `reports/tax-ledger/?company&month` (or `from_date`+`to_date`)`&component&direction&summary` | GET | GL on accounts tagged with `Account.gst_direction`: `{company, period, accounts[{account_id, account_name, direction, component, opening_balance, total_debit, total_credit, closing_balance, entries[{posting_date, voucher_type, voucher_no, party, debit, credit, balance}]}], net_by_component{CGST/SGST/IGST: {output, input, rcm, net_payable}}}`. Balance = debit minus credit (Output accounts negative), opening = net before `from_date`; the period is optional (open-ended); `component` (CGST/SGST/IGST/CESS) and `direction` (Input/Output/Reverse Charge) filter case-insensitively, other values = 400; `summary=1` omits `entries`; `net_payable` = output + rcm - input. Only `is_cancelled=False` rows (same as the general ledger) | Auth + `can_read` on `accounts.GLEntry` | Aggregate | ✅ | — | GLEntry, Account (read) | N/A (read-only) | Function view; tax rows carry no party themselves, so `party` is looked up from the voucher's receivable/payable row |

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
