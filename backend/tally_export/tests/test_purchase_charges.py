"""Purchase bill number / date and extra charges in the Tally export."""

import datetime
from decimal import Decimal

from django.test import override_settings

from accounts.models import Account
from buying.models import PurchaseInvoice, PurchaseInvoiceCharge, PurchaseInvoiceItem
from buying.services import charges as charges_service
from buying.testing import next_bill_no
from tally_export.models import TallyLedgerMap
from tally_export.tests.test_export import MEDIA, Base, tearDownModule  # noqa: F401

D = Decimal


@override_settings(MEDIA_ROOT=MEDIA)
class PurchaseBillExportTests(Base):
    def setUp(self):
        super().setUp()
        charges_service.ensure_charge_setup(self.company)
        self.freight = Account.objects.get(company=self.company, name="Freight Inward")

    def bill(self, charges, **extra):
        extra.setdefault("bill_no", next_bill_no())
        pi = PurchaseInvoice.objects.create(
            name=self.name("PINV"),
            supplier=self.supplier,
            company=self.company,
            posting_date=datetime.date(2026, 4, 1),
            update_stock=True,
            credit_to=self.creditors,
            expense_account=self.purchases,
            **extra,
        )
        PurchaseInvoiceItem.objects.create(
            purchase_invoice=pi,
            item=self.chilli,
            warehouse=self.wh,
            qty=D("10"),
            rate=D("40"),
        )
        for i, ch in enumerate(charges, start=1):
            PurchaseInvoiceCharge.objects.create(purchase_invoice=pi, idx=i, **ch)
        pi.calculate_taxes_and_totals(resolve_lines=True)
        pi.submit()
        return pi

    def exported(self, pi):
        self.map_all()
        batch = self.export()
        v = self.vouchers(batch)[pi.name]
        total = sum((amt for _l, amt in v["ledgers"]), D("0")) + sum(
            (amt for _l, amt in v["allocs"]), D("0")
        )
        return v, total

    def test_reference_and_reference_date_are_the_supplier_bill(self):
        pi = self.bill([], bill_no="SUP/77", bill_date=datetime.date(2026, 3, 30))
        v, total = self.exported(pi)
        self.assertEqual(v["el"].findtext("REFERENCE"), "SUP/77")
        self.assertEqual(v["el"].findtext("REFERENCEDATE"), "20260330")
        self.assertEqual(total, D("0"))

    def test_reference_date_falls_back_to_posting_date(self):
        pi = self.bill([], bill_no="SUP/78")
        v, _total = self.exported(pi)
        self.assertEqual(v["el"].findtext("REFERENCE"), "SUP/78")
        self.assertEqual(v["el"].findtext("REFERENCEDATE"), "20260401")

    def test_on_bill_charge_is_its_own_ledger_and_the_voucher_balances(self):
        pi = self.bill(
            [
                {
                    "description": "Freight",
                    "account": self.freight,
                    "amount": D("100"),
                    "included_in_bill": True,
                },
                {"description": "Hamali", "amount": D("25"), "distribute_by": "Qty"},
            ]
        )
        v, total = self.exported(pi)
        ledgers = dict(v["ledgers"])
        self.assertEqual(ledgers["T Freight Inward"], D("100.00"))
        self.assertEqual(ledgers["Supp Traders"], D("-500.00"))
        # perpetual: the capitalised charges and their clearing account are folded
        # away -- Tally sees only what the supplier billed
        self.assertNotIn("T Expenses Included In Valuation", ledgers)
        self.assertNotIn("T Stock In Hand", ledgers)
        self.assertEqual([a for _l, a in v["allocs"]], [D("400.00")])
        self.assertEqual(total, D("0"))

    def test_off_bill_charge_is_not_in_the_purchase_voucher(self):
        pi = self.bill(
            [{"description": "Hamali", "amount": D("25"), "distribute_by": "Qty"}]
        )
        v, total = self.exported(pi)
        self.assertEqual(dict(v["ledgers"]), {"Supp Traders": D("-400.00")})
        self.assertEqual([a for _l, a in v["allocs"]], [D("400.00")])
        self.assertEqual(total, D("0"))

    def test_without_perpetual_inventory_the_voucher_balances_too(self):
        self.company.enable_perpetual_inventory = False
        self.company.save()
        pi = self.bill(
            [
                {
                    "description": "Freight",
                    "account": self.freight,
                    "amount": D("100"),
                    "included_in_bill": True,
                },
                {"description": "Hamali", "amount": D("25"), "distribute_by": "Qty"},
            ]
        )
        v, total = self.exported(pi)
        ledgers = dict(v["ledgers"])
        self.assertEqual(ledgers["T Freight Inward"], D("100.00"))
        self.assertEqual(ledgers["Supp Traders"], D("-500.00"))
        self.assertEqual(total, D("0"))
        self.assertFalse(TallyLedgerMap.objects.filter(tally_ledger="").exists())
