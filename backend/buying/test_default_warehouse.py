"""S-4: a blank purchase line warehouse is filled with the Unprocessed warehouse."""

from decimal import Decimal

from rest_framework import status

from buying.testing import next_bill_no
from core.models import Company
from core.test_e2e import BaseAPITest
from stock.models import Warehouse


class PurchaseDefaultWarehouseTest(BaseAPITest):
    def setUp(self):
        super().setUp()
        self.unprocessed = Warehouse.objects.create(
            name="Unprocessed WH", company=self.company, is_group=False
        )
        Company.objects.filter(pk=self.company.pk).update(
            unprocessed_warehouse=self.unprocessed
        )

    def _stock(self, warehouse):
        res = self.client.get(
            f"/api/stock/reports/stock-balance/?item={self.item.id}"
            f"&warehouse={warehouse.id}"
        )
        return Decimal(res.data["total_actual_qty"])

    def _pi_payload(self, items, name="PINV-DEFAULT-WH"):
        return {
            "name": name,
            "supplier": self.supplier.id,
            "company": self.company.id,
            "posting_date": "2026-04-01",
            "update_stock": True,
            "credit_to": self.creditors.id,
            "expense_account": self.purchase_expense.id,
            "bill_no": next_bill_no(),
            "items": items,
        }

    def test_purchase_invoice_without_warehouse_posts_stock_into_unprocessed(self):
        created = self.client.post(
            "/api/buying/purchase-invoices/",
            self._pi_payload([{"item": self.item.id, "qty": "5.000", "rate": "50.00"}]),
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.content)
        self.assertEqual(created.data["items"][0]["warehouse"], self.unprocessed.id)

        submit = self.client.post(
            f"/api/buying/purchase-invoices/{created.data['id']}/submit/"
        )
        self.assertEqual(submit.status_code, status.HTTP_200_OK, submit.content)
        self.assertEqual(self._stock(self.unprocessed), Decimal("5.000"))
        self.assertEqual(self._stock(self.warehouse), Decimal("0.000"))

    def test_purchase_invoice_keeps_a_warehouse_the_user_sent(self):
        created = self.client.post(
            "/api/buying/purchase-invoices/",
            self._pi_payload(
                [
                    {
                        "item": self.item.id,
                        "warehouse": self.warehouse.id,
                        "qty": "2.000",
                        "rate": "50.00",
                    }
                ],
                name="PINV-KEEP-WH",
            ),
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.content)
        self.assertEqual(created.data["items"][0]["warehouse"], self.warehouse.id)

    def test_purchase_order_blank_warehouse_still_moves_stock_when_billed(self):
        po = self.client.post(
            "/api/buying/purchase-orders/",
            {
                "name": "PO-DEFAULT-WH",
                "supplier": self.supplier.id,
                "company": self.company.id,
                "transaction_date": "2026-04-01",
                "items": [{"item": self.item.id, "qty": "7.000", "rate": "40.00"}],
            },
            format="json",
        )
        self.assertEqual(po.status_code, status.HTTP_201_CREATED, po.content)
        self.assertEqual(po.data["items"][0]["warehouse"], self.unprocessed.id)
        self.client.post(f"/api/buying/purchase-orders/{po.data['id']}/submit/")

        mk = self.client.post(
            f"/api/buying/purchase-orders/{po.data['id']}/make-purchase-invoice/"
        )
        self.assertEqual(mk.status_code, status.HTTP_201_CREATED, mk.content)
        self.assertTrue(mk.data["update_stock"])
        self.assertEqual(mk.data["items"][0]["warehouse"], self.unprocessed.id)
        patched = self.client.patch(
            f"/api/buying/purchase-invoices/{mk.data['id']}/",
            {"bill_no": next_bill_no()},
            format="json",
        )
        self.assertEqual(patched.status_code, status.HTTP_200_OK, patched.content)

        submit = self.client.post(
            f"/api/buying/purchase-invoices/{mk.data['id']}/submit/"
        )
        self.assertEqual(submit.status_code, status.HTTP_200_OK, submit.content)
        self.assertEqual(self._stock(self.unprocessed), Decimal("7.000"))
