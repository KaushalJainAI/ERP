import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from rest_framework.test import APIClient

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    TaxPayment,
)
from accounts.test_outstanding import OutstandingBase
from core.models import Attachment, DocStatus

D = Decimal
TAX_PAYMENT_URL = "/api/accounts/tax-payments/"


class TaxPaymentTests(OutstandingBase):
    def setUp(self):
        super().setUp()
        self.tds_account = Account.objects.create(
            name="TDS Payable",
            company=self.company,
            parent=Account.objects.get(company=self.company, name="Liabilities"),
            root_type=AccountRootType.LIABILITY,
        )

    def create_attachment(self, doc):
        return Attachment.objects.create(
            model_label=doc._meta.label,
            object_id=str(doc.pk),
            original_filename="challan.pdf",
            content_type="application/pdf",
            size=120,
            sha256="1" * 64,
            uploaded_by=self.admin,
            file="attachments/challan.pdf",
        )

    def test_gst_payment_submit_creates_balanced_journal_entry(self):
        tp = TaxPayment.objects.create(
            name="TAX-2026-0001",
            company=self.company,
            tax_type="GST",
            period=datetime.date(2026, 10, 1),
            posting_date=datetime.date(2026, 10, 20),
            challan_no="CH-GST-001",
            paid_from=self.bank,
            igst_cash=D("500.00"),
            cgst_cash=D("250.00"),
            sgst_cash=D("250.00"),
            igst_credit_used=D("100.00"),
            cgst_credit_used=D("50.00"),
            sgst_credit_used=D("50.00"),
        )
        tp.submit()
        self.assertEqual(tp.docstatus, DocStatus.SUBMITTED)
        self.assertIsNotNone(tp.journal_entry)
        self.assertEqual(tp.journal_entry.docstatus, DocStatus.SUBMITTED)
        self.assert_balanced(tp.journal_entry.name)

        # Check GL lines
        gl_rows = {
            (name, dr, cr) for name, dr, cr in self.gl(tp.journal_entry.name)
        }
        # Output tax accounts debited:
        # IGST: 500 cash + 100 credit = 600
        # CGST: 250 cash + 50 credit = 300
        # SGST: 250 cash + 50 credit = 300
        out_igst = self.tag[("Output", "IGST")]
        out_cgst = self.tag[("Output", "CGST")]
        out_sgst = self.tag[("Output", "SGST")]
        in_igst = self.tag[("Input", "IGST")]
        in_cgst = self.tag[("Input", "CGST")]
        in_sgst = self.tag[("Input", "SGST")]

        self.assertIn((out_igst.name, D("500.00"), D("0.00")), gl_rows)
        self.assertIn((out_igst.name, D("100.00"), D("0.00")), gl_rows)
        self.assertIn((out_cgst.name, D("250.00"), D("0.00")), gl_rows)
        self.assertIn((out_cgst.name, D("50.00"), D("0.00")), gl_rows)
        self.assertIn((out_sgst.name, D("250.00"), D("0.00")), gl_rows)
        self.assertIn((out_sgst.name, D("50.00"), D("0.00")), gl_rows)
        # Bank credited 1000 cash
        self.assertIn((self.bank.name, D("0.00"), D("1000.00")), gl_rows)
        # Input tax accounts credited for credit used
        self.assertIn((in_igst.name, D("0.00"), D("100.00")), gl_rows)
        self.assertIn((in_cgst.name, D("0.00"), D("50.00")), gl_rows)
        self.assertIn((in_sgst.name, D("0.00"), D("50.00")), gl_rows)

    def test_gst_payment_cancel_cancels_journal_entry(self):
        tp = TaxPayment.objects.create(
            name="TAX-2026-0002",
            company=self.company,
            tax_type="GST",
            period=datetime.date(2026, 10, 1),
            posting_date=datetime.date(2026, 10, 20),
            challan_no="CH-GST-002",
            paid_from=self.bank,
            igst_cash=D("300.00"),
        )
        tp.submit()
        je = tp.journal_entry
        self.assertEqual(je.docstatus, DocStatus.SUBMITTED)

        tp.cancel()
        self.assertEqual(tp.docstatus, DocStatus.CANCELLED)
        je.refresh_from_db()
        self.assertEqual(je.docstatus, DocStatus.CANCELLED)
        # Live GL rows should now be empty (cancelled)
        self.assertEqual(self.gl(je.name, live_only=True), [])

    def test_tds_payment_submit_and_cancel(self):
        tp = TaxPayment.objects.create(
            name="TAX-2026-0003",
            company=self.company,
            tax_type="TDS",
            period=datetime.date(2026, 10, 1),
            posting_date=datetime.date(2026, 10, 7),
            challan_no="CH-TDS-001",
            paid_from=self.bank,
            tds_amount=D("1200.00"),
        )
        tp.submit()
        self.assertEqual(tp.docstatus, DocStatus.SUBMITTED)
        self.assertIsNotNone(tp.journal_entry)
        self.assertEqual(tp.journal_entry.docstatus, DocStatus.SUBMITTED)
        self.assert_balanced(tp.journal_entry.name)

        gl_rows = set(self.gl(tp.journal_entry.name))
        self.assertIn((self.tds_account.name, D("1200.00"), D("0.00")), gl_rows)
        self.assertIn((self.bank.name, D("0.00"), D("1200.00")), gl_rows)

        tp.cancel()
        self.assertEqual(tp.docstatus, DocStatus.CANCELLED)
        tp.journal_entry.refresh_from_db()
        self.assertEqual(tp.journal_entry.docstatus, DocStatus.CANCELLED)
        self.assertEqual(self.gl(tp.journal_entry.name, live_only=True), [])

    def test_unique_challan_per_period_constraint(self):
        tp1 = TaxPayment.objects.create(
            name="TAX-2026-0004",
            company=self.company,
            tax_type="GST",
            period=datetime.date(2026, 10, 1),
            posting_date=datetime.date(2026, 10, 20),
            challan_no="CH-DUP-001",
            paid_from=self.bank,
            igst_cash=D("100.00"),
        )

        from django.db import transaction

        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                TaxPayment.objects.create(
                    name="TAX-2026-0005",
                    company=self.company,
                    tax_type="GST",
                    period=datetime.date(2026, 10, 1),
                    posting_date=datetime.date(2026, 10, 20),
                    challan_no="CH-DUP-001",
                    paid_from=self.bank,
                    igst_cash=D("200.00"),
                )

        # Once tp1 is cancelled, same challan is allowed
        tp1.submit()
        tp1.cancel()

        tp2 = TaxPayment.objects.create(
            name="TAX-2026-0006",
            company=self.company,
            tax_type="GST",
            period=datetime.date(2026, 10, 1),
            posting_date=datetime.date(2026, 10, 20),
            challan_no="CH-DUP-001",
            paid_from=self.bank,
            igst_cash=D("200.00"),
        )
        self.assertIsNotNone(tp2.pk)

    def test_require_proof_enforcement(self):
        self.company.require_proof = True
        self.company.save(update_fields=["require_proof"])

        tp = TaxPayment.objects.create(
            name="TAX-2026-0007",
            company=self.company,
            tax_type="GST",
            period=datetime.date(2026, 10, 1),
            posting_date=datetime.date(2026, 10, 20),
            challan_no="CH-PROOF-001",
            paid_from=self.bank,
            igst_cash=D("100.00"),
        )

        with self.assertRaises(ValidationError):
            tp.submit()

        # Attach proof and submit
        self.create_attachment(tp)
        tp.submit()
        self.assertEqual(tp.docstatus, DocStatus.SUBMITTED)
        self.assertIsNotNone(tp.journal_entry)
        self.assertEqual(tp.journal_entry.docstatus, DocStatus.SUBMITTED)

    def test_validation_constraints(self):
        # GST payment with TDS amount fails
        with self.assertRaises(ValidationError):
            tp = TaxPayment(
                name="TAX-BAD-1",
                company=self.company,
                tax_type="GST",
                period=datetime.date(2026, 10, 1),
                posting_date=datetime.date(2026, 10, 20),
                challan_no="CH-BAD-1",
                paid_from=self.bank,
                igst_cash=D("100.00"),
                tds_amount=D("50.00"),
            )
            tp.clean()

        # TDS payment with GST amount fails
        with self.assertRaises(ValidationError):
            tp = TaxPayment(
                name="TAX-BAD-2",
                company=self.company,
                tax_type="TDS",
                period=datetime.date(2026, 10, 1),
                posting_date=datetime.date(2026, 10, 20),
                challan_no="CH-BAD-2",
                paid_from=self.bank,
                tds_amount=D("100.00"),
                cgst_cash=D("50.00"),
            )
            tp.clean()

        # All amounts zero fails
        with self.assertRaises(ValidationError):
            tp = TaxPayment(
                name="TAX-BAD-3",
                company=self.company,
                tax_type="GST",
                period=datetime.date(2026, 10, 1),
                posting_date=datetime.date(2026, 10, 20),
                challan_no="CH-BAD-3",
            )
            tp.clean()

        # Cash without paid_from fails
        with self.assertRaises(ValidationError):
            tp = TaxPayment(
                name="TAX-BAD-4",
                company=self.company,
                tax_type="GST",
                period=datetime.date(2026, 10, 1),
                posting_date=datetime.date(2026, 10, 20),
                challan_no="CH-BAD-4",
                igst_cash=D("100.00"),
            )
            tp.clean()

    def test_api_crud_submit_cancel(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)

        # Create
        res = client.post(
            TAX_PAYMENT_URL,
            {
                "company": self.company.id,
                "tax_type": "GST",
                "period": "2026-10-01",
                "posting_date": "2026-10-20",
                "challan_no": "CH-API-001",
                "paid_from": self.bank.id,
                "igst_cash": "400.00",
                "cgst_cash": "200.00",
                "sgst_cash": "200.00",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        tp_id = res.data["id"]

        # Submit
        res = client.post(f"{TAX_PAYMENT_URL}{tp_id}/submit/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["docstatus"], DocStatus.SUBMITTED)
        self.assertIsNotNone(res.data["journal_entry"])

        # Cancel
        res = client.post(f"{TAX_PAYMENT_URL}{tp_id}/cancel/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["docstatus"], DocStatus.CANCELLED)
