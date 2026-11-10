import datetime
from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Company, DocStatus
from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    PaymentEntry,
    TaxTemplate,
    TaxTemplateRow,
)
from stock.models import Item, ItemGroup, UOM
from buying.models import (
    ExpenseCategory,
    Supplier,
    SupplierGroup,
    PurchaseInvoice,
    PurchaseInvoiceItem,
)
from buying.services.expenses import create_expense
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


class ExpenseServiceTests(TestCase):
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
        self.company.default_payable_account = self.creditors_account
        self.company.save(update_fields=["default_payable_account"])

        self.asset_group = Account.objects.create(
            name="Current Assets",
            company=self.company,
            root_type=AccountRootType.ASSET,
            is_group=True,
        )
        self.bank_account = Account.objects.create(
            name="Bank Account",
            company=self.company,
            parent=self.asset_group,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
            is_group=False,
        )
        self.uom = UOM.objects.create(name="Nos")
        self.item_group = ItemGroup.objects.create(name="Services", is_group=False)
        self.category = ExpenseCategory.objects.create(
            company=self.company,
            name="Rent",
            account=self.rent_account,
        )
        self.supplier_group = SupplierGroup.objects.create(name="All Suppliers")
        self.supplier = Supplier.objects.create(
            name="Landlord Realty",
            company=self.company,
            supplier_group=self.supplier_group,
        )
        self.input_cgst = Account.objects.create(
            name="Input CGST",
            company=self.company,
            parent=self.asset_group,
            root_type=AccountRootType.ASSET,
            is_group=False,
        )
        self.input_sgst = Account.objects.create(
            name="Input SGST",
            company=self.company,
            parent=self.asset_group,
            root_type=AccountRootType.ASSET,
            is_group=False,
        )
        self.tax_template = TaxTemplate.objects.create(
            company=self.company,
            title="GST 18%",
            template_type="Purchase",
        )
        TaxTemplateRow.objects.create(
            template=self.tax_template,
            idx=1,
            account=self.input_cgst,
            rate=Decimal("9.000"),
        )
        TaxTemplateRow.objects.create(
            template=self.tax_template,
            idx=2,
            account=self.input_sgst,
            rate=Decimal("9.000"),
        )
        self.today = datetime.date(2026, 4, 1)

    def test_create_expense_with_gst_split(self):
        """Amount is split into net base and taxes using tax calculation."""
        from core.models import State, GSTCategory
        from gst.tests.test_validators import make_gstin

        state, _ = State.objects.get_or_create(name="Maharashtra", code="27")
        self.company.gstin = make_gstin("27", "AABCT1234C")
        self.company.gst_category = GSTCategory.REGISTERED_REGULAR
        self.company.state = state
        self.company.save()

        self.supplier.gstin = make_gstin("27", "AABCT1234D")
        self.supplier.gst_category = GSTCategory.REGISTERED_REGULAR
        self.supplier.state = state
        self.supplier.save()

        self.input_cgst.account_type = AccountType.TAX
        self.input_cgst.gst_direction = "Input"
        self.input_cgst.gst_component = "CGST"
        self.input_cgst.save()

        self.input_sgst.account_type = AccountType.TAX
        self.input_sgst.gst_direction = "Input"
        self.input_sgst.gst_component = "SGST"
        self.input_sgst.save()

        invoice, pe = create_expense(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("1180.00"),
            bill_no="BILL-GST-01",
            has_gst=True,
            tax_template=self.tax_template,
            paid_now=False,
        )
        self.assertEqual(invoice.docstatus, DocStatus.SUBMITTED)
        self.assertEqual(invoice.net_total, Decimal("1000.00"))
        self.assertEqual(invoice.total_taxes_and_charges, Decimal("180.00"))
        self.assertEqual(invoice.grand_total, Decimal("1180.00"))

    def test_paid_now_creates_submitted_invoice_and_payment_entry(self):
        """Paid now creates invoice (status=Submitted) and PaymentEntry (status=Submitted),
        and invoice outstanding becomes 0 (status=Paid)."""
        invoice, pe = create_expense(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("12000.00"),
            bill_no="BILL-PAID-01",
            paid_now=True,
            paid_from=self.bank_account,
        )
        self.assertEqual(invoice.docstatus, DocStatus.SUBMITTED)
        self.assertIsNotNone(pe)
        self.assertEqual(pe.docstatus, DocStatus.SUBMITTED)
        self.assertEqual(pe.paid_amount, Decimal("12000.00"))
        self.assertEqual(invoice.outstanding_amount, Decimal("0.00"))
        self.assertEqual(invoice.payment_label, "Paid")
        self.assertEqual(invoice.payment_status, "Paid")

    def test_paid_now_with_mode_uses_the_mode_account(self):
        """paid_now with a mode and no paid_from pays from the cash account (Cash)."""
        cash = Account.objects.create(
            name="Cash in Hand",
            company=self.company,
            parent=self.asset_group,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.CASH,
            is_group=False,
        )
        invoice, pe = create_expense(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("300.00"),
            bill_no="BILL-CASH-01",
            paid_now=True,
            mode="Cash",
        )
        self.assertEqual(pe.paid_from_id, cash.id)
        self.assertEqual(pe.mode, "Cash")
        self.assertEqual(invoice.outstanding_amount, Decimal("0.00"))

    def test_paid_now_cheque_needs_a_number_and_leaves_nothing_behind(self):
        before = PurchaseInvoice.objects.count()
        with self.assertRaises(ValidationError):
            create_expense(
                company=self.company,
                category=self.category,
                supplier=self.supplier,
                amount=Decimal("300.00"),
                bill_no="BILL-CHQ-01",
                paid_now=True,
                mode="Cheque",
                paid_from=self.bank_account,
            )
        self.assertEqual(PurchaseInvoice.objects.count(), before)

    def test_pay_later_creates_invoice_with_outstanding(self):
        """Pay later creates invoice with outstanding == amount (status=Due)."""
        future_due = datetime.date.today() + datetime.timedelta(days=30)
        invoice, pe = create_expense(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("5000.00"),
            due_date=future_due,
            bill_no="BILL-LATER-01",
            paid_now=False,
        )
        self.assertEqual(invoice.docstatus, DocStatus.SUBMITTED)
        self.assertIsNone(pe)
        self.assertEqual(invoice.outstanding_amount, Decimal("5000.00"))
        self.assertEqual(invoice.payment_label, "Due")
        self.assertEqual(invoice.payment_status, "Due")

    def test_failed_payment_rolls_back_invoice(self):
        """Failed payment rolls back invoice (atomic)."""
        with patch.object(
            PaymentEntry, "submit", side_effect=ValidationError("Bank payment failed.")
        ):
            with self.assertRaises(ValidationError):
                create_expense(
                    company=self.company,
                    category=self.category,
                    supplier=self.supplier,
                    amount=Decimal("8000.00"),
                    date=self.today,
                    bill_no="BILL-ROLLBACK",
                    paid_now=True,
                    paid_from=self.bank_account,
                )
        self.assertFalse(
            PurchaseInvoice.objects.filter(bill_no="BILL-ROLLBACK").exists()
        )

    def test_missing_category_or_non_positive_amount_refused(self):
        """Missing category or non-positive amount refused."""
        with self.assertRaises(ValidationError):
            create_expense(
                company=self.company,
                category=None,
                supplier=self.supplier,
                amount=Decimal("100.00"),
                bill_no="BILL-NO-CAT",
            )
        with self.assertRaises(ValidationError):
            create_expense(
                company=self.company,
                category=self.category,
                supplier=self.supplier,
                amount=Decimal("0.00"),
                bill_no="BILL-ZERO",
            )
        with self.assertRaises(ValidationError):
            create_expense(
                company=self.company,
                category=self.category,
                supplier=self.supplier,
                amount=Decimal("-500.00"),
                bill_no="BILL-NEG",
            )


