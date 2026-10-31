"""TDS on salaried employees' salary slips.

* Only Monthly employees. Daily wagers never get salary TDS.
* The monthly amount is the employee's fixed ``tds_per_month`` when the
  accountant set one (0 means none).  Otherwise it is worked out from the
  income-tax rates of the employee's regime, on the slip's posting date.
* Deducted once per calendar month (by posting date): a slip gets the TDS row
  only when no other submitted slip of that employee in that month has one.
* Capped by the pay left before advance recovery, so net pay stays zero or more.
* Checked again at submit: the stored amount must still match the rule, and a
  slip with TDS needs a PAN and a TDS payable account.

Nothing here is a tax rate; the rates are in ``hr.services.tax_rates``.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils.translation import gettext as _

from accounts.services.gl import quantize_money
from core.models import DocStatus
from hr.models import (
    ComponentType,
    PayType,
    SalaryComponent,
    SalarySlipDetail,
    SystemComponent,
)
from hr.services.tax_rates import monthly_tds_from_salary, rate_set_for
from hr.services.wages import wage_rate_for

ZERO = Decimal("0.00")
TDS_IDX = 800


def tds_component(company):
    """The company's TDS salary component (clear error when setup has not run)."""
    component = SalaryComponent.objects.filter(
        company=company, system_key=SystemComponent.TDS
    ).first()
    if component is None:
        raise ValidationError(
            _(
                "The TDS salary component is missing for company %(company)s. "
                "Run seed_defaults or POST hr/settings/ensure-setup/ first."
            )
            % {"company": company}
        )
    return component


def monthly_tds(employee, on_date):
    """One month's TDS for ``employee`` from the rules in force on ``on_date``."""
    if employee.pay_type != PayType.MONTHLY:
        return ZERO
    if employee.tds_per_month is not None:
        return quantize_money(employee.tds_per_month)
    rate = wage_rate_for(employee, on_date)
    if rate is None or rate.monthly_salary is None:
        raise ValidationError(
            _(
                "%(employee)s has no monthly salary effective on %(date)s, so TDS "
                "cannot be worked out."
            )
            % {"employee": employee.employee_name, "date": on_date}
        )
    return monthly_tds_from_salary(
        rate.monthly_salary, rate_set_for(employee.tax_regime, on_date)
    )


def pay_before_tds(slip):
    """Earnings less the manual deductions.  Generated deductions (TDS and advance
    recovery) are worked out from this, so the rule can be checked at submit."""
    earnings = ZERO
    manual_deductions = ZERO
    for row in slip.details.select_related("component"):
        if row.component.component_type == ComponentType.EARNING:
            earnings += row.amount
        elif row.is_manual:
            manual_deductions += row.amount
    return earnings - manual_deductions


def _month_booked_elsewhere(slip):
    """True when another submitted slip of this employee, posted in the same
    calendar month, already carries a TDS row."""
    return (
        SalarySlipDetail.objects.filter(
            slip__employee_id=slip.employee_id,
            slip__docstatus=DocStatus.SUBMITTED,
            slip__posting_date__year=slip.posting_date.year,
            slip__posting_date__month=slip.posting_date.month,
            component__system_key=SystemComponent.TDS,
        )
        .exclude(slip_id=slip.pk)
        .exists()
    )


def expected_tds(slip, available):
    """The TDS this slip must carry: the monthly amount, capped by ``available``
    pay, and zero when the month is already deducted elsewhere."""
    if _month_booked_elsewhere(slip):
        return ZERO
    monthly = monthly_tds(slip.employee, slip.posting_date)
    if monthly <= 0:
        return ZERO
    return min(monthly, max(available, ZERO))


def propose_tds(slip):
    """Add the TDS row to a draft slip when one is due (called before advance
    recovery, so the statutory deduction comes first)."""
    amount = expected_tds(slip, pay_before_tds(slip))
    if amount <= 0:
        return
    SalarySlipDetail.objects.create(
        slip=slip,
        idx=TDS_IDX,
        component=tds_component(slip.company),
        amount=amount,
        is_manual=False,
    )


def refresh_tds(slip):
    """Rebuild a draft slip's TDS row from its current pay.  Called on every draft
    save, so the row survives a save and tracks any change to pay."""
    slip.details.filter(component__system_key=SystemComponent.TDS).delete()
    propose_tds(slip)


def check_tds(slip, employee, settings_row):
    """Submit-time check (inside ``validate_slip``, with the employee locked)."""
    stored = (
        slip.details.filter(component__system_key=SystemComponent.TDS).aggregate(
            t=Sum("amount")
        )["t"]
        or ZERO
    )
    expected = expected_tds(slip, pay_before_tds(slip))
    if stored != expected:
        raise ValidationError(
            _("TDS for this slip is out of date; recompute it before submitting.")
        )
    if expected > 0:
        if not employee.pan.strip():
            raise ValidationError(
                _("%(employee)s needs a PAN before TDS can be deducted.")
                % {"employee": employee.employee_name}
            )
        if settings_row.tds_payable_account_id is None:
            raise ValidationError(
                _("Set the TDS payable account in HR Settings before deducting TDS.")
            )
