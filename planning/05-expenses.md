# 5. Expenses

## Goal

Every rupee spent is recorded with proof, placed in a clear category, and shown as
**Paid** or **Due**. The owner can answer: where did the money go this month, and what
do we still owe?

## The problem today

The books have 102 expense accounts from Tally, with duplicates such as `Advet Exp`,
`Advt Exp Gst 12%` and `Advertisement Expenses`. Staff cannot choose from that list,
and it cannot give a clean monthly picture. The only ERP path for a general expense is
a Journal Entry, which is an accountant's tool.

## Design

### Categories

New master `ExpenseCategory`: name (translatable), account, `is_active`. Start with
about 15, agreed with the accountant, for example:

Electricity, Rent, Freight and cartage, Packing and labour charges, Machinery repair,
Fuel and vehicle, Office and stationery, Phone and internet, Professional fees, Bank
charges, Licence and lab fees, Marketing and advertising, Staff welfare, Other.

Each category points at one account. Old accounts stay for history and do not appear
in the picker. Every category needs a Tally ledger mapping before export.

### Each expense is a bill

An **Expense** is a `PurchaseInvoice` with `update_stock=False`, plus two new fields:
`is_expense` and `expense_category`. Each category has a hidden non-stock item for the
line.

Why reuse it instead of a new document:

- GST input credit flows into the GST reports with no extra work (doc 6).
- Outstanding, ageing, payments and Paid or Due status already work.
- Tally export already handles it.
- There are fewer ledger paths to keep correct.

A generic **Cash Expense** supplier covers small spends with no named vendor. It is
treated as unregistered, so no GST credit arises. Only a real supplier with GSTIN can
give credit.

### The form

| Field | Notes |
|---|---|
| Category | Large list, Hindi names |
| Paid to | Search suppliers, or Cash Expense |
| Amount | Total as on the bill |
| GST on bill? | If yes: bill number, GST rate, credit claimable (default yes) |
| Date | Today by default |
| Status | **Paid now** (cash or bank) or **Pay later** (due date) |
| Proof | Photo or file; required |

**Paid now** saves the expense and a Payment Entry together in one transaction. **Pay
later** puts it into Bills to Pay. Status is always derived from payments, never typed.

### Regular expenses

Rent, electricity and similar: a `Repeat monthly` option creates a draft each month
and reminds the Admin (doc 7). Add this after the basic form works.

### Wages

Wages are not re-entered. Payroll posts them already, so the report reads them from the
ledger as their own line.

## Reports

**Expense summary:** by month and category, with Paid, Due and total, and a comparison
with last month. Drill down to the bill and its proof. The total should reconcile to
the expense accounts in the P&L, apart from purchases and stock movement.

Marketing is one category today. The owner's goal of return per campaign can add a
campaign tag later, without changing this design.

## Rules

- Amount above zero; category required; proof required at submit.
- Supplier bill number is unique per supplier (existing constraint), and is optional
  for Cash Expense.
- Due date is required for Pay later.
- Paid expenses keep the normal cancel path.

## Agent

"Bijli ka bill 12,400 diya" opens the form with category, amount and Paid filled.
A photo-to-expense step can follow later (checklist 8.8).

## Tests

- Paid now creates both documents, or neither.
- Pay later shows Due, then Paid after payment.
- GST on an expense appears in input credit; Cash Expense gives none.
- Category totals match the GL.
- Missing proof blocks submit.

## Done when

- One month of real expenses is entered by the accountant.
- The summary total matches the ledger.
- All categories export to Tally ledgers correctly.

## Task cards

### E-1 Category master
- **Files:** `buying/models.py`, `buying/serializers.py`, `buying/views.py`, `buying/urls.py`,
  `frontend/src/resources/buying.ts`, `seed_defaults.py`.
- **Do:** add `ExpenseCategory(TimeStampedModel)`: `company`, `name`, `account` (PROTECT, Expense
  root, non-group), `item` (PROTECT, non-stock), `is_active`. Unique on `(company, name)`.
  Validate the account's root type and company. Add CRUD, register it for translations like
  other masters, and add it to `Staff` permissions. Add `ensure_categories(company)`, creating
  the listed categories and their hidden non-stock items only when an account is passed in by
  the accountant's mapping file. It must never guess an account.
- **Test:** a non-expense account or a group is refused; a duplicate name is refused.

### E-2 Mark bills as expenses
- **Depends:** E-1, M-2. **Files:** `buying/models.py`, `buying/serializers.py`.
- **Do:** add `PurchaseInvoice.is_expense` (default False) and nullable `expense_category`
  (PROTECT). CHECK: `is_expense` true requires a category. An expense has `update_stock=False`,
  and `expense_account` equals the category account; enforce this in `validate_before_submit`.
  Read how `bill_no` is required at submit, and allow it blank only for a supplier flagged
  `is_cash_expense` (a new Supplier boolean).
- **Test:** each rule; the unique supplier-and-bill constraint still applies.

### E-3 Expense service
- **Depends:** E-2, M-8. **Files:** new `buying/services/expenses.py`.
- **Do:** `create_expense(company, category, supplier, amount, date, has_gst, tax_template,
  bill_no, paid_now, paid_from, due_date, user)`. In one atomic block, create a one-line
  invoice with the category's item, use `accounts.mapping.default_payable_account`, then
  submit if requested. If `paid_now`, create and submit a Payment Entry using
  `mapping.build_payment_entry`, allocated to it. When the amount includes GST, split it with
  the same tax calculation as a normal bill; do not write new tax arithmetic.
- **Test:** the design's Tests list, including rollback when payment fails.

### E-4 Expense API
- **Depends:** E-3. **Do:** `POST /api/buying/expenses/` and `GET` with filters for month,
  category and status. Reuse invoice serializers for output, plus `payment_label`. Update
  `API.md`.
- **Test:** create, filter, and that a Cash Expense gives no input credit.

### E-5 Expense page
- **Depends:** E-4. **Files:** `frontend/src/pages/accounts/ExpensesPage.tsx`,
  `NewExpensePage.tsx`, routes, i18n.
- **Do:** the list shows month total, Paid total and Due total from the server, then rows with a
  status badge. The form follows the design table. Save does not depend on the agent.
- **Test:** `npm run build`; check at phone width.

### E-6 Expense summary
- **Depends:** E-4. **Do:** `GET /api/accounts/reports/expense-summary/?from=&to=` gives category,
  Paid, Due, total and last month's total. Wages come from the wage expense account in the
  ledger, as a separate line. Add a `ReportConfig`, with drill-down to the list.
- **Test:** the total equals the GL for those accounts over the period.

### E-7 Monthly repeats
- **Depends:** E-3, N-8. **Do:** `RecurringExpense`: category, supplier, amount, day of month,
  `is_active`. A scheduler job creates a draft once a month, unique per recurring row and month.
  Mention it in the digest.
- **Test:** runs once a month, and a short month uses its last day.

### E-8 Tally mapping check
- **Depends:** E-2. **Do:** read `tally_export/services/build.py` and `maps.py`. Confirm a bill
  with `update_stock=False` and a non-stock line exports as an expense without stock lines, and
  add a test. If it does not, stop and report.

### E-9 Category list (OWNER)
The accountant agrees the categories and their account for each, including GST and Tally ledger
mapping. Provide it as a CSV for `ensure_categories`.
