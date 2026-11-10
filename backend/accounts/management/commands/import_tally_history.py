"""Import Tally vouchers as historical documents (planning/11-tally-history-import.md).

    python manage.py import_tally_history --source tally_history.jsonl.gz            # dry run
    python manage.py import_tally_history --source tally_history.jsonl.gz --commit

Sales -> Sales Invoice, Purchase -> Purchase Invoice, Credit Note -> Sales Invoice
return, Debit Note -> Purchase Invoice return, Receipt / Payment -> Payment Entry.
Other Tally types stay journals only. Each document is named after, and linked to,
its TLY-* Journal Entry; it posts nothing. Idempotent: a journal that already has
its document is counted as "already" and left alone. Logic in
``accounts.services.tally_history``.
"""

from __future__ import annotations

import csv
import json
import os

from django.core.management.base import BaseCommand, CommandError

from accounts.management.commands.export_tally_history import resolve_company
from accounts.services import tally_history as svc
from stock.models import Item


class Command(BaseCommand):
    help = "Import Tally vouchers from tally_history.jsonl.gz as historical documents."

    def add_arguments(self, parser):
        parser.add_argument("--source", default="tally_history.jsonl.gz")
        parser.add_argument(
            "--company", help="ERP company name (default: the only one)."
        )
        parser.add_argument(
            "--year", help="Only this Tally financial year, e.g. 2024-25."
        )
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run", action="store_true", help="Report only (default)."
        )
        mode.add_argument("--commit", action="store_true", help="Write the documents.")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument(
            "--type-map",
            help='JSON file {"<Tally voucher type>": "sales|purchase|credit note|debit note|'
            'receipt|payment|journal"} added to / overriding the built-in mapping.',
        )
        parser.add_argument(
            "--placeholder-item",
            help="Code of an EXISTING non-stock item used as the single line of vouchers "
            "that have no item lines. Without it such vouchers are skipped.",
        )
        parser.add_argument(
            "--report-dir",
            help="Write skipped.csv and parties.csv (every row) into this folder.",
        )

    def handle(self, *args, **opts):
        if not os.path.exists(opts["source"]):
            raise CommandError(f"Source file not found: {opts['source']}")
        company = resolve_company(opts.get("company"))
        type_map = None
        if opts.get("type_map"):
            with open(opts["type_map"], encoding="utf-8") as fh:
                try:
                    type_map = svc.parse_type_map(json.load(fh))
                except ValueError as exc:
                    raise CommandError(str(exc)) from exc
        placeholder = None
        if opts.get("placeholder_item"):
            placeholder = Item.objects.filter(code=opts["placeholder_item"]).first()
            if placeholder is None:
                raise CommandError(
                    f"Item '{opts['placeholder_item']}' does not exist (it is never created here)."
                )
            if placeholder.is_stock_item:
                raise CommandError(
                    f"Item '{placeholder.code}' is a stock item; use a non-stock item."
                )

        commit = bool(opts["commit"])
        self.stdout.write(
            f"Tally history import: {opts['source']} -> {company.name}"
            f"{' (' + opts['year'] + ')' if opts.get('year') else ''} "
            f"[{'COMMIT' if commit else 'DRY RUN, nothing is saved'}]"
        )
        try:
            result = svc.run_import(
                opts["source"],
                company,
                year=opts.get("year"),
                commit=commit,
                batch_size=opts["batch_size"],
                type_map=type_map,
                placeholder_item=placeholder,
            )
        except svc.BooksChanged as exc:
            raise CommandError(
                f"Stopped: {exc}. Nothing in that batch was saved."
            ) from exc
        self.report(result)
        if opts.get("report_dir"):
            self.write_csv(result, opts["report_dir"])
        if not commit:
            self.stdout.write(
                self.style.WARNING(
                    "Dry run: rolled back. Re-run with --commit to save."
                )
            )

    # ----------------------------------------------------------------- report

    def report(self, r: svc.Result):
        w = self.stdout.write
        w(
            "\nPer Tally voucher type and year (in file / created / already / skipped / journal only):"
        )
        w(
            f"  {'type':24} {'year':8} {'tally':>7} {'created':>8} {'already':>8} {'skipped':>8} {'journal':>8}"
        )
        totals = {
            "tally": 0,
            "created": 0,
            "already": 0,
            "skipped": 0,
            "journal_only": 0,
        }
        for (vt, fy), c in sorted(r.counts.items()):
            w(
                f"  {vt[:24]:24} {fy:8} {c['tally']:7} {c['created']:8} {c['already']:8} "
                f"{c['skipped']:8} {c['journal_only']:8}"
            )
            for k in totals:
                totals[k] += c[k]
        w(
            f"  {'TOTAL':24} {'':8} {totals['tally']:7} {totals['created']:8} {totals['already']:8} "
            f"{totals['skipped']:8} {totals['journal_only']:8}"
        )
        if r.unmapped_types:
            w(
                "\nTally types kept as journals only (map with --type-map if they are invoices / payments):"
            )
            for vt, n in r.unmapped_types.most_common():
                w(f"  {n:7}  {vt}")
        if r.reasons:
            w("\nSkipped, by reason (the voucher stays a journal only):")
            for reason, n in r.reasons.most_common():
                w(f"  {n:7}  {reason}")
                for ex in r.examples[reason]:
                    w(f"             e.g. {ex}")
        if r.unmatched_items:
            w(
                "\nTally stock items with no ERP item (add them to the Tally item map, never invented here):"
            )
            for name, n in r.unmatched_items.most_common():
                w(f"  {n:7} vouchers  {name}")
        for note, n in r.notes.most_common():
            w(f"\nNote: {n} x {note}")
        w(f"\nDisplay-only payment allocations created: {r.references}")
        if r.party_rows:
            w(
                f"\nPer party as of {r.last_date}: historical documents (invoices - receipts/payments) vs ledger "
                "balance. The difference is opening balances and journal-only vouchers."
            )
            rows = sorted(r.party_rows, key=lambda x: -abs(x["difference"]))
            for row in rows[:25]:
                w(
                    f"  {row['party_type'][:1]} {row['party'][:40]:40} docs {row['documents_net']:>14} "
                    f"ledger {row['ledger_balance']:>14} diff {row['difference']:>14}"
                )
            if len(rows) > 25:
                w(f"  ... {len(rows) - 25} more parties (see --report-dir parties.csv)")
        w("\nBooks: GL, stock ledger and Bin unchanged (checked after every batch).")

    def write_csv(self, r: svc.Result, folder: str):
        os.makedirs(folder, exist_ok=True)
        with open(
            os.path.join(folder, "skipped.csv"), "w", newline="", encoding="utf-8"
        ) as fh:
            out = csv.DictWriter(
                fh,
                fieldnames=[
                    "fy",
                    "type",
                    "number",
                    "date",
                    "journal",
                    "reason",
                    "detail",
                ],
            )
            out.writeheader()
            out.writerows(r.skipped_rows)
        with open(
            os.path.join(folder, "parties.csv"), "w", newline="", encoding="utf-8"
        ) as fh:
            fields = [
                "party_type",
                "party",
                "as_of",
                "documents_net",
                "ledger_balance",
                "difference",
            ]
            out = csv.DictWriter(fh, fieldnames=fields)
            out.writeheader()
            out.writerows(r.party_rows)
        self.stdout.write(f"Wrote {folder}/skipped.csv and {folder}/parties.csv")
