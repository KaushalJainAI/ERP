import datetime
from decimal import Decimal
import io
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Company, DocStatus
from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
)
from stock.models import Item, ItemGroup, UOM
from buying.models import (
    ExpenseCategory,
    Supplier,
    SupplierGroup,
    PurchaseInvoice,
    RecurringExpense,
)
from buying.services.expenses import generate_due_recurring_expenses
from users.models import User


class RecurringExpenseModelTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Test Spices Ltd", abbr="TSL", default_currency="INR"
        )
        self.other_company = Company.objects.create(
            name="Other Spices Ltd", abbr="OTH", default_currency="INR"
        )
        self.expense_group = Account.objects.create(
            name="Expenses",
            company=self.company,
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.expense_account = Account.objects.create(
            name="Rent Expense",
            company=self.company,
            parent=self.expense_group,
            root_type=AccountRootType.EXPENSE,
            is_group=False,
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

        self.uom = UOM.objects.create(name="Nos")
        self.item_group = ItemGroup.objects.create(
            name="All Item Groups", is_group=False
        )
        self.item = Item.objects.create(
            code="EXP-RENT-MODEL",
            name="Expense - Rent",
            item_group=self.item_group,
            stock_uom=self.uom,
            is_stock_item=False,
        )
        self.category = ExpenseCategory.objects.create(
            company=self.company,
            name="Rent",
            account=self.expense_account,
            item=self.item,
        )
        self.supplier_group = SupplierGroup.objects.create(
            name="Landlords", is_group=False
        )
        self.supplier = Supplier.objects.create(
            company=self.company,
            supplier_group=self.supplier_group,
            name="Property Landlord",
        )

        # Other company objects
        self.other_expense_group = Account.objects.create(
            name="Other Expenses",
            company=self.other_company,
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.other_expense_account = Account.objects.create(
            name="Other Rent Expense",
            company=self.other_company,
            parent=self.other_expense_group,
            root_type=AccountRootType.EXPENSE,
            is_group=False,
        )
        self.other_category = ExpenseCategory.objects.create(
            company=self.other_company,
            name="Rent",
            account=self.other_expense_account,
        )
        self.other_supplier = Supplier.objects.create(
            company=self.other_company,
            supplier_group=self.supplier_group,
            name="Other Landlord",
        )

    def test_valid_recurring_expense_creation(self):
        rec = RecurringExpense.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("15000.00"),
            day_of_month=5,
            remarks="Monthly office rent",
        )
        self.assertEqual(rec.amount, Decimal("15000.00"))
        self.assertEqual(rec.day_of_month, 5)
        self.assertTrue(rec.is_active)
        self.assertEqual(rec.description, "Monthly office rent")
        self.assertIn("Rent", str(rec))
        self.assertIn("Day 5", str(rec))

    def test_amount_must_be_greater_than_zero_validation(self):
        rec = RecurringExpense(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("0.00"),
            day_of_month=5,
        )
        with self.assertRaises(ValidationError) as ctx:
            rec.clean()
        self.assertIn("amount", ctx.exception.message_dict)

    def test_amount_must_be_greater_than_zero_db_constraint(self):
        rec = RecurringExpense(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("0.00"),
            day_of_month=5,
        )
        with self.assertRaises(IntegrityError):
            super(RecurringExpense, rec).save()

    def test_day_of_month_bounds_validation(self):
        # 0 is invalid
        rec0 = RecurringExpense(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("1000.00"),
            day_of_month=0,
        )
        with self.assertRaises(ValidationError) as ctx:
            rec0.clean()
        self.assertIn("day_of_month", ctx.exception.message_dict)

        # 32 is invalid
        rec32 = RecurringExpense(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("1000.00"),
            day_of_month=32,
        )
        with self.assertRaises(ValidationError) as ctx:
            rec32.clean()
        self.assertIn("day_of_month", ctx.exception.message_dict)

    def test_day_of_month_db_constraint(self):
        rec = RecurringExpense(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("1000.00"),
            day_of_month=32,
        )
        with self.assertRaises(IntegrityError):
            super(RecurringExpense, rec).save()

    def test_cross_company_category_refused(self):
        rec = RecurringExpense(
            company=self.company,
            category=self.other_category,
            supplier=self.supplier,
            amount=Decimal("1000.00"),
            day_of_month=1,
        )
        with self.assertRaises(ValidationError) as ctx:
            rec.clean()
        self.assertIn("category", ctx.exception.message_dict)

    def test_cross_company_supplier_refused(self):
        rec = RecurringExpense(
            company=self.company,
            category=self.category,
            supplier=self.other_supplier,
            amount=Decimal("1000.00"),
            day_of_month=1,
        )
        with self.assertRaises(ValidationError) as ctx:
            rec.clean()
        self.assertIn("supplier", ctx.exception.message_dict)


class RecurringExpenseServiceTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Test Spices Ltd", abbr="TSL", default_currency="INR"
        )
        self.other_company = Company.objects.create(
            name="Other Spices Ltd", abbr="OTH", default_currency="INR"
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
        self.payable_account = Account.objects.create(
            name="Creditors",
            company=self.company,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
            is_group=False,
        )
        self.company.default_payable_account = self.payable_account
        self.company.save()

        self.uom = UOM.objects.create(name="Nos")
        self.item_group = ItemGroup.objects.create(
            name="All Item Groups", is_group=False
        )
        self.item = Item.objects.create(
            code="EXP-RENT-MODEL",
            name="Expense - Rent",
            item_group=self.item_group,
            stock_uom=self.uom,
            is_stock_item=False,
        )
        self.category = ExpenseCategory.objects.create(
            company=self.company,
            name="Rent",
            account=self.rent_account,
            item=self.item,
        )
        self.supplier_group = SupplierGroup.objects.create(
            name="Suppliers", is_group=False
        )
        self.supplier = Supplier.objects.create(
            company=self.company,
            supplier_group=self.supplier_group,
            name="Landlord Realty",
        )

    def test_due_on_exact_day_creates_draft_invoice(self):
        rec = RecurringExpense.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("25000.00"),
            day_of_month=15,
        )

        # On Jan 14 -> not due
        created_14 = generate_due_recurring_expenses(
            company=self.company, as_on=datetime.date(2026, 1, 14)
        )
        self.assertEqual(len(created_14), 0)
        self.assertEqual(PurchaseInvoice.objects.count(), 0)

        # On Jan 15 -> due, creates draft invoice
        created_15 = generate_due_recurring_expenses(
            company=self.company, as_on=datetime.date(2026, 1, 15)
        )
        self.assertEqual(len(created_15), 1)
        inv = created_15[0]

        self.assertEqual(inv.docstatus, DocStatus.DRAFT)
        self.assertTrue(inv.is_expense)
        self.assertFalse(inv.update_stock)
        self.assertEqual(inv.recurring_expense, rec)
        self.assertEqual(inv.expense_category, self.category)
        self.assertEqual(inv.supplier, self.supplier)
        self.assertEqual(inv.posting_date, datetime.date(2026, 1, 15))
        self.assertEqual(inv.grand_total, Decimal("25000.00"))

    def test_short_month_february_triggers_on_last_day(self):
        # Expenses due on 28th, 29th, 30th, 31st
        rec28 = RecurringExpense.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("1000.00"),
            day_of_month=28,
        )
        rec29 = RecurringExpense.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("2000.00"),
            day_of_month=29,
        )
        rec30 = RecurringExpense.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("3000.00"),
            day_of_month=30,
        )
        rec31 = RecurringExpense.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("4000.00"),
            day_of_month=31,
        )

        # In 2026, February has 28 days
        # On Feb 27: none of them are due
        created_27 = generate_due_recurring_expenses(
            company=self.company, as_on=datetime.date(2026, 2, 27)
        )
        self.assertEqual(len(created_27), 0)

        # On Feb 28: all 4 are due (28 is exact day, 29/30/31 trigger because 28 is the last day)
        created_28 = generate_due_recurring_expenses(
            company=self.company, as_on=datetime.date(2026, 2, 28)
        )
        self.assertEqual(len(created_28), 4)
        rec_ids = {inv.recurring_expense_id for inv in created_28}
        self.assertEqual(rec_ids, {rec28.id, rec29.id, rec30.id, rec31.id})

    def test_short_month_30_day_triggers_on_30th(self):
        rec31 = RecurringExpense.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("5000.00"),
            day_of_month=31,
        )

        # April has 30 days
        # On April 29 -> not due
        created_29 = generate_due_recurring_expenses(
            company=self.company, as_on=datetime.date(2026, 4, 29)
        )
        self.assertEqual(len(created_29), 0)

        # On April 30 -> due because 30 is last day and 31 > 30
        created_30 = generate_due_recurring_expenses(
            company=self.company, as_on=datetime.date(2026, 4, 30)
        )
        self.assertEqual(len(created_30), 1)
        self.assertEqual(created_30[0].recurring_expense, rec31)

    def test_repeated_execution_does_not_duplicate(self):
        RecurringExpense.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("12000.00"),
            day_of_month=10,
        )

        # First run on 10th
        run1 = generate_due_recurring_expenses(
            company=self.company, as_on=datetime.date(2026, 3, 10)
        )
        self.assertEqual(len(run1), 1)
        self.assertEqual(PurchaseInvoice.objects.count(), 1)

        # Re-run on same day -> deduplicated, returns empty list
        run2 = generate_due_recurring_expenses(
            company=self.company, as_on=datetime.date(2026, 3, 10)
        )
        self.assertEqual(len(run2), 0)
        self.assertEqual(PurchaseInvoice.objects.count(), 1)

    def test_inactive_recurring_expenses_are_ignored(self):
        RecurringExpense.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("12000.00"),
            day_of_month=10,
            is_active=False,
        )
        created = generate_due_recurring_expenses(
            company=self.company, as_on=datetime.date(2026, 3, 10)
        )
        self.assertEqual(len(created), 0)
        self.assertEqual(PurchaseInvoice.objects.count(), 0)

    def test_company_scoping(self):
        # Recurring expense for this company
        RecurringExpense.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("5000.00"),
            day_of_month=1,
        )

        # Other company expense
        other_exp_acc = Account.objects.create(
            name="Other Rent",
            company=self.other_company,
            root_type=AccountRootType.EXPENSE,
            is_group=False,
        )
        other_payable = Account.objects.create(
            name="Other Creditors",
            company=self.other_company,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
            is_group=False,
        )
        self.other_company.default_payable_account = other_payable
        self.other_company.save()
        other_cat = ExpenseCategory.objects.create(
            company=self.other_company,
            name="Other Rent",
            account=other_exp_acc,
        )
        other_sup = Supplier.objects.create(
            company=self.other_company,
            supplier_group=self.supplier_group,
            name="Other Supplier",
        )
        RecurringExpense.objects.create(
            company=self.other_company,
            category=other_cat,
            supplier=other_sup,
            amount=Decimal("8000.00"),
            day_of_month=1,
        )

        # Scoped to self.company
        created = generate_due_recurring_expenses(
            company=self.company, as_on=datetime.date(2026, 5, 1)
        )
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].company, self.company)

        # Scoped to all companies (company=None)
        # Note: self.company was already generated for 2026-05, so only other_company runs
        created_all = generate_due_recurring_expenses(
            company=None, as_on=datetime.date(2026, 5, 1)
        )
        self.assertEqual(len(created_all), 1)
        self.assertEqual(created_all[0].company, self.other_company)

    def test_cancelled_draft_allows_regeneration(self):
        RecurringExpense.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("5000.00"),
            day_of_month=1,
        )

        created = generate_due_recurring_expenses(
            company=self.company, as_on=datetime.date(2026, 6, 1)
        )
        self.assertEqual(len(created), 1)
        inv = created[0]

        # Cancel the draft invoice
        inv.docstatus = DocStatus.CANCELLED
        inv.save(update_fields=["docstatus"])

        # Running again now creates a fresh draft because the previous one is cancelled
        regenerated = generate_due_recurring_expenses(
            company=self.company, as_on=datetime.date(2026, 6, 1)
        )
        self.assertEqual(len(regenerated), 1)
        self.assertEqual(regenerated[0].docstatus, DocStatus.DRAFT)


class GenerateRecurringExpensesCommandTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Test Spices Ltd", abbr="TSL", default_currency="INR"
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
        self.payable_account = Account.objects.create(
            name="Creditors",
            company=self.company,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
            is_group=False,
        )
        self.company.default_payable_account = self.payable_account
        self.company.save()

        self.category = ExpenseCategory.objects.create(
            company=self.company,
            name="Rent",
            account=self.rent_account,
        )
        self.supplier_group = SupplierGroup.objects.create(
            name="Suppliers", is_group=False
        )
        self.supplier = Supplier.objects.create(
            company=self.company,
            supplier_group=self.supplier_group,
            name="Landlord Realty",
        )
        self.rec = RecurringExpense.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("10000.00"),
            day_of_month=1,
        )

    def test_command_generates_recurring_expenses(self):
        out = io.StringIO()
        call_command(
            "generate_recurring_expenses",
            company="TSL",
            as_on="2026-07-01",
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("[OK] Generated 1 draft recurring expense invoice(s).", output)
        self.assertEqual(PurchaseInvoice.objects.count(), 1)

    def test_command_invalid_date(self):
        with self.assertRaises(CommandError):
            call_command("generate_recurring_expenses", as_on="invalid-date")

    def test_command_unknown_company(self):
        with self.assertRaises(CommandError):
            call_command("generate_recurring_expenses", company="NONEXISTENT")


class RecurringExpenseAPITests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Test Spices Ltd", abbr="TSL", default_currency="INR"
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
        self.category = ExpenseCategory.objects.create(
            company=self.company,
            name="Rent",
            account=self.rent_account,
        )
        self.supplier_group = SupplierGroup.objects.create(
            name="Suppliers", is_group=False
        )
        self.supplier = Supplier.objects.create(
            company=self.company,
            supplier_group=self.supplier_group,
            name="Landlord Realty",
        )
        self.user = User.objects.create_superuser(
            username="admin", email="admin@test.com", password="password123"
        )
        self.client.force_authenticate(user=self.user)

    def test_list_recurring_expenses(self):
        RecurringExpense.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("15000.00"),
            day_of_month=10,
        )
        response = self.client.get("/api/buying/recurring-expenses/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["category_name"], "Rent")
        self.assertEqual(results[0]["supplier_name"], "Landlord Realty")

    def test_create_recurring_expense_api(self):
        payload = {
            "company": self.company.id,
            "category": self.category.id,
            "supplier": self.supplier.id,
            "amount": "18000.00",
            "day_of_month": 12,
            "remarks": "Warehouse lease",
        }
        response = self.client.post("/api/buying/recurring-expenses/", payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(RecurringExpense.objects.count(), 1)
        rec = RecurringExpense.objects.first()
        self.assertEqual(rec.amount, Decimal("18000.00"))
        self.assertEqual(rec.day_of_month, 12)
        self.assertEqual(rec.remarks, "Warehouse lease")
