# 12. Ease of use: a system people choose to open

Added 2026-11-10, after a code review of selling, buying, stock, accounts and the
frontend. Cards are `U` (use) and `S` (schema and what we fill).

## Goal

The staff know the business. They do not know software, they do not keep tidy
records, and they have never worked inside an ERP. The system must fit how they
already work, not train them to work its way. They should open it because it is
the fastest way to get their own job done, and for no other reason.

**Done when** each daily job below is finished within its field budget by the person
who does it, without help, in Hindi, on a phone, for two weeks running.

## Who we are designing for

| Person | Knows | Does not want | Wins when |
|---|---|---|---|
| Order entry | Customers, products, usual quantities | Forms, codes, typing | An order is in before the phone call ends |
| Factory manager | Stock, batches, workers | Anything that needs a desk | One tick dispatches and bills |
| Accountant | Tally, GST, parties | Double entry in two systems | Tally receives entries without re-keying |
| Owner | Who owes, what was spent | Reports to read | The phone answers "how are we doing today" |

## The seven rules

Every card below applies one of these. Use them to judge any future screen.

1. **Never ask what the system can know.** A field is on screen only if the person
   is the only source of that fact. Company, date, warehouse, accounts, tax, price,
   numbering and due date are the system's job.
2. **Capture now, complete later.** Disorganised work arrives as a spoken sentence, a
   bill photo, or half the facts. Accept it in that form at once. Missing details
   become a short list for the accountant, never a wall in front of the person.
3. **Work comes to the person.** Home is a list of what needs doing today, each row
   one tap from done. Nobody should have to find a menu.
4. **Say it in their words.** Bill, Order, Paid, Due, Money in, Money out. No Submit,
   Debit To, Ledger, Bin, Template. Errors say what to do next.
5. **Mistakes are cheap.** Nothing typed is ever lost. A wrong entry is fixed in one
   action. The books underneath stay immutable; the person never sees that machinery.
6. **One screen, one job.** A daily job fits on one phone screen. Everything else is
   behind "More details" and is never required.
7. **Give something back.** Every entry should return a visible benefit the same day:
   a bill to share, a balance that updated, a reminder that went away.

## What the review found

All items were checked in the code on 2026-11-10. Subagent findings that could not be
confirmed are left out.

### Already good (keep)

- Role home pages with figures, an attention list and shortcuts (`pages/Home.tsx`).
- Dispatch tick creates and submits the bill (`selling/views.py` dispatch,
  `PendingOrdersPage.tsx`).
- Record Money sheet: party and amount, oldest bill first, submits at once
  (`RecordMoneySheet.tsx`).
- One-call expense with proof (`buying/services/expenses.py`, `NewExpensePage.tsx`).
- Voice and bill photo open a prefilled page; the assistant cannot save.
- Company, dates, rate, due date, tax template, place of supply and party accounts are
  filled by the server. "More details" hides advanced fields and opens on an error.
- Stock can never go negative (`Bin` CHECK); supplier bill number is unique per
  supplier in the database.

### What pushes people away

| # | Finding | Evidence |
|---|---|---|
| F1 | A new customer, supplier or item cannot be added from inside a form. The lookup shows "No results" and the person must leave, losing their place | `LinkField.tsx` has no create action |
| F2 | Group is compulsory with no default on Customer, Supplier and Item; Item also demands a code and a unit | `selling/models.py:62`, `stock/models.py:49-55` |
| F3 | Direct bills need a warehouse per line, with no default. The error appears only at submit. A purchase order line without a warehouse silently makes a bill that moves no stock | `selling/models.py:424`, `accounts/mapping.py:312` |
| F4 | Nothing is saved until Save is pressed, and leaving the page gives no warning. Save and Approve are two buttons plus a confirm | `DocumentPage.tsx` (no draft guard; `:630-662`) |
| F5 | The generic forms still speak accounting: Submit Document, Debit To (Receivable), Income Account, Update Stock, Party Type, Entry Type, Stock Balance (Bins) | `en.core.ts`, `resources/selling.ts`, `resources/accounts.ts` |
| F6 | Admin sees 78 sidebar links in 10 modules. The phone bottom bar's "Reports" opens the General Ledger, and two of its labels use keys that do not exist | `resources/registry.ts`, `BottomBar.tsx:101,120` |
| F7 | Error toasts show raw field keys ("customer: ..."); network errors are English only | `api/endpoints.ts:124,176` |
| F8 | No way to say how money moved (cash, bank transfer, cheque). The system picks the default bank account, so cash received lands in the bank | `accounts/mapping.py` `default_bank_or_cash_account`; `PaymentEntry` has no mode |
| F9 | An item has no GST rate field. The rate exists only through a tax template; HSN is optional until a bill fails at submit | `stock/models.py:72-77`, `gst/services/supply.py:426` |
| F10 | Not installable and no weak-network safety: no manifest, no service worker | `frontend/index.html`, `frontend/public/` |
| F11 | All 12 Indian locale files are machine-generated and unreviewed; some pages have hardcoded English | file headers in `i18n/locales/`; `GstPositionPage.tsx`, `CollectionsPage.tsx` |
| F12 | No bulk import for items or parties; every master is typed by hand | no import route outside bank statements and Tally history |

