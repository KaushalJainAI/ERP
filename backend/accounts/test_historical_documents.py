"""Historical documents (Tally-era invoices and payments shown in the ERP).

They are records only: their GL is the TLY-* Journal Entry the Tally import
created, so they post nothing, owe nothing, cannot be cancelled and stay out of
the GST returns (filed from Tally)."""

import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from accounts.models import InvoiceStatus, JournalEntry
from core.models import DocStatus
from gst.tests.test_documents import SI_URL
from gst.tests.test_reports import OCT, ReportBase
from selling.models import SalesInvoice, SalesInvoiceItem


class HistoricalDocumentTests(ReportBase):
    def make_historical(self):
        d = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        journal = JournalEntry.objects.create(
            name="TLY-2627-000001",
            company=self.company,
            posting_date=datetime.date(2026, 10, 5),
            user_remark="Tally Sales 1",
        )
        SalesInvoice.objects.filter(pk=d["id"]).update(
            is_historical=True,
            source_journal=journal,
            outstanding_amount=0,
            status=InvoiceStatus.HISTORICAL,
        )
        return SalesInvoice.objects.get(pk=d["id"]), journal

    def test_db_refuses_a_historical_invoice_without_its_journal(self):
        d = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesInvoice.objects.filter(pk=d["id"]).update(is_historical=True)

    def test_db_refuses_a_historical_invoice_that_owes_money(self):
        inv, journal = self.make_historical()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesInvoice.objects.filter(pk=inv.pk).update(outstanding_amount=10)

    def test_db_refuses_a_zero_quantity_line_on_a_normal_invoice(self):
        d = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        line = SalesInvoiceItem.objects.filter(sales_invoice_id=d["id"]).first()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesInvoiceItem.objects.filter(pk=line.pk).update(qty=0)

    def test_db_allows_a_zero_quantity_line_on_a_historical_invoice(self):
        inv, _ = self.make_historical()
        SalesInvoiceItem.objects.filter(sales_invoice=inv).update(
            qty=0, is_historical=True
        )
        self.assertTrue(SalesInvoiceItem.objects.filter(sales_invoice=inv, qty=0).exists())

    def test_cannot_be_cancelled(self):
        inv, _ = self.make_historical()
        with self.assertRaises(ValidationError):
            inv.cancel()
        inv.refresh_from_db()
        self.assertEqual(inv.docstatus, DocStatus.SUBMITTED)

    def test_api_shows_and_filters_historical(self):
        inv, journal = self.make_historical()
        self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)  # a normal one
        data = self.client.get(f"{SI_URL}{inv.pk}/").data
        self.assertTrue(data["is_historical"])
        self.assertEqual(data["source_journal_name"], journal.name)
        self.assertEqual(data["status"], "Historical")
        rows = self.client.get(SI_URL, {"is_historical": "true"}).data["results"]
        self.assertEqual([r["id"] for r in rows], [inv.pk])

    def test_api_cannot_set_the_flag(self):
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_intra, self.lines((self.item18, 1, 100)), is_historical=True
            ),
        )
        self.assertFalse(SalesInvoice.objects.get(pk=d["id"]).is_historical)

    def test_left_out_of_gst_returns(self):
        from decimal import Decimal
        import json

        d = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        before = Decimal(self.gstr3b()["3.1"]["a_outward_taxable"]["taxable_value"])
        self.assertIn(d["name"], json.dumps(self.gstr1(), default=str))
        journal = JournalEntry.objects.create(
            name="TLY-2627-000002",
            company=self.company,
            posting_date=datetime.date(2026, 10, 5),
        )
        SalesInvoice.objects.filter(pk=d["id"]).update(
            is_historical=True,
            source_journal=journal,
            outstanding_amount=0,
            status=InvoiceStatus.HISTORICAL,
        )
        after = Decimal(self.gstr3b()["3.1"]["a_outward_taxable"]["taxable_value"])
        self.assertEqual(before - after, Decimal("100.00"))
        self.assertNotIn(d["name"], json.dumps(self.gstr1(), default=str))
