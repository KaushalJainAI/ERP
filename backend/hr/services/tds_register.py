"""TDS register for one financial-year quarter (T-7).

Per employee: PAN, gross pay and TDS deducted, from the submitted salary slips
posted in the quarter.  Deposits (submitted TDS Tax Payments) are shown by month,
because a challan covers many employees and cannot be split between them.  The
accountant uses this for the quarterly return and the salary certificates; the
ERP does not file them.

The register total is checked against the ledger: the net credit to the TDS
payable account from salary-slip postings in the same quarter must equal the TDS
deducted in the register.  ``ledger.matches`` says whether they agree.
"""

import datetime
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils.translation import gettext as _

from accounts.models import GLEntry, TaxPayment, TaxType
from accounts.services.gl import quantize_money
from core.models import DocStatus
from hr.models import HRSettings, SalarySlip, SystemComponent

ZERO = Decimal("0.00")
QUARTER_RE = re.compile(r"^(\d{4})-(\d{2})-Q([1-4])$")


def quarter_bounds(label):
    """``(first day, last day)`` of a quarter label such as ``2026-27-Q1``.

    Q1 is April to June, Q2 July to September, Q3 October to December and Q4
    January to March of the following year.
    """
    match = QUARTER_RE.match(label or "")
    if match:
        start_year, end_two_digits, quarter = (
            int(match[1]),
            int(match[2]),
            int(match[3]),
        )
    if not match or end_two_digits != (start_year + 1) % 100:
        raise ValidationError(
            _("Use the financial year and quarter, for example 2026-27-Q1.")
        )
    if quarter == 1:
        return datetime.date(start_year, 4, 1), datetime.date(start_year, 6, 30)
    if quarter == 2:
        return datetime.date(start_year, 7, 1), datetime.date(start_year, 9, 30)
    if quarter == 3:
        return datetime.date(start_year, 10, 1), datetime.date(start_year, 12, 31)
    return datetime.date(start_year + 1, 1, 1), datetime.date(start_year + 1, 3, 31)


def _months_in(start, end):
    """The first day of each calendar month from ``start`` to ``end``."""
    months = []
    cursor = start.replace(day=1)
    while cursor <= end:
        months.append(cursor)
        cursor = (cursor.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return months


def tds_register(company_id, label):
    start, end = quarter_bounds(label)

    slips = (
        SalarySlip.objects.filter(
            company_id=company_id,
            docstatus=DocStatus.SUBMITTED,
            posting_date__gte=start,
            posting_date__lte=end,
        )
        .select_related("employee")
        .prefetch_related("details__component")
        .order_by("employee__name", "posting_date", "pk")
    )
    by_employee = {}
    for slip in slips:
        row = by_employee.setdefault(
            slip.employee_id,
            {
                "employee": slip.employee.name,
                "employee_name": slip.employee.employee_name,
                "pan": slip.employee.pan or "",
                "slips": 0,
                "pay": ZERO,
                "tds_deducted": ZERO,
            },
        )
        row["slips"] += 1
        row["pay"] += slip.gross_pay
        row["tds_deducted"] += sum(
            (
                detail.amount
                for detail in slip.details.all()
                if detail.component.system_key == SystemComponent.TDS
            ),
            ZERO,
        )
    rows = [
        {
            **row,
            "pay": str(quantize_money(row["pay"])),
            "tds_deducted": str(quantize_money(row["tds_deducted"])),
        }
        for row in by_employee.values()
    ]
    total_pay = sum((row["pay"] for row in by_employee.values()), ZERO)
    total_tds = sum((row["tds_deducted"] for row in by_employee.values()), ZERO)

    # Deposits: submitted TDS Tax Payments whose period lies in the quarter.
    months = _months_in(start, end)
    deposited = {month: ZERO for month in months}
    challans = []
    payments = TaxPayment.objects.filter(
        company_id=company_id,
        tax_type=TaxType.TDS,
        docstatus=DocStatus.SUBMITTED,
        period__gte=start,
        period__lte=end,
    ).order_by("period", "pk")
    for payment in payments:
        month = payment.period.replace(day=1)
        deposited[month] = deposited.get(month, ZERO) + payment.tds_amount
        challans.append(
            {
                "challan_no": payment.challan_no,
                "period": payment.period.strftime("%Y-%m"),
                "posting_date": str(payment.posting_date),
                "amount": str(quantize_money(payment.tds_amount)),
            }
        )
    total_deposited = sum(deposited.values(), ZERO)

    # Ledger: net credit to the TDS payable account from salary-slip postings.
    settings_row = (
        HRSettings.objects.filter(company_id=company_id)
        .select_related("tds_payable_account")
        .first()
    )
    account = settings_row.tds_payable_account if settings_row else None
    movement = ZERO
    if account is not None:
        sums = GLEntry.objects.filter(
            company_id=company_id,
            account=account,
            voucher_type="hr.SalarySlip",
            is_cancelled=False,
            posting_date__gte=start,
            posting_date__lte=end,
        ).aggregate(credit=Sum("credit"), debit=Sum("debit"))
        movement = (sums["credit"] or ZERO) - (sums["debit"] or ZERO)

    return {
        "quarter": label,
        "from_date": str(start),
        "to_date": str(end),
        "rows": rows,
        "total_pay": str(quantize_money(total_pay)),
        "total_tds_deducted": str(quantize_money(total_tds)),
        "deposits_by_month": [
            {
                "period": month.strftime("%Y-%m"),
                "amount": str(quantize_money(deposited[month])),
            }
            for month in months
        ],
        "total_deposited": str(quantize_money(total_deposited)),
        "challans": challans,
        "ledger": {
            "account": account.name if account is not None else "",
            "movement": str(quantize_money(movement)),
            "matches": quantize_money(movement) == quantize_money(total_tds),
        },
    }
