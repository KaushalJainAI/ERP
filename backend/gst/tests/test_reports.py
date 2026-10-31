"""GST Task 4a: GSTR-1, GSTR-3B and the tax ledger."""

from decimal import Decimal

from rest_framework.test import APIClient

from accounts.models import GLEntry
from core.models import Company
from gst.reports import gstr1
from gst.tests.test_documents import PI_URL, SI_URL
from gst.tests.test_posting import PostingBase
from selling.models import SalesInvoice
from stock.models import UOM, Item
from users.models import Role, RolePermission, User, UserRole

D = Decimal

GSTR1_URL = "/api/gst/reports/gstr1/"
GSTR3B_URL = "/api/gst/reports/gstr3b/"
LEDGER_URL = "/api/gst/reports/tax-ledger/"

OCT = {"posting_date": "2026-10-05"}


class ReportBase(PostingBase):
    def setUp(self):
        super().setUp()
        self.nos = UOM.objects.get(name="Nos")
        self.nos.gst_uqc = "NOS"
        self.nos.save()
        self.walk_delhi = self.customer(
            "Walk-in Delhi", None, None, category="Unregistered", state="07"
        )
        self.sup_comp = self.supplier(
            "Comp Sup", "27", "AAACC5555C", category="Registered Composition"
        )
        self.sup_unreg_delhi = self.supplier(
            "Unreg Delhi", None, None, category="Unregistered", state="07"
        )

    def get(self, url, expect=200, **params):
        res = self.client.get(url, params)
        self.assertEqual(res.status_code, expect, res.content)
        return res.data

    def gstr1(self, month="2026-10", **extra):
        return self.get(GSTR1_URL, company=self.company.id, month=month, **extra)

    def gstr3b(self, month="2026-10"):
        return self.get(GSTR3B_URL, company=self.company.id, month=month)

    def cancel(self, url, doc):
        res = self.client.post(f"{url}{doc['id']}/cancel/")
        self.assertEqual(res.status_code, 200, res.content)

    def b2b(self, report):
        return {p["party_gstin"]: p for p in report["b2b"]}


