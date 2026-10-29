"""HTTP endpoints for the registers and the party statement (read-only).

``GET /api/accounts/reports/{sales-register,purchase-register,day-book,cash-book,
party-statement}/`` -- see ``accounts/reports/`` and ``docs/reports.md``. Access is
RBAC ``can_read`` on the model each report serves; a company is always required.
"""

from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from accounts.reports import (
    _int_param,
    cashbook,
    daybook,
    registers,
    resolve_page,
    resolve_period,
    statement,
)
from core.company_context import resolve_active_company
from users.permissions import require_model_permission, user_has_model_permission


def _register(request, kind):
    params = request.query_params
    company = resolve_active_company(request)
    from_date, to_date = resolve_period(params)
    page, size = resolve_page(params)
    return Response(
        registers.build(
            kind,
            company,
            from_date,
            to_date,
            _int_param(params, "party"),
            _int_param(params, "item_group"),
            page,
            size,
        )
    )


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("selling.SalesInvoice")]
)
def sales_register(request):
    """Sales Register: one row per submitted Sales Invoice (``company``, ``from_date``,
    ``to_date`` required; ``party`` = customer id, ``item_group``, ``page``,
    ``page_size``)."""
    return _register(request, "sales")


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("buying.PurchaseInvoice")]
)
def purchase_register(request):
    """Purchase Register: as the Sales Register, for Purchase Invoices (``party`` =
    supplier id)."""
    return _register(request, "purchase")


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("accounts.GLEntry")]
)
def day_book(request):
    """Day Book: live GL vouchers in the period (``voucher_type`` optional)."""
    params = request.query_params
    company = resolve_active_company(request)
    from_date, to_date = resolve_period(params)
    page, size = resolve_page(params)
    return Response(
        daybook.build(
            company,
            from_date,
            to_date,
            (params.get("voucher_type") or "").strip() or None,
            page,
            size,
        )
    )


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("accounts.GLEntry")]
)
def cash_book(request):
    """Cash / Bank Book of one Cash or Bank ``account`` (id)."""
    params = request.query_params
    company = resolve_active_company(request)
    account = cashbook.resolve_account(params, company)
    from_date, to_date = resolve_period(params)
    page, size = resolve_page(params)
    return Response(cashbook.build(company, account, from_date, to_date, page, size))


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("accounts.GLEntry")]
)
def party_statement(request):
    """Statement of Account of a ``party_type`` (Customer | Supplier) ``party`` (id).

    Also needs read access to the party master (the statement exposes its identity).
    """
    params = request.query_params
    party_type = params.get("party_type")
    if party_type in statement.PARTY_TYPES and not user_has_model_permission(
        request.user, statement.model_label(party_type), "can_read"
    ):
        raise PermissionDenied()
    company = resolve_active_company(request)
    party_type, party = statement.resolve_party(params, company)
    from_date, to_date = resolve_period(params)
    page, size = resolve_page(params)
    return Response(
        statement.build(company, party_type, party, from_date, to_date, page, size)
    )
