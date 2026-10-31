"""TDS on salaried employees (T-6).  Run with ``pytest hr/test_tds.py``.

The rate figures below are the seeded FY 2026-27 values (``hr.services.tax_rates``).
The old-regime rates are a test fixture only: the sources given for this task
do not set them, so none are seeded.  Nothing here names a section number.
"""

import datetime
from decimal import Decimal as D

from django.core.exceptions import ValidationError

from accounts.models import Account, AccountRootType, GLEntry
from core.models import DocStatus
from hr.models import (
    Employee,
    HRSettings,
    IncomeTaxRateSet,
    IncomeTaxSlab,
    PayType,
    SalaryComponent,
    SalarySlip,
    SalarySlipDetail,
    SystemComponent,
    TaxRegime,
)
from hr.services.tax_rates import (
    check_slab_rows,
    monthly_tds_from_salary,
    rate_set_for,
    seed_income_tax_rates,
)
from hr.tests import HRBase

d = datetime.date
API = "/api/hr/"
PAN = "ABCDE1234F"


class TdsBase(HRBase):
    """HR base with the salary helpers TDS tests need."""

    def monthly_employee(self, monthly, pan=PAN, **kw):
        emp = self.employee("Salaried", pay_type=PayType.MONTHLY, pan=pan, **kw)
        self.rate(emp, daily=None, monthly=str(monthly), ot="0")
        return emp

    def tds_credit(self, slip_name=None):
        """Net credit to the TDS payable account from salary slips (live rows)."""
        entries = GLEntry.objects.filter(
            account=self.cfg.tds_payable_account,
            voucher_type="hr.SalarySlip",
            is_cancelled=False,
        )
        if slip_name:
            entries = entries.filter(voucher_no=slip_name)
        return sum((e.credit - e.debit for e in entries), D("0.00"))

    def gl_count(self, slip_name):
        return GLEntry.objects.filter(
            voucher_type="hr.SalarySlip", voucher_no=slip_name, is_cancelled=False
        ).count()


def new_rates():
    return rate_set_for(TaxRegime.NEW, d(2026, 10, 31))


def make_old_regime_fixture():
    """Test-only old-regime rates (not seeded; the accountant enters the real ones)."""
    rate_set = IncomeTaxRateSet.objects.create(
        regime=TaxRegime.OLD,
        effective_from=d(2026, 4, 1),
        label="Test fixture old regime",
        standard_deduction=D("50000"),
        rebate_income_limit=D("500000"),
        rebate_amount=D("12500"),
        cess_percent=D("4"),
    )
    for lower, upper, rate in (
        ("0", "250000", "0"),
        ("250000", "500000", "5"),
        ("500000", "1000000", "20"),
        ("1000000", None, "30"),
    ):
        IncomeTaxSlab.objects.create(
            rate_set=rate_set,
            lower_limit=D(lower),
            upper_limit=D(upper) if upper else None,
            rate_percent=D(rate),
        )
    return rate_set


