import io

from django.core.management import call_command
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Account, AccountRootType, AccountType, BankAccount
from buying.models import ExpenseCategory, Supplier, SupplierGroup
from core.models import Company, FiscalYear
from core.services.setup_health import setup_health
from core.test_e2e import BaseAPITest
from stock.models import Warehouse

ALL_CODES = {
    "default_receivable_account",
    "default_payable_account",
    "default_income_account",
    "round_off_account",
    "unprocessed_warehouse",
    "in_process_warehouse",
    "ready_warehouse",
    "gstin",
    "default_bank_account",
    "cash_account",
    "cash_expense_supplier",
    "expense_category",
}


def _build_fully_set_up_company():
    company = Company.objects.create(
        name="Full Traders",
        abbr="FULL",
        default_currency="INR",
        gstin="23AAAAA0000A1Z5",
    )
    mk = lambda name, rt, at=None: Account.objects.create(  # noqa: E731
        company=company, name=name, root_type=rt, account_type=at
    )
    receivable = mk("Debtors", AccountRootType.ASSET, AccountType.RECEIVABLE)
    payable = mk("Creditors", AccountRootType.LIABILITY, AccountType.PAYABLE)
    income = mk("Sales", AccountRootType.INCOME)
    round_off = mk("Round Off", AccountRootType.EXPENSE, AccountType.ROUND_OFF)
    bank = mk("HDFC Bank", AccountRootType.ASSET, AccountType.BANK)
    mk("Cash in Hand", AccountRootType.ASSET, AccountType.CASH)
    expense = mk("Office Expenses", AccountRootType.EXPENSE)

    company.default_receivable_account = receivable
    company.default_payable_account = payable
    company.default_income_account = income
    company.round_off_account = round_off
    company.unprocessed_warehouse = Warehouse.objects.create(
        name="Unprocessed", company=company, is_group=False
    )
    company.in_process_warehouse = Warehouse.objects.create(
        name="In Process", company=company, is_group=False
    )
    company.ready_warehouse = Warehouse.objects.create(
        name="Ready", company=company, is_group=False
    )
    company.save()

    BankAccount.objects.create(
        company=company, account=bank, bank_name="HDFC", is_default=True
    )
    group = SupplierGroup.objects.create(name="Cash Vendors", is_group=True)
    Supplier.objects.create(
        name="Cash Expenses",
        company=company,
        supplier_group=group,
        is_cash_expense=True,
    )
    ExpenseCategory.objects.create(name="Office", company=company, account=expense)
    return company


class SetupHealthServiceTests(TestCase):
    def test_bare_company_lists_every_item(self):
        company = Company.objects.create(
            name="Bare Co", abbr="BARE", default_currency="INR"
        )
        codes = {item["code"] for item in setup_health(company)}
        self.assertTrue(ALL_CODES <= codes, ALL_CODES - codes)

    def test_items_have_code_message_and_fix_path(self):
        company = Company.objects.create(
            name="Bare Co", abbr="BARE", default_currency="INR"
        )
        for item in setup_health(company):
            self.assertEqual(set(item), {"code", "message", "fix_path"})
            self.assertTrue(item["message"])
            self.assertTrue(item["fix_path"].startswith("/"))

    def test_fully_set_up_company_lists_none(self):
        company = _build_fully_set_up_company()
        self.assertEqual(setup_health(company), [])

    def test_missing_fiscal_year_for_today_is_listed(self):
        company = _build_fully_set_up_company()
        FiscalYear.objects.all().delete()
        codes = {item["code"] for item in setup_health(company)}
        self.assertEqual(codes, {"fiscal_year"})

    def test_closed_fiscal_year_does_not_count(self):
        company = _build_fully_set_up_company()
        FiscalYear.objects.update(is_closed=True)
        codes = {item["code"] for item in setup_health(company)}
        self.assertEqual(codes, {"fiscal_year"})

    def test_cash_account_must_be_postable(self):
        company = _build_fully_set_up_company()
        Account.objects.filter(company=company, account_type=AccountType.CASH).update(
            is_group=True
        )
        codes = {item["code"] for item in setup_health(company)}
        self.assertEqual(codes, {"cash_account"})


class SetupHealthEndpointTests(BaseAPITest):
    def test_endpoint_lists_missing_items_for_active_company(self):
        res = self.client.get("/api/core/setup-health/")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.content)
        codes = {item["code"] for item in res.data["items"]}
        self.assertTrue(ALL_CODES <= codes, ALL_CODES - codes)

    def test_endpoint_requires_authentication(self):
        res = APIClient().get("/api/core/setup-health/")
        self.assertIn(
            res.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        )

    def test_endpoint_is_read_only(self):
        res = self.client.post("/api/core/setup-health/", {}, format="json")
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class BootstrapCompanyTests(TestCase):
    def test_bootstrap_runs_to_completion_on_empty_database(self):
        call_command(
            "bootstrap_company",
            name="Bootstrap Traders",
            abbr="BTP",
            state="23",
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        company = Company.objects.get(abbr="BTP")
        self.assertEqual(company.stock_adjustment_account.name, "Stock Adjustment")
        self.assertIsNone(company.stock_adjustment_account.account_type)
