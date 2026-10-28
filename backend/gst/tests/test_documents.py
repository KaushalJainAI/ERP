"""Tax engine on documents (GST Task 2): SO / SI / PO / PI through the API and ORM."""

from decimal import Decimal

from django.core.cache import cache
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient, APITestCase

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    ItemTaxTemplate,
    TaxTemplate,
)
from buying.models import (
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderTax,
    Supplier,
    SupplierGroup,
)
from core.models import Company, DocStatus
from gst.services import seeding
from gst.tests.test_seeding import make_chart
from gst.tests.test_validators import make_gstin
from selling.models import (
    Customer,
    CustomerGroup,
    SalesInvoice,
    SalesInvoiceItem,
    SalesInvoiceTax,
    SalesOrder,
    SalesOrderItem,
)
from stock.models import UOM, Item, ItemGroup
from users.models import Role, RolePermission, User, UserRole

D = Decimal

SO_URL = "/api/selling/sales-orders/"
SI_URL = "/api/selling/sales-invoices/"
PO_URL = "/api/buying/purchase-orders/"
PI_URL = "/api/buying/purchase-invoices/"

SAME = object()
COMPANY_GSTIN = make_gstin("27", "AABCT1234C")


class GSTDocBase(APITestCase):
    """A GST-registered Maharashtra company with seeded accounts / templates."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username="gstdoc_admin", email="gstdoc@test.com", password="x"
        )
        self.client.force_authenticate(self.admin)
        self.company = Company.objects.create(
            name="Tax Co",
            abbr="TXC",
            gstin=COMPANY_GSTIN,
            state_id="27",
            gst_category="Registered Regular",
        )
        roots = make_chart(self.company)
        self.debtors = Account.objects.create(
            name="Debtors",
            company=self.company,
            parent=roots[AccountRootType.ASSET],
            root_type=AccountRootType.ASSET,
            account_type=AccountType.RECEIVABLE,
        )
        self.creditors = Account.objects.create(
            name="Creditors",
            company=self.company,
            parent=roots[AccountRootType.LIABILITY],
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
        )
        self.income = Account.objects.create(
            name="Sales",
            company=self.company,
            parent=roots[AccountRootType.INCOME],
            root_type=AccountRootType.INCOME,
        )
        self.expense = Account.objects.create(
            name="Purchases",
            company=self.company,
            parent=roots[AccountRootType.EXPENSE],
            root_type=AccountRootType.EXPENSE,
        )
        seeding.ensure_item_tax_templates()
        seeding.seed_company_gst(self.company)
        self.tag = {
            (a.gst_direction, a.gst_component): a
            for a in Account.objects.filter(
                company=self.company, gst_direction__isnull=False
            )
        }

        self.cgroup = CustomerGroup.objects.create(name="Retail")
        self.sgroup = SupplierGroup.objects.create(name="Vendors")
        self.cust_intra = self.customer("Intra Cust", "27", "AABCI1111A")
        self.cust_inter = self.customer("Inter Cust", "07", "AAGCS4567K")
        self.cust_b2c = self.customer(
            "Walk-in", None, None, category="Unregistered", state=None
        )
        self.cust_sez = self.customer("SEZ Cust", "24", "AAGCS9087Q", category="SEZ")
        self.cust_overseas = self.customer(
            "Overseas Cust", None, None, category="Overseas", state=None
        )
        self.sup_intra = self.supplier("Intra Sup", "27", "AAACM2209L")
        self.sup_inter = self.supplier("Inter Sup", "09", "AABCG7741N")
        self.sup_unreg = self.supplier(
            "Unreg Sup", None, None, category="Unregistered", state="27"
        )

        uom = UOM.objects.create(name="Nos")
        grp = ItemGroup.objects.create(name="Goods")

        def tpl(name):
            return ItemTaxTemplate.objects.get(name=name)

        def item(code, hsn="100610", template=None, **kw):
            return Item.objects.create(
                code=code,
                name=code,
                item_group=grp,
                stock_uom=uom,
                is_stock_item=False,
                gst_hsn_code=hsn,
                item_tax_template=template,
                **kw,
            )

        self.item18 = item("I18", template=tpl("GST 18%"))
        self.item5 = item("I5", template=tpl("GST 5%"))
        self.item_plain = item("IPLAIN")  # no template: the tax row's own rate
        self.item_nil = item("INIL", is_nil_exempt=True)
        self.item_nongst = item("INONGST", hsn="", is_non_gst=True)
        self.item_exempt = item("IEXEMPT", template=tpl("Exempted"))
        self.item_nohsn = item("INOHSN", hsn="", template=tpl("GST 18%"))

    # ------------------------------------------------------------- factories

    def customer(
        self, name, gstin_state, pan, category="Registered Regular", state=SAME
    ):
        gstin = make_gstin(gstin_state, pan) if pan else None
        return Customer.objects.create(
            name=name,
            company=self.company,
            customer_group=self.cgroup,
            gstin=gstin,
            gst_category=category,
            state_id=gstin_state if state is SAME else state,
        )

    def supplier(
        self, name, gstin_state, pan, category="Registered Regular", state=SAME
    ):
        gstin = make_gstin(gstin_state, pan) if pan else None
        return Supplier.objects.create(
            name=name,
            company=self.company,
            supplier_group=self.sgroup,
            gstin=gstin,
            gst_category=category,
            state_id=gstin_state if state is SAME else state,
        )

    def lines(self, *specs):
        return [
            {"item": it.id, "qty": str(qty), "rate": str(rate)}
            for it, qty, rate in specs
        ]

    def si_body(self, customer, items, **extra):
        body = {
            "customer": customer.id,
            "company": self.company.id,
            "posting_date": "2026-04-01",
            "update_stock": False,
            "debit_to": self.debtors.id,
            "income_account": self.income.id,
            "items": items,
        }
        body.update(extra)
        return body

    def pi_body(self, supplier, items, **extra):
        body = {
            "supplier": supplier.id,
            "company": self.company.id,
            "posting_date": "2026-04-01",
            "update_stock": False,
            "credit_to": self.creditors.id,
            "expense_account": self.expense.id,
            "items": items,
        }
        body.update(extra)
        return body

    def so_body(self, customer, items, **extra):
        body = {
            "customer": customer.id,
            "company": self.company.id,
            "transaction_date": "2026-04-01",
            "items": items,
        }
        body.update(extra)
        return body

    def po_body(self, supplier, items, **extra):
        body = {
            "supplier": supplier.id,
            "company": self.company.id,
            "transaction_date": "2026-04-01",
            "items": items,
        }
        body.update(extra)
        return body

    def create(self, url, body, expect=201):
        res = self.client.post(url, body, format="json")
        self.assertEqual(res.status_code, expect, res.content)
        return res.data

    def components(self, data):
        return {t["gst_component"]: D(t["tax_amount"]) for t in data["taxes"]}

    def template(self, title):
        return TaxTemplate.objects.get(company=self.company, title=title)


class SalesTaxKindTests(GSTDocBase):
    def test_intra_state_auto_selects_cgst_sgst(self):
        d = self.create(
            SI_URL, self.si_body(self.cust_intra, self.lines((self.item18, 2, 100)))
        )
        self.assertEqual(self.components(d), {"CGST": D("18.00"), "SGST": D("18.00")})
        self.assertEqual(D(d["net_total"]), D("200.00"))
        self.assertEqual(D(d["total_taxes_and_charges"]), D("36.00"))
        self.assertEqual(D(d["grand_total"]), D("236.00"))
        self.assertEqual(D(d["rounded_total"]), D("236.00"))
        self.assertEqual(D(d["rounding_adjustment"]), D("0.00"))
        self.assertEqual(d["place_of_supply"], "27")
        self.assertEqual(d["gst_category"], "Registered Regular")
        self.assertEqual(d["party_gstin"], self.cust_intra.gstin)
        self.assertEqual(d["company_gstin"], COMPANY_GSTIN)
        self.assertEqual(d["taxes_and_charges_name"], "Output GST In-state")
        line = d["items"][0]
        self.assertEqual(line["gst_treatment"], "Taxable")
        self.assertEqual(line["gst_hsn_code"], "100610")
        self.assertEqual(D(line["gst_rate"]), D("18"))
        self.assertEqual(
            (line["cgst_amount"], line["sgst_amount"], line["igst_amount"]),
            ("18.00", "18.00", "0.00"),
        )
        self.assertEqual(line["item_tax_rate"]["CGST"], "9.000")
        rows = d["taxes"]
        self.assertEqual([r["idx"] for r in rows], [1, 2])
        self.assertEqual([r["total"] for r in rows], ["218.00", "236.00"])
        self.assertEqual(rows[0]["net_amount"], "200.00")

    def test_inter_state_auto_selects_igst(self):
        d = self.create(
            SI_URL, self.si_body(self.cust_inter, self.lines((self.item18, 2, 100)))
        )
        self.assertEqual(self.components(d), {"IGST": D("36.00")})
        self.assertEqual(D(d["grand_total"]), D("236.00"))
        self.assertEqual(d["place_of_supply"], "07")
        self.assertEqual(d["taxes_and_charges_name"], "Output GST Out-state")
        line = d["items"][0]
        self.assertEqual(
            (line["cgst_amount"], line["sgst_amount"], line["igst_amount"]),
            ("0.00", "0.00", "36.00"),
        )

    def test_b2c_defaults_place_of_supply_to_company_state(self):
        d = self.create(
            SI_URL, self.si_body(self.cust_b2c, self.lines((self.item18, 1, 100)))
        )
        self.assertEqual(d["place_of_supply"], "27")
        self.assertEqual(sorted(self.components(d)), ["CGST", "SGST"])
        self.assertIsNone(d["party_gstin"])

    def test_b2c_uses_customer_state_when_known(self):
        cust = self.customer(
            "Delhi walk-in", None, None, category="Unregistered", state="07"
        )
        d = self.create(SI_URL, self.si_body(cust, self.lines((self.item18, 1, 100))))
        self.assertEqual(d["place_of_supply"], "07")
        self.assertEqual(sorted(self.components(d)), ["IGST"])

    def test_explicit_place_of_supply_overrides_default(self):
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_b2c, self.lines((self.item18, 1, 100)), place_of_supply="29"
            ),
        )
        self.assertEqual(sorted(self.components(d)), ["IGST"])

    def test_sez_without_payment_is_zero_rated(self):
        d = self.create(
            SI_URL, self.si_body(self.cust_sez, self.lines((self.item18, 1, 100)))
        )
        self.assertEqual(d["taxes"], [])
        self.assertEqual(D(d["grand_total"]), D("100.00"))
        self.assertEqual(D(d["total_taxes_and_charges"]), D("0.00"))
        self.assertEqual(d["place_of_supply"], "24")
        res = self.client.post(
            SI_URL,
            self.si_body(
                self.cust_sez,
                self.lines((self.item18, 1, 100)),
                taxes=[{"account": self.tag[("Output", "IGST")].id, "rate": "18"}],
            ),
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("taxes", res.data)

    def test_sez_with_payment_charges_igst_even_in_same_state(self):
        sez_home = self.customer("SEZ Mumbai", "27", "AAGCS7777Q", category="SEZ")
        d = self.create(
            SI_URL,
            self.si_body(
                sez_home, self.lines((self.item18, 1, 100)), is_export_with_gst=True
            ),
        )
        self.assertEqual(self.components(d), {"IGST": D("18.00")})

    def test_overseas_export_with_and_without_payment(self):
        d = self.create(
            SI_URL, self.si_body(self.cust_overseas, self.lines((self.item18, 1, 100)))
        )
        self.assertEqual(d["place_of_supply"], "96")
        self.assertEqual(d["taxes"], [])
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_overseas,
                self.lines((self.item18, 1, 100)),
                is_export_with_gst=True,
            ),
        )
        self.assertEqual(self.components(d), {"IGST": D("18.00")})

    def test_export_flag_only_for_sez_or_overseas(self):
        res = self.client.post(
            SI_URL,
            self.si_body(
                self.cust_intra,
                self.lines((self.item18, 1, 100)),
                is_export_with_gst=True,
            ),
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("is_export_with_gst", res.data)

    def test_sales_reverse_charge_is_rejected(self):
        for url, body in (
            (
                SI_URL,
                self.si_body(
                    self.cust_intra,
                    self.lines((self.item18, 1, 100)),
                    is_reverse_charge=True,
                ),
            ),
            (
                SO_URL,
                self.so_body(
                    self.cust_intra,
                    self.lines((self.item18, 1, 100)),
                    is_reverse_charge=True,
                ),
            ),
        ):
            res = self.client.post(url, body, format="json")
            self.assertEqual(res.status_code, 400, res.content)
            self.assertIn("is_reverse_charge", res.data)

    def test_sales_order_has_the_same_engine(self):
        d = self.create(
            SO_URL, self.so_body(self.cust_inter, self.lines((self.item5, 10, 100)))
        )
        self.assertEqual(self.components(d), {"IGST": D("50.00")})
        self.assertEqual(D(d["grand_total"]), D("1050.00"))


class RateResolutionTests(GSTDocBase):
    def test_item_template_beats_row_rate_and_zero_rated_treatments(self):
        items = self.lines(
            (self.item5, 1, 100),  # template 2.5 / 2.5
            (self.item_nil, 1, 100),  # nil-rated
            (self.item_nongst, 1, 100),  # non-GST
            (self.item_plain, 1, 100),  # no template -> row rate 9
            (self.item_exempt, 1, 100),  # Exempted template
        )
        d = self.create(SI_URL, self.si_body(self.cust_intra, items))
        self.assertEqual(self.components(d), {"CGST": D("11.50"), "SGST": D("11.50")})
        self.assertEqual(D(d["grand_total"]), D("523.00"))
        treatments = [ln["gst_treatment"] for ln in d["items"]]
        self.assertEqual(
            treatments, ["Taxable", "Nil-Rated", "Non-GST", "Taxable", "Exempted"]
        )
        self.assertEqual(
            [ln["cgst_amount"] for ln in d["items"]],
            ["2.50", "0.00", "0.00", "9.00", "0.00"],
        )
        self.assertEqual(d["taxes"][0]["net_amount"], "200.00")
        self.assertEqual(d["items"][2]["gst_hsn_code"], "")

    def test_line_snapshot_ignores_later_master_change_until_resaved(self):
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        ItemTaxTemplate.objects.get(name="GST 18%").rows.update(tax_rate=D("1"))
        # the stored document is a snapshot ...
        got = self.client.get(f"{SO_URL}{d['id']}/").data
        self.assertEqual(got["grand_total"], d["grand_total"])
        # ... a draft header PATCH re-resolves from the master (draft = live)
        patched = self.client.patch(
            f"{SO_URL}{d['id']}/", {"delivery_date": "2026-05-01"}, format="json"
        ).data
        self.assertEqual(D(patched["grand_total"]), D("102.00"))


class RoundingTests(GSTDocBase):
    def test_exact_half_rounds_half_up_at_document_level_with_residual(self):
        items = self.lines((self.item_plain, 1, "0.25"), (self.item_plain, 1, "0.25"))
        d = self.create(SI_URL, self.si_body(self.cust_inter, items))
        # 18% of 0.25 = 0.045 per line -> row total 0.09 (not 0.05 + 0.05)
        self.assertEqual(self.components(d), {"IGST": D("0.09")})
        self.assertEqual([ln["igst_amount"] for ln in d["items"]], ["0.05", "0.04"])
        self.assertEqual(D(d["grand_total"]), D("0.59"))

    def test_single_line_exact_half(self):
        d = self.create(
            SI_URL,
            self.si_body(self.cust_inter, self.lines((self.item_plain, 1, "0.25"))),
        )
        self.assertEqual(self.components(d), {"IGST": D("0.05")})

    def test_whole_rupee_gst_option(self):
        Company.objects.filter(pk=self.company.pk).update(round_off_gst_values=True)
        d = self.create(
            SI_URL, self.si_body(self.cust_inter, self.lines((self.item_plain, 1, 105)))
        )
        self.assertEqual(self.components(d), {"IGST": D("19.00")})  # 18.90 -> 19
        self.assertEqual(d["items"][0]["igst_amount"], "19.00")
        self.assertEqual(D(d["grand_total"]), D("124.00"))

    def test_rounded_total_and_adjustment(self):
        Company.objects.filter(pk=self.company.pk).update(enable_rounded_total=True)
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_intra, self.lines((self.item_plain, 1, "123.45")), taxes=[]
            ),
        )
        self.assertEqual(D(d["grand_total"]), D("123.45"))
        self.assertEqual(D(d["rounded_total"]), D("123.00"))
        self.assertEqual(D(d["rounding_adjustment"]), D("-0.45"))
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_intra, self.lines((self.item_plain, 1, "0.50")), taxes=[]
            ),
        )
        self.assertEqual(D(d["rounded_total"]), D("1.00"))
        self.assertEqual(D(d["rounding_adjustment"]), D("0.50"))

    def test_rounding_disabled_means_rounded_equals_grand(self):
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_intra, self.lines((self.item_plain, 1, "123.45")), taxes=[]
            ),
        )
        self.assertEqual(D(d["rounded_total"]), D(d["grand_total"]))
        self.assertEqual(D(d["rounding_adjustment"]), D("0.00"))


class RecomputeTests(GSTDocBase):
    def test_patch_header_only_keeps_rows_and_totals(self):
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 2, 100)))
        )
        res = self.client.patch(
            f"{SO_URL}{d['id']}/", {"delivery_date": "2026-05-01"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["grand_total"], d["grand_total"])
        self.assertEqual(len(res.data["taxes"]), 2)
        self.assertEqual(
            [t["id"] for t in res.data["taxes"]], [t["id"] for t in d["taxes"]]
        )

    def test_patch_items_only_recomputes_and_keeps_tax_rows(self):
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 2, 100)))
        )
        res = self.client.patch(
            f"{SO_URL}{d['id']}/",
            {"items": self.lines((self.item18, 5, 100))},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(D(res.data["net_total"]), D("500.00"))
        self.assertEqual(D(res.data["grand_total"]), D("590.00"))
        self.assertEqual(
            self.components(res.data), {"CGST": D("45.00"), "SGST": D("45.00")}
        )
        self.assertEqual(
            [t["id"] for t in res.data["taxes"]], [t["id"] for t in d["taxes"]]
        )

    def test_put_without_taxes_leaves_rows_alone(self):
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 2, 100)))
        )
        body = self.so_body(self.cust_intra, self.lines((self.item18, 3, 100)))
        res = self.client.put(f"{SO_URL}{d['id']}/", body, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(D(res.data["grand_total"]), D("354.00"))

    def test_explicit_empty_taxes_means_no_tax(self):
        d = self.create(
            SO_URL,
            self.so_body(self.cust_intra, self.lines((self.item18, 2, 100)), taxes=[]),
        )
        self.assertEqual(d["taxes"], [])
        self.assertEqual(D(d["grand_total"]), D("200.00"))
        self.assertIsNone(d["taxes_and_charges"])

    def test_patch_empty_taxes_clears_rows(self):
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 2, 100)))
        )
        res = self.client.patch(f"{SO_URL}{d['id']}/", {"taxes": []}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["taxes"], [])
        self.assertEqual(D(res.data["grand_total"]), D("200.00"))

    def test_chosen_template_is_expanded_when_taxes_omitted(self):
        tpl = self.template("Output GST In-state")
        d = self.create(
            SO_URL,
            self.so_body(
                self.cust_intra,
                self.lines((self.item18, 1, 100)),
                taxes_and_charges=tpl.id,
            ),
        )
        self.assertEqual(sorted(self.components(d)), ["CGST", "SGST"])

    def test_wrong_template_is_rejected(self):
        purchase = self.template("Input GST In-state")
        res = self.client.post(
            SO_URL,
            self.so_body(
                self.cust_intra,
                self.lines((self.item18, 1, 100)),
                taxes_and_charges=purchase.id,
            ),
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("taxes_and_charges", res.data)

    def test_stale_rows_after_place_change_are_rederived(self):
        """ERPNext re-applies the taxes when the supply changes: so do we."""
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        res = self.client.patch(
            f"{SO_URL}{d['id']}/", {"place_of_supply": "07"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["place_of_supply"], "07")
        self.assertEqual(self.components(res.data), {"IGST": D("18.00")})
        self.assertEqual(res.data["taxes_and_charges_name"], "Output GST Out-state")
        self.assertEqual(D(res.data["grand_total"]), D("118.00"))
        got = self.client.get(f"{SO_URL}{d['id']}/").data
        self.assertEqual(self.components(got), {"IGST": D("18.00")})

    def test_changing_party_rederives_rows(self):
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        res = self.client.patch(
            f"{SO_URL}{d['id']}/", {"customer": self.cust_inter.id}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.components(res.data), {"IGST": D("18.00")})

    def test_changing_to_a_zero_rated_party_clears_the_rows(self):
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        res = self.client.patch(
            f"{SO_URL}{d['id']}/", {"customer": self.cust_sez.id}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["taxes"], [])
        self.assertIsNone(res.data["taxes_and_charges"])
        self.assertEqual(D(res.data["grand_total"]), D("100.00"))

    def test_unrelated_update_keeps_stored_rows(self):
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        res = self.client.patch(
            f"{SO_URL}{d['id']}/", {"delivery_date": "2026-05-01"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(sorted(self.components(res.data)), ["CGST", "SGST"])

    def test_non_taxes_structural_error_still_400(self):
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        res = self.client.patch(
            f"{SO_URL}{d['id']}/", {"is_reverse_charge": True}, format="json"
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("is_reverse_charge", res.data)

    def test_reapplying_the_template_fixes_it(self):
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        out_state = self.template("Output GST Out-state")
        res = self.client.patch(
            f"{SO_URL}{d['id']}/",
            {"customer": self.cust_inter.id, "taxes_and_charges": out_state.id},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(
            res.data["place_of_supply"], "07"
        )  # re-defaulted for the new party
        self.assertEqual(self.components(res.data), {"IGST": D("18.00")})

    def test_submitted_documents_are_still_immutable(self):
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        self.assertEqual(
            self.client.post(f"{SO_URL}{d['id']}/submit/").status_code, 200
        )
        res = self.client.patch(f"{SO_URL}{d['id']}/", {"taxes": []}, format="json")
        self.assertEqual(res.status_code, 409)

    def test_submit_amend_keeps_totals(self):
        d = self.create(
            SO_URL,
            self.so_body(
                self.cust_inter, self.lines((self.item18, 3, 100), (self.item5, 1, 50))
            ),
        )
        sub = self.client.post(f"{SO_URL}{d['id']}/submit/")
        self.assertEqual(sub.status_code, 200, sub.content)
        self.assertEqual(sub.data["grand_total"], d["grand_total"])
        self.assertEqual(
            self.client.post(f"{SO_URL}{d['id']}/cancel/").status_code, 200
        )
        am = self.client.post(f"{SO_URL}{d['id']}/amend/")
        self.assertEqual(am.status_code, 201, am.content)
        for key in (
            "net_total",
            "total_taxes_and_charges",
            "grand_total",
            "rounded_total",
            "place_of_supply",
        ):
            self.assertEqual(am.data[key], d[key], key)
        self.assertEqual(
            [(t["account"], t["tax_amount"], t["total"]) for t in am.data["taxes"]],
            [(t["account"], t["tax_amount"], t["total"]) for t in d["taxes"]],
        )
        self.assertEqual(
            [ln["igst_amount"] for ln in am.data["items"]],
            [ln["igst_amount"] for ln in d["items"]],
        )

    def test_orm_created_document_is_calculated_and_gated_at_submit(self):
        so = SalesOrder.objects.create(
            name="SO-ORM-1",
            customer=self.cust_inter,
            company=self.company,
            transaction_date="2026-04-01",
        )
        SalesOrderItem.objects.create(
            sales_order=so, item=self.item18, qty=D("2"), rate=D("100")
        )
        self.assertEqual(so.gst_category, "")  # never calculated
        so.submit()
        so.refresh_from_db()
        self.assertEqual(so.net_total, D("200.00"))
        self.assertEqual(so.grand_total, D("200.00"))  # no template chosen, no rows
        self.assertEqual(so.gst_category, "Registered Regular")
        line = so.items.get()
        self.assertEqual((line.gst_hsn_code, line.gst_treatment), ("100610", "Taxable"))


class PurchaseTests(GSTDocBase):
    def test_intra_state_purchase_uses_input_accounts(self):
        d = self.create(
            PI_URL, self.pi_body(self.sup_intra, self.lines((self.item18, 2, 100)))
        )
        self.assertEqual(self.components(d), {"CGST": D("18.00"), "SGST": D("18.00")})
        self.assertEqual({t["gst_direction"] for t in d["taxes"]}, {"Input"})
        self.assertEqual(d["place_of_supply"], "27")
        self.assertEqual(D(d["grand_total"]), D("236.00"))

    def test_inter_state_purchase(self):
        d = self.create(
            PO_URL, self.po_body(self.sup_inter, self.lines((self.item18, 2, 100)))
        )
        self.assertEqual(self.components(d), {"IGST": D("36.00")})
        self.assertEqual(d["place_of_supply"], "27")  # purchases: company state

    def test_unregistered_supplier_has_no_gst_without_reverse_charge(self):
        d = self.create(
            PI_URL, self.pi_body(self.sup_unreg, self.lines((self.item18, 1, 100)))
        )
        self.assertEqual(d["taxes"], [])
        res = self.client.post(
            PI_URL,
            self.pi_body(
                self.sup_unreg,
                self.lines((self.item18, 1, 100)),
                taxes=[{"account": self.tag[("Input", "CGST")].id, "rate": "9"}],
            ),
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)

    def test_reverse_charge_rows_are_memo_and_leave_grand_total_alone(self):
        d = self.create(
            PI_URL,
            self.pi_body(
                self.sup_unreg,
                self.lines((self.item18, 2, 100)),
                is_reverse_charge=True,
            ),
        )
        self.assertEqual(sorted(self.components(d)), ["CGST", "SGST"])
        self.assertEqual(D(d["taxes"][0]["tax_amount"]), D("18.00"))
        self.assertEqual(D(d["grand_total"]), D("200.00"))
        self.assertEqual(D(d["net_total"]), D("200.00"))
        self.assertEqual(D(d["total_taxes_and_charges"]), D("0.00"))
        self.assertEqual([t["total"] for t in d["taxes"]], ["200.00", "200.00"])
        self.assertEqual(d["taxes_and_charges_name"], "Input GST In-state RCM")

    def test_purchase_rejects_export_flag(self):
        res = self.client.post(
            PI_URL,
            self.pi_body(
                self.sup_intra,
                self.lines((self.item18, 1, 100)),
                is_export_with_gst=True,
            ),
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)

    def test_composition_supplier_carries_no_gst(self):
        comp = self.supplier(
            "Comp Sup", "27", "AABCC3333C", category="Registered Composition"
        )
        d = self.create(PI_URL, self.pi_body(comp, self.lines((self.item18, 1, 100))))
        self.assertEqual(d["taxes"], [])


class RowValidationTests(GSTDocBase):
    def post_rows(self, rows, customer=None, item=None):
        return self.client.post(
            SO_URL,
            self.so_body(
                customer or self.cust_intra,
                self.lines((item or self.item18, 1, 100)),
                taxes=rows,
            ),
            format="json",
        )

    def test_wrong_direction_accounts(self):
        res = self.post_rows([{"account": self.tag[("Input", "CGST")].id, "rate": "9"}])
        self.assertEqual(res.status_code, 400, res.content)
        res = self.client.post(
            PO_URL,
            self.po_body(
                self.sup_intra,
                self.lines((self.item18, 1, 100)),
                taxes=[{"account": self.tag[("Output", "CGST")].id, "rate": "9"}],
            ),
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)

    def test_reverse_charge_tagged_account_is_not_a_document_row(self):
        res = self.post_rows(
            [{"account": self.tag[("Reverse Charge", "CGST")].id, "rate": "9"}]
        )
        self.assertEqual(res.status_code, 400, res.content)

    def test_cross_company_account(self):
        other = Company.objects.create(name="Other Co", abbr="OTH")
        roots = make_chart(other)
        foreign = Account.objects.create(
            name="Foreign Tax",
            company=other,
            parent=roots[AccountRootType.LIABILITY],
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.TAX,
        )
        res = self.post_rows([{"account": foreign.id, "rate": "5"}])
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("company", str(res.data["taxes"]).lower())

    def test_non_tax_and_group_accounts(self):
        res = self.post_rows([{"account": self.income.id, "rate": "5"}])
        self.assertEqual(res.status_code, 400, res.content)
        res = self.post_rows([{"account": self.income.parent_id, "rate": "5"}])
        self.assertEqual(res.status_code, 400, res.content)

    def test_gst_row_cannot_be_a_deduction(self):
        res = self.post_rows(
            [
                {
                    "account": self.tag[("Output", "CGST")].id,
                    "rate": "9",
                    "add_deduct_tax": "Deduct",
                }
            ]
        )
        self.assertEqual(res.status_code, 400, res.content)

    def test_igst_cannot_be_mixed_with_cgst(self):
        res = self.post_rows(
            [
                {"account": self.tag[("Output", "CGST")].id, "rate": "9"},
                {"account": self.tag[("Output", "IGST")].id, "rate": "9"},
            ]
        )
        self.assertEqual(res.status_code, 400, res.content)

    def test_duplicate_account_and_bad_rate(self):
        acc = self.tag[("Output", "CGST")].id
        res = self.post_rows(
            [{"account": acc, "rate": "9"}, {"account": acc, "rate": "9"}]
        )
        self.assertEqual(res.status_code, 400, res.content)
        res = self.post_rows([{"account": acc, "rate": "101"}])
        self.assertEqual(res.status_code, 400, res.content)

    def test_igst_on_intra_state_supply_is_rejected(self):
        res = self.post_rows(
            [{"account": self.tag[("Output", "IGST")].id, "rate": "18"}]
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("not applicable", str(res.data["taxes"]))

    def test_manual_rows_are_computed_server_side(self):
        res = self.post_rows(
            [
                {
                    "account": self.tag[("Output", "CGST")].id,
                    "rate": "6",
                    "tax_amount": "999",
                },
                {"account": self.tag[("Output", "SGST")].id, "rate": "6"},
            ],
            item=self.item_plain,
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(
            res.data["taxes"][0]["tax_amount"], "6.00"
        )  # client value ignored
        self.assertEqual(res.data["taxes"][0]["description"], "Output CGST")
        self.assertEqual(D(res.data["grand_total"]), D("112.00"))

    def test_non_gst_tax_row_with_non_gst_company(self):
        plain = Company.objects.create(name="Plain Co", abbr="PLN")
        roots = make_chart(plain)
        cess = Account.objects.create(
            name="Local Tax",
            company=plain,
            parent=roots[AccountRootType.LIABILITY],
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.TAX,
        )
        cust = Customer.objects.create(
            name="P1", company=plain, customer_group=self.cgroup
        )
        res = self.client.post(
            SO_URL,
            {
                "customer": cust.id,
                "company": plain.id,
                "transaction_date": "2026-04-01",
                "items": self.lines((self.item18, 1, 100)),
                "taxes": [{"account": cess.id, "rate": "2"}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(D(res.data["grand_total"]), D("102.00"))

    def test_gst_rows_rejected_for_company_without_gstin(self):
        plain = Company.objects.create(name="Plain Co", abbr="PLN")
        roots = make_chart(plain)
        seeding.seed_company_gst(plain)
        tag = Account.objects.get(
            company=plain, gst_direction="Output", gst_component="CGST"
        )
        cust = Customer.objects.create(
            name="P1", company=plain, customer_group=self.cgroup
        )
        res = self.client.post(
            SO_URL,
            {
                "customer": cust.id,
                "company": plain.id,
                "transaction_date": "2026-04-01",
                "items": self.lines((self.item18, 1, 100)),
                "taxes": [{"account": tag.id, "rate": "9"}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)
        del roots


class SubmitGateTests(GSTDocBase):
    def submit_so(self, customer, lines, expect, **extra):
        d = self.create(SO_URL, self.so_body(customer, lines, **extra))
        res = self.client.post(f"{SO_URL}{d['id']}/submit/")
        self.assertEqual(res.status_code, expect, res.content)
        return res

    def test_happy_path(self):
        self.submit_so(self.cust_intra, self.lines((self.item18, 1, 100)), 200)

    def test_missing_hsn(self):
        res = self.submit_so(
            self.cust_intra, self.lines((self.item_nohsn, 1, 100)), 400
        )
        self.assertIn("HSN", str(res.data))

    def test_non_gst_line_needs_no_hsn(self):
        self.submit_so(self.cust_intra, self.lines((self.item_nongst, 1, 100)), 200)

    def test_short_or_non_numeric_hsn(self):
        Item.objects.filter(pk=self.item18.pk).update(gst_hsn_code="1006")
        res = self.submit_so(self.cust_intra, self.lines((self.item18, 1, 100)), 400)
        self.assertIn("at least 6", str(res.data))
        Item.objects.filter(pk=self.item18.pk).update(gst_hsn_code="AB1006")
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        res = self.client.post(f"{SO_URL}{d['id']}/submit/")
        self.assertEqual(res.status_code, 400)
        self.assertIn("digits", str(res.data))

    def test_hsn_min_digits_follows_company_setting(self):
        Item.objects.filter(pk=self.item18.pk).update(gst_hsn_code="1006")
        Company.objects.filter(pk=self.company.pk).update(hsn_min_digits=4)
        self.submit_so(self.cust_intra, self.lines((self.item18, 1, 100)), 200)

    def test_invalid_party_gstin_checksum(self):
        Customer.objects.filter(pk=self.cust_intra.pk).update(gstin="27AABCI1111A1Z5")
        res = self.submit_so(self.cust_intra, self.lines((self.item18, 1, 100)), 400)
        self.assertIn("GSTIN", str(res.data))

    def test_party_gstin_prefix_must_match_party_state(self):
        Customer.objects.filter(pk=self.cust_intra.pk).update(state="29")
        res = self.submit_so(
            self.cust_intra, self.lines((self.item18, 1, 100)), 400, taxes=[]
        )
        self.assertIn("prefix", str(res.data))

    def test_orm_imported_party_with_valid_gstin_is_treated_as_registered(self):
        legacy = Customer.objects.create(
            name="Imported",
            company=self.company,
            customer_group=self.cgroup,
            gstin=make_gstin("07", "AAGCS1010K"),
            gst_category="Unregistered",
            state=None,
        )
        d = self.create(SO_URL, self.so_body(legacy, self.lines((self.item18, 1, 100))))
        self.assertEqual(d["gst_category"], "Registered Regular")
        self.assertEqual(d["party_gstin"], legacy.gstin)
        self.assertEqual(d["place_of_supply"], "07")  # from the GSTIN prefix
        self.assertEqual(sorted(self.components(d)), ["IGST"])
        self.assertEqual(
            self.client.post(f"{SO_URL}{d['id']}/submit/").status_code, 200
        )

    def test_orm_imported_party_with_garbage_gstin_is_unregistered(self):
        legacy = Customer.objects.create(
            name="Garbage",
            company=self.company,
            customer_group=self.cgroup,
            gstin="27AAACR0000A1Z5",
            gst_category="Unregistered",
            state=None,
        )
        d = self.create(SO_URL, self.so_body(legacy, self.lines((self.item18, 1, 100))))
        self.assertEqual(d["gst_category"], "Unregistered")
        self.assertIsNone(d["party_gstin"])
        self.assertEqual(d["place_of_supply"], "27")
        self.assertEqual(
            self.client.post(f"{SO_URL}{d['id']}/submit/").status_code, 200
        )

    def test_registered_party_without_gstin_fails(self):
        Customer.objects.filter(pk=self.cust_intra.pk).update(gstin=None)
        self.submit_so(self.cust_intra, self.lines((self.item18, 1, 100)), 400)

    def test_place_of_supply_required(self):
        Company.objects.filter(pk=self.company.pk).update(state=None)
        res = self.submit_so(self.cust_b2c, self.lines((self.item18, 1, 100)), 400)
        self.assertIn("Place of supply", str(res.data))

    def test_invoice_number_longer_than_16_characters(self):
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_intra,
                self.lines((self.item18, 1, 100)),
                name="SINV-2026-0001-10",
                taxes=[],
            ),
        )
        res = self.client.post(f"{SI_URL}{d['id']}/submit/")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("16", str(res.data))
        # a 16-character number is fine
        d = self.create(
            SI_URL,
            self.si_body(
                self.cust_intra,
                self.lines((self.item18, 1, 100)),
                name="SINV-2026-00001",
                taxes=[],
            ),
        )
        self.assertEqual(
            self.client.post(f"{SI_URL}{d['id']}/submit/").status_code, 200
        )

    def test_company_without_gstin_is_not_gated(self):
        Company.objects.filter(pk=self.company.pk).update(gstin=None)
        Item.objects.filter(pk=self.item_nohsn.pk).update(gst_hsn_code="")
        self.submit_so(
            self.cust_b2c, self.lines((self.item_nohsn, 1, 100)), 200, taxes=[]
        )


class PostingGuardTests(GSTDocBase):
    """The Task 2 posting guard is gone: taxed invoices submit and post (Task 3)."""

    def test_taxed_invoices_submit(self):
        d = self.create(
            SI_URL, self.si_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        res = self.client.post(f"{SI_URL}{d['id']}/submit/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["docstatus"], DocStatus.SUBMITTED)
        d = self.create(
            PI_URL, self.pi_body(self.sup_intra, self.lines((self.item18, 1, 100)))
        )
        res = self.client.post(f"{PI_URL}{d['id']}/submit/")
        self.assertEqual(res.status_code, 200, res.content)

    def test_guard_helper_is_removed(self):
        self.assertFalse(hasattr(SalesInvoice, "_assert_gst_posting_enabled"))
        self.assertFalse(hasattr(PurchaseInvoice, "_assert_gst_posting_enabled"))

    def test_untaxed_invoice_submits_and_posts_exactly_as_before(self):
        d = self.create(
            SI_URL,
            self.si_body(self.cust_intra, self.lines((self.item18, 2, 100)), taxes=[]),
        )
        res = self.client.post(f"{SI_URL}{d['id']}/submit/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(D(res.data["grand_total"]), D("200.00"))
        from accounts.models import GLEntry

        self.assertEqual(
            sorted(
                (e.account.name, e.debit, e.credit)
                for e in GLEntry.objects.filter(voucher_no=d["name"])
            ),
            [("Debtors", D("200.00"), D("0.00")), ("Sales", D("0.00"), D("200.00"))],
        )

    def test_untaxed_purchase_invoice_submits(self):
        d = self.create(
            PI_URL,
            self.pi_body(self.sup_intra, self.lines((self.item18, 2, 100)), taxes=[]),
        )
        self.assertEqual(
            self.client.post(f"{PI_URL}{d['id']}/submit/").status_code, 200
        )


class LegacyBehaviourTests(APITestCase):
    """A company with no GST setup behaves exactly as before the tax engine."""

    def test_no_taxes_means_net_equals_grand_and_no_rounding(self):
        company = Company.objects.create(
            name="Old Co", abbr="OLD", enable_rounded_total=False
        )
        roots = make_chart(company)
        debtors = Account.objects.create(
            name="Debtors",
            company=company,
            parent=roots[AccountRootType.ASSET],
            root_type=AccountRootType.ASSET,
            account_type=AccountType.RECEIVABLE,
        )
        income = Account.objects.create(
            name="Sales",
            company=company,
            parent=roots[AccountRootType.INCOME],
            root_type=AccountRootType.INCOME,
        )
        grp = CustomerGroup.objects.create(name="G")
        cust = Customer.objects.create(
            name="C", company=company, customer_group=grp, gstin="27AAACR0000A1Z5"
        )
        item = Item.objects.create(
            code="X",
            name="X",
            item_group=ItemGroup.objects.create(name="IG"),
            stock_uom=UOM.objects.create(name="Nos"),
            is_stock_item=False,
        )
        si = SalesInvoice.objects.create(
            name="SINV-OLD-1",
            customer=cust,
            company=company,
            posting_date="2026-04-01",
            update_stock=False,
            debit_to=debtors,
            income_account=income,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=si, item=item, qty=D("3"), rate=D("33.33")
        )
        si.submit()
        si.refresh_from_db()
        self.assertEqual(si.grand_total, D("99.99"))
        self.assertEqual(si.net_total, D("99.99"))
        self.assertEqual(si.rounded_total, D("99.99"))
        self.assertEqual(
            (si.total_taxes_and_charges, si.rounding_adjustment), (D("0.00"), D("0.00"))
        )
        self.assertEqual(si.taxes.count(), 0)


class DBConstraintTests(GSTDocBase):
    def so(self):
        d = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 1, 100)))
        )
        return SalesOrder.objects.get(pk=d["id"])

    def assert_integrity_error(self, fn):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                fn()

    def test_header_identities(self):
        so = self.so()
        self.assert_integrity_error(
            lambda: SalesOrder.objects.filter(pk=so.pk).update(grand_total=D("1.00"))
        )
        self.assert_integrity_error(
            lambda: SalesOrder.objects.filter(pk=so.pk).update(rounded_total=D("1.00"))
        )
        self.assert_integrity_error(
            lambda: SalesOrder.objects.filter(pk=so.pk).update(
                is_reverse_charge=True, is_export_with_gst=True
            )
        )
        # consistent change is fine
        SalesOrder.objects.filter(pk=so.pk).update(
            net_total=D("10.10"),
            total_taxes_and_charges=D("1.82"),
            grand_total=D("11.92"),
            rounding_adjustment=D("0.08"),
            rounded_total=D("12.00"),
        )

    def test_line_constraints(self):
        so = self.so()
        line = so.items.get()
        model = type(line)
        self.assert_integrity_error(
            lambda: model.objects.filter(pk=line.pk).update(gst_treatment="Exempted")
        )  # Exempted line still carries GST amounts
        self.assert_integrity_error(
            lambda: model.objects.filter(pk=line.pk).update(igst_amount=D("5"))
        )  # IGST together with CGST/SGST
        self.assert_integrity_error(
            lambda: model.objects.filter(pk=line.pk).update(gst_rate=D("101"))
        )
        self.assert_integrity_error(
            lambda: model.objects.filter(pk=line.pk).update(cgst_amount=D("-1"))
        )
        model.objects.filter(pk=line.pk).update(
            gst_treatment="Exempted", cgst_amount=0, sgst_amount=0
        )

    def test_tax_row_constraints(self):
        so = self.so()
        row = so.taxes.first()
        model = type(row)
        acc = self.tag[("Output", "CESS")] if ("Output", "CESS") in self.tag else None
        self.assert_integrity_error(
            lambda: model.objects.filter(pk=row.pk).update(rate=D("101"))
        )
        self.assert_integrity_error(
            lambda: model.objects.filter(pk=row.pk).update(charge_type="Actual")
        )
        self.assert_integrity_error(
            lambda: model.objects.filter(pk=row.pk).update(
                category="Valuation and Total"
            )
        )
        self.assert_integrity_error(
            lambda: model.objects.filter(pk=row.pk).update(add_deduct_tax="X")
        )
        self.assert_integrity_error(
            lambda: model.objects.filter(pk=row.pk).update(tax_amount=D("-1"))
        )
        self.assert_integrity_error(
            lambda: model.objects.filter(pk=row.pk).update(idx=2)
        )  # clashes with row 2
        other = so.taxes.exclude(pk=row.pk).first()
        self.assert_integrity_error(
            lambda: model.objects.filter(pk=other.pk).update(account=row.account)
        )
        del acc

    def test_purchase_and_invoice_tables_carry_the_same_constraints(self):
        for model in (SalesInvoiceTax, PurchaseOrderTax):
            names = {c.name for c in model._meta.constraints}
            self.assertEqual(len(names), 7, names)
        for model in (SalesInvoiceItem, PurchaseInvoiceItem, PurchaseOrderItem):
            self.assertEqual(len(model._meta.constraints), 4)
        for model in (SalesOrder, SalesInvoice, PurchaseOrder, PurchaseInvoice):
            self.assertEqual(len(model._meta.constraints), 3)


class DryRunTests(GSTDocBase):
    URL = "/api/accounts/taxes/calculate/"

    def body(self, doctype, party, items, **extra):
        body = {
            "doctype": doctype,
            "company": self.company.id,
            "party": party.id,
            "items": [
                {"item": i.id, "qty": str(q), "rate": str(r)} for i, q, r in items
            ],
        }
        body.update(extra)
        return body

    def test_matches_a_subsequent_create_exactly(self):
        cases = [
            ("selling.SalesInvoice", SI_URL, self.cust_intra, self.si_body),
            ("selling.SalesInvoice", SI_URL, self.cust_inter, self.si_body),
            ("selling.SalesOrder", SO_URL, self.cust_b2c, self.so_body),
            ("buying.PurchaseInvoice", PI_URL, self.sup_intra, self.pi_body),
            ("buying.PurchaseOrder", PO_URL, self.sup_inter, self.po_body),
        ]
        specs = [
            (self.item18, 3, "33.33"),
            (self.item5, 1, "10.10"),
            (self.item_nil, 2, "7.77"),
        ]
        for doctype, url, party, body_fn in cases:
            dry = self.client.post(
                self.URL, self.body(doctype, party, specs), format="json"
            )
            self.assertEqual(dry.status_code, 200, dry.content)
            real = self.create(url, body_fn(party, self.lines(*specs)))
            for key in (
                "net_total",
                "total_taxes_and_charges",
                "grand_total",
                "rounding_adjustment",
                "rounded_total",
            ):
                self.assertEqual(dry.data[key], real[key], (doctype, key))
            self.assertEqual(
                dry.data["supply"]["place_of_supply"], real["place_of_supply"]
            )
            self.assertEqual(
                [
                    (
                        t["account"],
                        t["rate"],
                        t["net_amount"],
                        t["tax_amount"],
                        t["total"],
                    )
                    for t in dry.data["taxes"]
                ],
                [
                    (
                        t["account"],
                        t["rate"],
                        t["net_amount"],
                        t["tax_amount"],
                        t["total"],
                    )
                    for t in real["taxes"]
                ],
            )
            for a, b in zip(dry.data["items"], real["items"]):
                for key in (
                    "amount",
                    "gst_treatment",
                    "gst_hsn_code",
                    "gst_rate",
                    "cgst_amount",
                    "sgst_amount",
                    "igst_amount",
                ):
                    self.assertEqual(a[key], b[key], (doctype, key))

    def test_response_shape_and_supply_block(self):
        dry = self.client.post(
            self.URL,
            self.body("selling.SalesInvoice", self.cust_inter, [(self.item18, 2, 100)]),
            format="json",
        ).data
        self.assertEqual(dry["supply"]["kind"], "INTER")
        self.assertEqual(dry["supply"]["allowed_components"], ["IGST", "CESS"])
        self.assertEqual(dry["supply"]["gst_category"], "Registered Regular")
        self.assertEqual(dry["supply"]["party_gstin"], self.cust_inter.gstin)
        self.assertEqual(dry["taxes"][0]["gst_component"], "IGST")
        self.assertEqual(dry["items"][0]["igst_amount"], "36.00")
        self.assertEqual(dry["grand_total"], "236.00")
        self.assertEqual(
            dry["taxes_and_charges"], self.template("Output GST Out-state").id
        )

    def test_explicit_rows_and_flags(self):
        dry = self.client.post(
            self.URL,
            self.body(
                "selling.SalesInvoice",
                self.cust_sez,
                [(self.item18, 1, 100)],
                is_export_with_gst=True,
                taxes=[{"account": self.tag[("Output", "IGST")].id, "rate": "18"}],
            ),
            format="json",
        )
        self.assertEqual(dry.status_code, 200, dry.content)
        self.assertEqual(dry.data["grand_total"], "118.00")
        dry = self.client.post(
            self.URL,
            self.body(
                "selling.SalesInvoice",
                self.cust_intra,
                [(self.item18, 1, 100)],
                taxes=[],
            ),
            format="json",
        )
        self.assertEqual(dry.data["taxes"], [])

    def test_writes_nothing(self):
        before = (SalesInvoice.objects.count(), SalesInvoiceTax.objects.count())
        self.client.post(
            self.URL,
            self.body("selling.SalesInvoice", self.cust_intra, [(self.item18, 1, 100)]),
            format="json",
        )
        self.assertEqual(
            before, (SalesInvoice.objects.count(), SalesInvoiceTax.objects.count())
        )

    def test_input_errors_are_400(self):
        wrong_dir = self.body(
            "selling.SalesInvoice",
            self.cust_intra,
            [(self.item18, 1, 100)],
            taxes=[{"account": self.tag[("Input", "CGST")].id, "rate": "9"}],
        )
        res = self.client.post(self.URL, wrong_dir, format="json")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("taxes", res.data)
        res = self.client.post(
            self.URL, {**wrong_dir, "doctype": "selling.Customer"}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        res = self.client.post(
            self.URL,
            self.body("selling.SalesInvoice", self.cust_intra, []),
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        res = self.client.post(
            self.URL,
            self.body("selling.SalesInvoice", self.sup_intra, [(self.item18, 1, 1)]),
            format="json",
        )  # a supplier id that is not this company's customer id space
        self.assertIn(res.status_code, (200, 400))
        other = Company.objects.create(name="Other", abbr="OTH")
        stray = Customer.objects.create(
            name="Stray", company=other, customer_group=self.cgroup
        )
        res = self.client.post(
            self.URL,
            self.body("selling.SalesInvoice", stray, [(self.item18, 1, 1)]),
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)

    def test_needs_can_create_on_the_target_document(self):
        user = User.objects.create_user(
            username="reader", email="r@test.com", password="x"
        )
        role = Role.objects.create(name="Reader")
        RolePermission.objects.create(
            role=role, model_label="selling.SalesInvoice", can_read=True
        )
        RolePermission.objects.create(
            role=role, model_label="selling.SalesOrder", can_create=True
        )
        UserRole.objects.create(user=user, role=role)
        client = APIClient()
        client.force_authenticate(user)
        res = client.post(
            self.URL,
            self.body("selling.SalesInvoice", self.cust_intra, [(self.item18, 1, 100)]),
            format="json",
        )
        self.assertEqual(res.status_code, 403)
        res = client.post(
            self.URL,
            self.body("selling.SalesOrder", self.cust_intra, [(self.item18, 1, 100)]),
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(APIClient().post(self.URL, {}, format="json").status_code, 401)


class SupplyContextEndpointTests(GSTDocBase):
    URL = "/api/gst/supply-context/"

    def get(self, **params):
        return self.client.get(self.URL, params)

    def test_sales_inter(self):
        res = self.get(
            company=self.company.id, party=self.cust_inter.id, direction="Sales"
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["kind"], "INTER")
        self.assertEqual(res.data["place_of_supply"], "07")
        self.assertEqual(
            res.data["suggested_template"]["title"], "Output GST Out-state"
        )
        self.assertEqual(res.data["allowed_components"], ["IGST", "CESS"])

    def test_place_override_and_flags(self):
        res = self.get(
            company=self.company.id,
            party=self.cust_intra.id,
            direction="Sales",
            place_of_supply="29",
        )
        self.assertEqual(res.data["kind"], "INTER")
        res = self.get(
            company=self.company.id, party=self.cust_sez.id, direction="Sales"
        )
        self.assertEqual(res.data["allowed_components"], [])
        self.assertIsNone(res.data["suggested_template"])
        self.assertTrue(res.data["reason"])
        res = self.get(
            company=self.company.id,
            party=self.cust_sez.id,
            direction="Sales",
            is_export_with_gst="1",
        )
        self.assertEqual(
            res.data["suggested_template"]["title"], "Output GST Out-state"
        )

    def test_purchase_reverse_charge(self):
        res = self.get(
            company=self.company.id,
            party=self.sup_unreg.id,
            direction="Purchase",
            is_reverse_charge="true",
        )
        self.assertEqual(res.data["kind"], "INTRA")
        self.assertEqual(
            res.data["suggested_template"]["title"], "Input GST In-state RCM"
        )
        res = self.get(
            company=self.company.id, party=self.sup_unreg.id, direction="Purchase"
        )
        self.assertIsNone(res.data["suggested_template"])

    def test_validation(self):
        self.assertEqual(
            self.get(
                company=self.company.id, party=self.cust_intra.id, direction="X"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.get(company=self.company.id, party=0, direction="Sales").status_code,
            400,
        )
        self.assertEqual(
            self.get(
                company=self.company.id,
                party=self.cust_intra.id,
                direction="Sales",
                place_of_supply="ZZ",
            ).status_code,
            400,
        )
        self.assertEqual(APIClient().get(self.URL).status_code, 401)

    def test_needs_read_on_party_master(self):
        user = User.objects.create_user(
            username="nobody", email="n@test.com", password="x"
        )
        client = APIClient()
        client.force_authenticate(user)
        res = client.get(
            self.URL,
            {
                "company": self.company.id,
                "party": self.cust_intra.id,
                "direction": "Sales",
            },
        )
        self.assertEqual(res.status_code, 403)


class MappingTests(GSTDocBase):
    def test_sales_order_to_invoice_satisfies_the_header_identity(self):
        so = self.create(
            SO_URL, self.so_body(self.cust_intra, self.lines((self.item18, 4, 100)))
        )
        self.assertEqual(
            self.client.post(f"{SO_URL}{so['id']}/submit/").status_code, 200
        )
        res = self.client.post(f"{SO_URL}{so['id']}/make-sales-invoice/")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(D(res.data["net_total"]), D("400.00"))
        self.assertEqual(
            D(res.data["grand_total"]),
            D(res.data["net_total"]) + D(res.data["total_taxes_and_charges"]),
        )

    def test_purchase_order_to_invoice(self):
        po = self.create(
            PO_URL, self.po_body(self.sup_intra, self.lines((self.item18, 4, 100)))
        )
        self.assertEqual(
            self.client.post(f"{PO_URL}{po['id']}/submit/").status_code, 200
        )
        res = self.client.post(f"{PO_URL}{po['id']}/make-purchase-invoice/")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(D(res.data["net_total"]), D("400.00"))


class AmendInvoiceTests(GSTDocBase):
    def test_untaxed_purchase_invoice_amend(self):
        d = self.create(
            PI_URL,
            self.pi_body(self.sup_intra, self.lines((self.item18, 2, 100)), taxes=[]),
        )
        self.assertEqual(
            self.client.post(f"{PI_URL}{d['id']}/submit/").status_code, 200
        )
        self.assertEqual(
            self.client.post(f"{PI_URL}{d['id']}/cancel/").status_code, 200
        )
        am = self.client.post(f"{PI_URL}{d['id']}/amend/")
        self.assertEqual(am.status_code, 201, am.content)
        self.assertEqual(am.data["grand_total"], d["grand_total"])
        self.assertEqual(am.data["taxes"], [])
        self.assertTrue(
            PurchaseInvoice.objects.filter(pk=am.data["id"], docstatus=0).exists()
        )
