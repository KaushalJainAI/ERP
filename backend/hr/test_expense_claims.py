"""Employee expense claims and reimbursements (ERPNext "Expense Claim").

Run with ``pytest hr/test_expense_claims.py``.  Dates are in April 2026, like the
other HR tests, inside the seeded fiscal year.
"""

import datetime
from decimal import Decimal as D
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Account, AccountRootType, AccountType, GLEntry
from buying.models import ExpenseCategory
from core.models import Attachment, Company, DocStatus
from hr.models import (
    Employee,
    EmployeeStatus,
    ExpenseClaim,
    ExpenseClaimDetail,
    ExpenseReimbursement,
    HRSettings,
    PaymentMode,
    PayType,
)
from hr.services import expense_claims as claim_service
from hr.services import integrity
from hr.services.setup import ensure_hr_setup
from users.models import User

d = datetime.date
API = "/api/hr/"
CLAIM = "hr.ExpenseClaim"
REIMB = "hr.ExpenseReimbursement"


class ExpenseBase(TestCase):
    def setUp(self):
        self._n = 0
        self.company = Company.objects.create(name="Spice Expense", abbr="SPE")
        assets = Account.objects.create(
            company=self.company,
            name="Assets",
            root_type=AccountRootType.ASSET,
            is_group=True,
        )
        Account.objects.create(
            company=self.company,
            name="Liabilities",
            root_type=AccountRootType.LIABILITY,
            is_group=True,
        )
        expenses = Account.objects.create(
            company=self.company,
            name="Expenses",
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.cash = Account.objects.create(
            company=self.company,
            name="Cash Account",
            parent=assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.CASH,
        )
        self.bank = Account.objects.create(
            company=self.company,
            name="Bank Account",
            parent=assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
        )
        self.cfg = ensure_hr_setup(self.company)
        self.payable = self.cfg.expense_claim_payable_account
        self.diesel_acct = Account.objects.create(
            company=self.company,
            name="Diesel Expense",
            parent=expenses,
            root_type=AccountRootType.EXPENSE,
        )
        self.courier_acct = Account.objects.create(
            company=self.company,
            name="Courier Expense",
            parent=expenses,
            root_type=AccountRootType.EXPENSE,
        )
        self.diesel = ExpenseCategory.objects.create(
            company=self.company, name="Diesel", account=self.diesel_acct
        )
        self.courier = ExpenseCategory.objects.create(
            company=self.company, name="Courier", account=self.courier_acct
        )
        self.emp = self.employee(self.company, "Ravi")
        self.admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="x"
        )
        self.api = APIClient()
        self.api.force_authenticate(self.admin)
        self.api.credentials(HTTP_X_COMPANY_ID=str(self.company.pk))

    # ------------------------------------------------------------- helpers
    def next_no(self):
        self._n += 1
        return self._n

    def employee(self, company, first_name):
        n = self.next_no()
        return Employee.objects.create(
            name=f"EMP-E{n}",
            first_name=first_name,
            company=company,
            date_of_joining=d(2026, 1, 1),
            pay_type=PayType.MONTHLY,
        )

    def attach_proof(self, doc):
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

    def claim(
        self, lines, submit=True, proof=True, employee=None, posting=d(2026, 4, 10)
    ):
        """A claim built with the ORM.  ``lines``: (category, claimed, sanctioned)."""
        claim = ExpenseClaim.objects.create(
            name=f"TEST-CLM-{self.next_no()}",
            company=self.company,
            employee=employee or self.emp,
            posting_date=posting,
            payable_account=self.payable,
        )
        for idx, (category, amount, sanctioned) in enumerate(lines, start=1):
            ExpenseClaimDetail.objects.create(
                claim=claim,
                idx=idx,
                expense_date=d(2026, 4, 3),
                expense_category=category,
                description=f"Expense {idx}",
                amount=D(amount),
                sanctioned_amount=D(sanctioned),
            )
        claim_service.rebuild_claim(claim)
        if proof:
            self.attach_proof(claim)
        if submit:
            claim.submit()
        return claim

    def reimburse(self, claim, amount, paid_from=None, submit=True):
        reimbursement = ExpenseReimbursement.objects.create(
            name=f"TEST-REIMB-{self.next_no()}",
            company=self.company,
            employee=claim.employee,
            expense_claim=claim,
            posting_date=d(2026, 4, 20),
            amount=D(amount),
            mode=PaymentMode.CASH,
            paid_from=paid_from or self.cash,
        )
        if submit:
            reimbursement.submit()
        return reimbursement

    def gl(self, voucher_type, name):
        return GLEntry.objects.filter(
            voucher_type=voucher_type, voucher_no=name, is_cancelled=False
        )

    def hr_errors(self):
        return integrity.check()[0]

    def payload(self, lines, **overrides):
        data = {
            "employee": self.emp.pk,
            "company": self.company.pk,
            "posting_date": "2026-04-10",
            "remarks": "Diesel for the generator",
            "lines": lines,
        }
        data.update(overrides)
        return data

    def line(self, category, amount, sanctioned=None, expense_date="2026-04-03"):
        row = {
            "expense_date": expense_date,
            "expense_category": category.pk,
            "description": "Diesel can",
            "amount": amount,
        }
        if sanctioned is not None:
            row["sanctioned_amount"] = sanctioned
        return row


