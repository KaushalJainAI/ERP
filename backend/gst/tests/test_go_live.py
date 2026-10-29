"""GST go-live: separate Input / Output accounts, rate templates, company GSTIN checks,
Tally ledger-name suggestions (docs/tally_data_gaps.md G3)."""

import shutil
import tempfile
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    TaxTemplate,
    TaxTemplateRow,
)
from core.models import Company
from gst.services import go_live, integrity
from gst.tests.test_reports import OCT, ReportBase
from tally_export.services import export as tally_export
from tally_export.services.maps import suggest_ledger

D = Decimal
MEDIA = tempfile.mkdtemp(prefix="gst-go-live-")


def tearDownModule():
    shutil.rmtree(MEDIA, ignore_errors=True)


class TallyStyleBase(ReportBase):
    """The company as it comes out of the Tally import: combined GST ledgers under
    'Duties & Taxes', no tagged accounts and no tax templates."""

    def setUp(self):
        super().setUp()
        TaxTemplate.objects.filter(company=self.company).delete()
        Account.objects.filter(
            company=self.company, gst_direction__isnull=False
        ).delete()
        # The seeded groups are not part of a Tally-imported chart.
        Account.objects.filter(
            company=self.company, name__in=["Duties and Taxes", "Input Tax Credit"]
        ).delete()
        liabilities = Account.objects.get(company=self.company, name="Liabilities")
        self.duties = Account.objects.create(
            company=self.company,
            name="Duties & Taxes",
            parent=liabilities,
            root_type=AccountRootType.LIABILITY,
            is_group=True,
        )
        self.legacy = {}
        for name in ("C Gst", "S Gst", "I Gst", "Cess Gst"):
            self.legacy[name] = Account.objects.create(
                company=self.company,
                name=name,
                parent=self.duties,
                root_type=AccountRootType.LIABILITY,
                account_type=AccountType.TAX,
            )

    def setup_gst(self, **kw):
        return go_live.setup_company(self.company, **kw)

    def tagged(self):
        return {
            (a.gst_direction, a.gst_component): a
            for a in Account.objects.filter(
                company=self.company, gst_direction__isnull=False
            )
        }


class AccountSetupTests(TallyStyleBase):
    def test_creates_six_tagged_accounts_under_duties_and_taxes(self):
        summary = self.setup_gst()
        tagged = self.tagged()
        self.assertEqual(
            sorted(tagged),
            sorted(
                (d, c) for d in ("Input", "Output") for c in ("CGST", "SGST", "IGST")
            ),
        )
        for (direction, component), account in tagged.items():
            self.assertEqual(account.name, f"{direction} {component}")
            self.assertEqual(account.account_type, AccountType.TAX)
            self.assertEqual(account.parent_id, self.duties.pk)
            self.assertFalse(account.is_group)
        self.assertEqual(len(summary["accounts_created"]), 6)
        self.assertEqual(
            summary["legacy_accounts"], ["C Gst", "Cess Gst", "I Gst", "S Gst"]
        )

    def test_legacy_combined_ledgers_are_untouched(self):
        before = {
            a.pk: (
                a.name,
                a.parent_id,
                a.gst_direction,
                a.gst_component,
                a.account_type,
            )
            for a in self.legacy.values()
        }
        self.setup_gst()
        for pk, expected in before.items():
            a = Account.objects.get(pk=pk)
            self.assertEqual(
                (a.name, a.parent_id, a.gst_direction, a.gst_component, a.account_type),
                expected,
            )

    def test_cess_accounts_only_on_request(self):
        self.setup_gst()
        self.assertNotIn(("Output", "CESS"), self.tagged())
        self.setup_gst(with_cess=True)
        self.assertIn(("Output", "CESS"), self.tagged())
        self.assertIn(("Input", "CESS"), self.tagged())

    def test_group_is_created_when_the_chart_has_none(self):
        for a in self.legacy.values():
            Account.objects.filter(pk=a.pk).delete()
        self.duties.delete()
        self.setup_gst()
        group = Account.objects.get(company=self.company, name="Duties & Taxes")
        self.assertTrue(group.is_group)
        self.assertEqual(group.root_type, AccountRootType.LIABILITY)
        self.assertEqual(self.tagged()[("Input", "IGST")].parent_id, group.pk)

    def test_group_spelled_and_is_found(self):
        Account.objects.filter(pk=self.duties.pk).update(name="Duties and Taxes")
        self.setup_gst()
        self.assertEqual(self.tagged()[("Output", "SGST")].parent_id, self.duties.pk)
        self.assertEqual(
            Account.objects.filter(company=self.company, is_group=True)
            .filter(name__icontains="duties")
            .count(),
            1,
        )

    def test_an_untagged_account_of_the_target_name_is_tagged_not_duplicated(self):
        existing = Account.objects.create(
            company=self.company,
            name="Input CGST",
            parent=self.duties,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.TAX,
        )
        summary = self.setup_gst()
        existing.refresh_from_db()
        self.assertEqual(
            (existing.gst_direction, existing.gst_component), ("Input", "CGST")
        )
        self.assertEqual(
            Account.objects.filter(company=self.company, name="Input CGST").count(), 1
        )
        self.assertIn("Input CGST", summary["accounts_tagged"])

    def test_incompatible_same_name_account_is_skipped_with_a_warning(self):
        Account.objects.create(
            company=self.company,
            name="Output IGST",
            parent=self.duties,
            root_type=AccountRootType.LIABILITY,
            account_type=AccountType.PAYABLE,
        )
        summary = self.setup_gst()
        self.assertNotIn(("Output", "IGST"), self.tagged())
        self.assertTrue(any("Output IGST" in w for w in summary["warnings"]))


