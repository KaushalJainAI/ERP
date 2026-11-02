# 9. Go-live

## Goal

Move daily work to the ERP on one clear date, with correct opening money and stock, and
without double-counting anything in Tally.

## Opening balances: accounts

The ERP holds Tally's ledger up to 2026-10-19. On that date:

| | Parties with a balance | Total |
|---|---|---|
| Customers owe us | 324 | ₹5,64,22,429 |
| We owe suppliers | 53 | ₹1,60,06,453 |

These figures are not final, because Tally is still in use. Retake them at cutover.

### The problem

These are balances only. Reminders, ageing and Money to Collect read bills, and Tally
recorded only 125 bill allocations.

### The 90-day problem

If every opening bill were dated on go-live day, nothing would look overdue for three
months, and old debts would stay hidden.

### Recommended approach

Build estimated bills from the ledger:

1. Take each party's sales or purchase entries from newest to oldest.
2. Stop when they add up to the party's balance. Those entries are probably what remains
   unpaid, assuming payments cleared the oldest bills first.
3. Create one opening bill per remaining entry, keeping its original date, with the due
   date at bill date + 90 days.
4. Keep anything left over as one bill dated at the start of the earliest year.

This assumption may fail for some parties, so:

- Run it first as a dry run, producing a sheet for the accountant.
- The accountant corrects important parties, starting with the largest balances.
- Fallback for messy parties: one bill per party, with a date the accountant gives.

Credit balances (customers we owe, or advances to suppliers) become unallocated advances.

### Mechanics

New document `OpeningBill`: party, date, due date, amount, reference. It is a record of
outstanding money, and posts no GL, because the balance is already in the ledger. Receipts
settle it oldest first.

Check during build how this fits `outstanding.py`, ageing and Payment Entry references,
since these expect real invoices. If it fits poorly, use `SalesInvoice` and
`PurchaseInvoice` rows with an `is_opening` flag and no postings.

Rules:

- The sum of a party's opening bills must equal its ledger balance at cutover.
  `verify_integrity` checks this.
- Amount above zero, and due date not before bill date.
- **Never exported to Tally**, which already holds these balances. Add a test.

### Other opening figures

- Bank and cash balances: confirmed against statements at cutover.
- GST credit carried forward: from the portal's credit ledger.
- GST and TDS still owed for the current period.

## Opening balances: stock

1. Choose a stock-take day with no movement.
2. Count everything in Unprocessed, Ready and any in-process material, including the 7
   items with bad Tally history (Achar Masala, Papad, Mix Masala, Jeeravan, Chat Masala,
   Old Bardan, Rai Dal 500gm).
3. Enter it by Stock Reconciliation (Opening Stock) into the new warehouses.
4. The accountant agrees the stock value, and the difference goes to a balance-sheet
   account.
5. Exclude it from Tally export if Tally already holds the closing stock.

Item units must already be corrected (Q3).

## Cutover timeline

| When | Action |
|---|---|
| 4 weeks before | Docs 1 to 7 working on test data; company GSTIN and items ready |
| 3 weeks before | Dry-run opening bills; accountant starts checking |
| 2 weeks before | Run real orders in parallel with Tally |
| 1 week before | Staff training in Hindi; Hindi review; backup restore drill |
| Cutover day | Stock count; final Tally entries imported; opening bills and stock posted |
| Day 1 | All daily entry in the ERP; Tally receives exports only |
| Week 1 | Daily Tally import; fix issues the same day |
| Month 1 | Accountant closes the month and confirms GST position |

Choose the first day of a month, so GST periods and reports stay clean. A date near 1
December or 1 April suits well.

## Duplicate-entry risk

After cutover, nobody enters covered vouchers directly in Tally. The export has
`TLY-` exclusions for history, and a duplicate entry in Tally would corrupt the official
books. The accountant agrees a written rule on what stays Tally-only.

## Training

- One short Hindi session per person, on their own workspace.
- A one-page Hindi card for each role.
- A named person for problems in week one.

## Rollback