class ExpenseAPITests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Test Spices Ltd", abbr="TSL", default_currency="INR"
        )
        self.user = User.objects.create_user(
            username="expense_clerk", password="password123"
        )
        self.role = Role.objects.create(name="Expense Staff")
        RolePermission.objects.create(
            role=self.role,
            model_label="buying.PurchaseInvoice",
            can_read=True,
            can_create=True,
            can_write=True,
            can_submit=True,
        )
        RolePermission.objects.create(
            role=self.role,
            model_label="accounts.PaymentEntry",
            can_read=True,
            can_create=True,
            can_submit=True,
        )
        UserRole.objects.create(user=self.user, role=self.role)
        self.client.force_authenticate(user=self.user)

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
        self.valid_expense_account = Account.objects.create(
            name="Office Expense",
            company=self.company,
            parent=self.expense_group,
            root_type=AccountRootType.EXPENSE,
            is_group=False,
        )
        self.uom = UOM.objects.create(name="Nos")
        self.item_group = ItemGroup.objects.create(name="Services", is_group=False)
        self.item = Item.objects.create(
            name="Expense Item",
            item_group=self.item_group,
            stock_uom=self.uom,
            is_stock_item=False,
            gst_hsn_code="998211",
        )
        self.category = ExpenseCategory.objects.create(
            company=self.company,
            name="Office & Stationery",
            account=self.valid_expense_account,
            item=self.item,
            is_active=True,
        )
        self.supplier_group = SupplierGroup.objects.create(name="Service Providers")
        self.supplier = Supplier.objects.create(
            name="Office Supplies Co",
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

        self.input_cgst = Account.objects.create(
            name="Input CGST",
            company=self.company,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.TAX,
            is_group=False,
            gst_direction="Input",
            gst_component="CGST",
        )
        self.input_sgst = Account.objects.create(
            name="Input SGST",
            company=self.company,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.TAX,
            is_group=False,
            gst_direction="Input",
            gst_component="SGST",
        )
        self.tax_template = TaxTemplate.objects.create(
            company=self.company,
            title="GST 18%",
            template_type="Purchase",
        )
        TaxTemplateRow.objects.create(
            template=self.tax_template,
            idx=1,
            account=self.input_cgst,
            rate=Decimal("9.000"),
        )
        TaxTemplateRow.objects.create(
            template=self.tax_template,
            idx=2,
            account=self.input_sgst,
            rate=Decimal("9.000"),
        )

    def test_post_creates_expense_invoice_pay_later(self):
        """POST /api/buying/expenses/ creates submitted invoice with status Due when pay later."""
        payload = {
            "company": self.company.id,
            "category": self.category.id,
            "supplier": self.supplier.id,
            "amount": "5000.00",
            "date": "2026-10-15",
            "due_date": "2026-11-15",
            "bill_no": "BILL-OCT-01",
            "paid_now": False,
        }
        res = self.client.post("/api/buying/expenses/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertTrue(res.data["is_expense"])
        self.assertEqual(res.data["expense_category"], self.category.id)
        self.assertEqual(res.data["payment_status"], "Due")
        self.assertEqual(res.data["payment_label"], "Due")
        self.assertEqual(res.data["outstanding_amount"], "5000.00")
        self.assertEqual(res.data["docstatus"], DocStatus.SUBMITTED)

    def test_post_creates_expense_invoice_paid_now(self):
        """POST /api/buying/expenses/ creates submitted invoice + PaymentEntry when paid now."""
        payload = {
            "company": self.company.id,
            "category": self.category.id,
            "supplier": self.supplier.id,
            "amount": "12000.00",
            "date": "2026-10-20",
            "bill_no": "BILL-PAID-01",
            "paid_now": True,
            "paid_from": self.bank_account.id,
        }
        res = self.client.post("/api/buying/expenses/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["payment_status"], "Paid")
        self.assertEqual(res.data["payment_label"], "Paid")
        self.assertEqual(res.data["outstanding_amount"], "0.00")

        # PaymentEntry was created and submitted
        pe = PaymentEntry.objects.filter(paid_amount=Decimal("12000.00")).first()
        self.assertIsNotNone(pe)
        self.assertEqual(pe.docstatus, DocStatus.SUBMITTED)
        self.assertEqual(pe.paid_from, self.bank_account)

    def test_get_filters_by_month_and_payment_status(self):
        """GET /api/buying/expenses/ supports month, category, and payment_status filters."""
        # 1: Paid in October 2026
        create_expense(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("3000.00"),
            date=datetime.date(2026, 10, 5),
            bill_no="BILL-OCT-P",
            paid_now=True,
            paid_from=self.bank_account,
        )
        # 2: Due in October 2026
        create_expense(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("4000.00"),
            date=datetime.date(2026, 10, 15),
            due_date=datetime.date(2026, 11, 15),
            bill_no="BILL-OCT-D",
            paid_now=False,
        )
        # 3: Paid in September 2026
        create_expense(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            amount=Decimal("2000.00"),
            date=datetime.date(2026, 9, 10),
            bill_no="BILL-SEP-P",
            paid_now=True,
            paid_from=self.bank_account,
        )

        # Filter by month=2026-10: should get OCT bills, but not SEP
        res_oct = self.client.get("/api/buying/expenses/?month=2026-10")
        self.assertEqual(res_oct.status_code, status.HTTP_200_OK)
        bills_oct = res_oct.data.get("results", res_oct.data)
        bill_nos = [b["bill_no"] for b in bills_oct]
        self.assertIn("BILL-OCT-P", bill_nos)
        self.assertIn("BILL-OCT-D", bill_nos)
        self.assertNotIn("BILL-SEP-P", bill_nos)

        # Filter by month=2026-10 and payment_status=Paid
        res_oct_paid = self.client.get(
            "/api/buying/expenses/?month=2026-10&payment_status=Paid"
        )
        self.assertEqual(res_oct_paid.status_code, status.HTTP_200_OK)
        bills_paid = res_oct_paid.data.get("results", res_oct_paid.data)
        bill_nos_paid = [b["bill_no"] for b in bills_paid]
        self.assertIn("BILL-OCT-P", bill_nos_paid)
        self.assertNotIn("BILL-OCT-D", bill_nos_paid)

        # Filter by month=2026-10 and payment_status=Due
        res_oct_due = self.client.get(
            "/api/buying/expenses/?month=2026-10&payment_status=Due"
        )
        self.assertEqual(res_oct_due.status_code, status.HTTP_200_OK)
        bills_due = res_oct_due.data.get("results", res_oct_due.data)
        bill_nos_due = [b["bill_no"] for b in bills_due]
        self.assertIn("BILL-OCT-D", bill_nos_due)
        self.assertNotIn("BILL-OCT-P", bill_nos_due)

    def test_cash_expense_gives_no_input_credit(self):
        """Cash expense gives no input credit (unregistered / no tax rows / no input GST GL)."""
        payload = {
            "company": self.company.id,
            "category": self.category.id,
            "supplier": self.cash_supplier.id,
            "amount": "1180.00",
            "date": "2026-10-22",
            "has_gst": True,
            "tax_template": self.tax_template.id,
            "paid_now": True,
            "paid_from": self.bank_account.id,
        }
        res = self.client.post("/api/buying/expenses/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

        invoice = PurchaseInvoice.objects.get(pk=res.data["id"])
        self.assertEqual(invoice.taxes.count(), 0)
        self.assertEqual(invoice.total_taxes_and_charges, Decimal("0.00"))
        self.assertEqual(invoice.grand_total, Decimal("1180.00"))

        # GL entries check: no GL entries to input GST accounts
        gl_accounts = set(
            GLEntry.objects.filter(voucher_no=invoice.name).values_list(
                "account__name", flat=True
            )
        )
        self.assertNotIn("Input CGST", gl_accounts)
        self.assertNotIn("Input SGST", gl_accounts)
