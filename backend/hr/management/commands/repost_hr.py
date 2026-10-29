"""Rebuild derived HR figures.

* Draft salary slips: Wages / Overtime rows, day / hour counts and totals are
  recomputed from attendance and wage rates (manual rows are kept).
* Submitted slips are immutable facts: their stored totals are only *checked*
  against their rows; drift is reported, never rewritten (corrections are
  cancel + amend).

Advance outstanding and the wages-payable balance are derived on read, so there
is nothing else to rebuild.  No ledger row is touched.
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import DocStatus
from hr.models import SalarySlip
from hr.services import integrity, payroll


class Command(BaseCommand):
    help = "Recompute derived draft salary slip rows / totals and report drift on posted slips."

    def add_arguments(self, parser):
        parser.add_argument("--employee", dest="employee", default=None)
        parser.add_argument("--company", dest="company", default=None)

    def handle(self, *args, **options):
        qs = SalarySlip.objects.all().order_by("pk")
        if options["employee"]:
            qs = qs.filter(employee__name=options["employee"])
        if options["company"]:
            qs = qs.filter(company__abbr=options["company"])

        rebuilt = drift = failed = 0
        for slip in qs:
            if slip.docstatus == DocStatus.DRAFT:
                try:
                    with transaction.atomic():
                        payroll.refresh_draft_slip(slip)
                    rebuilt += 1
                except ValidationError as exc:
                    failed += 1
                    self.stderr.write(
                        self.style.WARNING(f"{slip.name}: {'; '.join(exc.messages)}")
                    )
            elif slip.docstatus == DocStatus.SUBMITTED:
                gross, deduction = integrity.slip_totals(slip)
                if (slip.gross_pay, slip.total_deduction) != (gross, deduction):
                    drift += 1
                    self.stderr.write(
                        self.style.ERROR(
                            f"{slip.name}: stored totals differ from its rows "
                            f"(posted slips are not rewritten; cancel and amend)."
                        )
                    )
        self.stdout.write(
            self.style.SUCCESS(
                f"Rebuilt {rebuilt} draft slip(s); {drift} posted slip(s) with "
                f"drift; {failed} draft(s) could not be computed."
            )
        )
