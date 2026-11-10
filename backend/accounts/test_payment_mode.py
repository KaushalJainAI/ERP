"""How the money moved: PaymentEntry.mode defaults the bank-side account, a cheque
needs its number, and "Cash put in bank" is an internal transfer from cash to bank."""

import datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from rest_framework import status

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    JournalEntry,
    PaymentEntry,
    PaymentMode,
)
from core.models import FiscalYear
from core.test_e2e import BaseAPITest

URL = "/api/accounts/payment-entries/"


class PaymentModeTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        FiscalYear.objects.get_or_create(
            name="FY2026-27",
            defaults={
                "start_date": datetime.date(2026, 4, 1),
                "end_date": datetime.date(2027, 3, 31),
                "is_closed": False,
            },
        )
        self.cash = Account.objects.create(
            name="Cash in Hand",
            company=self.company,
            parent=self.assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.CASH,
        )

    def _post(self, **extra):
        payload = {
            "posting_date": "2026-04-05",
            "company": self.company.id,
            "paid_amount": "500.00",
            **extra,
        }
        return self.client.post(URL, payload, format="json")

    def _submit(self, res):
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        out = self.client.post(f"{URL}{res.data['id']}/submit/")
        self.assertEqual(out.status_code, status.HTTP_200_OK, out.content)

    def _gl_debit_credit(self, account, voucher_no):
        rows = GLEntry.objects.filter(
            account=account, voucher_no=voucher_no, is_cancelled=False
        )
        debit = sum((r.debit for r in rows), Decimal("0.00"))
        credit = sum((r.credit for r in rows), Decimal("0.00"))
        return debit, credit

    def test_receive_with_cash_mode_posts_to_cash_account(self):
        res = self._post(
            payment_type="Receive", party_name=self.customer.name, mode="Cash"
        )
        self._submit(res)
        self.assertEqual(res.data["paid_to"], self.cash.id)
        self.assertEqual(res.data["paid_from"], self.debtors.id)
        self.assertEqual(res.data["mode"], "Cash")
        debit, credit = self._gl_debit_credit(self.cash, res.data["name"])
        self.assertEqual((debit, credit), (Decimal("500.00"), Decimal("0.00")))
        self.assertEqual(
            self._gl_debit_credit(self.bank, res.data["name"]),
            (Decimal("0.00"), Decimal("0.00")),
        )

    def test_receive_with_bank_transfer_mode_posts_to_default_bank(self):
        res = self._post(
            payment_type="Receive",
            party_name=self.customer.name,
            mode="Bank Transfer",
        )
        self._submit(res)
        self.assertEqual(res.data["paid_to"], self.bank.id)
        debit, _credit = self._gl_debit_credit(self.bank, res.data["name"])
        self.assertEqual(debit, Decimal("500.00"))

    def test_pay_with_cash_mode_pays_from_cash_account(self):
        res = self._post(payment_type="Pay", party_name=self.supplier.name, mode="Cash")
        self._submit(res)
        self.assertEqual(res.data["paid_from"], self.cash.id)
        self.assertEqual(res.data["paid_to"], self.creditors.id)

    def test_cash_mode_without_cash_account_is_refused(self):
        self.cash.delete()
        res = self._post(
            payment_type="Receive", party_name=self.customer.name, mode="Cash"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)
        self.assertIn("paid_to", res.data)
        self.assertEqual(PaymentEntry.objects.count(), 0)

    def test_cheque_without_reference_is_refused_with_translated_message(self):
        res = self._post(
            payment_type="Receive", party_name=self.customer.name, mode="Cheque"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)
        self.assertEqual(
            [str(m) for m in res.data["reference_no"]],
            ["A cheque needs its number."],
        )
        self.assertEqual(PaymentEntry.objects.count(), 0)

    def test_cheque_with_reference_is_accepted_and_uses_default_bank(self):
        res = self._post(
            payment_type="Receive",
            party_name=self.customer.name,
            mode="Cheque",
            reference_no="CHQ-000123",
        )
        self._submit(res)
        self.assertEqual(res.data["paid_to"], self.bank.id)
        self.assertEqual(res.data["reference_no"], "CHQ-000123")

    def test_zero_amount_draft_is_refused_by_serializer(self):
        res = self._post(
            payment_type="Receive",
            party_name=self.customer.name,
            mode="Cash",
            paid_amount="0.00",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)
        self.assertIn("paid_amount", res.data)
        self.assertEqual(PaymentEntry.objects.count(), 0)

    def test_unknown_mode_is_refused(self):
        res = self._post(
            payment_type="Receive", party_name=self.customer.name, mode="Wire"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)

    def test_no_mode_keeps_the_existing_default(self):
        res = self._post(payment_type="Receive", party_name=self.customer.name)
        self._submit(res)
        self.assertIsNone(res.data["mode"])
        self.assertEqual(res.data["paid_to"], self.bank.id)

    def test_cash_put_in_bank_needs_only_amount_and_date(self):
        res = self._post(payment_type="Internal Transfer", mode="Cash")
        self._submit(res)
        self.assertEqual(res.data["paid_from"], self.cash.id)
        self.assertEqual(res.data["paid_to"], self.bank.id)
        self.assertIsNone(res.data["party_type"])
        pe = PaymentEntry.objects.get(pk=res.data["id"])
        self.assertIsNone(pe.party_name)
        # Cash goes down, bank goes up, by the same amount.
        self.assertEqual(
            self._gl_debit_credit(self.bank, pe.name),
            (Decimal("500.00"), Decimal("0.00")),
        )
        self.assertEqual(
            self._gl_debit_credit(self.cash, pe.name),
            (Decimal("0.00"), Decimal("500.00")),
        )
        lines = GLEntry.objects.filter(voucher_no=pe.name, is_cancelled=False)
        self.assertTrue(all(not r.party and not r.party_type for r in lines))

    def test_internal_transfer_without_mode_still_needs_both_accounts(self):
        res = self._post(payment_type="Internal Transfer")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)
        self.assertIn("paid_from", res.data)
        self.assertIn("paid_to", res.data)

    def test_internal_transfer_with_bank_mode_still_needs_both_accounts(self):
        res = self._post(payment_type="Internal Transfer", mode="Bank Transfer")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)
        self.assertIn("paid_from", res.data)

    def test_cash_put_in_bank_without_bank_account_is_refused(self):
        self.bank.delete()
        res = self._post(payment_type="Internal Transfer", mode="Cash")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)
        self.assertIn("paid_to", res.data)
        self.assertEqual(PaymentEntry.objects.count(), 0)

    def test_historical_row_without_mode_stays_valid(self):
        journal = JournalEntry.objects.create(
            name="TLY-HIST-1",
            company=self.company,
            posting_date=datetime.date(2026, 4, 5),
            docstatus=1,
        )
        pe = PaymentEntry.objects.create(
            name="TLY-HIST-1",
            company=self.company,
            payment_type="Receive",
            posting_date=datetime.date(2026, 4, 5),
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=Decimal("0.00"),
            received_amount=Decimal("0.00"),
            is_historical=True,
            source_journal=journal,
            docstatus=1,
        )
        self.assertIsNone(pe.mode)
        self.assertEqual(
            PaymentEntry.objects.get(pk=pe.pk).paid_amount, Decimal("0.00")
        )


class PaymentModeConstraintTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        self.date = datetime.date(2026, 4, 5)

    def _pe(self, **kw):
        values = {
            "name": "PE-DB-1",
            "company": self.company,
            "payment_type": "Receive",
            "posting_date": self.date,
            "paid_from": self.debtors,
            "paid_to": self.bank,
            "paid_amount": Decimal("100.00"),
            "received_amount": Decimal("100.00"),
        }
        values.update(kw)
        return PaymentEntry(**values)

    def test_zero_amount_entry_is_refused(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._pe(paid_amount=Decimal("0.00")).save()

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._pe(mode="Wire").save()

    def test_cheque_without_reference_is_refused(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._pe(mode=PaymentMode.CHEQUE, reference_no=None).save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._pe(mode=PaymentMode.CHEQUE, reference_no="").save()

    def test_valid_mode_rows_are_accepted(self):
        self._pe(mode=PaymentMode.CASH).save()
        self._pe(name="PE-DB-2", mode=PaymentMode.CHEQUE, reference_no="1").save()
        self.assertEqual(PaymentEntry.objects.count(), 2)

    def test_journal_line_rules_are_enforced_by_the_database(self):
        from accounts.models import JournalEntryLine

        journal = JournalEntry.objects.create(
            name="JV-DB-1", company=self.company, posting_date=self.date
        )
        bad = [
            {"debit": Decimal("-1.00"), "credit": Decimal("0.00")},
            {"debit": Decimal("0.00"), "credit": Decimal("-1.00")},
            {"debit": Decimal("5.00"), "credit": Decimal("5.00")},
        ]
        for kw in bad:
            with self.assertRaises(IntegrityError), transaction.atomic():
                JournalEntryLine.objects.create(
                    journal_entry=journal, account=self.bank, **kw
                )
        # A one-sided line is fine.
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=self.bank,
            debit=Decimal("5.00"),
            credit=Decimal("0.00"),
        )


class CheckAccountsConstraintsCommandTests(BaseAPITest):
    def test_command_reports_and_writes_nothing(self):
        from io import StringIO

        from django.core.management import call_command

        PaymentEntry.objects.create(
            name="PE-CMD-1",
            company=self.company,
            payment_type="Receive",
            posting_date=datetime.date(2026, 4, 5),
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=Decimal("10.00"),
            received_amount=Decimal("10.00"),
        )
        out = StringIO()
        call_command("check_accounts_constraints", stdout=out)
        self.assertIn("No existing rows would violate", out.getvalue())
        self.assertEqual(PaymentEntry.objects.count(), 1)
