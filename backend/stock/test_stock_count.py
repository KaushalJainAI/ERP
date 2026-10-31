import csv
from decimal import Decimal
from io import StringIO
import os
import tempfile

from django.core.management import call_command, CommandError
from django.test import TestCase

from accounts.models import Account
from core.models import Company, DocStatus
from stock.models import (
    Item,
    ItemGroup,
    ReconciliationPurpose,
    StockReconciliation,
    UOM,
    Warehouse,
)


class StockCountCommandsTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Nidhi Masala Count Test",
            abbr="NMCT",
            default_currency="INR",
        )
        self.uom = UOM.objects.create(name="Kg")
        self.group = ItemGroup.objects.create(name="Spices", is_group=False)

        # 3 stage warehouses
        self.wh_unproc = Warehouse.objects.create(
            name="Raw Material Stores", company=self.company
        )
        self.wh_inproc = Warehouse.objects.create(
            name="WIP Grinding", company=self.company
        )
        self.wh_ready = Warehouse.objects.create(
            name="Finished Goods FG", company=self.company
        )

        self.company.unprocessed_warehouse = self.wh_unproc
        self.company.in_process_warehouse = self.wh_inproc
        self.company.ready_warehouse = self.wh_ready
        self.company.save(
            update_fields=[
                "unprocessed_warehouse",
                "in_process_warehouse",
                "ready_warehouse",
            ]
        )

        # Difference account
        self.opening_account = Account.objects.create(
            name="Temporary Opening",
            company=self.company,
            root_type="Equity",
            is_group=False,
        )

        # Items
        self.item_a = Item.objects.create(
            code="ITEM-A",
            name="Item A Raw Haldi",
            item_group=self.group,
            stock_uom=self.uom,
            standard_rate=Decimal("120.00"),
            is_stock_item=True,
            is_active=True,
        )
        self.item_b = Item.objects.create(
            code="ITEM-B",
            name="Item B Haldi Powder",
            item_group=self.group,
            stock_uom=self.uom,
            standard_rate=Decimal("180.00"),
            is_stock_item=True,
            is_active=True,
        )
        self.item_inactive = Item.objects.create(
            code="ITEM-INACTIVE",
            name="Item Inactive",
            item_group=self.group,
            stock_uom=self.uom,
            standard_rate=Decimal("50.00"),
            is_stock_item=True,
            is_active=False,
        )

    def test_build_stock_count_sheet(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            temp_path = f.name

        try:
            out = StringIO()
            call_command(
                "build_stock_count_sheet",
                company=self.company.abbr,
                out=temp_path,
                stdout=out,
            )

            self.assertIn("Wrote 2 active items", out.getvalue())
            self.assertTrue(os.path.exists(temp_path))

            with open(temp_path, mode="r", encoding="utf-8") as f:
                reader = list(csv.reader(f))

            header = reader[0]
            self.assertEqual(
                header,
                [
                    "item_code",
                    "item_name",
                    "uom",
                    "standard_rate",
                    "Raw Material Stores",
                    "WIP Grinding",
                    "Finished Goods FG",
                ],
            )
            # Only 2 active items (item_inactive excluded)
            self.assertEqual(len(reader), 3)
            codes = [row[0] for row in reader[1:]]
            self.assertIn("ITEM-A", codes)
            self.assertIn("ITEM-B", codes)
            self.assertNotIn("ITEM-INACTIVE", codes)

            # Warehouse cells must be blank
            for row in reader[1:]:
                self.assertEqual(row[4:], ["", "", ""])
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_load_stock_count_dry_run_vs_commit(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
        ) as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "item_code",
                    "item_name",
                    "uom",
                    "standard_rate",
                    "Raw Material Stores",
                    "WIP Grinding",
                    "Finished Goods FG",
                ]
            )
            writer.writerow(
                ["ITEM-A", "Item A Raw Haldi", "Kg", "120.00", "50", "", ""]
            )
            writer.writerow(
                ["ITEM-B", "Item B Haldi Powder", "Kg", "180.00", "", "", "25"]
            )
            temp_path = f.name

        try:
            # 1. Dry run
            out_dry = StringIO()
            call_command(
                "load_stock_count",
                temp_path,
                company=self.company.abbr,
                dry_run=True,
                stdout=out_dry,
            )
            dry_output = out_dry.getvalue()
            self.assertIn("Dry-run preview: 2 line(s) to reconcile", dry_output)
            self.assertIn("ITEM-A", dry_output)
            self.assertIn("ITEM-B", dry_output)
            self.assertIn("[DRY-RUN] No records created", dry_output)
            # Ensure no DB records created
            self.assertEqual(StockReconciliation.objects.count(), 0)

            # Also verify default without --commit is dry-run
            out_default = StringIO()
            call_command(
                "load_stock_count",
                temp_path,
                company=self.company.abbr,
                stdout=out_default,
            )
            self.assertIn("[DRY-RUN] No records created", out_default.getvalue())
            self.assertEqual(StockReconciliation.objects.count(), 0)

            # 2. Commit
            out_commit = StringIO()
            call_command(
                "load_stock_count",
                temp_path,
                company=self.company.abbr,
                commit=True,
                stdout=out_commit,
            )
            commit_output = out_commit.getvalue()
            self.assertIn("Created Draft Stock Reconciliation", commit_output)
            self.assertIn("with 2 items", commit_output)

            self.assertEqual(StockReconciliation.objects.count(), 1)
            reco = StockReconciliation.objects.first()
            self.assertEqual(reco.docstatus, DocStatus.DRAFT)
            self.assertEqual(reco.purpose, ReconciliationPurpose.OPENING_STOCK)
            self.assertEqual(reco.company, self.company)
            self.assertEqual(reco.expense_account, self.opening_account)

            lines = reco.items.order_by("pk")
            self.assertEqual(lines.count(), 2)

            line1 = lines.filter(item=self.item_a).first()
            self.assertIsNotNone(line1)
            self.assertEqual(line1.warehouse, self.wh_unproc)
            self.assertEqual(line1.qty, Decimal("50.000"))
            self.assertEqual(line1.valuation_rate, Decimal("120.00"))

            line2 = lines.filter(item=self.item_b).first()
            self.assertIsNotNone(line2)
            self.assertEqual(line2.warehouse, self.wh_ready)
            self.assertEqual(line2.qty, Decimal("25.000"))
            self.assertEqual(line2.valuation_rate, Decimal("180.00"))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_blank_and_zero_rows_handled(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
        ) as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "item_code",
                    "item_name",
                    "uom",
                    "standard_rate",
                    "Raw Material Stores",
                    "WIP Grinding",
                    "Finished Goods FG",
                ]
            )
            # Item A has blank wh1, 0 wh2, 10 wh3 -> only wh3 should be created
            writer.writerow(
                ["ITEM-A", "Item A Raw Haldi", "Kg", "120.00", "", "0", "10"]
            )
            # Item B has all blanks -> completely skipped
            writer.writerow(
                ["ITEM-B", "Item B Haldi Powder", "Kg", "180.00", "", "", ""]
            )
            # Item A again in another row with all zeros -> completely skipped
            writer.writerow(
                ["ITEM-A", "Item A Raw Haldi", "Kg", "120.00", "0", "0.0", "0.000"]
            )
            # Blank row -> completely skipped
            writer.writerow(["", "", "", "", "", "", ""])
            temp_path = f.name

        try:
            out = StringIO()
            call_command(
                "load_stock_count",
                temp_path,
                company=self.company.abbr,
                commit=True,
                stdout=out,
            )
            self.assertIn("Created Draft Stock Reconciliation", out.getvalue())
            self.assertIn("with 1 items", out.getvalue())

            reco = StockReconciliation.objects.first()
            self.assertEqual(reco.items.count(), 1)
            line = reco.items.first()
            self.assertEqual(line.item, self.item_a)
            self.assertEqual(line.warehouse, self.wh_ready)
            self.assertEqual(line.qty, Decimal("10.000"))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_unknown_item_refused(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
        ) as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "item_code",
                    "item_name",
                    "uom",
                    "standard_rate",
                    "Raw Material Stores",
                    "WIP Grinding",
                    "Finished Goods FG",
                ]
            )
            writer.writerow(["GHOST-ITEM", "Ghost Spice", "Kg", "100.00", "10", "", ""])
            temp_path = f.name

        try:
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "load_stock_count",
                    temp_path,
                    company=self.company.abbr,
                    commit=True,
                )
            self.assertIn("Unknown item code 'GHOST-ITEM'", str(ctx.exception))
            self.assertEqual(StockReconciliation.objects.count(), 0)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_unknown_warehouse_refused(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
        ) as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "item_code",
                    "item_name",
                    "uom",
                    "standard_rate",
                    "Nonexistent Warehouse",
                ]
            )
            writer.writerow(["ITEM-A", "Item A Raw Haldi", "Kg", "120.00", "10"])
            temp_path = f.name

        try:
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "load_stock_count",
                    temp_path,
                    company=self.company.abbr,
                    commit=True,
                )
            self.assertIn(
                "Unknown warehouse: 'Nonexistent Warehouse'", str(ctx.exception)
            )
            self.assertEqual(StockReconciliation.objects.count(), 0)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_negative_quantity_refused(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
        ) as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "item_code",
                    "item_name",
                    "uom",
                    "standard_rate",
                    "Raw Material Stores",
                ]
            )
            writer.writerow(["ITEM-A", "Item A Raw Haldi", "Kg", "120.00", "-5"])
            temp_path = f.name

        try:
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "load_stock_count",
                    temp_path,
                    company=self.company.abbr,
                    commit=True,
                )
            self.assertIn("Negative quantity '-5'", str(ctx.exception))
            self.assertEqual(StockReconciliation.objects.count(), 0)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
