import datetime
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Company, DocStatus
from accounts.models import Account, AccountRootType, AccountType
from stock.models import Item, ItemGroup, UOM
from buying.models import (
    ExpenseCategory,
    Supplier,
    SupplierGroup,
    PurchaseInvoice,
    PurchaseInvoiceItem,
)
from users.models import User, Role, UserRole, RolePermission


class ExpenseCategoryModelTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Test Spices Ltd", abbr="TSL", default_currency="INR"
        )
        self.other_company = Company.objects.create(
            name="Other Company", abbr="OTH", default_currency="INR"
        )
        self.expense_group = Account.objects.create(
            name="Expenses",
            company=self.company,
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.valid_expense_account = Account.objects.create(
            name="Electricity Expense",
            company=self.company,
            parent=self.expense_group,
            root_type=AccountRootType.EXPENSE,
            is_group=False,
        )
        self.asset_account = Account.objects.create(
            name="Bank Account",
            company=self.company,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
            is_group=False,
        )
        self.other_company_account = Account.objects.create(
            name="Other Electricity",
            company=self.other_company,
            root_type=AccountRootType.EXPENSE,
            is_group=False,
        )
        self.uom = UOM.objects.create(name="Nos")
        self.item_group = ItemGroup.objects.create(
            name="All Item Groups", is_group=False
        )
        self.item = Item.objects.create(
            name="Expense - Electricity",
            item_group=self.item_group,
            stock_uom=self.uom,
            is_stock_item=False,
        )

    def test_valid_expense_category_created(self):
        cat = ExpenseCategory.objects.create(
            company=self.company,
            name="Electricity",
            account=self.valid_expense_account,
            item=self.item,
        )
        self.assertEqual(str(cat), "Electricity (TSL)")
        self.assertTrue(cat.is_active)

    def test_non_expense_account_refused(self):
        cat = ExpenseCategory(
            company=self.company,
            name="Bank Fee",
            account=self.asset_account,
        )
        with self.assertRaises(ValidationError) as ctx:
            cat.clean()
        self.assertIn("account", ctx.exception.message_dict)

    def test_group_account_refused(self):
        cat = ExpenseCategory(
            company=self.company,
            name="General Expenses",
            account=self.expense_group,
        )
        with self.assertRaises(ValidationError) as ctx:
            cat.clean()
        self.assertIn("account", ctx.exception.message_dict)

    def test_other_company_account_refused(self):
        cat = ExpenseCategory(
            company=self.company,
            name="Electricity",
            account=self.other_company_account,
        )
        with self.assertRaises(ValidationError) as ctx:
            cat.clean()
        self.assertIn("account", ctx.exception.message_dict)

    def test_duplicate_name_for_same_company_refused(self):
        ExpenseCategory.objects.create(
            company=self.company,
            name="Electricity",
            account=self.valid_expense_account,
        )
        with self.assertRaises(IntegrityError):
            ExpenseCategory.objects.create(
                company=self.company,
                name="Electricity",
                account=self.valid_expense_account,
            )

    def test_same_name_different_company_allowed(self):
        cat1 = ExpenseCategory.objects.create(
            company=self.company,
            name="Electricity",
            account=self.valid_expense_account,
        )
        cat2 = ExpenseCategory.objects.create(
            company=self.other_company,
            name="Electricity",
            account=self.other_company_account,
        )
        self.assertNotEqual(cat1.pk, cat2.pk)


class ExpenseCategoryAPITests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Test Spices Ltd", abbr="TSL", default_currency="INR"
        )
        self.user = User.objects.create_user(
            username="testclerk", password="password123"
        )
        self.role = Role.objects.create(name="Staff Role")
        RolePermission.objects.create(
            role=self.role,
            model_label="buying.ExpenseCategory",
            can_read=True,
            can_create=True,
            can_write=True,
            can_delete=True,
        )
        UserRole.objects.create(user=self.user, role=self.role)
        self.client.force_authenticate(user=self.user)

        self.expense_group = Account.objects.create(
            name="Expenses",
            company=self.company,
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.valid_expense_account = Account.objects.create(
            name="Rent Expense",
            company=self.company,
            parent=self.expense_group,
            root_type=AccountRootType.EXPENSE,
            is_group=False,
        )

    def test_create_and_list_expense_category(self):
        res = self.client.post(
            "/api/buying/expense-categories/",
            {
                "company": self.company.id,
                "name": "Rent",
                "account": self.valid_expense_account.id,
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["name"], "Rent")
        self.assertEqual(res.data["account_name"], "Rent Expense")

        list_res = self.client.get("/api/buying/expense-categories/")
        self.assertEqual(list_res.status_code, status.HTTP_200_OK)
        names = [r["name"] for r in list_res.data.get("results", list_res.data)]
        self.assertIn("Rent", names)

    def test_ensure_categories_creates_mapped_categories(self):
        from core.management.commands.seed_defaults import ensure_categories

        # No mapping, only Rent Expense exists, matching Rent
        ensure_categories(self.company, mapping={"Rent": self.valid_expense_account})
        cat = ExpenseCategory.objects.filter(company=self.company, name="Rent").first()
        self.assertIsNotNone(cat)
        self.assertEqual(cat.account, self.valid_expense_account)
        self.assertIsNotNone(cat.item)
        self.assertFalse(cat.item.is_stock_item)
        self.assertEqual(cat.item.name, "Expense - Rent")

        # electricity does not exist and wasn't in mapping -> not created
        self.assertFalse(
            ExpenseCategory.objects.filter(
                company=self.company, name="Electricity"
            ).exists()
        )


class ExpenseInvoiceModelTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Spices Ltd", abbr="SL", default_currency="INR"
        )
        self.expense_group = Account.objects.create(
            name="Expenses",
            company=self.company,
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.rent_account = Account.objects.create(
            name="Office Rent",
            company=self.company,
            parent=self.expense_group,
            root_type=AccountRootType.EXPENSE,
            is_group=False,
        )
        self.other_expense_account = Account.objects.create(
            name="Stationery",
            company=self.company,
            parent=self.expense_group,
            root_type=AccountRootType.EXPENSE,
            is_group=False,
        )
        self.liability_group = Account.objects.create(
            name="Current Liabilities",
            company=self.company,
            root_type=AccountRootType.LIABILITY,
            is_group=True,
        )
        self.creditors_account = Account.objects.create(
            name="Creditors",
            company=self.company,
            parent=self.liability_group,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
            is_group=False,
        )
        self.uom = UOM.objects.create(name="Nos")
        self.item_group = ItemGroup.objects.create(name="Services", is_group=False)
        self.item = Item.objects.create(
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
        self.supplier_group = SupplierGroup.objects.create(name="All Suppliers")
        self.supplier = Supplier.objects.create(
            name="Landlord Realty",
            company=self.company,
            supplier_group=self.supplier_group,
            is_cash_expense=False,
        )
        self.cash_supplier = Supplier.objects.create(
            name="Cash Expense",
            company=self.company,
            supplier_group=self.supplier_group,
            is_cash_expense=True,
        )
        self.today = datetime.date(2026, 4, 1)

    def test_check_expense_requires_category_constraint(self):
        """is_expense=True without category raises IntegrityError."""
        with self.assertRaises(IntegrityError):
            PurchaseInvoice.objects.create(
                name="PINV-EXP-FAIL",
                company=self.company,
                supplier=self.supplier,
                posting_date=self.today,
                due_date=self.today,
                bill_no="BILL-101",
                is_expense=True,
                expense_category=None,
                update_stock=False,
                credit_to=self.creditors_account,
                expense_account=self.rent_account,
            )

    def test_expense_update_stock_refused_at_submit(self):
        """update_stock=True on expense raises ValidationError at submit."""
        inv = PurchaseInvoice.objects.create(
            name="PINV-EXP-001",
            company=self.company,
            supplier=self.supplier,
            posting_date=self.today,
            due_date=self.today,
            bill_no="BILL-102",
            is_expense=True,
            expense_category=self.category,
            update_stock=True,
            credit_to=self.creditors_account,
            expense_account=self.rent_account,
        )
        PurchaseInvoiceItem.objects.create(
            purchase_invoice=inv,
            item=self.item,
            qty=Decimal("1.000"),
            rate=Decimal("5000.00"),
        )
        with self.assertRaises(ValidationError) as ctx:
            inv.submit()
        self.assertIn("Expenses cannot update stock.", str(ctx.exception))

    def test_expense_line_account_mismatch_refused_at_submit(self):
        """Line account not matching category account raises ValidationError."""
        inv = PurchaseInvoice.objects.create(
            name="PINV-EXP-002",
            company=self.company,
            supplier=self.supplier,
            posting_date=self.today,
            due_date=self.today,
            bill_no="BILL-103",
            is_expense=True,
            expense_category=self.category,
            update_stock=False,
            credit_to=self.creditors_account,
            expense_account=self.other_expense_account,
        )
        PurchaseInvoiceItem.objects.create(
            purchase_invoice=inv,
            item=self.item,
            qty=Decimal("1.000"),
            rate=Decimal("5000.00"),
        )
        with self.assertRaises(ValidationError) as ctx:
            inv.submit()
        self.assertIn(
            "Expense line account must match category account.", str(ctx.exception)
        )

    def test_bill_no_blank_allowed_for_cash_expense_refused_for_normal(self):
        """bill_no blank allowed for cash expense supplier, refused for normal supplier."""
        inv_normal = PurchaseInvoice.objects.create(
            name="PINV-EXP-003",
            company=self.company,
            supplier=self.supplier,
            posting_date=self.today,
            due_date=self.today,
            bill_no="",
            is_expense=True,
            expense_category=self.category,
            update_stock=False,
            credit_to=self.creditors_account,
            expense_account=self.rent_account,
        )
        PurchaseInvoiceItem.objects.create(
            purchase_invoice=inv_normal,
            item=self.item,
            qty=Decimal("1.000"),
            rate=Decimal("5000.00"),
        )
        with self.assertRaises(ValidationError) as ctx:
            inv_normal.submit()
        self.assertIn("Bill No", str(ctx.exception))

        inv_cash = PurchaseInvoice.objects.create(
            name="PINV-EXP-004",
            company=self.company,
            supplier=self.cash_supplier,
            posting_date=self.today,
            due_date=self.today,
            bill_no="",
            is_expense=True,
            expense_category=self.category,
            update_stock=False,
            credit_to=self.creditors_account,
            expense_account=self.rent_account,
        )
        PurchaseInvoiceItem.objects.create(
            purchase_invoice=inv_cash,
            item=self.item,
            qty=Decimal("1.000"),
            rate=Decimal("5000.00"),
        )
        inv_cash.submit()
        self.assertEqual(inv_cash.docstatus, DocStatus.SUBMITTED)
