from django.core.management.base import BaseCommand
from stock.services import ledger as stock_service


class Command(BaseCommand):
    help = "Rebuilds derived Bin stock balances and valuation from StockLedgerEntry logs."

    def add_arguments(self, parser):
        parser.add_argument("--item", type=int, help="Optional Item ID to repost")
        parser.add_argument("--warehouse", type=int, help="Optional Warehouse ID to repost")

    def handle(self, *args, **options):
        item_id = options.get("item")
        warehouse_id = options.get("warehouse")
        self.stdout.write("Running Stock Repost...")

        stock_service.repost(item_id=item_id, warehouse_id=warehouse_id)
        self.stdout.write(self.style.SUCCESS("[OK] Stock Repost completed successfully."))
