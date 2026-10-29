"""GL-derived balances for employees (wages payable, advance control account)."""

from decimal import Decimal

from django.db.models import Sum

from accounts.models import GLEntry
from accounts.services.gl import quantize_money
from hr.services.setup import get_settings

ZERO = Decimal("0.00")


def _net(account_id, company_id, employee_name):
    agg = GLEntry.objects.filter(
        company_id=company_id,
        account_id=account_id,
        party_type="Employee",
        party=employee_name,
        is_cancelled=False,
    ).aggregate(dr=Sum("debit"), cr=Sum("credit"))
    return quantize_money(agg["dr"] or ZERO), quantize_money(agg["cr"] or ZERO)


def payable_balance(employee, company=None):
    """What the company owes ``employee`` in wages: credit less debit on the
    payroll-payable account for that employee party (live rows only)."""
    company = company or employee.company
    account = get_settings(company).payroll_payable_account
    dr, cr = _net(account.pk, company.pk, employee.name)
    return cr - dr


def advance_ledger_balance(employee, company=None):
    """Debit less credit on the employee-advance account for the employee (what
    the ledger says the employee still owes)."""
    company = company or employee.company
    account = get_settings(company).employee_advance_account
    dr, cr = _net(account.pk, company.pk, employee.name)
    return dr - cr
