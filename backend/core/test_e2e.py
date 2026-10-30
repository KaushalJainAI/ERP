"""
End-to-end API tests — black-box HTTP journeys over the real DRF stack.

Unlike the per-app unit tests (which call models/services directly), these drive
the system the way the React SPA does: authenticate with a JWT, POST documents
with nested line items, submit/cancel over the `/submit/` and `/cancel/` action
endpoints, and read the results back through the report endpoints. They prove the
whole request→posting→projection→report pipeline holds together.

Philosophy borrowed from the NGU e2e suite: assert the happy path *and* that
every unhappy branch returns a clean 4xx (never a 500 / stack trace), and that
immutable facts stay immutable when poked through the API.
"""

from decimal import Decimal

from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from buying.testing import next_bill_no
from core.models import Company
from accounts.models import Account, AccountRootType, AccountType
from stock.models import UOM, ItemGroup, Item, Warehouse, ValuationMethod
from selling.models import CustomerGroup, Customer
from buying.models import SupplierGroup, Supplier
from users.models import User


class BaseAPITest(APITestCase):
    """Shared master data + JWT login helpers for the HTTP-level suites."""

    def setUp(self):
        # Throttle counters live in the process cache; isolate every test so an
        # earlier test's requests don't push a later one over a rate limit.
        cache.clear()
        self.admin = User.objects.create_superuser(
            username="e2e_admin", email="e2e_admin@test.com", password="AdminPass123!"
        )
        self.company = Company.objects.create(
            name="Zenith Traders", abbr="ZEN", default_currency="INR"
        )

        # --- Chart of accounts ---
        self.assets = Account.objects.create(
            name="Assets",
            company=self.company,
            root_type=AccountRootType.ASSET,
            is_group=True,
        )
        self.liabilities = Account.objects.create(
            name="Liabilities",
            company=self.company,
            root_type=AccountRootType.LIABILITY,
            is_group=True,
        )
        self.income = Account.objects.create(
            name="Income",
            company=self.company,
            root_type=AccountRootType.INCOME,
            is_group=True,
        )
        self.expenses = Account.objects.create(
            name="Expenses",
            company=self.company,
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )

        self.bank = Account.objects.create(
            name="HDFC Bank",
            company=self.company,
            parent=self.assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
        )
        self.debtors = Account.objects.create(
            name="Debtors",
            company=self.company,
            parent=self.assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.RECEIVABLE,
        )
        self.creditors = Account.objects.create(
            name="Creditors",
            company=self.company,
            parent=self.liabilities,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
        )
        self.sales_income = Account.objects.create(
            name="Sales",
            company=self.company,
            parent=self.income,
            root_type=AccountRootType.INCOME,
        )
        self.purchase_expense = Account.objects.create(
            name="Cost of Purchase",
            company=self.company,
            parent=self.expenses,
            root_type=AccountRootType.EXPENSE,
        )

        # --- Stock masters ---
        self.uom = UOM.objects.create(name="Nos")
        self.group = ItemGroup.objects.create(name="Products", is_group=True)
        self.item = Item.objects.create(
            code="WIDGET-1",
            name="Widget",
            item_group=self.group,
            stock_uom=self.uom,
            valuation_method=ValuationMethod.MOVING_AVERAGE,
        )
        self.warehouse = Warehouse.objects.create(
            name="Main WH", company=self.company, is_group=False
        )

        # --- Parties ---
        self.cust_group = CustomerGroup.objects.create(name="Retail", is_group=True)
        self.customer = Customer.objects.create(
            name="Acme Corp", company=self.company, customer_group=self.cust_group
        )
        self.supp_group = SupplierGroup.objects.create(name="Vendors", is_group=True)
        self.supplier = Supplier.objects.create(
            name="Bolt Supply", company=self.company, supplier_group=self.supp_group
        )

        self.login(self.admin.username, "AdminPass123!")

    def login(self, username, password):
        """Authenticate over HTTP against the JWT endpoint and attach the token."""
        res = self.client.post(
            "/api/auth/token/",
            {"username": username, "password": password},
            format="json",
        )
        assert (
            res.status_code == 200
        ), f"login failed: {res.status_code} {res.content!r}"
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        return res.data

    def _stock_qty(self):
        res = self.client.get(
            f"/api/stock/reports/stock-balance/?item={self.item.id}&warehouse={self.warehouse.id}"
        )
        return Decimal(res.data["total_actual_qty"])

    def _pnl(self):
        return self.client.get(
            f"/api/accounts/reports/profit-and-loss/?company={self.company.id}"
        ).data


