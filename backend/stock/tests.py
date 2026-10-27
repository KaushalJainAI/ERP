import datetime
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from core.models import Company, DummySubmittableDoc, DocStatus
from stock.models import (
    UOM,
    ItemGroup,
    Item,
    Warehouse,
    Bin,
    ValuationMethod,
    StockEntry,
    StockEntryItem,
    StockEntryType,
)
from stock.services import ledger as stock_service


class StockAppTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Apex Logistics", abbr="APEX", default_currency="INR"
        )
        self.uom = UOM.objects.create(name="Nos")
        self.group = ItemGroup.objects.create(name="Raw Materials", is_group=True)
        self.item = Item.objects.create(
            code="ITEM-001",
            name="Steel Plate 5mm",
            item_group=self.group,
            stock_uom=self.uom,
            valuation_method=ValuationMethod.FIFO,
            standard_rate=Decimal("100.00"),
        )
        self.wh_parent = Warehouse.objects.create(
            name="Main Hub", company=self.company, is_group=True
        )
        self.warehouse = Warehouse.objects.create(
            name="Stores Store-1",
            company=self.company,
            parent=self.wh_parent,
            is_group=False,
        )
        self.doc = DummySubmittableDoc.objects.create(
            name="MAT-REC-001",
            title="Material Receipt 1",
            docstatus=DocStatus.SUBMITTED,
        )

    # -------------------------------------------------------------------------
    # Good Unit Tests
    # -------------------------------------------------------------------------

    def test_item_and_warehouse_creation(self):
        """GOOD: Item and Warehouse master records created successfully."""
        self.assertEqual(self.item.code, "ITEM-001")
        self.assertEqual(self.warehouse.name, "Stores Store-1")
        self.assertFalse(self.warehouse.is_group)

    def test_stock_post_receipt_updates_bin(self):
        """GOOD: Posting stock receipt (+qty) creates SLE and updates derived Bin balance & valuation."""
        sle_data = [
            {
                "item": self.item,
                "warehouse": self.warehouse,
                "qty": Decimal("10.000"),
                "valuation_rate": Decimal("150.00"),
            }
        ]
        sles = stock_service.post(
            voucher=self.doc,
            sle_entries=sle_data,
            posting_date=datetime.date(2026, 4, 1),
        )
        self.assertEqual(len(sles), 1)

        # Verify Bin derived state
        bin_obj = Bin.objects.get(item=self.item, warehouse=self.warehouse)
        self.assertEqual(bin_obj.actual_qty, Decimal("10.000"))
        self.assertEqual(bin_obj.stock_value, Decimal("1500.00"))
        self.assertEqual(bin_obj.valuation_rate, Decimal("150.000000"))

    def test_stock_issue_and_reversal_lifecycle(self):
        """GOOD: Posting receipt (+10) then issue (-4) then reversing receipt updates Bin correctly."""
        # Receipt 10 @ 100
        stock_service.post(
            voucher=self.doc,
            sle_entries=[
                {
                    "item": self.item,
                    "warehouse": self.warehouse,
                    "qty": Decimal("10.000"),
                    "valuation_rate": Decimal("100.00"),
                }
            ],
            posting_date=datetime.date(2026, 4, 1),
        )
        bin_obj = Bin.objects.get(item=self.item, warehouse=self.warehouse)
        self.assertEqual(bin_obj.actual_qty, Decimal("10.000"))

        # Issue 4
        issue_doc = DummySubmittableDoc.objects.create(
            name="MAT-ISS-001", title="Issue 1", docstatus=DocStatus.SUBMITTED
        )
        stock_service.post(
            voucher=issue_doc,
            sle_entries=[
                {
                    "item": self.item,
                    "warehouse": self.warehouse,
                    "qty": Decimal("-4.000"),
                    "valuation_rate": Decimal("100.00"),
                }
            ],
            posting_date=datetime.date(2026, 4, 2),
        )
        bin_obj.refresh_from_db()
        self.assertEqual(bin_obj.actual_qty, Decimal("6.000"))

        # Reversal of Issue 4
        stock_service.reverse(issue_doc)
        bin_obj.refresh_from_db()
        self.assertEqual(bin_obj.actual_qty, Decimal("10.000"))

    def test_stock_repost_restores_corrupted_bin(self):
        """GOOD: Self-healing repost restores Bin state when derived table is corrupted."""
        stock_service.post(
            voucher=self.doc,
            sle_entries=[
                {
                    "item": self.item,
                    "warehouse": self.warehouse,
                    "qty": Decimal("20.000"),
                    "valuation_rate": Decimal("50.00"),
                }
            ],
            posting_date=datetime.date(2026, 4, 1),
        )
        bin_obj = Bin.objects.get(item=self.item, warehouse=self.warehouse)
        self.assertEqual(bin_obj.actual_qty, Decimal("20.000"))

        # Corrupt derived Bin
        bin_obj.actual_qty = Decimal("999.999")
        bin_obj.save()

        # Run repost self-healing
        stock_service.repost(item_id=self.item.id, warehouse_id=self.warehouse.id)
        rebuilt_bin = Bin.objects.get(item=self.item, warehouse=self.warehouse)

        # Restored to 20.000 from SLE logs
        self.assertEqual(rebuilt_bin.actual_qty, Decimal("20.000"))

    # -------------------------------------------------------------------------
    # Bad / Error Unit Tests
    # -------------------------------------------------------------------------

    def test_stock_post_to_group_warehouse_fails(self):
        """BAD: Posting stock to a group warehouse raises ValidationError."""
        sle_data = [
            {
                "item": self.item,
                "warehouse": self.wh_parent,
                "qty": Decimal("5.000"),
                "valuation_rate": Decimal("100.00"),
            }
        ]
        with self.assertRaises(ValidationError):
            stock_service.post(
                voucher=self.doc,
                sle_entries=sle_data,
                posting_date=datetime.date(2026, 4, 1),
            )

    def test_stock_issue_insufficient_stock_fails(self):
        """BAD: Attempting to issue more stock than available raises ValidationError."""
        sle_data = [
            {
                "item": self.item,
                "warehouse": self.warehouse,
                "qty": Decimal("-5.000"),
                "valuation_rate": Decimal("100.00"),
            }
        ]
        with self.assertRaises(ValidationError):
            stock_service.post(
                voucher=self.doc,
                sle_entries=sle_data,
                posting_date=datetime.date(2026, 4, 1),
            )

    def test_stock_ledger_entry_immutability_fails(self):
        """BAD: Updating or deleting an existing StockLedgerEntry row raises ValidationError."""
        sles = stock_service.post(
            voucher=self.doc,
            sle_entries=[
                {
                    "item": self.item,
                    "warehouse": self.warehouse,
                    "qty": Decimal("5.000"),
                    "valuation_rate": Decimal("10.00"),
                }
            ],
            posting_date=datetime.date(2026, 4, 1),
        )
        sle = sles[0]

        # BAD: Direct update
        sle.qty = Decimal("500.000")
        with self.assertRaises(ValidationError):
            sle.save()

        # BAD: Direct delete
        with self.assertRaises(ValidationError):
            sle.delete()

    def test_duplicate_bin_fails(self):
        """BAD: Duplicate Bin for same (item, warehouse) violates UNIQUE constraint."""
        Bin.objects.create(
            item=self.item, warehouse=self.warehouse, actual_qty=Decimal("5.000")
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Bin.objects.create(
                    item=self.item,
                    warehouse=self.warehouse,
                    actual_qty=Decimal("10.000"),
                )

    def test_stock_entry_receipt_and_cancel_flow(self):
        """GOOD: Submitting StockEntry (Material Receipt) posts SLE and updates Bin balance; cancel reverses."""
        ste = StockEntry.objects.create(
            name="STE-REC-001",
            stock_entry_type=StockEntryType.MATERIAL_RECEIPT,
            posting_date=datetime.date(2026, 4, 1),
            company=self.company,
        )
        StockEntryItem.objects.create(
            stock_entry=ste,
            item=self.item,
            target_warehouse=self.warehouse,
            qty=Decimal("15.000"),
            valuation_rate=Decimal("120.00"),
        )

        # Submit Stock Entry
        ste.submit()
        self.assertEqual(ste.docstatus, DocStatus.SUBMITTED)
        bin_obj = Bin.objects.get(item=self.item, warehouse=self.warehouse)
        self.assertEqual(bin_obj.actual_qty, Decimal("15.000"))

        # Cancel Stock Entry
        ste.cancel()
        self.assertEqual(ste.docstatus, DocStatus.CANCELLED)
        bin_obj.refresh_from_db()
        self.assertEqual(bin_obj.actual_qty, Decimal("0.000"))

    def test_material_transfer_conserves_total_stock_value(self):
        """GOOD: A transfer values the receipt at the source's outgoing rate, so
        total stock value across warehouses is unchanged even when the item's
        entered valuation_rate on the transfer differs from the source rate."""
        dest = Warehouse.objects.create(
            name="Stores Store-2",
            company=self.company,
            parent=self.wh_parent,
            is_group=False,
        )
        # Seed source warehouse at rate 100 -> stock value 1000.
        stock_service.post(
            voucher=self.doc,
            sle_entries=[
                {
                    "item": self.item,
                    "warehouse": self.warehouse,
                    "qty": Decimal("10.000"),
                    "valuation_rate": Decimal("100.00"),
                }
            ],
            posting_date=datetime.date(2026, 4, 1),
        )
        total_before = sum(b.stock_value for b in Bin.objects.all())

        # Transfer 4 units with a bogus/zero transfer valuation_rate.
        ste = StockEntry.objects.create(
            name="STE-TRF-001",
            stock_entry_type=StockEntryType.MATERIAL_TRANSFER,
            posting_date=datetime.date(2026, 4, 2),
            company=self.company,
        )
        StockEntryItem.objects.create(
            stock_entry=ste,
            item=self.item,
            source_warehouse=self.warehouse,
            target_warehouse=dest,
            qty=Decimal("4.000"),
            valuation_rate=Decimal("0.00"),  # deliberately wrong / unset
        )
        ste.submit()

        total_after = sum(b.stock_value for b in Bin.objects.all())
        self.assertEqual(total_after, total_before)

        # Destination valued at the source's outgoing rate (100), not 0.
        dest_bin = Bin.objects.get(item=self.item, warehouse=dest)
        self.assertEqual(dest_bin.actual_qty, Decimal("4.000"))
        self.assertEqual(dest_bin.stock_value, Decimal("400.00"))
        self.assertEqual(dest_bin.valuation_rate, Decimal("100.000000"))

    def test_cancel_receipt_after_stock_consumed_is_blocked(self):
        """BAD: Reversing a receipt whose units were later issued would drive the
        Bin negative — the No-Negative-Stock invariant must block it."""
        # Receipt of 10 on the receipt voucher (self.doc).
        stock_service.post(
            voucher=self.doc,
            sle_entries=[
                {
                    "item": self.item,
                    "warehouse": self.warehouse,
                    "qty": Decimal("10.000"),
                    "valuation_rate": Decimal("100.00"),
                }
            ],
            posting_date=datetime.date(2026, 4, 1),
        )
        # A different voucher issues 8 of those units.
        issue_doc = DummySubmittableDoc.objects.create(
            name="MAT-ISS-001", title="Issue", docstatus=DocStatus.SUBMITTED
        )
        stock_service.post(
            voucher=issue_doc,
            sle_entries=[
                {
                    "item": self.item,
                    "warehouse": self.warehouse,
                    "qty": Decimal("-8.000"),
                    "valuation_rate": Decimal("100.00"),
                }
            ],
            posting_date=datetime.date(2026, 4, 2),
        )

        # Cancelling the original receipt would leave -8 on hand: reject it.
        with self.assertRaises(ValidationError):
            with transaction.atomic():
                stock_service.reverse(self.doc)

        # Bin is untouched by the failed reversal (atomic rollback), no negatives.
        bin_obj = Bin.objects.get(item=self.item, warehouse=self.warehouse)
        self.assertEqual(bin_obj.actual_qty, Decimal("2.000"))

    def test_stock_entry_receipt_missing_target_warehouse_fails(self):
        """BAD: Material Receipt without target warehouse raises ValidationError on submit."""
        ste = StockEntry.objects.create(
            name="STE-BAD-001",
            stock_entry_type=StockEntryType.MATERIAL_RECEIPT,
            posting_date=datetime.date(2026, 4, 1),
            company=self.company,
        )
        StockEntryItem.objects.create(
            stock_entry=ste,
            item=self.item,
            target_warehouse=None,  # Missing
            qty=Decimal("5.000"),
        )
        with self.assertRaises(ValidationError):
            ste.submit()

    def test_stock_balance_report_endpoint(self):
        """GOOD: Stock balance report endpoint calculates total inventory quantity and valuation."""
        from rest_framework.test import APIClient
        from users.models import User

        user = User.objects.create_superuser(
            username="admin_stock_rep", email="admin_srep@test.com", password="password"
        )
        client = APIClient()
        client.force_authenticate(user=user)

        stock_service.post(
            voucher=self.doc,
            sle_entries=[
                {
                    "item": self.item,
                    "warehouse": self.warehouse,
                    "qty": Decimal("75.000"),
                    "valuation_rate": Decimal("80.00"),
                }
            ],
            posting_date=datetime.date(2026, 4, 1),
        )

        res = client.get(f"/api/stock/reports/stock-balance/?item={self.item.id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["total_actual_qty"], "75.000")
        self.assertEqual(res.data["total_stock_value"], "6000.00")
