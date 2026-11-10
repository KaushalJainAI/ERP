import datetime
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from core.models import Company, DummySubmittableDoc, DocStatus
from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    JournalEntry,
    JournalEntryLine,
    PaymentEntry,
    PaymentType,
)
from accounts.services import gl as gl_service


class AccountsAppTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Acme Trading", abbr="ACME", default_currency="INR"
        )
        self.asset_root = Account.objects.create(
            name="Application of Funds (Assets)",
            company=self.company,
            root_type=AccountRootType.ASSET,
            is_group=True,
        )
        self.bank_acc = Account.objects.create(
            name="HDFC Bank",
            company=self.company,
            parent=self.asset_root,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
            is_group=False,
        )
        self.income_root = Account.objects.create(
            name="Income",
            company=self.company,
            root_type=AccountRootType.INCOME,
            is_group=True,
        )
        self.sales_acc = Account.objects.create(
            name="Sales Income",
            company=self.company,
            parent=self.income_root,
            root_type=AccountRootType.INCOME,
            is_group=False,
        )
        self.doc = DummySubmittableDoc.objects.create(
            name="INV-2026-0001",
            title="Sales Invoice 1",
            docstatus=DocStatus.SUBMITTED,
        )

    # -------------------------------------------------------------------------
    # Good Unit Tests
    # -------------------------------------------------------------------------

    def test_account_creation_success(self):
        """GOOD: Account created successfully under company with unique constraint."""
        acc = Account.objects.create(
            name="Petty Cash",
            company=self.company,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.CASH,
            is_group=False,
        )
        self.assertEqual(acc.company, self.company)
        self.assertEqual(str(acc), "Petty Cash (ACME)")

    def test_gl_post_success(self):
        """GOOD: Posting balanced GL entries creates immutable GLEntry rows."""
        entries = [
            {
                "account": self.bank_acc,
                "debit": Decimal("5000.00"),
                "credit": Decimal("0.00"),
            },
            {
                "account": self.sales_acc,
                "debit": Decimal("0.00"),
                "credit": Decimal("5000.00"),
            },
        ]
        gl_rows = gl_service.post(
            voucher=self.doc,
            entries=entries,
            posting_date=datetime.date(2026, 4, 1),
        )
        self.assertEqual(len(gl_rows), 2)
        self.assertEqual(GLEntry.objects.filter(voucher_no="INV-2026-0001").count(), 2)

    def test_gl_reversal_success(self):
        """GOOD: Reversing a GL voucher posts reversing entries with swapped debit/credit and is_cancelled=True."""
        entries = [
            {
                "account": self.bank_acc,
                "debit": Decimal("1000.00"),
                "credit": Decimal("0.00"),
            },
            {
                "account": self.sales_acc,
                "debit": Decimal("0.00"),
                "credit": Decimal("1000.00"),
            },
        ]
        gl_service.post(
            voucher=self.doc, entries=entries, posting_date=datetime.date(2026, 4, 1)
        )

        # Perform reversal
        reversals = gl_service.reverse(self.doc)
        self.assertEqual(len(reversals), 2)

        # Check total rows (2 original + 2 reversals = 4)
        total_rows = GLEntry.objects.filter(voucher_no="INV-2026-0001")
        self.assertEqual(total_rows.count(), 4)

        # Total active sum is zeroed out
        dr_sum = sum(r.debit for r in total_rows)
        cr_sum = sum(r.credit for r in total_rows)
        self.assertEqual(dr_sum, cr_sum)

    def test_gl_repost_success(self):
        """GOOD: Reposting GL verifies ledger balance consistency."""
        entries = [
            {
                "account": self.bank_acc,
                "debit": Decimal("2500.00"),
                "credit": Decimal("0.00"),
            },
            {
                "account": self.sales_acc,
                "debit": Decimal("0.00"),
                "credit": Decimal("2500.00"),
            },
        ]
        gl_service.post(
            voucher=self.doc, entries=entries, posting_date=datetime.date(2026, 4, 1)
        )
        self.assertTrue(gl_service.repost(company_id=self.company.id))

    # -------------------------------------------------------------------------
    # Bad / Error Unit Tests
    # -------------------------------------------------------------------------

    def test_duplicate_account_name_in_same_company_fails(self):
        """BAD: Creating duplicate account name in same company violates UNIQUE constraint."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Account.objects.create(
                    name="HDFC Bank",
                    company=self.company,
                    root_type=AccountRootType.ASSET,
                )

    def test_gl_post_unbalanced_fails(self):
        """BAD: Posting GL entries where total debit != total credit raises ValidationError."""
        entries = [
            {
                "account": self.bank_acc,
                "debit": Decimal("5000.00"),
                "credit": Decimal("0.00"),
            },
            {
                "account": self.sales_acc,
                "debit": Decimal("0.00"),
                "credit": Decimal("4000.00"),
            },  # Unbalanced
        ]
        with self.assertRaises(ValidationError):
            gl_service.post(
                voucher=self.doc,
                entries=entries,
                posting_date=datetime.date(2026, 4, 1),
            )

    def test_gl_post_to_group_account_fails(self):
        """BAD: Posting GL entries to a group account (is_group=True) raises ValidationError."""
        entries = [
            {
                "account": self.asset_root,
                "debit": Decimal("1000.00"),
                "credit": Decimal("0.00"),
            },  # Group node
            {
                "account": self.sales_acc,
                "debit": Decimal("0.00"),
                "credit": Decimal("1000.00"),
            },
        ]
        with self.assertRaises(ValidationError):
            gl_service.post(
                voucher=self.doc,
                entries=entries,
                posting_date=datetime.date(2026, 4, 1),
            )

    def test_gl_entry_immutability_update_and_delete_forbidden(self):
        """BAD: Updating or deleting an existing GLEntry raises ValidationError."""
        entries = [
            {
                "account": self.bank_acc,
                "debit": Decimal("500.00"),
                "credit": Decimal("0.00"),
            },
            {
                "account": self.sales_acc,
                "debit": Decimal("0.00"),
                "credit": Decimal("500.00"),
            },
        ]
        rows = gl_service.post(
            voucher=self.doc, entries=entries, posting_date=datetime.date(2026, 4, 1)
        )
        gl_row = rows[0]

        # BAD: Updating GLEntry directly
        gl_row.debit = Decimal("999.00")
        with self.assertRaises(ValidationError):
            gl_row.save()

        # BAD: Deleting GLEntry directly
        with self.assertRaises(ValidationError):
            gl_row.delete()

    def test_journal_entry_submit_and_cancel_flow(self):
        """GOOD: Submitting JournalEntry posts balanced GLEntry rows; cancelling reverses them."""
        jv = JournalEntry.objects.create(
            name="JV-TEST-001",
            posting_date=datetime.date(2026, 4, 1),
            company=self.company,
            user_remark="Opening balance",
        )
        JournalEntryLine.objects.create(
            journal_entry=jv,
            account=self.bank_acc,
            debit=Decimal("3000.00"),
            credit=Decimal("0.00"),
        )
        JournalEntryLine.objects.create(
            journal_entry=jv,
            account=self.sales_acc,
            debit=Decimal("0.00"),
            credit=Decimal("3000.00"),
        )

        # Submit JV
        jv.submit()
        self.assertEqual(jv.docstatus, DocStatus.SUBMITTED)
        self.assertEqual(GLEntry.objects.filter(voucher_no="JV-TEST-001").count(), 2)

        # Cancel JV
        jv.cancel()
        self.assertEqual(jv.docstatus, DocStatus.CANCELLED)
        self.assertEqual(GLEntry.objects.filter(voucher_no="JV-TEST-001").count(), 4)

    def test_journal_entry_totals_are_derived_from_lines(self):
        """GOOD: header totals follow the lines -- on API create and on submit,
        even if the lines were written without the serializer (ORM/imports)."""
        from rest_framework.test import APIClient
        from users.models import User

        user = User.objects.create_superuser(
            username="admin_je", email="admin_je@test.com", password="password"
        )
        client = APIClient()
        client.force_authenticate(user=user)
        res = client.post(
            "/api/accounts/journal-entries/",
            {
                "name": "JV-API-001",
                "posting_date": "2026-04-01",
                "company": self.company.id,
                "lines": [
                    {"account": self.bank_acc.id, "debit": "500.00", "credit": "0.00"},
                    {"account": self.sales_acc.id, "debit": "0.00", "credit": "500.00"},
                ],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["total_debit"], "500.00")
        self.assertEqual(res.data["total_credit"], "500.00")

        # Lines written straight through the ORM leave the header at zero ...
        jv = JournalEntry.objects.create(
            name="JV-ORM-001",
            posting_date=datetime.date(2026, 4, 1),
            company=self.company,
        )
        JournalEntryLine.objects.create(
            journal_entry=jv,
            account=self.bank_acc,
            debit=Decimal("750.00"),
            credit=Decimal("0.00"),
        )
        JournalEntryLine.objects.create(
            journal_entry=jv,
            account=self.sales_acc,
            debit=Decimal("0.00"),
            credit=Decimal("750.00"),
        )
        self.assertEqual(jv.total_debit, Decimal("0.00"))
        # ... until submit re-derives and persists them.
        jv.submit()
        jv.refresh_from_db()
        self.assertEqual(jv.total_debit, Decimal("750.00"))
        self.assertEqual(jv.total_credit, Decimal("750.00"))

    def test_journal_entry_unbalanced_submit_fails(self):
        """BAD: Submitting unbalanced JournalEntry raises ValidationError."""
        jv = JournalEntry.objects.create(
            name="JV-BAD-001",
            posting_date=datetime.date(2026, 4, 1),
            company=self.company,
        )
        JournalEntryLine.objects.create(
            journal_entry=jv,
            account=self.bank_acc,
            debit=Decimal("3000.00"),
            credit=Decimal("0.00"),
        )
        JournalEntryLine.objects.create(
            journal_entry=jv,
            account=self.sales_acc,
            debit=Decimal("0.00"),
            credit=Decimal("2000.00"),
        )

        with self.assertRaises(ValidationError):
            jv.submit()

    def test_trial_balance_report_endpoint(self):
        """GOOD: Trial balance report endpoint calculates balanced debit and credit totals."""
        from rest_framework.test import APIClient
        from users.models import User

        user = User.objects.create_superuser(
            username="admin_tb", email="admin_tb@test.com", password="password"
        )
        client = APIClient()
        client.force_authenticate(user=user)

        entries = [
            {
                "account": self.bank_acc,
                "debit": Decimal("12500.00"),
                "credit": Decimal("0.00"),
            },
            {
                "account": self.sales_acc,
                "debit": Decimal("0.00"),
                "credit": Decimal("12500.00"),
            },
        ]
        gl_service.post(
            voucher=self.doc, entries=entries, posting_date=datetime.date(2026, 4, 1)
        )

        res = client.get(
            f"/api/accounts/reports/trial-balance/?company={self.company.id}"
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["is_balanced"])
        self.assertEqual(res.data["total_debit"], "12500.00")
        self.assertEqual(res.data["total_credit"], "12500.00")

    def test_payment_entry_submit_and_cancel_flow(self):
        """GOOD: Submitting PaymentEntry posts GL entries (Debit paid_to, Credit paid_from); cancel reverses."""
        pe = PaymentEntry.objects.create(
            name="PE-2026-0001",
            payment_type=PaymentType.RECEIVE,
            posting_date=datetime.date(2026, 4, 1),
            company=self.company,
            party_type="Customer",
            party_name="Acme Client",
            paid_from=self.sales_acc,
            paid_to=self.bank_acc,
            paid_amount=Decimal("4500.00"),
        )
        pe.submit()
        self.assertEqual(pe.docstatus, DocStatus.SUBMITTED)
        self.assertEqual(
            GLEntry.objects.filter(
                voucher_no="PE-2026-0001", is_cancelled=False
            ).count(),
            2,
        )

        pe.cancel()
        self.assertEqual(pe.docstatus, DocStatus.CANCELLED)
        self.assertEqual(GLEntry.objects.filter(voucher_no="PE-2026-0001").count(), 4)

    def test_payment_entry_invalid_amount_fails(self):
        """BAD: A PaymentEntry with a zero or negative paid_amount cannot be saved.
        The DB CHECK (accounts_paymententry_paid_amount_gt_0, planning S-3) now refuses
        the row before submit() can see it; submit()'s own check is the second line."""
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentEntry.objects.create(
                name="PE-BAD-001",
                payment_type=PaymentType.RECEIVE,
                posting_date=datetime.date(2026, 4, 1),
                company=self.company,
                paid_from=self.sales_acc,
                paid_to=self.bank_acc,
                paid_amount=Decimal("0.00"),
            )

    def test_financial_reports_endpoints(self):
        """GOOD: Financial report endpoints (GL, P&L, Balance Sheet) return accurate aggregate metrics."""
        from rest_framework.test import APIClient
        from users.models import User

        user = User.objects.create_superuser(
            username="admin_reports", email="admin_rep@test.com", password="password"
        )
        client = APIClient()
        client.force_authenticate(user=user)

        entries = [
            {
                "account": self.bank_acc,
                "debit": Decimal("10000.00"),
                "credit": Decimal("0.00"),
            },
            {
                "account": self.sales_acc,
                "debit": Decimal("0.00"),
                "credit": Decimal("10000.00"),
            },
        ]
        gl_service.post(
            voucher=self.doc, entries=entries, posting_date=datetime.date(2026, 4, 1)
        )

        # 1. General Ledger
        gl_res = client.get(
            f"/api/accounts/reports/general-ledger/?company={self.company.id}"
        )
        self.assertEqual(gl_res.status_code, 200)
        self.assertEqual(len(gl_res.data["entries"]), 2)

        # 2. Profit and Loss
        pnl_res = client.get(
            f"/api/accounts/reports/profit-and-loss/?company={self.company.id}"
        )
        self.assertEqual(pnl_res.status_code, 200)
        self.assertEqual(pnl_res.data["total_income"], "10000.00")

        # 3. Balance Sheet
        bs_res = client.get(
            f"/api/accounts/reports/balance-sheet/?company={self.company.id}"
        )
        self.assertEqual(bs_res.status_code, 200)
        self.assertEqual(bs_res.data["total_assets"], "10000.00")
