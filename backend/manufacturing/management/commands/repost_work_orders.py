"""Rebuild the derived Work Order progress columns from the stock ledger.

Safe to run repeatedly: it only rewrites ``produced_qty``, ``process_loss_qty``,
``status`` and the per-row transferred/consumed quantities.  No ledger or GL row
is touched (design §7).
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import DocStatus
from manufacturing.models import WorkOrder
from manufacturing.services.work_orders import refresh_progress


class Command(BaseCommand):
    help = "Recompute derived Work Order progress from submitted Stock Entries."

    def add_arguments(self, parser):
        parser.add_argument("--work-order", dest="work_order", default=None)
        parser.add_argument("--company", dest="company", default=None)

    def handle(self, *args, **options):
        qs = WorkOrder.objects.exclude(docstatus=DocStatus.DRAFT).order_by("pk")
        if options["work_order"]:
            qs = qs.filter(name=options["work_order"])
        if options["company"]:
            qs = qs.filter(company__abbr=options["company"])
        count = 0
        with transaction.atomic():
            for wo in qs:
                refresh_progress(wo)
                count += 1
        self.stdout.write(
            self.style.SUCCESS(f"Recomputed progress for {count} Work Order(s).")
        )
