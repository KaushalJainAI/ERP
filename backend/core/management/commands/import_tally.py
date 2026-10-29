"""Import TallyPrime books (NIDHI GRAH UDYOG, 10 years) into this ERP database.

    manage.py import_tally [--source DSN] [--years 2017-18,...]
                           [--close-through 2024-25] [--reconcile-only]

Thin wrapper; the work lives in ``core.tally_import``. The Tally database is
opened read-only. Idempotency is per year: a re-import means recreating the DB.
"""

import io
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from core.tally_import import archive, masters, reconcile, stock, vouchers
from core.tally_import.source import DEFAULT_DSN, TallySource


class Command(BaseCommand):
    help = "Import TallyPrime books into this ERP (see core/tally_import)."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=DEFAULT_DSN, help="Tally Postgres DSN")
        parser.add_argument(
            "--years", default="", help="comma list of FYs, e.g. 2017-18,2018-19"
        )
        parser.add_argument(
            "--close-through",
            default="2024-25",
            help="close fiscal years up to and including this FY",
        )
        parser.add_argument("--reconcile-only", action="store_true")
        parser.add_argument(
            "--skip-verify",
            action="store_true",
            help="do not run verify_integrity for the report",
        )
        parser.add_argument("--report", default=reconcile.REPORT_PATH)

    def handle(self, *args, **opts):
        t0 = time.time()
        log = lambda m: self.stdout.write(m)  # noqa: E731
        src = TallySource(opts["source"])
        try:
            all_years = src.years()
            known = {y.fy for y in all_years}
            requested = [y for y in all_years]
            if opts["years"]:
                want = [s.strip() for s in opts["years"].split(",") if s.strip()]
                bad = [w for w in want if w not in known]
                if bad:
                    raise CommandError(f"Unknown year(s) {bad}; known: {sorted(known)}")
                requested = [y for y in all_years if y.fy in want]

            company = masters.setup_company()
            if not opts["reconcile_only"]:
                self._check_idempotent(requested)
                masters.setup_fiscal_years([(y.cy, y.fy) for y in all_years])

            log("Building chart of accounts and parties ...")
            chart = masters.build_chart(src, all_years, company, log=log)
            log("Building stock masters ...")
            sm = stock.build_stock_masters(src, all_years, company, log=log)

            if not opts["reconcile_only"]:
                self._archive_masters(src, chart, sm, all_years, log)
                self._import(
                    src, chart, sm, all_years, requested, opts["close_through"], log
                )

            self._report(
                src, chart, sm, all_years, requested, opts, time.time() - t0, log
            )
        finally:
            src.close()

    # ------------------------------------------------------------------
    def _check_idempotent(self, requested):
        from accounts.models import JournalEntry
        from stock.models import StockEntry

        for y in requested:
            code = masters.year_code(y.fy)
            if JournalEntry.objects.filter(name__startswith=f"TLY-{code}-").exists():
                raise CommandError(
                    f"Target already contains TLY-{code}-* journal entries (FY {y.fy}). "
                    "Re-import means recreating the database."
                )
            if StockEntry.objects.filter(name__startswith=f"TLY-{code}-").exists():
                raise CommandError(
                    f"Target already contains TLY-{code}-* stock entries."
                )

    def _archive_masters(self, src, chart, sm, all_years, log):
        from core.models import SystemHealthCheck

        log("Archiving Tally masters and importing addresses ...")
        with transaction.atomic():
            counts = archive.archive_masters(src, chart, sm, all_years, log)
            addr = archive.import_addresses(src, chart, log)
            SystemHealthCheck.objects.create(
                check_name="tally_import:archive:masters",
                status="ok",
                details={"records": counts, "addresses": addr},
            )

    def _import(self, src, chart, sm, all_years, requested, close_through, log):
        from core.models import FiscalYear, SystemHealthCheck

        bal = vouchers.Balances.from_gl()
        progress = vouchers.Progress(log)
        last_fy = all_years[-1].fy
        for y in requested:
            t = time.time()
            completed = y.fy != last_fy
            log(f"FY {y.fy} ({y.cy}) ...")
            with transaction.atomic():
                snap = bal.snapshot()
                je_links, stock_links = {}, {}
                try:
                    st = vouchers.import_year(
                        src, chart, y, bal, progress, completed, log, links=je_links
                    )
                    if y.fy == stock.STOCK_FY:
                        sst = stock.import_stock_year(
                            src, y, sm, chart.company, log, links=stock_links
                        )
                        SystemHealthCheck.objects.create(
                            check_name=f"tally_import:stock:{y.fy}",
                            status="ok",
                            details=sst,
                        )
                    ast = archive.archive_year(
                        src, chart, sm, y, je_links, stock_links, log
                    )
                    SystemHealthCheck.objects.create(
                        check_name=f"tally_import:archive:{y.fy}",
                        status="ok",
                        details=ast,
                    )
                except Exception:
                    bal.restore(snap)
                    raise
            log(
                f"  FY {y.fy}: {st['journals']} JEs, skipped {st['skipped_unbalanced']} unbalanced / "
                f"{st['skipped_error']} errors, {st['no_lines']} without lines, "
                f"{st['cancelled']} cancelled, {time.time() - t:.0f}s"
            )
        cutoff = close_through
        n = 0
        for y in requested:
            if y.fy <= cutoff:
                n += FiscalYear.objects.filter(
                    name=masters.fiscal_year_name(y.fy)
                ).update(is_closed=True)
        log(f"Closed {n} fiscal year(s) up to {cutoff}.")

    def _report(self, src, chart, sm, all_years, requested, opts, elapsed, log):
        from accounts.models import Account, GLEntry, JournalEntry
        from buying.models import Supplier
        from core.models import SystemHealthCheck
        from selling.models import Customer
        from stock.models import UOM, Bin, Item, StockEntry, StockLedgerEntry

        health = {}
        for y in all_years:
            h = (
                SystemHealthCheck.objects.filter(check_name=f"tally_import:{y.fy}")
                .order_by("-id")
                .first()
            )
            if h:
                health[y.fy] = h.details
        sh = (
            SystemHealthCheck.objects.filter(
                check_name=f"tally_import:stock:{stock.STOCK_FY}"
            )
            .order_by("-id")
            .first()
        )
        stock_stats = sh.details if sh else None

        verify = ""
        if not opts["skip_verify"]:
            buf = io.StringIO()
            try:
                call_command("verify_integrity", stdout=buf, stderr=buf)
            except CommandError as e:
                buf.write(f"\nFAILED: {e}\n")
            verify = buf.getvalue()
            log(verify)

        counts = {
            "accounts (all)": Account.objects.filter(company=chart.company).count(),
            "  of which group accounts": Account.objects.filter(
                company=chart.company, is_group=True
            ).count(),
            "  of which leaf accounts": Account.objects.filter(
                company=chart.company, is_group=False
            ).count(),
            "customers": Customer.objects.filter(company=chart.company).count(),
            "suppliers": Supplier.objects.filter(company=chart.company).count(),
            "items": Item.objects.count(),
            "UOMs": UOM.objects.count(),
            "UOM mapping (Tally -> ERP)": ", ".join(
                f"{k or '(blank)'} -> {v}" for k, v in sorted(sm.uom_map.items())
            ),
            "journal entries (TLY-*)": JournalEntry.objects.filter(
                name__startswith="TLY-"
            ).count(),
            "GL entries": GLEntry.objects.filter(company=chart.company).count(),
            "stock entries (TLY-*)": StockEntry.objects.filter(
                name__startswith="TLY-"
            ).count(),
            "stock ledger entries": StockLedgerEntry.objects.count(),
            "bins": Bin.objects.count(),
        }
        from legacy.models import TallyItemLine, TallyRecord

        archived = dict(
            TallyRecord.objects.filter(company=chart.company)
            .values_list("kind")
            .annotate(n=Count("id"))
        )
        archived["item_line"] = TallyItemLine.objects.filter(
            company=chart.company
        ).count()
        archive_counts = {
            k: (n, archived.get(k, 0)) for k, n in src.archive_source_counts().items()
        }
        am = (
            SystemHealthCheck.objects.filter(check_name="tally_import:archive:masters")
            .order_by("-id")
            .first()
        )
        runtime = f"{elapsed / 60:.1f} min (this invocation)"
        text = reconcile.build_report(
            src,
            chart,
            [y for y in all_years if y.fy in health or y in requested],
            health,
            stock_stats,
            verify,
            chart.notes,
            counts,
            runtime,
            archive_counts,
            sm.notes,
            addresses=am.details.get("addresses") if am else None,
        )
        with open(opts["report"], "w", encoding="utf-8") as f:
            f.write(text)
        log(f"Report written to {opts['report']}")
