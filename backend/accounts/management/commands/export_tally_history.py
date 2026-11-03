"""Export historical Tally vouchers and item lines into a portable JSONL.GZ file.

Reads from the external tally-pg Postgres extract and maps each voucher to its
corresponding ERP TLY-* JournalEntry by GUID. The resulting file
(tally_history.jsonl.gz) can be safely copied to production servers and loaded
without needing the external tally-pg database there.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from accounts.models import JournalEntry

try:
    import psycopg
except ImportError:
    psycopg = None  # type: ignore


VCH_TYPE_MAP = {
    "Sales Gst": ("SalesInvoice", False, "Sales"),
    "TAX INVOICE": ("SalesInvoice", False, "Sales"),
    "Sales": ("SalesInvoice", False, "Sales"),
    "Tax Free Sales": ("SalesInvoice", False, "Sales"),
    "Credit Note": ("SalesInvoice", True, "Credit Note"),
    "Purchase Gst": ("PurchaseInvoice", False, "Purchase"),
    "Purchase": ("PurchaseInvoice", False, "Purchase"),
    "Debit Note": ("PurchaseInvoice", True, "Debit Note"),
    "Receipt": ("PaymentEntry", False, "Receipt"),
    "Payment": ("PaymentEntry", False, "Payment"),
}


class Command(BaseCommand):
    help = "Export historical Tally vouchers and item lines to tally_history.jsonl.gz."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tally-url",
            default=os.environ.get(
                "TALLY_DATABASE_URL", "postgresql://postgres:tally@127.0.0.1:5432/tally"
            ),
            help="Postgres connection URL for tally-pg database",
        )
        parser.add_argument(
            "--output",
            default="tally_history.jsonl.gz",
            help="Path to output .jsonl.gz archive (default: tally_history.jsonl.gz)",
        )
        parser.add_argument(
            "--year",
            help="Filter export to a specific financial year (e.g. 2024-25)",
        )

    def handle(self, *args, **options):
        if psycopg is None:
            raise CommandError("psycopg package is required to connect to tally-pg.")

        tally_url = options["tally_url"]
        output_path = options["output"]
        fy_filter = options["year"]

        self.stdout.write(f"Connecting to Tally extract: {tally_url}")
        try:
            tally_conn = psycopg.connect(tally_url)
        except Exception as e:
            raise CommandError(f"Could not connect to tally-pg: {e}")

        # 1. Build GUID -> JournalEntry mapping from ERP database
        self.stdout.write("Indexing ERP TLY-* Journal Entries by GUID...")
        guid_to_je: dict[str, dict[str, Any]] = {}
        je_qs = JournalEntry.objects.filter(name__startswith="TLY-").values_list(
            "id", "name", "user_remark", "posting_date", "total_debit"
        )
        for pk, name, remark, posting_date, total_debit in je_qs:
            if not remark:
                continue
            m = re.search(r"guid\s+([^\s\|]+)", remark)
            if m:
                guid = m.group(1).strip()
                guid_to_je[guid] = {
                    "id": pk,
                    "name": name,
                    "posting_date": str(posting_date),
                    "total_debit": str(total_debit),
                }

        self.stdout.write(f"Indexed {len(guid_to_je)} ERP TLY-* Journal Entries.")

        # 2. Query Tally vouchers
        cur_t = tally_conn.cursor()
        vch_sql = """
            SELECT 
                v.row_key, v.guid, v.company_year, c.fy, v.vchtype, 
                v.vouchernumber, v.reference, v.date, v.partyledgername, 
                v.narration, v.amount
            FROM txn_voucher v
            JOIN company c USING (company_year)
            WHERE NOT v.iscancelled
        """
        params: list[Any] = []
        if fy_filter:
            vch_sql += " AND c.fy = %s"
            params.append(fy_filter)
        vch_sql += " ORDER BY v.date, v.row_key"

        self.stdout.write("Fetching Tally vouchers...")
        cur_t.execute(vch_sql, params)
        vouchers = cur_t.fetchall()
        self.stdout.write(f"Found {len(vouchers)} non-cancelled Tally vouchers.")

        # 3. Query all inventory lines grouped by voucher_key
        self.stdout.write("Fetching inventory line items...")
        inv_sql = """
            SELECT 
                voucher_key, stockitemname, actualqty_num, billedqty_num, 
                rate_num, rate_unit, amount
            FROM txn_voucher_inventory_entry
            ORDER BY row_key
        """
        cur_t.execute(inv_sql)
        items_by_vch: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for (
            v_key,
            item_name,
            act_qty,
            bill_qty,
            rate,
            unit,
            amt,
        ) in cur_t.fetchall():
            items_by_vch[v_key].append(
                {
                    "stockitemname": item_name.strip() if item_name else "",
                    "actualqty": str(act_qty) if act_qty is not None else "0.000",
                    "billedqty": str(bill_qty) if bill_qty is not None else "0.000",
                    "rate": str(rate) if rate is not None else "0.00",
                    "rate_unit": unit.strip() if unit else "",
                    "amount": str(amt) if amt is not None else "0.00",
                }
            )

        # 4. Query all ledger lines grouped by voucher_key
        self.stdout.write("Fetching ledger entries...")
        led_sql = """
            SELECT voucher_key, ledgername, amount, isdeemedpositive
            FROM txn_voucher_ledger_entry
            ORDER BY row_key
        """
        cur_t.execute(led_sql)
        ledgers_by_vch: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for v_key, lname, lamt, ispos in cur_t.fetchall():
            ledgers_by_vch[v_key].append(
                {
                    "ledgername": lname.strip() if lname else "",
                    "amount": str(lamt) if lamt is not None else "0.00",
                    "isdeemedpositive": bool(ispos),
                }
            )

        # 5. Write to compressed JSONL
        self.stdout.write(f"Writing to {output_path}...")
        stats_by_type: dict[str, int] = defaultdict(int)
        stats_by_fy: dict[str, int] = defaultdict(int)
        matched_journals = 0
        unmatched_journals = 0

        with gzip.open(output_path, "wt", encoding="utf-8") as gz_out:
            for vch in vouchers:
                (
                    row_key,
                    guid,
                    company_year,
                    fy,
                    vchtype,
                    vnum,
                    ref,
                    vdate,
                    party,
                    narration,
                    v_amt,
                ) = vch

                clean_guid = guid.strip() if guid else ""
                je_info = guid_to_je.get(clean_guid)
                if je_info:
                    matched_journals += 1
                    journal_name = je_info["name"]
                else:
                    unmatched_journals += 1
                    journal_name = None

                map_info = VCH_TYPE_MAP.get(vchtype)
                target_doctype = map_info[0] if map_info else None
                is_return = map_info[1] if map_info else False
                category = map_info[2] if map_info else "Other"

                v_items = items_by_vch.get(row_key, [])
                v_ledgers = ledgers_by_vch.get(row_key, [])

                # Find party line amount from ledger entries
                party_amount = None
                clean_party = party.strip() if party else ""
                for le in v_ledgers:
                    if le["ledgername"] == clean_party:
                        party_amount = str(abs(Decimal(le["amount"])))
                        break

                record = {
                    "guid": clean_guid,
                    "voucher_key": row_key,
                    "company_year": company_year,
                    "fy": fy,
                    "vchtype": vchtype,
                    "category": category,
                    "target_doctype": target_doctype,
                    "is_return": is_return,
                    "vouchernumber": vnum.strip() if vnum else "",
                    "reference": ref.strip() if ref else "",
                    "date": str(vdate),
                    "party": clean_party,
                    "journal_name": journal_name,
                    "narration": narration.strip() if narration else "",
                    "header_amount": str(v_amt) if v_amt is not None else "0.00",
                    "party_amount": party_amount,
                    "inventory_entries": v_items,
                    "ledger_entries": v_ledgers,
                }

                gz_out.write(json.dumps(record, ensure_ascii=False) + "\n")
                stats_by_type[vchtype] += 1
                stats_by_fy[fy] += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSuccessfully exported {len(vouchers)} vouchers to {output_path}!\n"
                f"  Matched to ERP JournalEntry: {matched_journals}\n"
                f"  Unmatched: {unmatched_journals}\n"
            )
        )
        self.stdout.write("Vouchers by type:")
        for vt, cnt in sorted(stats_by_type.items(), key=lambda x: -x[1]):
            self.stdout.write(f"  {vt}: {cnt}")

        self.stdout.write("\nVouchers by FY:")
        for y, cnt in sorted(stats_by_fy.items()):
            self.stdout.write(f"  {y}: {cnt}")
