"""Price-list resolution and credit-limit gate (design sections 5 and 6)."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import AuditLog, Company, DocStatus
from core.test_e2e import BaseAPITest
from selling.models import Customer, CustomerGroup, SalesOrder, SalesOrderItem
from stock.models import ItemPrice, PriceList
from stock.services import pricing
from users.models import User


class PricingUnitTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Spice Co", abbr="SPC", default_currency="INR"
        )
        self.group = CustomerGroup.objects.create(name="Retail", is_group=True)
        self.customer = Customer.objects.create(
            name="Retail Buyer", company=self.company, customer_group=self.group
        )
        from stock.models import Item, ItemGroup, UOM

        self.uom = UOM.objects.create(name="Kg")
        self.item_group = ItemGroup.objects.create(name="Spices", is_group=True)
        self.item = Item.objects.create(
            code="TURMERIC",
            name="Turmeric",
            item_group=self.item_group,
            stock_uom=self.uom,
        )
        self.price_list = PriceList.objects.create(name="Retail INR", selling=True)

    def _row(self, **kwargs):
        defaults = {
            "item": self.item,
            "price_list": self.price_list,
            "rate": Decimal("100.000000"),
        }
        defaults.update(kwargs)
        return ItemPrice.objects.create(**defaults)

    def test_latest_valid_from_wins_and_uom_breaks_ties(self):
        date = datetime.date(2026, 10, 28)
        self._row(valid_from=datetime.date(2026, 1, 1), rate=Decimal("90"))
        self._row(valid_from=datetime.date(2026, 10, 1), rate=Decimal("95"))
        specific = self._row(
            valid_from=datetime.date(2026, 10, 1), uom=self.uom, rate=Decimal("80")
        )
        row = pricing.item_price_for(self.item, self.price_list, date, self.uom)
        self.assertEqual(row, specific)
        self.assertEqual(row.rate, Decimal("80.000000"))

    def test_blank_uom_row_applies_to_the_stock_uom(self):
        date = datetime.date(2026, 10, 28)
        row = self._row(rate=Decimal("77"))
        self.assertEqual(
            pricing.item_price_for(self.item, self.price_list, date, self.uom), row
        )

    def test_expired_and_future_rows_are_ignored(self):
        date = datetime.date(2026, 10, 28)
        self._row(
            valid_from=datetime.date(2026, 1, 1),
            valid_upto=datetime.date(2026, 9, 30),
            rate=Decimal("10"),
        )
        self._row(
            valid_from=datetime.date(2026, 11, 1),
            rate=Decimal("20"),
        )
        self.assertIsNone(
            pricing.item_price_for(self.item, self.price_list, date, self.uom)
        )

    def test_resolution_order_customer_group_ancestor_then_company(self):
        company_list = PriceList.objects.create(name="Company List", selling=True)
        parent_list = PriceList.objects.create(name="Parent List", selling=True)
        child = CustomerGroup.objects.create(
            name="Wholesale", parent=self.group, is_group=False
        )
        self.group.default_price_list = parent_list
        self.group.save(update_fields=["default_price_list"])
        self.customer.customer_group = child
        self.customer.save(update_fields=["customer_group"])
        self.company.default_selling_price_list = company_list
        self.company.save(update_fields=["default_selling_price_list"])

        resolved, source = pricing.price_list_for(
            company=self.company, selling=True, customer=self.customer
        )
        self.assertEqual(resolved, parent_list)
        self.assertEqual(source, "customer_group")

    def test_customer_default_beats_group_and_company(self):
        own = PriceList.objects.create(name="Own List", selling=True)
        group_list = PriceList.objects.create(name="Group List", selling=True)
        self.group.default_price_list = group_list
        self.group.save(update_fields=["default_price_list"])
        self.customer.default_price_list = own
        self.customer.save(update_fields=["default_price_list"])
        resolved, source = pricing.price_list_for(
            company=self.company, selling=True, customer=self.customer
        )
        self.assertEqual((resolved, source), (own, "customer"))


class PricingApiTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        self.list = PriceList.objects.create(name="Acme Retail", selling=True)
        self.company.default_selling_price_list = self.list
        self.company.save(update_fields=["default_selling_price_list"])
        ItemPrice.objects.create(
            item=self.item,
            price_list=self.list,
            rate=Decimal("125.500000"),
        )

    def _payload(self, *, rate=None, price_list=None):
        row = {
            "item": self.item.id,
            "warehouse": self.warehouse.id,
            "qty": "10.000",
        }
        if rate is not None:
            row["rate"] = rate
        payload = {
            "customer": self.customer.id,
            "company": self.company.id,
            "transaction_date": str(datetime.date.today()),
            "items": [row],
        }
        if price_list is not None:
            payload["price_list"] = price_list
        return payload

    def test_omitted_rate_is_filled_from_the_company_default_list(self):
        res = self.client.post(
            "/api/selling/sales-orders/", self._payload(), format="json"
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["price_list"], self.list.id)
        line = res.data["items"][0]
        self.assertEqual(line["rate"], "125.50")
        self.assertEqual(line["price_list_rate"], "125.500000")

    def test_explicit_list_wins_over_the_company_default(self):
        other = PriceList.objects.create(name="Acme Export", selling=True)
        ItemPrice.objects.create(
            item=self.item,
            price_list=other,
            rate=Decimal("200.000000"),
        )
        res = self.client.post(
            "/api/selling/sales-orders/",
            self._payload(price_list=other.id),
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["price_list"], other.id)
        self.assertEqual(res.data["items"][0]["rate"], "200.00")

    def test_a_buying_only_list_is_refused_on_a_sale(self):
        buying = PriceList.objects.create(name="Vendor INR", buying=True)
        res = self.client.post(
            "/api/selling/sales-orders/",
            self._payload(price_list=buying.id),
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("not a selling list", str(res.content))

    def test_currency_mismatch_is_refused(self):
        usd = PriceList.objects.create(
            name="USD Retail", currency="USD", selling=True
        )
        res = self.client.post(
            "/api/selling/sales-orders/",
            self._payload(price_list=usd.id),
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("no currency conversion", str(res.content))

    def test_update_rates_refreshes_every_draft_line(self):
        res = self.client.post(
            "/api/selling/sales-orders/", self._payload(rate="999.00"), format="json"
        )
        self.assertEqual(res.status_code, 201, res.content)
        order_id = res.data["id"]
        ItemPrice.objects.filter(item=self.item, price_list=self.list).update(
            rate=Decimal("150.000000")
        )
        res = self.client.post(
            f"/api/selling/sales-orders/{order_id}/update-rates/", format="json"
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["changed"], 1)
        self.assertEqual(res.data["document"]["items"][0]["rate"], "150.00")

    def test_rate_endpoint_resolves_the_customer_list(self):
        res = self.client.get(
            "/api/stock/item-prices/rate/",
            {
                "item": self.item.id,
                "company": self.company.id,
                "customer": self.customer.id,
            },
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["price_list"], self.list.id)
        self.assertEqual(res.data["price_list_source"], "company")
        self.assertEqual(res.data["rate"], "125.50")

    def test_rate_endpoint_returns_nulls_when_nothing_matches(self):
        other_item = self.item.__class__.objects.create(
            code="CARDAMOM",
            name="Cardamom",
            item_group=self.item.item_group,
            stock_uom=self.uom,
        )
        res = self.client.get(
            "/api/stock/item-prices/rate/",
            {
                "item": other_item.id,
                "company": self.company.id,
                "customer": self.customer.id,
            },
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIsNone(res.data["rate"])
        self.assertEqual(res.data["price_list"], self.list.id)


class BuyingPricingTests(BaseAPITest):
    def test_supplier_default_list_fills_a_purchase_order_rate(self):
        buying = PriceList.objects.create(name="Bolt Vendor INR", buying=True)
        self.supplier.default_price_list = buying
        self.supplier.save(update_fields=["default_price_list"])
        ItemPrice.objects.create(
            item=self.item, price_list=buying, rate=Decimal("55.250000")
        )
        res = self.client.post(
            "/api/buying/purchase-orders/",
            {
                "supplier": self.supplier.id,
                "company": self.company.id,
                "transaction_date": str(datetime.date.today()),
                "items": [
                    {
                        "item": self.item.id,
                        "warehouse": self.warehouse.id,
                        "qty": "4.000",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data["price_list"], buying.id)
        self.assertEqual(res.data["items"][0]["rate"], "55.25")
        self.assertEqual(res.data["items"][0]["price_list_rate"], "55.250000")


class CreditLimitTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        self.plain = User.objects.create_user(
            username="sales_plain", email="sales@test.com", password="Pass123!"
        )
        self.customer.credit_limit = Decimal("1000.00")
        self.customer.save(update_fields=["credit_limit"])

    def _order(self, qty="6.000", rate="100.00"):
        order = SalesOrder.objects.create(
            name=f"SO-CREDIT-{SalesOrder.objects.count() + 1}",
            customer=self.customer,
            company=self.company,
            transaction_date=datetime.date.today(),
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            item=self.item,
            warehouse=self.warehouse,
            qty=Decimal(qty),
            rate=Decimal(rate),
        )
        order.calculate_taxes_and_totals()
        order.save()
        return order

    def test_submit_is_blocked_when_the_limit_would_be_crossed(self):
        order = self._order(qty="15.000")
        with self.assertRaises(ValidationError) as caught:
            order.submit(user=self.plain)
        self.assertIn("Credit limit crossed", str(caught.exception))

    def test_bypass_flag_and_zero_limit_skip_the_check(self):
        self.customer.bypass_credit_limit_check = True
        self.customer.save(update_fields=["bypass_credit_limit_check"])
        order = self._order(qty="15.000")
        order.submit(user=self.plain)
        self.assertEqual(order.docstatus, DocStatus.SUBMITTED)

        self.customer.bypass_credit_limit_check = False
        self.customer.credit_limit = Decimal("0.00")
        self.customer.save(
            update_fields=["bypass_credit_limit_check", "credit_limit"]
        )
        second = self._order(qty="999.000")
        second.submit(user=self.plain)
        self.assertEqual(second.docstatus, DocStatus.SUBMITTED)

    def test_unbilled_orders_count_towards_exposure(self):
        first = self._order(qty="6.000")
        first.submit(user=self.plain)
        second = self._order(qty="6.000")
        with self.assertRaises(ValidationError):
            second.submit(user=self.plain)

    def test_credit_controller_override_is_audited(self):
        order = self._order(qty="15.000")
        order.submit(user=self.admin)  # superuser may override
        self.assertEqual(order.docstatus, DocStatus.SUBMITTED)
        log = AuditLog.objects.filter(
            action="credit_limit_override",
            model_label="selling.SalesOrder",
            object_id=order.name,
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.changes["credit_limit"], "1000.00")
        self.assertEqual(log.changes["document_total"], "1500.00")

    def test_credit_status_endpoint_reports_exposure(self):
        first = self._order(qty="6.000")
        first.submit(user=self.plain)
        res = self.client.get(
            f"/api/selling/customers/{self.customer.id}/credit-status/"
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["credit_limit"], "1000.00")
        self.assertEqual(res.data["unbilled_orders"], "600.00")
        self.assertEqual(res.data["exposure"], "600.00")
        self.assertEqual(res.data["available"], "400.00")
        self.assertEqual(res.data["source"], "gl")
