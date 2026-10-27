"""Seeding, legacy-account migration and RBAC matrix tests."""

import importlib
from decimal import Decimal

from django.apps import apps as django_apps
from django.core.management import call_command
from rest_framework.test import APIClient, APITestCase

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    ItemTaxTemplate,
    TaxTemplate,
)
from core.management.commands.seed_defaults import (
    EXPOSED_MODEL_LABELS,
    validate_model_labels,
)
from core.models import Company, State
from gst.services import seeding
from stock.models import UOM
from users.models import Role, RolePermission, User, UserRole

GST_LABELS = ("core.State", "accounts.TaxTemplate", "accounts.ItemTaxTemplate")
LEGACY_MIGRATION = "accounts.migrations.0006_tag_legacy_gst_accounts"


def make_chart(company, with_legacy_tax=False):
    roots = {}
    for name, rt in (
        ("Assets", AccountRootType.ASSET),
        ("Liabilities", AccountRootType.LIABILITY),
        ("Income", AccountRootType.INCOME),
        ("Expenses", AccountRootType.EXPENSE),
    ):
        roots[rt] = Account.objects.create(
            name=name, company=company, root_type=rt, is_group=True
        )
    if with_legacy_tax:
        for comp in ("CGST", "SGST", "IGST"):
            Account.objects.create(
                name=comp,
                company=company,
                parent=roots[AccountRootType.LIABILITY],
                root_type=AccountRootType.LIABILITY,
                account_type=AccountType.TAX,
            )
    return roots


def tag_map(company):
    return {
        (a.gst_direction, a.gst_component): a
        for a in Account.objects.filter(company=company, gst_direction__isnull=False)
    }


def run_legacy_tag_migration():
    importlib.import_module(LEGACY_MIGRATION).tag_legacy_accounts(django_apps, None)


class ItemTaxTemplateSeedTests(APITestCase):
    def test_slabs_and_component_splits(self):
        seeding.ensure_item_tax_templates()
        expected = {
            "GST 0%": ("0", "0", "0"),
            "GST 0.25%": ("0.125", "0.125", "0.25"),
            "GST 3%": ("1.5", "1.5", "3"),
            "GST 5%": ("2.5", "2.5", "5"),
            "GST 18%": ("9", "9", "18"),
            "GST 40%": ("20", "20", "40"),
            "GST 12%": ("6", "6", "12"),
            "GST 28%": ("14", "14", "28"),
        }
        for name, (cgst, sgst, igst) in expected.items():
            t = ItemTaxTemplate.objects.get(name=name)
            rows = {r.gst_component: r.tax_rate for r in t.rows.all()}
            self.assertEqual(
                rows,
                {
                    "CGST": Decimal(cgst),
                    "SGST": Decimal(sgst),
                    "IGST": Decimal(igst),
                },
                name,
            )
            self.assertEqual(t.gst_treatment, "Taxable")
        self.assertTrue(ItemTaxTemplate.objects.get(name="GST 12%").is_legacy)
        self.assertTrue(ItemTaxTemplate.objects.get(name="GST 28%").is_legacy)
        self.assertFalse(ItemTaxTemplate.objects.get(name="GST 18%").is_legacy)
        for name, treatment in (
            ("Nil Rated", "Nil-Rated"),
            ("Exempted", "Exempted"),
            ("Non-GST", "Non-GST"),
        ):
            self.assertEqual(
                ItemTaxTemplate.objects.get(name=name).gst_treatment, treatment
            )

    def test_idempotent_and_keeps_operator_edits(self):
        seeding.ensure_item_tax_templates()
        n = ItemTaxTemplate.objects.count()
        t = ItemTaxTemplate.objects.get(name="GST 5%")
        t.rows.filter(gst_component="IGST").update(tax_rate=Decimal("6"))
        t.rows.filter(gst_component="CGST").delete()
        self.assertEqual(seeding.ensure_item_tax_templates(), 0)
        self.assertEqual(ItemTaxTemplate.objects.count(), n)
        self.assertEqual(t.rows.count(), 2)  # not re-added
        self.assertEqual(t.rows.get(gst_component="IGST").tax_rate, Decimal("6"))