class GSTR1B2BTests(ReportBase):
    def test_b2b_grouped_by_gstin_with_rates_and_sez_types(self):
        a = self.sale(
            self.cust_intra, (self.item18, 2, 100), (self.item5, 1, 100), **OCT
        )
        b = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        c = self.sale(self.cust_inter, (self.item18, 1, 1000), **OCT)
        sez0 = self.sale(self.cust_sez, (self.item18, 1, 500), **OCT)
        sez1 = self.sale(
            self.cust_sez, (self.item18, 1, 200), is_export_with_gst=True, **OCT
        )
        report = self.gstr1()
        parties = self.b2b(report)
        self.assertEqual(
            [p["party_gstin"] for p in report["b2b"]],
            sorted([self.cust_intra.gstin, self.cust_inter.gstin, self.cust_sez.gstin]),
        )
        intra = parties[self.cust_intra.gstin]
        self.assertEqual(intra["party_name"], "Intra Cust")
        self.assertEqual([i["name"] for i in intra["invoices"]], [a["name"], b["name"]])
        inv_a = intra["invoices"][0]
        self.assertEqual(inv_a["invoice_value"], "341.00")
        self.assertEqual(inv_a["place_of_supply"], "27")
        self.assertEqual(inv_a["reverse_charge"], "N")
        self.assertEqual(inv_a["invoice_type"], "Regular")
        self.assertEqual(
            inv_a["rates"],
            [
                {
                    "rate": "5.000",
                    "taxable_value": "100.00",
                    "igst": "0.00",
                    "cgst": "2.50",
                    "sgst": "2.50",
                },
                {
                    "rate": "18.000",
                    "taxable_value": "200.00",
                    "igst": "0.00",
                    "cgst": "18.00",
                    "sgst": "18.00",
                },
            ],
        )
        inter = parties[self.cust_inter.gstin]["invoices"][0]
        self.assertEqual(inter["name"], c["name"])
        self.assertEqual(inter["place_of_supply"], "07")
        self.assertEqual(inter["rates"][0]["igst"], "180.00")
        sez = parties[self.cust_sez.gstin]["invoices"]
        self.assertEqual(
            {i["name"]: i["invoice_type"] for i in sez},
            {
                sez0["name"]: "SEZ supplies without payment",
                sez1["name"]: "SEZ supplies with payment",
            },
        )
        zero = next(i for i in sez if i["name"] == sez0["name"])
        self.assertEqual(zero["rates"][0]["igst"], "0.00")
        paid = next(i for i in sez if i["name"] == sez1["name"])
        self.assertEqual(paid["rates"][0]["igst"], "36.00")
        self.assertEqual(report["b2cl"], [])
        self.assertEqual(report["b2cs"], [])

        summary = report["summary"]
        self.assertEqual(summary["invoice_count"], 5)
        self.assertEqual(summary["taxable_value"], "2100.00")
        self.assertEqual(summary["igst"], "216.00")
        self.assertEqual(summary["cgst"], "29.50")
        self.assertEqual(summary["sgst"], "29.50")

    def test_money_values_are_two_dp_strings(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        report = self.gstr1()
        self.assertEqual(report["company"]["state"], "27")
        self.assertEqual(report["company"]["gstin"], self.company.gstin)
        self.assertEqual(
            report["period"], {"from_date": "2026-10-01", "to_date": "2026-10-31"}
        )
        for key in ("taxable_value", "igst", "cgst", "sgst", "invoice_value"):
            self.assertRegex(report["summary"][key], r"^\d+\.\d{2}$")


class GSTR1B2CTests(ReportBase):
    def test_b2cl_threshold_after_2024_08_01(self):
        # 150,000 + 18% = 177,000: above 1,00,000 from 2024-08-01.
        d = self.sale(
            self.walk_delhi, (self.item18, 1, 150000), posting_date="2024-08-01"
        )
        report = self.gstr1("2024-08")
        self.assertEqual(report["b2cs"], [])
        self.assertEqual(len(report["b2cl"]), 1)
        group = report["b2cl"][0]
        self.assertEqual(group["place_of_supply"], "07")
        self.assertEqual(group["invoices"][0]["name"], d["name"])
        self.assertEqual(group["invoices"][0]["invoice_value"], "177000.00")
        self.assertEqual(group["invoices"][0]["rates"][0]["igst"], "27000.00")

    def test_same_invoice_is_b2cs_before_2024_08_01(self):
        # Threshold was 2,50,000 until 2024-07-31.
        self.sale(self.walk_delhi, (self.item18, 1, 150000), posting_date="2024-07-31")
        report = self.gstr1("2024-07")
        self.assertEqual(report["b2cl"], [])
        self.assertEqual(
            report["b2cs"],
            [
                {
                    "place_of_supply": "07",
                    "supply_type": "INTER",
                    "rate": "18.000",
                    "taxable_value": "150000.00",
                    "igst": "27000.00",
                    "cgst": "0.00",
                    "sgst": "0.00",
                }
            ],
        )

    def test_old_threshold_still_applies_before_the_change(self):
        # 250,000 + 18% = 295,000 > 2,50,000, so B2CL even on 2024-07-31.
        self.sale(self.walk_delhi, (self.item18, 1, 250000), posting_date="2024-07-31")
        report = self.gstr1("2024-07")
        self.assertEqual(len(report["b2cl"]), 1)
        self.assertEqual(report["b2cs"], [])

    def test_threshold_is_strictly_greater_than(self):
        at = self.sale(self.walk_delhi, (self.item_plain, 1, 100000), taxes=[], **OCT)
        over = self.sale(
            self.walk_delhi, (self.item_plain, 1, "100000.01"), taxes=[], **OCT
        )
        report = self.gstr1()
        self.assertEqual(
            [i["name"] for g in report["b2cl"] for i in g["invoices"]], [over["name"]]
        )
        self.assertEqual(report["b2cs"][0]["taxable_value"], "100000.00")
        self.assertEqual(at["name"] in str(report["b2cl"]), False)

    def test_intra_state_large_unregistered_is_never_b2cl(self):
        self.sale(self.cust_b2c, (self.item18, 1, 500000), **OCT)
        report = self.gstr1()
        self.assertEqual(report["b2cl"], [])
        self.assertEqual(report["b2cs"][0]["supply_type"], "INTRA")

    def test_b2cs_groups_by_pos_supply_type_and_rate(self):
        self.sale(self.cust_b2c, (self.item18, 1, 100), (self.item5, 1, 200), **OCT)
        self.sale(self.cust_b2c, (self.item18, 2, 100), **OCT)
        self.sale(self.walk_delhi, (self.item18, 1, 1000), **OCT)
        report = self.gstr1()
        self.assertEqual(
            [
                (r["place_of_supply"], r["supply_type"], r["rate"], r["taxable_value"])
                for r in report["b2cs"]
            ],
            [
                ("07", "INTER", "18.000", "1000.00"),
                ("27", "INTRA", "5.000", "200.00"),
                ("27", "INTRA", "18.000", "300.00"),
            ],
        )
        intra18 = report["b2cs"][2]
        self.assertEqual(
            (intra18["cgst"], intra18["sgst"], intra18["igst"]),
            ("27.00", "27.00", "0.00"),
        )
        self.assertEqual(report["b2cs"][0]["igst"], "180.00")
        self.assertEqual(report["b2b"], [])


class GSTR1ExportTests(ReportBase):
    def test_exports_wpay_and_wopay(self):
        wopay = self.sale(self.cust_overseas, (self.item18, 1, 1000), **OCT)
        wpay = self.sale(
            self.cust_overseas, (self.item18, 1, 400), is_export_with_gst=True, **OCT
        )
        report = self.gstr1()
        by_type = {e["export_type"]: e["invoices"] for e in report["exports"]}
        self.assertEqual(sorted(by_type), ["WOPAY", "WPAY"])
        self.assertEqual(by_type["WOPAY"][0]["name"], wopay["name"])
        self.assertEqual(by_type["WOPAY"][0]["rates"][0]["igst"], "0.00")
        self.assertEqual(by_type["WOPAY"][0]["place_of_supply"], "96")
        self.assertEqual(by_type["WPAY"][0]["name"], wpay["name"])
        self.assertEqual(by_type["WPAY"][0]["rates"][0]["igst"], "72.00")
        self.assertEqual(report["b2b"], [])
        self.assertEqual(report["b2cl"], [])
        self.assertEqual(report["b2cs"], [])


class GSTR1NilAndHSNTests(ReportBase):
    def test_nil_exempt_non_gst_buckets(self):
        self.sale(
            self.cust_intra,
            (self.item_nil, 1, 100),
            (self.item_exempt, 1, 50),
            (self.item_nongst, 1, 25),
            (self.item18, 1, 100),
            **OCT,
        )
        self.sale(self.cust_inter, (self.item_nil, 1, 200), **OCT)
        self.sale(self.cust_b2c, (self.item_exempt, 1, 70), **OCT)
        self.sale(self.walk_delhi, (self.item_nongst, 1, 30), **OCT)
        self.sale(self.cust_overseas, (self.item_nil, 1, 999), **OCT)  # not in Table 8
        report = self.gstr1()
        buckets = {r["description"]: r for r in report["nil_exempt_non_gst"]}
        self.assertEqual(
            [r["description"] for r in report["nil_exempt_non_gst"]],
            [
                "Inter-State supplies to registered persons",
                "Intra-State supplies to registered persons",
                "Inter-State supplies to unregistered persons",
                "Intra-State supplies to unregistered persons",
            ],
        )

        def amounts(key):
            r = buckets[key]
            return (r["nil_rated"], r["exempted"], r["non_gst"])

        self.assertEqual(
            amounts("Inter-State supplies to registered persons"),
            ("200.00", "0.00", "0.00"),
        )
        self.assertEqual(
            amounts("Intra-State supplies to registered persons"),
            ("100.00", "50.00", "25.00"),
        )
        self.assertEqual(
            amounts("Inter-State supplies to unregistered persons"),
            ("0.00", "0.00", "30.00"),
        )
        self.assertEqual(
            amounts("Intra-State supplies to unregistered persons"),
            ("0.00", "70.00", "0.00"),
        )
        # Only the taxable line of the mixed invoice reaches the B2B table.
        inv = self.b2b(report)[self.cust_intra.gstin]["invoices"][0]
        self.assertEqual(len(inv["rates"]), 1)
        self.assertEqual(inv["rates"][0]["taxable_value"], "100.00")
        self.assertEqual(report["summary"]["taxable_value"], "100.00")
        # Nil-only invoices contribute no invoice rows.
        self.assertEqual(report["b2cs"], [])

    def test_hsn_summary_splits_b2b_b2c_with_uqc(self):
        kg = UOM.objects.create(name="Kg", gst_uqc="KGS")
        rice = Item.objects.create(
            code="RICE",
            name="Rice",
            item_group=self.item18.item_group,
            stock_uom=kg,
            is_stock_item=False,
            gst_hsn_code="100630",
            item_tax_template=self.item5.item_tax_template,
        )
        self.sale(self.cust_intra, (self.item18, 2, 100), (rice, "10.5", 50), **OCT)
        self.sale(self.cust_inter, (self.item18, 1, 100), **OCT)
        self.sale(self.cust_b2c, (self.item18, 3, 100), (rice, 1, 80), **OCT)
        self.sale(self.cust_b2c, (self.item_nongst, 1, 40), **OCT)  # excluded
        self.sale(self.cust_b2c, (self.item_nil, 1, 60), **OCT)
        report = self.gstr1()
        hsn_b2b = {(r["hsn"], r["rate"]): r for r in report["hsn"]["b2b"]}
        hsn_b2c = {(r["hsn"], r["rate"]): r for r in report["hsn"]["b2c"]}
        goods = hsn_b2b[("100610", "18.000")]
        self.assertEqual(goods["uqc"], "NOS")
        self.assertEqual(goods["total_qty"], "3.000")
        self.assertEqual(goods["taxable_value"], "300.00")
        self.assertEqual(goods["igst"], "18.00")
        self.assertEqual(goods["cgst"], "18.00")
        self.assertEqual(goods["sgst"], "18.00")
        self.assertEqual(goods["total_value"], "354.00")
        grain = hsn_b2b[("100630", "5.000")]
        self.assertEqual(grain["uqc"], "KGS")
        self.assertEqual(grain["total_qty"], "10.500")
        self.assertEqual(grain["taxable_value"], "525.00")
        self.assertEqual(
            grain["total_value"], "551.26"
        )  # 13.125 per head rounds half up
        self.assertEqual(hsn_b2c[("100610", "18.000")]["taxable_value"], "300.00")
        self.assertEqual(hsn_b2c[("100610", "18.000")]["total_qty"], "3.000")
        self.assertEqual(hsn_b2c[("100630", "5.000")]["uqc"], "KGS")
        # Nil-rated goods appear (rate 0); Non-GST goods never do.
        self.assertEqual(hsn_b2c[("100610", "0.000")]["taxable_value"], "60.00")
        self.assertEqual(len(report["hsn"]["b2c"]), 3)


class GSTR1DocsAndExclusionTests(ReportBase):
    def test_docs_issued_counts_cancelled_and_cancelled_is_excluded(self):
        first = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        mid = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        last = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        self.cancel(SI_URL, mid)
        report = self.gstr1()
        self.assertEqual(len(report["docs_issued"]), 1)
        series = report["docs_issued"][0]
        self.assertEqual(series["from"], first["name"])
        self.assertEqual(series["to"], last["name"])
        self.assertEqual(series["total"], 3)
        self.assertEqual(series["cancelled"], 1)
        self.assertTrue(series["from"].startswith(series["series"]))
        self.assertEqual(report["summary"]["invoice_count"], 2)
        self.assertEqual(report["summary"]["taxable_value"], "200.00")
        names = [i["name"] for p in report["b2b"] for i in p["invoices"]]
        self.assertEqual(names, [first["name"], last["name"]])

    def test_drafts_are_ignored_everywhere(self):
        self.create(
            SI_URL,
            self.si_body(self.cust_intra, self.lines((self.item18, 1, 100)), **OCT),
        )
        report = self.gstr1()
        self.assertEqual(report["summary"]["invoice_count"], 0)
        self.assertEqual(report["docs_issued"], [])

    def test_series_key_attributes_amendments_to_their_original(self):
        class Doc:
            def __init__(self, name, amended_from_id=None):
                self.name = name
                self.amended_from_id = amended_from_id

        self.assertEqual(gstr1._series_key(Doc("SINV-2026-0007")), ("SINV-2026-", 7))
        self.assertEqual(
            gstr1._series_key(Doc("SINV-2026-0007-1", amended_from_id=3)),
            ("SINV-2026-", 7),
        )
        self.assertEqual(gstr1._series_key(Doc("ABC")), ("ABC", 0))

    def test_legacy_invoices_are_excluded_with_a_warning(self):
        keep = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        old = self.sale(self.cust_inter, (self.item18, 1, 100), **OCT)
        SalesInvoice.objects.filter(pk=old["id"]).update(gst_category="")
        report = self.gstr1()
        self.assertEqual(report["warnings"]["legacy_invoices_without_gst_data"], 1)
        self.assertEqual(report["summary"]["invoice_count"], 1)
        self.assertEqual(
            [p["party_gstin"] for p in report["b2b"]], [self.cust_intra.gstin]
        )
        self.assertEqual(report["b2b"][0]["invoices"][0]["name"], keep["name"])
        # They were still issued, so they stay in the number series.
        self.assertEqual(report["docs_issued"][0]["total"], 2)

    def test_period_boundaries(self):
        before = self.sale(
            self.cust_intra, (self.item18, 1, 100), posting_date="2026-09-30"
        )
        first = self.sale(
            self.cust_intra, (self.item18, 1, 100), posting_date="2026-10-01"
        )
        last = self.sale(
            self.cust_intra, (self.item18, 1, 100), posting_date="2026-10-31"
        )
        after = self.sale(
            self.cust_intra, (self.item18, 1, 100), posting_date="2026-11-01"
        )
        report = self.gstr1()
        names = [i["name"] for p in report["b2b"] for i in p["invoices"]]
        self.assertEqual(names, [first["name"], last["name"]])
        self.assertNotIn(before["name"], names)
        self.assertNotIn(after["name"], names)

        explicit = self.get(
            GSTR1_URL,
            company=self.company.id,
            from_date="2026-09-30",
            to_date="2026-10-01",
        )
        names = [i["name"] for p in explicit["b2b"] for i in p["invoices"]]
        self.assertEqual(names, [before["name"], first["name"]])

    def test_other_companies_are_not_included(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        other = Company.objects.create(
            name="Other Co", abbr="OTH", gstin=self.company.gstin, state_id="27"
        )
        report = self.get(GSTR1_URL, company=other.id, month="2026-10")
        self.assertEqual(report["summary"]["invoice_count"], 0)


class ReportParameterTests(ReportBase):
    def test_bad_parameters_are_400(self):
        c = self.company.id
        for url in (GSTR1_URL, GSTR3B_URL):
            self.get(url, 400, company=c)  # no period
            self.get(url, 400, company=c, month="2026-13")
            self.get(url, 400, company=c, month="2026-1")
            self.get(url, 400, company=c, month="October")
            self.get(url, 400, company=c, from_date="2026-10-01")  # half a range
            self.get(url, 400, company=c, from_date="2026-13-01", to_date="2026-10-31")
            self.get(url, 400, company=c, from_date="nope", to_date="2026-10-31")
            self.get(url, 400, company=c, from_date="2026-10-31", to_date="2026-10-01")
            self.get(
                url, 400, company=c, month="2026-10", from_date="2026-10-01"
            )  # ambiguous
            # The active company is now server-resolved; omitting `company` uses
            # the superuser's default rather than failing.
            self.get(url, 200, month="2026-10")
            self.get(url, 400, company="abc", month="2026-10")
            self.get(url, 404, company=999999, month="2026-10")
        self.get(LEDGER_URL, 400, company=c, month="2026-00")
        self.get(LEDGER_URL, 400, company=c, from_date="31/10/2026")
        # Period is optional for the tax ledger; the active company is enough.
        self.get(LEDGER_URL, 200)

    def test_company_without_gstin_is_400(self):
        Company.objects.filter(pk=self.company.pk).update(gstin=None)
        for url in (GSTR1_URL, GSTR3B_URL, LEDGER_URL):
            res = self.get(url, 400, company=self.company.id, month="2026-10")
            self.assertIn("GSTIN", str(res["company"]))
        Company.objects.filter(pk=self.company.pk).update(gstin=" ")
        self.get(GSTR1_URL, 400, company=self.company.id, month="2026-10")

    def test_leap_february_month_range(self):
        report = self.gstr1("2028-02")
        self.assertEqual(report["period"]["to_date"], "2028-02-29")


class ReportRBACTests(ReportBase):
    def user_client(self, *labels):
        tag = f"user{len(labels)}{'_'.join(lb[-3:] for lb in labels)}"
        user = User.objects.create_user(
            username=tag,
            email=f"{tag}@test.com",
            password="x",
        )
        role = Role.objects.create(name=f"R{user.pk}")
        for label in labels:
            RolePermission.objects.create(role=role, model_label=label, can_read=True)
        UserRole.objects.create(user=user, role=role)
        client = APIClient()
        client.force_authenticate(user)
        return client

    def status(self, client, url):
        return client.get(
            url, {"company": self.company.id, "month": "2026-10"}
        ).status_code

    def test_no_roles_is_403_on_every_report(self):
        client = self.user_client()
        for url in (GSTR1_URL, GSTR3B_URL, LEDGER_URL):
            self.assertEqual(self.status(client, url), 403)

    def test_each_report_needs_its_own_read_labels(self):
        si = self.user_client("selling.SalesInvoice")
        self.assertEqual(self.status(si, GSTR1_URL), 200)
        self.assertEqual(self.status(si, GSTR3B_URL), 403)
        self.assertEqual(self.status(si, LEDGER_URL), 403)

        pi = self.user_client("buying.PurchaseInvoice", "selling.Customer")
        self.assertEqual(self.status(pi, GSTR1_URL), 403)
        self.assertEqual(self.status(pi, GSTR3B_URL), 403)

        both = self.user_client("selling.SalesInvoice", "buying.PurchaseInvoice")
        self.assertEqual(self.status(both, GSTR3B_URL), 200)
        self.assertEqual(self.status(both, LEDGER_URL), 403)

        gl = self.user_client("accounts.GLEntry")
        self.assertEqual(self.status(gl, LEDGER_URL), 200)
        self.assertEqual(self.status(gl, GSTR1_URL), 403)

    def test_anonymous_is_rejected(self):
        res = APIClient().get(
            GSTR1_URL, {"company": self.company.id, "month": "2026-10"}
        )
        self.assertIn(res.status_code, (401, 403))


class GSTR3BTests(ReportBase):
    def build_period(self):
        # Outward.
        self.sale(self.cust_intra, (self.item18, 2, 100), **OCT)  # 200: CGST/SGST 18
        self.sale(self.cust_inter, (self.item18, 1, 1000), **OCT)  # IGST 180
        self.sale(self.walk_delhi, (self.item18, 1, 500), **OCT)  # IGST 90, 3.2
        self.sale(
            self.cust_sez, (self.item18, 1, 200), is_export_with_gst=True, **OCT
        )  # 3.1(b) IGST 36
        self.sale(self.cust_overseas, (self.item18, 1, 300), **OCT)  # 3.1(b) zero tax
        self.sale(self.cust_intra, (self.item_nil, 1, 100), **OCT)  # 3.1(c)
        self.sale(self.cust_b2c, (self.item_nongst, 1, 40), **OCT)  # 3.1(e)
        # Inward.
        self.purchase(self.sup_intra, (self.item18, 1, 1000), **OCT)  # CGST/SGST 90
        self.purchase(self.sup_inter, (self.item18, 1, 500), **OCT)  # IGST 90
        self.purchase(
            self.sup_unreg, (self.item18, 1, 100), is_reverse_charge=True, **OCT
        )  # RCM CGST/SGST 9
        self.purchase(self.sup_intra, (self.item_nil, 1, 60), **OCT)  # Table 5 intra
        self.purchase(self.sup_unreg, (self.item_plain, 1, 80), taxes=[], **OCT)
        self.purchase(self.sup_comp, (self.item_plain, 1, 50), taxes=[], **OCT)
        self.purchase(self.sup_unreg_delhi, (self.item_plain, 1, 30), taxes=[], **OCT)

    def test_table_3_1_and_3_2(self):
        self.build_period()
        report = self.gstr3b()
        t31 = report["3.1"]
        self.assertEqual(
            t31["a_outward_taxable"],
            {
                "taxable_value": "1700.00",
                "igst": "270.00",
                "cgst": "18.00",
                "sgst": "18.00",
                "cess": "0.00",
            },
        )
        self.assertEqual(
            t31["b_outward_zero_rated"], {"taxable_value": "500.00", "igst": "36.00"}
        )
        self.assertEqual(t31["c_other_nil_exempt"], {"taxable_value": "100.00"})
        self.assertEqual(
            t31["d_inward_rcm"],
            {
                "taxable_value": "100.00",
                "igst": "0.00",
                "cgst": "9.00",
                "sgst": "9.00",
            },
        )
        self.assertEqual(t31["e_non_gst_outward"], {"taxable_value": "40.00"})
        self.assertEqual(
            report["3.2"],
            [
                {
                    "place_of_supply": "07",
                    "category": "Unregistered",
                    "taxable_value": "500.00",
                    "igst": "90.00",
                }
            ],
        )

    def test_table_4_itc_and_table_5(self):
        self.build_period()
        report = self.gstr3b()
        itc = report["4"]
        zero = {"igst": "0.00", "cgst": "0.00", "sgst": "0.00", "cess": "0.00"}
        self.assertEqual(
            itc["A"]["inward_rcm"],
            {"igst": "0.00", "cgst": "9.00", "sgst": "9.00", "cess": "0.00"},
        )
        self.assertEqual(
            itc["A"]["all_other_itc"],
            {"igst": "90.00", "cgst": "90.00", "sgst": "90.00", "cess": "0.00"},
        )
        for key in ("import_goods", "import_services", "isd"):
            self.assertEqual(itc["A"][key], zero)
        self.assertEqual(itc["B"]["reversed"], zero)
        self.assertEqual(itc["D_ineligible"], zero)
        self.assertEqual(
            itc["C_net_itc"],
            {"igst": "90.00", "cgst": "99.00", "sgst": "99.00", "cess": "0.00"},
        )
        # Nil purchase (60) + unregistered (80) + composition (50) intra; Delhi unreg inter.
        self.assertEqual(report["5"], {"inter_state": "30.00", "intra_state": "190.00"})

    def test_table_6_1_matches_tax_ledger_net_by_component(self):
        self.build_period()
        report = self.gstr3b()
        self.assertEqual(
            report["6.1"],
            {
                "igst": {"output": "306.00", "itc": "90.00", "net_payable": "216.00"},
                "cgst": {"output": "27.00", "itc": "99.00", "net_payable": "-72.00"},
                "sgst": {"output": "27.00", "itc": "99.00", "net_payable": "-72.00"},
            },
        )
        ledger = self.get(
            LEDGER_URL, company=self.company.id, month="2026-10", summary=1
        )
        net = ledger["net_by_component"]
        for head in ("igst", "cgst", "sgst"):
            self.assertEqual(
                net[head.upper()]["net_payable"], report["6.1"][head]["net_payable"]
            )
        self.assertEqual(
            net["CGST"],
            {
                "output": "18.00",
                "input": "99.00",
                "rcm": "9.00",
                "net_payable": "-72.00",
            },
        )
        self.assertEqual(
            net["IGST"],
            {
                "output": "306.00",
                "input": "90.00",
                "rcm": "0.00",
                "net_payable": "216.00",
            },
        )

    def test_gstr1_summary_agrees_with_gstr3b_outward(self):
        self.build_period()
        g1 = self.gstr1()["summary"]
        a = self.gstr3b()["3.1"]
        b = self.gstr3b()["3.1"]["b_outward_zero_rated"]
        self.assertEqual(
            D(g1["igst"]), D(a["a_outward_taxable"]["igst"]) + D(b["igst"])
        )
        self.assertEqual(g1["cgst"], a["a_outward_taxable"]["cgst"])

    def test_rcm_tax_comes_from_lines_not_document_totals(self):
        d = self.purchase(
            self.sup_unreg, (self.item18, 1, 100), is_reverse_charge=True, **OCT
        )
        self.assertEqual(D(d["total_taxes_and_charges"]), D("0.00"))
        report = self.gstr3b()
        self.assertEqual(report["3.1"]["d_inward_rcm"]["cgst"], "9.00")
        self.assertEqual(report["4"]["A"]["inward_rcm"]["sgst"], "9.00")
        self.assertEqual(report["4"]["A"]["all_other_itc"]["cgst"], "0.00")

    def test_cancelled_and_other_months_are_excluded(self):
        keep = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        gone = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-11-01")
        pur = self.purchase(self.sup_intra, (self.item18, 1, 100), **OCT)
        self.cancel(SI_URL, gone)
        self.cancel(PI_URL, pur)
        report = self.gstr3b()
        self.assertEqual(report["3.1"]["a_outward_taxable"]["taxable_value"], "100.00")
        self.assertEqual(report["4"]["C_net_itc"]["cgst"], "0.00")
        self.assertTrue(keep)

    def test_legacy_documents_excluded_with_warning(self):
        s = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        p = self.purchase(self.sup_intra, (self.item18, 1, 100), **OCT)
        SalesInvoice.objects.filter(pk=s["id"]).update(gst_category="")
        from buying.models import PurchaseInvoice

        PurchaseInvoice.objects.filter(pk=p["id"]).update(gst_category="")
        report = self.gstr3b()
        self.assertEqual(
            report["warnings"],
            {
                "legacy_sales_invoices_without_gst_data": 1,
                "legacy_purchase_invoices_without_gst_data": 1,
            },
        )
        self.assertEqual(report["3.1"]["a_outward_taxable"]["taxable_value"], "0.00")
        self.assertEqual(report["4"]["C_net_itc"]["cgst"], "0.00")

    def test_empty_period_is_all_zero(self):
        report = self.gstr3b("2030-01")
        self.assertEqual(report["3.2"], [])
        self.assertEqual(report["6.1"]["igst"]["net_payable"], "0.00")

    def test_ineligible_purchase_invoice_excluded_from_itc(self):
        self.purchase(self.sup_intra, (self.item18, 1, 100), **OCT, itc_eligible=True)
        self.purchase(self.sup_intra, (self.item18, 1, 200), **OCT, itc_eligible=False)

        report = self.gstr3b()
        self.assertEqual(report["4"]["A"]["all_other_itc"]["cgst"], "9.00")
        self.assertEqual(report["4"]["A"]["all_other_itc"]["sgst"], "9.00")
        self.assertEqual(report["4"]["C_net_itc"]["cgst"], "9.00")
        self.assertEqual(report["4"]["C_net_itc"]["sgst"], "9.00")
        self.assertEqual(report["4"]["D_ineligible"]["cgst"], "18.00")
        self.assertEqual(report["4"]["D_ineligible"]["sgst"], "18.00")


class TaxLedgerTests(ReportBase):
    def ledger(self, **params):
        return self.get(LEDGER_URL, company=self.company.id, **params)

    def account(self, report, name):
        return next(a for a in report["accounts"] if a["account_name"] == name)

    def test_opening_running_and_closing_balances(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-09-15")
        a = self.sale(self.cust_intra, (self.item18, 1, 200), posting_date="2026-10-02")
        b = self.sale(self.cust_intra, (self.item18, 1, 300), posting_date="2026-10-20")
        self.sale(self.cust_intra, (self.item18, 1, 400), posting_date="2026-11-03")
        report = self.ledger(month="2026-10")
        out = self.account(report, "Output CGST")
        self.assertEqual(out["direction"], "Output")
        self.assertEqual(out["component"], "CGST")
        self.assertEqual(out["opening_balance"], "-9.00")
        self.assertEqual(out["total_debit"], "0.00")
        self.assertEqual(out["total_credit"], "45.00")
        self.assertEqual(out["closing_balance"], "-54.00")
        self.assertEqual(
            [(e["voucher_no"], e["credit"], e["balance"]) for e in out["entries"]],
            [(a["name"], "18.00", "-27.00"), (b["name"], "27.00", "-54.00")],
        )
        self.assertEqual(out["entries"][0]["voucher_type"], "selling.SalesInvoice")
        self.assertEqual(out["entries"][0]["party"], "Intra Cust")
        self.assertEqual(out["entries"][0]["posting_date"], "2026-10-02")
        self.assertEqual(
            self.account(report, "Output SGST")["closing_balance"], "-54.00"
        )
        self.assertEqual(self.account(report, "Input CGST")["closing_balance"], "0.00")
        # Opening + movement = closing, on every account.
        for acc in report["accounts"]:
            self.assertEqual(
                D(acc["opening_balance"])
                + D(acc["total_debit"])
                - D(acc["total_credit"]),
                D(acc["closing_balance"]),
            )

    def test_open_ended_period_starts_from_zero(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-09-15")
        self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-11-15")
        report = self.ledger()
        out = self.account(report, "Output CGST")
        self.assertEqual(out["opening_balance"], "0.00")
        self.assertEqual(out["closing_balance"], "-18.00")
        self.assertEqual(report["period"], {"from_date": None, "to_date": None})
        up_to = self.ledger(to_date="2026-09-30")
        self.assertEqual(self.account(up_to, "Output CGST")["closing_balance"], "-9.00")

    def test_cancelled_vouchers_are_excluded(self):
        keep = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        gone = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        pur = self.purchase(self.sup_intra, (self.item18, 1, 100), **OCT)
        self.cancel(SI_URL, gone)
        self.cancel(PI_URL, pur)
        # Both the original and the reversal rows of a cancelled voucher are flagged.
        self.assertTrue(
            GLEntry.objects.filter(voucher_no=gone["name"], is_cancelled=True).exists()
        )
        report = self.ledger(month="2026-10")
        out = self.account(report, "Output CGST")
        self.assertEqual([e["voucher_no"] for e in out["entries"]], [keep["name"]])
        self.assertEqual(out["closing_balance"], "-9.00")
        inp = self.account(report, "Input CGST")
        self.assertEqual(inp["entries"], [])
        self.assertEqual(inp["closing_balance"], "0.00")

    def test_net_by_component_and_filters(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        self.sale(self.cust_inter, (self.item18, 1, 100), **OCT)
        self.purchase(self.sup_intra, (self.item18, 1, 1000), **OCT)
        self.purchase(
            self.sup_unreg, (self.item18, 1, 100), is_reverse_charge=True, **OCT
        )
        report = self.ledger(month="2026-10")
        self.assertEqual(
            report["net_by_component"]["CGST"],
            {
                "output": "9.00",
                "input": "99.00",
                "rcm": "9.00",
                "net_payable": "-81.00",
            },
        )
        self.assertEqual(
            report["net_by_component"]["IGST"],
            {"output": "18.00", "input": "0.00", "rcm": "0.00", "net_payable": "18.00"},
        )
        self.assertEqual(
            [a["account_name"] for a in report["accounts"]][:3],
            ["Output CGST", "Input CGST", "RCM Payable CGST"],
        )

        only = self.ledger(month="2026-10", component="igst", direction="Output")
        self.assertEqual([a["account_name"] for a in only["accounts"]], ["Output IGST"])
        self.assertEqual(list(only["net_by_component"]), ["IGST"])

        summary = self.ledger(month="2026-10", summary="1", component="CGST")
        self.assertTrue(all("entries" not in a for a in summary["accounts"]))
        self.assertEqual(summary["accounts"][0]["total_credit"], "9.00")

        self.get(LEDGER_URL, 400, company=self.company.id, component="XGST")
        self.get(LEDGER_URL, 400, company=self.company.id, direction="Sideways")

    def test_untagged_accounts_never_appear(self):
        self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        names = {a["account_name"] for a in self.ledger()["accounts"]}
        self.assertNotIn("Debtors", names)
        self.assertNotIn("Sales", names)
        self.assertNotIn("Round Off", names)
