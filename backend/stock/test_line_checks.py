"""Database CHECKs on item price and stock-entry lines (card S-3, stock part).

Each rule is proven by a direct ORM write that the database refuses.
"""

from decimal import Decimal as D

from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import Company
from stock.models import (
    UOM,
    Item,
    ItemGroup,
    StockEntry,
    StockEntryItem,
    StockEntryType,
    Warehouse,
)


class StockLineChecksTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Check Co", abbr="CK")
        self.uom = UOM.objects.create(name="Nos")
        self.group = ItemGroup.objects.create(name="Misc", is_group=False)
        self.wh = Warehouse.objects.create(name="Store", company=self.company)
        self.item = Item.objects.create(
            code="CHK", name="Check", item_group=self.group, stock_uom=self.uom
        )
        self.ste = StockEntry.objects.create(
            name="STE-CHECK-1",
            stock_entry_type=StockEntryType.MATERIAL_RECEIPT,
            posting_date="2026-04-10",
            company=self.company,
        )

    def line(self, **kw):
        values = {
            "stock_entry": self.ste,
            "item": self.item,
            "target_warehouse": self.wh,
            "qty": D("1"),
            "valuation_rate": D("1"),
        }
        values.update(kw)
        return StockEntryItem.objects.create(**values)

    def assertRefused(self, **kw):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.line(**kw)

    def test_item_standard_rate_cannot_be_negative(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Item.objects.create(
                code="NEG",
                name="Negative",
                item_group=self.group,
                stock_uom=self.uom,
                standard_rate=D("-0.01"),
            )
        self.assertFalse(Item.objects.filter(code="NEG").exists())

    def test_item_standard_rate_zero_is_allowed(self):
        Item.objects.create(
            code="FREE",
            name="Free",
            item_group=self.group,
            stock_uom=self.uom,
            standard_rate=D("0"),
        )

    def test_stock_entry_item_qty_must_be_positive(self):
        self.assertRefused(qty=D("0"))
        self.assertRefused(qty=D("-2"))

    def test_stock_entry_item_valuation_rate_cannot_be_negative(self):
        self.assertRefused(valuation_rate=D("-1"))

    def test_valid_stock_entry_line_is_saved(self):
        row = self.line(qty=D("0.001"), valuation_rate=D("0"))
        self.assertEqual(row.qty, D("0.001"))