class CompanySeedTests(APITestCase):
    def test_fresh_company_gets_tagged_accounts_round_off_and_templates(self):
        co = Company.objects.create(name="Fresh", abbr="FRSH")
        make_chart(co)
        summary = seeding.seed_company_gst(co)
        self.assertFalse(summary["skipped"])
        tags = tag_map(co)
        self.assertEqual(len(tags), 9)
        self.assertEqual(tags[("Output", "CGST")].name, "Output CGST")
        self.assertEqual(tags[("Input", "IGST")].name, "Input IGST")
        self.assertEqual(tags[("Reverse Charge", "SGST")].name, "RCM Payable SGST")
        self.assertEqual(tags[("Output", "CGST")].parent.name, "Duties and Taxes")
        self.assertEqual(tags[("Input", "CGST")].parent.name, "Input Tax Credit")
        self.assertEqual(tags[("Input", "CGST")].root_type, "Asset")
        co.refresh_from_db()
        self.assertEqual(co.round_off_account.name, "Round Off")
        self.assertEqual(co.round_off_account.account_type, "Round Off")
        self.assertEqual(co.round_off_account.root_type, "Expense")
        # only the seeded ACME company is switched to rounded totals
        self.assertFalse(co.enable_rounded_total)
        titles = set(
            TaxTemplate.objects.filter(company=co).values_list("title", flat=True)
        )
        self.assertEqual(
            titles,
            {
                "Output GST In-state",
                "Output GST Out-state",
                "Input GST In-state",
                "Input GST Out-state",
                "Input GST In-state RCM",
                "Input GST Out-state RCM",
            },
        )
        t = TaxTemplate.objects.get(company=co, title="Output GST In-state")
        self.assertEqual(
            [(r.account.name, r.rate) for r in t.rows.all()],
            [("Output CGST", Decimal("9")), ("Output SGST", Decimal("9"))],
        )
        self.assertTrue(t.is_default)
        self.assertEqual(t.gst_supply_type, "Intra-State")
        out = TaxTemplate.objects.get(company=co, title="Output GST Out-state")
        self.assertEqual([r.account.name for r in out.rows.all()], ["Output IGST"])
        self.assertEqual(out.rows.get().rate, Decimal("18"))
        rcm = TaxTemplate.objects.get(company=co, title="Input GST Out-state RCM")
        self.assertTrue(rcm.is_reverse_charge and rcm.is_default)
        self.assertEqual(rcm.template_type, "Purchase")

    def test_idempotent(self):
        co = Company.objects.create(name="Idem", abbr="IDEM")
        make_chart(co)
        seeding.seed_company_gst(co)
        counts = (
            Account.objects.filter(company=co).count(),
            TaxTemplate.objects.filter(company=co).count(),
        )
        summary = seeding.seed_company_gst(co)
        self.assertEqual(summary["accounts_created"], 0)
        self.assertEqual(summary["templates_created"], 0)
        self.assertEqual(
            counts,
            (
                Account.objects.filter(company=co).count(),
                TaxTemplate.objects.filter(company=co).count(),
            ),
        )

    def test_operator_template_edits_survive_reseed(self):
        co = Company.objects.create(name="Edit", abbr="EDIT")
        make_chart(co)
        seeding.seed_company_gst(co)
        t = TaxTemplate.objects.get(company=co, title="Output GST In-state")
        t.rows.all().delete()
        t.is_default = False
        t.save()
        seeding.seed_company_gst(co)
        t.refresh_from_db()
        self.assertEqual(t.rows.count(), 0)
        self.assertFalse(t.is_default)

    def test_company_without_chart_is_skipped(self):
        co = Company.objects.create(name="Bare", abbr="BARE")
        self.assertTrue(seeding.seed_company_gst(co)["skipped"])
        self.assertEqual(Account.objects.filter(company=co).count(), 0)

    def test_legacy_database_reaches_same_tagged_accounts_without_duplicates(self):
        """A DB seeded by the old seed_defaults has bare CGST/SGST/IGST accounts."""
        co = Company.objects.create(name="Legacy", abbr="LEG")
        make_chart(co, with_legacy_tax=True)
        run_legacy_tag_migration()
        tags = tag_map(co)
        self.assertEqual(set(tags), {("Output", c) for c in ("CGST", "SGST", "IGST")})
        self.assertEqual(tags[("Output", "CGST")].name, "CGST")  # name kept
        legacy_ids = {a.pk for a in tags.values()}

        seeding.seed_company_gst(co)  # must not trip the unique tag constraint
        tags = tag_map(co)
        self.assertEqual(len(tags), 9)
        self.assertEqual(
            {tags[("Output", c)].pk for c in ("CGST", "SGST", "IGST")}, legacy_ids
        )
        self.assertFalse(
            Account.objects.filter(company=co, name="Output CGST").exists()
        )
        # the templates use the legacy accounts for Output
        t = TaxTemplate.objects.get(company=co, title="Output GST In-state")
        self.assertEqual([r.account.name for r in t.rows.all()], ["CGST", "SGST"])

    def test_legacy_tag_migration_is_idempotent(self):
        co = Company.objects.create(name="Leg2", abbr="LEG2")
        make_chart(co, with_legacy_tax=True)
        run_legacy_tag_migration()
        run_legacy_tag_migration()
        self.assertEqual(len(tag_map(co)), 3)

    def test_untagged_same_name_account_is_tagged_not_duplicated(self):
        co = Company.objects.create(name="Named", abbr="NAMD")
        roots = make_chart(co)
        existing = Account.objects.create(
            name="Output IGST",
            company=co,
            parent=roots[AccountRootType.LIABILITY],
            root_type=AccountRootType.LIABILITY,
        )
        seeding.seed_company_gst(co)
        existing.refresh_from_db()
        self.assertEqual(
            (existing.gst_direction, existing.gst_component, existing.account_type),
            ("Output", "IGST", "Tax"),
        )

    def test_conflicting_same_name_account_is_skipped_with_warning(self):
        co = Company.objects.create(name="Clash", abbr="CLSH")
        roots = make_chart(co)
        Account.objects.create(
            name="Input CGST",
            company=co,
            parent=roots[AccountRootType.INCOME],
            root_type=AccountRootType.INCOME,
        )
        result = seeding.ensure_gst_accounts(co)
        self.assertTrue(any("Input CGST" in w for w in result["warnings"]))
        self.assertNotIn(("Input", "CGST"), tag_map(co))

    def test_acme_rounding_switched_on_once_and_respects_later_choice(self):
        co = Company.objects.create(name="Acme Corp 2", abbr="ACME")
        make_chart(co)
        seeding.seed_company_gst(co)
        co.refresh_from_db()
        self.assertTrue(co.enable_rounded_total)
        co.enable_rounded_total = False
        co.save()
        seeding.seed_company_gst(co)
        co.refresh_from_db()
        self.assertFalse(co.enable_rounded_total)


