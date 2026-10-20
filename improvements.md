# improvements.md

A prioritized, actionable improvement backlog for the ERP, produced from a full
read-through of the backend (`core`, `users`, `accounts`, `stock`, `selling`,
`buying`). It complements — and does not replace — the authoritative gap list in
`backend/docs/erpnext_divergences.md`; where an item is already tracked there it
is cross-referenced (e.g. *[div G4]*).

Priority key: **P0** correctness/security you should not ship without · **P1**
important parity/robustness · **P2** polish, DX, scope growth.

> Items marked ✅ **Done (2026-07-23)** were fixed in that pass; ✅ **Done
> (2026-10-21)** in the RBAC pass. The rest are proposals. See the "Fixed /
> hardened" section of the divergences doc for the running fix log.

> **New in this file:** a second, evidence-based gap audit of the *whole*
> repository (backend, frontend, ops and docs) was appended on 2026-10-21 —
> see [Gap audit (2026-10-21)](#gap-audit-2026-10-21--verified-against-the-code).
> It is **appended**; every item above is preserved as written.

---

## Already fixed ✅ (2026-10-21) — RBAC pass

| Area | Fix |
|------|-----|
| **RBAC — `is_staff` bypass** *[P0.3]* | `HasRolePermission` and `user_has_model_permission` no longer treat `is_staff` as an all-power bypass. In Django `is_staff` only means "may open the admin site"; it was handing every staff-flagged operator the whole company's books, plus submit/cancel authority. Only `is_superuser` bypasses now. Staff keep admin-site access via `ModelAdmin`. |
| **RBAC — fail open** *[P0.4]* | `HasRolePermission` now **denies** when a view cannot be tied to a model, instead of allowing it. Function-based report endpoints keep working because they declare their model via `require_model_permission(...)`. |
| **RBAC — the fail-open also hit empty tables** | Model resolution used truthiness checks (`if not model`), and a Django `QuerySet` is falsy when it has no rows — so on a fresh install, or any table the user had not written to yet, *every viewset* failed to resolve its model and was allowed through. Resolution now uses `is None` checks throughout (`users/permissions.resolve_model_label`). |
| **RBAC — privilege escalation via the role API** | `RoleViewSet` / `RolePermissionViewSet` were gated on `IsAdminUser` (`is_staff`), so any staff user could grant themselves `can_read` on `accounts.GLEntry` and read the whole ledger. They now require `HasRolePermission` on `users.Role` / `users.RolePermission` — the seeded *System Manager* role, or a superuser. |
| **RBAC — seeded label list had drifted** | The hand-maintained list pointed `accounts.SalesInvoice` at a model living in `selling` (and `accounts.PurchaseInvoice` at `buying`), listed three labels for models that do not exist (`selling.Quotation`, `selling.DeliveryNote`, `buying.PurchaseReceipt`), and **omitted `accounts.GLEntry` and `stock.Bin`** — the exact two labels the report endpoints are gated on. Consequence: **no seeded role could read a single report**; only `is_staff` could get in. The seed now derives labels from one matrix, validates every label against the app registry (a bad label aborts the seed), and prunes rows that no longer resolve. |
| **RBAC — roles were not scoped** | Every manager role held every permission on every model, so "Sales Manager" and "System Manager" were the same account wearing different hats. The seeded matrix is now least-privilege per domain, and read on `accounts.GLEntry` / `stock.Bin` (i.e. the financial and stock reports) is reserved for the Accounts and Stock Managers. The seed is authoritative for the six roles it owns, so a re-seed repairs a database seeded by the older version; operator-created roles are never touched. |
| **Tests** | New `users/test_rbac.py` (26 tests) covers all of the above, including that an empty queryset still resolves its model and that the seed is idempotent. Suite: 113 passing. |

---

## Already fixed in this pass ✅ (2026-07-23)

| Area | Fix |
|------|-----|
| Stock invariant | `stock.services.ledger.reverse()` now refuses to drive a `Bin` negative (cancelling a receipt whose stock was consumed) — the No-Negative-Stock mandate held only on the forward path before. |
| Self-healing | `verify_integrity` now also checks `Bin.valuation_rate == stock_value / qty` (valuation drift was invisible). |
| Audit trail | `perform_destroy` writes the audit row **after** a successful delete, not before — no more phantom "delete" entries for refused deletes. |
| API pagination | New `core.pagination.StablePageNumberPagination` applies a stable `pk` tie-breaker to unordered querysets — every list endpoint paged in undefined DB order before, silently skipping/duplicating rows. |
| Input validation | Line items reject `qty <= 0` and negative `rate`/`valuation_rate` (SO/SI/PO/PI/StockEntry). |
| Atomicity | Nested document `create()` (parent + lines + totals) is wrapped in `transaction.atomic()` — no more orphaned partial documents on a mid-create failure. |
| Multi-tenant integrity | SI/PI/PE/JE reject accounts, customers, suppliers, and lines that belong to a **different company** than the document; `PaymentEntry` rejects `paid_from == paid_to`. |
| **Security — report RBAC** *[div F1]* | The five financial/stock report endpoints were `IsAuthenticated`-only, and `HasRolePermission` *fails open* for function views, so **any** logged-in user read every company's books. Added `users.permissions.require_model_permission(...)`; reports now require `can_read` on `accounts.GLEntry` / `stock.Bin`. |
| Docs | Corrected the misleading `gl.repost()` docstring (it validates; it does not rebuild — balances are derived on read). |

---

## P0 — Correctness & security

### P0.1 Perpetual inventory / COGS is missing *[div G4]*
A `SalesInvoice(update_stock=True)` books revenue and moves stock but posts **no
cost entry**; a `PurchaseInvoice` expenses inventory instead of capitalising it.
The GL's inventory/COGS therefore never reconciles with `Bin.stock_value`.
- **Impact:** P&L overstates gross profit; balance sheet has no inventory asset;
  the ledger and the stock projection tell different stories.
- **Suggested:** introduce `Stock-in-Hand` (asset) + `COGS` accounts on `Company`.
  Purchase → `Stock-in-Hand Dr / Creditors Cr`. Sale → additionally
  `COGS Dr / Stock-in-Hand Cr` valued at the outgoing `stock_value_difference`
  the SLE already computes. Add a `verify_integrity` check that
  `Σ Stock-in-Hand GL == Σ Bin.stock_value` per company.
- **Effort:** M–L (touches posting services + a new integrity invariant + tests).

### P0.2 Closed / missing fiscal period is not enforced *[div G7]*
`FiscalYear.is_closed` exists but nothing blocks posting into a closed or absent
period, and `GLEntry.fiscal_year` is always `NULL`.
- **Impact:** back-dated postings into audited/closed periods; no period lock.
- **Suggested:** in `gl.post()` resolve the `FiscalYear` for `posting_date`,
  reject if missing or `is_closed`, and stamp `fiscal_year` on every row. Mirror
  the date guard for stock in `ledger.post()`.
- **Effort:** S–M.

### P0.3 `is_staff` grants a full RBAC bypass — ✅ **Done (2026-10-21)**
`HasRolePermission` returns `True` for `is_superuser` **or** `is_staff`. In Django
`is_staff` only means "may open the admin site", so any staff-flagged operator
silently bypasses every per-model/per-action rule.
- **Suggested:** gate the bypass on `is_superuser` alone (keep `is_staff` for
  admin-site access only), or introduce an explicit "Administrator" role. Decide
  and record the intent.
- **Effort:** S. ⚠️ Review existing seeded users first so you don't lock anyone out.
- **Resolution:** the bypass is now `is_superuser`-only, and the seeded
  *System Manager* role is the explicit administrator role (it carries
  `users.Role` / `users.RolePermission`, so it can grant authority without
  `is_staff`). `is_staff` still grants Django admin-site access. Role
  management moved off `IsAdminUser` onto RBAC at the same time, closing the
  escalation path where a staff user granted themselves ledger read.

### P0.4 `HasRolePermission` fails open when no model is resolvable — ✅ **Done (2026-10-21)**
For any view where the model can't be inferred, the class returns `True`. That is
what made the report endpoints world-readable (now fixed for reports, but the
fail-open default remains for any future function view).
- **Suggested:** default-deny (`return False`) and require views to declare a
  `model_label`; convert the report fix pattern into the norm.
- **Effort:** S, but audit every non-ViewSet endpoint before flipping.
- **Resolution:** the class now **denies** when no model resolves, and the
  resolution helper (`users/permissions.resolve_model_label`) uses `is None`
  checks rather than truthiness — an empty `QuerySet` is falsy, so the old
  truthiness check discarded the model on any table with no rows and turned
  every viewset into a fail-open. The five report endpoints declare their
  model explicitly with `require_model_permission(...)`, so they are unaffected.

---

## P1 — Parity & robustness

### P1.1 GST / tax computation *[div G5]*
No CGST/SGST/IGST lines; `grand_total == Σ(qty×rate)` (tax-exclusive). This is a
core Indian-market feature. Needs tax templates, per-line tax breakup, and GL tax
lines. **Effort:** L.

### P1.2 AR/AP outstanding & payment allocation *[div G6]*
Invoices carry no `outstanding_amount`; `PaymentEntryReference` is stored but
never allocates, never sets `against_voucher_*`, never drives aging or
paid/unpaid status. **Effort:** L.

### P1.3 True FIFO valuation *[div G1]*
`Item.valuation_method` offers FIFO but everything is valued Moving Average.
Requires per-layer quantity queues. **Effort:** L.

### P1.4 Cancellation does not re-value later moving-average entries
`ledger.reverse()` appends a tail reversal; it does not repost SLEs that were
valued off the entry being cancelled. `verify_integrity` (qty + value + now rate)
stays green because it sums facts, but a mid-history cancel can still leave a
economically stale `valuation_rate` on intervening entries.
- **Suggested:** on cancel, trigger a scoped `repost_stock(item, warehouse)` from
  the affected posting date forward (ERPNext's approach). **Effort:** M.

### P1.5 Writable nested `update()` is unimplemented
Draft documents are editable, but the nested-`items` serializers only implement
`create()`. A `PATCH` that includes `items` hits DRF's default nested `update`,
which raises. Today it's masked because drafts are rarely patched with lines and
submitted docs are blocked at 409.
- **Suggested:** implement `update()` (replace-children within `atomic()`) or make
  `items` read-only on update and require delete-and-recreate. **Effort:** M.

### P1.6 Auto-naming is dead code *[div F2]*
`name` is writable ⇒ DRF marks it required ⇒ the `NamingSeries.next()` fallback
never runs; clients must supply names and can collide within a series.
- **Suggested:** make `name` read-only on create and always derive from the
  series (allow an explicit override only behind a permission). **Effort:** S.

### P1.7 Cross-company still relies on application checks
The new company guards live in `validate_before_submit` (submit-time), not the
DB. A raw ORM/SQL write can still cross companies — same class of gap as *[div
G2]* for ledger immutability.
- **Suggested:** longer term, composite FKs / DB constraints or triggers keyed on
  company. **Effort:** M–L.

### P1.8 Throttle counters are per-process
The new rate limits use the default `LocMemCache`; with multiple workers the
`auth`/`user`/`anon` budgets are enforced per worker, multiplying the real limit.
- **Suggested:** require a shared cache (Redis/Memcached) in production settings;
  document it (already noted in the divergences fix log). **Effort:** S (ops).

---

## P2 — Polish, DX, scope

- **P2.1 Same-item duplicate lines / self-transfer.** No check stops a
  `Material Transfer` with `source == target`, or two lines for the same item in
  one document. Add line-consistency validation. *(S)*
- **P2.2 `PaymentEntry.received_amount` is stored but unused.** Meaningful only
  with multi-currency; either wire it or hide it until *[div G9]* lands. *(S)*
- **P2.3 `JournalEntry.total_debit/credit`** are only set by the serializer, not
  by `validate_before_submit`; a JE created by other code paths can show stale
  totals. Recompute at submit. *(S)*
- **P2.4 Selling/StockEntry pass the *selling/entered* rate as the SLE
  `valuation_rate`.** Harmless while `Bin.valuation_rate > 0` (cost basis wins),
  but wrong if a bin has zero valuation. Pass cost, not price. *(S)*
- **P2.5 `Account`/`Warehouse` tree integrity.** Nothing prevents a cycle in the
  `parent` self-FK, posting to a non-group leaf's children, or mixing companies
  across a parent/child account. Add tree validation. *(M)*
- **P2.6 Observability.** The swallowed-audit-failure path now logs, but there's
  no metric/alert on `SystemHealthCheck` rows with `status="alert"`. Wire
  `verify_integrity` into CI and a scheduled job. *(S–M)*
- **P2.7 Frontend/UX (not audited here).** Surface the new 400/403/409 error
  bodies as human messages; show `verify_integrity` health on an admin screen;
  block submit buttons client-side using the same qty/rate/company rules now
  enforced server-side so users get instant feedback. *(M)*
- **P2.8 Negative-stock policy is a hard default *[div G8]*.** Decide whether to
  expose ERPNext's "Allow Negative Stock" toggle or keep the strict default;
  record the decision.

---

## Suggested sequencing

1. ~~**P0.3 / P0.4**~~ ✅ **Done (2026-10-21)** — `is_staff` no longer bypasses RBAC,
   the check fails closed, and the seeded role matrix is scoped and validated →
2. **P0.2** fiscal-period lock (small, prevents bad data now) →
3. **P0.1** perpetual inventory/COGS (the biggest correctness gap) →
4. **P1.1 / P1.2** (GST, AR/AP — the features that make it usable in India) →
5. everything else as scope allows.

---

# Gap audit (2026-10-21) — verified against the code

A second pass over the **whole** repository — backend, frontend, ops and docs
— run after the RBAC pass. Every item below was confirmed by reading the code
or by running it, not inferred from the documentation. The earlier sections of
this file are **preserved as written**; this audit only adds.

**IDs continue the priority scheme above** (`P0.5`, `P1.9`, `P2.9` …) so the
new items sort alongside the existing ones. Where an item duplicates an
earlier entry it says so and carries the cross-reference.

> **Legend:** ✅ fixed · 📋 open · 🔁 extends an earlier item

---

## New P0 — correctness & trust

### P0.5 The dashboard is entirely hardcoded fake data 📋

`frontend/src/pages/Dashboard.tsx` makes **zero** API calls — no `apiClient`,
`useQuery`, `fetch` or `axios` anywhere in the file. Every figure is a
literal in the `kpis` array:

```tsx
{ title: "Total Sales",     value: 1250000,       change: "+14.2%" },
{ title: "Open Orders",     value: 345000,        change: "+5.1%"  },
{ title: "Stock Valuation", value: 890000,        change: "FIFO Active" },
{ title: "System Health",   value: "100% Validated", change: "0 Drift" },
```

plus a hardcoded badge reading `verify_integrity Status: GREEN`.

- **Impact:** the app displays a healthy ₹12.5L sales book, a populated
  warehouse and a green integrity status on an **empty database**. This is
  the most misleading thing in the project — worse than a missing feature,
  because it looks like a working one. "FIFO Active" is *also* false
  (see P1.3 below). `core.SystemHealthCheck` rows are written by
  `verify_integrity` and read by **nothing**.
- **Suggested:** wire the four KPIs to real endpoints and read the latest
  `SystemHealthCheck` row for the health tile — or, if the panel is
  deliberately a mockup, label it as demo data. A health tile that cannot
  go red is worse than no health tile.
- **Effort:** S–M.
- **Cross-ref:** `checklist.md` Phase 3 currently ticks *"Dashboards / KPIs
  (Executive dashboard with real-time metrics & health)"*. That tick is
  **not earned** and should be revisited.

### P0.6 The amend flow produces a broken document 📋

`core.SubmittableDocument.amend()` copies only `self._meta.fields` — the
parent's own columns. **Child line items are never copied**, and neither are
the recomputed totals.

Verified by running it against a two-line `JournalEntry`:

```
original lines: 2
amended lines.count() -> 0
amended total_debit: Decimal('0.00')
submit REFUSED: ValidationError: ['Journal Entry must have at least one line.']
```

`core/mixins.SubmittableViewSetMixin.amend_doc` then saves it and returns
**201 Created**, so the client believes the amendment succeeded.

- **Impact:** every amend yields an unusable document that can never be
  submitted and must be re-keyed by hand — defeating the purpose of amend
  entirely. Affects all seven submittable types (`JournalEntry`,
  `PaymentEntry`, `StockEntry`, `SalesOrder`, `SalesInvoice`,
  `PurchaseOrder`, `PurchaseInvoice`).
- **Suggested:** copy the child rows inside `transaction.atomic()` (deep-copy
  each line, re-point the parent FK, clear the auto-`id`), recompute the
  totals (`total_debit` / `total_credit` / `grand_total`), and re-run the
  same `validate_before_submit` the original passed. Add a test that amends
  a multi-line document and asserts the line count and totals survive.
- **Effort:** S–M.

### P0.7 Role grants are the only unaudited mutation 📋

Every business ViewSet carries `AuditLogMixin` — directly, or through
`SubmittableViewSetMixin`. `users.views.RoleViewSet` and
`users.views.RolePermissionViewSet` do **not**. Creating, editing or
deleting a role or a permission grant therefore leaves **no
`core.AuditLog` row**.

- **Impact:** the single most security-sensitive mutation in the system —
  granting yourself `can_read` on `accounts.GLEntry` — has no trail. The
  2026-10-21 RBAC pass made role management the control plane; the audit
  log did not follow it.
- **Suggested:** add `AuditLogMixin` to both viewsets, and use a distinct
  `action` value (`grant` / `revoke`) so a permission change is
  distinguishable from a generic update.
- **Effort:** S.
- **Related:** `core.AuditLog` and `core.SystemHealthCheck` have **no read
  endpoint at all** — the audit trail is currently visible only in the
  Django admin.

### P0.8 No API to assign a role to a user 📋

`users.UserRole` has no ViewSet. Assigning a role to a user is a Django
admin or `manage.py shell` action only.

- **Impact:** now that role management is RBAC-gated (P0.3), a
  non-superuser *System Manager* can grant permissions but **cannot give
  anyone a role** — the grants it writes are unreachable by anyone else. A
  non-superuser administrator can also not onboard a user.
- **Suggested:** expose a `UserRole` endpoint (RBAC-gated on
  `users.UserRole`), or accept a `role_ids` list on the user write path.
- **Effort:** S–M.

---

## New P1 — parity & robustness

### P1.9 `verify_integrity` implements 3 of `plan.md`'s 7 invariants 📋

`plan.md` §6 ("Consistency invariants — what `verify_integrity` enforces")
specifies **seven**. The command checks **three**:

| # | Invariant | Status |
|---|-----------|--------|
| 1 | `Bin.actual_qty == Σ SLE.qty`, `Bin.stock_value == Σ SLE.stock_value_difference` | ✅ checked |
| 2 | Every submitted voucher satisfies `Σ debit == Σ credit` | ✅ checked |
| 3 | `SalesInvoice.outstanding_amount == grand_total − Σ allocated payments` | ❌ missing |
| 4 | `SalesOrderItem.delivered_qty == Σ` linked delivery quantities, `≤ qty` | ❌ missing |
| 5 | Doc status is monotonic (no `1→0`, no `2→anything`) | ❌ missing |
| 6 | No orphan ledger (every GL/SLE row references an existing, non-draft voucher) | ❌ missing |
| 7 | Naming uniqueness across submitted documents | ❌ missing |

- **Impact:** the self-healing engine is the project's headline guarantee and
  it covers 3 of 7 promised invariants. Two of the missing five (#3, #4)
  depend on fields that do not exist yet (`outstanding_amount`,
  `delivered_qty` — confirmed absent), so the data model is behind its own
  checker.
- **Suggested:** implement **#5, #6 and #7 first** — they need no new fields
  and close real holes, particularly #6 (an orphan ledger row is exactly
  the corruption a repost should catch). #3 and #4 land with P1.2 and the
  fulfilment work.
- **Effort:** M (#3/#4 blocked on other items).
- **Cross-ref:** extends P2.6.

### P1.10 `repost_gl` rebuilds nothing — the docs promise otherwise 📋

`accounts/management/commands/repost_gl.py` calls `gl_service.repost()`,
which is a per-voucher `Σ debit == Σ credit` re-validation — a partial
duplicate of `verify_integrity`. `gl.repost()`'s own docstring is honest
(*"there is no stored projection to rebuild here — balances are derived on
read"*), but `README.md`, `CLAUDE.md` and `checklist.md` all advertise
`repost_gl` as *"rebuild GL projections"*.

- **Impact:** an operator who believes the GL is a rebuildable projection
  will not notice that it is not one. `repost_stock`, by contrast, **does**
  genuinely rebuild `Bin` from the SLE log (`stock/services/ledger.py`
  deletes and replays the bins) — so the self-healing story is real for
  stock and hollow for the ledger.
- **Suggested:** decide whether the GL should own a stored projection. If
  not, correct the three docs to say "re-validates", and consider renaming
  the command to `verify_gl` so it stops implying a rebuild.
- **Effort:** S (docs) or L (build a real projection).

### P1.11 The Outbox is a dead table and Celery is a dead dependency 📋

`core.Outbox` has a model, a `dedupe_key` UNIQUE constraint, a Django admin
registration and two unit tests. **Nothing ever writes to it and nothing
ever dispatches it.** `celery==5.4.0` and `redis==5.0.7` are in
`backend/requirements.txt`, but there is **no Celery app, no
`INSTALLED_APPS` entry, no worker and no beat schedule** anywhere in the
tree.

- **Impact:** mandate #1 (*"external effects go through the Outbox"*) is
  unimplemented, and the requirements file advertises an infrastructure layer
  that does not exist. `plan.md` Phase 3 explicitly called for a *"Celery
  worker for Outbox + scheduled `verify_integrity`"*.
- **Suggested:** either build it (worker + beat schedule + a
  `dispatch_outbox` command + one real side-effect) or remove
  `celery` / `redis` from requirements and keep the Outbox as a documented
  future seam. Shipping a half-wired dependency list is the worst of the
  three options.
- **Effort:** M–L (build) or S (remove).

### P1.12 The frontend is read-only and its navigation points at nothing 📋

Three findings, one root cause: the frontend was built as a list-view
prototype and never grown into the application the backend describes.

- **`<DocForm>` is dead code.** The component — the headline of the
  "metadata-driven SPA" architecture in `frontend/docs/architecture.md` — is
  **never imported by any page**. The only references to it are inside
  `DocForm.tsx` itself.
- **`frontend/src/api/endpoints.ts` is 0 bytes.** Every page hand-writes
  URLs against `api/client.ts` instead.
- **`Sidebar.tsx` links to three routes that do not exist:**
  `/selling/sales-orders`, `/buying/purchase-orders`, `/users`.
  `AppRoutes.tsx` defines only `/login`, `/dashboard`, `/accounts/*`,
  `/stock/*` and a catch-all `*` → `/dashboard`. Clicking "Selling",
  "Buying" or "Users" **silently redirects to the dashboard**.

Consequence: the selling chain, the buying chain, payments and user
administration have **no UI at all**, and no screen anywhere can create or
edit a document. The report surface is also partial — the API exposes
General Ledger, P&L and Balance Sheet, but the frontend only surfaces Trial
Balance (`AccountsPage` has tabs `accounts | journals | trial`).

- **Suggested:** build the missing routes and document screens on top of the
  existing `<DocForm>`, and make the sidebar either link to real routes or
  hide the items until they exist. A nav item that silently does nothing is
  worse than a missing one.
- **Effort:** M–L.
- **Cross-ref:** extends P2.7. `checklist.md` Phase 1.5 (*"Frontend:
  Account/Item/Warehouse lists + `<DocForm>`; JournalEntry & StockEntry with
  submit/cancel"*) and Phase 2.4 (*"Frontend flows for both cycles"*) are
  **not earned** and should be revisited.

### P1.13 `model_label` is free text, not a foreign key 📋

`users.RolePermission.model_label` is a `CharField`, not a FK to a model.
The seed now validates every label against the app registry (P0.3 fix), but
the **Django admin's `RolePermissionInline` does not** — a typo typed into
the admin grants authority on nothing, silently, and there is no DB-level
guarantee that the label names a real model.

- **Impact:** a silent, undetected misconfiguration on the control plane.
- **Suggested:** point `model_label` at `django.contrib.contenttypes`
  (`GenericForeignKey` style) or add a `ModelLabel` registry table with a
  FK, and validate the admin form against it.
- **Effort:** M (with a data migration).

---

## New P2 — polish, DX, scope

### P2.9 `contacts/` and `core/` are empty apps mounted as live routes 📋
`backend/contacts/` and `backend/core/` are mounted in `config/urls.py` at
`/api/contacts/` and `/api/core/`, but both `urls.py` files contain
`urlpatterns = []`, and `contacts/models.py`, `contacts/serializers.py` and
`contacts/views.py` are **0 bytes**. Meanwhile `Customer` / `CustomerGroup`
live in `selling/models.py`, `Supplier` / `SupplierGroup` in
`buying/models.py`, and `ItemGroup` in `stock/models.py` — so the bounded
contexts do not match the layout documented in `README.md` and `CLAUDE.md`.
- **Suggested:** move the contact masters into `contacts/` (the natural home)
  or delete the app and correct the docs.
- **Effort:** M (with migrations).

### P2.10 A test-only model ships in production `core/models.py` 📋
`core.models.DummySubmittableDoc` — *"Concrete SubmittableDocument model used
for testing submittable document workflows"* — lives in the production
models module, with its own migration and a live table in every deployment.
- **Suggested:** move it into a test app or define it in the test module.
- **Effort:** S.

### P2.11 The frontend has no test framework at all 📋
`frontend/package.json` has no `test` script and no vitest / jest
dependency. The CI job `frontend-lint-build` only runs `npm run lint` and
`npm run build`. All 113 tests in the repository are backend.
- **Suggested:** add vitest + React Testing Library with a smoke test per
  page, and a CI job that runs them.
- **Effort:** M.

### P2.12 Insecure container defaults 📋
`docker-compose.yml` hardcodes `SECRET_KEY=django-insecure-docker-secret-key-12345`
and sets `DEBUG=True` on the backend container. Fine for a laptop; unsafe if
that compose file is ever used as a deployment.
- **Suggested:** read the secret from the environment and fail to start
  when `DEBUG=True` outside development.
- **Effort:** S.

---

## Existing items re-verified against the code (2026-10-21)

These are **not** new items — they are the earlier entries above, confirmed
against the current code so their state is on record. Nothing above was
rewritten.

| Item | Confirmed current state |
|------|--------------------------|
| **P0.1** COGS / perpetual inventory | Confirmed. `SalesInvoice.on_submit` posts only `debit_to Dr / income_account Cr` plus a stock-out SLE — **no cost entry**. `PurchaseInvoice.on_submit` posts `expense_account Dr / credit_to Cr` — periodic-style expensing, **no capitalisation**. The seeded `AccountType.STOCK` account ("Stock Assets") is **never referenced by any posting code**. The GL's inventory therefore never reconciles with `Bin.stock_value`. |
| **P0.2** Fiscal period | Confirmed. `GLEntry.fiscal_year` is a `CharField` that is **always NULL** — `gl.post()` takes `fiscal_year=None` and no caller ever passes one. `FiscalYear.is_closed` is referenced **nowhere** in `accounts/services/gl.py` or `stock/services/ledger.py`. |
| **P1.1** GST | Confirmed. No tax fields on any document; `grand_total == Σ(qty × rate)`. The `CGST` / `SGST` / `IGST` accounts are seeded by `seed_defaults` and then **never used**. |
| **P1.2** AR/AP | Confirmed, and worse than described. `PaymentEntry.on_submit` posts only the `paid_from → paid_to` transfer and **never reads `self.references`**; `PaymentEntryReference.allocated_amount` is never consumed; `outstanding_amount` and `delivered_qty` **do not exist as fields**. ⚠ `backend/docs/API.md` nevertheless lists that endpoint's DB tables as `PaymentEntry, GLEntry, (invoice outstanding)` and its note as *"Allocates against references"* — **that row is wrong and should be corrected.** |
| **P1.3** FIFO | Confirmed. Everything is valued Moving Average (`stock/services/ledger.py`). The dashboard additionally hardcodes the string "FIFO Active" (see P0.5). |
| **P1.4** Cancel does not re-value | Confirmed. `ledger.reverse()` appends a tail reversal and does not repost the intervening SLEs. |
| **P1.5** Nested `update()` | Confirmed. The nested `items` serializers implement `create()` only. |
| **P2.1** Duplicate lines / self-transfer | Confirmed. `StockEntry.validate_before_submit` requires both warehouses for a Material Transfer but **never checks `source != target`**, and no document rejects two lines for the same item. |
| **P2.3** `JournalEntry` totals | Confirmed. `total_debit` / `total_credit` exist **only** on `JournalEntry` (`accounts/models.py:94`) and are set by the serializer, not by `validate_before_submit`. |
| **P2.4** Selling rate as `valuation_rate` | Confirmed. `selling/models.py:139` posts `"valuation_rate": item.rate` — the *selling* price, not cost. `StockEntry` passes its own `item.valuation_rate` through unchanged. |

---

## Checklist ticks that are not earned

Recorded here so the backlog and `checklist.md` agree. These are claims in
`checklist.md` that the code does not support:

- **Phase 1.5** — *"Frontend: Account/Item/Warehouse lists + `<DocForm>`;
  JournalEntry & StockEntry with submit/cancel"*. `<DocForm>` is never
  rendered (P1.12); the Stock screen is a read-only list.
- **Phase 2.4** — *"Frontend flows for both cycles"*. There are no selling
  or buying routes (P1.12).
- **Phase 3** — *"Dashboards / KPIs (Executive dashboard with real-time
  metrics & health)"*. The dashboard is hardcoded (P0.5).
- **Phase 3** — *"Outbox transactional queue & `verify_integrity` self-healing
  engine"*. The Outbox has no producer or dispatcher (P1.11); the engine
  implements 3 of 7 invariants (P1.9).
- **Phase 3** — *"Full RBAC enforced on every endpoint; audit log verified
  for all mutations"*. Role grants are unaudited (P0.7), and role assignment
  has no API (P0.8).

---

## Suggested sequencing (revised 2026-10-21)

Supersedes the list above; nothing above was removed.

1. **P0.5 + P0.6** — the dashboard lies and amend produces broken documents.
   Small, self-contained, and both actively deceive a user. Start here. →
2. **P0.7 + P0.8** — audit the control plane, and let administrators assign
   roles. Small, and they complete the 2026-10-21 RBAC pass. →
3. **P0.2** — fiscal-period lock (small, prevents bad data now) →
4. **P0.1** — perpetual inventory/COGS (the biggest correctness gap) →
5. **P1.9** — close the missing `verify_integrity` invariants (#5, #6, #7
   first) →
6. **P1.1 / P1.2** — GST and AR/AP (the features that make it usable in
   India) →
7. **P1.12** — the frontend, once the data model underneath it is right →
8. **P1.10 / P1.11** — make the self-healing story true (or remove the
   promises) →
9. everything else as scope allows.
