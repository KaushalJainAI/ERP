"""Cash in hand: the balance of the company's Cash-type accounts (read-only).

``GET /api/accounts/cash-in-hand/`` -- see ``docs/API.md``. Sums the non-cancelled
GL entries of every postable Cash account of the active company, so it matches the
Cash rows of the trial balance. Access is RBAC ``can_read`` on ``accounts.GLEntry``.
"""

from decimal import Decimal

from django.db.models import Sum
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from accounts.models import Account, AccountType, GLEntry
from core.company_context import resolve_active_company
from users.permissions import require_model_permission

ZERO = Decimal("0.00")


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("accounts.GLEntry")]
)
def cash_in_hand(request):
    """``{amount}``: debit minus credit over the company's postable Cash accounts."""
    company = resolve_active_company(request)
    total = GLEntry.objects.filter(
        account__in=Account.objects.filter(
            company=company, account_type=AccountType.CASH, is_group=False
        ),
        company=company,
        is_cancelled=False,
    ).aggregate(sum_dr=Sum("debit"), sum_cr=Sum("credit"))
    dr = total["sum_dr"] or ZERO
    cr = total["sum_cr"] or ZERO
    return Response({"amount": str((dr - cr).quantize(Decimal("0.01")))})
