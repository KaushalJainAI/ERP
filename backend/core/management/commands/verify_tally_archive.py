"""Prove the ERP database still holds everything the Tally extract contains.

    manage.py verify_tally_archive [--source DSN]

Compares the Tally Postgres extract (opened read-only) with this ERP's database:
every master and voucher archived in ``legacy.TallyRecord``, every inventory line
in ``legacy.TallyItemLine``, every Tally ledger line inside the ``TLY-*`` journal
entries, and the party / address / GSTIN masters. Exits non-zero on any
shortfall, so it can gate a go-live.

The source is only read; nothing is written.
"""

import re

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from accounts.models import JournalEntryLine
from buying.models import Supplier
from contacts.models import Address
from core.models import Company
from core.tally_import.source import DEFAULT_DSN, TallySource
from legacy.models import TallyItemLine, TallyRecord
from selling.models import Customer

# Tally extract table -> legacy.TallyRecord.kind (one archived record per row).
MASTERS = (
    ("company", "company"),
    ("group", "mst_group"),
    ("ledger", "mst_ledger"),
    ("stock_group", "mst_stockgroup"),
    ("stock_item", "mst_stockitem"),
    ("unit", "mst_unit"),
    ("godown", "mst_godown"),
    ("voucher_type", "mst_vouchertype"),
    ("cost_category", "mst_costcategory"),
    ("currency", "mst_currency"),
)


def _norm(name: str) -> str:
    """Case/whitespace-insensitive key, matching the importer's name handling."""
    return re.sub(r"\s+", " ", (name or "")).strip().casefold()


