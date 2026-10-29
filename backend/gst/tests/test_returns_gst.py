"""Sales / Purchase returns through the API with GST: totals, GL mirror, GSTR-1
(CDNR / CDNUR / B2CS netting) and GSTR-3B signs."""

from decimal import Decimal

from rest_framework.test import APIClient

from gst.tests.test_documents import PI_URL, SI_URL
from gst.tests.test_reports import ReportBase
from users.models import User

D = Decimal
OCT = {"posting_date": "2026-10-05"}
RETURN_DATE = "2026-10-20"


class ReturnGSTBase(ReportBase):
    def make_return(self, url, doc, action, expect=201, date=RETURN_DATE):
        res = self.client.post(f"{url}{doc['id']}/{action}/")
        self.assertEqual(res.status_code, expect, res.content)
        if expect != 201:
            return res.data
        data = res.data
        patched = self.client.patch(
            f"{url}{data['id']}/", {"posting_date": date}, format="json"
        )
        self.assertEqual(patched.status_code, 200, patched.content)
        return patched.data

    def sales_return(self, doc, **kw):
        return self.make_return(SI_URL, doc, "make-sales-return", **kw)

    def purchase_return(self, doc, **kw):
        return self.make_return(PI_URL, doc, "make-purchase-return", **kw)


