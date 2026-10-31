"""Tests for Stock Stages Report (Card P-5)."""

from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company
from stock.models import UOM, Bin, Item, ItemGroup, Warehouse
from stock.reports.stages import stock_stages_report
from users.models import User


class StockStagesReportTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Nidhi Masala Stage Test", abbr="NMST", default_currency="INR"
        )
        self.uom = UOM.objects.create(name="Kg")
        self.group = ItemGroup.objects.create(name="Spices", is_group=False)

        # 3 stage warehouses + 1 other warehouse
        self.wh_unproc = Warehouse.objects.create(
            name="Raw Material Stores", company=self.company
        )
        self.wh_inproc = Warehouse.objects.create(
            name="WIP Grinding", company=self.company
        )
        self.wh_ready = Warehouse.objects.create(
            name="Finished Goods FG", company=self.company
        )
        self.wh_other = Warehouse.objects.create(
            name="Transit Warehouse", company=self.company
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

        # Item A: critical stock 10, ready 4 <= 10 -> Critical!
        self.item_a = Item.objects.create(
            code="ITEM-A",
            name="Item A (Coriander)",
            item_group=self.group,
            stock_uom=self.uom,
            critical_stock=Decimal("10.000"),
            is_stock_item=True,
        )
        Bin.objects.create(
            item=self.item_a,
            warehouse=self.wh_unproc,
            actual_qty=Decimal("5.000"),
            valuation_rate=Decimal("100.00"),
            stock_value=Decimal("500.00"),
        )
        Bin.objects.create(
            item=self.item_a,
            warehouse=self.wh_inproc,
            actual_qty=Decimal("2.000"),
            valuation_rate=Decimal("100.00"),
            stock_value=Decimal("200.00"),
        )
        Bin.objects.create(
            item=self.item_a,
            warehouse=self.wh_ready,
            actual_qty=Decimal("4.000"),
            valuation_rate=Decimal("100.00"),
            stock_value=Decimal("400.00"),
        )
        Bin.objects.create(
            item=self.item_a,
            warehouse=self.wh_other,
            actual_qty=Decimal("3.000"),
            valuation_rate=Decimal("100.00"),
            stock_value=Decimal("300.00"),
        )

        # Item B: critical stock 10, ready 15 > 10 -> Normal
        self.item_b = Item.objects.create(
            code="ITEM-B",
            name="Item B (Turmeric)",
            item_group=self.group,
            stock_uom=self.uom,
            critical_stock=Decimal("10.000"),
            is_stock_item=True,
        )
        Bin.objects.create(
            item=self.item_b,
            warehouse=self.wh_ready,
            actual_qty=Decimal("15.000"),
            valuation_rate=Decimal("120.00"),
            stock_value=Decimal("1800.00"),
        )

        # Item C: critical stock 0, ready 0 -> Normal (critical_stock = 0 is not critical)
        self.item_c = Item.objects.create(
            code="ITEM-C",
            name="Item C (Black Pepper)",
            item_group=self.group,
            stock_uom=self.uom,
            critical_stock=Decimal("0.000"),
            is_stock_item=True,
        )
        Bin.objects.create(
            item=self.item_c,
            warehouse=self.wh_unproc,
            actual_qty=Decimal("1.000"),
            valuation_rate=Decimal("500.00"),
            stock_value=Decimal("500.00"),
        )

        # Item D: critical stock 8, ready 2 <= 8 -> Critical!
        self.item_d = Item.objects.create(
            code="ITEM-D",
            name="Item D (Chili Powder)",
            item_group=self.group,
            stock_uom=self.uom,
            critical_stock=Decimal("8.000"),
            is_stock_item=True,
        )
        Bin.objects.create(
            item=self.item_d,
            warehouse=self.wh_ready,
            actual_qty=Decimal("2.000"),
            valuation_rate=Decimal("150.00"),
            stock_value=Decimal("300.00"),
        )

        # Item E: Service item (not a stock item) -> Should NOT appear
        self.item_service = Item.objects.create(
            code="SRV-01",
            name="Grinding Service",
            item_group=self.group,
            stock_uom=self.uom,
            critical_stock=Decimal("0.000"),
            is_stock_item=False,
        )

    def test_stock_stages_report_calculation_and_sorting(self):
        """Assert report totals match Bins and critical items sort first, then alphabetically."""
        rows = stock_stages_report(self.company)

        # 4 stock items returned, service item excluded
        self.assertEqual(len(rows), 4)

        # Critical items must come first
        # Item A (Critical) and Item D (Critical), sorted alphabetically by name
        self.assertTrue(rows[0]["is_critical"])
        self.assertEqual(rows[0]["item_id"], self.item_a.id)
        self.assertEqual(rows[0]["name"], "Item A (Coriander)")
        self.assertEqual(rows[0]["critical_stock"], Decimal("10.000"))
        self.assertEqual(rows[0]["unprocessed_qty"], Decimal("5.000"))
        self.assertEqual(rows[0]["in_process_qty"], Decimal("2.000"))
        self.assertEqual(rows[0]["ready_qty"], Decimal("4.000"))
        # total_qty sums across all warehouses for this company (5 + 2 + 4 + 3 = 14)
        self.assertEqual(rows[0]["total_qty"], Decimal("14.000"))

        self.assertTrue(rows[1]["is_critical"])
        self.assertEqual(rows[1]["item_id"], self.item_d.id)
        self.assertEqual(rows[1]["name"], "Item D (Chili Powder)")
        self.assertEqual(rows[1]["critical_stock"], Decimal("8.000"))
        self.assertEqual(rows[1]["unprocessed_qty"], Decimal("0.000"))
        self.assertEqual(rows[1]["in_process_qty"], Decimal("0.000"))
        self.assertEqual(rows[1]["ready_qty"], Decimal("2.000"))
        self.assertEqual(rows[1]["total_qty"], Decimal("2.000"))

        # Non-critical items come next, sorted alphabetically
        self.assertFalse(rows[2]["is_critical"])
        self.assertEqual(rows[2]["item_id"], self.item_b.id)
        self.assertEqual(rows[2]["name"], "Item B (Turmeric)")
        self.assertEqual(rows[2]["ready_qty"], Decimal("15.000"))
        self.assertEqual(rows[2]["total_qty"], Decimal("15.000"))

        self.assertFalse(rows[3]["is_critical"])
        self.assertEqual(rows[3]["item_id"], self.item_c.id)
        self.assertEqual(rows[3]["name"], "Item C (Black Pepper)")
        self.assertEqual(rows[3]["unprocessed_qty"], Decimal("1.000"))
        self.assertEqual(rows[3]["ready_qty"], Decimal("0.000"))
        self.assertEqual(rows[3]["total_qty"], Decimal("1.000"))

        # Verify stages report totals match Bins
        all_bins_total = sum(
            Bin.objects.filter(warehouse__company=self.company).values_list(
                "actual_qty", flat=True
            )
        )
        report_total = sum(r["total_qty"] for r in rows)
        self.assertEqual(report_total, all_bins_total)
        self.assertEqual(report_total, Decimal("32.000"))

    def test_stock_stages_report_endpoint(self):
        """GET /api/stock/reports/stages/ returns 200 with rows and stage totals."""
        user = User.objects.create_superuser(
            username="stock_stages_admin",
            email="stages_admin@example.com",
            password="secretpassword",
        )
        client = APIClient()
        client.force_authenticate(user=user)

        res = client.get(f"/api/stock/reports/stages/?company={self.company.id}")
        self.assertEqual(res.status_code, 200)

        data = res.json()
        self.assertIn("rows", data)
        self.assertEqual(len(data["rows"]), 4)

        # Verify totals
        self.assertEqual(data["total_unprocessed_qty"], "6.000")  # 5 + 1
        self.assertEqual(data["total_in_process_qty"], "2.000")   # 2
        self.assertEqual(data["total_ready_qty"], "21.000")       # 4 + 15 + 2
        self.assertEqual(data["total_qty"], "32.000")             # 14 + 15 + 1 + 2

        # First row is critical Item A
        first = data["rows"][0]
        self.assertEqual(first["name"], "Item A (Coriander)")
        self.assertTrue(first["is_critical"])
        self.assertEqual(first["status"], "Critical")
