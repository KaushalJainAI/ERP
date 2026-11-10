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

---

## Phase 6 — ERP completion (2026-10-28)

### 6.1 Invoice status and ageing
- [x] `SalesInvoice` / `PurchaseInvoice` expose `billing_status`, `outstanding_amount`
      and `ageing_bucket`; payment allocation is reflected without rewriting invoices
- [x] Receivable / payable ageing report endpoint with bucket totals
- [x] Frontend list status badges and ageing report screen

### 6.2 Batches on document lines
- [x] `Batch` master, balance and expiry derived from the stock ledger
- [x] Batch + manufacturing/expiry dates on sales and purchase document lines
- [x] Batch-aware stock posting, FEFO selection and expiry blocking
- [x] Batch trace / recall and expiry reports
- [x] Frontend batch picker and batch list

### 6.3 Manufacturing backend
- [x] `BOM`, `BOMItem`, `BOMScrapItem` with cost rollup, sub-BOM explosion and
      usage lock (master, not submittable)
- [x] `WorkOrder` / `WorkOrderItem`; `qty` is desired good output and required
      materials are grossed up by the BOM process loss (design §14)
- [x] Material Transfer for Manufacture / Manufacture / Repack stock entries,
      WIP transfer, scrap valuation, process-loss calculation and batch output
- [x] WIP warehouse account and per-account stock GL posting
- [x] Work Order stop/resume/cancel guard, progress projection, `repost_work_orders`
- [x] Production yield and BOM cost reports; API + divergence docs
- [x] 33 manufacturing tests; full suite green (`verify_integrity` clean)

### 6.4 Price lists and credit limits
- [x] `PriceList` / `ItemPrice` masters with currency, UOM and date windows
- [x] One resolver: explicit header → party → customer-group ancestor → company
      default; omitted line rates filled server-side, `price_list_rate` snapshot
- [x] Draft-only `update-rates` action on all four sales/purchase documents
- [x] Customer `credit_limit` / bypass; submit gate uses live receivable plus
      unbilled orders; superuser / `Credit Controller` override is audited
- [x] Customer credit-status endpoint; seeded `Credit Controller` and
      `Manufacturing Manager` roles
- [x] Price-list/credit API and divergence docs

### 6.5 Frontend screens
- [x] Manufacturing module: BOM and Work Order list/form, read-only required
      items, stop/resume, make-transfer/make-manufacture actions
- [x] Stock Entry manufacturing fields and entry types restored
- [x] Price list and item price screens; customer/supplier price-list fields and
      credit limit; document headers and list-rate columns; Update Rates actions
- [x] English strings plus all 12 locale bundles at 100% i18n key coverage;
      `lint`, `build` and `i18n:check` green

### 6.6 Demo, docs and final verification
- [x] `seed_demo`: WIP/scrap warehouses, completed and draft Work Orders,
      retail/wholesale/vendor price lists, credit limits and an over-limit draft order
- [x] Seed idempotency test and manufacturing/pricing assertions; `verify_integrity`
      green on an existing and a fresh database
- [x] `backend/docs/manufacturing.md` operator how-to; README demo description updated
- [x] Full backend suite (694 passing), ruff, frontend lint, build and i18n
      check all green; `makemigrations --check` reports no drift

### 6.7 Tally cutover: lose nothing (2026-10-28)
- [x] ERP on Postgres (`docker-compose.yml` `postgres`, host port 5433); SQLite stays dev/test only
- [x] `legacy.TallyRecord`: every Tally master and voucher of every year with its original XML and ERP link; append-only; read-only API
- [x] `legacy.TallyItemLine`: item-wise sales / purchase / note history of all 10 years; read-only API
- [x] `contacts.Address` (ERPNext Address, one party per row) + CRUD API; Tally mailing addresses imported
- [x] Journal Entry `voucher_type`, `cheque_no/date`, `bill_no/date`; filled from Tally type and reference
- [x] Item tax templates set from Tally's latest GST rate
- [x] Import report proves archive completeness row-for-row; ledgers reconcile per year **(V)**
- [ ] Get the sender's Day Book + All Masters XML per year (`Desktop\Tally\SENDER_REQUEST.md`) and import it: stock-journal lines, bank instrument details, invoice consignee / e-way bill, company GSTIN (`backend/docs/tally_data_gaps.md` §2)
- [ ] Physical count + Stock Reconciliation for the 7 negative-stock items (needs 6.8 Stock Reconciliation)
- [ ] Accountant decision: reclassify the 20 ledgers that change type between years (profit differs in 3 years)
- [x] Translate the new `lg.*` strings (Addresses, Tally History) and the Journal Entry type / reference labels into the 12 Indian languages (English fallback until then)

