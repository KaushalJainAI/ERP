from django.core.management.base import BaseCommand
from accounts.services import gl as gl_service


class Command(BaseCommand):
    help = "Recomputes and verifies General Ledger entries balance consistency."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, help="Optional Company ID to repost")
        parser.add_argument("--account", type=int, help="Optional Account ID to repost")

    def handle(self, *args, **options):
        company_id = options.get("company")
        account_id = options.get("account")
        self.stdout.write("Running GL Repost...")

        gl_service.repost(company_id=company_id, account_id=account_id)
        self.stdout.write(self.style.SUCCESS("[OK] GL Repost completed successfully."))
