# Tally data: what the ERP holds, what it doesn't, what still needs Tally

Books: TallyPrime, NIDHI GRAH UDYOG, 10 FYs (2017-18 → 2026-27, the last up to
19-Oct-2026), read from the Tally Postgres extract (`tally-pg`, project
`Desktop\Tally`). Checked 2026-10-28. Companion to `tally_import_report.md`,
which has the per-year reconciliation (every ledger matching to the paisa) and
the archive-completeness table.

> **Update 2026-10-30: the in-ERP Tally archive is gone.** The `legacy` app (tables
> `legacy_tallyrecord`, `legacy_tallyitemline`), its `/api/legacy/` routes, the Past Bills
> UI, and the `import_tally`, `verify_tally_archive` and `prefill_from_tally` commands were
> removed. The ERP's own tables now hold everything it needs: accounts, parties (898
> addresses), items, prices, the `TLY-*` journals/stock entries (Tally voucher type/number is
> in their `user_remark`), per-party price lists (`Rates - <party>`), 6 estimated BOMs, and
> the `tally_export` ledger/item maps. The full Tally data remains in the external `tally-pg`
> database and the original company folders; a dump of the removed tables is at
> `backend/backups/legacy_tally_archive_2026-10-30.sql.gz` (git-ignored). Sections 1 and 3
> below describe the state before this removal; `TallyRecord` references are historical.

## 1. In the ERP

| Data | Where |
|---|---|
| Chart of accounts, 978 parties (GSTIN, state, PAN), 144 items (HSN, GST template) | masters |
| All 54,501 vouchers: accounting as `TLY-*` Journal Entries with type, cheque/bill no. | `accounts.JournalEntry` |
| Opening and closing stock value per year; FY 2026-27 stock ledger by item | GL, `stock.*` |
| Party mailing addresses (382 parties) | `contacts.Address` |
| **Every Tally master and voucher, every year, as Tally exported it**, linked to the ERP document it became | `legacy.TallyRecord` (`/api/legacy/tally-records/`) |
| **Item-wise sales / purchase / note lines, all 10 years** (item, qty, rate, party, ledger) | `legacy.TallyItemLine` (`/api/legacy/tally-item-lines/`) |

`python manage.py prefill_from_tally` (idempotent, `--dry-run` supported) then fills
the masters the import left empty: 10 `BankAccount` rows (number / IFSC from the Tally
bank ledgers, Bank of India current account as default), `Standard Selling` /
`Standard Buying` price lists with 90 + 66 `ItemPrice` rows (latest Tally rate per
item), `Item.standard_rate` (132 of 144 items), the customer-group default price
list, blank party states from the GSTIN prefix, and party addresses (town, pincode, state; +516 new, 353 completed) from the dashboard `dim_party.csv`, and 6 ESTIMATED blend BOMs (Achar, Garam, Jeeravan, Chat, Chana, Mix Masala; 100 kg basis, 1.25 % loss) plus 3 estimated raw materials (Black salt, Pudina, Anardana). The recipes are industry estimates, not owner-confirmed. Tally holds no phone / email /
credit limits, so those stay blank.

Because of `TallyRecord`, **nothing in the Tally Postgres extract is lost**:
the ERP database alone holds it all. The import report's "Archive completeness"
table proves it row-for-row per kind. Fields not mapped yet (interest settings,
BRS rows, VAT details, mailing-name history, voucher numbering) can be promoted
later from `TallyRecord.raw` without Tally.

Re-run the proof at any time (needs the `tally-pg` extract, read-only):

```bash
python manage.py verify_tally_archive        # exits non-zero on any shortfall
python manage.py verify_tally_archive --source postgresql://postgres:tally@127.0.0.1:5432/tally
```

## 2. Still needs Tally (never extracted)

The extract has every rupee of every voucher, but **not Tally's complete
voucher record**. The copy of Tally used for the extract runs in Educational
mode, which blocked the full Day Book export (`Desktop\Tally\RECOVERY_PLAN.md`
L1–L4, L11–L13). These exist only inside the Tally company data:

| # | Missing | Impact |
|---|---|---|
| T1 | **Stock Journal lines.** All 54 Stock Journal vouchers came without their in/out lines | Production / transfer quantities missing in those years |
| T2 | **Bank details on receipts / payments**: instrument no. and date, bank, clearance date | No cheque tracing or bank reconciliation history |
| T3 | **Invoice extras**: buyer / consignee name, address and GSTIN on the invoice, dispatch, vehicle no., e-way bill, e-invoice IRN, line-level HSN | History only. e-way bill / e-invoice are not built in the ERP (Tally does statutory output) |
| T4 | **Company master**: own GSTIN, PAN, address | The ERP company has no GSTIN until it is typed in |
| T5 | **Tally's own reports** (Balance Sheet, P&L, Stock Summary per year) | Extra cross-check only. Our reconciliation already uses Tally's ledger closings |
| T6 | Edit log, deleted vouchers, users, attachments | Tally does not export these at all |

Tally stays as the official books (the ERP exports to it, `checklist.md` 6.9).
What goes away is any need for the *old* Tally data, which takes this:

**To stop needing the old Tally data:**
1. Get the sender's export, already drafted in `Desktop\Tally\SENDER_REQUEST.md`:
   Day Book XML + All Masters XML per year from their licensed Tally. Then
   extend the importer to read it (`load_full.py` already parses that format),
   which closes T1–T4.
2. Type the company GSTIN / PAN / address into Company settings (T4) at go-live.
3. Keep `Desktop\Tally\send to kani\` (the original Tally company folders) and
   the `tally` DB dumps (`Desktop\Tally\backups\`) as a read-only cold backup.
   They are the only copy of T1–T3 and T6 until step 1 is done.

## 3. The ERP can't hold it yet (model gaps)

| # | Tally data | Volume | What the ERP needs |
|---|---|---:|---|
| G1 | **Old invoices as real Sales / Purchase Invoices** (not just JE + history lines) | 27,575 vouchers | Invoice outstanding, ageing and payment matching work only for invoices created in the ERP. Bill-wise is on for only 84 ledgers in Tally (125 allocations), so open bills per invoice **cannot be derived**. Party *balances* are exact. Go-live plan: carry party balances (already correct) and allocate new receipts in the ERP |
| G2 | **Negative stock** on 7 items (Achar Masala, Papad, Mix Masala, Jeeravan, Chat Masala, Old Bardan, Rai Dal 500gm) | 7 | Physical count + Stock Reconciliation (ERPNext disallows negative stock by default). Until then these items show 0 and have no FY 2026-27 movements. The movements are in `TallyItemLine` |
| G3 | **Combined GST ledgers** (`C Gst`, `S Gst`, `I Gst`, `Cess Gst` are both input and output) | 6 | GST reports can't split Tally-period tax. New invoices need separate Input/Output GST accounts set up before go-live |
| G4 | Interest on overdue balances | 75 ledgers | Interest / dunning feature (in archive) |
| G5 | Bank reconciliation (BRS) | 5 rows | Bank reconciliation tool (in archive) |
| G6 | Bill-wise allocations | 125 rows | JE line `reference_type/name` (in archive) |
| G7 | Pre-GST VAT details | 25 ledgers, 20 items | Not needed (in archive) |
| G8 | Address city / district | 382 addresses | Tally address lines are free text, so city is left blank rather than guessed |

## 4. In Tally but empty or unused (nothing lost)

Batches (all "Primary Batch"), godowns (one "Main Location"), cost centres
(0 lines use them), credit limits (none set), phone / email (contact slots
empty), sales / purchase orders, payroll, attendance, optional vouchers.

## 5. Data-quality notes

- 7 party GSTINs fail checksum. They are kept, and `verify_integrity` warns.
- 20 ledgers change type between years. One account per ledger, latest year
  wins. Balances match every year, but for ledgers that switch between P&L and
  balance sheet (e.g. `Interest Received From Others`), ERP profit differs from
  Tally's in 2021-22 (-57,300.00), 2023-24 (+5,110.00) and 2024-25 (+6,948.00).
  Reclassify by JE if the accountant agrees.
- Tally's years don't carry forward: closings ≠ next openings (see
  `Desktop\Tally\RECOVERY_PLAN.md`). Each year is opened exactly as Tally
  opens it. The difference goes to `Difference in Opening Balances` (FY 2026-27:
  -41,46,397.68).
- FY 2026-27 is partly entered in Tally (receipts stop 14-Jun, purchases 1-Aug).
- SQLite sums money as floats. The dev SQLite DB shows sub-paisa noise in SQL
  sums. The ERP now runs on Postgres (`docker-compose.yml`, host port 5433).