### 6.8 Go-live gaps against ERPNext (P1, `backend/docs/erpnext_gap_analysis.md`)
- [x] Print formats (GST Tax Invoice, Delivery Challan, PO, receipt) as PDF **(V)** — `GET /api/print/…` (backend/printing), A4 view `/print/<kind>/<id>` (Print / Save as PDF = browser dialog)
- [x] Sales / Purchase returns (`is_return` credit / debit note against an invoice, stock back in) **(V)**
- [-] e-Way Bill; e-Invoice (IRN) — **skipped (user, 2026-10-28)**: Tally does the statutory filing
- [x] Stock Reconciliation document (opening stock, physical count) **(V)**
- [-] UOM conversion per item — **skipped (user, 2026-10-28)**: operators do the carton maths
- [x] Registers: Sales, Purchase, Day Book, Cash / Bank Book, party statement (`accounts/reports/`, `accounts/test_registers.py`, `docs/reports.md`)
- [x] Bank Account + Bank Reconciliation (statement import, clearance date) (`accounts/services/bank*.py`, `accounts/test_bank_reconciliation.py`; CSV import only, partial matching skipped, see divergences)
- [x] Period Closing Voucher (year-end P&L → retained earnings) **(V)** (`accounts/services/closing.py`, `accounts/test_period_closing.py`)
- [x] Separate Input / Output GST accounts and tax templates for NIDHI GRAH UDYOG; company GSTIN (`manage.py setup_gst_accounts --company <name>`, `gst/services/go_live.py`, `gst/tests/test_go_live.py`; `verify_integrity` warns on a missing / invalid company GSTIN or state; Tally export proposes `Input CGST` / `Output CGST` ... ledger names) **(V)**
- [x] Payment Reconciliation tool (match advances, lump-sum receipts and credit / debit notes to open invoices; net-zero reconciliation entry, unreconcile = cancel; `payment_reconciliation/`, `/payment-reconciliation` page) **(V)**

### 6.9 ERP feeds Tally (user direction, 2026-10-28)
Operators enter everything in the ERP; the accountant completes the official books
and GST filing in Tally from what the ERP exports.
- [x] Attachments: proof files (bill scans, photos, PDFs) on any document or master; immutable once the document is submitted (add-only); audited **(V)**
- [x] Purchase invoice extra charges (freight, hamali, loading) added to stock valuation, like ERPNext's landed cost but on the bill itself **(V)**
- [x] Tally export: submitted vouchers (sales, purchase, credit / debit notes, payments, journals) and new masters as Tally import XML for a date range; each voucher marked exported, re-export on demand **(V)**

## Phase 7 — HR and payroll (2026-10-28)

New `hr` app. ERPNext core only ships the employee masters (`setup/doctype`);
attendance, leave and payroll live in the separate frappe/hrms app, which is not
in `erpnext/`. Record every design choice beyond the masters in
`erpnext_divergences.md`. PF and ESI are **not applicable today** but get their
own sections so they can be switched on later without reworking payroll.
Most of the workforce is **daily wage**: the daily rate × attendance (+ OT) is
the primary pay path; monthly salary is the secondary case.

### 7.1 Employee masters
- [x] `Department`, `Designation`, `Branch`, `HolidayList` / `Holiday`
      (mirrors ERPNext `setup/doctype`). *Design note:* `EmployeeGroup` not built.
- [x] `Employee`: `pay_type` Daily / Monthly (piece-rate and contract are not
      built), joining/relieving dates, status, bank account + IFSC, PAN, Aadhaar
      (masked in the API unless the caller has `can_write` on `hr.Employee`);
      optional UAN and ESIC number (inert until 7.7 / 7.8); `mobile_no` UNIQUE
      when set
