import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import FiscalYear


class Command(BaseCommand):
    help = (
        "Create the April-to-March fiscal year containing today and the one containing "
        "today + 30 days, when missing. Never creates an overlapping year."
    )

    def handle(self, *args, **options):
        today = timezone.localdate()
        for day in (today, today + datetime.timedelta(days=30)):
            covered = FiscalYear.objects.filter(
                start_date__lte=day, end_date__gte=day
            ).exists()
            if covered:
                self.stdout.write(f"{day}: fiscal year already exists.")
                continue
            fy = FiscalYear.ensure_for_date(day)
            if fy is None:
                self.stdout.write(
                    self.style.WARNING(f"{day}: no fiscal year created (see rules).")
                )
            else:
                self.stdout.write(self.style.SUCCESS(f"{day}: created {fy.name}."))
