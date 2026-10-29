"""Print-data endpoints: ``GET /api/print/<document>/<id>/``.

Built on the GST posting fixtures (Maharashtra company; 18% item: net 100 -> CGST 9 +
SGST 9 or IGST 18; 5% item: 2.5 + 2.5 or IGST 5).
"""

import datetime
from decimal import Decimal

from rest_framework.test import APIClient

from accounts.models import PaymentEntry, PaymentEntryReference, PaymentType
from contacts.models import Address
from core.models import Company, NamingSeries, UserCompany
from gst.tests import test_posting
from gst.tests.test_documents import PO_URL, SI_URL, SO_URL
from printing import builders
from printing.words import amount_in_words
from selling.models import SalesInvoice
from users.models import Role, RolePermission, User, UserRole

D = Decimal
BASE = "/api/print/"


def url(kind, pk):
    return f"{BASE}{kind}/{pk}/"


class PrintBase(test_posting.PostingBase):
    def setUp(self):
        super().setUp()
        Company.objects.filter(pk=self.company.pk).update(
            legal_name="Tax Company Pvt Ltd",
            address_line1="12 Spice Road",
            city="Bhopal",
            pincode="462001",
            phone_no="0755-111111",
            email="accounts@tax.test",
            pan="AABCT1234C",
        )
        self.company.refresh_from_db()

    def get(self, path, expect=200, client=None, **params):
        res = (client or self.client).get(path, params)
        self.assertEqual(res.status_code, expect, res.content)
        return res.data

    def tax_by_component(self, data):
        return {t["component"]: D(t["tax_amount"]) for t in data["taxes"]}

    def make_return(self, original, date="2026-04-10"):
        res = self.client.post(f"{SI_URL}{original['id']}/make-sales-return/")
        self.assertEqual(res.status_code, 201, res.content)
        # The mapping's default name is longer than the 16-character GST invoice rule.
        SalesInvoice.objects.filter(pk=res.data["id"]).update(
            posting_date=date, name=f"SRET-{res.data['id']:04d}"
        )
        return self.submit(SI_URL, res.data)

    def pay(self, inv, amount, date="2026-04-05"):
        entry = PaymentEntry.objects.create(
            name=NamingSeries.next("PE-2026-"),
            payment_type=PaymentType.RECEIVE,
            posting_date=datetime.date.fromisoformat(date),
            company=self.company,
            party_type="Customer",
            party_name=SalesInvoice.objects.get(pk=inv["id"]).customer.name,
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=D(amount),
            received_amount=D(amount),
            reference_no="UTR123",
        )
        PaymentEntryReference.objects.create(
            payment_entry=entry,
            reference_doctype="selling.SalesInvoice",
            reference_name=inv["name"],
            allocated_amount=D(amount),
        )
        entry.submit()
        return entry

    def user_client(self, *labels, company=None):
        tag = f"p{len(labels)}{'_'.join(lb[-5:] for lb in labels)}"
        user = User.objects.create_user(
            username=tag, email=f"{tag}@test.com", password="x"
        )
        role = Role.objects.create(name=f"R{user.pk}")
        for label in labels:
            RolePermission.objects.create(role=role, model_label=label, can_read=True)
        UserRole.objects.create(user=user, role=role)
        UserCompany.objects.create(
            user=user, company=company or self.company, is_default=True
        )
        client = APIClient()
        client.force_authenticate(user)
        return client