class ClaimPostingTests(ExpenseBase):
    def test_submit_posts_category_accounts_and_payable_for_the_employee(self):
        claim = self.claim(
            [(self.diesel, "1200.00", "1200.00"), (self.courier, "300.00", "250.00")]
        )
        self.assertEqual(claim.docstatus, DocStatus.SUBMITTED)
        self.assertEqual(claim.name[:8], "TEST-CLM")
        rows = list(self.gl(CLAIM, claim.name))
        debit_by_account = {}
        for row in rows:
            debit_by_account[row.account_id] = (
                debit_by_account.get(row.account_id, D("0")) + row.debit
            )
        self.assertEqual(debit_by_account[self.diesel_acct.pk], D("1200.00"))
        self.assertEqual(debit_by_account[self.courier_acct.pk], D("250.00"))
        payable = [r for r in rows if r.account_id == self.payable.pk]
        self.assertEqual(len(payable), 1)
        self.assertEqual(payable[0].credit, D("1450.00"))
        self.assertEqual(
            (payable[0].party_type, payable[0].party), ("Employee", self.emp.name)
        )
        claim.refresh_from_db()
        self.assertEqual(claim.total_claimed, D("1500.00"))
        self.assertEqual(claim.total_sanctioned, D("1450.00"))
        self.assertEqual(claim.outstanding_amount, D("1450.00"))
        self.assertEqual(claim_service.status_of(claim), "Unpaid")
        self.assertEqual(self.hr_errors(), [])

    def test_naming_series_is_used_when_no_name_is_sent(self):
        response = self.api.post(
            API + "expense-claims/",
            self.payload([self.line(self.diesel, "100.00")]),
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()["name"].startswith("EXP-CLM-"))
        self.assertEqual(response.json()["payable_account"], self.payable.pk)
        self.assertEqual(response.json()["total_sanctioned"], "100.00")

    def test_proof_is_required_before_submit(self):
        claim = self.claim(
            [(self.diesel, "500.00", "500.00")], submit=False, proof=False
        )
        with self.assertRaisesRegex(ValidationError, "Proof attachment is required"):
            claim.submit()
        claim.refresh_from_db()
        self.assertEqual(claim.docstatus, DocStatus.DRAFT)
        self.attach_proof(claim)
        claim.submit()
        self.assertEqual(claim.docstatus, DocStatus.SUBMITTED)

    def test_submit_needs_a_line_and_a_positive_sanctioned_total(self):
        empty = self.claim([], submit=False)
        with self.assertRaisesRegex(ValidationError, "at least one expense"):
            empty.submit()
        zero = self.claim([(self.diesel, "500.00", "0.00")], submit=False)
        with self.assertRaisesRegex(ValidationError, "greater than zero"):
            zero.submit()

    def test_sanctioned_above_claimed_is_refused_by_api(self):
        response = self.api.post(
            API + "expense-claims/",
            self.payload([self.line(self.diesel, "100.00", sanctioned="150.00")]),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("sanctioned_amount", response.json()["lines"][0])

    def test_expense_date_after_posting_date_is_refused(self):
        response = self.api.post(
            API + "expense-claims/",
            self.payload([self.line(self.diesel, "100.00", expense_date="2026-05-01")]),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("after the posting date", str(response.json()))

    def test_inactive_employee_is_refused(self):
        self.emp.status = EmployeeStatus.INACTIVE
        self.emp.save()
        response = self.api.post(
            API + "expense-claims/",
            self.payload([self.line(self.diesel, "100.00")]),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("not active", str(response.json()))

    def test_api_submit_and_cancel_round_trip(self):
        response = self.api.post(
            API + "expense-claims/",
            self.payload([self.line(self.diesel, "400.00")]),
            format="json",
        )
        pk = response.json()["id"]
        self.attach_proof(ExpenseClaim.objects.get(pk=pk))
        submitted = self.api.post(f"{API}expense-claims/{pk}/submit/")
        self.assertEqual(submitted.status_code, 200, submitted.content)
        self.assertEqual(submitted.json()["status"], "Unpaid")
        self.assertEqual(submitted.json()["outstanding_amount"], "400.00")
        cancelled = self.api.post(f"{API}expense-claims/{pk}/cancel/")
        self.assertEqual(cancelled.status_code, 200, cancelled.content)
        claim = ExpenseClaim.objects.get(pk=pk)
        self.assertEqual(claim.outstanding_amount, D("0.00"))
        self.assertFalse(self.gl(CLAIM, claim.name).exists())
        self.assertEqual(self.hr_errors(), [])


class ReimbursementTests(ExpenseBase):
    def test_reimbursement_posts_and_reduces_outstanding(self):
        claim = self.claim([(self.diesel, "1000.00", "1000.00")])
        reimbursement = self.reimburse(claim, "600.00")
        rows = list(self.gl(REIMB, reimbursement.name))
        payable = [r for r in rows if r.account_id == self.payable.pk]
        self.assertEqual(payable[0].debit, D("600.00"))
        self.assertEqual(
            (payable[0].party_type, payable[0].party), ("Employee", self.emp.name)
        )
        cash = [r for r in rows if r.account_id == self.cash.pk]
        self.assertEqual(cash[0].credit, D("600.00"))
        claim.refresh_from_db()
        self.assertEqual(claim.outstanding_amount, D("400.00"))
        self.assertEqual(claim_service.status_of(claim), "Partly Paid")
        self.reimburse(claim, "400.00")
        claim.refresh_from_db()
        self.assertEqual(claim.outstanding_amount, D("0.00"))
        self.assertEqual(claim_service.status_of(claim), "Paid")
        self.assertEqual(self.hr_errors(), [])

    def test_over_reimbursement_is_refused(self):
        claim = self.claim([(self.diesel, "1000.00", "900.00")])
        with self.assertRaisesRegex(ValidationError, "exceeds the outstanding"):
            self.reimburse(claim, "900.01")
        claim.refresh_from_db()
        self.assertEqual(claim.outstanding_amount, D("900.00"))

    def test_two_reimbursements_cannot_exceed_the_claim(self):
        claim = self.claim([(self.diesel, "1000.00", "1000.00")])
        self.reimburse(claim, "600.00")
        with self.assertRaisesRegex(ValidationError, "exceeds the outstanding"):
            self.reimburse(claim, "600.00")
        self.reimburse(claim, "400.00")
        claim.refresh_from_db()
        self.assertEqual(claim.outstanding_amount, D("0.00"))
        self.assertEqual(self.hr_errors(), [])

    def test_cancelling_a_reimbursement_restores_outstanding(self):
        claim = self.claim([(self.diesel, "1000.00", "1000.00")])
        reimbursement = self.reimburse(claim, "600.00")
        reimbursement.cancel()
        claim.refresh_from_db()
        self.assertEqual(claim.outstanding_amount, D("1000.00"))
        self.assertFalse(self.gl(REIMB, reimbursement.name).exists())
        self.assertEqual(self.hr_errors(), [])

    def test_claim_cannot_be_cancelled_while_a_reimbursement_is_live(self):
        claim = self.claim([(self.diesel, "1000.00", "1000.00")])
        reimbursement = self.reimburse(claim, "200.00")
        with self.assertRaisesRegex(ValidationError, "reimbursements"):
            claim.cancel()
        claim.refresh_from_db()
        self.assertEqual(claim.docstatus, DocStatus.SUBMITTED)
        reimbursement.cancel()
        claim.cancel()
        claim.refresh_from_db()
        self.assertEqual(claim.docstatus, DocStatus.CANCELLED)
        self.assertEqual(claim.outstanding_amount, D("0.00"))
        self.assertEqual(self.hr_errors(), [])

    def test_reimbursement_for_another_employee_is_refused(self):
        claim = self.claim([(self.diesel, "300.00", "300.00")])
        other = self.employee(self.company, "Meena")
        reimbursement = ExpenseReimbursement(
            name="TEST-REIMB-OTHER",
            company=self.company,
            employee=other,
            expense_claim=claim,
            posting_date=d(2026, 4, 20),
            amount=D("100.00"),
            paid_from=self.cash,
        )
        reimbursement.save()
        with self.assertRaisesRegex(ValidationError, "another employee"):
            reimbursement.submit()

    def test_draft_claim_cannot_be_reimbursed(self):
        claim = self.claim([(self.diesel, "300.00", "300.00")], submit=False)
        with self.assertRaisesRegex(ValidationError, "Only a submitted expense claim"):
            self.reimburse(claim, "100.00")

    def test_reimbursement_api_round_trip(self):
        claim = self.claim([(self.diesel, "700.00", "700.00")])
        response = self.api.post(
            API + "expense-reimbursements/",
            {
                "employee": self.emp.pk,
                "company": self.company.pk,
                "expense_claim": claim.pk,
                "posting_date": "2026-04-20",
                "amount": "250.00",
                "mode": PaymentMode.BANK,
                "paid_from": self.bank.pk,
                "reference_no": "UTR123",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertTrue(body["name"].startswith("EXP-REIMB-"))
        self.assertEqual(body["expense_claim_name"], claim.name)
        submitted = self.api.post(f"{API}expense-reimbursements/{body['id']}/submit/")
        self.assertEqual(submitted.status_code, 200, submitted.content)
        claim.refresh_from_db()
        self.assertEqual(claim.outstanding_amount, D("450.00"))

    def test_wrong_company_paid_from_is_refused(self):
        other = Company.objects.create(name="Other Co", abbr="OTH")
        other_assets = Account.objects.create(
            company=other, name="Assets", root_type=AccountRootType.ASSET, is_group=True
        )
        other_cash = Account.objects.create(
            company=other,
            name="Cash",
            parent=other_assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.CASH,
        )
        claim = self.claim([(self.diesel, "300.00", "300.00")])
        response = self.api.post(
            API + "expense-reimbursements/",
            {
                "employee": self.emp.pk,
                "company": self.company.pk,
                "expense_claim": claim.pk,
                "posting_date": "2026-04-20",
                "amount": "100.00",
                "mode": PaymentMode.CASH,
                "paid_from": other_cash.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("paid_from", response.json())


class WrongCompanyTests(ExpenseBase):
    def setUp(self):
        super().setUp()
        self.other = Company.objects.create(name="Other Firm", abbr="OFI")
        other_expenses = Account.objects.create(
            company=self.other,
            name="Expenses",
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.other_expense_acct = Account.objects.create(
            company=self.other,
            name="Other Expense",
            parent=other_expenses,
            root_type=AccountRootType.EXPENSE,
        )
        self.other_category = ExpenseCategory.objects.create(
            company=self.other, name="Other Cat", account=self.other_expense_acct
        )
        self.other_emp = self.employee(self.other, "Asha")

    def test_employee_of_another_company_is_refused(self):
        response = self.api.post(
            API + "expense-claims/",
            self.payload(
                [self.line(self.diesel, "100.00")], employee=self.other_emp.pk
            ),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("different company", str(response.json()))

    def test_category_of_another_company_is_refused(self):
        response = self.api.post(
            API + "expense-claims/",
            self.payload([self.line(self.other_category, "100.00")]),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("another company", str(response.json()))

    def test_payable_account_of_another_company_is_refused(self):
        other_liab = Account.objects.create(
            company=self.other, name="Other Liab", root_type=AccountRootType.LIABILITY
        )
        response = self.api.post(
            API + "expense-claims/",
            self.payload(
                [self.line(self.diesel, "100.00")], payable_account=other_liab.pk
            ),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("payable_account", response.json())

    def test_claim_with_a_foreign_category_cannot_submit(self):
        claim = self.claim([(self.diesel, "100.00", "100.00")], submit=False)
        ExpenseClaimDetail.objects.filter(claim=claim).update(
            expense_category=self.other_category
        )
        with self.assertRaisesRegex(ValidationError, "another company"):
            claim.submit()

    def test_reimbursement_against_another_company_claim_is_refused(self):
        claim = self.claim([(self.diesel, "100.00", "100.00")])
        other_cash = Account.objects.create(
            company=self.other,
            name="Other Cash",
            root_type=AccountRootType.ASSET,
            account_type=AccountType.CASH,
        )
        reimbursement = ExpenseReimbursement(
            name="TEST-REIMB-X",
            company=self.other,
            employee=claim.employee,
            expense_claim=claim,
            posting_date=d(2026, 4, 20),
            amount=D("50.00"),
            paid_from=other_cash,
        )
        reimbursement.save()
        with self.assertRaisesRegex(ValidationError, "different company"):
            reimbursement.submit()


class SettingsLockTests(ExpenseBase):
    def test_payable_account_cannot_change_once_a_claim_is_posted(self):
        self.claim([(self.diesel, "100.00", "100.00")])
        other_liab = Account.objects.create(
            company=self.company,
            name="New Payable",
            root_type=AccountRootType.LIABILITY,
        )
        self.cfg.expense_claim_payable_account = other_liab
        with self.assertRaisesRegex(ValidationError, "cannot be changed"):
            self.cfg.save()
        stored = HRSettings.objects.get(pk=self.cfg.pk)
        self.assertEqual(stored.expense_claim_payable_account_id, self.payable.pk)


class OwedEndpointTests(ExpenseBase):
    def test_owed_totals_per_employee(self):
        meena = self.employee(self.company, "Meena")
        claim_a = self.claim([(self.diesel, "1000.00", "900.00")])
        self.reimburse(claim_a, "300.00")
        self.claim([(self.courier, "500.00", "500.00")], employee=meena)
        self.claim([(self.courier, "800.00", "800.00")], submit=False, employee=meena)

        response = self.api.get(API + "expense-claims/owed/")
        self.assertEqual(response.status_code, 200, response.content)
        rows = {row["employee"]: row for row in response.json()}
        self.assertEqual(set(rows), {self.emp.pk, meena.pk})
        self.assertEqual(
            rows[self.emp.pk],
            {
                "employee": self.emp.pk,
                "employee_name": self.emp.employee_name,
                "claimed": "1000.00",
                "sanctioned": "900.00",
                "reimbursed": "300.00",
                "outstanding": "600.00",
            },
        )
        self.assertEqual(rows[meena.pk]["outstanding"], "500.00")
        self.assertEqual(rows[meena.pk]["reimbursed"], "0.00")

        filtered = self.api.get(API + "expense-claims/owed/", {"employee": meena.pk})
        self.assertEqual([row["employee"] for row in filtered.json()], [meena.pk])


class IntegrityAndDatabaseTests(ExpenseBase):
    def test_integrity_detects_drifted_outstanding_and_repost_repairs_it(self):
        claim = self.claim([(self.diesel, "1000.00", "1000.00")])
        self.reimburse(claim, "300.00")
        self.assertEqual(self.hr_errors(), [])

        ExpenseClaim.objects.filter(pk=claim.pk).update(outstanding_amount=D("900.00"))
        errors = self.hr_errors()
        self.assertTrue(
            any(claim.name in e and "stored outstanding" in e for e in errors), errors
        )

        call_command("repost_hr", stdout=StringIO(), stderr=StringIO())
        claim.refresh_from_db()
        self.assertEqual(claim.outstanding_amount, D("700.00"))
        self.assertEqual(self.hr_errors(), [])

    def test_repost_rebuilds_totals_of_a_draft(self):
        claim = self.claim([(self.diesel, "200.00", "150.00")], submit=False)
        # Drift that the CHECK constraints still allow: 160 claimed, 200 on the lines.
        ExpenseClaim.objects.filter(pk=claim.pk).update(total_claimed=D("160.00"))
        self.assertTrue(any(claim.name in e for e in self.hr_errors()))
        call_command("repost_hr", stdout=StringIO(), stderr=StringIO())
        claim.refresh_from_db()
        self.assertEqual(claim.total_claimed, D("200.00"))
        self.assertEqual(claim.total_sanctioned, D("150.00"))
        self.assertEqual(self.hr_errors(), [])

    def test_database_refuses_bad_claim_rows(self):
        base = dict(
            company=self.company,
            employee=self.emp,
            posting_date=d(2026, 4, 10),
            payable_account=self.payable,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExpenseClaim.objects.create(name="DB-1", total_claimed=D("-1"), **base)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExpenseClaim.objects.create(
                    name="DB-2",
                    total_claimed=D("100"),
                    total_sanctioned=D("150"),
                    **base,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExpenseClaim.objects.create(
                    name="DB-3",
                    docstatus=DocStatus.SUBMITTED,
                    total_claimed=D("100"),
                    total_sanctioned=D("0"),
                    **base,
                )

        claim = self.claim([(self.diesel, "100.00", "80.00")])
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExpenseClaim.objects.filter(pk=claim.pk).update(
                    outstanding_amount=D("81.00")
                )

    def test_database_refuses_bad_line_and_reimbursement_rows(self):
        claim = self.claim([(self.diesel, "100.00", "100.00")], submit=False)
        common = dict(
            claim=claim,
            expense_date=d(2026, 4, 3),
            expense_category=self.diesel,
            description="x",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExpenseClaimDetail.objects.create(
                    idx=5, amount=D("0"), sanctioned_amount=D("0"), **common
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExpenseClaimDetail.objects.create(
                    idx=6, amount=D("50"), sanctioned_amount=D("60"), **common
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExpenseReimbursement.objects.create(
                    name="DB-R1",
                    company=self.company,
                    employee=self.emp,
                    expense_claim=claim,
                    posting_date=d(2026, 4, 20),
                    amount=D("0"),
                    paid_from=self.cash,
                )

    def test_hr_settings_expense_payable_must_be_a_liability(self):
        from hr.serializers import HRSettingsSerializer

        serializer = HRSettingsSerializer(
            instance=self.cfg,
            data={
                "company": self.company.pk,
                "wage_expense_account": self.cfg.wage_expense_account_id,
                "payroll_payable_account": self.cfg.payroll_payable_account_id,
                "employee_advance_account": self.cfg.employee_advance_account_id,
                "default_payment_account": self.cfg.default_payment_account_id,
                "expense_claim_payable_account": self.cash.pk,
            },
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("expense_claim_payable_account", serializer.errors)

    def test_draft_totals_and_status_helpers(self):
        claim = self.claim([(self.diesel, "100.00", "90.00")], submit=False)
        self.assertEqual(claim_service.status_of(claim), "Draft")
        self.assertEqual(claim.total_sanctioned, D("90.00"))
        self.assertEqual(claim.outstanding_amount, D("0.00"))
        self.assertEqual(claim_service.repost_claims(self.company.pk), 0)


class TallyExportTests(ExpenseBase):
    """Expense claims post GL, so Tally gets them as balanced Journal vouchers.  They
    have no mapping of their own, like the other HR documents (salary, advances)."""

    def test_claims_and_reimbursements_export_as_balanced_journals(self):
        from tally_export.services import export as tally_export

        claim = self.claim(
            [(self.diesel, "1200.00", "1200.00"), (self.courier, "300.00", "250.00")]
        )
        reimbursement = self.reimburse(claim, "600.00")
        plan = tally_export.make_plan(self.company, include_exported=True)
        drafts = {(draft.doctype, draft.name): draft for draft in plan.drafts}
        for key in ((CLAIM, claim.name), (REIMB, reimbursement.name)):
            self.assertIn(key, drafts)
            draft = drafts[key]
            self.assertEqual(draft.tally_type, "Journal")
            self.assertEqual(draft.problems, [])
            self.assertTrue(draft.balanced)
        self.assertEqual(plan.unbalanced, [])
