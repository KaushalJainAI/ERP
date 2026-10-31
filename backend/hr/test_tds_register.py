"""TDS register (T-7).  Run with ``pytest hr/test_tds_register.py``.

The register total must equal the ledger movement on the TDS payable account
(``ledger.matches``).  Dates are FY 2026-27 quarters.
"""

import datetime
from decimal import Decimal as D

from rest_framework.test import APIClient

from accounts.models import TaxPayment, TaxType
from hr.services.tax_rates import seed_income_tax_rates
from hr.test_tds import TdsBase

d = datetime.date
API = "/api/hr/"
REGISTER = API + "reports/tds-register/"


class TdsRegisterTests(TdsBase):
    def setUp(self):
        super().setUp()
        seed_income_tax_rates()

    def get_register(self, quarter, expect=200):
        r = self.api.get(REGISTER, {"quarter": quarter})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def post_monthly_slip(self, emp, start, end):
        self.present(emp, start, end)
        slip = self.make_slip(emp, start, end)
        self.submit_slip(slip["id"])
        return slip

    def test_register_total_equals_ledger_movement(self):
        first = self.monthly_employee(D("150000"))
        second = self.monthly_employee(D("150000"), pan="BCDEF2345G")
        self.post_monthly_slip(first, d(2026, 4, 1), d(2026, 4, 30))
        self.post_monthly_slip(second, d(2026, 4, 1), d(2026, 4, 30))

        report = self.get_register("2026-27-Q1")

        self.assertEqual(report["total_tds_deducted"], "25133.34")
        self.assertEqual(report["ledger"]["movement"], "25133.34")
        self.assertTrue(report["ledger"]["matches"])
        self.assertEqual(len(report["rows"]), 2)
        self.assertEqual({row["tds_deducted"] for row in report["rows"]}, {"12566.67"})
        self.assertEqual(
            {row["pan"] for row in report["rows"]}, {"ABCDE1234F", "BCDEF2345G"}
        )
        self.assertEqual(report["total_pay"], "300000.00")
        self.verify()

    def test_quarter_is_by_posting_date(self):
        emp = self.monthly_employee(D("150000"))
        self.post_monthly_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        self.post_monthly_slip(emp, d(2026, 7, 1), d(2026, 7, 31))

        q1 = self.get_register("2026-27-Q1")
        q2 = self.get_register("2026-27-Q2")

        self.assertEqual(q1["total_tds_deducted"], "12566.67")
        self.assertEqual(q2["total_tds_deducted"], "12566.67")
        self.assertEqual(q2["from_date"], "2026-07-01")
        self.assertEqual(q2["to_date"], "2026-09-30")
        self.assertTrue(q1["ledger"]["matches"] and q2["ledger"]["matches"])

    def test_cancelled_slip_leaves_both_register_and_ledger(self):
        emp = self.monthly_employee(D("150000"))
        slip = self.post_monthly_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        r = self.api.post(f"{API}salary-slips/{slip['id']}/cancel/")
        self.assertEqual(r.status_code, 200, r.content)

        report = self.get_register("2026-27-Q1")

        self.assertEqual(report["rows"], [])
        self.assertEqual(report["total_tds_deducted"], "0.00")
        self.assertEqual(report["ledger"]["movement"], "0.00")
        self.assertTrue(report["ledger"]["matches"])

    def test_daily_wager_is_listed_with_zero_tds(self):
        from hr.models import PayType

        emp = self.employee("Ramu", pay_type=PayType.DAILY, pan="CDEFG3456H")
        self.rate(emp, daily="500", ot="0")
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        slip = self.make_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        self.submit_slip(slip["id"])

        report = self.get_register("2026-27-Q1")

        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0]["tds_deducted"], "0.00")
        self.assertEqual(report["rows"][0]["pay"], "15000.00")
        self.assertTrue(report["ledger"]["matches"])

    def test_deposits_show_by_month_from_tax_payments(self):
        emp = self.monthly_employee(D("150000"))
        self.post_monthly_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        payment = TaxPayment.objects.create(
            name="TAX-TDS-T7-1",
            company=self.company,
            tax_type=TaxType.TDS,
            period=d(2026, 4, 1),
            posting_date=d(2026, 5, 7),
            challan_no="CH-T7-APR",
            paid_from=self.bank,
            tds_amount=D("12566.67"),
        )
        payment.submit()

        report = self.get_register("2026-27-Q1")

        by_month = {row["period"]: row["amount"] for row in report["deposits_by_month"]}
        self.assertEqual(
            by_month, {"2026-04": "12566.67", "2026-05": "0.00", "2026-06": "0.00"}
        )
        self.assertEqual(report["total_deposited"], "12566.67")
        self.assertEqual(report["challans"][0]["challan_no"], "CH-T7-APR")
        # Deposits move the payable, not the salary-slip movement the register checks.
        self.assertEqual(report["ledger"]["movement"], "12566.67")
        self.assertTrue(report["ledger"]["matches"])

    def test_quarter_label_is_checked(self):
        for label in ("", "2026-Q1", "2026-27-Q5", "2026-28-Q1", "2026-27-Q0"):
            with self.subTest(label=label):
                r = self.api.get(REGISTER, {"quarter": label})
                self.assertEqual(r.status_code, 400, r.content)

    def test_quarter_with_no_slips_is_all_zero_and_matches(self):
        report = self.get_register("2027-28-Q1")
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["total_tds_deducted"], "0.00")
        self.assertTrue(report["ledger"]["matches"])

    def test_register_needs_authentication(self):
        r = APIClient().get(REGISTER, {"quarter": "2026-27-Q1"})
        self.assertIn(r.status_code, (401, 403))