class SalesInvoicePrintTests(PrintBase):
    def setUp(self):
        super().setUp()
        self.addr = Address.objects.create(
            address_title="Intra HQ",
            address_line1="5 MG Road",
            city="Pune",
            state_id="27",
            pincode="411001",
            is_primary_address=True,
            customer=self.cust_intra,
        )
        Address.objects.create(
            address_title="Intra Godown",
            address_type="Shipping",
            address_line1="Plot 9 MIDC",
            city="Nagpur",
            state_id="27",
            pincode="440001",
            is_shipping_address=True,
            customer=self.cust_intra,
        )
        # 2 x 100 @18% and 1 x 200 @5%.
        self.inv = self.sale(self.cust_intra, (self.item18, 2, 100), (self.item5, 1, 200))

    def test_header_blocks_and_title(self):
        data = self.get(url("sales-invoice", self.inv["id"]))
        self.assertEqual(data["title_key"], "tax_invoice")
        self.assertEqual(data["title"], "Tax Invoice")
        self.assertEqual(data["name"], self.inv["name"])
        self.assertEqual(data["date"], "2026-04-01")
        self.assertFalse(data["is_draft"])
        self.assertFalse(data["is_return"])
        self.assertIsNone(data["return_against"])
        co = data["company"]
        self.assertEqual(co["name"], "Tax Co")
        self.assertEqual(co["legal_name"], "Tax Company Pvt Ltd")
        self.assertEqual(co["gstin"], self.company.gstin)
        self.assertEqual(co["state"], {"code": "27", "name": co["state"]["name"]})
        self.assertEqual((co["address_line1"], co["city"]), ("12 Spice Road", "Bhopal"))
        # No bank / logo / terms on the Company master yet: nothing is invented.
        for missing in ("bank", "logo_url", "terms", "fssai_license_no"):
            self.assertNotIn(missing, co)
        bill, ship = data["bill_to"], data["ship_to"]
        self.assertEqual(bill["name"], "Intra Cust")
        self.assertEqual(bill["gstin"], self.cust_intra.gstin)
        self.assertEqual(bill["address"]["city"], "Pune")
        self.assertEqual(ship["address"]["city"], "Nagpur")
        self.assertFalse(ship["same_as_billing"])
        self.assertEqual(data["place_of_supply"]["code"], "27")

    def test_ship_to_falls_back_to_billing(self):
        d = self.sale(self.cust_inter, (self.item18, 1, 100))
        Address.objects.create(
            address_title="Delhi",
            address_line1="1 Rajpath",
            city="Delhi",
            state_id="07",
            is_primary_address=True,
            customer=self.cust_inter,
        )
        data = self.get(url("sales-invoice", d["id"]))
        self.assertTrue(data["ship_to"]["same_as_billing"])
        self.assertEqual(data["ship_to"]["address"]["city"], "Delhi")

    def test_totals_equal_the_document(self):
        data = self.get(url("sales-invoice", self.inv["id"]))
        t = data["totals"]
        for key in (
            "net_total",
            "total_taxes_and_charges",
            "grand_total",
            "rounding_adjustment",
            "rounded_total",
        ):
            self.assertEqual(D(t[key]), D(self.inv[key]), key)
        self.assertEqual(t["net_total"], "400.00")
        self.assertEqual(t["grand_total"], "441.00")  # 400 + 36 + 5
        self.assertEqual(t["total_qty"], "3.000")
        self.assertEqual(
            t["in_words"], "Rupees Four Hundred Forty One Only"
        )
        self.assertEqual(
            sum(D(r["amount"]) for r in data["lines"]), D(t["net_total"])
        )

    def test_lines(self):
        data = self.get(url("sales-invoice", self.inv["id"]))
        first, second = data["lines"]
        self.assertEqual(
            (first["idx"], first["item_code"], first["hsn"], first["qty"], first["uom"]),
            (1, "I18", "100610", "2.000", "Nos"),
        )
        self.assertEqual((first["rate"], first["amount"]), ("100.00", "200.00"))
        self.assertEqual(first["gst_rate"], "18")
        self.assertEqual((first["cgst_amount"], first["sgst_amount"]), ("18.00", "18.00"))
        self.assertEqual(second["gst_rate"], "5")

    def test_intra_state_hsn_summary_matches_tax_rows(self):
        data = self.get(url("sales-invoice", self.inv["id"]))
        hs = data["hsn_summary"]
        self.assertEqual(hs["mode"], "cgst_sgst")
        # One row per (HSN, rate): both items share HSN 100610 but not the rate.
        self.assertEqual(
            [(r["hsn"], r["gst_rate"]) for r in hs["rows"]],
            [("100610", "18"), ("100610", "5")],
        )
        tax = self.tax_by_component(data)
        self.assertEqual(D(hs["totals"]["cgst"]), tax["CGST"])
        self.assertEqual(D(hs["totals"]["sgst"]), tax["SGST"])
        self.assertEqual(D(hs["totals"]["igst"]), D("0"))
        self.assertEqual(D(hs["totals"]["taxable_value"]), D(data["totals"]["net_total"]))
        self.assertEqual(
            D(hs["totals"]["total_tax"]), D(data["totals"]["total_taxes_and_charges"])
        )
        row18 = hs["rows"][0]
        self.assertEqual(
            (row18["taxable_value"], row18["cgst"], row18["sgst"], row18["total_tax"]),
            ("200.00", "18.00", "18.00", "36.00"),
        )
        self.assertEqual(sum(D(r["cgst"]) for r in hs["rows"]), tax["CGST"])

    def test_inter_state_uses_igst_only(self):
        d = self.sale(self.cust_inter, (self.item18, 2, 100))
        data = self.get(url("sales-invoice", d["id"]))
        hs = data["hsn_summary"]
        self.assertEqual(hs["mode"], "igst")
        self.assertEqual(data["place_of_supply"]["code"], "07")
        self.assertEqual(hs["totals"]["igst"], "36.00")
        self.assertEqual((hs["totals"]["cgst"], hs["totals"]["sgst"]), ("0.00", "0.00"))
        self.assertEqual(self.tax_by_component(data), {"IGST": D("36.00")})

    def test_rounding_and_words_use_the_rounded_total(self):
        self.enable_rounding()
        d = self.sale(self.cust_intra, (self.item18, 1, "100.50"))
        data = self.get(url("sales-invoice", d["id"]))
        t = data["totals"]
        self.assertEqual((t["grand_total"], t["rounding_adjustment"], t["rounded_total"]),
                         ("118.60", "0.40", "119.00"))
        self.assertEqual(t["in_words"], "Rupees One Hundred Nineteen Only")

    def test_hindi_words_and_unknown_language(self):
        data = self.get(url("sales-invoice", self.inv["id"]), words_lang="hi")
        self.assertEqual(data["totals"]["words_lang"], "hi")
        self.assertEqual(
            data["totals"]["in_words"],
            amount_in_words(D("441.00"), "hi"),
        )
        data = self.get(url("sales-invoice", self.inv["id"]), words_lang="ta")
        self.assertEqual(data["totals"]["words_lang"], "en")

    def test_copy_selector(self):
        self.assertEqual(self.get(url("sales-invoice", self.inv["id"]))["copy"]["key"], "original")
        data = self.get(url("sales-invoice", self.inv["id"]), copy="triplicate")
        self.assertEqual(data["copy"]["key"], "triplicate")
        self.assertEqual(data["copy"]["options"], list(builders.COPIES))
        self.get(url("sales-invoice", self.inv["id"]), 400, copy="fourth")

    def test_balances(self):
        self.pay(self.inv, "100.00", "2026-04-03")
        second = self.sale(self.cust_intra, (self.item18, 1, 100), posting_date="2026-04-10")
        data = self.get(url("sales-invoice", second["id"]))
        b = data["balances"]
        self.assertEqual(b["outstanding_amount"], "118.00")
        # 441 invoiced - 100 paid, before this invoice.
        self.assertEqual(b["previous_balance"], "341.00")
        self.assertEqual(b["current_balance"], "459.00")
        first = self.get(url("sales-invoice", self.inv["id"]))["balances"]
        self.assertEqual(first["outstanding_amount"], "341.00")
        self.assertEqual(first["previous_balance"], "0.00")

    def test_discount_from_price_list_rate(self):
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_intra,
                [{"item": self.item18.id, "qty": "2", "rate": "90", "price_list_rate": "100"}],
            ),
        )
        data = self.get(url("sales-invoice", d["id"]))
        line = data["lines"][0]
        self.assertEqual((line["discount_amount"], line["discount_percent"]), ("20.00", "10.00"))
        # A line without a price-list snapshot has no discount.
        plain = self.get(url("sales-invoice", self.inv["id"]))["lines"][0]
        self.assertIsNone(plain["discount_amount"])

    def test_draft_is_flagged_and_has_no_balances(self):
        d = self.create(
            SI_URL, self.si_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        data = self.get(url("sales-invoice", d["id"]))
        self.assertTrue(data["is_draft"])
        self.assertEqual(data["totals"]["grand_total"], "118.00")
        self.assertIsNone(data["balances"]["previous_balance"])

    def test_cancelled_is_flagged(self):
        d = self.sale(self.cust_intra, (self.item18, 1, 100))
        res = self.client.post(f"{SI_URL}{d['id']}/cancel/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(self.get(url("sales-invoice", d["id"]))["is_cancelled"])

    def test_unknown_id_is_404(self):
        self.get(url("sales-invoice", 999999), 404)


class CreditNotePrintTests(PrintBase):
    def test_credit_note_title_reference_and_signs(self):
        inv = self.sale(self.cust_intra, (self.item18, 2, 100))
        ret = self.make_return(inv)
        data = self.get(url("sales-invoice", ret["id"]))
        self.assertEqual(data["title_key"], "credit_note")
        self.assertEqual(data["title"], "Credit Note")
        self.assertTrue(data["is_return"])
        self.assertEqual(
            data["return_against"], {"name": inv["name"], "date": "2026-04-01"}
        )
        t = data["totals"]
        self.assertEqual(t["net_total"], "-200.00")
        self.assertEqual(t["grand_total"], "-236.00")
        self.assertEqual(D(t["rounded_total"]), D(ret["rounded_total"]))
        self.assertEqual(t["in_words"], "Rupees Two Hundred Thirty Six Only")
        self.assertEqual(data["lines"][0]["qty"], "-2.000")
        hs = data["hsn_summary"]
        self.assertEqual(hs["mode"], "cgst_sgst")
        self.assertEqual(hs["totals"]["cgst"], "-18.00")
        self.assertEqual(D(hs["totals"]["cgst"]), self.tax_by_component(data)["CGST"])


class DeliveryChallanTests(PrintBase):
    def setUp(self):
        super().setUp()
        self.inv = self.sale(self.cust_intra, (self.item18, 2, 100))
        self.so = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item5, 3, 50)))
        )

    PRICE_KEYS = ("rate", "amount", "price_list_rate", "gst_rate", "cgst_amount", "igst_amount")

    def test_challan_from_invoice_has_no_prices(self):
        data = self.get(url("delivery-challan/sales-invoice", self.inv["id"]))
        self.assertEqual(data["title_key"], "delivery_challan")
        self.assertFalse(data["with_prices"])
        self.assertEqual(
            data["source"], {"type": "Sales Invoice", "name": self.inv["name"], "date": "2026-04-01"}
        )
        for key in ("totals", "taxes", "hsn_summary", "balances"):
            self.assertNotIn(key, data)
        line = data["lines"][0]
        for key in self.PRICE_KEYS:
            self.assertNotIn(key, line)
        self.assertEqual((line["item_code"], line["qty"], line["uom"]), ("I18", "2.000", "Nos"))
        self.assertEqual(data["total_qty"], "2.000")
        self.assertEqual(data["ship_to"]["name"], "Intra Cust")

    def test_challan_from_order_with_prices(self):
        data = self.get(url("delivery-challan/sales-order", self.so["id"]))
        self.assertEqual(data["source"]["type"], "Sales Order")
        self.assertNotIn("totals", data)
        priced = self.get(
            url("delivery-challan/sales-order", self.so["id"]), with_prices="1"
        )
        self.assertTrue(priced["with_prices"])
        self.assertEqual(priced["lines"][0]["rate"], "50.00")
        self.assertEqual(priced["totals"]["net_total"], "150.00")
        self.assertEqual(D(priced["totals"]["grand_total"]), D(self.so["grand_total"]))
        self.assertEqual(priced["totals"]["grand_total"], "157.50")

    def test_challan_rbac_follows_the_source_document(self):
        so_only = self.user_client("selling.SalesOrder")
        self.get(url("delivery-challan/sales-order", self.so["id"]), client=so_only)
        self.get(url("delivery-challan/sales-invoice", self.inv["id"]), 403, client=so_only)
        self.get(url("sales-invoice", self.inv["id"]), 403, client=so_only)


