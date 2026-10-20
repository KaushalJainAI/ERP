# ERP Build Checklist

Ordered, atomic task tracker for the ERP build (design background in
[plan.md](plan.md); the source of truth is the code — see [CLAUDE.md](CLAUDE.md)).
**Do tasks top-to-bottom,
one at a time.** For each: implement → verify (see [instructions.md §5](instructions.md))
→ tick → commit. Do not skip or batch. When a task is ambiguous, stop and ask.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · **(V)** = must pass
`verify_integrity` before ticking.

> **North star:** every feature must replicate the corresponding **ERPNext**
> behaviour in our stack (instructions.md §0).

---

## Phase 0 — Foundation

### 0.1 Backend project scaffold
- [x] Fill `config/settings.py` (env-driven; DRF, JWT, CORS, i18n, drf-spectacular)
- [x] Fill `config/urls.py`, `wsgi.py`, `asgi.py`, `manage.py`
- [x] `.env.example` (SECRET_KEY, DEBUG, DATABASE_URL, ALLOWED_HOSTS, CORS, LANGUAGES)
- [x] `python manage.py check` passes

### 0.2 core app — abstract bases & framework tables
- [x] `TimeStampedModel` (created/modified at+by)
- [x] `DocStatus` choices + `SubmittableDocument` (submit/cancel/amend, atomic, edit-lock)
- [x] `SubmittableViewSet` mixin (CRUD + submit/cancel actions)
- [x] `Company` model + constraints
- [x] `FiscalYear` model + `end_date > start_date` CHECK
- [x] `NamingSeries` model + atomic `next()`
- [x] `AuditLog` model (append-only) + signal/mixin that records create/update/submit/cancel/delete
- [x] `SystemHealthCheck` model
- [x] `Outbox` model (dedupe_key UNIQUE, backoff fields)
- [x] Migrations generated, reviewed, applied

### 0.3 users app — auth & RBAC
- [x] Custom `User` (email unique+lowercased, `language` field)
- [x] `Role`, `UserRole`, `RolePermission` models + UNIQUE constraints
- [x] Permission-enforcing DRF permission class (reads RolePermission per model+action)
- [x] JWT endpoints wired (`/api/auth/token/`, `/refresh/`)
- [x] Default role seed: read+create only

### 0.4 i18n plumbing
- [x] `Language` model (code, name, is_rtl, is_active)
- [x] `FieldTranslation` model + UNIQUE(model_label, object_id, field_name, language)
- [x] Serializer translation mixin (resolve active language, fall back to English)
- [x] Django `LANGUAGES` = 13 codes; `LocaleMiddleware`; `locale/` dirs; sample `.po`
- [x] Language resolution: `User.language` → `Accept-Language` → `en`

### 0.5 seed_defaults command
- [x] Idempotent: Company, FiscalYear, Languages (13), Roles + default permissions,
      UOMs, base Chart of Accounts, ItemGroup/Customer/Supplier group roots
- [x] Re-running changes nothing

### 0.6 Frontend shell
- [x] `package.json`, `vite.config.ts`, `tsconfig*.json`, `index.html`, `.env.example`
- [x] React Router: login, dashboard, list, detail routes
- [x] Typed Axios client + JWT auth interceptor (refresh on 401)
- [x] TanStack Query provider
- [x] `useAuth` hook + guarded routes
- [x] `react-i18next` setup + language switcher + bundled Indic (Noto) fonts + RTL for `ur`
- [x] Indian number formatting util (lakh/crore)
- [x] Generic `<DataTable>` (list from schema)
- [x] Generic `<DocForm>` (create/edit from serializer metadata, incl. submit/cancel + status badge)

### 0.7 Ops
- [x] Dockerfile (backend) + docker-compose (backend + postgres + frontend)
- [x] `pytest`/`pytest-django` configured; first passing test
- [x] CI: lint + test on push

---

## Phase 1 — Accounting + Stock spine

### 1.1 accounts — Chart of Accounts & GL
- [x] `Account` tree model (root_type, account_type, is_group) + UNIQUE(company,name)
- [x] `GLEntry` immutable model + CHECK(debit,credit ≥0, not both) + indexes
- [x] `accounts/services/gl.py`: `post()`, `reverse()`, `repost()`
- [x] Read-only GLEntry ViewSet; Account ModelViewSet + permissions
- [x] Tests: posting balances, reversal, immutability **(V)**

