"""Active-company context: the tenant boundary for every request.

The active company is carried per request in ``X-Company-ID``. It must be one of
the caller's ``UserCompany`` memberships (superusers may enter any active
company). Every company-owned viewset filters its queryset through
``CompanyScopedMixin`` and injects the header company on create, so the client
cannot read or write another company's rows by changing a form field or a query
parameter.

The "switch only on the home screen" rule lives in the frontend. Security lives
here: a request is always resolved against the database membership, never
against what the client claims.
"""

from django.utils.translation import gettext as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from core.models import Company


def companies_for(user):
    """Active companies the user may enter (all active companies for a superuser)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Company.objects.none()
    if getattr(user, "is_superuser", False):
        return Company.objects.filter(is_active=True).order_by("id")
    return (
        Company.objects.filter(
            is_active=True,
            user_memberships__user=user,
            user_memberships__is_active=True,
        )
        .distinct()
        .order_by("id")
    )


def resolve_active_company(request, required=True):
    """Resolve and cache ``request.active_company``.

    Order: the ``X-Company-ID`` header, then the user's default membership, then
    their first membership. A superuser may also pass ``?company=`` for
    back-compat; ordinary users cannot choose a company through the URL.

    Raises ``PermissionDenied`` when the caller has no company (or asks for one
    they are not a member of), and ``ValidationError`` for a malformed id.
    """
    cached = getattr(request, "active_company", None)
    if cached is not None:
        return cached

    available = companies_for(request.user)
    raw = request.headers.get("X-Company-ID") if hasattr(request, "headers") else None
    if raw in (None, "") and getattr(request.user, "is_superuser", False):
        params = getattr(request, "query_params", None)
        raw = (
            params.get("company") if params is not None else request.GET.get("company")
        )

    company = None
    if raw not in (None, ""):
        try:
            company_id = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({"company": _("Must be a company id.")})
        company = available.filter(pk=company_id).first()
        if company is None:
            if getattr(request.user, "is_superuser", False):
                raise NotFound(_("Company not found."))
            raise PermissionDenied(_("You do not have access to this company."))
    elif getattr(request.user, "is_superuser", False):
        company = available.first()
    else:
        company = available.filter(
            user_memberships__user=request.user,
            user_memberships__is_default=True,
            user_memberships__is_active=True,
        ).first()
        if company is None:
            company = available.first()

    if company is None and required:
        raise PermissionDenied(_("No company is assigned to your user."))
    request.active_company = company
    return company


class CompanyScopedMixin:
    """Scope a DRF viewset to the request's active company.

    ``company_lookup`` is the ORM path to the company: ``"company"`` for models
    that carry it directly, ``"warehouse__company"`` for ledger rows that inherit
    it, or a custom ``get_queryset`` override for multi-path models (addresses).
    """

    company_lookup = "company"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.active_company = resolve_active_company(request)

    def get_queryset(self):
        queryset = super().get_queryset()
        company = getattr(self, "active_company", None)
        if company is None:
            try:
                company = resolve_active_company(self.request)
            except Exception:
                # Schema generation and other contexts without a real request.
                return queryset.none()
            self.active_company = company
        return queryset.filter(**{self.company_lookup: company})

    def get_serializer(self, *args, **kwargs):
        # The server owns the company. Replace any client-supplied value with the
        # active company *before* validation, so serializer-level checks (party
        # company, account company, payroll employees) see the truth. The field
        # stays writable only so those validators can run; ``perform_create``
        # injects it again and updates are pinned to the same company.
        company = getattr(self, "active_company", None)
        data = kwargs.get("data")
        if company is not None and data is not None:
            requested = data.get("company")
            if (
                getattr(self, "action", None) in ("update", "partial_update")
                and requested not in (None, "")
                and str(requested) != str(company.pk)
            ):
                raise ValidationError({"company": _("The company cannot be changed.")})
            data = data.copy() if hasattr(data, "copy") else dict(data)
            data["company"] = company.pk
            kwargs["data"] = data
        serializer = super().get_serializer(*args, **kwargs)
        fields = getattr(serializer, "fields", None)
        if fields is None and hasattr(serializer, "child"):
            fields = serializer.child.fields
        if fields is not None and "company" in fields:
            fields["company"].required = False
        return serializer

    def perform_create(self, serializer):
        if "company" in getattr(serializer, "fields", {}):
            serializer.validated_data["company"] = self.active_company
        super().perform_create(serializer)


def active_company_id(request):
    """Convenience for function-based report views."""
    return resolve_active_company(request).pk


def object_company_id(obj):
    """Best-effort company of a document/master, for attachment parent checks.

    Returns ``None`` for genuinely global masters (items, groups, UOMs), which
    are shared across companies. Transactional rows resolve through the first
    company-bearing relation (warehouse, employee, customer, supplier, batch).
    """
    if obj is None:
        return None
    company_id = getattr(obj, "company_id", None)
    if company_id is not None:
        return company_id
    for attribute in ("warehouse", "employee", "customer", "supplier", "batch"):
        related = getattr(obj, attribute, None)
        if related is not None:
            company_id = getattr(related, "company_id", None)
            if company_id is not None:
                return company_id
    return None