class PurchasePrintTests(PrintBase):
    def test_purchase_order(self):
        po = self.create(PO_URL, self.po_body(self.sup_intra, self.lines((self.item18, 5, 20))))
        data = self.get(url("purchase-order", po["id"]))
        self.assertEqual(data["title_key"], "purchase_order")
        self.assertEqual(data["bill_to"]["name"], "Intra Sup")
        self.assertEqual(D(data["totals"]["grand_total"]), D(po["grand_total"]))
        self.assertEqual(data["totals"]["grand_total"], "118.00")
        self.assertEqual(data["hsn_summary"]["totals"]["cgst"], "9.00")
        self.assertTrue(data["is_draft"])

    def test_purchase_invoice_inter_state(self):
        pi = self.purchase(self.sup_inter, (self.item18, 2, 100))
        data = self.get(url("purchase-invoice", pi["id"]))
        self.assertEqual(data["title_key"], "purchase_invoice")
        self.assertEqual(data["hsn_summary"]["mode"], "igst")
        self.assertEqual(data["totals"]["grand_total"], "236.00")
        self.assertEqual(data["balances"]["outstanding_amount"], "236.00")

    def test_purchase_rbac_is_per_model(self):
        po = self.create(PO_URL, self.po_body(self.sup_intra, self.lines((self.item18, 1, 20))))
        client = self.user_client("buying.PurchaseInvoice")
        self.get(url("purchase-order", po["id"]), 403, client=client)