class SalesReturnApiTests(ReturnGSTBase):
    def test_make_return_builds_a_negative_draft_that_mirrors_the_invoice(self):
        inv = self.sale(self.cust_intra, (self.item18, 2, 100), **OCT)
        draft = self.sales_return(inv)
        self.assertTrue(draft["is_return"])
        self.assertEqual(draft["return_against"], inv["id"])
        self.assertEqual(draft["return_against_name"], inv["name"])
        self.assertTrue(draft["update_outstanding_for_self"])
        self.assertEqual(draft["docstatus"], 0)
        line = draft["items"][0]
        self.assertEqual(line["qty"], "-2.000")
        self.assertEqual(line["amount"], "-200.00")
        self.assertEqual(line["sales_invoice_item"], inv["items"][0]["id"])
        self.assertEqual(
            (line["cgst_amount"], line["sgst_amount"], line["igst_amount"]),
            ("-18.00", "-18.00", "0.00"),
        )
        self.assertEqual(
            {t["gst_component"]: t["tax_amount"] for t in draft["taxes"]},
            {"CGST": "-18.00", "SGST": "-18.00"},
        )
        self.assertEqual(draft["net_total"], "-200.00")
        self.assertEqual(draft["grand_total"], "-236.00")
        self.assertEqual(draft["rounded_total"], "-236.00")

        done = self.submit(SI_URL, draft)
        self.assertEqual(done["outstanding_amount"], "-236.00")
        self.assertEqual(done["status"], "Return")
        self.assertEqual(
            self.gl(done["name"]),
            sorted(
                [
                    ("Debtors", D("0.00"), D("236.00")),
                    ("Sales", D("200.00"), D("0.00")),
                    ("Output CGST", D("18.00"), D("0.00")),
                    ("Output SGST", D("18.00"), D("0.00")),
                ]
            ),
        )
        self.assert_balanced(done["name"])
        self.integrity_ok()  # includes the GST totals / GL-sign checks

    def test_rounding_adjustment_is_mirrored(self):
        self.enable_rounding()
        inv = self.sale(self.cust_intra, (self.item18, 1, "100.50"), **OCT)
        draft = self.sales_return(inv)
        self.assertEqual(draft["rounded_total"], "-119.00")
        self.assertEqual(draft["rounding_adjustment"], "-0.40")
        done = self.submit(SI_URL, draft)
        self.assertIn(("Round Off", D("0.40"), D("0.00")), self.gl(done["name"]))
        self.assertIn(("Debtors", D("0.00"), D("119.00")), self.gl(done["name"]))
        self.integrity_ok()

    def test_partial_return_and_over_return_through_the_api(self):
        inv = self.sale(self.cust_intra, (self.item18, 4, 100), **OCT)
        body = self.sales_return(inv)
        res = self.client.patch(
            f"{SI_URL}{body['id']}/",
            {
                "items": [
                    {
                        "item": self.item18.id,
                        "qty": "-3",
                        "rate": "100",
                        "sales_invoice_item": inv["items"][0]["id"],
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["net_total"], "-300.00")
        self.submit(SI_URL, res.data)
        second = self.sales_return(inv)
        self.assertEqual(second["items"][0]["qty"], "-1.000")
        over = self.client.patch(
            f"{SI_URL}{second['id']}/",
            {
                "items": [
                    {
                        "item": self.item18.id,
                        "qty": "-2",
                        "rate": "100",
                        "sales_invoice_item": inv["items"][0]["id"],
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(over.status_code, 200, over.content)
        res = self.client.post(f"{SI_URL}{second['id']}/submit/")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("Cannot return more than 1", str(res.data))

    def test_sign_rules_on_save(self):
        # A positive quantity on a return, and a negative one on an invoice, are 400.
        body = self.si_body(
            self.cust_intra,
            self.lines((self.item18, 1, 100)),
            is_return=True,
        )
        res = self.client.post(SI_URL, body, format="json")
        self.assertEqual(res.status_code, 400, res.content)
        body = self.si_body(self.cust_intra, self.lines((self.item18, -1, 100)))
        res = self.client.post(SI_URL, body, format="json")
        self.assertEqual(res.status_code, 400, res.content)
        # return_against needs is_return
        inv = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        body = self.si_body(
            self.cust_intra, self.lines((self.item18, 1, 100)), return_against=inv["id"]
        )
        res = self.client.post(SI_URL, body, format="json")
        self.assertEqual(res.status_code, 400, res.content)
        # a standalone return is allowed and numbered from the return series
        body = self.si_body(
            self.cust_intra, self.lines((self.item18, -1, 100)), is_return=True
        )
        res = self.client.post(SI_URL, body, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertTrue(res.data["name"].startswith("SRET-"))
        self.assertEqual(res.data["grand_total"], "-118.00")

    def test_cannot_return_a_draft_or_a_return_and_rates_stay_fixed(self):
        d = self.create(
            SI_URL, self.si_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        res = self.client.post(f"{SI_URL}{d['id']}/make-sales-return/")
        self.assertEqual(res.status_code, 400, res.content)
        inv = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        draft = self.sales_return(inv)
        again = self.client.post(f"{SI_URL}{draft['id']}/update-rates/")
        self.assertEqual(again.status_code, 409)
        done = self.submit(SI_URL, draft)
        res = self.client.post(f"{SI_URL}{done['id']}/make-sales-return/")
        self.assertEqual(res.status_code, 400, res.content)

    def test_creating_a_return_needs_create_permission_on_sales_invoice(self):
        inv = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        reader = User.objects.create_user(
            username="reader", email="r@test.com", password="x"
        )
        client = APIClient()
        client.force_authenticate(reader)
        res = client.post(f"{SI_URL}{inv['id']}/make-sales-return/")
        self.assertIn(res.status_code, (403, 404))

    def test_cancel_the_original_blocked_then_allowed(self):
        inv = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        ret = self.submit(SI_URL, self.sales_return(inv))
        res = self.client.post(f"{SI_URL}{inv['id']}/cancel/")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("cancel the linked returns", str(res.data))
        self.cancel(SI_URL, ret)
        self.cancel(SI_URL, inv)
        self.integrity_ok()

    def test_flip_to_self_is_reported_on_the_submit_response(self):
        inv = self.sale(self.cust_intra, (self.item18, 2, 100), **OCT)
        # Pay it in full by a normal receipt, so there is nothing left to reduce.
        from accounts.models import PaymentEntry

        pe = acc_mapping_payment(self, inv)
        draft = self.sales_return(inv)
        res = self.client.patch(
            f"{SI_URL}{draft['id']}/",
            {"update_outstanding_for_self": False},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        out = self.client.post(f"{SI_URL}{draft['id']}/submit/")
        self.assertEqual(out.status_code, 200, out.content)
        self.assertTrue(out.data["update_outstanding_for_self"])
        self.assertEqual(out.data["outstanding_amount"], "-236.00")
        self.assertIn("less than", out.data["notices"][0])
        self.assertTrue(PaymentEntry.objects.filter(pk=pe.pk).exists())


def acc_mapping_payment(case, inv):
    """A submitted Receive payment settling the whole invoice."""
    from accounts import mapping as acc_mapping
    from selling.models import SalesInvoice

    invoice = SalesInvoice.objects.get(pk=inv["id"])
    entry = acc_mapping.build_payment_entry(
        invoice,
        payment_type="Receive",
        party_type="Customer",
        party_name=invoice.customer.name,
        party_account=invoice.debit_to,
    )
    entry.submit()
    return entry


class GSTR1ReturnTests(ReturnGSTBase):
    def test_registered_credit_note_goes_to_cdnr_and_nets_the_summary(self):
        inv = self.sale(self.cust_intra, (self.item18, 4, 100), **OCT)
        draft = self.sales_return(inv)
        self.client.patch(
            f"{SI_URL}{draft['id']}/",
            {
                "items": [
                    {
                        "item": self.item18.id,
                        "qty": "-1",
                        "rate": "100",
                        "sales_invoice_item": inv["items"][0]["id"],
                    }
                ]
            },
            format="json",
        )
        note = self.submit(SI_URL, {"id": draft["id"]})
        report = self.gstr1()
        # The invoice stays in B2B; the note is a separate CDNR row.
        self.assertEqual(len(self.b2b(report)[self.cust_intra.gstin]["invoices"]), 1)
        (party,) = report["cdnr"]
        self.assertEqual(party["party_gstin"], self.cust_intra.gstin)
        (row,) = party["notes"]
        self.assertEqual(row["name"], note["name"])
        self.assertEqual(row["note_type"], "C")
        self.assertEqual(row["original_invoice"], inv["name"])
        self.assertEqual(row["note_value"], "-118.00")
        self.assertEqual(
            row["rates"],
            [
                {
                    "rate": "18.000",
                    "taxable_value": "-100.00",
                    "igst": "0.00",
                    "cgst": "-9.00",
                    "sgst": "-9.00",
                }
            ],
        )
        # 400 + 18% less the -100 note: the summary is net of the return.
        summary = report["summary"]
        self.assertEqual(summary["invoice_count"], 1)
        self.assertEqual(summary["credit_note_count"], 1)
        self.assertEqual(summary["taxable_value"], "300.00")
        self.assertEqual(summary["cgst"], "27.00")
        self.assertEqual(summary["invoice_value"], "354.00")
        hsn = report["hsn"]["b2b"][0]
        self.assertEqual(hsn["total_qty"], "3.000")
        self.assertEqual(report["cdnur"], [])

    def test_small_unregistered_credit_note_is_netted_in_b2cs(self):
        self.sale(self.cust_b2c, (self.item18, 2, 100), **OCT)
        inv = self.sale(self.cust_b2c, (self.item18, 2, 100), **OCT)
        self.submit(SI_URL, self.sales_return(inv))
        report = self.gstr1()
        self.assertEqual(report["cdnr"], [])
        self.assertEqual(report["cdnur"], [])
        (row,) = report["b2cs"]
        self.assertEqual(row["taxable_value"], "200.00")  # 400 sold - 200 returned
        self.assertEqual(row["cgst"], "18.00")

    def test_export_credit_note_is_cdnur(self):
        inv = self.sale(self.cust_overseas, (self.item18, 2, 100), **OCT)
        note = self.submit(SI_URL, self.sales_return(inv))
        report = self.gstr1()
        (group,) = report["cdnur"]
        self.assertEqual(group["ur_type"], "EXPWOP")
        self.assertEqual(group["notes"][0]["name"], note["name"])
        self.assertEqual(group["notes"][0]["note_value"], "-200.00")
        self.assertEqual(report["exports"][0]["invoices"][0]["name"], inv["name"])

    def test_large_inter_state_unregistered_credit_note_is_cdnur_b2cl(self):
        inv = self.sale(self.walk_delhi, (self.item18, 1, 150000), **OCT)
        note = self.submit(SI_URL, self.sales_return(inv))
        report = self.gstr1()
        (group,) = report["cdnur"]
        self.assertEqual(group["ur_type"], "B2CL")
        self.assertEqual(group["notes"][0]["name"], note["name"])
        self.assertEqual(group["notes"][0]["rates"][0]["igst"], "-27000.00")
        self.assertEqual(report["b2cs"], [])

    def test_cancelled_credit_note_leaves_the_report(self):
        inv = self.sale(self.cust_intra, (self.item18, 1, 100), **OCT)
        note = self.submit(SI_URL, self.sales_return(inv))
        self.cancel(SI_URL, note)
        report = self.gstr1()
        self.assertEqual(report["cdnr"], [])
        self.assertEqual(report["summary"]["taxable_value"], "100.00")


class GSTR3BReturnTests(ReturnGSTBase):
    def test_credit_note_reduces_outward_tax_and_debit_note_reduces_itc(self):
        inv = self.sale(self.cust_intra, (self.item18, 2, 100), **OCT)
        self.submit(SI_URL, self.sales_return(inv))
        pur = self.purchase(self.sup_intra, (self.item18, 10, 100), **OCT)
        debit = self.purchase_return(pur)
        # A debit note: negative totals, GL mirrors the purchase.
        self.assertEqual(debit["grand_total"], "-1180.00")
        done = self.submit(PI_URL, debit)
        self.assertTrue(done["name"].startswith("PRET-"))
        self.assertEqual(
            self.gl(done["name"]),
            sorted(
                [
                    ("Creditors", D("1180.00"), D("0.00")),
                    ("Purchases", D("0.00"), D("1000.00")),
                    ("Input CGST", D("0.00"), D("90.00")),
                    ("Input SGST", D("0.00"), D("90.00")),
                ]
            ),
        )
        self.assertEqual(done["outstanding_amount"], "-1180.00")
        report = self.gstr3b()
        out = report["3.1"]["a_outward_taxable"]
        self.assertEqual(out["taxable_value"], "0.00")  # 200 sold, 200 returned
        self.assertEqual((out["cgst"], out["sgst"]), ("0.00", "0.00"))
        itc = report["4"]["C_net_itc"]
        self.assertEqual((itc["cgst"], itc["sgst"]), ("0.00", "0.00"))
        self.integrity_ok()

    def test_partial_debit_note_leaves_the_remaining_itc(self):
        pur = self.purchase(self.sup_intra, (self.item18, 10, 100), **OCT)
        draft = self.purchase_return(pur)
        self.client.patch(
            f"{PI_URL}{draft['id']}/",
            {
                "items": [
                    {
                        "item": self.item18.id,
                        "qty": "-4",
                        "rate": "100",
                        "purchase_invoice_item": pur["items"][0]["id"],
                    }
                ]
            },
            format="json",
        )
        self.submit(PI_URL, {"id": draft["id"]})
        itc = self.gstr3b()["4"]["C_net_itc"]
        self.assertEqual((itc["cgst"], itc["sgst"]), ("54.00", "54.00"))

    def test_debit_note_over_return_rejected(self):
        pur = self.purchase(self.sup_intra, (self.item18, 2, 100), **OCT)
        first = self.purchase_return(pur)
        self.submit(PI_URL, first)
        res = self.client.post(f"{PI_URL}{pur['id']}/make-purchase-return/")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("already been returned in full", str(res.data))