- [x] `HR User` / `HR Manager` roles (seeded); employee PII **writes** audited
      (Aadhaar stored masked in the audit log). *Not done:* auditing PII reads
- [ ] Screens + locale files (backend API, gettext strings and tests are done:
      `hr/tests.py`)

### 7.2 Attendance
- [x] `Attendance` (employee, date, Present / Absent / Half Day / **Paid Leave**,
      OT hours); UNIQUE(employee, date). *Design note:* Paid Leave stands in for
      On Leave until 7.3
- [x] Daily muster-roll bulk entry endpoint (`attendance/muster-roll/`, atomic
      upsert) and frontend screen (`/hr/muster-roll`)
- [x] Attendance locked once a **submitted salary slip** covers it (there are no
      payroll periods in this design)
- [x] Muster roll report (`attendance/muster-roll-report/`); tests

### 7.3 Leave (basic — mainly for monthly staff; a daily wager's absence is simply unpaid)
*Deferred:* the `Paid Leave` attendance status covers it for now.
- [x] `LeaveType`, `LeaveAllocation`, `LeaveApplication` (submittable);
      approved leave marks attendance
- [x] Leave balance report; tests

### 7.4 Salary structures
- [x] `SalaryComponent` (earning / deduction, GL account; fixed amounts only,
      no formulas). System components Wages / Overtime / Advance Recovery seeded
      via `HRSettings` / `ensure_hr_setup`
- [x] **Design differs:** no Salary Structure; `EmployeeWageRate` is the
      effective-dated per-employee pay (daily rate or monthly salary, OT hourly
      rate). Piece rate not built
- [ ] MP minimum-wage rates by skill category with effective dates; warn when
      a rate is below the minimum
- [ ] Configurable wage-definition rule (Labour Codes 50% basic); tests

### 7.5 Payroll and GL posting
- [x] **Design differs:** no `PayrollPeriod`; a slip covers any date range and
      the admin pays any day (submitted slips of one employee may not overlap)
- [x] `SalarySlip` (submittable) computed from attendance and wage rates;
      posts Dr wage expense, Cr wages payable (+ advance and other deductions);
      cancel posts reversals
- [x] `PayrollEntry` (submittable): bulk-create slips for a range/department/branch;
      submit and cancel act on all its slips atomically
- [x] **Design differs:** `EmployeePayment` (own document, not Payment Entry):
      Dr wages payable / Cr cash or bank, capped at the wages payable;
      `PayrollEntry.make-payments` pays a whole run. *Bank transfer file export
      pending*
- [x] Salary register and wage register reports (backend endpoints + frontend report views)
- [x] `verify_integrity` covers payroll GL (slips, advances, payments, overlaps);
      tests

### 7.6 Advances and loans
- [x] `EmployeeAdvance` (submittable, posts Dr employee advance, Cr cash/bank);
      outstanding / status derived from slips (not stored)
- [x] Instalment recovery as a salary-slip deduction (proposed on the slip,
      editable in draft); tests. *Advance balance report pending* (per-employee
      figure is in `employees/{id}/summary/`)

### 7.7 Provident Fund (deferred — off by default)
- [ ] Company `pf_enabled` flag + PF registration number; when off, no PF
      component is computed and PF fields are hidden
- [ ] Employee/employer contribution (EPF / EPS split, wage ceiling) as
      configurable components with payable accounts
- [ ] PF ECR export and PF register; tests with the flag on and off

### 7.8 Employees' State Insurance (deferred — off by default)
- [ ] Company `esi_enabled` flag + ESI code; when off, no ESI component is
      computed and ESI fields are hidden
- [ ] Employee/employer rates and wage eligibility ceiling, configurable and
      effective-dated, with payable accounts
- [ ] ESI contribution return export and ESI register; tests with the flag on and off

### 7.9 Other statutory items
- [ ] MP Professional Tax slabs and MP Labour Welfare Fund as configurable deductions
- [ ] Bonus (Payment of Bonus Act) and gratuity calculation reports

### 7.10 Piece-rate linked to manufacturing
- [ ] Record worker output against Work Orders; piece-rate wages flow into
      salary slips and labour cost into production costing