### Schema and data risks

| # | Finding | Evidence | Risk |
|---|---|---|---|
| R1 | Only one fiscal year exists and there is no way to add one outside Django admin. Posting into a date with no fiscal year is refused | `core/models.py:632-650`, `closing_views.py:139` | **Every posting fails on 2027-04-01** |
| R2 | Order, payment, journal and tax numbers are hardcoded `SO-2026-`, `PO-2026-`, `PE-2026-`, `JV-2026-`, `TAX-2026-`. Invoices already use the financial year | `selling/serializers.py:144`, `buying/serializers.py:190`, `accounts/serializers.py:159,205,877`, `buying/services/expenses.py:237` | Wrong year printed from April 2027 |
| R3 | Line `rate` on orders and invoices is `(18,2)`; the project rule is `(18,6)`. A rate of 12.345 per kg is rejected. Not recorded as a divergence | `selling/models.py:204,549`, `buying/models.py:275,787` | Real prices cannot be entered |
| R4 | No database CHECK on line quantity and rate, payment amount, or journal line signs. Only serializers guard them | `selling`, `buying`, `accounts` models | Violates "the DB is the guarantee" |
| R5 | `FiscalYear` allows overlapping years | `core/models.py:618-630` | Ambiguous posting year |
| R6 | Customer and supplier names are unique only by exact case; GSTIN can repeat | `buying/models.py:84`, `selling/models.py:67` | Duplicate parties split one balance in two |
| R7 | A registered customer with no state is silently taxed as in-state | `gst/services/supply.py:134-146` | Wrong GST (CGST+SGST instead of IGST) |
| R8 | A company with no default tax template gets an empty tax table and no error. Company default accounts are nullable and fail only at submit | `gst/services/supply.py:520-540`, `core/models.py:373-395` | Bills with no GST; late errors |
| R9 | `bootstrap_company` uses `AccountType.STOCK_ADJUSTMENT`, which does not exist | `bootstrap_company.py:165` | Command crashes |
| R10 | `PurchaseOrder.schedule_date` and `SalesOrder.delivery_date` are only printed; `PaymentEntry.received_amount` is not used in posting | models | Harmless; keep hidden, do not ask for them |

Not a problem: no `float` in money paths; stored totals and statuses are documented
projections with integrity checks; party as text on `PaymentEntry` and `GLEntry` follows
ERPNext.

## What each job asks for

This is the contract. "Person gives" is the complete list; anything else on screen is a
defect. Counts exclude taps on Approve.

| Job | Person gives | System fills | Today | Fixed by |
|---|---|---|---|---|
| Take an order | Customer; per line item and quantity | Company, date, number, price, warehouse, tax, place of supply | Met by voice; by hand a new customer breaks it | U-1, U-2 |
| Dispatch and bill | One tick (or the sent quantity) | Bill, stock out, receivable, due date in 90 days | Met | – |
| Money received | Customer, amount, cash or bank (or a photo of the slip) | Account from the mode, oldest bills first, date | Mode missing | S-6 |
| Purchase bill | Photo or: supplier, bill number, item, quantity (rate if new) | Warehouse (Unprocessed), accounts, tax, due date | Warehouse asked; bill number error comes late | S-4, U-6 |
| Pay a supplier | Supplier, amount, how it went | Account, oldest bills first | Mode missing | S-6 |
| Expense | Category, amount, proof photo, paid or due | Supplier (cash), account, tax, number | Met where a cash supplier exists | S-5 |
| Move a batch | Product, quantity; actual output at Ready | Raw from the recipe, warehouses, loss | Met on the batch screen | – |
| New customer or supplier | Name (phone optional) | Group, GST category Unregistered, company | Group asked; must leave the form | U-1, U-2 |
| New item | Name, unit, GST %, selling price | Code, group, HSN later, valuation, tax template from the % | Code and group asked; no GST % field | U-2, S-7 |
| Opening stock | Item and counted quantity | Date, warehouse, opening account, value from the item rate | Opening account asked | S-4 |

