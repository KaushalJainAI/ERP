"""A line without a rate and without a price-list row takes the party's last billed rate (ERP bills)."""

import datetime
from decimal import Decimal

from core.test_e2e import BaseAPITest


class LastPartyRateTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        from core.models import FiscalYear

        FiscalYear.objects.get_or_create(
            name="FY2026-27",
            defaults={
                "start_date": datetime.date(2026, 4, 1),
                "end_date": datetime.date(2027, 3, 31),
                "is_closed": False,
            },
        )

    def _invoice(self, date, rate=None):
        line = {"item": self.item.id, "warehouse": self.warehouse.id, "qty": "2.000"}
        if rate is not None:
            line["rate"] = rate
        return self.client.post(
            "/api/selling/sales-invoices/",
            {
                "customer": self.customer.id,
                "posting_date": date,
                "update_stock": False,
                "items": [line],
            },
            format="json",
        )

    def _submit_sale(self, date, rate):
        res = self._invoice(date, rate=rate)
        self.assertEqual(res.status_code, 201, res.content)
        sub = self.client.post(f"/api/selling/sales-invoices/{res.data['id']}/submit/")
        self.assertEqual(sub.status_code, 200, sub.content)

    def test_rate_lookup_returns_last_erp_rate(self):
        self._submit_sale("2026-04-01", "45.00")
        res = self.client.get(
            "/api/stock/item-prices/rate/",
            {"item": self.item.id, "customer": self.customer.id},
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["rate"], "45.00")
        self.assertEqual(res.data["rate_source"], "last_bill")
        self.assertEqual(res.data["last_rate_date"], "2026-04-01")

    def test_invoice_line_gets_last_rate_and_newer_erp_bill_wins(self):
        self._submit_sale("2026-04-01", "45.00")
        first = self._invoice("2026-04-02")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(Decimal(first.data["items"][0]["rate"]), Decimal("45.00"))
        self._submit_sale("2026-04-03", "48.00")
        third = self._invoice("2026-04-04")
        self.assertEqual(Decimal(third.data["items"][0]["rate"]), Decimal("48.00"))

    def test_other_customer_falls_back_to_standard_rate(self):
        res = self.client.get("/api/stock/item-prices/rate/", {"item": self.item.id})
        self.assertNotEqual(res.data["rate_source"], "last_bill")

    def _purchase_invoice(self, date, rate=None):
        line = {"item": self.item.id, "warehouse": self.warehouse.id, "qty": "5.000"}
        if rate is not None:
            line["rate"] = rate
        return self.client.post(
            "/api/buying/purchase-invoices/",
            {
                "supplier": self.supplier.id,
                "bill_no": f"BILL-{date}",
                "posting_date": date,
                "update_stock": False,
                "items": [line],
            },
            format="json",
        )

    def _submit_purchase(self, date, rate):
        res = self._purchase_invoice(date, rate=rate)
        self.assertEqual(res.status_code, 201, res.content)
        sub = self.client.post(f"/api/buying/purchase-invoices/{res.data['id']}/submit/")
        self.assertEqual(sub.status_code, 200, sub.content)

    def test_supplier_rate_lookup_returns_last_erp_rate(self):
        self._submit_purchase("2026-04-01", "30.00")
        res = self.client.get(
            "/api/stock/item-prices/rate/",
            {"item": self.item.id, "supplier": self.supplier.id, "type": "buying"},
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["rate"], "30.00")
        self.assertEqual(res.data["rate_source"], "last_bill")
        self.assertEqual(res.data["last_rate_date"], "2026-04-01")

    def test_purchase_invoice_line_gets_last_rate_and_newer_erp_bill_wins(self):
        self._submit_purchase("2026-04-01", "32.00")
        first = self._purchase_invoice("2026-04-02")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(Decimal(first.data["items"][0]["rate"]), Decimal("32.00"))
        self._submit_purchase("2026-04-03", "35.00")
        third = self._purchase_invoice("2026-04-04")
        self.assertEqual(Decimal(third.data["items"][0]["rate"]), Decimal("35.00"))
