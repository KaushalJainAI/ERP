import datetime
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from core.models import Company
from buying.services.expenses import generate_due_recurring_expenses


class Command(BaseCommand):
    help = (
        "Generate monthly draft purchase invoices for active recurring expenses "
        "that are due today (or on the specified date)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--company",
            type=str,
            help="Optional Company abbreviation to scope generation.",
        )
        parser.add_argument(
            "--date",
            "--as-on",
            dest="as_on",
            type=str,
            help="Optional date in YYYY-MM-DD format (defaults to today).",
        )

    def handle(self, *args, **options):
        abbr = options.get("company")
        company = None
        if abbr:
            company = Company.objects.filter(abbr=abbr).first()
            if company is None:
                raise CommandError(f"No company with abbreviation '{abbr}'.")

        date_str = options.get("as_on")
        as_on = None
        if date_str:
            try:
                as_on = datetime.date.fromisoformat(date_str)
            except ValueError:
                raise CommandError(
                    f"Invalid date format '{date_str}'. Expected YYYY-MM-DD."
                )
        else:
            as_on = timezone.localdate()

        self.stdout.write(
            f"Checking recurring expenses due on {as_on} "
            f"for {company.abbr if company else 'all companies'}..."
        )

        invoices = generate_due_recurring_expenses(company=company, as_on=as_on)

        self.stdout.write(
            self.style.SUCCESS(
                f"[OK] Generated {len(invoices)} draft recurring expense invoice(s)."
            )
        )
