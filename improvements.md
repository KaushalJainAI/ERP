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
