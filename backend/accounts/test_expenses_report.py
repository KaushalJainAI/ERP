import datetime
from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    JournalEntry,
    JournalEntryLine,
)
from accounts.reports.expenses import expense_summary
from buying.models import ExpenseCategory, Supplier, SupplierGroup
from buying.services.expenses import create_expense
from core.models import Company
from stock.models import Item, ItemGroup, UOM
from users.models import Role, RolePermission, User, UserRole


class ExpenseSummaryReportTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Test Masala Co", abbr="TMC", default_currency="INR"
        )
        self.payable_account = Account.objects.create(
            name="Creditors",
            company=self.company,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
            is_group=False,
        )
        self.company.default_payable_account = self.payable_account
        self.company.save()

        self.bank_account = Account.objects.create(
            name="HDFC Bank",
            company=self.company,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
            is_group=False,
        )

        self.expense_group = Account.objects.create(
            name="Expenses",
            company=self.company,
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.rent_account = Account.objects.create(
            name="Rent Expense",
            company=self.company,
            parent=self.expense_group,
            root_type=AccountRootType.EXPENSE,
            is_group=False,
        )
        self.electricity_account = Account.objects.create(
            name="Electricity Expense",
            company=self.company,
            parent=self.expense_group,
            root_type=AccountRootType.EXPENSE,
            is_group=False,
        )
        self.wage_account = Account.objects.create(
            name="Wages and Salaries",
            company=self.company,
            parent=self.expense_group,
            root_type=AccountRootType.EXPENSE,
            is_group=False,
        )
        self.payroll_payable = Account.objects.create(
            name="Payroll Payable",
            company=self.company,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
            is_group=False,
        )

        self.uom = UOM.objects.create(name="Nos")
        self.item_group = ItemGroup.objects.create(name="Services", is_group=False)
        self.rent_item = Item.objects.create(
            code="RENT-SVC",
            name="Rent Service",
            item_group=self.item_group,
            stock_uom=self.uom,
            is_stock_item=False,
        )
        self.elec_item = Item.objects.create(
            code="ELEC-SVC",
            name="Electricity Service",
            item_group=self.item_group,
            stock_uom=self.uom,
            is_stock_item=False,
        )

        self.rent_category = ExpenseCategory.objects.create(
            company=self.company,
            name="Rent",
            account=self.rent_account,
            item=self.rent_item,
            is_active=True,
        )
        self.elec_category = ExpenseCategory.objects.create(
            company=self.company,
            name="Electricity",
            account=self.electricity_account,
            item=self.elec_item,
            is_active=True,
        )

        self.supplier_group = SupplierGroup.objects.create(name="Vendors")
        self.supplier = Supplier.objects.create(
            name="Landlord Co",
            company=self.company,
            supplier_group=self.supplier_group,
            is_cash_expense=False,
        )

    def test_summary_total_matches_gl_for_expense_accounts_over_period(self):
        """Summary grand total reconciles exactly with GL expense accounts."""
        from_date = datetime.date(2026, 10, 1)
        to_date = datetime.date(2026, 10, 31)

        # 1. Rent expense: 15,000 paid now
        create_expense(
            company=self.company,
            category=self.rent_category,
            supplier=self.supplier,
            amount=Decimal("15000.00"),
            date=datetime.date(2026, 10, 5),
            bill_no="RENT-OCT",
            paid_now=True,
            paid_from=self.bank_account,
        )

        # 2. Electricity expense: 5,000 pay later
        create_expense(
            company=self.company,
            category=self.elec_category,
            supplier=self.supplier,
            amount=Decimal("5000.00"),
            date=datetime.date(2026, 10, 10),
            due_date=datetime.date(2026, 11, 10),
            bill_no="ELEC-OCT",
            paid_now=False,
        )

        # 3. Wages entry in GL: 20,000 via JournalEntry
        je = JournalEntry.objects.create(
            name="JV-2026-WAGE",
            company=self.company,
            posting_date=datetime.date(2026, 10, 15),
            voucher_type="Journal Entry",
        )
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=self.wage_account,
            debit=Decimal("20000.00"),
            credit=Decimal("0.00"),
        )
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=self.payroll_payable,
            debit=Decimal("0.00"),
            credit=Decimal("20000.00"),
        )
        je.submit()

        # Build report
        report = expense_summary(self.company, from_date, to_date)

        # Category breakdowns
        by_cat = {r["category_name"]: r for r in report["categories"]}
        self.assertEqual(by_cat["Rent"]["paid"], "15000.00")
        self.assertEqual(by_cat["Rent"]["due"], "0.00")
        self.assertEqual(by_cat["Rent"]["total"], "15000.00")

        self.assertEqual(by_cat["Electricity"]["paid"], "0.00")
        self.assertEqual(by_cat["Electricity"]["due"], "5000.00")
        self.assertEqual(by_cat["Electricity"]["total"], "5000.00")

        # Wages row
        self.assertEqual(report["wages"]["total"], "20000.00")
        self.assertEqual(report["wages"]["due"], "20000.00")
        self.assertEqual(report["wages"]["paid"], "0.00")

        # Grand totals: 15,000 + 5,000 + 20,000 = 40,000
        self.assertEqual(report["grand_totals"]["total"], "40000.00")
        self.assertEqual(report["grand_totals"]["paid"], "15000.00")
        self.assertEqual(report["grand_totals"]["due"], "25000.00")

        # GL reconciliation: Query all GL entries on expense accounts for company in period
        gl_expense_entries = GLEntry.objects.filter(
            company=self.company,
            account__root_type=AccountRootType.EXPENSE,
            is_cancelled=False,
            posting_date__gte=from_date,
            posting_date__lte=to_date,
        )
        gl_total = sum(
            (e.debit - e.credit for e in gl_expense_entries), Decimal("0.00")
        )
        self.assertEqual(gl_total, Decimal("40000.00"))
        self.assertEqual(Decimal(report["grand_totals"]["total"]), gl_total)

    def test_last_month_comparison(self):
        """Report reflects previous calendar month total."""
        from_date = datetime.date(2026, 10, 1)
        to_date = datetime.date(2026, 10, 31)

        # September Rent: 14,000
        create_expense(
            company=self.company,
            category=self.rent_category,
            supplier=self.supplier,
            amount=Decimal("14000.00"),
            date=datetime.date(2026, 9, 20),
            bill_no="RENT-SEP",
            paid_now=True,
            paid_from=self.bank_account,
        )

        # October Rent: 15,000
        create_expense(
            company=self.company,
            category=self.rent_category,
            supplier=self.supplier,
            amount=Decimal("15000.00"),
            date=datetime.date(2026, 10, 5),
            bill_no="RENT-OCT-2",
            paid_now=True,
            paid_from=self.bank_account,
        )

        report = expense_summary(self.company, from_date, to_date)
        by_cat = {r["category_name"]: r for r in report["categories"]}
        self.assertEqual(by_cat["Rent"]["last_month_total"], "14000.00")
        self.assertEqual(by_cat["Rent"]["total"], "15000.00")
        self.assertEqual(report["grand_totals"]["last_month_total"], "14000.00")


class ExpenseSummaryAPITests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="API Test Masala Co", abbr="ATC", default_currency="INR"
        )
        self.payable_account = Account.objects.create(
            name="Creditors",
            company=self.company,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
            is_group=False,
        )
        self.company.default_payable_account = self.payable_account
        self.company.save()

        self.user = User.objects.create_user(
            username="report_user", password="password123"
        )
        self.role = Role.objects.create(name="Report Viewer")
        RolePermission.objects.create(
            role=self.role,
            model_label="accounts.GLEntry",
            can_read=True,
        )
        UserRole.objects.create(user=self.user, role=self.role)
        self.client.force_authenticate(user=self.user)

    def test_get_expense_summary_endpoint(self):
        """GET /api/accounts/reports/expense-summary/ returns 200 with structured report."""
        url = (
            f"/api/accounts/reports/expense-summary/"
            f"?company={self.company.id}&from_date=2026-10-01&to_date=2026-10-31"
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertIn("categories", res.data)
        self.assertIn("wages", res.data)
        self.assertIn("rows", res.data)
        self.assertIn("grand_totals", res.data)
        self.assertEqual(res.data["from_date"], "2026-10-01")
        self.assertEqual(res.data["to_date"], "2026-10-31")
