import datetime
from decimal import Decimal

from rest_framework import status

from accounts.services import outstanding as outstanding_service
from accounts.services.collections import bills_to_pay
from accounts.test_outstanding import OutstandingBase
from gst.tests.test_documents import PI_URL

D = Decimal


class BillsToPayTests(OutstandingBase):
    def setUp(self):
        super().setUp()
        self.sup_intra.credit_days = 30
        self.sup_intra.save(update_fields=["credit_days"])

    def _create_pi(self, supplier, amount, posting_date, due_date, bill_no="B-01"):
        body = self.pi_body(
            supplier,
            self.lines((self.item18, 1, amount)),
            bill_no=bill_no,
            bill_date=posting_date,
            posting_date=posting_date,
            due_date=due_date,
        )
        res = self.create(PI_URL, body)
        pinv = self.pi_obj(res)
        pinv.submit()
        return pinv

    def test_totals_match_payables_ageing_total(self):
        # Create 3 purchase bills
        self._create_pi(
            self.sup_intra, 100, "2026-01-01", "2026-02-01", bill_no="B-101"
        )
        pi2 = self._create_pi(
            self.sup_intra, 200, "2026-02-01", "2026-03-01", bill_no="B-102"
        )
        self._create_pi(
            self.sup_inter, 300, "2026-03-01", "2026-04-01", bill_no="B-103"
        )

        # Part payment on pi2: 50.00
        self.pay(self.sup_intra, [(pi2, "50.00")], "50.00")

        as_on = "2026-07-15"
        res = bills_to_pay(self.company, as_on=as_on)
        ageing = outstanding_service.ageing_report(
            "Supplier",
            as_on=datetime.date.fromisoformat(as_on),
            company_id=self.company.id,
        )

        self.assertEqual(
            res["totals"]["total_payables"],
            ageing["total"]["outstanding"],
        )
        self.assertEqual(res["totals"]["bill_count"], len(ageing["rows"]))

    def test_filter_options(self):
        as_on = "2026-05-10"

        # 1. Overdue: due 2026-05-01 (< 2026-05-10)
        self._create_pi(
            self.sup_intra, 100, "2026-04-01", "2026-05-01", bill_no="B-OVERDUE"
        )
        # 2. Due this week (within 7 days): due 2026-05-15 (5 days away)
        self._create_pi(
            self.sup_intra, 100, "2026-04-15", "2026-05-15", bill_no="B-WEEK"
        )
        # 3. Due this month (within 30 days): due 2026-06-01 (22 days away)
        self._create_pi(
            self.sup_intra, 100, "2026-05-01", "2026-06-01", bill_no="B-MONTH"
        )
        # 4. Due later: due 2026-07-01 (> 30 days)
        self._create_pi(
            self.sup_intra, 100, "2026-05-01", "2026-07-01", bill_no="B-LATER"
        )

        # All bills
        res_all = bills_to_pay(self.company, as_on=as_on, filter_type=None)
        self.assertEqual(res_all["totals"]["bill_count"], 4)
        self.assertEqual(res_all["totals"]["filtered_count"], 4)
        self.assertEqual(len(res_all["bills"]), 4)

        # Overdue filter
        res_overdue = bills_to_pay(self.company, as_on=as_on, filter_type="overdue")
        self.assertEqual(res_overdue["totals"]["bill_count"], 4)
        self.assertEqual(res_overdue["totals"]["filtered_count"], 1)
        self.assertEqual(len(res_overdue["bills"]), 1)
        self.assertEqual(res_overdue["bills"][0]["bill_no"], "B-OVERDUE")
        self.assertTrue(res_overdue["bills"][0]["is_overdue"])

        # Week filter
        res_week = bills_to_pay(self.company, as_on=as_on, filter_type="week")
        self.assertEqual(res_week["totals"]["bill_count"], 4)
        self.assertEqual(res_week["totals"]["filtered_count"], 1)
        self.assertEqual(len(res_week["bills"]), 1)
        self.assertEqual(res_week["bills"][0]["bill_no"], "B-WEEK")

        # Month filter (includes week and month: 0 <= diff <= 30)
        res_month = bills_to_pay(self.company, as_on=as_on, filter_type="month")
        self.assertEqual(res_month["totals"]["bill_count"], 4)
        self.assertEqual(res_month["totals"]["filtered_count"], 2)
        bill_nos = {b["bill_no"] for b in res_month["bills"]}
        self.assertEqual(bill_nos, {"B-WEEK", "B-MONTH"})

        # Grand totals check
        self.assertEqual(res_all["totals"]["total_payables"], "472.00")  # 4 * 118
        self.assertEqual(res_all["totals"]["total_overdue"], "118.00")
        self.assertEqual(res_all["totals"]["due_this_week"], "118.00")
        self.assertEqual(res_all["totals"]["due_this_month"], "236.00")  # week + month

    def test_gst_180_day_warning(self):
        # Invoice posted 160 days before as_on with GST
        as_on = "2026-06-10"
        self._create_pi(
            self.sup_intra, 100, "2026-01-01", "2026-02-01", bill_no="B-OLD-GST"
        )

        res = bills_to_pay(self.company, as_on=as_on)
        self.assertEqual(res["totals"]["gst_warning_count"], 1)
        self.assertTrue(res["bills"][0]["gst_180_warning"])
        self.assertGreaterEqual(res["bills"][0]["days_since_posting"], 150)

    def test_api_endpoint(self):
        as_on = "2026-05-10"
        self._create_pi(
            self.sup_intra, 100, "2026-04-01", "2026-05-01", bill_no="B-API"
        )
        url = f"/api/accounts/bills-to-pay/?company={self.company.id}&as_on={as_on}&filter=overdue"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        self.assertEqual(data["totals"]["total_payables"], "118.00")
        self.assertEqual(data["totals"]["total_overdue"], "118.00")
        self.assertEqual(len(data["bills"]), 1)
        self.assertEqual(data["bills"][0]["bill_no"], "B-API")
