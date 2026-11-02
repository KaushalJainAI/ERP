"""verify_integrity: orphan ledger rows (plan #6) and over-billed orders (#4)."""

from decimal import Decimal

from accounts.models import GLEntry
from core.models import DocStatus
from core.services import ledger_integrity
from gst.tests.test_documents import SO_URL
from gst.tests.test_posting import PostingBase
from selling.models import SalesInvoice, SalesInvoiceItem, SalesOrder


class LedgerIntegrityTests(PostingBase):
    def errors(self):
        return ledger_integrity.check()[0]

    def test_clean_books_have_no_errors(self):
        self.sale(self.cust_intra, (self.item18, 1, 100))
        self.assertEqual(ledger_integrity.check(), ([], []))

    def test_live_rows_on_a_draft_document_are_an_error(self):
        d = self.sale(self.cust_intra, (self.item18, 1, 100))
        SalesInvoice.objects.filter(name=d["name"]).update(docstatus=DocStatus.DRAFT)
        errors = self.errors()
        self.assertTrue(any(d["name"] in e and "Orphan" in e for e in errors), errors)

    def test_rows_without_a_document_are_a_warning(self):
        d = self.sale(self.cust_intra, (self.item18, 1, 100))
        GLEntry.objects.filter(voucher_no=d["name"]).update(voucher_no="SINV-GONE")
        errors, warnings = ledger_integrity.check()
        self.assertEqual(errors, [])
        self.assertTrue(any("SINV-GONE" in w for w in warnings), warnings)

    def test_over_billed_order_is_an_error(self):
        so = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 2, 100)))
        )
        self.submit(SO_URL, so)
        order = SalesOrder.objects.get(name=so["name"])
        inv = self.sale(self.cust_intra, (self.item18, 2, 100), sales_order=order.pk)
        self.assertEqual(self.errors(), [])
        # Bill 3 against an order of 2 (only possible by bypassing the services).
        SalesInvoiceItem.objects.filter(sales_invoice__name=inv["name"]).update(
            qty=Decimal("3")
        )
        errors = self.errors()
        self.assertTrue(
            any(so["name"] in e and "Over-billed" in e for e in errors), errors
        )