Fields the person never sees in the daily screens: company, warehouse (while each
direction has one obvious warehouse), any account, tax template, place of supply, price
list, update stock, valuation method, party type, entry type, numbering, fiscal year.

## Design

### Home is "today's work"

Keep the role homes. Change what the attention list means: every row is a job with a
verb and one tap to finish it ("3 orders to send", "Sharma Traders pays today",
"2 bills have no photo", "5 new names need details"). An empty list says the day is done.
Jobs disappear as they are completed; that is the reward.

The phone bottom bar becomes the same four verbs for everyone, in this order: Home,
Speak, Money, More. "Reports" as a tab is removed; the owner's numbers are on Home.

### Speak, photo or tap, all ending on the same page

Voice and bill photo already open a prefilled page. Add a third door that needs no
assistant: a **quick note**. From any screen the person can save a line of text, a
voice clip or a photo as "to be entered" in one tap. It writes nothing to the books. It
appears on the accountant's Home as a job, and opening it starts the assistant with that
content. This is how a disorganised day gets captured without anyone being asked to be
organised.

### Add a name without leaving

When a lookup finds nothing, its last row is "Add '<typed text>'". It creates the
customer, supplier or item from the name alone and selects it. Records made this way are
flagged incomplete and appear in one list, **Names to complete**, for the accountant
(GSTIN, state, HSN, group). GST rules still hold: a bill that needs a missing fact
names the fact and links straight to it.

### Nothing is lost, and Approve is one step

Unsaved forms are kept on the device and restored on return. For the daily documents,
one button, Approve, saves and submits. The confirm step stays only for cancelling.
"Fix this" on an approved document cancels it and opens the corrected copy in one
action; the audit trail and reversal entries are unchanged underneath.

### Every entry pays back

- After a bill is approved, a **Share** button sends the PDF through the phone's share
  sheet (WhatsApp included). No integration is needed.
- Each customer and supplier gets one page: what is owed, bills, payments, last orders,
  with Call and Share statement buttons.
- A **day summary** goes to the owner each evening through the existing digest: sold,
  collected, spent, to collect, to pay, low stock.

### Rollout is part of the product

One job per person per week, starting with the job that saves that person the most
time. The owner sits with each person for the first ten entries. Each week the owner
reads four numbers (below) and one question is asked of each person: "what did you
still write on paper?" The answer is the next card.

## What we measure

Add to the existing assistant metrics. All are derivable from `AuditLog`,
`AssistantProposal` and document timestamps; no new tracking of people.

| Measure | Target |
|---|---|
| Median time from opening a daily form to Approve | Under 60 seconds |
| Fields typed per entry against its budget above | At or under budget |
| Approvals that fail with a server error | Under 5% |
| Entries cancelled the same day (a sign of confusion) | Falling week on week |
| Days each person made at least one entry | Every working day |
| Names waiting in "Names to complete" older than 7 days | 0 |

## Deliberately not doing

- **UPI QR on bills.** Customers pay in cash or by bank transfer (owner, 2026-11-10).
  Print the company's bank account details on the bill instead (part of U-10).
- **QR or barcode labels on packets.** Revisit only if the pilot shows wrong items being
  picked.

- **A one-step cash sale or POS.** Selling here is on 90-day credit; retail runs in NGU.
- **A setup wizard.** There is one company and it is already set up. A setup health
  check (S-5) covers the same failures.
- **Offline entry.** Books must not be written from a device that cannot be validated.
  U-8 protects drafts only.
- **Removing the full ERP screens.** They stay under More for the accountant.

## Task cards

Do the `S` cards first where a `U` card depends on them. Follow
[the implementer guide](00-implementer-guide.md).