class SeedDefaultsTests(APITestCase):
    def snapshot(self):
        return (
            State.objects.count(),
            Account.objects.count(),
            TaxTemplate.objects.count(),
            ItemTaxTemplate.objects.count(),
            RolePermission.objects.count(),
        )

    def test_seed_defaults_is_idempotent_and_complete(self):
        call_command("seed_defaults", verbosity=0)
        before = self.snapshot()
        call_command("seed_defaults", verbosity=0)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(State.objects.count(), 40)
        acme = Company.objects.get(abbr="ACME")
        self.assertTrue(acme.enable_rounded_total)
        self.assertEqual(acme.hsn_min_digits, 6)
        self.assertIsNone(acme.gstin)  # never invented
        self.assertIsNone(acme.state_id)
        self.assertIsNotNone(acme.round_off_account_id)
        self.assertEqual(len(tag_map(acme)), 9)
        # the bare CGST/SGST/IGST names are no longer created for new databases
        self.assertFalse(Account.objects.filter(company=acme, name="CGST").exists())
        self.assertEqual(UOM.objects.get(name="Kg").gst_uqc, "KGS")
        self.assertEqual(UOM.objects.get(name="Nos").gst_uqc, "NOS")
        self.assertEqual(UOM.objects.get(name="Meter").gst_uqc, "MTR")

    def test_seed_defaults_over_legacy_accounts(self):
        """Bare accounts + migration tagging + the new seed: no unique violation."""
        call_command("seed_defaults", verbosity=0)
        acme = Company.objects.get(abbr="ACME")
        # simulate a legacy DB: drop the new tags, restore bare untagged accounts
        for a in Account.objects.filter(company=acme, gst_direction__isnull=False):
            a.gst_direction = a.gst_component = None
            a.save()
        for comp in ("CGST", "SGST", "IGST"):
            Account.objects.filter(company=acme, name=f"Output {comp}").update(
                name=comp
            )
        run_legacy_tag_migration()
        call_command("seed_defaults", verbosity=0)
        tags = tag_map(acme)
        self.assertEqual(len(tags), 9)
        self.assertEqual(tags[("Output", "SGST")].name, "SGST")


class RBACTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_defaults", verbosity=0)

    def perm(self, role, label):
        return RolePermission.objects.get(role__name=role, model_label=label)

    def test_labels_resolve_and_are_exposed(self):
        validate_model_labels(EXPOSED_MODEL_LABELS)
        for label in GST_LABELS:
            self.assertIn(label, EXPOSED_MODEL_LABELS)

    def test_roles_are_scoped(self):
        for label in GST_LABELS[1:]:
            full = self.perm("Accounts Manager", label)
            self.assertTrue(full.can_create and full.can_write and full.can_delete)
            for role in ("Sales Manager", "Purchase Manager", "Default Role"):
                p = self.perm(role, label)
                self.assertTrue(p.can_read, (role, label))
                self.assertFalse(
                    p.can_create or p.can_write or p.can_delete, (role, label)
                )
        for role in (
            "Sales Manager",
            "Purchase Manager",
            "Default Role",
            "Accounts Manager",
        ):
            p = self.perm(role, "core.State")
            self.assertTrue(p.can_read)
            self.assertFalse(p.can_create or p.can_write or p.can_delete)
        self.assertTrue(self.perm("Stock Manager", "accounts.ItemTaxTemplate").can_read)

    def client_for(self, role_name):
        user = User.objects.create_user(
            username=f"u_{role_name[:5]}", email=f"{role_name[:5]}@t.com", password="x"
        )
        UserRole.objects.create(user=user, role=Role.objects.get(name=role_name))
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_api_enforces_scoping(self):
        sales = self.client_for("Sales Manager")
        self.assertEqual(sales.get("/api/accounts/tax-templates/").status_code, 200)
        self.assertEqual(
            sales.get("/api/accounts/item-tax-templates/").status_code, 200
        )
        self.assertEqual(sales.get("/api/core/states/").status_code, 200)
        res = sales.post(
            "/api/accounts/item-tax-templates/", {"name": "X"}, format="json"
        )
        self.assertEqual(res.status_code, 403)

        acct = self.client_for("Accounts Manager")
        res = acct.post(
            "/api/accounts/item-tax-templates/",
            {"name": "GST 1%", "rows": [{"gst_component": "IGST", "tax_rate": "1"}]},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        # the states endpoint is read-only: a role without can_create gets 403
        self.assertEqual(
            acct.post("/api/core/states/", {}, format="json").status_code, 403
        )

        nobody = User.objects.create_user(
            username="nobody", email="nobody@t.com", password="x"
        )
        c = APIClient()
        c.force_authenticate(nobody)
        self.assertEqual(c.get("/api/core/states/").status_code, 403)
        self.assertEqual(c.get("/api/accounts/tax-templates/").status_code, 403)

    def test_accounts_manager_can_create_a_tax_template(self):
        acme = Company.objects.get(abbr="ACME")
        tags = tag_map(acme)
        acct = self.client_for("Accounts Manager")
        res = acct.post(
            "/api/accounts/tax-templates/",
            {
                "company": acme.pk,
                "title": "Custom 5%",
                "template_type": "Sales",
                "rows": [{"account": tags[("Output", "IGST")].pk, "rate": "5"}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
