from django.core.management.base import BaseCommand
from django.db.models import Q

from accounts.models import JournalEntryLine, PaymentEntry, PaymentMode


def _violations():
    """(label, queryset of EXISTING rows that a new constraint would reject)."""
    return [
        (
            "JournalEntryLine debit < 0",
            JournalEntryLine.objects.filter(debit__lt=0),
        ),
        (
            "JournalEntryLine credit < 0",
            JournalEntryLine.objects.filter(credit__lt=0),
        ),
        (
            "JournalEntryLine has both debit and credit > 0",
            JournalEntryLine.objects.filter(debit__gt=0, credit__gt=0),
        ),
        (
            "PaymentEntry paid_amount <= 0 (historical rows excluded)",
            PaymentEntry.objects.filter(is_historical=False, paid_amount__lte=0),
        ),
        (
            "PaymentEntry mode not in the allowed list",
            PaymentEntry.objects.exclude(mode__isnull=True).exclude(
                mode__in=PaymentMode.values
            ),
        ),
        (
            "PaymentEntry Cheque without reference_no",
            PaymentEntry.objects.filter(mode=PaymentMode.CHEQUE).filter(
                Q(reference_no__isnull=True) | Q(reference_no="")
            ),
        ),
    ]


# Read-only: this command counts and lists, it never writes or repairs.
class Command(BaseCommand):
    help = (
        "Counts existing accounts rows that would violate the new accounts "
        "constraints. Read-only: writes nothing. Run before deploying the migration."
    )

    def handle(self, *args, **options):
        total = 0
        for label, qs in _violations():
            count = qs.count()
            total += count
            self.stdout.write(f"{count:>8}  {label}")
            if count:
                ids = list(qs.values_list("pk", flat=True)[:20])
                self.stdout.write(f"          first ids: {ids}")
        if total:
            self.stdout.write(
                self.style.WARNING(
                    f"{total} row(s) would violate the new constraints; fix them before migrating."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "No existing rows would violate the new constraints."
                )
            )