## Phase 8 — Assistant: voice + chat agent (2026-10-28)

Design: [assistant_plan.md](assistant_plan.md). Non-technical staff say what
happened; the agent looks things up and opens the **real form pre-filled**; the
user always presses Save themselves. **The agent has no write tools** — every
task below must keep that true. Built from the NGU assistant pattern
(`NGU-Backend-audit/assistant`). Record in `erpnext_divergences.md`; add every
route to `backend/docs/API.md`.

### 8.0 Decisions
- [ ] Confirm first entry types, staff languages, phone vs desktop, STT
      provider (assistant_plan.md §10) — stop and ask before 8.4

### 8.1 Backend skeleton
- [ ] New `assistant` app: `AssistantConversation`, `AssistantMessage`,
      `AssistantProposal` with CHECK constraints (role, input_mode, status,
      saved fields set iff status = saved); owner-only conversation endpoints
- [x] LLM provider adapter (env-configured) + per-user burst/daily throttles;
      LLM unavailable → friendly reply, never a 500
- [x] Gunicorn to threaded workers so a streaming turn can't block other users;
      test two concurrent requests

### 8.2 Read tools (RBAC-scoped)
- [ ] `search_customers/suppliers/items/employees` (fuzzy + FieldTranslation
      names), `get_stock` (Bin), `get_party_outstanding`, `get_rate`
      (read-only `pricing.fetch_rate`), `recent_documents`, `get_document`;
      `<<DATA>>` wrapping
- [x] Tests: no-write guarantee (CaptureQueriesContext over every tool), RBAC
      denial, prompt-injection-as-data

### 8.3 Agent loop + step streaming
- [x] Bounded loop (≤ 6 tool rounds, 20 s/LLM call, 1 retry), token-budgeted
      history, `ask_user` and `navigate` (route allow-list)
- [x] `POST assistant/chat/stream/` SSE: `step` before/after every tool,
      `question`, `proposal`, `reply`, `done`, `error`; tests

### 8.4 Proposal builders (first targets)
- [x] Common builder: `can_create` check, ID re-validation, Decimal parsing,
      serializer **dry-run** (`is_valid()` only) → warnings, preview total,
      localized summary; multi-line all-or-nothing
- [x] `propose_sales_invoice`, `propose_purchase_invoice` (bill-total
      mismatch warning), `propose_payment_entry` (receive); ambiguity → ask;
      tests
- [x] Save tagging: `X-Assistant-Proposal` header read by a core viewset mixin
      after a successful create → proposal `saved` + `AuditLog` source; bad or
      reused id ignored; tests

### 8.5 Frontend
- [x] Mic/text launcher in `Layout`, `AssistantPanel` (tappable options,
      RTL-safe), `en.assistant.ts` + 12 locales
- [x] `AgentWorkingOverlay` driven by SSE `step` events, with Stop
- [x] Handoff: `hydrateSeed` through `LinkDef.fill`, `AssistantReviewBanner`
      (summary, warnings, attach-bill reminder, "tell me what to change"),
      AI-filled field highlight; Save unchanged + proposal header

### 8.6 Voice
- [x] Port NGU `stt.py` (Voxtral → whisper.cpp fallback) as
      `assistant/transcribe/`; language hint from `User.language`; vocabulary
      biasing with item/party names; port `useVoiceInput` + `audio.ts`; tests

### 8.7 Evaluate and pilot
- [ ] Eval set ≥ 60 real staff utterances (Hindi/Hinglish/English, number
      words, units) under `pytest -m eval`; pick the model on it
- [ ] One-week pilot with 2–3 staff on the real books; review saved/opened
      ratio and most-edited fields; fix before wider rollout

### 8.8 Later
- [x] Bill photo → pre-filled Purchase Invoice (vision extraction + attach the
      photo as proof)
- [ ] Attendance / advance / employee payment proposals (after HR screens exist)
- [x] Spoken read-back of the summary (TTS)


---

## Phase 9 - Mobile UI & NGU Experience Parity (2026-10-30)

Design inspired by **Nidhi Gruh Udyog (NGU)**: mobile-first navigation, high data-density compact cards, and an interactive conversational assistant.

