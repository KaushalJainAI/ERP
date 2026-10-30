"""
HTTP tests for (1) editing draft documents with their child rows and (2) the
server-side document mapping actions (``make-sales-invoice`` etc.).

Both go through the real DRF stack with a JWT, like ``test_e2e``.
"""

from decimal import Decimal

from rest_framework import status

from accounts.models import Account, AccountRootType, AccountType, PaymentEntry
from buying.testing import next_bill_no
from core.models import Company, DocStatus
from core.test_e2e import BaseAPITest
from users.models import Role, RolePermission, User, UserRole


class DraftEditAPITests(BaseAPITest):
    """PUT / PATCH of a draft with a replaced child set."""

    def _line(self, qty, rate="100.00"):
        return {
            "item": self.item.id,
            "warehouse": self.warehouse.id,
            "qty": qty,
            "rate": rate,
        }

    def _spec(self):
        """(url, create payload, edited payload, children key, expected header total or None)."""
        c = self.company.id
        return {
            "so": (
                "/api/selling/sales-orders/",
                {
                    "name": "SO-ED-1",
                    "customer": self.customer.id,
                    "company": c,
                    "transaction_date": "2026-04-01",
                    "items": [self._line("1.000")],
                },
                {
                    "name": "SO-ED-1",
                    "customer": self.customer.id,
                    "company": c,
                    "transaction_date": "2026-04-02",
                    "items": [self._line("2.000"), self._line("3.000", "10.00")],
                },
                "items",
                {"grand_total": "230.00"},
            ),
            "si": (
                "/api/selling/sales-invoices/",
                {
                    "name": "SINV-ED-1",
                    "customer": self.customer.id,
                    "company": c,
                    "posting_date": "2026-04-01",
                    "update_stock": False,
                    "debit_to": self.debtors.id,
                    "income_account": self.sales_income.id,
                    "items": [self._line("1.000")],
                },
                {
                    "name": "SINV-ED-1",
                    "customer": self.customer.id,
                    "company": c,
                    "posting_date": "2026-04-01",
                    "update_stock": False,
                    "debit_to": self.debtors.id,
                    "income_account": self.sales_income.id,
                    "items": [self._line("2.000"), self._line("3.000", "10.00")],
                },
                "items",
                {"grand_total": "230.00"},
            ),
            "po": (
                "/api/buying/purchase-orders/",
                {
                    "name": "PO-ED-1",
                    "supplier": self.supplier.id,
                    "company": c,
                    "transaction_date": "2026-04-01",
                    "items": [self._line("1.000")],
                },
                {
                    "name": "PO-ED-1",
                    "supplier": self.supplier.id,
                    "company": c,
                    "transaction_date": "2026-04-01",
                    "items": [self._line("2.000"), self._line("3.000", "10.00")],
                },
                "items",
                {"grand_total": "230.00"},
            ),
            "pi": (
                "/api/buying/purchase-invoices/",
                {
                    "name": "PINV-ED-1",
                    "bill_no": next_bill_no(),
                    "supplier": self.supplier.id,
                    "company": c,
                    "posting_date": "2026-04-01",
                    "update_stock": False,
                    "credit_to": self.creditors.id,
                    "expense_account": self.purchase_expense.id,
                    "items": [self._line("1.000")],
                },
                {
                    "name": "PINV-ED-1",
                    "bill_no": next_bill_no(),
                    "supplier": self.supplier.id,
                    "company": c,
                    "posting_date": "2026-04-01",
                    "update_stock": False,
                    "credit_to": self.creditors.id,
                    "expense_account": self.purchase_expense.id,
                    "items": [self._line("2.000"), self._line("3.000", "10.00")],
                },
                "items",
                {"grand_total": "230.00"},
            ),
            "je": (
                "/api/accounts/journal-entries/",
                {
                    "name": "JV-ED-1",
                    "posting_date": "2026-04-01",
                    "company": c,
                    "lines": [
                        {"account": self.bank.id, "debit": "100.00", "credit": "0.00"},
                        {
                            "account": self.sales_income.id,
                            "debit": "0.00",
                            "credit": "100.00",
                        },
                    ],
                },
                {
                    "name": "JV-ED-1",
                    "posting_date": "2026-04-01",
                    "company": c,
                    "lines": [
                        {"account": self.bank.id, "debit": "250.00", "credit": "0.00"},
                        {
                            "account": self.sales_income.id,
                            "debit": "0.00",
                            "credit": "250.00",
                        },
                    ],
                },
                "lines",
                {"total_debit": "250.00", "total_credit": "250.00"},
            ),
            "pe": (
                "/api/accounts/payment-entries/",
                {
                    "name": "PE-ED-1",
                    "payment_type": "Receive",
                    "posting_date": "2026-04-01",
                    "company": c,
                    "party_type": "Customer",
                    "party_name": self.customer.name,
                    "paid_from": self.debtors.id,
                    "paid_to": self.bank.id,
                    "paid_amount": "100.00",
                    "received_amount": "100.00",
                    "references": [
                        {
                            "reference_doctype": "selling.SalesInvoice",
                            "reference_name": "X-1",
                            "allocated_amount": "100.00",
                        }
                    ],
                },
                {
                    "name": "PE-ED-1",
                    "payment_type": "Receive",
                    "posting_date": "2026-04-01",
                    "company": c,
                    "party_type": "Customer",
                    "party_name": self.customer.name,
                    "paid_from": self.debtors.id,
                    "paid_to": self.bank.id,
                    "paid_amount": "100.00",
                    "received_amount": "100.00",
                    "references": [
                        {
                            "reference_doctype": "selling.SalesInvoice",
                            "reference_name": "X-2",
                            "allocated_amount": "60.00",
                        },
                        {
                            "reference_doctype": "selling.SalesInvoice",
                            "reference_name": "X-3",
                            "allocated_amount": "40.00",
                        },
                    ],
                },
                "references",
                {},
            ),
            "ste": (
                "/api/stock/stock-entries/",
                {
                    "name": "STE-ED-1",
                    "stock_entry_type": "Material Receipt",
                    "posting_date": "2026-04-01",
                    "company": c,
                    "items": [
                        {
                            "item": self.item.id,
                            "target_warehouse": self.warehouse.id,
                            "qty": "1.000",
                            "valuation_rate": "10.000000",
                        }
                    ],
                },
                {
                    "name": "STE-ED-1",
                    "stock_entry_type": "Material Receipt",
                    "posting_date": "2026-04-01",
                    "company": c,
                    "items": [
                        {
                            "item": self.item.id,
                            "target_warehouse": self.warehouse.id,
                            "qty": "4.000",
                            "valuation_rate": "10.000000",
                        },
                        {
                            "item": self.item.id,
                            "target_warehouse": self.warehouse.id,
                            "qty": "5.000",
                            "valuation_rate": "12.000000",
                        },
                    ],
                },
                "items",
                {},
            ),
        }

    def test_put_replaces_children_and_recomputes_totals(self):
        for key, (url, create, edited, children, totals) in self._spec().items():
            with self.subTest(doc=key):
                res = self.client.post(url, create, format="json")
                self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
                put = self.client.put(f"{url}{res.data['id']}/", edited, format="json")
                self.assertEqual(put.status_code, status.HTTP_200_OK, put.content)
                self.assertEqual(len(put.data[children]), len(edited[children]))
                for field, value in totals.items():
                    self.assertEqual(Decimal(put.data[field]), Decimal(value))
                # Persisted, not just echoed: a fresh GET sees the replaced set.
                got = self.client.get(f"{url}{res.data['id']}/")
                self.assertEqual(len(got.data[children]), len(edited[children]))
                for field, value in totals.items():
                    self.assertEqual(Decimal(got.data[field]), Decimal(value))

    def test_patch_header_only_keeps_children(self):
        for key, (url, create, _edited, children, totals) in self._spec().items():
            with self.subTest(doc=key):
                res = self.client.post(url, create, format="json")
                self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
                patch = self.client.patch(
                    f"{url}{res.data['id']}/",
                    (
                        {"posting_date": "2026-05-01"}
                        if "posting_date" in create
                        else {"transaction_date": "2026-05-01"}
                    ),
                    format="json",
                )
                self.assertEqual(patch.status_code, status.HTTP_200_OK, patch.content)
                self.assertEqual(len(patch.data[children]), len(create[children]))

    def test_patch_with_incomplete_child_is_400(self):
        url, create, _e, children, _t = self._spec()["so"]
        res = self.client.post(url, create, format="json")
        patch = self.client.patch(
            f"{url}{res.data['id']}/",
            {"items": [{"item": self.item.id}]},
            format="json",
        )
        self.assertEqual(patch.status_code, status.HTTP_400_BAD_REQUEST, patch.content)
        # Nothing was replaced.
        got = self.client.get(f"{url}{res.data['id']}/")
        self.assertEqual(len(got.data["items"]), 1)

    def test_put_invalid_child_is_400_and_keeps_old_children(self):
        spec = self._spec()
        bad = {
            "so": lambda p: p["items"].append(self._line("0.000")),
            "si": lambda p: p["items"].append(self._line("-1.000")),
            "po": lambda p: p["items"].append(self._line("1.000", "-5.00")),
            "pi": lambda p: p["items"].append(
                {"item": 999999, "qty": "1", "rate": "1"}
            ),
            "ste": lambda p: p["items"].append({**p["items"][0], "qty": "0.000"}),
            "pe": lambda p: p["references"].append({"reference_doctype": "x"}),
            "je": lambda p: p["lines"].append({"account": 999999, "debit": "1.00"}),
        }
        for key, mutate in bad.items():
            with self.subTest(doc=key):
                url, create, edited, children, _t = spec[key]
                res = self.client.post(url, create, format="json")
                mutate(edited)
                put = self.client.put(f"{url}{res.data['id']}/", edited, format="json")
                self.assertEqual(
                    put.status_code, status.HTTP_400_BAD_REQUEST, put.content
                )
                got = self.client.get(f"{url}{res.data['id']}/")
                self.assertEqual(len(got.data[children]), len(create[children]))

    def test_put_on_submitted_document_is_409(self):
        spec = self._spec()
        for key in ("so", "si", "po", "pi", "je", "ste"):
            with self.subTest(doc=key):
                url, create, edited, children, _t = spec[key]
                res = self.client.post(url, create, format="json")
                sub = self.client.post(f"{url}{res.data['id']}/submit/")
                self.assertEqual(sub.status_code, status.HTTP_200_OK, sub.content)
                put = self.client.put(f"{url}{res.data['id']}/", edited, format="json")
                self.assertEqual(put.status_code, status.HTTP_409_CONFLICT, put.content)
                got = self.client.get(f"{url}{res.data['id']}/")
                self.assertEqual(len(got.data[children]), len(create[children]))

    def test_put_on_submitted_payment_entry_is_409(self):
        spec = self._spec()
        # Submitting a payment validates its references: allocate to a real invoice.
        si_url, si_create = spec["si"][0], spec["si"][1]
        si = self.client.post(si_url, si_create, format="json")
        self.client.post(f"{si_url}{si.data['id']}/submit/")
        url, create, edited, children, _t = spec["pe"]
        create["references"][0]["reference_name"] = si_create["name"]
        create["references"][0]["allocated_amount"] = "10.00"
        res = self.client.post(url, create, format="json")
        sub = self.client.post(f"{url}{res.data['id']}/submit/")
        self.assertEqual(sub.status_code, status.HTTP_200_OK, sub.content)
        put = self.client.put(f"{url}{res.data['id']}/", edited, format="json")
        self.assertEqual(put.status_code, status.HTTP_409_CONFLICT, put.content)
        self.assertEqual(
            self.client.get(f"{url}{res.data['id']}/").data[children][0][
                "reference_name"
            ],
            si_create["name"],
        )


