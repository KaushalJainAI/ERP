"""Cash in hand: the balance of the company's postable Cash accounts, read-only."""

import datetime
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    JournalEntry,
    JournalEntryLine,
)
from core.models import Company, FiscalYear

User = get_user_model()
URL = "/api/accounts/cash-in-hand/"
D = datetime.date


class CashInHandTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="cashier", email="cashier@erp.local", password="password"
        )
        self.client.force_authenticate(user=self.user)
        FiscalYear.objects.create(
            name="FY-CASH-" + uuid.uuid4().hex[:6],
            start_date=D(2026, 4, 1),
            end_date=D(2027, 3, 31),
        )
        self.company = self.make_company("Cash Co")
        self.other = self.make_company("Other Cash Co")
        self.cash = self.make_account(self.company, "Cash in Hand", AccountType.CASH)
        self.bank = self.make_account(self.company, "HDFC", AccountType.BANK)
        self.sales = self.make_account(
            self.company, "Sales", None, AccountRootType.INCOME
        )
        self.other_cash = self.make_account(self.other, "Cash", AccountType.CASH)
        self.other_sales = self.make_account(
            self.other, "Sales", None, AccountRootType.INCOME
        )

    def make_company(self, name):
        return Company.objects.create(
            name=f"{name} {uuid.uuid4().hex[:6]}",
            abbr=uuid.uuid4().hex[:4].upper(),
            default_currency="INR",
        )

    def make_account(self, company, name, account_type, root=AccountRootType.ASSET):
        return Account.objects.create(
            name=name,
            company=company,
            root_type=root,
            account_type=account_type,
        )

    def make_receipt(self, company, debit_account, credit_account, amount, name):
        """A submitted journal entry that moves ``amount`` into ``debit_account``."""
        je = JournalEntry.objects.create(
            name=name, posting_date=D(2026, 5, 1), company=company
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=debit_account, debit=Decimal(amount)
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=credit_account, credit=Decimal(amount)
        )
        je.submit()
        return je

    def get_amount(self, company):
        resp = self.client.get(URL, headers={"X-Company-ID": str(company.pk)})
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data["amount"]

    def test_sums_cash_accounts_only(self):
        self.make_receipt(self.company, self.cash, self.sales, "1500.50", "JE-CASH-1")
        self.make_receipt(self.company, self.bank, self.sales, "900.00", "JE-BANK-1")

        self.assertEqual(self.get_amount(self.company), "1500.50")

    def test_cash_paid_out_reduces_the_balance(self):
        self.make_receipt(self.company, self.cash, self.sales, "1000.00", "JE-CASH-2")
        self.make_receipt(self.company, self.sales, self.cash, "250.25", "JE-CASH-3")

        self.assertEqual(self.get_amount(self.company), "749.75")

    def test_cancelled_entries_do_not_count(self):
        je = self.make_receipt(
            self.company, self.cash, self.sales, "400.00", "JE-CASH-4"
        )
        self.assertEqual(self.get_amount(self.company), "400.00")

        je.cancel()

        self.assertEqual(self.get_amount(self.company), "0.00")
        self.assertTrue(
            GLEntry.objects.filter(voucher_no="JE-CASH-4", is_cancelled=True).exists()
        )

    def test_another_companys_cash_is_not_counted(self):
        self.make_receipt(
            self.other, self.other_cash, self.other_sales, "777.00", "JE-OTHER-1"
        )

        self.assertEqual(self.get_amount(self.company), "0.00")
        self.assertEqual(self.get_amount(self.other), "777.00")

    def test_no_cash_account_is_zero(self):
        self.make_receipt(self.company, self.bank, self.sales, "300.00", "JE-BANK-2")

        self.assertEqual(self.get_amount(self.company), "0.00")

    def test_anonymous_user_is_refused(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get(URL, headers={"X-Company-ID": str(self.company.pk)})
        self.assertIn(resp.status_code, (401, 403))