class PaymentPrintTests(PrintBase):
    def test_receipt_with_allocation(self):
        inv = self.sale(self.cust_intra, (self.item18, 2, 100))
        entry = self.pay(inv, "100.00")
        data = self.get(url("payment-entry", entry.pk))
        self.assertEqual(data["title_key"], "payment_receipt")
        self.assertEqual(data["party"]["name"], "Intra Cust")
        self.assertEqual(data["payment"]["mode"], "bank")
        self.assertEqual(data["payment"]["reference_no"], "UTR123")
        self.assertEqual(data["payment"]["account"], "Bank")
        self.assertEqual(
            data["allocations"],
            [
                {
                    "reference_doctype": "selling.SalesInvoice",
                    "reference_name": inv["name"],
                    "invoice_date": "2026-04-01",
                    "invoice_total": "236.00",
                    "allocated_amount": "100.00",
                    "outstanding_after": "136.00",
                }
            ],
        )
        t = data["totals"]
        self.assertEqual((t["amount"], t["allocated"], t["unallocated"]), ("100.00", "100.00", "0.00"))
        self.assertEqual(t["in_words"], "Rupees One Hundred Only")

    def test_payment_voucher_to_supplier(self):
        pi = self.purchase(self.sup_intra, (self.item18, 1, 100))
        entry = PaymentEntry.objects.create(
            name=NamingSeries.next("PE-2026-"),
            payment_type=PaymentType.PAY,
            posting_date=datetime.date(2026, 4, 6),
            company=self.company,
            party_type="Supplier",
            party_name="Intra Sup",
            paid_from=self.bank,
            paid_to=self.creditors,
            paid_amount=D("50.50"),
            received_amount=D("50.50"),
        )
        PaymentEntryReference.objects.create(
            payment_entry=entry,
            reference_doctype="buying.PurchaseInvoice",
            reference_name=pi["name"],
            allocated_amount=D("50.00"),
        )
        data = self.get(url("payment-entry", entry.pk))
        self.assertEqual(data["title_key"], "payment_voucher")
        self.assertTrue(data["is_draft"])
        self.assertEqual(data["totals"]["unallocated"], "0.50")
        self.assertEqual(
            data["totals"]["in_words"],
            "Rupees Fifty and Fifty Paise Only",
        )
        self.assertEqual(data["party"]["party_type"], "Supplier")


