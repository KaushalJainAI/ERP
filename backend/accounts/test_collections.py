import datetime
from decimal import Decimal

from rest_framework import status

from accounts.services import outstanding as outstanding_service
from accounts.services.collections import collections
from accounts.test_outstanding import OutstandingBase

D = Decimal


class CollectionsTests(OutstandingBase):
    def setUp(self):
        super().setUp()
        self.cust_intra.credit_days = 90
        self.cust_intra.save(update_fields=["credit_days"])
        self.cust_inter.credit_days = 90
        self.cust_inter.save(update_fields=["credit_days"])

    def test_overdue_split_on_90_day_boundary(self):
        # Bill posted on 2026-01-01. Customer credit days = 90. Due date = 2026-04-01.
        self.sale(
            self.cust_intra,
            (self.item18, 1, 100),
            posting_date="2026-01-01",
            due_date="2026-04-01",
        )

        # On day 90 (2026-04-01): not overdue
        res_day90 = collections(self.company, as_on="2026-04-01")
        self.assertEqual(res_day90["totals"]["total_receivables"], "118.00")
        self.assertEqual(res_day90["totals"]["total_overdue"], "0.00")
        self.assertEqual(res_day90["customers"][0]["days_late"], 0)
        self.assertEqual(res_day90["customers"][0]["bills"][0]["is_overdue"], False)

        # On day 91 (2026-04-02): overdue!
        res_day91 = collections(self.company, as_on="2026-04-02")
        self.assertEqual(res_day91["totals"]["total_receivables"], "118.00")
        self.assertEqual(res_day91["totals"]["total_overdue"], "118.00")
        self.assertEqual(res_day91["customers"][0]["days_late"], 1)
        self.assertEqual(res_day91["customers"][0]["bills"][0]["is_overdue"], True)

    def test_totals_match_ageing_report_total(self):
        # Create several invoices across customers with different dates
        self.si_obj(
            self.sale(
                self.cust_intra,
                (self.item18, 1, 100),
                posting_date="2026-01-01",
                due_date="2026-04-01",
            )
        )
        inv2 = self.si_obj(
            self.sale(
                self.cust_intra,
                (self.item18, 2, 100),
                posting_date="2026-02-01",
                due_date="2026-05-01",
            )
        )
        self.si_obj(
            self.sale(
                self.cust_inter,
                (self.item18, 1, 200),
                posting_date="2026-03-01",
                due_date="2026-06-01",
            )
        )

        # Part payment on inv2: 100.00
        self.receive(self.cust_intra, [(inv2, "100.00")], "100.00")

        as_on = "2026-07-15"
        col_res = collections(self.company, as_on=as_on)
        ageing_res = outstanding_service.ageing_report(
            "Customer",
            as_on=datetime.date.fromisoformat(as_on),
            company_id=self.company.id,
        )

        # Grand totals match
        self.assertEqual(
            col_res["totals"]["total_receivables"],
            ageing_res["total"]["outstanding"],
        )
        # Customer count and invoice count match
        self.assertEqual(
            col_res["totals"]["customer_count"], len(ageing_res["parties"])
        )
        self.assertEqual(col_res["totals"]["invoice_count"], len(ageing_res["rows"]))

        # Check each party's total matches ageing party outstanding
        ageing_parties = {p["party"]: p["outstanding"] for p in ageing_res["parties"]}
        for cust in col_res["customers"]:
            self.assertEqual(
                cust["total_outstanding"], ageing_parties[cust["customer_name"]]
            )

    def test_collections_api_endpoint(self):
        self.sale(
            self.cust_intra,
            (self.item18, 1, 100),
            posting_date="2026-01-01",
            due_date="2026-04-01",
        )
        url = f"/api/accounts/collections/?company={self.company.id}&as_on=2026-05-01"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        self.assertEqual(data["totals"]["total_receivables"], "118.00")
        self.assertEqual(data["totals"]["total_overdue"], "118.00")
        self.assertEqual(len(data["customers"]), 1)
        self.assertEqual(data["customers"][0]["customer_name"], self.cust_intra.name)
        self.assertEqual(len(data["customers"][0]["bills"]), 1)

    def test_collections_empty_company(self):
        res = collections(self.company, as_on="2026-05-01")
        self.assertEqual(res["totals"]["total_receivables"], "0.00")
        self.assertEqual(res["totals"]["total_overdue"], "0.00")
        self.assertEqual(len(res["customers"]), 0)