class MappingBase(BaseAPITest):
    def setUp(self):
        super().setUp()
        self.cash = Account.objects.create(
            name="Cash",
            company=self.company,
            parent=self.assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.CASH,
        )
        self.stock_acc = Account.objects.create(
            name="Stock In Hand",
            company=self.company,
            parent=self.assets,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.STOCK,
        )

    def _line(self, qty, rate="100.00"):
        return {
            "item": self.item.id,
            "warehouse": self.warehouse.id,
            "qty": qty,
            "rate": rate,
        }

    def _submitted_order(self, kind, qty="10.000"):
        if kind == "so":
            url = "/api/selling/sales-orders/"
            payload = {
                "name": "SO-MAP-1",
                "customer": self.customer.id,
                "company": self.company.id,
                "transaction_date": "2026-04-01",
                "items": [self._line(qty)],
            }
        else:
            url = "/api/buying/purchase-orders/"
            payload = {
                "name": "PO-MAP-1",
                "supplier": self.supplier.id,
                "company": self.company.id,
                "transaction_date": "2026-04-01",
                "items": [self._line(qty)],
            }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        sub = self.client.post(f"{url}{res.data['id']}/submit/")
        self.assertEqual(sub.status_code, 200, sub.content)
        return url, res.data["id"]

    def _limited_user(self, username, perms):
        """Non-superuser holding {model_label: flags} and logged in."""
        user = User.objects.create_user(
            username=username, email=f"{username}@t.com", password="Pass123!"
        )
        role = Role.objects.create(name=f"role_{username}")
        UserRole.objects.create(user=user, role=role)
        for label, flags in perms.items():
            RolePermission.objects.create(role=role, model_label=label, **flags)
        self.login(username, "Pass123!")
        return user


