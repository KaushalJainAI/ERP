"""S-4: a blank line warehouse is filled with the company's default, never asked for."""

from decimal import Decimal

from rest_framework import status

from core.models import Company
from core.test_e2e import BaseAPITest
from stock.models import Item, Warehouse


class SalesDefaultWarehouseTest(BaseAPITest):
    def setUp(self):
        super().setUp()
        self.ready = Warehouse.objects.create(
            name="Ready WH", company=self.company, is_group=False
        )
        Company.objects.filter(pk=self.company.pk).update(ready_warehouse=self.ready)
        self.company.refresh_from_db()

    def _stock(self, warehouse, item=None):
        res = self.client.get(
            f"/api/stock/reports/stock-balance/?item={(item or self.item).id}"
            f"&warehouse={warehouse.id}"
        )
        return Decimal(res.data["total_actual_qty"])

    def _seed_ready(self, qty="10.000"):
        """Put stock in the ready warehouse through a purchase bill (not sales)."""
        from buying.testing import next_bill_no

        pinv = self.client.post(
            "/api/buying/purchase-invoices/",
            {
                "name": "PINV-READY-SEED",
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
                        "warehouse": self.ready.id,
                        "qty": qty,
                        "rate": "50.00",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(pinv.status_code, status.HTTP_201_CREATED, pinv.content)
        submit = self.client.post(
            f"/api/buying/purchase-invoices/{pinv.data['id']}/submit/"
        )
        self.assertEqual(submit.status_code, status.HTTP_200_OK, submit.content)

    def _si(self, items, **extra):
        payload = {
            "name": "SINV-DEFAULT-WH",
            "customer": self.customer.id,
            "company": self.company.id,
            "posting_date": "2026-04-03",
            "update_stock": True,
            "debit_to": self.debtors.id,
            "income_account": self.sales_income.id,
            "items": items,
        }
        payload.update(extra)
        return self.client.post("/api/selling/sales-invoices/", payload, format="json")

    def test_sales_invoice_without_warehouse_submits_from_ready_warehouse(self):
        self._seed_ready("10.000")
        created = self._si([{"item": self.item.id, "qty": "4.000", "rate": "100.00"}])
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.content)
        self.assertEqual(created.data["items"][0]["warehouse"], self.ready.id)

        submit = self.client.post(
            f"/api/selling/sales-invoices/{created.data['id']}/submit/"
        )
        self.assertEqual(submit.status_code, status.HTTP_200_OK, submit.content)
        self.assertEqual(self._stock(self.ready), Decimal("6.000"))
        self.assertEqual(self._stock(self.warehouse), Decimal("0.000"))

    def test_sales_invoice_keeps_a_warehouse_the_user_sent(self):
        created = self._si(
            [
                {
                    "item": self.item.id,
                    "warehouse": self.warehouse.id,
                    "qty": "1.000",
                    "rate": "100.00",
                }
            ]
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.content)
        self.assertEqual(created.data["items"][0]["warehouse"], self.warehouse.id)

    def test_non_stock_item_line_keeps_no_warehouse(self):
        service = Item.objects.create(
            code="SVC-1",
            name="Service",
            item_group=self.group,
            stock_uom=self.uom,
            is_stock_item=False,
        )
        created = self._si([{"item": service.id, "qty": "1.000", "rate": "100.00"}])
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.content)
        self.assertIsNone(created.data["items"][0]["warehouse"])

    def test_sales_order_blank_warehouse_gets_ready_warehouse(self):
        res = self.client.post(
            "/api/selling/sales-orders/",
            {
                "name": "SO-DEFAULT-WH",
                "customer": self.customer.id,
                "company": self.company.id,
                "transaction_date": "2026-04-02",
                "items": [{"item": self.item.id, "qty": "2.000", "rate": "120.00"}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(res.data["items"][0]["warehouse"], self.ready.id)

    def test_sales_order_then_invoice_without_warehouse_takes_stock_from_ready(self):
        self._seed_ready("10.000")
        so = self.client.post(
            "/api/selling/sales-orders/",
            {
                "name": "SO-DEFAULT-WH-2",
                "customer": self.customer.id,
                "company": self.company.id,
                "transaction_date": "2026-04-02",
                "items": [{"item": self.item.id, "qty": "3.000", "rate": "120.00"}],
            },
            format="json",
        )
        self.client.post(f"/api/selling/sales-orders/{so.data['id']}/submit/")
        mk = self.client.post(
            f"/api/selling/sales-orders/{so.data['id']}/make-sales-invoice/"
        )
        self.assertEqual(mk.status_code, status.HTTP_201_CREATED, mk.content)
        self.assertTrue(mk.data["update_stock"])
        submit = self.client.post(
            f"/api/selling/sales-invoices/{mk.data['id']}/submit/"
        )
        self.assertEqual(submit.status_code, status.HTTP_200_OK, submit.content)
        self.assertEqual(self._stock(self.ready), Decimal("7.000"))
