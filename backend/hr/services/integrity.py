"""HR integrity checks for ``verify_integrity``.

Everything HR stores that is derivable is checked against its source:

* a slip's header totals equal the sum of its detail rows;
* every submitted slip / advance / payment has live, balanced GL entries that
  match its amounts, and a cancelled one has none left live;
* an advance is never over-recovered, and per employee the sum of the advances'
  outstanding equals the ledger balance of the employee-advance account;
* no two submitted slips of one employee overlap.

Nothing is stored that needs a repost beyond draft slip totals (see
``repost_hr``).
"""

from decimal import Decimal

from django.db.models import Sum

from accounts.models import GLEntry
from core.models import DocStatus
from hr.models import (
    ComponentType,
    EmployeeAdvance,
    EmployeePayment,
    HRSettings,
    SalarySlip,
)
from hr.services import advances as advance_service
from hr.services.balances import advance_ledger_balance, payable_balance

ZERO = Decimal("0.00")


def _live(voucher_type, voucher_no):
    return GLEntry.objects.filter(
        voucher_type=voucher_type, voucher_no=voucher_no, is_cancelled=False
    )


def _sum(qs, field):
    return qs.aggregate(t=Sum(field))["t"] or ZERO


def slip_totals(slip):
    """``(gross, deduction)`` recomputed from the detail rows."""
    gross = deduction = ZERO
    for row in slip.details.select_related("component"):
        if row.component.component_type == ComponentType.EARNING:
            gross += row.amount
        else:
            deduction += row.amount
    return gross, deduction