### S-1 Fiscal years never run out
- **Files:** `backend/core/models.py`, `backend/accounts/services/gl.py`,
  `backend/core/management/commands/`, `backend/core/tests.py`, `backend/docs/API.md`.
- **Do:** add `FiscalYear.ensure_for_date(date)` that creates the April-to-March year
  containing `date` when none exists, named `YYYY-YY`, and never creates one before the
  earliest existing year. Call it from a daily scheduled task for today and today plus 30
  days, not from the posting path (a typo date must not create a year). Add a PostgreSQL
  exclusion constraint so years cannot overlap, after checking existing rows; on SQLite
  keep a model-level check. Add a `verify_integrity` check: a fiscal year exists for today
  and for 30 days ahead.
- **Test:** the task run on 2027-03-15 creates 2027-28; a second run changes nothing;
  an overlapping year is refused; posting on 2027-04-01 succeeds.

### S-2 Numbers follow the financial year
- **Depends:** S-1. **Files:** `backend/selling/serializers.py`, `buying/serializers.py`,
  `buying/services/expenses.py`, `accounts/serializers.py`, `accounts/bank_views.py`,
  `accounts/services/tax_payment.py`, `accounts/mapping.py`.
- **Do:** replace every hardcoded `"<PREFIX>-2026-"` with
  `account_mapping.financial_year_series(prefix, date)`, as invoices already do. Check
  what that helper returns for 2026-27 first: existing series must continue their current
  counter, not restart. If the format would change for the current year, stop and report.
- **Test:** an order dated 2027-04-02 gets the 2027-28 series starting at 0001; one dated
  2027-03-31 continues the 2026-27 counter; a grep test fails on any new literal year
  prefix outside tests.

### S-3 Database rules for lines and money
- **Files:** `backend/selling/models.py`, `buying/models.py`, `stock/models.py`,
  `accounts/models.py`, new migrations, matching tests.
- **Do:** first write a read-only management command `check_constraint_candidates` that
  counts rows which would break each rule below, including historical documents. Add only
  the constraints with zero violations; list the rest in the handover with counts.
  Rules: order lines `qty > 0` and `rate >= 0`; invoice lines `qty <> 0`, `rate >= 0`
  (returns carry negative quantity; confirm in `accounts/services/returns.py`);
  `StockEntryItem.qty > 0`; `Item.standard_rate >= 0`; `PaymentEntry.paid_amount > 0`;
  journal line `debit >= 0`, `credit >= 0`, not both non-zero; `docstatus IN (0,1,2)` on
  submittable documents; `NamingSeries.current >= 0`.
- **Test:** each constraint has a test that a direct ORM write violating it raises
  `IntegrityError`.

### S-4 Rates to six decimals, and the warehouse is never asked
- **Files:** `backend/selling/models.py`, `selling/serializers.py`, `buying/models.py`,
  `buying/serializers.py`, `accounts/mapping.py`, `stock/serializers.py`,
  `frontend/src/resources/selling.ts`, `buying.ts`, `stock.ts`,
  `backend/docs/erpnext_divergences.md`.
- **Do:** (a) change line `rate` and `price_list_rate` on `SalesOrderItem`,
  `SalesInvoiceItem`, `PurchaseOrderItem`, `PurchaseInvoiceItem` to `(18,6)`. `amount`
  stays `(18,2)`, half-up, through `compute_amount`. Confirm the identity CHECKs on
  totals still hold and prints show two decimals unless more are present. (b) In the
  Sales Invoice and Purchase Invoice serializers, default a blank line warehouse the way
  `SalesOrderSerializer.validate` does: `Company.ready_warehouse` for sales,
  `Company.unprocessed_warehouse` for purchases, else the first leaf warehouse. Apply the
  same to Purchase Order lines so `lines_update_stock` no longer turns stock off
  silently. (c) Stock Reconciliation with purpose Opening Stock defaults its account to
  the company's opening account when one exists. (d) In the frontend mark line warehouse
  `advanced` when the company has both defaults set, and fix the buying placeholder that
  says "Ready warehouse".
- **Test:** a rate of 12.345678 saves and the amount rounds half-up; a purchase bill sent
  with no warehouse posts stock into Unprocessed; a bill from an order with blank
  warehouses still moves stock.