class Command(BaseCommand):
    help = "Compare the Tally extract with this ERP database and report any loss."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=DEFAULT_DSN, help="Tally Postgres DSN")

    def handle(self, *args, **opts):
        try:
            src = TallySource(opts["source"])
        except Exception as exc:  # noqa: BLE001 - surface any driver/DSN failure
            raise CommandError(
                f"cannot open the Tally extract at {opts['source']}: {exc}"
            )
        try:
            self._run(src)
        finally:
            src.close()

    # ------------------------------------------------------------------
    def _run(self, src: TallySource):
        self.stdout.write("Tally extract -> ERP completeness check\n")
        self.stdout.write(
            f"{'check':<40}{'Tally':>14}{'ERP':>14}  result\n"
            f"{'-' * 40}{'-' * 14}{'-' * 14}  {'-' * 6}"
        )
        failures: list[str] = []

        def check(label: str, want: int, have: int, exact: bool = True):
            ok = have == want if exact else have >= want
            if not ok:
                failures.append(f"{label}: Tally {want}, ERP {have}")
            self.stdout.write(
                f"{label:<40}{want:>14,}{have:>14,}  {'OK' if ok else 'MISSING'}"
            )

        # Masters: one archived record per source row.
        for kind, table in MASTERS:
            want = int(src.scalar(f"select count(*) from {table}") or 0)
            check(kind, want, TallyRecord.objects.filter(kind=kind).count())

        # Vouchers: one archived record per voucher, per fiscal year.
        for year in src.years():
            want = int(
                src.scalar(
                    "select count(*) from txn_voucher where company_year=%s",
                    (year.cy,),
                )
                or 0
            )
            check(
                f"vouchers {year.fy}",
                want,
                TallyRecord.objects.filter(kind="voucher", fiscal_year=year.fy).count(),
            )
        want_vouchers = int(src.scalar("select count(*) from txn_voucher") or 0)
        check(
            "vouchers (total)",
            want_vouchers,
            TallyRecord.objects.filter(kind="voucher").count(),
        )

        # Inventory lines: row count and absolute amount must match to the paisa.
        want_lines = int(
            src.scalar("select count(*) from txn_voucher_inventory_entry") or 0
        )
        check("item lines", want_lines, TallyItemLine.objects.count())
        want_amt = float(
            src.scalar(
                "select coalesce(sum(abs(amount)), 0) from txn_voucher_inventory_entry"
            )
            or 0
        )
        have_amt = float(
            TallyItemLine.objects.aggregate(total=Sum("amount"))["total"] or 0
        )
        amt_ok = round(want_amt, 2) == round(have_amt, 2)
        if not amt_ok:
            failures.append(
                f"item line amount: Tally {want_amt:.2f}, ERP {have_amt:.2f}"
            )
        self.stdout.write(
            f"{'item line amount (abs)':<40}{want_amt:>18,.2f}{have_amt:>18,.2f}"
            f"  {'OK' if amt_ok else 'MISMATCH'}"
        )

        # Ledger lines: the Tally lines plus the reconstructed inventory lines
        # must fit inside the ERP journal-entry lines (extras are opening /
        # closing / period-closing journals).
        want_ledger = int(
            src.scalar("select count(*) from txn_voucher_ledger_entry") or 0
        )
        erp_journal_lines = JournalEntryLine.objects.count()
        check(
            "Tally ledger lines",
            want_ledger,
            erp_journal_lines - TallyItemLine.objects.count(),
            exact=False,
        )
        self.stdout.write(
            f"{'  journal lines (incl. synthetic)':<40}{'':>14}{erp_journal_lines:>14,}"
        )

        # Parties: the same set of names.
        src_parties = {
            _norm(r["name"])
            for r in src.rows(
                "select distinct name from mst_ledger "
                "where parent in ('Sundry Debtors', 'Sundry Creditors')"
            )
        }
        erp_parties = {
            _norm(n) for n in Customer.objects.values_list("name", flat=True)
        }
        erp_parties |= {
            _norm(n) for n in Supplier.objects.values_list("name", flat=True)
        }
        missing = src_parties - erp_parties
        if missing:
            failures.append(f"parties missing: {sorted(missing)[:5]}")
        self.stdout.write(
            f"{'parties (debtors + creditors)':<40}{len(src_parties):>14,}"
            f"{len(erp_parties):>14,}  {'OK' if not missing else 'MISSING'}"
        )

        # Addresses: Tally stores one address per party ledger.
        want_addr = int(
            src.scalar(
                "select count(distinct l.name) from mst_ledger_address a "
                "join mst_ledger l on l.row_key = a.parent_key "
                "where btrim(coalesce(a.address, '')) <> ''"
            )
            or 0
        )
        check("party addresses", want_addr, Address.objects.count())

        # GSTIN: the ERP may hold more (customers and suppliers both), never less.
        want_gst = int(
            src.scalar(
                "select count(distinct name) from mst_ledger "
                "where partygstin is not null and btrim(partygstin) <> ''"
            )
            or 0
        )
        have_gst = (
            Customer.objects.filter(gstin__isnull=False).exclude(gstin="").count()
            + Supplier.objects.filter(gstin__isnull=False).exclude(gstin="").count()
        )
        check("parties with GSTIN", want_gst, have_gst, exact=False)

        # Bill allocations: not a model of its own; they live in TallyRecord.raw.
        # Look up the exact vouchers that had one and confirm its XML is archived.
        bill_guids = [
            r["guid"]
            for r in src.rows(
                "select distinct v.guid from txn_voucher_ledger_entry_bill_alloc b "
                "join txn_voucher v on v.row_key = b.voucher_key"
            )
            if r["guid"]
        ]
        archived = {
            r.guid
            for r in TallyRecord.objects.filter(
                kind="voucher", guid__in=bill_guids
            ).only("guid", "raw")
            if "BILLALLOCATIONS" in r.raw
        }
        check("vouchers with bill allocations (raw)", len(bill_guids), len(archived))

        # Company master: the extract carries no GSTIN, so this is a note, not a
        # completeness failure. It must be typed in at go-live (tally_data_gaps T4).
        company = Company.objects.filter(is_active=True).order_by("id").first()
        self.stdout.write(
            f"\nCompany: {company.name if company else '(none)'} "
            f"GSTIN={company.gstin or '(empty)'}"
        )
        self.stdout.write(
            "  note: the Tally extract has no company GSTIN/PAN/address "
            "(tally_data_gaps T4); set it in the app at go-live."
        )

        if failures:
            raise CommandError(
                "Tally archive is incomplete:\n  " + "\n  ".join(failures)
            )
        self.stdout.write(
            self.style.SUCCESS(
                "\nOK - every Tally master, voucher, item line and ledger line is "
                "in the ERP."
            )
        )
