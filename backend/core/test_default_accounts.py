"""Invoices saved without party / income accounts get the company defaults (ERPNext set_missing_values)."""

from django.core.exceptions import ValidationError

from accounts.models import Account, AccountRootType
from core.test_e2e import BaseAPITest


class DefaultInvoiceAccountsTests(BaseAPITest):
    def _line(self):
        return {"item": self.item.id, "warehouse": self.warehouse.id, "qty": "1.000", "rate": "50.00"}

    def test_sales_invoice_without_accounts_gets_company_defaults(self):
        other_income = Account.objects.create(
            name="Sales Gst @ 5%",
            company=self.company,
            root_type=AccountRootType.INCOME,
            parent=self.income,
        )
        self.company.default_income_account = other_income
        self.company.save()
        res = self.client.post(
            "/api/selling/sales-invoices/",
            {
                "customer": self.customer.id,
                "posting_date": "2026-04-01",
                "update_stock": False,
                "items": [self._line()],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["debit_to"], self.debtors.id)
        self.assertEqual(res.data["income_account"], other_income.id)

    def test_explicit_account_is_kept_and_update_without_it_keeps_stored(self):
        res = self.client.post(
            "/api/selling/sales-invoices/",
            {
                "customer": self.customer.id,
                "posting_date": "2026-04-01",
                "update_stock": False,
                "income_account": self.sales_income.id,
                "items": [self._line()],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["income_account"], self.sales_income.id)
        patch = self.client.patch(
            f"/api/selling/sales-invoices/{res.data['id']}/",
            {"posting_date": "2026-04-02"},
            format="json",
        )
        self.assertEqual(patch.status_code, 200, patch.content)
        self.assertEqual(patch.data["income_account"], self.sales_income.id)

    def test_purchase_invoice_without_accounts_gets_defaults(self):
        res = self.client.post(
            "/api/buying/purchase-invoices/",
            {
                "supplier": self.supplier.id,
                "posting_date": "2026-04-01",
                "update_stock": False,
                "credit_to": None,
                "items": [self._line()],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["credit_to"], self.creditors.id)
        self.assertIsNotNone(res.data["expense_account"])

    def test_company_rejects_wrong_kind_of_default_account(self):
        self.company.default_receivable_account = self.creditors
        with self.assertRaises(ValidationError) as ctx:
            self.company.full_clean()
        self.assertIn("default_receivable_account", ctx.exception.message_dict)