### S-5 Setup health, shown before it hurts
- **Files:** `backend/core/services/` (new `setup_health.py`), `core/views.py`,
  `core/urls.py`, `core/management/commands/verify_integrity.py`,
  `core/management/commands/bootstrap_company.py`, `frontend/src/pages/Home.tsx`,
  `backend/docs/API.md`.
- **Do:** one function returns the list of missing pieces for a company, each with a
  plain translated sentence and the page that fixes it: default receivable, payable,
  income and round-off accounts; a default bank account and a cash account; Unprocessed,
  In Process and Ready warehouses; GSTIN and a default tax template per supply kind; a
  cash-expense supplier; at least one expense category with an account; a fiscal year for
  today. Expose it read-only at `GET /core/setup-health/`. Show the items on the Admin
  and Accounts homes as attention rows; show nothing when the list is empty. Add the same
  list to `verify_integrity` as warnings. Fix `bootstrap_company.py:165` (use an existing
  `AccountType` or none, matching `seed_defaults`).
- **Test:** a bare company lists every item; a fully configured one lists none;
  `bootstrap_company` runs to completion on an empty database.

### S-6 How the money moved
- **Files:** `backend/accounts/models.py`, `accounts/serializers.py`,
  `accounts/mapping.py`, migration, `frontend/src/components/accounts/RecordMoneySheet.tsx`,
  `frontend/src/resources/accounts.ts`, `buying/services/expenses.py`,
  `backend/docs/erpnext_divergences.md`.
- **Do:** add `PaymentEntry.mode` with choices Cash, Bank transfer, Cheque (nullable
  for historical rows, with a CHECK on the allowed values). When the bank-side account is
  not sent: Cash picks the company cash account; the others pick the default bank
  account. `reference_no` is required for Cheque only. In Record Money and the expense
  "paid now" step, replace the account picker with three large mode buttons, remembering
  the last one used; keep the account picker under More details. Record the difference
  from ERPNext's Mode of Payment master.
- **Test:** Cash posts to the cash account; Bank transfer to the default bank; a cheque with no
  number is refused with a translated message; historical rows are untouched.

### S-7 An item needs only a name, a unit, a GST % and a price
- **Files:** `backend/stock/models.py`, `stock/serializers.py`, migration,
  `frontend/src/resources/stock.ts`, `gst/services/` (template lookup).
- **Do:** make `code` optional on input and generate it from `NamingSeries` (`ITM-`)
  when blank. Default `item_group` to the company's root or a seeded "General" leaf, and
  `stock_uom` to the unit most used on existing items. Accept a write-only `gst_rate`
  (0, 5, 12, 18, 28) on the item serializer and resolve it to the matching seeded item tax
  template; reject a rate with no template. Do not add a stored rate column: the template
  remains the single source. In the form show name, unit, GST %, selling price and HSN;
  move everything else to More details.
- **Test:** an item posted with name, unit, `gst_rate=5` and a price saves with a code,
  a group and the 5% template; an unknown rate is refused.

### S-8 One party, one record
- **Files:** `backend/selling/models.py`, `buying/models.py`, serializers, migrations,
  `gst/services/supply.py`.
- **Do:** run the S-3 candidate command for case-insensitive duplicate names per company
  first. If none, replace the name uniqueness on Customer and Supplier with a
  `Lower("name")` unique constraint; if some exist, report them for the accountant and add
  only the serializer check. On create, when another active party in the company has the
  same GSTIN, return a warning the form shows ("Already saved as ...") without refusing
  (branches share a GSTIN). For a party whose GST category is registered, require `state`
  in the serializer, and derive it from the GSTIN's first two digits when blank, so place
  of supply is never guessed from the company.
- **Test:** "sharma traders" is refused when "Sharma Traders" exists; a registered
  customer saved with only a GSTIN gets its state; an out-of-state customer's bill
  carries IGST.

### U-1 Add a name from inside the form
- **Depends:** U-2. **Files:** `frontend/src/types/index.ts` (`LinkDef.quickCreate`),
  `frontend/src/components/doc/LinkField.tsx`, `frontend/src/resources/selling.ts`,
  `buying.ts`, `stock.ts`, i18n files.
- **Do:** add an optional `quickCreate` to `LinkDef`: the endpoint, a function building
  the payload from the typed text, and optional extra fields (unit and GST % for an item).
  When set and the search has text, `LinkField` shows a last row "Add '<text>'". With no
  extra fields it creates at once; with extra fields it opens a small sheet. On success it
  selects the new record and runs the link's `fill`. Enable it for customer, supplier and
  item links. Server errors show inside the sheet.
