# Custom ERP — Master Design & Implementation Plan

> ## ⚠️ STATUS: Historical design notes — **NOT the source of truth**
>
> This document captured the original design intent *before* the system was
> built. It is kept for background and rationale only. **It is no longer
> authoritative and may drift from the running code.**
>
> The source of truth is now the **code**:
> - **Schema** → the Django models and migrations (`backend/*/models.py`,
>   `backend/*/migrations/`). What the migrations define is what exists.
> - **Behaviour** → the **tests** (`backend/*/tests.py`) and the posting/repost
>   services. What the tests assert is the contract.
> - **Deliberate deviations from ERPNext** → `backend/docs/erpnext_divergences.md`.
>
> When this file and the code disagree, **the code wins.** Do not treat the
> schema, constraints, or precisions below as binding — verify against the
> models/migrations. The three mandates in §1 remain the guiding principles, but
> they are enforced by the code, not by this prose.

---

## 0. What we are building

A custom ERP for the Indian market, built on:

- **Backend:** Django + Django REST Framework (DRF), PostgreSQL. A *modular
  monolith* — one deployable process, one database, split into apps that are
  bounded contexts. Architecture patterned on the **NGU** project.
- **Frontend:** React + Vite + TypeScript, a metadata-driven admin SPA.
- **Reference:** **ERPNext** (in `erpnext/`, git-ignored). We clone its *data
  model and business logic*, not its code. See §8.

We are **not** reusing ERPNext's frontend — it is Frappe-framework runtime code
welded to Frappe's own endpoints and metadata engine, not a portable SPA. We
harvest its DocType JSON schemas and controller logic instead.

---

## 1. The three cross-cutting mandates (non-negotiable)

Every module, table, and endpoint must satisfy all three. They are woven into
the schema and the patterns below; these sections explain the *strategy*.

### 1.1 Self-healing — the system must recover to a valid state after any failure

Bugs and crashes are inevitable. The design guarantees we can always return to a
**consistent, valid state** without manual data surgery. The mechanism:

> **Store immutable facts. Derive everything else. If a derived value is ever
> wrong, delete it and recompute it from the facts.**

- **Immutable ledgers are the source of truth.** `GLEntry` (accounting) and
  `StockLedgerEntry` (inventory) are *append-only*. They are never updated or
  hard-deleted. A cancellation posts *reversing* entries; it never erases.
- **Derived tables are disposable projections.** `Bin` (stock balance per
  item+warehouse), account balances, invoice `outstanding_amount`, sales-order
  `delivered_qty` — all of these are computed *from* the ledgers. If any of them
  drifts, it is rebuilt by a **repost** command that replays the ledger. Losing
  a derived table is a non-event.
- **Atomicity.** Every document submission wraps *all* writes (the document, its
  child rows, and its ledger entries) in a single `transaction.atomic()` block.
  A failure anywhere rolls the whole thing back — the system stays in its last
  valid state. There is no such thing as a half-posted document.
- **Validation gates.** `validate_before_submit()` runs before any ledger write.
  Invalid data can never reach a ledger.
- **Idempotency.** Submitting an already-submitted document is a no-op guarded by
  `docstatus`. External side effects (email, SMS, payments) go through an
  **Outbox** table (§4.3) and are retried at-least-once with a dedupe key.
- **Consistency checker + auto-heal.** A scheduled job (`verify_integrity`)
  compares every derived value against the ledger and records any drift in
  `SystemHealthCheck`. Safe discrepancies are auto-reposted; unsafe ones raise an
  alert. Nothing is silently ignored.
- **No hard deletes of transactional data.** Only *draft* documents can be
  deleted. Submitted documents are cancelled + amended.

The invariants the checker enforces are listed in §6.

### 1.2 User-error-proofing — users must not be able to break the system

We give users the *least power necessary*. Correctness is enforced by the server
and the database, never trusted to the client or the user's discipline.

- **Least-privilege RBAC.** The default role can *read* and *create drafts*
  only. `submit`, `cancel`, `delete`, and master-data edits are separate
  permissions granted deliberately. See `RolePermission` (§3.2).
