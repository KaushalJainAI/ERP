from django.core.management.base import BaseCommand, CommandError
from accounts.services import outstanding as outstanding_service
from core.models import Company


class Command(BaseCommand):
    help = (
        "Rebuilds the derived outstanding_amount / status of every Sales and Purchase "
        "Invoice from the General Ledger."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--company", type=str, help="Optional Company abbreviation to repost"
        )

    def handle(self, *args, **options):
        abbr = options.get("company")
        company = None
        if abbr:
            company = Company.objects.filter(abbr=abbr).first()
            if company is None:
                raise CommandError(f"No company with abbreviation '{abbr}'.")
        self.stdout.write("Running Invoice Outstanding Repost...")

        changed = outstanding_service.repost(company=company)
        self.stdout.write(
            self.style.SUCCESS(
                f"[OK] Invoice Outstanding Repost completed ({changed} corrected)."
            )
        )
