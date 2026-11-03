# Tally history import: owner steps and the agent prompt

Goal: the ten Tally years appear in the ERP as real documents (sales invoices with
items, purchase invoices, receipts and payments, credit and debit notes, what each
party still owes) without changing the books, which already hold every rupee as
`TLY-*` Journal Entries reconciled to Tally to ₹0.00.

The ERP side is ready (branch `claude/cool-cerf-roneeb`, 2026-11-03):
`is_historical` + `source_journal` on Sales Invoice, Purchase Invoice and Payment
Entry, status `Historical`, DB CHECKs, no posting, no cancel, excluded from
GSTR-1/3B and the Tally export. What is missing is the importer, and it needs the
Tally data that only exists on the owner's computer.

## Owner steps

1. **Get the latest code** on the computer that has the Tally data:
   ```bash
   cd ERP
   git fetch origin
   git checkout claude/cool-cerf-roneeb
   git pull
   ```
   (or merge that branch into `main` first and use `main`).
2. **Make the Tally data reachable**, one of:
   - the `tally-pg` Postgres extract running (project `Desktop\Tally`), or
   - the dump `backend/backups/legacy_tally_archive_2026-10-30.sql.gz`.
3. **Get a copy of the production database** to test on, never the live one:
   on the VM run `./scripts/backup_erp.sh`, copy the newest
   `~/ERP/backups/erp_db_*.dump` to this computer, and restore it into a local
   Postgres 16 database.
4. **Start Claude Code** in the `ERP` folder (`claude` in a terminal, or the Claude
   desktop app on this folder) and paste the prompt below. Tell it where the Tally
   data and the restored database are.
5. **Read the dry-run report** it gives you (counts per voucher type, totals per
   party against the ledger). Only when you are happy, let it run with `--commit`
   on the local copy, look at the result in the app, then let it commit and push.
6. **Production**: on the VM, `./scripts/backup_erp.sh`, deploy the new image as in
   `docs/DEPLOYMENT_SHARED_VM.md`, copy the export file to the VM, run the import
   with `--dry-run`, then `--commit`, then `verify_integrity`.
7. **Bank statements** (optional, any time): download one CSV per year from the
   bank, import them under Bank → Import, then Bank → Reconcile → *Accept all
   suggestions*.

## The prompt

Paste everything between the lines into Claude Code.

---

You are working in this ERP repository (Django + DRF + React). Read `CLAUDE.md`,
`backend/docs/tally_data_gaps.md`, `backend/docs/erpnext_divergences.md` (section
"Historical documents from Tally") and `planning/11-tally-history-import.md` first.
Work on branch `claude/cool-cerf-roneeb` (or `main` if it has been merged).

**Goal.** Import every Tally sales, purchase, receipt, payment, credit-note and
debit-note voucher of the ten Tally years (2017-18 to 2026-27) into the ERP as
**historical** documents, so the owner sees invoice history, procurement history,
receipts and payments per customer and supplier, with items.

**The books must not change.** Every Tally voucher is already a `TLY-*` Journal
Entry (Tally voucher type and number in `user_remark`; see
`backend/accounts/reports/tally.py`). Historical documents post nothing: no
GLEntry, no StockLedgerEntry, no Bin change. `verify_integrity` must stay green and
the trial balance must be identical before and after.

**Already built (use it, don't rebuild it).** `SalesInvoice`, `PurchaseInvoice`
and `PaymentEntry` have `is_historical` and `source_journal` (one-to-one to the TLY
Journal Entry); invoice status `Historical`; CHECKs require a historical row to be
submitted, to name its journal and (invoices) to have outstanding 0. Cancel is
refused. GST returns and the Tally export skip them.

**Data source.** The item-wise lines exist only outside the ERP: the `tally-pg`
Postgres extract (I will give you the connection) and/or the dump
`backend/backups/legacy_tally_archive_2026-10-30.sql.gz`, which holds the removed
`legacy_tallyrecord` (every voucher and master, `raw` JSON) and
`legacy_tallyitemline` (item, qty, rate, amount, party, ledger per line) tables.
Inspect the real schema first and show me what you find before writing the importer.

**Build, in this order, showing me each step:**

1. `export_tally_history`: a small script or management command that reads the
   source and writes one portable file (`tally_history.jsonl.gz`): per voucher its
   Tally type, number, date, party, the TLY journal name it became, header amounts,
   and its item lines. This file is what gets copied to the server, so the import
   never needs `tally-pg` there.
2. `import_tally_history --source tally_history.jsonl.gz [--company] [--year]
   [--dry-run (default) | --commit]`, idempotent (re-running creates nothing new;
   key = `source_journal`). Mapping:
   - Sales -> Sales Invoice; Purchase -> Purchase Invoice (supplier bill no. if
     Tally has it); Credit Note -> Sales Invoice `is_return`; Debit Note -> Purchase
     Invoice `is_return`; Receipt -> Payment Entry (Receive); Payment -> Payment
     Entry (Pay). Journal / Contra / Stock Journal / others stay journals only.
   - Name each document after its TLY journal (e.g. `TLY-2425-000123`), so
     `TLY-` keeps it out of the Tally export.
   - Items: match Tally stock items to ERP `Item` through the existing Tally item
     map (`tally_export` maps / item names); report unmatched names, never invent
     items. Lines without a stock item become a single non-stock line only if an
     ERP "Tally item" placeholder item already exists; otherwise ask me.
   - Amounts: Decimal at model precision. The invoice's grand total must equal the
     party line of its journal to the paisa; taxes from the journal's GST lines
     (Tally used combined GST ledgers, see the gaps doc). Any voucher that does not
     tie is listed and skipped, not forced.
   - Create the documents directly as submitted historical rows inside
     `transaction.atomic()` per batch, without calling `submit()` (which would
     post). Bypass proof requirements only for `TLY-` names, as the models already
     do.
   - Receipts and payments: add `PaymentEntryReference` rows that settle the
     party's historical invoices oldest-first, for display only; they must not
     change opening bills or any outstanding (check
     `accounts/services/opening.py` and `accounts/services/outstanding.py`).
   - What is still owed stays on the opening bills (G-1/G-2), which reference the
     same Tally bill numbers. Do not create a second source of outstanding.
3. A dry-run report: counts per voucher type and year (Tally vs created vs
   skipped, with reasons), and per party the sum of historical sales minus
   receipts compared with the party's ledger balance on the same date.
4. Tests (`backend/accounts/test_tally_history_import.py`) with a small synthetic
   source file: each voucher type, idempotent re-run, a voucher that does not tie
   is skipped, GL / SLE / Bin counts unchanged, `verify_integrity` green.
5. Screens: the existing invoice and payment lists already show historical rows;
   add a "Historical" badge and an `is_historical` filter where it helps, and on
   the customer / supplier pages make sure their history lists include them. All
   text through i18n in all 13 locales (`npm run i18n:check`).
6. Docs: `backend/docs/API.md` if any route or serializer changes,
   `backend/docs/tally_data_gaps.md` (G1 now covered), `checklist.md`.

**Run** against a local Postgres restored from the production backup I give you,
never against production. Before saying it is done: `pytest`, `makemigrations
--check`, `verify_integrity`, `npm run lint`, `npm run build`, `npm run i18n:check`,
and the dry-run report. Stop and ask me whenever the data is ambiguous; never guess
amounts, parties or items. Commit in small steps with clear messages and push to
the branch.

---
