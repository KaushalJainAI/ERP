"""API / model tests for the GST masters (Task 1)."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient, APITestCase

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    ItemTaxTemplate,
    TaxTemplate,
    TaxTemplateRow,
)
from buying.models import Supplier, SupplierGroup
from core.models import Company, State
from gst.tests.test_validators import make_gstin
from selling.models import Customer, CustomerGroup
from stock.models import UOM, Item, ItemGroup
from users.models import User


class GSTBase(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username="gst_admin", email="gst_admin@test.com", password="x"
        )
        self.client.force_authenticate(self.admin)
        self.company = Company.objects.create(name="GST Co", abbr="GSTC")
        self.other_company = Company.objects.create(name="Other Co", abbr="OTH")
        self.liab = Account.objects.create(
            name="Liabilities",
            company=self.company,
            root_type=AccountRootType.LIABILITY,
            is_group=True,
        )
        self.assets = Account.objects.create(
            name="Assets",
            company=self.company,
            root_type=AccountRootType.ASSET,
            is_group=True,
        )

    def tax_account(self, name, direction=None, component=None, company=None, **kw):
        return Account.objects.create(
            name=name,
            company=company or self.company,
            parent=self.liab,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.TAX,
            gst_direction=direction,
            gst_component=component,
            **kw,
        )


class StateTests(GSTBase):
    def test_forty_states_seeded_by_migration(self):
        self.assertEqual(State.objects.count(), 40)
        self.assertEqual(State.objects.get(pk="27").name, "Maharashtra")
        self.assertFalse(State.objects.get(pk="25").is_active)
        self.assertFalse(State.objects.get(pk="28").is_active)
        self.assertTrue(State.objects.get(pk="07").is_union_territory)

    def test_state_code_length_check(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            State.objects.create(code="123", name="Bogus")

    def test_list_search_and_pk_is_code(self):
        res = self.client.get("/api/core/states/?search=mahar&page_size=100")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["results"][0]["code"], "27")
        res = self.client.get("/api/core/states/27/")
        self.assertEqual(res.data["name"], "Maharashtra")

    def test_states_are_read_only(self):
        self.assertEqual(
            self.client.post(
                "/api/core/states/", {"code": "98", "name": "X"}
            ).status_code,
            405,
        )
        self.assertEqual(
            self.client.patch("/api/core/states/27/", {"name": "X"}).status_code, 405
        )
        self.assertEqual(self.client.delete("/api/core/states/27/").status_code, 405)


class ValidateGstinEndpointTests(GSTBase):
    def test_valid(self):
        res = self.client.get("/api/gst/validate-gstin/?gstin=27aaacr5055k1z7")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            res.data,
            {
                "valid": True,
                "reason": "",
                "state_code": "27",
                "state_name": "Maharashtra",
                "pan": "AAACR5055K",
            },
        )

    def test_invalid_still_returns_state(self):
        res = self.client.get("/api/gst/validate-gstin/?gstin=27AAACR5055K1Z8")
        self.assertFalse(res.data["valid"])
        self.assertEqual(res.data["state_code"], "27")
        self.assertIsNone(res.data["pan"])

    def test_missing_param_is_400_and_anonymous_is_401(self):
        self.assertEqual(self.client.get("/api/gst/validate-gstin/").status_code, 400)
        self.assertEqual(
            APIClient().get("/api/gst/validate-gstin/?gstin=x").status_code, 401
        )


class CompanyGSTTests(GSTBase):
    def test_company_clean_rules(self):
        mh = State.objects.get(pk="27")
        c = Company(name="C1", abbr="C1", gstin=make_gstin(), state=mh)
        # gstin set but category still the Unregistered default
        with self.assertRaises(ValidationError) as cm:
            c.clean()
        self.assertIn("gst_category", cm.exception.message_dict)
        c.gst_category = "Registered Regular"
        c.clean()  # valid
        c.state = State.objects.get(pk="07")
        with self.assertRaises(ValidationError) as cm:
            c.clean()
        self.assertIn("state", cm.exception.message_dict)
        c.state = mh
        c.gstin = "27AAACR5055K1Z8"
        with self.assertRaises(ValidationError) as cm:
            c.clean()
        self.assertIn("gstin", cm.exception.message_dict)

    def test_company_without_gstin_needs_nothing(self):
        Company(name="C2", abbr="C2").clean()

    def test_round_off_account_must_belong_to_company(self):
        acc = Account.objects.create(
            name="Round Off",
            company=self.other_company,
            root_type=AccountRootType.EXPENSE,
            account_type=AccountType.ROUND_OFF,
        )
        self.company.round_off_account = acc
        with self.assertRaises(ValidationError) as cm:
            self.company.clean()
        self.assertIn("round_off_account", cm.exception.message_dict)

    def test_company_api_exposes_gst_fields_read_only(self):
        res = self.client.get(f"/api/core/companies/{self.company.pk}/")
        for key in (
            "state",
            "gst_category",
            "hsn_min_digits",
            "enable_rounded_total",
            "round_off_account",
            "round_off_gst_values",
        ):
            self.assertIn(key, res.data)
        self.assertEqual(res.data["gst_category"], "Unregistered")
        self.assertFalse(res.data["enable_rounded_total"])
        self.assertEqual(
            self.client.patch(
                f"/api/core/companies/{self.company.pk}/", {"gst_category": "SEZ"}
            ).status_code,
            405,
        )


PARTY_CASES = (
    ("customer", "/api/selling/customers/"),
    ("supplier", "/api/buying/suppliers/"),
)


class PartyGSTTests(GSTBase):
    def setUp(self):
        super().setUp()
        self.cg = CustomerGroup.objects.create(name="CG")
        self.sg = SupplierGroup.objects.create(name="SG")

    def payload(self, kind, **extra):
        self._n = getattr(self, "_n", 0) + 1
        data = {"name": f"Party {self._n}", "company": self.company.pk}
        data["customer_group" if kind == "customer" else "supplier_group"] = (
            self.cg.pk if kind == "customer" else self.sg.pk
        )
        data.update(extra)
        return data

    def test_gstin_normalised_and_state_autofilled(self):
        gstin = make_gstin()
        for kind, url in PARTY_CASES:
            res = self.client.post(
                url,
                self.payload(
                    kind, gstin=gstin.lower(), gst_category="Registered Regular"
                ),
                format="json",
            )
            self.assertEqual(res.status_code, 201, res.data)
            self.assertEqual(res.data["gstin"], gstin)
            self.assertEqual(res.data["state"], "27")
            self.assertEqual(res.data["state_name"], "Maharashtra")

    def test_bad_checksum_rejected(self):
        for kind, url in PARTY_CASES:
            res = self.client.post(
                url,
                self.payload(
                    kind, gstin="27AAACR5055K1Z8", gst_category="Registered Regular"
                ),
                format="json",
            )
            self.assertEqual(res.status_code, 400)
            self.assertIn("gstin", res.data)

    def test_category_versus_gstin(self):
        gstin = make_gstin()
        for kind, url in PARTY_CASES:
            # Registered without a GSTIN
            res = self.client.post(
                url,
                self.payload(kind, gst_category="Registered Regular"),
                format="json",
            )
            self.assertEqual(res.status_code, 400)
            self.assertIn("gstin", res.data)
            # Unregistered / Consumer / Overseas with a GSTIN
            for category in ("Unregistered", "Consumer", "Overseas"):
                res = self.client.post(
                    url,
                    self.payload(kind, gstin=gstin, gst_category=category),
                    format="json",
                )
                self.assertEqual(res.status_code, 400, category)
                self.assertIn("gstin", res.data)

    def test_unregistered_and_consumer_without_gstin(self):
        for kind, url in PARTY_CASES:
            for i, category in enumerate(("Unregistered", "Consumer")):
                res = self.client.post(
                    url,
                    self.payload(
                        kind, gst_category=category, state="07", tax_id=str(i)
                    ),
                    format="json",
                )
                self.assertEqual(res.status_code, 201, res.data)
                self.assertEqual(res.data["state"], "07")
                self.assertIsNone(res.data["gstin"])

    def test_state_prefix_mismatch(self):
        res = self.client.post(
            "/api/selling/customers/",
            self.payload(
                "customer",
                gstin=make_gstin(),
                gst_category="Registered Regular",
                state="07",
            ),
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(str(res.data["state"][0]), "GSTIN prefix 27 is Maharashtra.")

    def test_overseas_has_no_state(self):
        res = self.client.post(
            "/api/selling/customers/",
            self.payload("customer", gst_category="Overseas", state="27"),
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNone(res.data["state"])

    def test_patch_gstin_rederives_state_and_category_only_patch_revalidates(self):
        res = self.client.post(
            "/api/selling/customers/",
            self.payload(
                "customer", gstin=make_gstin(), gst_category="Registered Regular"
            ),
            format="json",
        )
        pk = res.data["id"]
        new = make_gstin(state="07", pan="AAGCS4567K")
        res = self.client.patch(
            f"/api/selling/customers/{pk}/", {"gstin": new}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["state"], "07")
        # Switching to Unregistered while a GSTIN is on file is refused.
        res = self.client.patch(
            f"/api/selling/customers/{pk}/",
            {"gst_category": "Unregistered"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        # An unrelated PATCH on a legacy-looking row is not blocked.
        res = self.client.patch(
            f"/api/selling/customers/{pk}/", {"phone": "123"}, format="json"
        )
        self.assertEqual(res.status_code, 200)

    def test_orm_creation_with_invalid_gstin_still_works(self):
        c = Customer.objects.create(
            name="Legacy",
            company=self.company,
            customer_group=self.cg,
            gstin="27AAACR0000A1Z5",  # fails the checksum; ORM writers are not validated
        )
        s = Supplier.objects.create(
            name="Legacy S",
            company=self.company,
            supplier_group=self.sg,
            gstin="X" * 20,
        )
        self.assertEqual(c.gst_category, "Unregistered")
        self.assertIsNone(c.state_id)
        self.assertEqual(s.gstin, "X" * 20)
        # ...and an untouched legacy row can still be PATCHed over the API.
        res = self.client.patch(
            f"/api/selling/customers/{c.pk}/", {"phone": "1"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)

    def test_filters(self):
        self.client.post(
            "/api/selling/customers/",
            self.payload("customer", gst_category="Consumer"),
            format="json",
        )
        res = self.client.get("/api/selling/customers/?gst_category=Consumer")
        self.assertEqual(res.data["count"], 1)
        res = self.client.get("/api/selling/customers/?gst_category=SEZ")
        self.assertEqual(res.data["count"], 0)


class AccountTagTests(GSTBase):
    def test_both_or_neither(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.tax_account("A1", direction="Output")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.tax_account("A2", component="CGST")
        self.tax_account("A3")  # untagged Tax account is fine

    def test_tax_type_only(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.create(
                name="Bank-ish",
                company=self.company,
                parent=self.assets,
                root_type=AccountRootType.ASSET,
                account_type=AccountType.BANK,
                gst_direction="Input",
                gst_component="CGST",
            )

    def test_unique_per_company_but_not_across_companies(self):
        self.tax_account("Output CGST", "Output", "CGST")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.tax_account("Output CGST 2", "Output", "CGST")
        # a different component, direction, or company is fine
        self.tax_account("Output SGST", "Output", "SGST")
        self.tax_account("Input CGST", "Input", "CGST")
        other_liab = Account.objects.create(
            name="Liabilities",
            company=self.other_company,
            root_type=AccountRootType.LIABILITY,
            is_group=True,
        )
        Account.objects.create(
            name="Output CGST",
            company=self.other_company,
            parent=other_liab,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.TAX,
            gst_direction="Output",
            gst_component="CGST",
        )

    def test_api_validation_of_tags(self):
        base = {
            "company": self.company.pk,
            "parent": self.liab.pk,
            "root_type": "Liability",
        }
        res = self.client.post(
            "/api/accounts/accounts/",
            {**base, "name": "T1", "account_type": "Tax", "gst_direction": "Output"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        res = self.client.post(
            "/api/accounts/accounts/",
            {
                **base,
                "name": "T2",
                "account_type": "Payable",
                "gst_direction": "Output",
                "gst_component": "CGST",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        res = self.client.post(
            "/api/accounts/accounts/",
            {
                **base,
                "name": "T3",
                "account_type": "Tax",
                "gst_direction": "Output",
                "gst_component": "CGST",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        dup = self.client.post(
            "/api/accounts/accounts/",
            {
                **base,
                "name": "T4",
                "account_type": "Tax",
                "gst_direction": "Output",
                "gst_component": "CGST",
            },
            format="json",
        )
        self.assertEqual(dup.status_code, 400)
        # PATCH an untagged-ness away from Tax while tagged is refused
        res2 = self.client.patch(
            f"/api/accounts/accounts/{res.data['id']}/",
            {"account_type": "Payable"},
            format="json",
        )
        self.assertEqual(res2.status_code, 400)

    def test_untagged_accounts_still_creatable_over_api(self):
        # Regression: DRF would otherwise make the nullable tag fields required via
        # the conditional unique constraint.
        for name in ("Plain 1", "Plain 2"):
            res = self.client.post(
                "/api/accounts/accounts/",
                {"company": self.company.pk, "name": name, "root_type": "Asset"},
                format="json",
            )
            self.assertEqual(res.status_code, 201, res.data)
            self.assertIsNone(res.data["gst_direction"])
        dup = self.client.post(
            "/api/accounts/accounts/",
            {"company": self.company.pk, "name": "Plain 1", "root_type": "Asset"},
            format="json",
        )
        self.assertEqual(dup.status_code, 400)

    def test_round_off_is_an_account_type(self):
        self.assertIn("Round Off", AccountType.values)


class ItemTaxTemplateAPITests(GSTBase):
    def test_nested_create_update(self):
        res = self.client.post(
            "/api/accounts/item-tax-templates/",
            {
                "name": "GST 7%",
                "gst_treatment": "Taxable",
                "rows": [
                    {"gst_component": "CGST", "tax_rate": "3.500"},
                    {"gst_component": "SGST", "tax_rate": "3.500"},
                    {"gst_component": "IGST", "tax_rate": "7.000"},
                ],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(len(res.data["rows"]), 3)
        pk = res.data["id"]
        # header-only PATCH leaves rows alone
        res = self.client.patch(
            f"/api/accounts/item-tax-templates/{pk}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(len(res.data["rows"]), 3)
        # rows replaced
        res = self.client.patch(
            f"/api/accounts/item-tax-templates/{pk}/",
            {"rows": [{"gst_component": "IGST", "tax_rate": "7.000"}]},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual([r["gst_component"] for r in res.data["rows"]], ["IGST"])
        # an incomplete replacement row is rejected
        res = self.client.patch(
            f"/api/accounts/item-tax-templates/{pk}/",
            {"rows": [{"gst_component": "IGST"}]},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_duplicate_component_and_rate_range(self):
        res = self.client.post(
            "/api/accounts/item-tax-templates/",
            {
                "name": "Dup",
                "rows": [
                    {"gst_component": "CGST", "tax_rate": "1"},
                    {"gst_component": "CGST", "tax_rate": "2"},
                ],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(ItemTaxTemplate.objects.filter(name="Dup").count(), 0)
        # DB CHECK on the rate
        t = ItemTaxTemplate.objects.create(name="Range")
        with self.assertRaises(IntegrityError), transaction.atomic():
            t.rows.create(gst_component="IGST", tax_rate=Decimal("100.001"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            t.rows.create(gst_component="IGST", tax_rate=Decimal("-1"))
        t.rows.create(gst_component="IGST", tax_rate=Decimal("0.125"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            t.rows.create(gst_component="IGST", tax_rate=Decimal("1"))


class TaxTemplateAPITests(GSTBase):
    def setUp(self):
        super().setUp()
        self.out_cgst = self.tax_account("Output CGST", "Output", "CGST")
        self.out_sgst = self.tax_account("Output SGST", "Output", "SGST")
        self.out_igst = self.tax_account("Output IGST", "Output", "IGST")
        self.in_cgst = self.tax_account("Input CGST", "Input", "CGST")
        self.plain_tax = self.tax_account("Misc Tax")

    def body(self, **kw):
        data = {
            "company": self.company.pk,
            "title": "Sales In",
            "template_type": "Sales",
            "gst_supply_type": "Intra-State",
            "is_default": True,
            "rows": [
                {"account": self.out_cgst.pk, "rate": "9.000"},
                {"account": self.out_sgst.pk, "rate": "9.000"},
            ],
        }
        data.update(kw)
        return data

    def test_nested_create_assigns_idx_and_returns_account_tags(self):
        res = self.client.post(
            "/api/accounts/tax-templates/", self.body(), format="json"
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual([r["idx"] for r in res.data["rows"]], [1, 2])
        self.assertEqual(res.data["rows"][0]["gst_component"], "CGST")
        self.assertEqual(res.data["rows"][0]["charge_type"], "On Net Total")
        self.assertEqual(res.data["rows"][0]["category"], "Total")
        self.assertEqual(res.data["rows"][0]["add_deduct_tax"], "Add")

    def test_row_validation(self):
        other = Account.objects.create(
            name="Liab",
            company=self.other_company,
            root_type=AccountRootType.LIABILITY,
            is_group=True,
        )
        other_tax = Account.objects.create(
            name="Tax",
            company=self.other_company,
            parent=other,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.TAX,
        )
        bad_accounts = {
            "other company": other_tax,
            "group": self.liab,
            "not tax type": self.assets,
            "wrong direction (Input on a Sales template)": self.in_cgst,
        }
        for label, account in bad_accounts.items():
            res = self.client.post(
                "/api/accounts/tax-templates/",
                self.body(
                    title=f"T {label}", rows=[{"account": account.pk, "rate": "5"}]
                ),
                format="json",
            )
            self.assertEqual(res.status_code, 400, label)
            self.assertIn("rows", res.data, label)
        # a Tax account without GST tags is acceptable on any template
        res = self.client.post(
            "/api/accounts/tax-templates/",
            self.body(
                title="Plain",
                is_default=False,
                rows=[{"account": self.plain_tax.pk, "rate": "5"}],
            ),
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        # the same account twice
        res = self.client.post(
            "/api/accounts/tax-templates/",
            self.body(
                title="Twice",
                is_default=False,
                rows=[
                    {"account": self.out_cgst.pk, "rate": "5"},
                    {"account": self.out_cgst.pk, "rate": "5"},
                ],
            ),
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_purchase_template_needs_input_accounts_and_sales_cannot_be_rcm(self):
        res = self.client.post(
            "/api/accounts/tax-templates/",
            self.body(title="Purch", template_type="Purchase"),
            format="json",
        )
        self.assertEqual(res.status_code, 400)  # Output accounts on a Purchase template
        res = self.client.post(
            "/api/accounts/tax-templates/",
            self.body(
                title="Purch OK",
                template_type="Purchase",
                rows=[{"account": self.in_cgst.pk, "rate": "9"}],
            ),
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        res = self.client.post(
            "/api/accounts/tax-templates/",
            self.body(title="RCM sale", is_default=False, is_reverse_charge=True),
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_partial_unique_default(self):
        self.client.post("/api/accounts/tax-templates/", self.body(), format="json")
        # API layer
        res = self.client.post(
            "/api/accounts/tax-templates/", self.body(title="Second"), format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("is_default", res.data)
        # non-default twin is fine; RCM / other supply type may have their own default
        self.assertEqual(
            self.client.post(
                "/api/accounts/tax-templates/",
                self.body(title="Second", is_default=False),
                format="json",
            ).status_code,
            201,
        )
        self.assertEqual(
            self.client.post(
                "/api/accounts/tax-templates/",
                self.body(
                    title="Out",
                    gst_supply_type="Inter-State",
                    rows=[{"account": self.out_igst.pk, "rate": "18"}],
                ),
                format="json",
            ).status_code,
            201,
        )
        # DB layer (ORM bypasses the serializer)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TaxTemplate.objects.create(
                company=self.company,
                title="ORM dup",
                template_type="Sales",
                gst_supply_type="Intra-State",
                is_default=True,
            )

    def test_title_unique_per_company(self):
        self.client.post("/api/accounts/tax-templates/", self.body(), format="json")
        res = self.client.post(
            "/api/accounts/tax-templates/", self.body(is_default=False), format="json"
        )
        self.assertEqual(res.status_code, 400)

    def test_update_rows_and_header_only_patch(self):
        res = self.client.post(
            "/api/accounts/tax-templates/", self.body(), format="json"
        )
        pk = res.data["id"]
        res = self.client.patch(
            f"/api/accounts/tax-templates/{pk}/", {"disabled": True}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(len(res.data["rows"]), 2)
        res = self.client.patch(
            f"/api/accounts/tax-templates/{pk}/",
            {"rows": [{"account": self.out_igst.pk, "rate": "18"}]},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual([r["idx"] for r in res.data["rows"]], [1])
        self.assertEqual(TaxTemplateRow.objects.filter(template_id=pk).count(), 1)
        # moving the template to another company re-validates the stored rows
        res = self.client.patch(
            f"/api/accounts/tax-templates/{pk}/",
            {"company": self.other_company.pk},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_filters(self):
        self.client.post("/api/accounts/tax-templates/", self.body(), format="json")
        base = "/api/accounts/tax-templates/"
        self.assertEqual(
            self.client.get(f"{base}?company={self.company.pk}").data["count"], 1
        )
        self.assertEqual(
            self.client.get(f"{base}?template_type=Purchase").data["count"], 0
        )
        self.assertEqual(self.client.get(f"{base}?disabled=false").data["count"], 1)

    def test_row_db_checks(self):
        t = TaxTemplate.objects.create(
            company=self.company, title="Chk", template_type="Sales"
        )
        base = dict(template=t, account=self.out_cgst, rate=Decimal("9"))
        for extra in (
            {"idx": 1, "charge_type": "On Previous Row Total"},
            {"idx": 1, "category": "Valuation and Total"},
            {"idx": 1, "add_deduct_tax": "Subtract"},
            {"idx": 1, "rate": Decimal("100.5")},
            {"idx": 1, "rate": Decimal("-1")},
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                TaxTemplateRow.objects.create(**{**base, **extra})
        TaxTemplateRow.objects.create(**base, idx=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TaxTemplateRow.objects.create(**{**base, "account": self.out_sgst}, idx=1)


class ItemGSTTests(GSTBase):
    def setUp(self):
        super().setUp()
        self.group = ItemGroup.objects.create(name="IG")
        self.uom = UOM.objects.create(name="Nos-T")
        self.tpl = ItemTaxTemplate.objects.create(name="GST 18%-T")
        for comp, rate in (("CGST", "9"), ("SGST", "9"), ("IGST", "18")):
            self.tpl.rows.create(gst_component=comp, tax_rate=Decimal(rate))

    def body(self, **kw):
        data = {
            "code": "I-1",
            "name": "Item 1",
            "item_group": self.group.pk,
            "stock_uom": self.uom.pk,
        }
        data.update(kw)
        return data

    def test_hsn_validation(self):
        for bad in ("12", "12345", "10a6", "123456789"):
            res = self.client.post(
                "/api/stock/items/", self.body(gst_hsn_code=bad), format="json"
            )
            self.assertEqual(res.status_code, 400, bad)
            self.assertIn("gst_hsn_code", res.data)
        res = self.client.post(
            "/api/stock/items/",
            self.body(gst_hsn_code="1006", item_tax_template=self.tpl.pk),
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["item_tax_template_name"], "GST 18%-T")
        self.assertEqual(
            res.data["item_tax_rate"],
            {"CGST": "9.000", "SGST": "9.000", "IGST": "18.000"},
        )

    def test_item_without_template_has_empty_rate_dict(self):
        res = self.client.post("/api/stock/items/", self.body(), format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["item_tax_rate"], {})
        self.assertEqual(res.data["gst_hsn_code"], "")
        self.assertFalse(res.data["is_nil_exempt"])

    def test_nil_exempt_and_non_gst_are_exclusive(self):
        res = self.client.post(
            "/api/stock/items/",
            self.body(is_nil_exempt=True, is_non_gst=True),
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Item.objects.create(
                code="X",
                name="X",
                item_group=self.group,
                stock_uom=self.uom,
                is_nil_exempt=True,
                is_non_gst=True,
            )
        res = self.client.post(
            "/api/stock/items/", self.body(is_nil_exempt=True), format="json"
        )
        self.assertEqual(res.status_code, 201)
        # PATCH checks against the stored flag
        res = self.client.patch(
            f"/api/stock/items/{res.data['id']}/", {"is_non_gst": True}, format="json"
        )
        self.assertEqual(res.status_code, 400)

    def test_template_in_use_cannot_be_deleted(self):
        self.client.post(
            "/api/stock/items/", self.body(item_tax_template=self.tpl.pk), format="json"
        )
        with self.assertRaises(Exception), transaction.atomic():
            self.tpl.delete()

    def test_uom_exposes_uqc(self):
        res = self.client.patch(
            f"/api/stock/uoms/{self.uom.pk}/", {"gst_uqc": "NOS"}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["gst_uqc"], "NOS")
