"""One-time HR setup per company: accounts, HR Settings and system components."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from accounts.models import Account, AccountRootType, AccountType
from hr.models import (
    ComponentType,
    HRSettings,
    SalaryComponent,
    SystemComponent,
)

WAGE_EXPENSE_NAME = "Wages Expense"
PAYROLL_PAYABLE_NAME = "Wages Payable"
EMPLOYEE_ADVANCE_NAME = "Employee Advances"
CASH_NAME = "Cash Account"
TDS_PAYABLE_NAME = "TDS Payable"
EXPENSE_CLAIM_PAYABLE_NAME = "Employee Reimbursements Payable"

SYSTEM_COMPONENTS = (
    (SystemComponent.WAGES, "Wages", ComponentType.EARNING),
    (SystemComponent.OVERTIME, "Overtime", ComponentType.EARNING),
    (SystemComponent.ADVANCE_RECOVERY, "Advance Recovery", ComponentType.DEDUCTION),
    (SystemComponent.TDS, "TDS", ComponentType.DEDUCTION),
)

#: Components a company must have before ordinary payroll runs.  TDS is not
#: among them: a company set up before TDS existed keeps paying wages, and a
#: slip that needs TDS asks for setup (``hr.services.tds.tds_component``).
REQUIRED_SYSTEM_KEYS = (
    SystemComponent.WAGES,
    SystemComponent.OVERTIME,
    SystemComponent.ADVANCE_RECOVERY,
)


def _root_group(company, root_type):
    """The top-level group of the chart for ``root_type`` (``None`` if the chart
    has no group of that kind -- the account is then created at the top level)."""
    groups = Account.objects.filter(company=company, root_type=root_type, is_group=True)
    return (
        groups.filter(parent__isnull=True).order_by("pk").first()
        or groups.order_by("pk").first()
    )


def _ensure_account(company, name, root_type, account_type=None):
    existing = Account.objects.filter(company=company, name=name).first()
    if existing is not None:
        return existing
    return Account.objects.create(
        company=company,
        name=name,
        parent=_root_group(company, root_type),
        root_type=root_type,
        account_type=account_type,
        is_group=False,
    )


@transaction.atomic
def ensure_hr_setup(company):
    """Idempotently create the HR accounts, ``HRSettings`` and system components.

    Existing settings are never overwritten (an admin may have chosen other
    accounts); missing pieces are filled in.  Returns the ``HRSettings``.
    """
    settings_row = HRSettings.objects.filter(company=company).first()
    if settings_row is None:
        cash = (
            Account.objects.filter(
                company=company, account_type=AccountType.CASH, is_group=False
            )
            .order_by("pk")
            .first()
        ) or _ensure_account(
            company, CASH_NAME, AccountRootType.ASSET, AccountType.CASH
        )
        settings_row = HRSettings.objects.create(
            company=company,
            wage_expense_account=_ensure_account(
                company, WAGE_EXPENSE_NAME, AccountRootType.EXPENSE
            ),
            payroll_payable_account=_ensure_account(
                company, PAYROLL_PAYABLE_NAME, AccountRootType.LIABILITY
            ),
            employee_advance_account=_ensure_account(
                company, EMPLOYEE_ADVANCE_NAME, AccountRootType.ASSET
            ),
            expense_claim_payable_account=_ensure_account(
                company, EXPENSE_CLAIM_PAYABLE_NAME, AccountRootType.LIABILITY
            ),
            default_payment_account=cash,
        )

    if settings_row.tds_payable_account_id is None:
        settings_row.tds_payable_account = _ensure_account(
            company, TDS_PAYABLE_NAME, AccountRootType.LIABILITY
        )
        settings_row.save()

    account_for = {
        SystemComponent.WAGES: settings_row.wage_expense_account,
        SystemComponent.OVERTIME: settings_row.wage_expense_account,
        SystemComponent.ADVANCE_RECOVERY: settings_row.employee_advance_account,
        SystemComponent.TDS: settings_row.tds_payable_account,
    }
    for key, name, component_type in SYSTEM_COMPONENTS:
        if SalaryComponent.objects.filter(company=company, system_key=key).exists():
            continue
        # A user component already called "Wages" would collide on the name.
        SalaryComponent.objects.update_or_create(
            company=company,
            name=name,
            defaults={
                "component_type": component_type,
                "account": account_for[key],
                "is_system": True,
                "system_key": key,
            },
        )
    return settings_row


def get_settings(company):
    """The company's ``HRSettings`` or a clear error (never silently created)."""
    row = (
        HRSettings.objects.filter(company=company)
        .select_related(
            "wage_expense_account",
            "payroll_payable_account",
            "employee_advance_account",
            "expense_claim_payable_account",
            "default_payment_account",
        )
        .first()
    )
    if row is None:
        raise ValidationError(
            _(
                "HR Settings are not configured for company %(company)s. Run "
                "seed_defaults or POST hr/settings/ensure-setup/ first."
            )
            % {"company": company}
        )
    return row


def system_components(company):
    """``{system_key: SalaryComponent}`` for the company (clear error if missing)."""
    found = {
        c.system_key: c
        for c in SalaryComponent.objects.filter(
            company=company, system_key__isnull=False
        ).select_related("account")
    }
    missing = [k for k in REQUIRED_SYSTEM_KEYS if k not in found]
    if missing:
        raise ValidationError(
            _(
                "System salary components are missing for company %(company)s. "
                "Run seed_defaults or POST hr/settings/ensure-setup/ first."
            )
            % {"company": company}
        )
    return found