class IdempotencyTests(TallyStyleBase):
    def snapshot(self):
        return (
            sorted(
                Account.objects.filter(company=self.company).values_list(
                    "name", flat=True
                )
            ),
            sorted(
                TaxTemplate.objects.filter(company=self.company).values_list(
                    "title", "is_default"
                )
            ),
            TaxTemplateRow.objects.filter(template__company=self.company).count(),
        )

    def test_second_run_changes_nothing(self):
        first = self.setup_gst()
        snap = self.snapshot()
        second = self.setup_gst()
        self.assertEqual(self.snapshot(), snap)
        self.assertTrue(first["accounts_created"] and first["templates_created"])
        self.assertEqual(second["accounts_created"], [])
        self.assertEqual(second["templates_created"], [])
        self.assertEqual(second["defaults_set"], [])

    def test_operator_edits_to_a_template_are_kept(self):
        self.setup_gst()
        template = TaxTemplate.objects.get(
            company=self.company, title="Output GST 12% In-state"
        )
        TaxTemplateRow.objects.filter(template=template).update(rate=D("7"))
        summary = self.setup_gst()
        rates = sorted(r.rate for r in template.rows.all())
        self.assertEqual(rates, [D("7"), D("7")])
        # Not re-created under another name either.
        self.assertEqual(summary["templates_created"], [])
        self.assertTrue(any("different rows" in w for w in summary["warnings"]))

    def test_command_is_idempotent_and_reports(self):
        out = StringIO()
        call_command("setup_gst_accounts", company=[self.company.name], stdout=out)
        snap = self.snapshot()
        self.assertIn("Output CGST", out.getvalue())
        self.assertIn("legacy combined ledgers left untouched", out.getvalue())
        call_command(
            "setup_gst_accounts", company=[self.company.abbr], stdout=StringIO()
        )
        self.assertEqual(self.snapshot(), snap)

    def test_command_rejects_unknown_company_and_bad_rates(self):
        with self.assertRaises(CommandError):
            call_command("setup_gst_accounts", company=["Nope"], stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command(
                "setup_gst_accounts",
                company=[self.company.name],
                rates="5,abc",
                stdout=StringIO(),
            )
        with self.assertRaises(CommandError):
            call_command(
                "setup_gst_accounts",
                company=[self.company.name],
                rates="5,18",
                default_rate="12",
                stdout=StringIO(),
            )


class TemplateTests(TallyStyleBase):
    def test_a_template_per_rate_supply_and_direction(self):
        self.setup_gst()
        titles = set(
            TaxTemplate.objects.filter(company=self.company).values_list(
                "title", flat=True
            )
        )
        expected = {
            f"{p} GST {r}% {s}"
            for p in ("Output", "Input")
            for r in (5, 12, 18)
            for s in ("In-state", "Out-state")
        }
        self.assertEqual(titles, expected)

    def test_rows_point_at_the_new_accounts_and_split_rates(self):
        self.setup_gst()
        tagged = self.tagged()

        def rows(title):
            t = TaxTemplate.objects.get(company=self.company, title=title)
            return [(r.account_id, r.rate) for r in t.rows.order_by("idx")]

        self.assertEqual(
            rows("Output GST 5% In-state"),
            [
                (tagged[("Output", "CGST")].pk, D("2.5")),
                (tagged[("Output", "SGST")].pk, D("2.5")),
            ],
        )
        self.assertEqual(
            rows("Input GST 12% Out-state"), [(tagged[("Input", "IGST")].pk, D("12"))]
        )
        # No template ever uses a legacy combined ledger.
        legacy_ids = {a.pk for a in self.legacy.values()}
        used = set(
            TaxTemplateRow.objects.filter(template__company=self.company).values_list(
                "account_id", flat=True
            )
        )
        self.assertFalse(used & legacy_ids)

    def test_five_percent_templates_are_the_defaults(self):
        self.setup_gst()
        defaults = set(
            TaxTemplate.objects.filter(
                company=self.company, is_default=True
            ).values_list("title", flat=True)
        )
        self.assertEqual(
            defaults,
            {
                "Output GST 5% In-state",
                "Output GST 5% Out-state",
                "Input GST 5% In-state",
                "Input GST 5% Out-state",
            },
        )

    def test_an_existing_default_moves_to_the_requested_rate(self):
        self.setup_gst(default_rate=D("18"))
        self.setup_gst(default_rate=D("5"))
        sales_intra = TaxTemplate.objects.filter(
            company=self.company,
            template_type="Sales",
            gst_supply_type="Intra-State",
            is_reverse_charge=False,
            is_default=True,
        )
        self.assertEqual([t.title for t in sales_intra], ["Output GST 5% In-state"])

    def test_keep_leaves_an_existing_default_alone(self):
        self.setup_gst(default_rate=D("18"))
        self.setup_gst(default_rate=None)
        self.assertTrue(
            TaxTemplate.objects.get(
                company=self.company, title="Output GST 18% In-state"
            ).is_default
        )

    def test_equivalent_existing_template_is_reused_not_duplicated(self):
        self.setup_gst()
        TaxTemplate.objects.filter(
            company=self.company, title="Output GST 18% In-state"
        ).update(title="Output GST In-state")
        summary = self.setup_gst()
        self.assertNotIn("Output GST 18% In-state", summary["templates_created"])
        self.assertFalse(
            TaxTemplate.objects.filter(
                company=self.company, title="Output GST 18% In-state"
            ).exists()
        )

    def test_legacy_templates_are_reported_and_not_default(self):
        legacy = TaxTemplate.objects.create(
            company=self.company,
            title="Old Tally GST",
            template_type="Sales",
            gst_supply_type="Intra-State",
            is_default=True,
        )
        TaxTemplateRow.objects.create(
            template=legacy, idx=1, account=self.legacy["C Gst"], rate=D("9")
        )
        summary = self.setup_gst()
        self.assertEqual(summary["legacy_templates"], ["Old Tally GST"])
        self.assertFalse(TaxTemplate.objects.get(pk=legacy.pk).is_default)


class InvoiceSplitAndGstr3bTests(TallyStyleBase):
    def setUp(self):
        super().setUp()
        self.setup_gst()
        self.t = {t.title: t for t in TaxTemplate.objects.filter(company=self.company)}

    def test_intra_state_sale_at_5_percent_splits_cgst_sgst(self):
        d = self.sale(self.cust_intra, (self.item_plain, 1, 1000), **OCT)
        self.assertEqual(d["taxes_and_charges_name"], "Output GST 5% In-state")
        self.assertEqual(self.components(d), {"CGST": D("25.00"), "SGST": D("25.00")})
        self.assertEqual(
            self.gl(d["name"]),
            sorted(
                [
                    ("Debtors", D("1050.00"), D("0.00")),
                    ("Sales", D("0.00"), D("1000.00")),
                    ("Output CGST", D("0.00"), D("25.00")),
                    ("Output SGST", D("0.00"), D("25.00")),
                ]
            ),
        )
        self.integrity_ok()

    def test_inter_state_sale_at_5_percent_is_igst_only(self):
        d = self.sale(self.cust_inter, (self.item_plain, 1, 1000), **OCT)
        self.assertEqual(d["taxes_and_charges_name"], "Output GST 5% Out-state")
        self.assertEqual(self.components(d), {"IGST": D("50.00")})

    def test_12_and_18_percent_templates_selected_explicitly(self):
        d12 = self.sale(
            self.cust_intra,
            (self.item_plain, 1, 1000),
            taxes_and_charges=self.t["Output GST 12% In-state"].pk,
            **OCT,
        )
        self.assertEqual(self.components(d12), {"CGST": D("60.00"), "SGST": D("60.00")})
        d18 = self.sale(
            self.cust_inter,
            (self.item_plain, 1, 1000),
            taxes_and_charges=self.t["Output GST 18% Out-state"].pk,
            **OCT,
        )
        self.assertEqual(self.components(d18), {"IGST": D("180.00")})

    def test_purchase_books_input_accounts(self):
        d = self.purchase(self.sup_intra, (self.item_plain, 1, 1000), **OCT)
        self.assertEqual(d["taxes_and_charges_name"], "Input GST 5% In-state")
        names = {n for n, _dr, _cr in self.gl(d["name"])}
        self.assertTrue({"Input CGST", "Input SGST"} <= names)
        self.assertFalse(names & {"Output CGST", "C Gst", "S Gst"})

    def test_gstr3b_separates_input_and_output(self):
        self.sale(self.cust_intra, (self.item_plain, 1, 1000), **OCT)  # Out 25 + 25
        self.sale(self.cust_inter, (self.item_plain, 1, 2000), **OCT)  # Out IGST 100
        self.purchase(self.sup_intra, (self.item_plain, 1, 400), **OCT)  # In 10 + 10
        self.purchase(self.sup_inter, (self.item_plain, 1, 600), **OCT)  # In IGST 30
        report = self.gstr3b()
        self.assertEqual(
            report["3.1"]["a_outward_taxable"],
            {
                "taxable_value": "3000.00",
                "igst": "100.00",
                "cgst": "25.00",
                "sgst": "25.00",
                "cess": "0.00",
            },
        )
        self.assertEqual(
            report["4"]["A"]["all_other_itc"],
            {"igst": "30.00", "cgst": "10.00", "sgst": "10.00", "cess": "0.00"},
        )
        self.assertEqual(
            report["4"]["C_net_itc"],
            {"igst": "30.00", "cgst": "10.00", "sgst": "10.00", "cess": "0.00"},
        )
        # Nothing ever posted to the combined legacy ledgers.
        self.assertFalse(
            GLEntry.objects.filter(account__in=list(self.legacy.values())).exists()
        )
        self.integrity_ok()


class GstinChecksTests(TallyStyleBase):
    def test_valid_company_has_no_identity_findings(self):
        self.assertEqual(go_live.company_gst_findings(self.company), [])

    def test_missing_gstin_is_a_clear_warning(self):
        Company.objects.filter(pk=self.company.pk).update(gstin=None)
        self.company.refresh_from_db()
        findings = go_live.company_gst_findings(self.company)
        self.assertEqual(len(findings), 1)
        self.assertIn("has no GSTIN", findings[0])
        self.assertIn("Madhya Pradesh = 23", findings[0])

    def test_invalid_gstin_is_reported(self):
        Company.objects.filter(pk=self.company.pk).update(gstin="27AABCT1234C1ZX")
        self.company.refresh_from_db()
        findings = go_live.company_gst_findings(self.company)
        self.assertTrue(any("invalid" in f for f in findings))

    def test_state_missing_or_mismatching_the_gstin_prefix(self):
        Company.objects.filter(pk=self.company.pk).update(state=None)
        self.company.refresh_from_db()
        self.assertTrue(
            any("no State" in f for f in go_live.company_gst_findings(self.company))
        )
        Company.objects.filter(pk=self.company.pk).update(state_id="23")
        self.company.refresh_from_db()
        self.assertTrue(
            any(
                "does not match" in f
                for f in go_live.company_gst_findings(self.company)
            )
        )

    def test_verify_integrity_surfaces_it_without_failing(self):
        Company.objects.filter(pk=self.company.pk).update(gstin=None)
        errors, warnings = integrity.check()
        self.assertEqual(errors, [])
        self.assertTrue(any("[GST setup]" in w and "no GSTIN" in w for w in warnings))
        self.integrity_ok()

    def test_registered_company_without_tagged_accounts_is_warned(self):
        _errors, warnings = integrity.check()
        self.assertTrue(any("no tagged GST account" in w for w in warnings))
        self.setup_gst()
        _errors, warnings = integrity.check()
        self.assertFalse(any("no tagged GST account" in w for w in warnings))


@override_settings(MEDIA_ROOT=MEDIA)
class TallyLedgerNameTests(TallyStyleBase):
    def test_suggested_names_for_tagged_accounts(self):
        self.setup_gst()
        tagged = self.tagged()
        self.assertEqual(suggest_ledger(tagged[("Input", "CGST")]), "Input CGST")
        self.assertEqual(suggest_ledger(tagged[("Output", "IGST")]), "Output IGST")

    def test_renamed_tagged_account_still_gets_the_conventional_name(self):
        self.setup_gst()
        acc = self.tagged()[("Output", "SGST")]
        Account.objects.filter(pk=acc.pk).update(name="SGST Payable")
        acc.refresh_from_db()
        self.assertEqual(suggest_ledger(acc), "Output SGST")

    def test_untagged_legacy_ledger_keeps_its_own_name(self):
        self.assertEqual(suggest_ledger(self.legacy["C Gst"]), "C Gst")

    def test_preflight_proposes_input_and_output_names(self):
        self.setup_gst()
        self.sale(self.cust_intra, (self.item_plain, 1, 1000), **OCT)
        self.purchase(self.sup_intra, (self.item_plain, 1, 400), **OCT)
        pre = tally_export.preflight(self.company)
        accounts = pre["unmapped"]["accounts"]
        suggested = {a["account_name"]: a["suggested_ledger"] for a in accounts}
        groups = {a["account_name"]: a["suggested_group"] for a in accounts}
        self.assertEqual(suggested["Output CGST"], "Output CGST")
        self.assertEqual(suggested["Input SGST"], "Input SGST")
        self.assertEqual(groups["Output CGST"], "Duties & Taxes")
