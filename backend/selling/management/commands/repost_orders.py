from django.core.management.base import BaseCommand
from django.db import transaction

from selling.models import SalesOrder
from selling.services import dispatch as dispatch_service


class Command(BaseCommand):
    help = "Recompute derived Sales Order dispatch_status from submitted invoices."

    def add_arguments(self, parser):
        parser.add_argument(
            "--order",
            dest="order",
            default=None,
            help="Optional Sales Order name or ID",
        )
        parser.add_argument(
            "--company", dest="company", default=None, help="Optional Company abbr"
        )

    def handle(self, *args, **options):
        self.stdout.write("Running Sales Order Repost...")
        qs = SalesOrder.objects.all().order_by("pk")
        if options.get("order"):
            val = options["order"]
            if val.isdigit():
                qs = qs.filter(pk=int(val))
            else:
                qs = qs.filter(name=val)
        if options.get("company"):
            qs = qs.filter(company__abbr=options["company"])
        count = 0
        with transaction.atomic():
            for so in qs:
                dispatch_service.refresh_status(so)
                count += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"[OK] Reposted dispatch status for {count} Sales Order(s)."
            )
        )