### 1.2 stock — Item, Warehouse, SLE, Bin
- [x] `UOM`, `ItemGroup` (tree), `Item`, `Warehouse` (tree) models + constraints
- [x] `StockLedgerEntry` immutable model + indexes
- [x] `Bin` derived model + UNIQUE(item,warehouse)
- [x] `stock/services/ledger.py`: `post()`, `reverse()`, `repost()` with FIFO/moving-avg valuation
- [x] ViewSets (Item/Warehouse/UOM writable; SLE/Bin read-only) + permissions
- [x] Tests: valuation, Bin rebuild, reversal **(V)**

### 1.3 Documents on the spine
- [x] `JournalEntry` + lines (submittable): submit posts balanced GL **(V)**
- [x] `StockEntry` + items (submittable): receipt/issue/transfer post SLE **(V)**

### 1.4 Self-healing toolbox
- [x] `verify_integrity` command implementing all invariants in plan.md §6
- [x] `repost_stock` command (rebuild Bin + valuation from SLE)
- [x] `repost_gl` command (rebuild balances/outstanding from GLEntry)
- [x] Test: corrupt a Bin row, run repost, invariant restored **(V)**

### 1.5 Reports & frontend
- [x] Trial Balance report endpoint
- [x] Frontend: Account/Item/Warehouse lists + `<DocForm>`; JournalEntry & StockEntry with submit/cancel

---

## Phase 2 — Selling & Buying

### 2.1 contacts & masters
- [x] `CustomerGroup`/`SupplierGroup` (trees), `Customer`, `Supplier` + constraints

### 2.2 Selling chain (each submittable, **(V)** each)
- [x] `SalesOrder` + items
- [x] `SalesInvoice` + items → posts GL & SLE; sets status **(V)**

### 2.3 Buying chain (each submittable, **(V)** each)
- [x] `PurchaseOrder` + items
- [x] `PurchaseInvoice` + items → posts GL & SLE **(V)**

### 2.4 Payments & mapping
- [x] `PaymentEntry` + `PaymentEntryReference` (submittable): posts GL, reduces outstanding **(V)**
- [x] Document mapping (SalesOrder -> SalesInvoice, PurchaseOrder -> PurchaseInvoice)
- [x] Frontend flows for both cycles
- [x] `verify_integrity` green across all chains **(V)**

---

## Phase 3 — Reporting & hardening

- [x] Reports: Balance Sheet, P&L, General Ledger, Stock Balance endpoints
- [x] Dashboards / KPIs (Executive dashboard with real-time metrics & health)
- [x] Outbox transactional queue & `verify_integrity` self-healing engine
- [x] Full RBAC enforced on every endpoint; audit log verified for all mutations
      (report endpoints were closed on 2026-07-23 — see Phase 5)
- [x] Test coverage: 113 positive & negative tests passing in pytest
- [x] Backup/restore & ledger replay validated via `repost_stock` & `repost_gl`

---

## Phase 4 — Custom requirements
- [x] Capture divergences from ERPNext once MVP modules are stable (backend/docs/erpnext_divergences.md)

---

## Phase 5 — Post-MVP hardening (2026-07-23)

Full backend audit; fixes below are in code + tests (`verify_integrity` green,
87 tests passing at the time). Forward-looking backlog captured in repo-root
`improvements.md`. (The suite is at 113 after the 2026-10-21 RBAC pass.)

- [x] Security: financial/stock **reports now enforce RBAC** (were login-only and
      fail-open) — `require_model_permission` on all report endpoints
- [x] Stock: **cancel cannot drive a `Bin` negative** (No-Negative-Stock held only
      on the forward path before)
- [x] Self-healing: `verify_integrity` also checks **valuation-rate** drift
- [x] API: **stable pagination** ordering (unordered lists could drop/duplicate rows)
- [x] Validation: reject `qty<=0` / negative rate; reject **cross-company** and
      **same-account** postings; **atomic** nested create
- [x] Audit: record delete **after** it succeeds; corrected `gl.repost()` docstring
- [x] Backlog (see `improvements.md`): perpetual inventory/COGS, fiscal-period lock,
      GST, AR/AP allocation, FIFO
- [x] **RBAC pass (2026-10-21)**: `is_staff` no longer bypasses RBAC; the permission
      check fails closed on an unresolvable view; empty querysets resolve their model
      (the old truthiness check made every viewset on an empty table fail open);
      role management moved onto RBAC; the seeded role matrix is scoped, validated
      against the app registry, and authoritative on re-seed — which is what makes the
      reports reachable again. 26 new tests (`users/test_rbac.py`); suite at 113.