# ------------------------------------------------------------- the calculation
class IncomeTaxCalculationTests(TdsBase):
    def setUp(self):
        super().setUp()
        seed_income_tax_rates()

    def test_seed_creates_fy_2026_27_new_regime_once(self):
        rate_set = new_rates()
        self.assertEqual(rate_set.effective_from, d(2026, 4, 1))
        self.assertEqual(rate_set.standard_deduction, D("75000"))
        self.assertEqual(rate_set.rebate_income_limit, D("1200000"))
        self.assertEqual(rate_set.rebate_amount, D("60000"))
        self.assertEqual(rate_set.slabs.count(), 7)
        self.assertEqual(rate_set.surcharges.count(), 3)
        # A second seed run changes nothing.
        self.assertEqual(seed_income_tax_rates(), 0)
        self.assertEqual(IncomeTaxRateSet.objects.count(), 1)

    def test_salary_1_50_000_a_month_under_new_regime(self):
        # Taxable 17.25 lakh: slab tax 1,45,000, cess 5,800, so 1,50,800 a year.
        self.assertEqual(
            monthly_tds_from_salary(D("150000"), new_rates()), D("12566.67")
        )

    def test_salary_2_00_000_a_month_under_new_regime(self):
        # Taxable 23.25 lakh: slab tax 2,81,250, cess 11,250, so 2,92,500 a year.
        self.assertEqual(
            monthly_tds_from_salary(D("200000"), new_rates()), D("24375.00")
        )

    def test_marginal_relief_just_above_the_rebate_limit(self):
        # Taxable 12.45 lakh: slab tax 66,750, but the tax is capped at the 45,000
        # above 12 lakh; cess 1,800, so 46,800 a year.
        self.assertEqual(
            monthly_tds_from_salary(D("110000"), new_rates()), D("3900.00")
        )

    def test_salary_at_the_rebate_limit_has_zero_tds(self):
        # 1,06,250 a month is 12.75 lakh a year: taxable exactly 12 lakh.
        self.assertEqual(monthly_tds_from_salary(D("106250"), new_rates()), D("0.00"))
        # Well below the limit as well.
        self.assertEqual(monthly_tds_from_salary(D("100000"), new_rates()), D("0.00"))

    def test_surcharge_applies_above_50_lakh_taxable(self):
        # Taxable 59.25 lakh: slab tax 13,57,500, surcharge 10 per cent, cess 4 per cent.
        self.assertEqual(
            monthly_tds_from_salary(D("500000"), new_rates()), D("129415.00")
        )

    def test_old_regime_is_an_opt_in_with_its_own_rates(self):
        make_old_regime_fixture()
        old = rate_set_for(TaxRegime.OLD, d(2026, 10, 31))
        # Test fixture: taxable 11.5 lakh gives 1,57,500 slab tax and 6,300 cess.
        self.assertEqual(monthly_tds_from_salary(D("100000"), old), D("13650.00"))
        # The same salary is zero under the new regime.
        self.assertEqual(monthly_tds_from_salary(D("100000"), new_rates()), D("0.00"))

    def test_rates_come_from_the_table_not_from_code(self):
        slab = IncomeTaxSlab.objects.get(
            rate_set=new_rates(), lower_limit=D("400000"), upper_limit=D("800000")
        )
        slab.rate_percent = D("6")
        slab.save()
        # 5 per cent became 6 per cent on the 4 to 8 lakh band: +4,000 tax, +160 cess.
        self.assertEqual(
            monthly_tds_from_salary(D("150000"), new_rates()), D("12913.33")
        )

    def test_effective_date_selects_the_rate_set_in_force(self):
        later = IncomeTaxRateSet.objects.create(
            regime=TaxRegime.NEW,
            effective_from=d(2027, 4, 1),
            label="FY 2027-28 new regime",
            standard_deduction=D("80000"),
            rebate_income_limit=D("1200000"),
            rebate_amount=D("60000"),
        )
        self.assertEqual(
            rate_set_for(TaxRegime.NEW, d(2026, 4, 1)).label, "FY 2026-27 new regime"
        )
        self.assertEqual(
            rate_set_for(TaxRegime.NEW, d(2027, 3, 31)).label, "FY 2026-27 new regime"
        )
        self.assertEqual(rate_set_for(TaxRegime.NEW, d(2027, 4, 1)), later)
        self.assertEqual(rate_set_for(TaxRegime.NEW, d(2028, 1, 1)), later)

    def test_no_rate_set_in_force_is_refused(self):
        # Before the first set.
        with self.assertRaises(ValidationError):
            rate_set_for(TaxRegime.NEW, d(2026, 3, 31))
        # Old regime has no rows in the seed.
        with self.assertRaises(ValidationError):
            rate_set_for(TaxRegime.OLD, d(2026, 10, 31))

    def test_slab_table_must_join_from_zero_with_no_gap_or_overlap(self):
        d0, d4, d8 = D("0"), D("400000"), D("800000")
        check_slab_rows([(d0, d4), (d4, d8), (d8, None)])  # valid: no error
        bad_tables = {
            "gap": [(d0, d4), (D("500000"), None)],
            "overlap": [(d0, D("500000")), (d4, None)],
            "not from zero": [(d4, None)],
            "middle without upper limit": [(d0, None), (d4, None)],
            "last with upper limit": [(d0, d4), (d4, d8)],
            "empty": [],
        }
        for name, rows in bad_tables.items():
            with self.subTest(name), self.assertRaises(ValidationError):
                check_slab_rows(rows)