def check():
    """Return ``(errors, warnings)`` for HR invariants."""
    errors = []
    warnings = []

    settings_by_company = {s.company_id: s for s in HRSettings.objects.all()}

    # 1. Slip totals vs detail rows, and the GL of posted slips.
    for slip in SalarySlip.objects.select_related("employee"):
        gross, deduction = slip_totals(slip)
        if (
            slip.gross_pay != gross
            or slip.total_deduction != deduction
            or slip.net_pay != gross - deduction
        ):
            errors.append(
                f"Salary slip {slip.name}: stored totals "
                f"({slip.gross_pay}, {slip.total_deduction}, {slip.net_pay}) do not "
                f"match its rows ({gross}, {deduction}, {gross - deduction}); drafts: run "
                f"repost_hr; posted slips: cancel and amend."
            )
        live = _live("hr.SalarySlip", slip.name)
        if slip.docstatus != DocStatus.SUBMITTED:
            if live.exists():
                errors.append(
                    f"Salary slip {slip.name} is not submitted but has live GL entries."
                )
            continue
        cfg = settings_by_company.get(slip.company_id)
        if cfg is None:
            errors.append(f"Salary slip {slip.name}: company has no HR Settings.")
            continue
        debit = _sum(live, "debit")
        credit = _sum(live, "credit")
        if debit != slip.gross_pay or credit != slip.gross_pay:
            errors.append(
                f"Salary slip {slip.name}: GL debit {debit} / credit {credit} do "
                f"not equal gross pay {slip.gross_pay}."
            )
        payable = _sum(
            live.filter(
                account_id=cfg.payroll_payable_account_id,
                party_type="Employee",
                party=slip.employee.name,
            ),
            "credit",
        )
        if payable != slip.net_pay:
            errors.append(
                f"Salary slip {slip.name}: wages payable credit {payable} does not "
                f"equal net pay {slip.net_pay}."
            )
        recovery_rows = _sum(slip.details.filter(advance__isnull=False), "amount")
        recovery_gl = _sum(
            live.filter(
                account_id=cfg.employee_advance_account_id,
                party_type="Employee",
                party=slip.employee.name,
            ),
            "credit",
        )
        if recovery_rows != recovery_gl:
            errors.append(
                f"Salary slip {slip.name}: advance recovery rows {recovery_rows} "
                f"do not match the advance account credit {recovery_gl}."
            )

    # 2. No overlapping submitted slips per employee.
    last = None
    for slip in SalarySlip.objects.filter(docstatus=DocStatus.SUBMITTED).order_by(
        "employee_id", "start_date", "end_date"
    ):
        if (
            last is not None
            and last.employee_id == slip.employee_id
            and slip.start_date <= last.end_date
        ):
            errors.append(
                f"Submitted salary slips {last.name} and {slip.name} overlap for "
                f"employee {slip.employee_id}."
            )
        if (
            last is None
            or last.employee_id != slip.employee_id
            or (slip.end_date > last.end_date)
        ):
            last = slip

    # 3. Advances: GL of each, never over-recovered, ledger balance agrees.
    expected_outstanding = {}
    advance_employees = {}
    for advance in EmployeeAdvance.objects.select_related("employee"):
        live = _live("hr.EmployeeAdvance", advance.name)
        if advance.docstatus != DocStatus.SUBMITTED:
            if live.exists():
                errors.append(
                    f"Employee advance {advance.name} is not submitted but has live "
                    f"GL entries."
                )
            continue
        cfg = settings_by_company.get(advance.company_id)
        if cfg is None:
            errors.append(
                f"Employee advance {advance.name}: company has no HR Settings."
            )
            continue
        debit = _sum(
            live.filter(
                account_id=cfg.employee_advance_account_id,
                party_type="Employee",
                party=advance.employee.name,
            ),
            "debit",
        )
        credit = _sum(live.filter(account_id=advance.paid_from_id), "credit")
        if debit != advance.amount or credit != advance.amount:
            errors.append(
                f"Employee advance {advance.name}: GL debit {debit} / credit "
                f"{credit} do not equal the amount {advance.amount}."
            )
        left = advance_service.outstanding(advance)
        if left < 0:
            errors.append(
                f"Employee advance {advance.name} is over-recovered "
                f"(outstanding {left})."
            )
        key = (advance.company_id, advance.employee_id)
        advance_employees[key] = advance.employee
        expected_outstanding[key] = expected_outstanding.get(key, ZERO) + left

    for key, left in expected_outstanding.items():
        employee = advance_employees[key]
        ledger = advance_ledger_balance(employee, employee.company)
        if ledger != left:
            errors.append(
                f"Employee {employee.name}: advance outstanding {left} does not "
                f"match the advance account ledger balance {ledger}."
            )

    # 4. Payments.
    for payment in EmployeePayment.objects.select_related("employee"):
        live = _live("hr.EmployeePayment", payment.name)
        if payment.docstatus != DocStatus.SUBMITTED:
            if live.exists():
                errors.append(
                    f"Employee payment {payment.name} is not submitted but has live "
                    f"GL entries."
                )
            continue
        cfg = settings_by_company.get(payment.company_id)
        if cfg is None:
            errors.append(
                f"Employee payment {payment.name}: company has no HR Settings."
            )
            continue
        debit = _sum(
            live.filter(
                account_id=cfg.payroll_payable_account_id,
                party_type="Employee",
                party=payment.employee.name,
            ),
            "debit",
        )
        credit = _sum(live.filter(account_id=payment.paid_from_id), "credit")
        if debit != payment.amount or credit != payment.amount:
            errors.append(
                f"Employee payment {payment.name}: GL debit {debit} / credit "
                f"{credit} do not equal the amount {payment.amount}."
            )

    # 5. Overpaid employees (negative wages payable) deserve a look, not a failure.
    seen = set()
    for model in (SalarySlip, EmployeePayment):
        for row in model.objects.filter(docstatus=DocStatus.SUBMITTED).select_related(
            "employee", "company"
        ):
            key = (row.company_id, row.employee_id)
            if key in seen or row.company_id not in settings_by_company:
                continue
            seen.add(key)
            balance = payable_balance(row.employee, row.company)
            if balance < 0:
                warnings.append(
                    f"Employee {row.employee.name} has been overpaid "
                    f"(wages payable {balance})."
                )

    return errors, warnings