class ProcureToPayE2E(BaseAPITest):
    """Buy stock on credit, verify inventory + payables, then pay the supplier."""

    def test_purchase_invoice_receives_stock_and_books_payable(self):
        payload = {
            "name": "PINV-E2E-P1",
            "supplier": self.supplier.id,
            "company": self.company.id,
            "posting_date": "2026-04-01",
            "update_stock": True,
            "credit_to": self.creditors.id,
            "expense_account": self.purchase_expense.id,
            "bill_no": next_bill_no(),
            "items": [
                {
                    "item": self.item.id,
                    "warehouse": self.warehouse.id,
                    "qty": "40.000",
                    "rate": "50.00",
                },
            ],
        }
        create = self.client.post(
            "/api/buying/purchase-invoices/", payload, format="json"
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        pinv_id = create.data["id"]
        self.assertEqual(
            self._stock_qty(), Decimal("0.000"), "stock must not move before submit"
        )

        submit = self.client.post(f"/api/buying/purchase-invoices/{pinv_id}/submit/")
        self.assertEqual(submit.status_code, status.HTTP_200_OK, submit.content)
        self.assertEqual(self._stock_qty(), Decimal("40.000"))

        # Trial balance stays balanced; payables carry the credit.
        tb = self.client.get(
            f"/api/accounts/reports/trial-balance/?company={self.company.id}"
        ).data
        self.assertTrue(tb["is_balanced"])
        self.assertEqual(Decimal(tb["total_debit"]), Decimal("2000.00"))

        # Settle the supplier with a payment (Pay: Creditors Dr, Bank Cr).
        pay = self.client.post(
            "/api/accounts/payment-entries/",
            {
                "name": "PE-E2E-P1",
                "payment_type": "Pay",
                "posting_date": "2026-04-05",
                "company": self.company.id,
                "party_type": "Supplier",
                "party_name": self.supplier.name,
                "paid_from": self.bank.id,
                "paid_to": self.creditors.id,
                "paid_amount": "2000.00",
            },
            format="json",
        )
        self.assertEqual(pay.status_code, status.HTTP_201_CREATED, pay.content)
        pay_submit = self.client.post(
            f"/api/accounts/payment-entries/{pay.data['id']}/submit/"
        )
        self.assertEqual(pay_submit.status_code, status.HTTP_200_OK, pay_submit.content)

    def test_cancel_purchase_invoice_reverses_stock(self):
        payload = {
            "name": "PINV-E2E-P2",
            "supplier": self.supplier.id,
            "company": self.company.id,
            "posting_date": "2026-04-01",
            "update_stock": True,
            "credit_to": self.creditors.id,
            "expense_account": self.purchase_expense.id,
            "bill_no": next_bill_no(),
            "items": [
                {
                    "item": self.item.id,
                    "warehouse": self.warehouse.id,
                    "qty": "10.000",
                    "rate": "50.00",
                }
            ],
        }
        pinv_id = self.client.post(
            "/api/buying/purchase-invoices/", payload, format="json"
        ).data["id"]
        self.client.post(f"/api/buying/purchase-invoices/{pinv_id}/submit/")
        self.assertEqual(self._stock_qty(), Decimal("10.000"))

        cancel = self.client.post(f"/api/buying/purchase-invoices/{pinv_id}/cancel/")
        self.assertEqual(cancel.status_code, status.HTTP_200_OK, cancel.content)
        self.assertEqual(
            self._stock_qty(), Decimal("0.000"), "cancel must reverse the receipt"
        )


class OrderToCashE2E(BaseAPITest):
    """Receive stock, sell it, and confirm revenue + inventory depletion."""

    def _seed_stock(self, qty="100.000", rate="50.00"):
        pinv = {
            "name": "PINV-E2E-SEED",
            "supplier": self.supplier.id,
            "company": self.company.id,
            "posting_date": "2026-04-01",
            "update_stock": True,
            "credit_to": self.creditors.id,
            "expense_account": self.purchase_expense.id,
            "bill_no": next_bill_no(),
            "items": [
                {
                    "item": self.item.id,
                    "warehouse": self.warehouse.id,
                    "qty": qty,
                    "rate": rate,
                }
            ],
        }
        pid = self.client.post(
            "/api/buying/purchase-invoices/", pinv, format="json"
        ).data["id"]
        self.client.post(f"/api/buying/purchase-invoices/{pid}/submit/")

    def test_full_sales_cycle_over_http(self):
        self._seed_stock()
        so_payload = {
            "name": "SO-E2E-1",
            "customer": self.customer.id,
            "company": self.company.id,
            "transaction_date": "2026-04-02",
            "items": [
                {
                    "item": self.item.id,
                    "warehouse": self.warehouse.id,
                    "qty": "10.000",
                    "rate": "120.00",
                }
            ],
        }
        so = self.client.post("/api/selling/sales-orders/", so_payload, format="json")
        self.assertEqual(so.status_code, status.HTTP_201_CREATED, so.content)
        self.assertEqual(Decimal(so.data["grand_total"]), Decimal("1200.00"))
        self.client.post(f"/api/selling/sales-orders/{so.data['id']}/submit/")

        si_payload = {
            "name": "SINV-E2E-1",
            "customer": self.customer.id,
            "company": self.company.id,
            "posting_date": "2026-04-03",
            "update_stock": True,
            "debit_to": self.debtors.id,
            "income_account": self.sales_income.id,
            "items": [
                {
                    "item": self.item.id,
                    "warehouse": self.warehouse.id,
                    "qty": "10.000",
                    "rate": "120.00",
                }
            ],
        }
        si = self.client.post("/api/selling/sales-invoices/", si_payload, format="json")
        self.assertEqual(si.status_code, status.HTTP_201_CREATED, si.content)
        si_id = si.data["id"]

        self.assertEqual(self._stock_qty(), Decimal("100.000"))
        self.client.post(f"/api/selling/sales-invoices/{si_id}/submit/")
        self.assertEqual(
            self._stock_qty(), Decimal("90.000"), "sale should deplete 10 units"
        )

        pnl = self._pnl()
        self.assertEqual(Decimal(pnl["total_income"]), Decimal("1200.00"))

        # Cancel restores stock and reverses revenue.
        self.client.post(f"/api/selling/sales-invoices/{si_id}/cancel/")
        self.assertEqual(self._stock_qty(), Decimal("100.000"))
        self.assertEqual(Decimal(self._pnl()["total_income"]), Decimal("0.00"))

    def test_overselling_is_rejected_cleanly(self):
        """Selling more than on hand must be a 400 with a message — never a 500."""
        self._seed_stock(qty="5.000")
        si_payload = {
            "name": "SINV-E2E-OS",
            "customer": self.customer.id,
            "company": self.company.id,
            "posting_date": "2026-04-03",
            "update_stock": True,
            "debit_to": self.debtors.id,
            "income_account": self.sales_income.id,
            "items": [
                {
                    "item": self.item.id,
                    "warehouse": self.warehouse.id,
                    "qty": "50.000",
                    "rate": "120.00",
                }
            ],
        }
        si_id = self.client.post(
            "/api/selling/sales-invoices/", si_payload, format="json"
        ).data["id"]
        res = self.client.post(f"/api/selling/sales-invoices/{si_id}/submit/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", res.data)
        # Stock untouched by the failed submit (atomic rollback).
        self.assertEqual(self._stock_qty(), Decimal("5.000"))


class DocumentLifecycleGuardsE2E(BaseAPITest):
    """The submit/cancel state machine and immutability, enforced over the API."""

    def _draft_invoice(self):
        payload = {
            "name": "SINV-E2E-DL",
            "customer": self.customer.id,
            "company": self.company.id,
            "posting_date": "2026-04-03",
            "update_stock": False,
            "debit_to": self.debtors.id,
            "income_account": self.sales_income.id,
            "items": [
                {
                    "item": self.item.id,
                    "warehouse": self.warehouse.id,
                    "qty": "1.000",
                    "rate": "120.00",
                }
            ],
        }
        return self.client.post(
            "/api/selling/sales-invoices/", payload, format="json"
        ).data["id"]

    def test_cannot_double_submit(self):
        si_id = self._draft_invoice()
        first = self.client.post(f"/api/selling/sales-invoices/{si_id}/submit/")
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        second = self.client.post(f"/api/selling/sales-invoices/{si_id}/submit/")
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_cancel_a_draft(self):
        si_id = self._draft_invoice()
        res = self.client.post(f"/api/selling/sales-invoices/{si_id}/cancel/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_submitted_invoice_cannot_be_edited_via_api(self):
        si_id = self._draft_invoice()
        self.client.post(f"/api/selling/sales-invoices/{si_id}/submit/")
        res = self.client.patch(
            f"/api/selling/sales-invoices/{si_id}/",
            {"due_date": "2026-05-01"},
            format="json",
        )
        # Must be a clean 4xx (immutability guard), never an uncaught 500.
        self.assertIn(
            res.status_code,
            (
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_409_CONFLICT,
            ),
        )
        self.assertLess(res.status_code, 500)

    def test_submitted_invoice_cannot_be_deleted_via_api(self):
        si_id = self._draft_invoice()
        self.client.post(f"/api/selling/sales-invoices/{si_id}/submit/")
        res = self.client.delete(f"/api/selling/sales-invoices/{si_id}/")
        self.assertIn(
            res.status_code,
            (
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_409_CONFLICT,
            ),
        )
        self.assertLess(res.status_code, 500)


class InputValidationE2E(BaseAPITest):
    """Bad line items and cross-company / self-referential postings are refused
    cleanly (400) — never accepted, never a 500."""

    def _si_payload(self, **overrides):
        payload = {
            "name": overrides.pop("name", "SINV-VAL-1"),
            "customer": self.customer.id,
            "company": self.company.id,
            "posting_date": "2026-04-03",
            "update_stock": False,
            "debit_to": self.debtors.id,
            "income_account": self.sales_income.id,
            "items": [
                {
                    "item": self.item.id,
                    "warehouse": self.warehouse.id,
                    "qty": "1.000",
                    "rate": "100.00",
                }
            ],
        }
        payload.update(overrides)
        return payload

    def test_zero_quantity_line_is_rejected(self):
        payload = self._si_payload()
        payload["items"][0]["qty"] = "0.000"
        res = self.client.post("/api/selling/sales-invoices/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)

    def test_negative_rate_line_is_rejected(self):
        payload = self._si_payload()
        payload["items"][0]["rate"] = "-100.00"
        res = self.client.post("/api/selling/sales-invoices/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.content)

    def test_cross_company_account_is_rejected_at_submit(self):
        """A draft may be created, but submitting an invoice that debits another
        company's account must be blocked before any GL is posted."""
        other = Company.objects.create(name="Other Co", abbr="OTH")
        foreign_debtors = Account.objects.create(
            name="Foreign Debtors",
            company=other,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.RECEIVABLE,
        )
        created = self.client.post(
            "/api/selling/sales-invoices/",
            self._si_payload(debit_to=foreign_debtors.id),
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.content)
        submit = self.client.post(
            f"/api/selling/sales-invoices/{created.data['id']}/submit/"
        )
        self.assertEqual(
            submit.status_code, status.HTTP_400_BAD_REQUEST, submit.content
        )

    def test_payment_to_same_account_is_rejected(self):
        pay = self.client.post(
            "/api/accounts/payment-entries/",
            {
                "name": "PE-VAL-SAME",
                "payment_type": "Pay",
                "posting_date": "2026-04-05",
                "company": self.company.id,
                "party_type": "Supplier",
                "party_name": self.supplier.name,
                "paid_from": self.bank.id,
                "paid_to": self.bank.id,
                "paid_amount": "100.00",
            },
            format="json",
        )
        self.assertEqual(pay.status_code, status.HTTP_201_CREATED, pay.content)
        submit = self.client.post(
            f"/api/accounts/payment-entries/{pay.data['id']}/submit/"
        )
        self.assertEqual(
            submit.status_code, status.HTTP_400_BAD_REQUEST, submit.content
        )
