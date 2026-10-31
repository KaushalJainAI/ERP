"""Income-tax rates: seed data, lookup by date, and the tax calculation.

The rules live in the ``IncomeTaxRateSet`` tables, which the accountant edits.
Nothing here is a rate.  ``SEED_RATE_SETS`` is only the first set of rows the
seed path writes, and it never overwrites a row that already exists.
"""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from accounts.services.gl import quantize_money
from hr.models import IncomeTaxRateSet, IncomeTaxSlab, IncomeTaxSurcharge, TaxRegime

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
TWELVE = Decimal("12")

# TO BE CONFIRMED BY THE ACCOUNTANT (checklist T-8) BEFORE GO-LIVE.
# The slabs, standard deduction and rebate limit are the FY 2026-27 new-regime
# figures the sources agree on.  The cess and the surcharge bands are NOT in
# those sources: they are entered from general knowledge and must be checked.
# Marginal relief on a surcharge threshold is not modelled, so a salary just
# above a surcharge threshold is taxed slightly high.
SEED_RATE_SETS = (
    {
        "regime": TaxRegime.NEW,
        "effective_from": date(2026, 4, 1),
        "label": "FY 2026-27 new regime",
        "standard_deduction": Decimal("75000"),
        "rebate_income_limit": Decimal("1200000"),
        "rebate_amount": Decimal("60000"),
        "cess_percent": Decimal("4"),
        "notes": (
            "Seeded. Slabs, standard deduction and rebate limit are the figures the "
            "sources agree on. Cess and surcharge bands are not in those sources. "
            "Accountant to confirm before go-live (checklist T-8)."
        ),
        # (income above, income up to or None, rate %)
        "slabs": (
            (Decimal("0"), Decimal("400000"), Decimal("0")),
            (Decimal("400000"), Decimal("800000"), Decimal("5")),
            (Decimal("800000"), Decimal("1200000"), Decimal("10")),
            (Decimal("1200000"), Decimal("1600000"), Decimal("15")),
            (Decimal("1600000"), Decimal("2000000"), Decimal("20")),
            (Decimal("2000000"), Decimal("2400000"), Decimal("25")),
            (Decimal("2400000"), None, Decimal("30")),
        ),
        # (taxable income above, surcharge %)
        "surcharges": (
            (Decimal("5000000"), Decimal("10")),
            (Decimal("10000000"), Decimal("15")),
            (Decimal("20000000"), Decimal("25")),
        ),
    },
)


def seed_income_tax_rates():
    """Create the seeded rate sets that are missing.  Returns how many were created.

    A set that already exists (same regime and date) is left as it is, so an
    accountant's edits survive a re-seed.
    """
    created = 0
    with transaction.atomic():
        for spec in SEED_RATE_SETS:
            rate_set, made = IncomeTaxRateSet.objects.get_or_create(
                regime=spec["regime"],
                effective_from=spec["effective_from"],
                defaults={
                    "label": spec["label"],
                    "standard_deduction": spec["standard_deduction"],
                    "rebate_income_limit": spec["rebate_income_limit"],
                    "rebate_amount": spec["rebate_amount"],
                    "cess_percent": spec["cess_percent"],
                    "notes": spec["notes"],
                },
            )
            if not made:
                continue
            for lower, upper, rate in spec["slabs"]:
                IncomeTaxSlab.objects.create(
                    rate_set=rate_set,
                    lower_limit=lower,
                    upper_limit=upper,
                    rate_percent=rate,
                )
            for above, rate in spec["surcharges"]:
                IncomeTaxSurcharge.objects.create(
                    rate_set=rate_set, above_income=above, rate_percent=rate
                )
            created += 1
    return created


def rate_set_for(regime, on_date):
    """The rate set of ``regime`` in force on ``on_date``: the latest one whose
    ``effective_from`` is on or before that date.  Refuses when there is none."""
    rate_set = (
        IncomeTaxRateSet.objects.filter(regime=regime, effective_from__lte=on_date)
        .order_by("-effective_from")
        .first()
    )
    if rate_set is None:
        raise ValidationError(
            _(
                "No %(regime)s income-tax rates are effective on %(date)s. "
                "The accountant must enter them first."
            )
            % {"regime": TaxRegime(regime).label, "date": on_date}
        )
    return rate_set


def check_slab_rows(rows):
    """Raise unless the ``(lower, upper)`` bands start at zero and join with no gap
    or overlap, and only the last band has no upper limit."""
    if not rows:
        raise ValidationError(_("A rate set needs at least one income slab."))
    expected_lower = ZERO
    ordered = sorted(rows, key=lambda row: row[0])
    for index, (lower, upper) in enumerate(ordered):
        last = index == len(ordered) - 1
        if lower != expected_lower:
            raise ValidationError(
                _("Income slabs must start at 0 and join with no gap or overlap.")
            )
        if upper is None:
            if not last:
                raise ValidationError(
                    _("Only the last income slab may have no upper limit.")
                )
        else:
            if last:
                raise ValidationError(
                    _("The last income slab must have no upper limit.")
                )
            expected_lower = upper


def annual_tax(taxable, rate_set):
    """Tax for a year on ``taxable`` income under ``rate_set``.

    Order: slab tax; rebate (up to the limit) or marginal relief (above it);
    surcharge on the tax; health and education cess on tax plus surcharge.
    Each step is rounded to paise.  Income is taken as salary only.
    """
    taxable = quantize_money(taxable)
    if taxable <= ZERO:
        return ZERO

    slab_tax = ZERO
    for slab in rate_set.slabs.all():  # ascending by lower limit
        if taxable <= slab.lower_limit:
            break
        top = taxable if slab.upper_limit is None else min(taxable, slab.upper_limit)
        slab_tax += (top - slab.lower_limit) * slab.rate_percent / HUNDRED
    tax = quantize_money(slab_tax)

    if taxable <= rate_set.rebate_income_limit:
        tax = max(ZERO, tax - rate_set.rebate_amount)
    else:
        # Marginal relief: the tax above the limit cannot exceed the income above it.
        tax = min(tax, taxable - rate_set.rebate_income_limit)

    surcharge_rate = ZERO
    for band in rate_set.surcharges.all():  # ascending; the highest band crossed wins
        if taxable > band.above_income:
            surcharge_rate = band.rate_percent
    surcharge = quantize_money(tax * surcharge_rate / HUNDRED)
    cess = quantize_money((tax + surcharge) * rate_set.cess_percent / HUNDRED)
    return tax + surcharge + cess


def monthly_tds_from_salary(monthly_salary, rate_set):
    """One month's TDS for a salary of ``monthly_salary``: the annual tax on
    12 x salary less the standard deduction, divided by 12."""
    annual_gross = Decimal(monthly_salary) * TWELVE
    taxable = max(ZERO, annual_gross - rate_set.standard_deduction)
    return quantize_money(annual_tax(taxable, rate_set) / TWELVE)