- **Test:** component tests if a frontend test runner exists; otherwise `npm run build`
  and `npm run lint`, and note the manual check in the handover.

### U-2 Names to complete
- **Depends:** S-7, S-8. **Files:** `backend/selling/models.py`, `buying/models.py`,
  `stock/models.py` (no new column: completeness is derived), a read-only view in
  `core/` or `contacts/`, `frontend/src/pages/accounts/` (new page), `Home.tsx`,
  `backend/docs/API.md`.
- **Do:** make group optional on input for Customer and Supplier, defaulting to the
  seeded root group ("All Customer Groups", "All Supplier Groups") in the serializer; the
  column stays NOT NULL. Add `GET /core/names-to-complete/` returning parties with no
  state, registered parties with no GSTIN, parties still in the root group, and items
  with no HSN or no tax template, each with what is missing. Add the page under Accounts
  and an attention row on the Accounts and Admin homes with the count.
- **Test:** a customer posted with only a name saves; the list contains it with "state"
  missing and drops it once the state is set.

### U-3 Approve in one step, and never lose typing
- **Files:** `frontend/src/components/doc/DocumentPage.tsx`, `frontend/src/types/index.ts`,
  resource configs for Sales Order, Sales Invoice, Purchase Invoice, Payment Entry,
  Stock Entry; i18n.
- **Do:** (a) add a resource flag `oneStepApprove`. When set, a new or dirty draft shows
  one primary button, Approve, that saves then submits (the code path at `submitMut`
  already saves when dirty), with no confirm dialog; "Save for later" becomes a secondary
  text button. Cancel keeps its confirm. (b) Keep the unsaved form of a new document in
  `sessionStorage`, keyed by resource and user, restore it on return with a "Continue
  where you left off" line and a Discard link, and clear it on save. (c) Warn before
  leaving a dirty form (`beforeunload` and the router blocker).
- **Test:** build and lint; manual script in the handover: type half an order, switch
  page, come back, the lines are there.

### U-4 Fix this
- **Files:** `frontend/src/components/doc/DocumentPage.tsx`, i18n. Backend only if no
  combined endpoint exists: check for an existing cancel-and-amend action before adding
  one.
