"""The evening day summary for Admins (card U-7 d)."""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    PaymentEntry,
    PaymentType,
)
from buying.models import ExpenseCategory, PurchaseInvoice, Supplier, SupplierGroup
from core.models import Company, DocStatus, Outbox, UserCompany
from notifications.models import AlertRule, Notification
from notifications.services.digests import send_day_summaries, send_day_summary
from selling.models import Customer, CustomerGroup, SalesInvoice
from stock.models import UOM, Item, ItemGroup
from users.models import User


class DaySummaryTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.company = Company.objects.create(name="Day Co", abbr="DAY")
        self.other = Company.objects.create(name="Other Co", abbr="OTH")

        self.admin = User.objects.create_user(
            username="day_admin", email="day_admin@test.com", password="pw"
        )
        UserCompany.objects.create(
            user=self.admin, company=self.company, is_default=True
        )
        UserCompany.objects.create(
            user=self.admin, company=self.other, is_default=False
        )

        self.rule = AlertRule.objects.create(key="day_summary", is_active=True)
        self.rule.recipients.add(self.admin)

        self.bank = self._account(
            "Bank", AccountRootType.ASSET, AccountType.BANK, self.company
        )
        self.debtors = self._account(
            "Debtors", AccountRootType.ASSET, AccountType.RECEIVABLE, self.company
        )
        self.creditors = self._account(
            "Creditors", AccountRootType.LIABILITY, AccountType.PAYABLE, self.company
        )
        self.income = self._account("Sales", AccountRootType.INCOME, None, self.company)
        self.expense = self._account(
            "Rent", AccountRootType.EXPENSE, None, self.company
        )

        self.expense_category = ExpenseCategory.objects.create(
            company=self.company, name="Rent", account=self.expense
        )
        self.customer_group = CustomerGroup.objects.create(
            name="Retail", is_group=False
        )
        self.customer = Customer.objects.create(
            name="Sharma Traders",
            company=self.company,
            customer_group=self.customer_group,
        )
        self.supplier_group = SupplierGroup.objects.create(name="Mills", is_group=False)
        self.supplier = Supplier.objects.create(
            name="Bolt Supply", company=self.company, supplier_group=self.supplier_group
        )

    def _account(self, name, root, account_type, company):
        return Account.objects.create(
            name=name, company=company, root_type=root, account_type=account_type
        )

    def _sales_invoice(self, posting_date, grand_total, outstanding):
        return SalesInvoice.objects.create(
            name=f"SI-{uuid.uuid4().hex[:8]}",
            company=self.company,
            customer=self.customer,
            posting_date=posting_date,
            due_date=posting_date,
            debit_to=self.debtors,
            income_account=self.income,
            net_total=Decimal(grand_total),
            grand_total=Decimal(grand_total),
            rounded_total=Decimal(grand_total),
            outstanding_amount=Decimal(outstanding),
            docstatus=DocStatus.SUBMITTED,
        )

    def _payment(self, payment_type, amount, posting_date):
        receive = payment_type == PaymentType.RECEIVE
        return PaymentEntry.objects.create(
            name=f"PE-{uuid.uuid4().hex[:8]}",
            company=self.company,
            payment_type=payment_type,
            posting_date=posting_date,
            paid_from=self.debtors if receive else self.bank,
            paid_to=self.bank if receive else self.creditors,
            paid_amount=Decimal(amount),
            received_amount=Decimal(amount),
            docstatus=DocStatus.SUBMITTED,
        )

    def _purchase(self, posting_date, grand_total, outstanding, is_expense):
        return PurchaseInvoice.objects.create(
            name=f"PI-{uuid.uuid4().hex[:8]}",
            company=self.company,
            supplier=self.supplier,
            credit_to=self.creditors,
            expense_account=self.expense,
            expense_category=self.expense_category,
            posting_date=posting_date,
            due_date=posting_date,
            bill_date=posting_date,
            net_total=Decimal(grand_total),
            grand_total=Decimal(grand_total),
            rounded_total=Decimal(grand_total),
            outstanding_amount=Decimal(outstanding),
            is_expense=is_expense,
            docstatus=DocStatus.SUBMITTED,
        )

    def _fixture(self):
        """Today: sold 1000, collected 600, paid 200 and an expense of 150.
        Still open: 400 owed by customers from today's bill, 5000 from last
        week's bill; 300 owed to a supplier."""
        yesterday = self.today - timedelta(days=7)
        self._sales_invoice(self.today, "1000.00", "400.00")
        self._sales_invoice(yesterday, "5000.00", "5000.00")
        # A draft bill and a cancelled bill today must not count.
        SalesInvoice.objects.create(
            name=f"SI-{uuid.uuid4().hex[:8]}",
            company=self.company,
            customer=self.customer,
            posting_date=self.today,
            due_date=self.today,
            debit_to=self.debtors,
            income_account=self.income,
            net_total=Decimal("9999.00"),
            grand_total=Decimal("9999.00"),
            rounded_total=Decimal("9999.00"),
            outstanding_amount=Decimal("9999.00"),
            docstatus=DocStatus.DRAFT,
        )
        self._payment(PaymentType.RECEIVE, "600.00", self.today)
        self._payment(PaymentType.PAY, "200.00", self.today)
        self._purchase(self.today, "150.00", "0.00", is_expense=True)
        self._purchase(yesterday, "300.00", "300.00", is_expense=False)
        Item.objects.create(
            code="LOW-1",
            name="Low item",
            item_group=ItemGroup.objects.create(name="Low group", is_group=False),
            stock_uom=UOM.objects.get_or_create(name="Nos")[0],
            critical_stock=Decimal("5.000"),
        )

    def test_totals_match_the_fixture(self):
        self._fixture()
        send_day_summary(self.company)
        notif = Notification.objects.get(rule_key="day_summary", user=self.admin)
        self.assertEqual(notif.params["sold"], "1000.00")
        self.assertEqual(notif.params["collected"], "600.00")
        self.assertEqual(notif.params["spent"], "350.00")
        self.assertEqual(notif.params["to_collect"], "5400.00")
        self.assertEqual(notif.params["to_pay"], "300.00")
        self.assertEqual(notif.params["low_stock"], "1")

    def test_one_summary_per_company_per_day(self):
        self._fixture()
        send_day_summary(self.company)
        send_day_summary(self.company)
        self.assertEqual(
            Notification.objects.filter(
                rule_key="day_summary", user=self.admin
            ).count(),
            1,
        )

    def test_each_company_gets_its_own_summary(self):
        send_day_summary(self.company)
        send_day_summary(self.other)
        self.assertEqual(
            Notification.objects.filter(
                rule_key="day_summary", user=self.admin
            ).count(),
            2,
        )

    def test_a_user_of_another_company_is_not_sent_this_company(self):
        outsider = User.objects.create_user(
            username="outsider", email="outsider@test.com", password="pw"
        )
        UserCompany.objects.create(user=outsider, company=self.other, is_default=True)
        self.rule.recipients.add(outsider)
        send_day_summary(self.company)
        self.assertFalse(Notification.objects.filter(user=outsider).exists())
        self.assertTrue(Notification.objects.filter(user=self.admin).exists())

    def test_email_is_queued_through_the_outbox(self):
        self.admin.email = "day_admin@test.com"
        self.admin.save()
        send_day_summary(self.company)
        self.assertEqual(Outbox.objects.filter(channel="email").count(), 1)

    def test_nothing_when_notifications_are_off(self):
        self.rule.is_active = False
        self.rule.save()
        self.assertEqual(send_day_summary(self.company), [])
        self.assertFalse(Notification.objects.filter(rule_key="day_summary").exists())

    def test_nothing_when_the_rule_is_missing(self):
        AlertRule.objects.filter(key="day_summary").delete()
        self.assertEqual(send_day_summary(self.company), [])

    def test_scheduler_pass_sends_every_company_once(self):
        send_day_summaries()
        send_day_summaries()
        self.assertEqual(
            Notification.objects.filter(
                rule_key="day_summary", user=self.admin
            ).count(),
            2,
        )
