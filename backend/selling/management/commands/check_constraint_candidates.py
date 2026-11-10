"""Read-only report: how many existing rows would break the new DB rules.

Run before deploying the S-3 / S-8 constraints. It counts, and it writes nothing.
A count above zero means that rule cannot be added until the rows are fixed (a
duplicate name is for the accountant to merge). Run:
``python manage.py check_constraint_candidates``
"""

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.db.models.functions import Lower

from buying.models import PurchaseInvoiceItem, PurchaseOrderItem, Supplier
from selling.models import Customer, SalesInvoiceItem, SalesOrderItem

SAMPLE_LIMIT = 20


def _line_checks():
    """``(label, queryset of violating rows)`` for each line rule."""
    return [
        (
            "selling.SalesOrderItem qty must be > 0",
            SalesOrderItem.objects.filter(qty__lte=0),
        ),
        (
            "selling.SalesOrderItem rate must be >= 0",
            SalesOrderItem.objects.filter(rate__lt=0),
        ),
        (
            "selling.SalesInvoiceItem qty must not be 0",
            SalesInvoiceItem.objects.filter(qty=0),
        ),
        (
            "selling.SalesInvoiceItem rate must be >= 0",
            SalesInvoiceItem.objects.filter(rate__lt=0),
        ),
        (
            "buying.PurchaseOrderItem qty must be > 0",
            PurchaseOrderItem.objects.filter(qty__lte=0),
        ),
        (
            "buying.PurchaseOrderItem rate must be >= 0",
            PurchaseOrderItem.objects.filter(rate__lt=0),
        ),
        (
            "buying.PurchaseInvoiceItem qty must not be 0",
            PurchaseInvoiceItem.objects.filter(qty=0),
        ),
        (
            "buying.PurchaseInvoiceItem rate must be >= 0",
            PurchaseInvoiceItem.objects.filter(rate__lt=0),
        ),
    ]


def _duplicate_names(model):
    """Case-insensitive duplicate names per company: ``[(company_id, lower, [names])]``."""
    groups = (
        model.objects.order_by()
        .annotate(lower_name=Lower("name"))
        .values("company_id", "lower_name")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .order_by("company_id", "lower_name")
    )
    out = []
    for group in groups:
        names = list(
            model.objects.filter(
                company_id=group["company_id"],
            )
            .annotate(lower_name=Lower("name"))
            .filter(lower_name=group["lower_name"])
            .values_list("name", flat=True)
        )
        out.append((group["company_id"], group["lower_name"], names))
    return out


class Command(BaseCommand):
    help = (
        "Count existing rows that would violate the new line CHECK constraints and the "
        "case-insensitive party name uniqueness. Read-only."
    )

    def handle(self, *args, **options):
        self.stdout.write("Constraint candidates (violating rows, read-only):")
        for label, qs in _line_checks():
            self.stdout.write(f"  {qs.count():>8}  {label}")

        for label, model in (
            ("selling.Customer name per company (ignoring case)", Customer),
            ("buying.Supplier name per company (ignoring case)", Supplier),
        ):
            groups = _duplicate_names(model)
            extra_rows = sum(len(names) - 1 for _c, _n, names in groups)
            self.stdout.write(
                f"  {len(groups):>8} groups ({extra_rows} extra rows)  {label}"
            )
            for company_id, _lower, names in groups[:SAMPLE_LIMIT]:
                self.stdout.write(
                    f"             company {company_id}: {' | '.join(names)}"
                )
            if len(groups) > SAMPLE_LIMIT:
                self.stdout.write(f"             ... {len(groups) - SAMPLE_LIMIT} more")