- **Do:** on an approved document show one action "Fix this". It asks once ("The old
  entry will be cancelled and a corrected copy opened"), then cancels, amends and opens
  the new draft. If the cancel is refused (for example a payment is allocated), show the
  server's reason and the link to the blocking document. Any new endpoint must run cancel
  and amend in one `transaction.atomic()`.
- **Test:** backend test if an endpoint is added: a refused cancel leaves the original
  untouched and creates no draft.

### U-5 Plain words everywhere, errors that say what to do
- **Files:** `frontend/src/i18n/en.core.ts`, `en.accounts.ts`, `en.selling.ts`,
  `en.buying_stock.ts`, `locales/hi.ts`, `frontend/src/api/endpoints.ts`,
  `frontend/src/components/BottomBar.tsx`, the pages with hardcoded text listed in F11.
- **Do:** extend A-6 to the generic forms through translation values only (do not rename
  keys or fields): Submit Document to Approve; Amend to Correct; Debit To (Receivable) to
  Customer account; Income Account to Sales account; Update Stock to "Stock moves with
  this bill"; Stock Balance (Bins) to Stock in hand; Party to Customer / Supplier where
  the type is known. In `endpoints.ts` map a field key to its translated label before
  showing a toast, and translate the network-error fallback. Add the two missing bottom
  bar keys. Move the hardcoded strings in `GstPositionPage.tsx`, `CollectionsPage.tsx`,
  `RecordMoneySheet.tsx` and `AlertSettingsPage.tsx` to keys.
- **Test:** `scripts/check-i18n.mjs` passes; a grep for the old English phrases in
  `en.*.ts` values returns nothing.

### U-6 Required means required up front
- **Files:** `frontend/src/resources/buying.ts`, `selling.ts`, `gst.ts`;
  `backend/buying/serializers.py`.
- **Do:** mark the supplier bill number required in the Purchase Invoice form and
  validate it in the serializer on save for non-draft-only flows, so the error is not
  first seen at submit. For each submit-time `ValidationError` in
  `validate_before_submit` of the five daily documents, list it and either default the
  value (preferred) or surface the same check when the draft is saved, as a warning that
  does not block saving. Report the list in the handover.
- **Test:** a purchase bill saved with no bill number returns the warning on save and the
  error on approve, both translated.

### U-7 Today's work, four verbs, and the day summary
- **Depends:** S-5, U-2, U-9. **Files:** `frontend/src/pages/Home.tsx`,
  `pages/home/HomePanels.tsx`, `useHomeData.ts`, `components/BottomBar.tsx`,
  `backend/notifications/services/` (digest), i18n.
- **Do:** (a) reword every attention row as a job with a count and a verb, and add rows
  for setup health, names to complete, quick notes waiting and bills with no proof. An
  empty list shows "All done for today". (b) Bottom bar: Home, Speak (opens the
  assistant), Money (Record Money sheet for Accounts and Admin; Pending orders for
  Factory; Orders list for Orders), More (All Modules). Remove the Reports tab. (c) Add
  "cash in hand" to the Admin figures from the cash account balance. (d) Add an evening
  day summary to the existing digest for the Admin: sold, collected, spent, to collect,
  to pay, low stock. Unique per company and day, through the `Outbox`.
- **Test:** digest test: one summary per day, correct totals against a fixture; none
  when notifications are off.

### U-8 Install on the phone
- **Files:** `frontend/index.html`, `frontend/public/` (manifest, icons),
  `frontend/vite.config.*`, `frontend/src/main.tsx`.
- **Do:** add a web app manifest (name, icons from `logo.png`, standalone display, theme
  colour) and a service worker that caches only the app shell and static assets. API
  calls are never cached and never queued. When the network is down, show a translated
  banner "No connection. Your typing is kept." (U-3 keeps the draft.) Make icon buttons
  at least 44 px in `TopBar.tsx` and the `ChildTable` row actions.
- **Test:** build passes; the built `dist` contains the manifest and worker; a request
  to `/api/` is not served from cache (unit test on the worker's route filter if
  practical, else the manual check in the handover).

### U-9 Quick note
- **Files:** new model in `backend/assistant/models.py` (`QuickNote`: company, created
  by, text, attachment, status Open or Done, linked proposal), serializer, view, URLs,
  migration, `backend/docs/API.md`; `frontend/src/components/assistant/`, `Home.tsx`.
- **Do:** a note holds text, a voice clip or a photo and writes nothing to any ledger.
  Constraints: at least one of text or attachment; status in the allowed values. A plus
  button beside the assistant launcher saves a note in one tap. Notes appear as jobs on
  the Accounts and Admin homes. Opening one starts the assistant with the note's content
  (reusing the bill-photo and transcription paths) and marks it Done when the resulting
  document is approved or the person dismisses it. Attachments use the existing
  attachment storage.
- **Test:** an empty note is refused by the database; creating a note creates no GL or
  stock rows; approving the document from a note marks it Done.

### U-10 Share the bill, and one page per party
- **Files:** `frontend/src/print/PrintButton.tsx`, `frontend/src/pages/selling/` (new
  party page), `frontend/src/pages/accounts/CollectionsPage.tsx`, i18n.
- **Do:** print the company's bank account name, number and IFSC on the bill (from the
  default `BankAccount`), so a customer can pay by transfer. Add Share beside Print on
  approved bills and on the party statement, using the
  Web Share API with the PDF file, falling back to download. Add a party page reached by
  tapping a customer or supplier anywhere: amount owed, open bills, recent payments, last
  five orders, with Call (`tel:`), Share statement and Record Money. Use existing
  endpoints (party statement, collections); add none unless one is missing, and report it.
- **Test:** build and lint; manual check on a phone noted in the handover.

### U-11 Bring the lists in at once
- **Files:** `backend/stock/`, `selling/`, `buying/` (import service and view),
  `frontend/src/pages/` (one import page), `backend/docs/API.md`.
- **Do:** one upload page for items, customers and suppliers from a spreadsheet, modelled
  on the opening stock template (G-6): download a template, upload, see a preview with
  each row marked ready or with its problem in plain words, then confirm. The confirm
  runs in one transaction through the normal serializers, so every rule applies. Rows
  matching an existing name (case-insensitive) are skipped and reported, never updated.
