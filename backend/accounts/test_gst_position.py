import datetime
from decimal import Decimal

from rest_framework.test import APIClient

from accounts.models import TaxPayment
from accounts.services.gst_position import gst_position
from accounts.test_outstanding import OutstandingBase
from core.models import DocStatus

D = Decimal
GST_POSITION_URL = "/api/accounts/reports/gst-position/"
OCT = {"posting_date": "2026-10-05"}


class GstPositionTests(OutstandingBase):
    def test_gst_position_service(self):
        # 1. Sale: 18% on 1000 -> 90 CGST, 90 SGST (Total output = 180.00)
        self.sale(self.cust_intra, (self.item18, 1, 1000), **OCT)

        # 2. Eligible Purchase: 18% on 400 -> 36 CGST, 36 SGST (Total eligible ITC = 72.00)
        self.purchase(self.sup_intra, (self.item18, 1, 400), **OCT, itc_eligible=True)

        # 3. Ineligible Purchase: 18% on 200 -> 18 CGST, 18 SGST (Must be excluded from ITC)
        self.purchase(self.sup_intra, (self.item18, 1, 200), **OCT, itc_eligible=False)

        # 4. Sale in different month: November
        self.sale(self.cust_intra, (self.item18, 1, 500), posting_date="2026-11-05")

        # Position for October 2026
        res = gst_position(self.company, "2026-10")
        self.assertEqual(res["output_tax"], "180.00")
        self.assertEqual(res["input_credit"], "72.00")
        # Net payable = 180 - 72 = 108.00
        self.assertEqual(res["net_payable"], "108.00")
        self.assertEqual(res["paid"], "0.00")
        self.assertEqual(res["status"], "Due")
        self.assertEqual(res["due_date"], "2026-11-20")

        # 5. Pay part of the liability (50.00) -> still Due
        tp1 = TaxPayment.objects.create(
            name="TAX-2026-P01",
            company=self.company,
            tax_type="GST",
            period=datetime.date(2026, 10, 1),
            posting_date=datetime.date(2026, 10, 15),
            challan_no="CH-P01",
            paid_from=self.bank,
            cgst_cash=D("25.00"),
            sgst_cash=D("25.00"),
        )
        tp1.submit()

        res = gst_position(self.company, "2026-10")
        self.assertEqual(res["paid"], "50.00")
        self.assertEqual(res["status"], "Due")

        # 6. Pay remainder (58.00) -> Paid
        tp2 = TaxPayment.objects.create(
            name="TAX-2026-P02",
            company=self.company,
            tax_type="GST",
            period=datetime.date(2026, 10, 1),
            posting_date=datetime.date(2026, 10, 19),
            challan_no="CH-P02",
            paid_from=self.bank,
            cgst_cash=D("29.00"),
            sgst_cash=D("29.00"),
        )
        tp2.submit()

        res = gst_position(self.company, "2026-10")
        self.assertEqual(res["paid"], "108.00")
        self.assertEqual(res["status"], "Paid")

    def test_gst_position_zero_net_payable_is_paid(self):
        # Input credit exceeds output tax
        self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        self.purchase(self.sup_intra, (self.item18, 1, 300), **OCT, itc_eligible=True)

        res = gst_position(self.company, "2026-10")
        self.assertEqual(res["output_tax"], "18.00")
        self.assertEqual(res["input_credit"], "54.00")
        self.assertEqual(res["net_payable"], "0.00")
        self.assertEqual(res["status"], "Paid")

    def test_gst_position_api_endpoint(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)

        self.sale(self.cust_intra, (self.item18, 1, 1000), **OCT)

        res = client.get(
            GST_POSITION_URL,
            {"month": "2026-10", "company": self.company.id},
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["output_tax"], "180.00")
        self.assertEqual(res.data["status"], "Due")
        self.assertEqual(res.data["due_date"], "2026-11-20")

        # Missing month param -> 400
        res = client.get(GST_POSITION_URL, {"company": self.company.id})
        self.assertEqual(res.status_code, 400)