- **Three-layer validation, DB is the guarantee.**
  1. UI (React) — convenience, instant feedback.
  2. DRF serializer — the API contract; every request re-validated server-side.
  3. **PostgreSQL constraints** — the guarantee. Even a buggy code path or a
     manual SQL edit cannot violate these (`CHECK`, `UNIQUE`, `FOREIGN KEY … ON
     DELETE PROTECT/RESTRICT`, `NOT NULL`).
- **Constrained, metadata-driven input.** The UI only offers valid choices
  (dropdowns from master data, not free text). There is no raw-query, no
  scripting, no arbitrary field the user can abuse.
- **Immutable submitted documents.** Once submitted, a document cannot be edited
  — only cancelled and amended. This removes an entire class of "someone changed
  a posted invoice" errors.
- **Guided workflows, not free-form.** You create a Delivery Note *from* a Sales
  Order (fields pre-filled, quantities capped). You don't hand-type linked
  documents.
- **Irreversible actions are minimized and confirmed.** Almost everything is
  reversible (cancel/amend). The few that aren't require an elevated role and an
  explicit confirmation step.

### 1.3 Multilingual — support multiple Indian languages

Supported languages (ISO 639-1). English is the fallback for any missing string.

| Code | Language | Code | Language | Code | Language |
|---|---|---|---|---|---|
| `en` | English | `ta` | Tamil | `pa` | Punjabi |
| `hi` | Hindi | `gu` | Gujarati | `or` | Odia |
| `bn` | Bengali | `kn` | Kannada | `as` | Assamese |
| `te` | Telugu | `ml` | Malayalam | `ur` | Urdu (RTL) |
| `mr` | Marathi | | | | |

Two distinct translation problems, two mechanisms:

- **UI strings** (labels, buttons, error messages): standard i18n resource
  files. Backend uses Django `gettext` + `.po` files under each app's `locale/`;
  frontend uses `react-i18next` with one JSON file per language. API error
  messages are translated using the request's language (user profile →
  `Accept-Language` header → `en`).
- **Master-data content** (item names, descriptions, group names): a generic
  `FieldTranslation` table (§3.7) keyed by `(model, object_id, field, language)`.
  Serializers resolve the active language at read time and fall back to the base
  record when no translation exists.

Also required: **Indian number formatting** (lakh/crore grouping), **Indic
fonts** bundled locally (Noto Sans Devanagari/Tamil/Bengali/… — never a CDN, per
the frontend CSP), and **RTL layout** when the active language is `ur`. Every
`User` carries a `language` field that drives all of the above.

---

## 2. Architecture

### 2.1 Modular monolith
One Django process, one Postgres DB, apps as bounded contexts. Dependency
direction is one-way and deliberate:

```
core  ←  users, contacts, accounts, stock
                     ↑                ↑
                  selling  ─────→  (accounts + stock)
                  buying   ─────→  (accounts + stock)
```

`selling`/`buying` may import `accounts`/`stock`/`contacts`/`core`. The reverse
is forbidden. `core` imports nothing from other local apps.

### 2.2 The document lifecycle (the backbone)

Every transactional document inherits `core.SubmittableDocument` and moves
through exactly three states:

```
docstatus:   0 Draft  ──submit──►  1 Submitted  ──cancel──►  2 Cancelled
                                        │
                                     amend ►  new Draft (amended_from = old)
```

- **Draft (0):** editable, deletable, has *no* ledger effect.
- **Submitted (1):** immutable; ledger entries posted; the only allowed
  transition is to Cancelled.
- **Cancelled (2):** immutable; ledger entries reversed; kept forever for audit.

On submit the document calls `on_submit()` to post immutable ledger rows inside
one atomic transaction. On cancel it calls `on_cancel()` to post the reversing
rows. This is the seam where self-healing, immutability, and correctness all
live — see §1.1.

### 2.3 Metadata-driven UI

The backend exposes each resource's field metadata (derived from its DRF
serializer, surfaced via `drf-spectacular` at `/api/schema/`). One generic React
`<DocForm>` renders any document from that metadata; `<DataTable>` renders any
list. This is how we cover ~20–30 document types without hand-coding each form,
and it's *why* users are constrained — the UI can only present what the schema
declares. Bespoke screens are allowed only where a per-DocType override is
justified.

---

## 3. Database schema (original design — verify against the migrations)

**Conventions used throughout:**