# ------------------------------------------------------------ slip behaviour
class TdsOnSlipTests(TdsBase):
    def setUp(self):
        super().setUp()
        seed_income_tax_rates()

    def tds_rows(self, slip_name):
        return SalarySlipDetail.objects.filter(
            slip__name=slip_name, component__system_key=SystemComponent.TDS
        )

    def test_monthly_slip_deducts_tds_and_credits_tds_payable(self):
        emp = self.monthly_employee(D("150000"))
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        slip = self.make_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        self.assertEqual(slip["gross_pay"], "150000.00")
        self.assertEqual(slip["total_deduction"], "12566.67")
        self.assertEqual(slip["net_pay"], "137433.33")
        self.submit_slip(slip["id"])
        self.assertEqual(self.tds_credit(slip["name"]), D("12566.67"))
        self.verify()

    def test_salary_at_rebate_limit_creates_no_tds_row(self):
        emp = self.monthly_employee(D("106250"))
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        slip = self.make_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        self.assertFalse(self.tds_rows(slip["name"]).exists())
        self.submit_slip(slip["id"])
        self.assertEqual(self.tds_credit(slip["name"]), D("0.00"))

    def test_daily_wager_gets_no_tds(self):
        emp = self.employee("Ramu", pay_type=PayType.DAILY)
        self.rate(emp, daily="500", ot="0")
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        slip = self.make_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        self.assertFalse(self.tds_rows(slip["name"]).exists())
        self.assertEqual(slip["net_pay"], "15000.00")
        self.submit_slip(slip["id"])
        self.assertEqual(self.tds_credit(slip["name"]), D("0.00"))

    def test_several_slips_in_one_month_deduct_once(self):
        emp = self.monthly_employee(D("150000"))
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        first = self.make_slip(emp, d(2026, 4, 1), d(2026, 4, 15))
        self.submit_slip(first["id"])
        # Created after the first submit: no TDS row at all.
        second = self.make_slip(emp, d(2026, 4, 16), d(2026, 4, 30))
        self.assertFalse(self.tds_rows(second["name"]).exists())
        self.assertEqual(second["net_pay"], "75000.00")
        self.submit_slip(second["id"])
        self.assertEqual(self.tds_credit(), D("12566.67"))
        self.verify()

    def test_two_drafts_in_one_month_refuse_second_submit_until_recomputed(self):
        emp = self.monthly_employee(D("150000"))
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        first = self.make_slip(emp, d(2026, 4, 1), d(2026, 4, 15))
        second = self.make_slip(emp, d(2026, 4, 16), d(2026, 4, 30))
        # Both drafts were created before either was submitted, so both carry TDS.
        self.assertTrue(self.tds_rows(second["name"]).exists())
        self.submit_slip(first["id"])
        r = self.api.post(f"{API}salary-slips/{second['id']}/submit/")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn(b"recompute", r.content)
        # Refused: still a draft, and nothing posted for it.
        self.assertEqual(
            SalarySlip.objects.get(pk=second["id"]).docstatus, DocStatus.DRAFT
        )
        self.assertEqual(self.gl_count(second["name"]), 0)
        # Recompute drops the duplicate TDS, and then it submits.
        r = self.api.post(f"{API}salary-slips/{second['id']}/recompute/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(self.tds_rows(second["name"]).exists())
        self.submit_slip(second["id"])
        self.assertEqual(self.tds_credit(), D("12566.67"))

    def test_draft_save_keeps_the_tds_row(self):
        emp = self.monthly_employee(D("150000"))
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        slip = self.make_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        r = self.api.patch(
            f"{API}salary-slips/{slip['id']}/", {"remarks": "April"}, format="json"
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.tds_rows(slip["name"]).get().amount, D("12566.67"))
        self.assertEqual(r.json()["net_pay"], "137433.33")

    def test_pan_is_required_when_tds_is_due_and_refusal_posts_nothing(self):
        emp = self.monthly_employee(D("150000"), pan="")
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        slip = self.make_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        r = self.api.post(f"{API}salary-slips/{slip['id']}/submit/")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn(b"PAN", r.content)
        self.assertEqual(
            SalarySlip.objects.get(pk=slip["id"]).docstatus, DocStatus.DRAFT
        )
        self.assertEqual(self.gl_count(slip["name"]), 0)
        # Once the PAN is on file, the same slip submits.
        Employee.objects.filter(pk=emp.pk).update(pan=PAN)
        self.submit_slip(slip["id"])
        self.assertEqual(self.tds_credit(slip["name"]), D("12566.67"))

    def test_stale_tds_is_refused_at_submit_after_a_rate_change(self):
        emp = self.monthly_employee(D("150000"))
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        slip = self.make_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        IncomeTaxSlab.objects.filter(
            rate_set=new_rates(), lower_limit=D("400000")
        ).update(rate_percent=D("6"))
        r = self.api.post(f"{API}salary-slips/{slip['id']}/submit/")
        self.assertEqual(r.status_code, 400, r.content)
        self.api.post(f"{API}salary-slips/{slip['id']}/recompute/")
        self.submit_slip(slip["id"])
        self.assertEqual(self.tds_credit(slip["name"]), D("12913.33"))

    def test_fixed_tds_is_capped_by_pay_so_net_pay_stays_zero_or_more(self):
        emp = self.monthly_employee(D("30000"), tds_per_month=D("50000"))
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        slip = self.make_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        self.assertEqual(slip["total_deduction"], "30000.00")
        self.assertEqual(slip["net_pay"], "0.00")
        self.submit_slip(slip["id"])
        self.assertEqual(self.tds_credit(slip["name"]), D("30000.00"))

    def test_fixed_tds_of_zero_means_no_deduction(self):
        emp = self.monthly_employee(D("150000"), tds_per_month=D("0"))
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        slip = self.make_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        self.assertFalse(self.tds_rows(slip["name"]).exists())

    def test_old_regime_opt_in_changes_the_slip(self):
        make_old_regime_fixture()
        new_emp = self.monthly_employee(D("100000"))
        old_emp = self.monthly_employee(D("100000"), tax_regime=TaxRegime.OLD)
        for emp in (new_emp, old_emp):
            self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        new_slip = self.make_slip(new_emp, d(2026, 4, 1), d(2026, 4, 30))
        old_slip = self.make_slip(old_emp, d(2026, 4, 1), d(2026, 4, 30))
        self.assertFalse(self.tds_rows(new_slip["name"]).exists())
        self.assertEqual(old_slip["total_deduction"], "13650.00")

    def test_setup_creates_the_tds_component_and_account(self):
        self.assertEqual(self.cfg.tds_payable_account.name, "TDS Payable")
        self.assertTrue(
            SalaryComponent.objects.filter(
                company=self.company, system_key=SystemComponent.TDS
            ).exists()
        )

    def test_tds_payable_account_is_locked_once_slips_post(self):
        emp = self.monthly_employee(D("150000"))
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        slip = self.make_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        self.submit_slip(slip["id"])
        other = Account.objects.create(
            company=self.company,
            name="Other TDS",
            root_type=AccountRootType.LIABILITY,
            is_group=False,
        )
        settings_row = HRSettings.objects.get(company=self.company)
        settings_row.tds_payable_account = other
        with self.assertRaises(ValidationError):
            settings_row.save()


class TdsWithoutRatesTests(TdsBase):
    def test_monthly_slip_is_refused_when_no_rates_exist(self):
        emp = self.monthly_employee(D("150000"))
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        before = SalarySlip.objects.count()
        r = self.api.post(
            f"{API}salary-slips/",
            self.slip_payload(emp, d(2026, 4, 1), d(2026, 4, 30)),
            format="json",
        )
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn(b"income-tax rates", r.content)
        self.assertEqual(SalarySlip.objects.count(), before)

    def test_daily_wager_slips_do_not_need_rates(self):
        emp = self.employee("Ramu", pay_type=PayType.DAILY)
        self.rate(emp, daily="500", ot="0")
        self.present(emp, d(2026, 4, 1), d(2026, 4, 30))
        slip = self.make_slip(emp, d(2026, 4, 1), d(2026, 4, 30))
        self.assertEqual(slip["net_pay"], "15000.00")