- **Test:** a file with one bad row imports nothing and names the row; a clean file
  imports all rows; a repeat upload creates no duplicates.

### U-12 Hindi that sounds like the shop floor (OWNER)
- **Do:** the owner or a staff member reads the daily screens in Hindi (the five jobs,
  Home, errors) and marks every phrase nobody would say. Extends L-5; the implementer
  applies the corrections to `locales/hi.ts` and the backend `.po`.

### U-13 Two-week pilot and the four numbers (OWNER)
- **Depends:** all above. **Do:** run the rollout in "Rollout is part of the product".
  The implementer adds a read-only report of the measures in "What we measure" under
  Admin, built from `AuditLog` and document timestamps.

### U-14 Read any bill, not only a photo
- **Files:** `backend/assistant/bill_photo.py`, `assistant/views.py`, `assistant/tests.py`,
  `frontend/src/components/assistant/AssistantPanel.tsx`, i18n, `backend/docs/API.md`.
- **Do:** `BillPhotoView` accepts JPG, PNG and WebP today. Accept PDF as well (read the
  first pages within the existing size limit; refuse password-protected files with a
  translated message), and several photos for a bill that runs over pages. Keep the
  contract: the result is a proposal the person checks; nothing is saved. Show the
  document beside the prefilled form on the review page so each figure can be compared,
  and attach it as the bill's proof on save.
- **Test:** a PDF bill yields a Purchase Invoice proposal; an unreadable or protected
  file returns the friendly 200 failure; the no-write test covers the new path.

### U-15 Other papers become entries
- **Depends:** U-14, S-6. **Files:** `backend/assistant/bill_photo.py` (generalise to a
  document reader), `assistant/proposals.py`, `prompts.py`, `tests.py`,
  `AssistantPanel.tsx`.
- **Do:** let the reader decide what the document is, then build the matching proposal
  that already exists: a supplier bill (Purchase Invoice), a customer's written or typed
  order (Sales Order), a bank transfer slip, cheque or deposit receipt (Payment Entry,
  with the mode and reference number filled), an expense receipt (expense). When the
  kind is unclear it asks, with the choices as buttons. A slip that names no known party
  asks for the party; it never guesses.
- **Test:** one fixture per kind gives the right proposal type; an ambiguous document
  asks; an unknown party asks.

### U-16 Send a bill to the ERP from WhatsApp
- **Depends:** U-8, U-9, U-14. **Files:** `frontend/public/` manifest (`share_target`),
  service worker, a small receiving route in `frontend/src/routes/`.
- **Do:** register the installed app as a share target for images and PDFs. A shared
  file opens the assistant's document reader directly; if the person is not signed in or
  the read fails, the file is kept as a quick note (U-9) so it is never lost.
- **Test:** build passes and the manifest declares the share target; manual check on an
  Android phone noted in the handover.

### U-17 Scan the QR on a supplier's e-invoice (later)
- **Depends:** U-14. **Files:** `frontend/src/components/assistant/`, a decoder helper in
  `frontend/src/lib/`, `backend/assistant/bill_photo.py`.
- **Do:** only after the pilot shows wrong totals or bill numbers from document reads.
  When a bill photo contains a GST e-invoice QR, decode it (supplier GSTIN, bill number,
  date, total value) and use those exact values in the proposal in place of the read
  ones, flagging any mismatch for the person. The QR carries no line items, so it
  supplements the read and never replaces it. Do not verify the signature online.
- **Test:** a sample QR payload overrides a deliberately wrong read total and shows the
  mismatch.

## Open questions

| # | Question | Who | Blocks |
|---|---|---|---|
| Q7 | Do staff share bills with customers on WhatsApp today, or only on paper? | Owner | Priority of U-10 |
| Q8 | Is cash received kept separate from the bank until deposited? Customers pay in cash or by bank transfer, not UPI (owner, 2026-11-10) | Accountant | Default in S-6 |
| Q11 | Do suppliers send bills on paper, as PDFs, or both? **Answered 2026-11-10: both.** U-14 and U-16 are needed as written | Owner | – |
| Q9 | Are there prices with more than two decimals (per kg or per gram)? | Owner | Priority of S-4 (a) |
| Q10 | Which phones do staff use (Android version, screen size)? | Owner | U-8 testing |
