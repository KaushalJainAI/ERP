"""Setup health: the pieces a company needs before its documents can post.

``setup_health(company)`` returns one entry per missing piece, each with a
translated sentence and the frontend path that fixes it. An empty list means the
company is fully set up. Read-only: nothing here writes.

Used by ``GET /api/core/setup-health/`` (Admin and Accounts homes) and by
``verify_integrity`` (as warnings, never failures).
"""

from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Account, BankAccount
from buying.models import ExpenseCategory, Supplier
from core.models import FiscalYear

COMPANY_PAGE = "/organisation/company"
BANK_PAGE = "/accounts/bank-accounts"
ACCOUNTS_PAGE = "/accounts/accounts"
SUPPLIER_PAGE = "/buying/suppliers"
EXPENSE_CATEGORY_PAGE = "/buying/expense-categories"
FISCAL_YEAR_PAGE = "/accounts/fiscal-years"


def _item(code, message, fix_path):
    return {"code": code, "message": message, "fix_path": fix_path}


def setup_health(company, today=None):
    """Return the missing setup items for ``company`` (empty list when complete)."""
    today = today or timezone.localdate()
    items = []

    company_checks = (
        (
            "default_receivable_account",
            company.default_receivable_account_id,
            _("Set the default receivable account for the company."),
        ),
        (
            "default_payable_account",
            company.default_payable_account_id,
            _("Set the default payable account for the company."),
        ),
        (
            "default_income_account",
            company.default_income_account_id,
            _("Set the default income account for the company."),
        ),
        (
            "round_off_account",
            company.round_off_account_id,
            _("Set the round-off account for the company."),
        ),
        (
            "unprocessed_warehouse",
            company.unprocessed_warehouse_id,
            _("Set the Unprocessed warehouse for the company."),
        ),
        (
            "in_process_warehouse",
            company.in_process_warehouse_id,
            _("Set the In Process warehouse for the company."),
        ),
        (
            "ready_warehouse",
            company.ready_warehouse_id,
            _("Set the Ready warehouse for the company."),
        ),
        (
            "gstin",
            (company.gstin or "").strip(),
            _("Enter the company GSTIN."),
        ),
    )
    for code, value, message in company_checks:
        if not value:
            items.append(_item(code, message, COMPANY_PAGE))

    if not BankAccount.objects.filter(
        company=company, is_default=True, is_active=True
    ).exists():
        items.append(
            _item(
                "default_bank_account",
                _("Mark one bank account as the default."),
                BANK_PAGE,
            )
        )

    if not Account.objects.filter(
        company=company, account_type="Cash", is_group=False, is_active=True
    ).exists():
        items.append(
            _item(
                "cash_account",
                _("Create a cash account in the chart of accounts."),
                ACCOUNTS_PAGE,
            )
        )

    if not Supplier.objects.filter(
        company=company, is_cash_expense=True, is_active=True
    ).exists():
        items.append(
            _item(
                "cash_expense_supplier",
                _("Add a supplier for cash expenses."),
                SUPPLIER_PAGE,
            )
        )

    if not ExpenseCategory.objects.filter(
        company=company, is_active=True, account__isnull=False
    ).exists():
        items.append(
            _item(
                "expense_category",
                _("Add an expense category with an account."),
                EXPENSE_CATEGORY_PAGE,
            )
        )

    if not FiscalYear.objects.filter(
        start_date__lte=today, end_date__gte=today, is_closed=False
    ).exists():
        items.append(
            _item(
                "fiscal_year",
                _("Open a fiscal year that covers today."),
                FISCAL_YEAR_PAGE,
            )
        )

    return items