- Every table has `id BIGSERIAL PRIMARY KEY`.
- Every table (unless noted "immutable/append-only") includes the **audit
  columns** from `TimeStampedModel`: `created_at TIMESTAMPTZ`, `modified_at
  TIMESTAMPTZ`, `created_by_id BIGINT NULL → users`, `modified_by_id BIGINT NULL
  → users`.
- Money = `DECIMAL(18,2)`. Quantity = `DECIMAL(18,3)`. Rate/valuation =
  `DECIMAL(18,6)`. Conversion factor = `DECIMAL(18,9)`. **Never `FLOAT` for money.**
- Submittable documents include `docstatus SMALLINT NOT NULL DEFAULT 0` and
  `amended_from_id BIGINT NULL → self`.
- FK deletion policy: master data referenced by transactions uses `ON DELETE
  PROTECT` (Django) / `RESTRICT` (SQL). Child rows of a document use `CASCADE`
  *from their own parent only*.
- `name` (document number) on submittables is a unique string from a
  `NamingSeries` (e.g. `SINV-2026-0001`).

### 3.1 `core`

**Company**
| column | type | notes |
|---|---|---|
| name | VARCHAR(200) | UNIQUE, NOT NULL |
| abbr | VARCHAR(10) | UNIQUE, NOT NULL |
| default_currency | CHAR(3) | default `INR` |
| country | VARCHAR(100) | default `India` |
| gstin | VARCHAR(20) | nullable |
| is_active | BOOLEAN | default TRUE |

**FiscalYear**
| column | type | notes |
|---|---|---|
| name | VARCHAR(40) | UNIQUE (e.g. `2026-2027`) |
| start_date | DATE | NOT NULL |
| end_date | DATE | NOT NULL |
| is_closed | BOOLEAN | default FALSE |
| CHECK | | `end_date > start_date` |

**NamingSeries**
| column | type | notes |
|---|---|---|
| prefix | VARCHAR(40) | UNIQUE (e.g. `SINV-2026-`) |
| current | INTEGER | default 0; incremented atomically (`F('current')+1`) |
| padding | SMALLINT | default 4 |

**AuditLog** *(append-only; never updated or deleted)*
| column | type | notes |
|---|---|---|
| timestamp | TIMESTAMPTZ | default now |
| user_id | BIGINT NULL → users | actor |
| action | VARCHAR(20) | `create`/`update`/`submit`/`cancel`/`delete` |
| model_label | VARCHAR(120) | e.g. `accounts.SalesInvoice` |
| object_id | VARCHAR(60) | |
| changes | JSONB | field-level before/after diff |
| ip_address | INET NULL | |

**SystemHealthCheck** *(written by the integrity checker; §6)*
| column | type | notes |
|---|---|---|
| checked_at | TIMESTAMPTZ | |
| check_name | VARCHAR(80) | e.g. `bin_matches_sle` |
| status | VARCHAR(10) | `ok`/`drift`/`healed`/`alert` |
| details | JSONB | discrepancy payload |

**Outbox** *(reliable external side effects; §4.3)*
| column | type | notes |
|---|---|---|
| dedupe_key | VARCHAR(120) | UNIQUE — idempotency |
| channel | VARCHAR(20) | `email`/`sms`/`whatsapp`/`webhook` |
| payload | JSONB | |
| status | VARCHAR(12) | `pending`/`sent`/`failed`/`dead` |
| attempts | SMALLINT | default 0 |
| next_attempt_at | TIMESTAMPTZ | backoff schedule |
| last_error | TEXT NULL | |

Abstract bases (not tables): **TimeStampedModel**, **SubmittableDocument**.

### 3.2 `users`

**User** *(extends Django `AbstractUser`)*
| column | type | notes |
|---|---|---|
| email | VARCHAR | UNIQUE, NOT NULL, lowercased on save |
| language | VARCHAR(5) | one of the supported codes; default `en` |
| is_active | BOOLEAN | default TRUE |

**Role**
| column | type | notes |
|---|---|---|
| name | VARCHAR(60) | UNIQUE |
| description | VARCHAR(240) | |

**UserRole** — M2M: `user_id → users`, `role_id → Role`. UNIQUE(user, role).

