"""Period closing (ERPNext Period Closing Voucher).

At year end every Income and Expense account is zeroed into one Liability / Equity
"closing account head" (Retained Earnings / Reserves & Surplus), so the next year
starts with clean P&L accounts and the profit sits on the balance sheet. The
vouchers posts through ``accounts.services.gl`` like any other document.

The closing entries are *excluded* from the Profit & Loss report (ERPNext does the
same through its ``is_period_closing_voucher_entry`` flag) -- otherwise the closed
year would report a nil profit -- and *included* in the Balance Sheet.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import F, Sum
from django.utils.translation import gettext as _

from accounts.models import (
    Account,
    AccountRootType,
    GLEntry,
    PeriodClosingVoucher,
)
from core.models import FiscalYear

ZERO = Decimal("0.00")

#: ``GLEntry.voucher_type`` of a Period Closing Voucher (``app_label.ObjectName``).
PCV_VOUCHER_TYPE = "accounts.PeriodClosingVoucher"

#: Account names (case-insensitive) an existing chart may already use for the
#: retained earnings / reserves account.
CLOSING_ACCOUNT_NAMES = (
    "retained earnings",
    "reserves & surplus",
    "reserves and surplus",
    "profit & loss a/c",
    "profit & loss account",
)
DEFAULT_CLOSING_ACCOUNT = "Retained Earnings"

_PL_ROOTS = (AccountRootType.INCOME, AccountRootType.EXPENSE)


# ------------------------------------------------------------------ accounts


def ensure_closing_account(company):
    """The company's retained-earnings account, created under its Equity root if
    missing. Returns ``None`` when the chart has no Equity group to hang it from."""
    candidates = Account.objects.filter(
        company=company,
        is_group=False,
        root_type__in=(AccountRootType.EQUITY, AccountRootType.LIABILITY),
    ).order_by("id")
    for account in candidates:
        if account.name.strip().lower() in CLOSING_ACCOUNT_NAMES:
            return account
    groups = Account.objects.filter(
        company=company, is_group=True, root_type=AccountRootType.EQUITY
    ).order_by("id")
    parent = groups.filter(parent__isnull=True).first() or groups.first()
    if parent is None:
        return None
    account, _created = Account.objects.get_or_create(
        company=company,
        name=DEFAULT_CLOSING_ACCOUNT,
        defaults={
            "parent": parent,
            "root_type": AccountRootType.EQUITY,
            "is_group": False,
        },
    )
    return account


# ------------------------------------------------------------------ queries


def closed_through(company_id):
    """Latest ``period_end_date`` of a submitted closing voucher of the company."""
    return (
        PeriodClosingVoucher.objects.filter(company_id=company_id, docstatus=1)
        .order_by("-period_end_date")
        .values_list("period_end_date", flat=True)
        .first()
    )


def assert_period_open(company_id, dates, voucher_type=None):
    """Refuse to post into / reverse within a period a closing voucher has closed.

    A late entry would leave the closed year's Income / Expense accounts non-zero
    again, so the closing would no longer match the books. Cancel the closing
    voucher first (the closing voucher itself is exempt, it *is* the closing).
    """
    if voucher_type == PCV_VOUCHER_TYPE or not dates:
        return
    closed = closed_through(company_id)
    if closed is not None and min(dates) <= closed:
        raise ValidationError(
            _(
                "The books are closed up to %(date)s by a Period Closing Voucher. "
                "Cancel the closing voucher before posting or cancelling entries "
                "dated on or before that day."
            )
            % {"date": closed}
        )


def pl_balances(company_id, start_date, end_date):
    """``{account: net debit - credit}`` of every Income / Expense account with a
    non-zero balance in the range, closing vouchers excluded."""
    rows = (
        GLEntry.objects.filter(
            company_id=company_id,
            is_cancelled=False,
            posting_date__gte=start_date,
            posting_date__lte=end_date,
            account__root_type__in=_PL_ROOTS,
        )
        .exclude(voucher_type=PCV_VOUCHER_TYPE)
        .values("account_id")
        .annotate(dr=Sum("debit"), cr=Sum("credit"))
        .order_by("account_id")
    )
    out = {}
    for row in rows:
        net = (row["dr"] or ZERO) - (row["cr"] or ZERO)
        if net != 0:
            out[row["account_id"]] = net.quantize(Decimal("0.01"))
    return out


# ------------------------------------------------------------------ the voucher


def compute(pcv):
    """The closing plan: GL ``entries``, the ``net_profit`` (negative for a loss)
    and the number of P&L accounts closed."""
    balances = pl_balances(
        pcv.company_id, pcv.fiscal_year.start_date, pcv.period_end_date
    )
    entries = []
    total = ZERO
    for account_id, net in sorted(balances.items()):
        # An account with a debit balance is closed by a credit, and vice versa.
        entries.append(
            {
                "account": account_id,
                "debit": -net if net < 0 else ZERO,
                "credit": net if net > 0 else ZERO,
            }
        )
        total += net
    # ``total`` = expense - income = -profit. The closing account takes the profit.
    entries.append(
        {
            "account": pcv.closing_account_head,
            "debit": total if total > 0 else ZERO,
            "credit": -total if total < 0 else ZERO,
        }
    )
    return {
        "entries": entries,
        "net_profit": -total,
        "accounts_closed": len(balances),
    }


def previous_fiscal_year(fiscal_year):
    last_day = fiscal_year.start_date.fromordinal(
        fiscal_year.start_date.toordinal() - 1
    )
    return (
        FiscalYear.objects.filter(end_date=last_day)
        .order_by("-start_date", "-pk")
        .first()
    )


def validate(pcv):
    """Everything ERPNext checks before a closing voucher may be submitted."""
    fy = pcv.fiscal_year
    account = pcv.closing_account_head
    if account.company_id != pcv.company_id:
        raise ValidationError(_("The closing account must belong to the company."))
    if account.is_group:
        raise ValidationError(_("The closing account cannot be a group."))
    if account.root_type not in (AccountRootType.LIABILITY, AccountRootType.EQUITY):
        raise ValidationError(
            _("Closing Account %(name)s must be of type Liability / Equity.")
            % {"name": account.name}
        )
    if not (fy.start_date < pcv.period_end_date <= fy.end_date):
        raise ValidationError(
            _("The period end date must fall within the fiscal year %(fy)s.")
            % {"fy": fy.name}
        )
    if fy.is_closed:
        raise ValidationError(
            _(
                "Fiscal year %(fy)s is closed. Post the Period Closing Voucher "
                "before closing the fiscal year."
            )
            % {"fy": fy.name}
        )
    # Posting resolves the fiscal year from the date; with overlapping year
    # definitions it must be the one chosen here.
    if FiscalYear.for_date(pcv.period_end_date).pk != fy.pk:
        raise ValidationError(
            _("The period end date belongs to a different fiscal year definition.")
        )
    others = PeriodClosingVoucher.objects.filter(
        company_id=pcv.company_id, docstatus=1
    ).exclude(pk=pcv.pk)
    if others.filter(fiscal_year=fy).exists():
        raise ValidationError(
            _("Fiscal year %(fy)s is already closed by a Period Closing Voucher.")
            % {"fy": fy.name}
        )
    later = others.filter(period_end_date__gt=pcv.period_end_date).first()
    if later is not None:
        raise ValidationError(
            _("You cannot close this period because %(name)s closes a later period.")
            % {"name": later.name}
        )
    _require_previous_year_closed(pcv)
    if compute(pcv)["accounts_closed"] == 0:
        raise ValidationError(
            _("There are no income or expense balances to close in this period.")
        )


def _require_previous_year_closed(pcv):
    """ERPNext: the previous fiscal year must be closed first, unless it has no
    postings at all."""
    previous = previous_fiscal_year(pcv.fiscal_year)
    if previous is None:
        return
    closed = PeriodClosingVoucher.objects.filter(
        company_id=pcv.company_id,
        docstatus=1,
        period_end_date__gte=previous.start_date,
        period_end_date__lte=previous.end_date,
    ).exists()
    if closed:
        return
    has_postings = GLEntry.objects.filter(
        company_id=pcv.company_id,
        is_cancelled=False,
        posting_date__gte=previous.start_date,
        posting_date__lte=previous.end_date,
    ).exists()
    if has_postings:
        raise ValidationError(
            _("The previous fiscal year (%(fy)s) is not closed, please close it first.")
            % {"fy": previous.name}
        )


def validate_cancel(pcv):
    """A closing may only be cancelled while no later closing exists (the later
    one built on this year's retained earnings)."""
    later = (
        PeriodClosingVoucher.objects.filter(
            company_id=pcv.company_id,
            docstatus=1,
            period_end_date__gt=pcv.period_end_date,
        )
        .exclude(pk=pcv.pk)
        .first()
    )
    if later is not None:
        raise ValidationError(
            _(
                "You cannot cancel this document because another Period Closing "
                "Voucher (%(name)s) exists after %(date)s."
            )
            % {"name": later.name, "date": pcv.period_end_date}
        )


# ------------------------------------------------------------------ integrity


def check_integrity():
    """``(errors, warnings)``: a submitted closing voucher's ledger rows still
    match what it closed, and nothing was posted into the closed period since."""
    errors, warnings = [], []
    for pcv in PeriodClosingVoucher.objects.filter(docstatus=1).select_related(
        "fiscal_year", "closing_account_head"
    ):
        rows = GLEntry.objects.filter(
            voucher_type=PCV_VOUCHER_TYPE, voucher_no=pcv.name, is_cancelled=False
        )
        if not rows.exists():
            errors.append(
                f"Period Closing Voucher {pcv.name} is submitted but has no GL entries."
            )
            continue
        head = rows.filter(account=pcv.closing_account_head).aggregate(
            dr=Sum("debit"), cr=Sum("credit")
        )
        posted_profit = (head["cr"] or ZERO) - (head["dr"] or ZERO)
        if posted_profit != pcv.net_profit:
            errors.append(
                f"Period Closing Voucher {pcv.name}: net profit {pcv.net_profit} != "
                f"closing account GL {posted_profit}."
            )
        if (
            rows.exclude(account__root_type__in=_PL_ROOTS)
            .exclude(account=pcv.closing_account_head)
            .exists()
        ):
            errors.append(
                f"Period Closing Voucher {pcv.name} posted to an account that is "
                "neither Income / Expense nor its closing account."
            )
        # Every P&L account of the period must net to zero including the closing
        # voucher. A residue means an entry was posted after the close.
        residue = (
            GLEntry.objects.filter(
                company_id=pcv.company_id,
                is_cancelled=False,
                posting_date__gte=pcv.fiscal_year.start_date,
                posting_date__lte=pcv.period_end_date,
                account__root_type__in=_PL_ROOTS,
            )
            .values("account__name")
            .annotate(dr=Sum("debit"), cr=Sum("credit"))
            .exclude(dr=F("cr"))
        )
        for row in residue:
            warnings.append(
                f"Period Closing Voucher {pcv.name}: '{row['account__name']}' has a "
                f"balance of {(row['dr'] or ZERO) - (row['cr'] or ZERO)} after the "
                "close (posted after the closing voucher?)."
            )
    return errors, warnings