Until the month is closed, Tally stays complete through exports. If the ERP fails badly,
daily entry returns to Tally, and unexported ERP vouchers are entered there by hand from
printed lists.

## Done when

The go-live checklist in `gap_plan.md` section 7 is complete, and the accountant has
accepted one full month.

## Task cards

### G-1 Opening bills
- **Depends:** M-3. **Files:** `accounts/models.py`, `accounts/services/opening.py`,
  `accounts/services/outstanding.py`, `verify_integrity.py`.
- **Read first:** `outstanding.ageing_report`, `allocated_amount`, `invoice_outstanding`,
  `PaymentEntryReference`.
- **Do:** choose the storage that fits these services. Try the `OpeningBill` document first:
  `company`, `party_type`, `party`, `bill_date`, `due_date`, `amount`, `outstanding_amount`,
  `reference`. Make it a valid `reference_doctype`. It posts no GL. Allocation must reduce
  `outstanding_amount`, and a cancelled payment must restore it. CHECKs: amount above 0,
  outstanding between 0 and amount, due date not before bill date. If this needs changing more
  than the listed services, stop and report before using the `is_opening` invoice option.
- **Test:** ageing and Collections include it; a receipt settles it; cancel restores it.

### G-2 Opening bills generator
- **Depends:** G-1. **Files:** `accounts/management/commands/build_opening_bills.py`.
- **Do:** options `--as-on`, `--company`, `--dry-run` (the default), `--commit`, `--out file.csv`.
  For each party, get the balance with `outstanding.party_balance`. Walk that party's debit lines
  (customers) or credit lines (suppliers) from newest to oldest, until the balance is covered,
  and use the voucher date and number. Any excess stays as one bill at the oldest date. Credit
  balances are listed separately, as advances. Due date is bill date plus credit days. The CSV has
  party, bill date, amount, due date and a method note. `--commit` must refuse unless totals
  match per party and overall. Never write in dry-run mode.
- **Test:** a simple party, a part-paid party, a credit balance, and a party with no debits.

### G-3 Accountant corrections
- **Depends:** G-2. **Do:** `--from-csv file.csv` loads corrected rows after the same total checks.
- **Test:** a mismatched total is refused with the party named.
- **Done 2026-11-02.** The accountant's loop:
  1. `python manage.py build_opening_bills --as-on 2026-11-30 --out opening.csv` (estimate).
  2. The accountant edits `opening.csv` in Excel: split, merge or re-date bills per party;
     a blank `due_date` means bill date + credit days. Only `bill` rows are loaded.
  3. `python manage.py build_opening_bills --as-on 2026-11-30 --from-csv opening.csv` (dry
     run: lists every problem, by row number and party).
  4. Same command with `--commit` once it reports no problems.

### G-4 Integrity check
- **Depends:** G-1. **Do:** in `verify_integrity`, add a check that outstanding values equal bill
  amount less allocations. A separate warning compares party opening totals with the cutover
  ledger balance.
- **Test:** a drifted row is reported.

### G-5 Keep openings out of Tally
- **Depends:** G-1. **Files:** `tally_export/constants.py`, `services/collect.py`, tests.
- **Do:** ensure opening bills and Opening Stock reconciliations do not export. Read how
  `StockReconciliation` is collected; add its opening case to the exclusion list.
- **Test:** an export after opening entries contains none of them.

### G-6 Opening stock template
- **Depends:** P-1. **Do:** `build_stock_count_sheet` writes a CSV of active items with blank counted
  columns for the three warehouses. `load_stock_count --dry-run` shows what it would post. On commit,
  it creates one Draft `StockReconciliation` with purpose `Opening Stock`, for a person to submit.
- **Test:** blank and zero rows; an unknown item is refused.

### G-7 Backups
- **Depends:** N-8. **Do:** add a nightly `pg_dump` job with 30-day retention, and write the restore
  steps in `DEPLOYMENT.md`.
- **Test:** restore a dump into a separate scratch database, not the real one.

### G-8 Cutover (OWNER)
Follow the timeline. Turn on `Company.require_proof`, retake balances, count stock, and enter opening
GST credit, each after a backup.
