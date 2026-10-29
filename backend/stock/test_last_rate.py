"""A line without a rate and without a price-list row takes the party's last billed rate."""

import datetime
from decimal import Decimal

from core.test_e2e import BaseAPITest
from legacy.models import TallyItemLine, TallyRecord


class LastPartyRateTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        record = TallyRecord.objects.create(
            company=self.company,
            kind="voucher",
            company_year="200000",
            fiscal_year="2025-26",
            guid="v-1",
            base_voucher_type="Sales",
            voucher_no="2526/9",
            posting_date=datetime.date(2025, 6, 1),
            raw="",
        )
        # 10 billed at 50 with a 10% discount: the effective rate is 45.
        TallyItemLine.objects.create(
            company=self.company,
            record=record,
            fiscal_year="2025-26",
            posting_date=datetime.date(2025, 6, 1),
            voucher_type="Sales",
            customer=self.customer,
            item_name="Widget",
            item=self.item,
            is_inward=False,
            qty=Decimal("10.000"),
            billed_qty=Decimal("10.000"),
            rate=Decimal("50.000000"),
            discount_percent=Decimal("10.0000"),
            amount=Decimal("450.00"),
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

    def test_rate_lookup_returns_last_tally_rate(self):
        res = self.client.get(
            "/api/stock/item-prices/rate/",
            {"item": self.item.id, "customer": self.customer.id},
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["rate"], "45.00")
        self.assertEqual(res.data["rate_source"], "last_bill")
        self.assertEqual(res.data["last_rate_date"], "2025-06-01")

    def test_invoice_line_gets_last_rate_and_newer_erp_bill_wins(self):
        first = self._invoice("2026-04-01")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(Decimal(first.data["items"][0]["rate"]), Decimal("45.00"))
        # A newer submitted ERP bill at 48 becomes the party's last rate.
        newer = self._invoice("2026-04-02", rate="48.00")
        self.assertEqual(newer.status_code, 201, newer.content)
        sub = self.client.post(f"/api/selling/sales-invoices/{newer.data['id']}/submit/")
        self.assertEqual(sub.status_code, 200, sub.content)
        third = self._invoice("2026-04-03")
        self.assertEqual(Decimal(third.data["items"][0]["rate"]), Decimal("48.00"))

    def test_other_customer_falls_back_to_standard_rate(self):
        res = self.client.get("/api/stock/item-prices/rate/", {"item": self.item.id})
        self.assertNotEqual(res.data["rate_source"], "last_bill")
