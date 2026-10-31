import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from accounts.models import (
    JournalEntry,
    JournalEntryLine,
    PaymentEntry,
    PaymentType,
)
from accounts.test_outstanding import OutstandingBase
from core.attachments import has_proof
from core.models import Attachment
from gst.tests.test_documents import PI_URL

D = Decimal


class ProofRequiredTests(OutstandingBase):
    def setUp(self):
        super().setUp()
        self.company.require_proof = True
        self.company.save(update_fields=["require_proof"])

    def create_attachment(self, doc):
        return Attachment.objects.create(
            model_label=doc._meta.label,
            object_id=str(doc.pk),
            original_filename="receipt.pdf",
            content_type="application/pdf",
            size=100,
            sha256="0" * 64,
            uploaded_by=self.admin,
            file="attachments/receipt.pdf",
        )

    # ------------------------------------------------------------- PaymentEntry
    def test_payment_entry_require_proof_false_passes(self):
        self.company.require_proof = False
        self.company.save(update_fields=["require_proof"])

        pe = PaymentEntry.objects.create(
            name="PE-TEST-001",
            payment_type=PaymentType.RECEIVE,
            posting_date=datetime.date.today(),
            company=self.company,
            party_type="Customer",
            party_name=self.cust_intra.name,
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=D("100.00"),
            received_amount=D("100.00"),
            reference_no="",
        )
        pe.submit()
        self.assertEqual(pe.docstatus, 1)

    def test_payment_entry_require_proof_true_without_proof_fails(self):
        pe = PaymentEntry.objects.create(
            name="PE-TEST-002",
            payment_type=PaymentType.RECEIVE,
            posting_date=datetime.date.today(),
            company=self.company,
            party_type="Customer",
            party_name=self.cust_intra.name,
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=D("100.00"),
            received_amount=D("100.00"),
            reference_no="",
        )
        with self.assertRaises(ValidationError) as ctx:
            pe.submit()
        self.assertIn(
            "Proof attachment is required before submission.", str(ctx.exception)
        )

    def test_payment_entry_require_proof_true_with_reference_no_passes(self):
        pe = PaymentEntry.objects.create(
            name="PE-TEST-003",
            payment_type=PaymentType.RECEIVE,
            posting_date=datetime.date.today(),
            company=self.company,
            party_type="Customer",
            party_name=self.cust_intra.name,
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=D("100.00"),
            received_amount=D("100.00"),
            reference_no="UTR-999888",
        )
        self.assertTrue(has_proof(pe))
        pe.submit()
        self.assertEqual(pe.docstatus, 1)

    def test_payment_entry_require_proof_true_with_attachment_passes(self):
        pe = PaymentEntry.objects.create(
            name="PE-TEST-004",
            payment_type=PaymentType.RECEIVE,
            posting_date=datetime.date.today(),
            company=self.company,
            party_type="Customer",
            party_name=self.cust_intra.name,
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=D("100.00"),
            received_amount=D("100.00"),
            reference_no="",
        )
        self.create_attachment(pe)
        self.assertTrue(has_proof(pe))
        pe.submit()
        self.assertEqual(pe.docstatus, 1)

    def test_payment_entry_tly_exempt(self):
        pe = PaymentEntry.objects.create(
            name="TLY-PE-001",
            payment_type=PaymentType.RECEIVE,
            posting_date=datetime.date.today(),
            company=self.company,
            party_type="Customer",
            party_name=self.cust_intra.name,
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=D("100.00"),
            received_amount=D("100.00"),
            reference_no="",
        )
        pe.submit()
        self.assertEqual(pe.docstatus, 1)

    # ------------------------------------------------------------- JournalEntry
    def _create_balanced_je(self, name="JV-TEST-001"):
        je = JournalEntry.objects.create(
            name=name,
            posting_date=datetime.date.today(),
            company=self.company,
        )
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=self.bank,
            debit=D("100.00"),
            credit=D("0.00"),
        )
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=self.debtors,
            debit=D("0.00"),
            credit=D("100.00"),
            party_type="Customer",
            party=self.cust_intra.name,
        )
        return je

    def test_journal_entry_without_proof_fails(self):
        je = self._create_balanced_je("JV-TEST-NOPROOF")
        with self.assertRaises(ValidationError) as ctx:
            je.submit()
        self.assertIn(
            "Proof attachment is required before submission.", str(ctx.exception)
        )

    def test_journal_entry_with_attachment_passes(self):
        je = self._create_balanced_je("JV-TEST-WITHPROOF")
        self.create_attachment(je)
        je.submit()
        self.assertEqual(je.docstatus, 1)

    def test_journal_entry_tly_exempt(self):
        je = self._create_balanced_je("TLY-JV-001")
        je.submit()
        self.assertEqual(je.docstatus, 1)

    # ---------------------------------------------------------- PurchaseInvoice
    def _create_draft_pi(self, name=None):
        extra = {}
        if name:
            extra["name"] = name
        body = self.pi_body(
            self.sup_intra,
            self.lines((self.item18, 1, 100)),
            bill_no="BILL-101",
            bill_date="2026-04-01",
            posting_date="2026-04-01",
            due_date="2026-05-01",
            **extra,
        )
        res = self.create(PI_URL, body)
        return self.pi_obj(res)

    def test_purchase_invoice_without_proof_fails(self):
        pinv = self._create_draft_pi()
        with self.assertRaises(ValidationError) as ctx:
            pinv.submit()
        self.assertIn(
            "Proof attachment is required before submission.", str(ctx.exception)
        )

    def test_purchase_invoice_with_attachment_passes(self):
        pinv = self._create_draft_pi()
        self.create_attachment(pinv)
        pinv.submit()
        self.assertEqual(pinv.docstatus, 1)

    def test_purchase_invoice_tly_exempt(self):
        pinv = self._create_draft_pi(name="TLY-PINV-001")
        pinv.submit()
        self.assertEqual(pinv.docstatus, 1)