class SalesMappingTests(MappingBase):
    def test_make_sales_invoice_happy_path(self):
        url, so_id = self._submitted_order("so")
        res = self.client.post(f"{url}{so_id}/make-sales-invoice/")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        d = res.data
        self.assertEqual(d["docstatus"], DocStatus.DRAFT)
        self.assertEqual(d["customer"], self.customer.id)
        self.assertEqual(d["company"], self.company.id)
        self.assertEqual(d["sales_order"], so_id)
        self.assertEqual(d["debit_to"], self.debtors.id)
        self.assertEqual(d["income_account"], self.sales_income.id)
        self.assertTrue(d["update_stock"])
        self.assertEqual(Decimal(d["grand_total"]), Decimal("1000.00"))
        self.assertEqual(len(d["items"]), 1)
        self.assertEqual(Decimal(d["items"][0]["qty"]), Decimal("10.000"))
        self.assertEqual(d["items"][0]["warehouse"], self.warehouse.id)

    def test_second_invoice_gets_only_remainder_then_fully_invoiced(self):
        url, so_id = self._submitted_order("so")
        first = self.client.post(f"{url}{so_id}/make-sales-invoice/")
        # Bill only 4 of 10 on the first invoice.
        inv_url = "/api/selling/sales-invoices/"
        body = {**first.data, "update_stock": False, "bill_no": next_bill_no()}
        body["items"] = [self._line("4.000")]
        put = self.client.put(f"{inv_url}{first.data['id']}/", body, format="json")
        self.assertEqual(put.status_code, 200, put.content)
        self.assertEqual(Decimal(put.data["grand_total"]), Decimal("400.00"))
        sub = self.client.post(f"{inv_url}{first.data['id']}/submit/")
        self.assertEqual(sub.status_code, 200, sub.content)

        second = self.client.post(f"{url}{so_id}/make-sales-invoice/")
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(Decimal(second.data["items"][0]["qty"]), Decimal("6.000"))
        self.assertEqual(Decimal(second.data["grand_total"]), Decimal("600.00"))
        body2 = {**second.data, "update_stock": False}
        self.assertEqual(
            self.client.put(
                f"{inv_url}{second.data['id']}/", body2, format="json"
            ).status_code,
            200,
        )
        sub2 = self.client.post(f"{inv_url}{second.data['id']}/submit/")
        self.assertEqual(sub2.status_code, 200, sub2.content)

        third = self.client.post(f"{url}{so_id}/make-sales-invoice/")
        self.assertEqual(third.status_code, 400, third.content)
        self.assertIn("error", third.data)

    def test_make_sales_invoice_from_draft_order_is_400(self):
        res = self.client.post(
            "/api/selling/sales-orders/",
            {
                "name": "SO-MAP-D",
                "customer": self.customer.id,
                "company": self.company.id,
                "transaction_date": "2026-04-01",
                "items": [self._line("1.000")],
            },
            format="json",
        )
        out = self.client.post(
            f"/api/selling/sales-orders/{res.data['id']}/make-sales-invoice/"
        )
        self.assertEqual(out.status_code, 400, out.content)
        self.assertIn("error", out.data)

    def test_make_sales_invoice_requires_create_on_target(self):
        url, so_id = self._submitted_order("so")
        self._limited_user("reader", {"selling.SalesOrder": {"can_read": True}})
        res = self.client.post(f"{url}{so_id}/make-sales-invoice/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.content)

    def test_make_sales_invoice_requires_read_on_source(self):
        url, so_id = self._submitted_order("so")
        self._limited_user("creator", {"selling.SalesInvoice": {"can_create": True}})
        res = self.client.post(f"{url}{so_id}/make-sales-invoice/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.content)

    def test_make_sales_invoice_with_read_and_create_succeeds(self):
        url, so_id = self._submitted_order("so")
        self._limited_user(
            "clerk",
            {
                "selling.SalesOrder": {"can_read": True},
                "selling.SalesInvoice": {"can_read": True, "can_create": True},
            },
        )
        res = self.client.post(f"{url}{so_id}/make-sales-invoice/")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)

    def test_make_sales_invoice_without_receivable_account_is_400(self):
        url, so_id = self._submitted_order("so")
        self.debtors.account_type = None
        self.debtors.save()
        res = self.client.post(f"{url}{so_id}/make-sales-invoice/")
        self.assertEqual(res.status_code, 400, res.content)

    def _submitted_invoice(self):
        url, so_id = self._submitted_order("so")
        inv = self.client.post(f"{url}{so_id}/make-sales-invoice/")
        inv_url = "/api/selling/sales-invoices/"
        body = {**inv.data, "update_stock": False, "bill_no": next_bill_no()}
        self.client.put(f"{inv_url}{inv.data['id']}/", body, format="json")
        sub = self.client.post(f"{inv_url}{inv.data['id']}/submit/")
        self.assertEqual(sub.status_code, 200, sub.content)
        return inv_url, inv.data["id"], inv.data["name"]

    def test_make_payment_entry_happy_and_partial(self):
        inv_url, inv_id, inv_name = self._submitted_invoice()
        res = self.client.post(f"{inv_url}{inv_id}/make-payment-entry/")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        d = res.data
        self.assertEqual(d["docstatus"], DocStatus.DRAFT)
        self.assertEqual(d["payment_type"], "Receive")
        self.assertEqual(d["party_type"], "Customer")
        self.assertEqual(d["party_name"], self.customer.name)
        self.assertEqual(d["paid_from"], self.debtors.id)
        self.assertEqual(d["paid_to"], self.bank.id)  # Bank preferred over Cash
        self.assertEqual(Decimal(d["paid_amount"]), Decimal("1000.00"))
        self.assertEqual(len(d["references"]), 1)
        ref = d["references"][0]
        self.assertEqual(ref["reference_doctype"], "selling.SalesInvoice")
        self.assertEqual(ref["reference_name"], inv_name)
        self.assertEqual(Decimal(ref["allocated_amount"]), Decimal("1000.00"))

        # Part-pay 300 and submit; the next mapping carries only the remainder.
        pe_url = "/api/accounts/payment-entries/"
        body = dict(d)
        body.update(paid_amount="300.00", received_amount="300.00")
        body["references"] = [{**ref, "allocated_amount": "300.00"}]
        put = self.client.put(f"{pe_url}{d['id']}/", body, format="json")
        self.assertEqual(put.status_code, 200, put.content)
        self.assertEqual(
            self.client.post(f"{pe_url}{d['id']}/submit/").status_code, 200
        )
        rest = self.client.post(f"{inv_url}{inv_id}/make-payment-entry/")
        self.assertEqual(rest.status_code, 201, rest.content)
        self.assertEqual(Decimal(rest.data["paid_amount"]), Decimal("700.00"))
        self.assertEqual(
            Decimal(rest.data["references"][0]["allocated_amount"]), Decimal("700.00")
        )

    def test_make_payment_entry_from_draft_invoice_is_400(self):
        url, so_id = self._submitted_order("so")
        inv = self.client.post(f"{url}{so_id}/make-sales-invoice/")
        res = self.client.post(
            f"/api/selling/sales-invoices/{inv.data['id']}/make-payment-entry/"
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("error", res.data)

    def test_make_payment_entry_fully_paid_is_400(self):
        inv_url, inv_id, _ = self._submitted_invoice()
        pe = self.client.post(f"{inv_url}{inv_id}/make-payment-entry/")
        self.client.post(f"/api/accounts/payment-entries/{pe.data['id']}/submit/")
        again = self.client.post(f"{inv_url}{inv_id}/make-payment-entry/")
        self.assertEqual(again.status_code, 400, again.content)

    def test_make_payment_entry_permissions(self):
        inv_url, inv_id, _ = self._submitted_invoice()
        self._limited_user(
            "sales_only",
            {"selling.SalesInvoice": {"can_read": True, "can_create": True}},
        )
        res = self.client.post(f"{inv_url}{inv_id}/make-payment-entry/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.content)
        self.assertEqual(PaymentEntry.objects.count(), 0)

    def test_make_payment_entry_without_bank_or_cash_is_400(self):
        inv_url, inv_id, _ = self._submitted_invoice()
        Account.objects.filter(
            account_type__in=[AccountType.BANK, AccountType.CASH]
        ).update(account_type=None)
        res = self.client.post(f"{inv_url}{inv_id}/make-payment-entry/")
        self.assertEqual(res.status_code, 400, res.content)


class BuyingMappingTests(MappingBase):
    def test_make_purchase_invoice_happy_path(self):
        url, po_id = self._submitted_order("po")
        res = self.client.post(f"{url}{po_id}/make-purchase-invoice/")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        d = res.data
        self.assertEqual(d["docstatus"], DocStatus.DRAFT)
        self.assertEqual(d["supplier"], self.supplier.id)
        self.assertEqual(d["purchase_order"], po_id)
        self.assertEqual(d["credit_to"], self.creditors.id)
        self.assertTrue(d["update_stock"])
        self.assertEqual(
            d["expense_account"], self.stock_acc.id
        )  # stock in -> stock account
        self.assertEqual(Decimal(d["grand_total"]), Decimal("1000.00"))

    def test_second_invoice_gets_only_remainder(self):
        url, po_id = self._submitted_order("po")
        first = self.client.post(f"{url}{po_id}/make-purchase-invoice/")
        inv_url = "/api/buying/purchase-invoices/"
        body = {**first.data, "update_stock": False, "bill_no": next_bill_no()}
        body["items"] = [self._line("7.000")]
        self.assertEqual(
            self.client.put(
                f"{inv_url}{first.data['id']}/", body, format="json"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(f"{inv_url}{first.data['id']}/submit/").status_code, 200
        )
        second = self.client.post(f"{url}{po_id}/make-purchase-invoice/")
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(Decimal(second.data["items"][0]["qty"]), Decimal("3.000"))

    def test_make_purchase_invoice_from_draft_is_400(self):
        res = self.client.post(
            "/api/buying/purchase-orders/",
            {
                "name": "PO-MAP-D",
                "supplier": self.supplier.id,
                "company": self.company.id,
                "transaction_date": "2026-04-01",
                "items": [self._line("1.000")],
            },
            format="json",
        )
        out = self.client.post(
            f"/api/buying/purchase-orders/{res.data['id']}/make-purchase-invoice/"
        )
        self.assertEqual(out.status_code, 400, out.content)

    def test_make_purchase_invoice_permission_denied(self):
        url, po_id = self._submitted_order("po")
        self._limited_user("po_reader", {"buying.PurchaseOrder": {"can_read": True}})
        res = self.client.post(f"{url}{po_id}/make-purchase-invoice/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.content)

    def _submitted_purchase_invoice(self):
        url, po_id = self._submitted_order("po")
        inv = self.client.post(f"{url}{po_id}/make-purchase-invoice/")
        inv_url = "/api/buying/purchase-invoices/"
        body = {**inv.data, "update_stock": False, "bill_no": next_bill_no()}
        self.client.put(f"{inv_url}{inv.data['id']}/", body, format="json")
        self.assertEqual(
            self.client.post(f"{inv_url}{inv.data['id']}/submit/").status_code, 200
        )
        return inv_url, inv.data["id"], inv.data["name"]

    def test_make_payment_entry_pay_happy_and_partial(self):
        inv_url, inv_id, inv_name = self._submitted_purchase_invoice()
        res = self.client.post(f"{inv_url}{inv_id}/make-payment-entry/")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        d = res.data
        self.assertEqual(d["payment_type"], "Pay")
        self.assertEqual(d["party_type"], "Supplier")
        self.assertEqual(d["party_name"], self.supplier.name)
        self.assertEqual(d["paid_from"], self.bank.id)
        self.assertEqual(d["paid_to"], self.creditors.id)
        self.assertEqual(Decimal(d["paid_amount"]), Decimal("1000.00"))
        ref = d["references"][0]
        self.assertEqual(ref["reference_doctype"], "buying.PurchaseInvoice")
        self.assertEqual(ref["reference_name"], inv_name)

        pe_url = "/api/accounts/payment-entries/"
        body = dict(d)
        body.update(paid_amount="250.00", received_amount="250.00")
        body["references"] = [{**ref, "allocated_amount": "250.00"}]
        self.assertEqual(
            self.client.put(f"{pe_url}{d['id']}/", body, format="json").status_code, 200
        )
        self.assertEqual(
            self.client.post(f"{pe_url}{d['id']}/submit/").status_code, 200
        )
        rest = self.client.post(f"{inv_url}{inv_id}/make-payment-entry/")
        self.assertEqual(Decimal(rest.data["paid_amount"]), Decimal("750.00"))

    def test_make_payment_entry_from_draft_is_400(self):
        url, po_id = self._submitted_order("po")
        inv = self.client.post(f"{url}{po_id}/make-purchase-invoice/")
        res = self.client.post(
            f"/api/buying/purchase-invoices/{inv.data['id']}/make-payment-entry/"
        )
        self.assertEqual(res.status_code, 400, res.content)

    def test_make_payment_entry_permission_denied(self):
        inv_url, inv_id, _ = self._submitted_purchase_invoice()
        self._limited_user(
            "buy_only",
            {"buying.PurchaseInvoice": {"can_read": True, "can_create": True}},
        )
        res = self.client.post(f"{inv_url}{inv_id}/make-payment-entry/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.content)


class LookupSearchTests(BaseAPITest):
    """Link-field dropdowns rely on ``?search=`` for these master lists."""

    def test_search_on_master_lists(self):
        cases = [
            ("/api/selling/customers/", "Acme", self.customer.id),
            ("/api/buying/suppliers/", "Bolt", self.supplier.id),
            ("/api/stock/items/", "WIDGET", self.item.id),
            ("/api/stock/items/", "Widget", self.item.id),
            ("/api/stock/warehouses/", "Main", self.warehouse.id),
            ("/api/accounts/accounts/", "HDFC", self.bank.id),
        ]
        for url, term, expected_id in cases:
            with self.subTest(url=url, term=term):
                res = self.client.get(url, {"search": term})
                self.assertEqual(res.status_code, 200, res.content)
                ids = [r["id"] for r in res.data["results"]]
                self.assertEqual(ids, [expected_id])
                miss = self.client.get(url, {"search": "zzz-no-such"})
                self.assertEqual(miss.data["results"], [])


class AutoNameAndLookupTests(BaseAPITest):
    """Auto-numbering, denormalised line labels and the companies list."""

    _line = DraftEditAPITests._line
    _spec = DraftEditAPITests._spec

    def test_post_without_name_auto_numbers_every_document(self):
        prefixes = {
            "so": "SO-",
            "si": "SINV-",
            "po": "PO-",
            "pi": "PINV-",
            "je": "JV-",
            "pe": "PE-",
            "ste": "STE-",
        }
        for key, (url, create, _e, _c, _t) in self._spec().items():
            with self.subTest(doc=key):
                payload = {k: v for k, v in create.items() if k != "name"}
                if "bill_no" in payload:
                    payload["bill_no"] = next_bill_no()
                res = self.client.post(url, payload, format="json")
                self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
                self.assertTrue(
                    res.data["name"].startswith(prefixes[key]), res.data["name"]
                )
                if "bill_no" in payload:
                    payload["bill_no"] = next_bill_no()
                second = self.client.post(url, payload, format="json")
                self.assertNotEqual(second.data["name"], res.data["name"])

    def test_put_without_name_keeps_name(self):
        url, create, edited, _c, _t = self._spec()["so"]
        res = self.client.post(url, create, format="json")
        edited = {k: v for k, v in edited.items() if k != "name"}
        put = self.client.put(f"{url}{res.data['id']}/", edited, format="json")
        self.assertEqual(put.status_code, 200, put.content)
        self.assertEqual(put.data["name"], "SO-ED-1")

    def test_lines_carry_read_only_labels(self):
        url, create, _e, _c, _t = self._spec()["so"]
        res = self.client.post(url, create, format="json")
        line = res.data["items"][0]
        self.assertEqual(line["item_name"], self.item.name)
        self.assertEqual(line["warehouse_name"], self.warehouse.name)
        url, create, _e, _c, _t = self._spec()["pi"]
        create["items"][0]["warehouse"] = None
        res = self.client.post(url, create, format="json")
        self.assertIsNone(res.data["items"][0]["warehouse_name"])
        url, create, _e, _c, _t = self._spec()["je"]
        res = self.client.post(url, create, format="json")
        self.assertEqual(res.data["lines"][0]["account_name"], self.bank.name)

    def test_companies_list_is_read_only_and_rbac_gated(self):
        res = self.client.get("/api/core/companies/")
        self.assertEqual(res.status_code, 200, res.content)
        row = res.data["results"][0]
        self.assertEqual(row["name"], self.company.name)
        self.assertEqual(row["abbr"], "ZEN")
        self.assertIn("default_currency", row)
        self.assertEqual(
            self.client.get("/api/core/companies/", {"search": "Zenith"}).data["count"],
            1,
        )
        self.assertEqual(
            self.client.post(
                "/api/core/companies/", {"name": "x"}, format="json"
            ).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        # Authenticated-only (no model RBAC): with several companies a new user has
        # no membership (it is only auto-assigned when exactly one company is
        # active), and simply sees no companies.
        Company.objects.create(name="Second Co", abbr="SEC")
        User.objects.create_user(
            username="nobody", email="n@t.com", password="Pass123!"
        )
        self.login("nobody", "Pass123!")
        res = self.client.get("/api/core/companies/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 0)
        # Read on core.Company -> 200.
        user = User.objects.get(username="nobody")
        role = Role.objects.create(name="company_reader")
        UserRole.objects.create(user=user, role=role)
        RolePermission.objects.create(
            role=role, model_label="core.Company", can_read=True
        )
        self.assertEqual(self.client.get("/api/core/companies/").status_code, 200)
