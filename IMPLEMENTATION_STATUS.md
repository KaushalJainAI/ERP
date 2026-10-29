# Implementation Status

> **Audit date:** 2026-10-29
> **Base commit:** `b427a08` (`main`)
> **Working tree at audit time:** 45 modified/untracked paths — several findings below are coded but not committed.
>
> This is a point-in-time audit of what the repository actually implements. **The code
> is the source of truth**, not this document. Re-run the checks in
> [§9](#9-how-to-re-verify) before relying on a row below.

## Scope and method

Every item was checked against the current code, not just the planning docs:

- **Schema:** `backend/*/models.py` and `backend/*/migrations/`
- **HTTP surface:** `backend/config/urls.py`, `backend/*/urls.py`, serializers, views
- **Behaviour:** services, posting code, tests
- **Frontend:** `frontend/src/resources/`, `frontend/src/routes/`, pages
- **Planning intent:** `gap_plan.md`, `checklist.md`, `plan.md`, `assistant_plan.md`, `improvements.md`

**Status labels**

| Label | Meaning |
|---|---|
| **Implemented** | Model + migration + API + UI (where applicable) + tests exist and are wired. |
| **Partial** | Some layers exist, but the feature is not usable end-to-end or is uncommitted/unwired. |
| **Missing** | No meaningful implementation. |
| **Operational** | Code exists; completion needs business data or a human decision. |

---

## 1. Status at a glance

| Area | Status | Summary |
|---|---|---|
| Foundation, auth, RBAC, company isolation | **Implemented** | `UserCompany`, company context, scoped querysets, frontend company gate. |
| Accounting, GST, registers, Tally export | **Implemented** | GL, GST engine, GSTR-1/3B, registers, returns, Tally XML export. |
| Selling / buying core | **Implemented** | SO → SI → Payment, PO → PI → Payment, returns, credit limits, price lists. |
| Stock / manufacturing | **Implemented** | SLE/Bin, stock entries, reconciliation, batches, BOM/work orders, COGS. |
| Reporting | **Mostly implemented** | Financial statements, registers, GST reports, stock reports. GP/item-wise sales missing. |
| Printing / invoice issuance | **Partial** | JSON print payloads exist; no HTML/PDF renderer and no frontend print UI. |
| Banking and period close | **Partial** | Models/services exist but are uncommitted and have no migration, API, UI, or tests. |
| HR and payroll | **Partial** | Masters, attendance, payroll, advances done; leave, statutory payroll, registers, and some screens missing. |
| Assistant | **Missing** | No backend app or frontend UI. |
| Ops / reliability | **Partial** | Integrity checker exists; no scheduling/alerting, backup automation, or outbox dispatcher. |

---

## 2. Entirely unimplemented

| Plan item | Ref | What is missing | Evidence |
|---|---|---|---|
| Historical invoice backfill | A1 | No `is_historical`, legacy journal entry, historical status, or backfill command. The Tally history viewer is read-only and separate from Selling/Buying invoices. | No `historical`/`backfill` fields or commands in `selling`/`buying`; only `legacy.TallyRecord`/`TallyItemLine`. |
| Payment reconciliation tool | B3 | No unallocated-receipts → invoice matching API or screen. Only per-payment allocation on Payment Entry. | No reconciliation service/view; `accounts` has allocation logic only. |
| TDS (194Q / 194C / 194J) | B4 | No Tax Withholding models, rates, or posting logic. | No `TaxWithholding`/`TDS` model anywhere. |
| Delivery Note / Purchase Receipt | C2 | No models, endpoints, or UI. Dispatch/GRN is not separated from the invoice. | No `DeliveryNote`/`PurchaseReceipt`; stock posts from invoice/stock entry. |
| Quality Inspection | C3 | No inspection document, parameters, or acceptance/rejection flow. | Absent from models/URLs/resources. |
| Gross Profit / item-wise sales reports | C4 | No report endpoints or pages. | Absent from `accounts/reports` and frontend reports. |
| CSV/Excel import-export | C5 | No bulk party/item/price import or report export. CSV/XLSX code exists only for attachment MIME validation. | `core/attachments.py` only. |
| Contact people / WhatsApp | C6 | Only `contacts.Address`; no `Contact` model, phone roles, or WhatsApp integration. | `backend/contacts/models.py` has `Address`/`AddressType` only. |
| Leave | D2 / 7.3 | No `LeaveType`, `LeaveAllocation`, or `LeaveApplication`. “Paid Leave” attendance is the stand-in. | Absent from `hr/models.py`. |
| Salary/wage registers | D3 / 7.5 | Neither report exists. Advance balance report and bank-transfer file export are also missing. | No register endpoints; only per-employee advance summary. |
| MP minimum wage + wage-definition rule | D4 / 7.4 | No effective-dated minimum-wage table, skill categories, or 50% basic wage rule. | Absent from `hr`. |
| PF / ESI / PT / LWF / bonus / gratuity | D5 / 7.7–7.9 | No enable flags, contribution logic, registers, returns, or calculation reports. | Absent from `Company` and `hr`. |
| Piece-rate manufacturing | D6 / 7.10 | No worker output against Work Orders, piece-rate wages, or labour cost in production costing. | Absent. |
| Assistant (voice + chat) | G1 / Phase 8 | No `backend/assistant` app, models, read tools, proposal builders, SSE endpoint, frontend launcher/panel, or STT/TTS. | `backend/assistant` does not exist. |
| Backup/restore drill + scheduled dumps | F1 | No management command, cron, or restore runbook automation. Only ad-hoc backup files/docs. | `core/management/commands` has no backup command. |
| Integrity alerting / cron | F2 | `verify_integrity` writes health rows but nothing schedules it or alerts on failure. | No scheduler/worker/alert path. |
| Tally XML import | E1 | Tally **export** is done. Import of Day Book + All Masters XML (stock-journal lines, bank instruments, invoice extras, company master) is not. | `import_tally.py` imports legacy records, not E1 XML. |
| Quotation | `plan.md` §2 | Mentioned in the roadmap (`Quotation → SalesOrder → DeliveryNote`) but never built. | No model or resource. |
| True FIFO valuation | improvements P1.3 | `stock/services/ledger.py` explicitly uses Moving Average, even for FIFO-flagged items. | Comment at `stock/services/ledger.py:121`. |
| Outbox dispatcher / Celery | improvements P1.11 | `core.Outbox` model exists, but production code never writes to it and nothing dispatches it. No Celery app/worker. | Only test code creates `Outbox` rows; no `celery.py`. |

Deliberately skipped by user decision: e-Way Bill/e-Invoice and per-item UOM conversion.

---

## 3. Partial / uncommitted / not wired end-to-end

### 3.1 Printing and invoice issuance (A2)

- `backend/printing/` is **untracked** and mounted at `/api/print/` in `backend/config/urls.py`.
- Endpoints return **JSON print payloads only** (`PrintView` subclasses in `printing/views.py`).
- No HTML template, PDF renderer, or PDF dependency (`requirements.txt` has no WeasyPrint/ReportLab).
- No frontend print button, preview, or download flow.
- Tests cover payload builders (`test_print_data.py`, `test_words.py`), not rendered output.
- **Risk:** committing the `urls.py` change without the untracked `backend/printing/` directory breaks deployment with an import error.

**Remaining:** PDF/HTML templates, frontend trigger, company print settings (bank details, terms, signatory, logo), and an end-to-end test.

### 3.2 Bank reconciliation (B1)

- `BankAccount`, `BankStatementImport`, `BankTransaction`, `BankClearance` are in `backend/accounts/models.py`.
- `accounts/services/bank.py` implements clearance/matching logic.
- **No migration:** latest tracked accounts migration is `0008`; the tables do not exist in a migrated database.
- No serializer, view, URL, admin registration, frontend, or tests.

**Remaining:** migration, API, admin, screen, statement-import format handling, tests.

### 3.3 Period Closing Voucher (B2)

- `PeriodClosingVoucher` model and `accounts/services/closing.py` exist.
- **No migration, API, admin, frontend, or tests.**
- Cannot be used to close a year end-to-end.

### 3.4 Purchase invoice charges / landed cost (A5 / 6.9)

- `backend/buying/services/charges.py` is untracked.
- It references `PurchaseInvoiceCharge` and `PurchaseChargeTemplate`, **which do not exist** in `buying/models.py`.
- No migration, serializer, UI, or tests; the service cannot be used as written.

### 3.5 HR screens (D1 / 7.1–7.2)

- Most HR resources and panels are implemented (`resources/hr.ts`, payroll/employee panels).
- `hrLinks` exposes **dead routes**: `/hr/muster-roll`, `/hr/muster-report`, `/hr/inbox`.
- `frontend/src/pages/hr/MusterRollPage.tsx` is untracked and imported by nothing.
- Backend muster-roll endpoints exist, so this is a wiring gap, not a backend gap.

### 3.6 Translations (E3)

- Frontend: English plus 12 Indian-language bundles in `frontend/src/i18n/locales/`.
- Backend: only a `hi` `.po` file under `backend/locale/`.
- New `lg.*`, Journal Entry type/reference labels, and other recent strings are not translated into all 12 languages.

### 3.7 `verify_integrity` invariants

`plan.md` §6 defines seven invariants. The checker covers GL balance, stock/bin, negative stock, GST, outstanding, batches, payroll, manufacturing, and reconciliation, but **does not enforce**:

- #4 order fulfilment (`SalesOrderItem.delivered_qty` vs linked deliveries)
- #5 document-status monotonicity (no `1 → 0` or `2 → anything`)
- #6 orphan GL/SLE rows
- #7 naming uniqueness across submitted documents

### 3.8 `repost_gl`

- `accounts/services/gl.py:repost()` only re-validates debit == credit.
- README/CLAUDE describe a projection rebuild; there is no stored GL projection to rebuild, so the documentation and behaviour disagree.

### 3.9 GST setup (A3 / E4)

- `setup_gst_accounts` command and `gst/services/go_live.py` create separate Input/Output CGST/SGST/IGST accounts and 5/12/18% templates. This is implemented but **untracked** at audit time.
- Company GSTIN/PAN/address/GST category fields and the organisation profile screen exist.
- Remaining work is **operational**: run the command per company and enter the real company details. There is no dedicated GST setup wizard.

### 3.10 Company isolation (W0)

Implemented and verified: `UserCompany`, `core/company_context.py`, `CompanyScopedMixin`, frontend `CompanyContext`, and company selection gating. Do not treat W0 as an open gap.

---

## 4. Operational / business-input items

| Item | Ref | What is needed |
|---|---|---|
| Physical count of 7 negative-stock items | C1 | Stock Reconciliation is implemented. The count and adjustment entry are pending. |
| Reclassify 20 ledgers that change type between years | E2 | Accountant decision; profit differs in 3 years. |
| Tally Day Book + All Masters XML | E1 | Obtain the sender’s files before import work can finish. |
| Company GSTIN/PAN/address + GST accounts | A3/E4 | Enter real data and run `setup_gst_accounts` per company. |
| Assistant discovery | 8.0 | Confirm entry types, staff languages, phone vs desktop, STT provider. |
| Pilot and training | 8.7 / go-live | One-week pilot with 2–3 staff on real books. |

---

## 5. Stale checklist items that are actually done

`checklist.md` is accurate for the later phases (6.7–6.9, 7.3–7.10, Phase 8), but several earlier “pending” items are stale. These are implemented:

- COGS / perpetual inventory and fiscal-period lock
- Nested draft updates and amend-copying lines
- Invoice outstanding and payment allocation
- GST engine, GSTR-1/3B, tax ledger
- Stock reconciliation, returns, registers, dashboards
- User/role API, attachments, manufacturing, price lists, credit limits
- Tally **export**
- Most HR masters, attendance, payroll, advances, and employee screens

`improvements.md` Phase 1.5 / 2.4 / Outbox ticks are also outdated.

---

## 6. Recommended order

1. **Commit and finish B1/B2** — add the missing migration, serializers/views/URLs/admin, frontend screens, and tests.
2. **Finish A2** — HTML/PDF templates and a frontend print/download flow. This is the main “can we issue a bill?” blocker.
3. **Decide A1** — backfill 27,575 historical invoices or explicitly import opening balances only.
4. **A5 + C2** — landed cost on purchase invoices, then Delivery Note / Purchase Receipt.
5. **Payroll completeness** — leave, salary/wage registers, MP wage rules, then PF/ESI/PT/LWF when switched on.
6. **Assistant last** — largest single build, not required for go-live.

---

## 7. Pre-go-live hardening (not plan items)

- `docker-compose.yml` hardcodes `SECRET_KEY=django-insecure-docker-secret-key-12345` and `DEBUG=True`.
- No frontend test framework or `npm test` script.
- `core.models.DummySubmittableDoc` still ships in production models.
- `RolePermission.model_label` is free text, not a registry/FK.
- Throttling uses Django’s default per-process cache; no shared `CACHES` configuration.
- Celery/Redis remain dependencies without a worker or dispatcher.

---

## 8. Working-tree warning

At audit time the following major features were **untracked or modified but uncommitted**:

- `backend/printing/`
- `backend/accounts/services/bank.py`
- `backend/accounts/services/closing.py`
- `backend/buying/services/charges.py`
- `backend/gst/management/` and `backend/gst/services/go_live.py`
- `frontend/src/pages/hr/MusterRollPage.tsx`
- Register/GL/Tally lookup changes and report pages

Do not assume any of these are delivered until they are committed, migrated, and tested.

---

## 9. How to re-verify

Run these from the repository root (PowerShell):

```powershell
# What is actually tracked / uncommitted
git status --short

# Models that should exist for a feature
Select-String -Path backend\*\models.py -Pattern 'class (LeaveType|LeaveApplication|TaxWithholding|DeliveryNote|PurchaseReceipt|QualityInspection|Contact)\b'

# Migrations for bank reconciliation / period close
Select-String -Path backend\accounts\migrations\*.py -Pattern 'BankAccount|BankStatement|PeriodClosingVoucher'

# Routes for printing / banking / closing
Select-String -Path backend\config\urls.py,backend\*\urls.py -Pattern 'printing|bank|closing'

# Printing is still JSON-only?
Select-String -Path backend\printing\*.py -Pattern 'pdf|PDF|HTML|html|render'

# Frontend print UI
Get-ChildItem frontend\src -Recurse -Include *.tsx,*.ts | Select-String -Pattern 'api/print|window.print|PrintButton'

# Assistant app
Test-Path backend\assistant

# Integrity invariants
Select-String -Path backend\core\management\commands\verify_integrity.py -Pattern 'delivered_qty|monotonic|orphan|naming'

# Outbox producers / dispatcher
Get-ChildItem backend -Recurse -Include *.py | Select-String -Pattern 'Outbox.objects.create' | Where-Object { $_.Path -notmatch 'tests.py' }

# FIFO status
Select-String -Path backend\stock\services\ledger.py -Pattern 'FIFO|Moving Average'
```

Also run the actual test suite and `verify_integrity` before marking anything done:

```powershell
cd backend
python manage.py test
python manage.py verify_integrity
```

---

## 10. Updating this document

- Change a row only after the code, migration, API, UI, and tests all exist.
- Add the commit hash and date next to the change.
- Re-audit at least before each release; the planning docs drift quickly.
- Keep the **status** labels consistent with §Scope and method.
