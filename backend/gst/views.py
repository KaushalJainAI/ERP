from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from django.utils.translation import gettext as _

from core.company_context import resolve_active_company
from gst.validators import check_gstin


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def validate_gstin(request):
    """Cheap, stateless GSTIN check for inline form feedback.

    ``GET /api/gst/validate-gstin/?gstin=27AAACR5055K1Z7`` returns
    ``{valid, reason, state_code, state_name, pan}``. State code/name are returned
    whenever the prefix is a known state, even if the GSTIN is otherwise invalid;
    ``pan`` only when valid. Authenticated users only (it exposes no stored data).
    """
    raw = request.query_params.get("gstin")
    if raw is None or not raw.strip():
        raise ValidationError({"gstin": _("This query parameter is required.")})
    result = check_gstin(raw)
    return Response(
        {
            "valid": result.valid,
            "reason": result.reason,
            "state_code": result.state_code,
            "state_name": result.state_name,
            "pan": result.pan,
        }
    )


def _bool_param(request, name):
    return request.query_params.get(name, "").strip().lower() in ("1", "true", "yes")


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def supply_context(request):
    """Form defaults for a taxable document (``GET /api/gst/supply-context/``).

    Query: ``company``, ``party`` (customer / supplier id), ``direction``
    (``Sales`` / ``Purchase``), optional ``place_of_supply`` (state code),
    ``is_export_with_gst`` and ``is_reverse_charge`` (``1`` / ``true``). Returns
    ``{kind, place_of_supply, gst_category, party_gstin, allowed_components, reason,
    suggested_template}`` where ``kind`` is ``INTRA`` / ``INTER`` / ``null`` and
    ``suggested_template`` is the company's default tax template (``{id, title}``) or
    ``null``. Needs read access to the party master.
    """
    from rest_framework.exceptions import PermissionDenied

    from buying.models import Supplier
    from core.models import State
    from gst.services import supply
    from selling.models import Customer
    from users.permissions import user_has_model_permission

    params = request.query_params
    direction = params.get("direction", "")
    if direction not in (supply.SALES, supply.PURCHASE):
        raise ValidationError({"direction": _("Must be 'Sales' or 'Purchase'.")})
    errors = {}
    company = resolve_active_company(request)
    party_model, label = (
        (Customer, "selling.Customer")
        if direction == supply.SALES
        else (Supplier, "buying.Supplier")
    )
    party = party_model.objects.filter(pk=params.get("party") or 0).first()
    if party is None:
        errors["party"] = _("A valid party is required.")
    elif company is not None and party.company_id != company.pk:
        errors["party"] = _("Party must belong to the company.")
    pos = params.get("place_of_supply") or None
    if pos and not State.objects.filter(pk=pos).exists():
        errors["place_of_supply"] = _("Unknown state code.")
    if errors:
        raise ValidationError(errors)
    if not user_has_model_permission(request.user, label, "can_read"):
        raise PermissionDenied(f"You do not have permission to read {label}.")

    is_rcm = _bool_param(request, "is_reverse_charge")
    ctx = supply.supply_context(
        company,
        party,
        direction,
        pos,
        _bool_param(request, "is_export_with_gst"),
        is_rcm,
    )
    template = (
        supply.default_template(company, direction, ctx.kind, is_rcm)
        if ctx.company_gstin and ctx.gst_rows_allowed
        else None
    )
    return Response(
        {
            "kind": ctx.kind,
            "place_of_supply": ctx.place_of_supply,
            "gst_category": ctx.gst_category,
            "party_gstin": ctx.party_gstin,
            "allowed_components": list(ctx.allowed_components),
            "reason": ctx.reason,
            "suggested_template": (
                {"id": template.pk, "title": template.title} if template else None
            ),
        }
    )


# ---------------------------------------------------------------------- reports


def require_all_model_permissions(*model_labels, flag="can_read"):
    """DRF permission class: the user must hold ``flag`` on *every* label."""
    from users.permissions import user_has_model_permission

    class _RequireAllModelPermissions(permissions.BasePermission):
        def has_permission(self, request, view):
            return all(
                user_has_model_permission(request.user, label, flag)
                for label in model_labels
            )

    return _RequireAllModelPermissions


def _report_context(request, period_required=True):
    from gst import reports

    from core.company_context import resolve_active_company

    company = resolve_active_company(request)
    if not (company.gstin or "").strip():
        raise ValidationError(
            {"company": _("The company has no GSTIN; GST reports are unavailable.")}
        )
    from_date, to_date = reports.resolve_period(request.query_params, period_required)
    return company, from_date, to_date


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_all_model_permissions("selling.SalesInvoice")]
)
def gstr1_report(request):
    """GSTR-1 (``GET /api/gst/reports/gstr1/?company=&month=YYYY-MM``).

    Period: ``month`` or ``from_date`` + ``to_date`` (ISO). Sections b2b, b2cl, b2cs,
    exports, nil_exempt_non_gst, hsn, docs_issued, warnings; money = 2dp strings.
    400 on a missing / malformed period, an unknown company or a company without GSTIN.
    """
    from gst.reports import gstr1

    return Response(gstr1.build(*_report_context(request)))


@api_view(["GET"])
@permission_classes(
    [
        permissions.IsAuthenticated,
        require_all_model_permissions("selling.SalesInvoice", "buying.PurchaseInvoice"),
    ]
)
def gstr3b_report(request):
    """GSTR-3B (``GET /api/gst/reports/gstr3b/?company=&month=YYYY-MM``).

    Needs read on both Sales and Purchase Invoices. Tables 3.1, 3.2, 4, 5 and the
    informational 6.1; same parameters and errors as GSTR-1.
    """
    from gst.reports import gstr3b

    return Response(gstr3b.build(*_report_context(request)))


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_all_model_permissions("accounts.GLEntry")]
)
def tax_ledger_report(request):
    """GST tax ledger (``GET /api/gst/reports/tax-ledger/?company=``).

    Optional ``month`` or ``from_date`` / ``to_date`` (open-ended when omitted),
    ``component`` (CGST / SGST / IGST / CESS), ``direction`` (Input / Output /
    Reverse Charge), ``summary=1`` (omit entries). GL rows on tagged accounts,
    ``is_cancelled=False`` only; balance = debit minus credit.
    """
    from gst.reports import tax_ledger

    company, from_date, to_date = _report_context(request, period_required=False)
    return Response(tax_ledger.build(company, from_date, to_date, request.query_params))