### 9.1 Mobile Navigation Shell (Top & Bottom Bars)
- [x] Mobile bottom navigation bar (`BottomBar.tsx`):
      Fixed at `bottom-0`, thumb-zone layout with 5 primary destinations (Home, Modules, Pending/Orders, Reports, Menu/More), badge counters, active tab indicators, and iOS/Android `safe-area-inset-bottom` padding.
- [x] Streamlined mobile `TopBar.tsx`:
      Compact header height (`h-14`), active company switcher pill visible on mobile, quick profile access, and search/language toggles.
- [x] `Layout.tsx` coordination:
      Add `pb-24 md:pb-6` content padding to prevent bottom bar overlap; reposition `<AssistantLauncher>` to `bottom-20 md:bottom-6`.

### 9.2 Compact Mobile Cards
- [x] Mobile workspace cards in `Home.tsx`:
      Refactor `WorkspaceCard` from heavy single-column `p-5` to compact 2-column grid (`grid-cols-2 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 sm:gap-4`), smaller icon badge (`h-8 w-8`), concise typography, and tap-friendly direct targets.
- [x] Compact pending work items:
      Tight padding (`py-2.5 px-3.5`), dense status badges, and quick link pills.
- [x] Compact module tiles in `ModulePage.tsx`:
      Dense `p-2.5`, `h-8 w-8` icons, and 1-column to 4-column responsive grid.
- [x] Responsive card view for records (`ListPage.tsx`):
      Provide a compact card view alternative on `< 640px` instead of wide horizontal table scrolling.

### 9.3 Assistant Chatbot NGU Parity
- [x] Mobile bottom-sheet panel:
      On mobile screens (`< 768px`), open `AssistantPanel` as a touch-friendly bottom sheet (`max-h-[85vh]` with drag handle, safe-area keyboard avoidance) matching NGU's `AssistantWidget.tsx`.
- [x] Starter prompt suggestion chips:
      Display tappable quick-question pills on empty state in the active language (e.g., "Aaj ke unbilled orders", "Haldi ka stock kitna hai?", "Naya Sales Invoice banao") matching NGU's `SUGGESTION_KEYS`.
- [x] In-chat interactive Proposal Cards:
      Render proposed documents (invoice, payment, stock) as an interactive card in the bubble with line items, quantities, and a single "Review & Save" trigger.
- [x] Voice recording waveform / pulsing visualizer.

---

## Phase 10 — Operating plan: the real working day (2026-10-31)

Design and full card instructions are in [planning/](planning/README.md). Implementers
read [planning/00-implementer-guide.md](planning/00-implementer-guide.md) first. Follow
the waves in the README. **OWNER** means a human does it; **BLOCKED** waits for that
question's recorded answer.

### 1. Access and workspaces
- [x] A-1 One all-access role
- [x] A-2 Record the exception
- [x] A-3 Landing workspace
- [x] A-4 Workspace home pages
- [x] A-5 Hide menus
- [x] A-6 Plain words
- [ ] A-7 Remove demo company (OWNER)

### 2. Orders, dispatch and billing
- [x] O-1 Dispatch status
- [x] O-2 Pending orders service
- [x] O-3 Dispatch action
- [x] O-4 Bill numbering
- [x] O-5 Pending Orders page
- [x] O-6 Ready warehouse default
- [ ] O-7 Real Tally test (OWNER)

### 3. Production and stock
- [x] P-1 Warehouses and stock constraints
- [x] P-2 Batch stage
- [x] P-3 Batch service (Q2 answered: bulk then pack)
- [x] P-4 Today's Batches page
- [x] P-5 Stock view
- [x] P-6 Loss report
- [ ] P-7 Item and recipe clean-up (OWNER, BLOCKED Q3)

### 4. Collections and supplier payments
- [x] M-1 Credit days
- [x] M-2 Due dates on bills
- [x] M-3 Paid or Due label
- [x] M-4 Money to Collect
- [x] M-5 Bills to Pay
- [x] M-6 Pages and short form
- [x] M-7 Oldest-first allocation
- [x] M-8 Proof required

