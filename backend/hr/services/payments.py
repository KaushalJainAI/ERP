"""Wage payments: Dr wages payable (party Employee) / Cr cash or bank."""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from accounts.services import gl as gl_service
from hr.models import Employee
from hr.services.advances import validate_paid_from
from hr.services.balances import payable_balance
from hr.services.setup import get_settings


def validate_payment(payment):
    employee = Employee.objects.select_for_update().get(pk=payment.employee_id)
    if employee.company_id != payment.company_id:
        raise ValidationError(_("The employee belongs to a different company."))
    validate_paid_from(payment.paid_from, payment.company)
    balance = payable_balance(employee, payment.company)
    if payment.amount > balance:
        raise ValidationError(
            _(
                "Payment of %(amount)s exceeds the wages payable to %(employee)s "
                "(%(balance)s)."
            )
            % {
                "amount": payment.amount,
                "employee": employee.employee_name,
                "balance": balance,
            }
        )


def post_payment(payment):
    settings_row = get_settings(payment.company)
    gl_service.post(
        payment,
        [
            {
                "account": settings_row.payroll_payable_account,
                "debit": payment.amount,
                "party_type": "Employee",
                "party": payment.employee.name,
            },
            {"account": payment.paid_from, "credit": payment.amount},
        ],
        posting_date=payment.posting_date,
        remarks=payment.remarks or payment.reference_no or None,
    )