**RolePermission** — granular, per-model (mirrors ERPNext's permission grid)
| column | type | notes |
|---|---|---|
| role_id | BIGINT → Role | |
| model_label | VARCHAR(120) | e.g. `stock.Item` |
| can_read / can_create / can_write / can_submit / can_cancel / can_delete | BOOLEAN | default FALSE |
| UNIQUE | | (role, model_label) |

> Default role seed: `can_read=TRUE`, `can_create=TRUE`, everything else FALSE.

### 3.3 `contacts`

**Address**: `line1 VARCHAR(240)`, `line2 VARCHAR(240) NULL`, `city
VARCHAR(120)`, `state VARCHAR(120) NULL`, `country VARCHAR(120) default India`,
`pincode VARCHAR(20) NULL`.

**Contact**: `first_name VARCHAR(120)`, `last_name VARCHAR(120) NULL`, `email
VARCHAR NULL`, `phone VARCHAR(30) NULL`.

### 3.4 `accounts`

**Account** *(Chart of Accounts tree)*
| column | type | notes |
|---|---|---|
| name | VARCHAR(200) | |
| company_id | BIGINT → Company | CASCADE |
| parent_id | BIGINT NULL → self | tree |
| root_type | VARCHAR(12) | `Asset`/`Liability`/`Equity`/`Income`/`Expense` |
| account_type | VARCHAR(40) NULL | e.g. `Receivable`, `Payable`, `Bank`, `Stock`, `Tax` |
| account_number | VARCHAR(40) NULL | |
| is_group | BOOLEAN | group nodes hold no postings |
| is_active | BOOLEAN | default TRUE |
| UNIQUE | | (company, name) |

**GLEntry** *(immutable, append-only)*
| column | type | notes |
|---|---|---|
| posting_date | DATE | NOT NULL |
| account_id | BIGINT → Account | PROTECT |
| company_id | BIGINT → Company | PROTECT |
| party_type | VARCHAR(20) NULL | `Customer`/`Supplier` |
| party | VARCHAR(200) NULL | |
| debit | DECIMAL(18,2) | default 0 |
| credit | DECIMAL(18,2) | default 0 |
| voucher_type | VARCHAR(60) | source model label |
| voucher_no | VARCHAR(60) | source document name |
| against_voucher_type | VARCHAR(60) NULL | for reconciliation |
| against_voucher_no | VARCHAR(60) NULL | |
| is_cancelled | BOOLEAN | default FALSE (reversal marker) |
| fiscal_year | VARCHAR(40) NULL | |
| remarks | TEXT NULL | |
| CHECK | | `debit >= 0 AND credit >= 0 AND NOT (debit > 0 AND credit > 0)` |
| INDEX | | (account, posting_date), (voucher_type, voucher_no) |

**JournalEntry** *(submittable)*: `name`, `posting_date`, `company_id`,
`total_debit DECIMAL(18,2)`, `total_credit DECIMAL(18,2)`, `user_remark TEXT`.
On submit: `Σ debit == Σ credit` (else reject) → post `GLEntry` per line.
**JournalEntryLine**: `journal_entry_id → JournalEntry (CASCADE)`, `account_id →
Account (PROTECT)`, `debit`, `credit`, `party_type NULL`, `party NULL`.

**SalesInvoice** *(submittable)*
| column | type | notes |
|---|---|---|
| name | VARCHAR(60) | UNIQUE, from naming series |
| customer_id | BIGINT → selling.Customer | PROTECT |
| company_id | BIGINT → Company | PROTECT |
| posting_date | DATE | |
| due_date | DATE NULL | |
| currency | CHAR(3) | default INR |
| net_total | DECIMAL(18,2) | sum of item amounts |
| total_taxes | DECIMAL(18,2) | |
| grand_total | DECIMAL(18,2) | net_total + taxes |
| outstanding_amount | DECIMAL(18,2) | **derived** — reduced by payments |
| status | VARCHAR(20) | `Unpaid`/`Partly Paid`/`Paid`/`Cancelled` (derived) |

**SalesInvoiceItem**: `invoice_id → SalesInvoice (CASCADE)`, `item_id →
stock.Item (PROTECT)`, `qty DECIMAL(18,3)`, `rate DECIMAL(18,6)`, `amount
DECIMAL(18,2)`, `tax_rate DECIMAL(9,4)`.

**PurchaseInvoice** / **PurchaseInvoiceItem**: mirror of the above with
`supplier_id → buying.Supplier`.

**PaymentEntry** *(submittable)*: `name`, `payment_type VARCHAR(10)`
(`Receive`/`Pay`), `party_type`, `party`, `company_id`, `posting_date`,
`paid_amount DECIMAL(18,2)`, `paid_from_account_id → Account`,
`paid_to_account_id → Account`.
**PaymentEntryReference**: `payment_id (CASCADE)`, `reference_type`,
`reference_name`, `allocated_amount DECIMAL(18,2)`. On submit: post GL + reduce
target invoice `outstanding_amount` (derived, repostable).

**TaxTemplate** / **TaxTemplateRow** *(India GST)*: template has `name`,
`company`; rows have `tax_type` (`CGST`/`SGST`/`IGST`), `rate DECIMAL(9,4)`,
`account_id → Account (type Tax)`.

### 3.5 `stock`

**UOM**: `name VARCHAR(40) UNIQUE`.
**ItemGroup** *(tree)*: `name`, `parent_id NULL → self`, `is_group`.
**Item**
| column | type | notes |
|---|---|---|
| code | VARCHAR(140) | UNIQUE |
| name | VARCHAR(200) | |
| description | TEXT NULL | |
| item_group_id | BIGINT → ItemGroup | PROTECT |
| stock_uom_id | BIGINT → UOM | PROTECT |
| is_stock_item | BOOLEAN | default TRUE |
| valuation_method | VARCHAR(15) | `FIFO`/`Moving Average`; default `FIFO` |
| standard_rate | DECIMAL(18,6) | default 0 |
| is_active | BOOLEAN | default TRUE |

**Warehouse** *(tree)*: `name`, `company_id → Company`, `parent_id NULL → self`,
`is_group`, UNIQUE(company, name).

**StockLedgerEntry** *(immutable, append-only)*
| column | type | notes |
|---|---|---|
| posting_date | DATE | |
| posting_time | TIME | for intra-day ordering |
| item_id | BIGINT → Item | PROTECT |
| warehouse_id | BIGINT → Warehouse | PROTECT |
| qty | DECIMAL(18,3) | signed: +in / −out |
| valuation_rate | DECIMAL(18,6) | |
| stock_value_difference | DECIMAL(18,2) | value delta this entry |
| voucher_type | VARCHAR(60) | |
| voucher_no | VARCHAR(60) | |
| is_cancelled | BOOLEAN | default FALSE |
| INDEX | | (item, warehouse, posting_date, posting_time), (voucher_type, voucher_no) |

**Bin** *(derived balance — rebuildable from SLE)*: `item_id`, `warehouse_id`,
`actual_qty DECIMAL(18,3)`, `valuation_rate DECIMAL(18,6)`, `stock_value
DECIMAL(18,2)`, UNIQUE(item, warehouse). **Never write directly except through
the posting/repost service.**

**StockEntry** *(submittable)*: `name`, `stock_entry_type VARCHAR(20)`
(`Material Receipt`/`Material Issue`/`Material Transfer`), `posting_date`,
`company_id`. **StockEntryItem**: `stock_entry_id (CASCADE)`, `item_id
(PROTECT)`, `source_warehouse_id NULL`, `target_warehouse_id NULL`, `qty`,
`valuation_rate`.

### 3.6 `selling` & `buying`

**CustomerGroup** / **SupplierGroup** *(trees)*: `name`, `parent NULL`,
`is_group`.

**Customer**: `name VARCHAR(200) UNIQUE`, `customer_group_id → CustomerGroup`,
`gstin VARCHAR(20) NULL`, `email NULL`, `phone NULL`, `default_address_id NULL →
contacts.Address`, `is_active`.
**Supplier**: same shape with `supplier_group_id`.

**Quotation → SalesOrder → DeliveryNote** (all submittable). Representative:

**SalesOrder**: `name`, `customer_id → Customer (PROTECT)`, `company_id`,
`transaction_date DATE`, `delivery_date DATE NULL`, `net_total`, `grand_total`,
`status VARCHAR(20)` (`Draft`/`To Deliver`/`To Bill`/`Completed`/`Cancelled` —
derived).
**SalesOrderItem**: `sales_order_id (CASCADE)`, `item_id (PROTECT)`, `qty`,
`rate`, `amount`, `delivered_qty DECIMAL(18,3) default 0` (**derived**),
`billed_qty DECIMAL(18,3) default 0` (**derived**).

**Buying** mirrors: **PurchaseOrder → PurchaseReceipt** with `supplier_id`, and
`received_qty`/`billed_qty` as derived fields.

Document mapping (§8) fills a downstream document from an upstream one, capping
quantities at the remaining balance.

### 3.7 i18n

**Language**: `code VARCHAR(5) UNIQUE`, `name VARCHAR(60)`, `is_rtl BOOLEAN
default FALSE`, `is_active BOOLEAN`.

**FieldTranslation** *(generic master-data translation)*
| column | type | notes |
|---|---|---|
| model_label | VARCHAR(120) | e.g. `stock.Item` |
| object_id | BIGINT | |
| field_name | VARCHAR(60) | e.g. `name` |
| language | VARCHAR(5) | → Language.code |
| value | TEXT | translated text |
| UNIQUE | | (model_label, object_id, field_name, language) |

Resolution: a serializer mixin looks up `(model, id, field, request.language)`;
on miss, returns the base record's value (English fallback).

---

## 4. Cross-cutting mechanisms (implementation-level)

### 4.1 The posting service (accounting + stock)
A single service module per ledger (`accounts/services/gl.py`,
`stock/services/ledger.py`) exposes:
- `post(document)` — write immutable entries for a submitted document.
- `reverse(document)` — write reversing entries on cancel.
- `repost(scope)` — delete derived projections in scope and rebuild them by
  replaying the immutable ledger in order.

Documents never write ledger rows directly; they call these services inside
`on_submit()`/`on_cancel()`. This keeps posting logic in one testable place.

### 4.2 Atomic submit
`SubmittableDocument.submit()`:
```
with transaction.atomic():
    validate_before_submit()      # raise → nothing written
    docstatus = SUBMITTED; save()
    on_submit()                   # posting service writes ledgers
```
Any exception rolls the whole block back. Same shape for `cancel()`.

### 4.3 Outbox for external effects
External calls (SMS/WhatsApp OTP, email, payment webhooks, notifications) are
**never** made inline. The document writes an `Outbox` row (with a `dedupe_key`)
in the same transaction; a worker drains the outbox with exponential backoff and
marks `sent`/`dead`. This makes external effects at-least-once and idempotent, so
a crash mid-send never corrupts state.

### 4.4 Management commands (self-healing toolbox)
- `verify_integrity` — run all §6 checks, write `SystemHealthCheck`, exit non-zero
  on unresolved drift.
- `repost_stock` `--item --warehouse --from-date` — rebuild `Bin` + valuation.
- `repost_gl` `--account --from-date` — rebuild account balances / outstanding.
- `seed_defaults` — Company, FiscalYear, default Role/permissions, Languages,
  base Chart of Accounts, UOMs. Idempotent.

---

## 5. API design conventions

- Base path `/api/`. One router per app, mounted in `config/urls.py`.
- Auth: JWT (`/api/auth/token/`, `/api/auth/token/refresh/`).
- Documents expose CRUD + custom actions `POST {id}/submit/` and `POST
  {id}/cancel/` via `core.mixins.SubmittableViewSet`.
- Master data: standard `ModelViewSet`. Immutable ledgers:
  `ReadOnlyModelViewSet` (no create/update/delete over the API — ever).
- Every list endpoint supports `?search=`, `?ordering=`, and declared
  `filterset_fields`. Pagination is default (page size 25).
- Errors: DRF validation errors, message text translated to the request language.
- Language selection: `User.language` → `Accept-Language` → `en`.

---

## 6. Consistency invariants (what `verify_integrity` enforces)

These must hold at all times. The checker detects violations; the reposter heals
them.

1. **Stock:** for every (item, warehouse), `Bin.actual_qty == Σ StockLedgerEntry.qty`
   (non-cancelled) and `Bin.stock_value == Σ stock_value_difference`.
2. **GL balance:** every submitted voucher's GL entries satisfy `Σ debit == Σ credit`.
3. **Invoice outstanding:** `SalesInvoice.outstanding_amount == grand_total − Σ
   allocated payments` (and never `< 0`, never `> grand_total`).
4. **Order fulfilment:** `SalesOrderItem.delivered_qty == Σ` linked delivery
   quantities, and `delivered_qty <= qty`.
5. **Doc status monotonic:** no document moves 1→0 or 2→anything.
6. **No orphan ledger:** every GL/SLE row references an existing, non-draft voucher.
7. **Naming uniqueness:** no two submitted documents share a `name`.

---

## 7. Phased roadmap

Execute in order. Each phase ends with `verify_integrity` green and tests passing.

- **Phase 0 — Foundation:** project scaffold, settings, `core` (abstract bases,
  Company, NamingSeries, AuditLog, Outbox, SystemHealthCheck), `users` (User,
  Role, RolePermission, JWT), i18n plumbing (Language, FieldTranslation, gettext,
  react-i18next), frontend shell (`<DocForm>`, `<DataTable>`, auth, language
  switcher), Docker, `seed_defaults`.
- **Phase 1 — Accounting + Stock spine:** Account tree + GLEntry + posting/repost;
  Item/Warehouse/UOM + StockLedgerEntry + Bin + posting/repost; JournalEntry,
  StockEntry; `verify_integrity` for stock & GL; Trial Balance report.
- **Phase 2 — Selling & Buying:** contacts, Customer/Supplier, the SO→DN→SI→Payment
  and PO→PR→PI→Payment chains, document mapping, derived fulfilment fields.
- **Phase 3 — Reporting, hardening:** Balance Sheet, P&L, Stock Ledger & registers;
  dashboards; Celery worker for Outbox + scheduled `verify_integrity`; full RBAC
  enforcement; test coverage; staging deploy.
- **Phase 4 — Custom requirements:** captured once MVP modules are stable.

Detailed task breakdown lives in [checklist.md](checklist.md).

---

## 8. Cloning from ERPNext (the workflow)

For each document/master:
1. Open the DocType JSON, e.g.
   `erpnext/erpnext/selling/doctype/sales_order/sales_order.json`.
2. Map its fields to the schema in §3 (we have already curated the MVP subset —
   do **not** add fields beyond §3 without asking).
3. Read the controller `sales_order.py` for *business logic* — validations and
   on-submit effects — and re-implement them in our posting services + hooks.
4. Never copy ERPNext Python/JS into our tree. `erpnext/` is read-only reference.

---

## 9. Repo layout

```
ERP/
├── backend/           # Django DRF API (modular monolith)
│   ├── config/        # settings, root urls, wsgi/asgi
│   ├── core/          # abstract bases, Company, naming, audit, outbox, health
│   ├── users/         # User, Role, RolePermission, JWT
│   ├── contacts/      # Address, Contact
│   ├── accounts/      # CoA, GLEntry, invoices, payments, tax
│   ├── stock/         # Item, Warehouse, SLE, Bin, stock entry
│   ├── selling/       # Customer, sales cycle
│   ├── buying/        # Supplier, purchase cycle
│   ├── docs/          # per-module design notes
│   ├── requirements.txt
│   └── manage.py
├── frontend/          # React + Vite + TS SPA
│   ├── src/           # api, components (DocForm/DataTable), pages, hooks, i18n
│   ├── docs/
│   └── package.json
├── erpnext/           # REFERENCE ONLY — git-ignored
├── plan.md            # this file — design & schema
├── instructions.md    # how the implementer must work
├── checklist.md       # ordered task tracker
├── CLAUDE.md          # quick orientation + invariants
└── .gitignore
```

---

## 10. Decisions already made (do not revisit)

| Question | Decision |
|---|---|
| Reuse ERPNext frontend code? | **No.** Clone schema + logic only. |
| Backend framework | Django + DRF, modular monolith (NGU pattern). |
| Frontend | React + Vite + TS, metadata-driven. |
| MVP modules | accounts, stock, selling, buying (+ core, users, contacts). |
| Money type | `DECIMAL(18,2)`; qty `(18,3)`; rate `(18,6)`. Never float. |
| Default stock valuation | FIFO. |
| Source of truth | Immutable ledgers; derived tables are repostable. |
| Deletes | Drafts only; submitted docs cancel+amend. Ledgers never deleted. |
| Multi-company | Schema supports it (Company FK everywhere); single company in MVP seed. |
| Multi-currency | Column present (`currency`), conversion deferred past MVP. |
| Languages | 13 (en + 12 Indian); English fallback; `ur` is RTL. |
| Auth | JWT (SimpleJWT). |

Anything not covered here is an **open question — ask, do not improvise.**
