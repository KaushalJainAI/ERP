"""Purchase-bill charges and the supplier bill no never change GST; the GST views show
the bill no."""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command

from accounts.models import Account, GLEntry
from buying.services import charges as charges_service
from gst.tests.test_documents import PI_URL
from gst.tests.test_posting import PostingBase
from stock.models import Item, ItemGroup, Warehouse
from accounts.models import ItemTaxTemplate

D = Decimal
GST_NET = "/api/gst/reports/tax-ledger/"
PURCHASE_REGISTER = "/api/accounts/reports/purchase-register/"
PERIOD = {"from_date": "2026-04-01", "to_date": "2026-04-30"}


class PurchaseChargesGstTests(PostingBase):
    def setUp(self):
        super().setUp()
        charges_service.ensure_charge_setup(self.company)
        self.freight = Account.objects.get(company=self.company, name="Freight Inward")
        self.wh = Warehouse.objects.create(name="Godown", company=self.company)
        self.client.defaults["HTTP_X_COMPANY_ID"] = str(self.company.pk)
        self.spice = Item.objects.create(
            code="SPICE",
            name="Spice",
            item_group=ItemGroup.objects.get(name="Goods"),
            stock_uom=self.item18.stock_uom,
            gst_hsn_code="090411",
            item_tax_template=ItemTaxTemplate.objects.get(name="GST 5%"),
        )

    def body(self, charges):
        return self.pi_body(
            self.sup_intra,
            [
                {
                    "item": self.spice.pk,
                    "warehouse": self.wh.pk,
                    "qty": "100",
                    "rate": "10",
                }
            ],
            update_stock=True,
            bill_no="AB/2026/15",
            bill_date="2026-03-31",
            charges=charges,
        )

    CHARGES = [
        {
            "description": "Freight",
            "account": None,  # set per test
            "amount": "60.00",
            "included_in_bill": True,
        },
        {
            "description": "Hamali",
            "amount": "40.00",
            "distribute_by": "Qty",
            "included_in_bill": False,
        },
    ]

    def make(self):
        rows = [dict(r) for r in self.CHARGES]
        rows[0]["account"] = self.freight.pk
        d = self.create(PI_URL, self.body(rows))
        return self.submit(PI_URL, d)

    def test_gst_is_computed_on_the_goods_only(self):
        d = self.make()
        # 5% on 1000 = 50 (25 + 25); the 60 freight and 40 hamali carry no tax
        self.assertEqual(d["net_total"], "1000.00")
        self.assertEqual(d["total_taxes_and_charges"], "110.00")  # 50 GST + 60 freight
        self.assertEqual(d["grand_total"], "1110.00")
        gl = {
            e.account.name: e.debit - e.credit
            for e in GLEntry.objects.filter(voucher_no=d["name"], is_cancelled=False)
        }
        self.assertEqual(gl["Input CGST"], D("25.00"))
        self.assertEqual(gl["Input SGST"], D("25.00"))
        self.assertEqual(gl["Freight Inward"], D("60.00"))
        self.assertEqual(gl["Creditors"], D("-1110.00"))
        self.assert_balanced(d["name"])
        self.integrity_ok()
        # the item line shows GST only on its own amount
        line = d["items"][0]
        self.assertEqual(line["cgst_amount"], "25.00")
        self.assertEqual(line["landed_cost_amount"], "1100.00")

    def test_tax_ledger_shows_supplier_bill_no_and_charges_are_not_tax(self):
        d = self.make()
        res = self.client.get(GST_NET, {**PERIOD, "company": self.company.pk})
        self.assertEqual(res.status_code, 200, res.content)
        entries = [
            e
            for row in res.data["accounts"]
            for e in row.get("entries", [])
            if e["voucher_no"] == d["name"]
        ]
        self.assertTrue(entries)
        self.assertTrue(all(e["supplier_bill_no"] == "AB/2026/15" for e in entries))
        names = {row["account_name"] for row in res.data["accounts"]}
        self.assertNotIn("Freight Inward", names)

    def test_purchase_register_shows_bill_no_and_ties_out(self):
        d = self.make()
        res = self.client.get(PURCHASE_REGISTER, PERIOD)
        self.assertEqual(res.status_code, 200, res.content)
        row = [r for r in res.data["rows"] if r["name"] == d["name"]][0]
        self.assertEqual(row["bill_no"], "AB/2026/15")
        self.assertEqual(row["bill_date"], "2026-03-31")
        taxes = sum((D(v) for v in row["taxes"].values()), D("0"))
        self.assertEqual(D(row["net_total"]) + taxes, D(row["grand_total"]))
        self.assertEqual(row["taxes"]["OTHER"], "60.00")

    def test_gstr3b_ignores_the_charges(self):
        self.make()
        res = self.client.get(
            "/api/gst/reports/gstr3b/", {"month": "2026-04", "company": self.company.pk}
        )
        self.assertEqual(res.status_code, 200, res.content)
        # the input credit is exactly the 50 of GST on the goods
        net_itc = res.data["4"]["C_net_itc"]
        self.assertEqual((net_itc["cgst"], net_itc["sgst"]), ("25.00", "25.00"))
        self.assertEqual(net_itc["igst"], "0.00")
        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())