class AccessTests(PrintBase):
    def setUp(self):
        super().setUp()
        self.inv = self.sale(self.cust_intra, (self.item18, 1, 100))

    def test_anonymous_is_denied(self):
        res = APIClient().get(url("sales-invoice", self.inv["id"]))
        self.assertIn(res.status_code, (401, 403))

    def test_user_without_roles_is_denied(self):
        client = self.user_client()
        self.get(url("sales-invoice", self.inv["id"]), 403, client=client)

    def test_needs_read_on_the_document_model(self):
        wrong = self.user_client("selling.SalesOrder", "buying.PurchaseInvoice")
        self.get(url("sales-invoice", self.inv["id"]), 403, client=wrong)
        right = self.user_client("selling.SalesInvoice")
        self.get(url("sales-invoice", self.inv["id"]), client=right)
        # Printing never needs more than read, but read on the invoice is not read on payments.
        self.get(url("payment-entry", 1), 403, client=right)

    def test_post_is_not_allowed(self):
        res = self.client.post(url("sales-invoice", self.inv["id"]), {})
        self.assertEqual(res.status_code, 405)

    def test_other_company_document_is_404(self):
        other = Company.objects.create(name="Other Co", abbr="OTH")
        client = self.user_client("selling.SalesInvoice", company=other)
        self.get(url("sales-invoice", self.inv["id"]), 404, client=client)
        # A superuser acting in the other company cannot reach it either.
        res = self.client.get(
            url("sales-invoice", self.inv["id"]), HTTP_X_COMPANY_ID=str(other.pk)
        )
        self.assertEqual(res.status_code, 404)
        res = self.client.get(
            url("sales-invoice", self.inv["id"]), HTTP_X_COMPANY_ID=str(self.company.pk)
        )
        self.assertEqual(res.status_code, 200)

    def test_print_does_not_change_anything(self):
        before = SalesInvoice.objects.filter(pk=self.inv["id"]).values().first()
        self.get(url("sales-invoice", self.inv["id"]))
        self.assertEqual(SalesInvoice.objects.filter(pk=self.inv["id"]).values().first(), before)
        self.integrity_ok()