### 5. Expenses
- [x] E-1 Category master
- [x] E-2 Mark bills as expenses
- [x] E-3 Expense service
- [x] E-4 Expense API
- [x] E-5 Expense page
- [x] E-6 Expense summary
- [x] E-7 Monthly repeats
- [x] E-8 Tally mapping check
- [ ] E-9 Category list (OWNER)
- [x] E-10 Employee expense claims and reimbursement (backend)

### 6. Taxes
- [x] T-1 Input credit flag
- [x] T-2 Tax Payment document
- [x] T-3 GST Position service
- [x] T-4 GST page
- [x] T-5 Credit at risk
- [x] T-6 TDS on salary
- [x] T-7 TDS register
- [ ] T-8 Rule check (OWNER)

### 7. Alerts and reminders
- [x] N-1 Email settings
- [x] N-2 Notifications app
- [x] N-3 Outbox sender
- [x] N-4 Rules engine
- [x] N-5 Raw material shortage
- [x] N-6 Digest and weekly emails
- [x] N-7 Instant alerts
- [x] N-8 Scheduler
- [x] N-9 Bell and alert settings
- [ ] N-10 Turn it on (OWNER)

### 8. Hindi agent and translation
- [x] H-1 Order proposals
- [x] H-2 Correct warehouses
- [x] H-3 Hindi and Latin matching
- [x] H-4 Rank by habit
- [x] H-5 Read tools
- [ ] H-6 More proposals
- [ ] H-7 Evaluation (OWNER for sentences)
- [x] L-1 Mark everything
- [x] L-2 Guard test
- [x] L-3 Build setup
- [x] L-4 Hindi — redone 2026-11-02: all 1,014 backend msgids translated (`backend/locale/hi/LC_MESSAGES/django.po`), placeholders checked, `core/test_hindi_api.py` proves an API message in Hindi. L-5 owner review still due
- [ ] L-5 Hindi review (OWNER)
- [x] L-6 Other languages

### 9. Go-live
- [x] G-1 Opening bills
- [x] G-2 Opening bills generator
- [x] G-3 Accountant corrections (`build_opening_bills --from-csv`, `accounts/test_opening_bills_build.py::CsvCorrectionTests`)
- [x] G-4 Integrity check
- [x] G-5 Keep openings out of Tally
- [x] G-6 Opening stock template
- [x] G-7 Backups
- [ ] G-8 Cutover (OWNER)

---

## Phase 11 — Ease of use and schema safety (2026-11-10)

Design, findings and full card instructions are in
[planning/12-ease-of-use.md](planning/12-ease-of-use.md). Do the `S` cards first.
**S-1 and S-2 must be live before 2027-04-01**: no fiscal year exists after 2026-27.

### 1. Schema and what we fill
- [x] S-1 Fiscal years never run out
- [x] S-2 Numbers follow the financial year
- [x] S-3 Database rules for lines and money
- [x] S-4 The warehouse is never asked
- [x] S-5 Setup health, shown before it hurts
- [x] S-6 How the money moved
- [x] S-7 An item needs only a name, a unit, a GST % and a price
- [x] S-8 One party, one record

### 2. Ease of use
- [x] U-1 Add a name from inside the form
- [x] U-2 Names to complete
- [x] U-3 Approve in one step, and never lose typing
- [x] U-4 Fix this
- [x] U-5 Plain words everywhere, errors that say what to do
- [x] U-6 Required means required up front
- [x] U-7 Today's work, four verbs, and the day summary (the "bills with no proof" row is not added yet)
- [x] U-8 Install on the phone
- [x] U-9 Quick note
- [ ] U-10 Share the bill, and one page per party (done: bank details on the bill, party page; open: a Share button, which needs a PDF or image renderer the app does not have, see doc 12)
- [x] U-11 Bring the lists in at once
- [ ] U-12 Hindi that sounds like the shop floor (OWNER)
- [ ] U-13 Two-week pilot and the four numbers (OWNER)

### 3. Documents in
- [x] U-14 Read any bill, not only a photo
- [x] U-15 Other papers become entries
- [x] U-16 Send a bill to the ERP from WhatsApp
- [ ] U-17 Scan the QR on a supplier's e-invoice (later)
