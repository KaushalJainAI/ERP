from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _
from rest_framework import permissions, viewsets
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from selling.models import CustomerGroup, Customer, SalesOrder, SalesInvoice
from selling.serializers import (
    CustomerGroupSerializer,
    CustomerSerializer,
    SalesOrderSerializer,
    SalesInvoiceSerializer,
)
from accounts.serializers import PaymentEntrySerializer
from core.company_context import CompanyScopedMixin, resolve_active_company
from core.mixins import (
    AuditLogMixin,
    MappedDocumentActionMixin,
    SubmittableViewSetMixin,
)
from core.models import DocStatus
from selling import mapping
from selling.reports import kpis
from selling.services import credit as credit_service
from stock.services import pricing as pricing_service
from users.permissions import HasRolePermission, user_has_model_permission


class CustomerGroupViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = CustomerGroup.objects.all()
    serializer_class = CustomerGroupSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    search_fields = ["name"]


class CustomerViewSet(CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet):
    queryset = Customer.objects.all().select_related(
        "company", "customer_group", "state"
    )
    serializer_class = CustomerSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = [
        "company",
        "customer_group",
        "is_active",
        "gst_category",
        "state",
    ]
    search_fields = ["name", "gstin", "email", "phone"]
    ordering_fields = ["name", "created_at"]

    def get_queryset(self):
        qs = super().get_queryset()
        missing_phone = self.request.query_params.get("missing_phone")
        if missing_phone in ("true", "1", "True"):
            qs = qs.filter(Q(phone__isnull=True) | Q(phone=""))
        return qs

    @action(detail=False, methods=["post"], url_path="bulk-update-contacts")
    def bulk_update_contacts(self, request):
        """Bulk update phone / email for customers (filling contact gaps from Tally data)."""
        entries = request.data
        if not isinstance(entries, list):
            raise DRFValidationError(
                {"error": _("Expected a list of customer updates.")}
            )
        from core.company_context import resolve_active_company
        from contacts.models import Address

        company = resolve_active_company(request)
        updated = 0
        with transaction.atomic():
            for item in entries:
                if not isinstance(item, dict):
                    continue
                cid = item.get("id")
                if not cid:
                    continue
                cust = Customer.objects.filter(pk=cid, company=company).first()
                if not cust:
                    continue
                update_fields = []
                if "phone" in item:
                    cust.phone = (item["phone"] or "").strip() or None
                    update_fields.append("phone")
                if "email" in item:
                    cust.email = (item["email"] or "").strip() or None
                    update_fields.append("email")
                if update_fields:
                    cust.save(update_fields=update_fields)
                    if cust.phone:
                        Address.objects.filter(
                            customer=cust, phone__isnull=True
                        ).update(phone=cust.phone)
                        Address.objects.filter(customer=cust, phone="").update(
                            phone=cust.phone
                        )
                    if cust.email:
                        Address.objects.filter(
                            customer=cust, email__isnull=True
                        ).update(email=cust.email)
                        Address.objects.filter(customer=cust, email="").update(
                            email=cust.email
                        )
                    updated += 1
        return Response({"updated": updated})

    @action(detail=True, methods=["get"], url_path="credit-status")
    def credit_status(self, request, pk=None):
        customer = self.get_object()
        return Response(credit_service.credit_status(customer))


class SalesOrderViewSet(
    CompanyScopedMixin,
    MappedDocumentActionMixin,
    SubmittableViewSetMixin,
    viewsets.ModelViewSet,
):
    action_permission_flags = {
        "make_sales_invoice": "can_read",
        "update_rates": "can_write",
        "pending": "can_read",
    }
    queryset = (
        SalesOrder.objects.all()
        .select_related("customer", "company", "place_of_supply", "taxes_and_charges")
        .prefetch_related("items", "taxes")
    )
    serializer_class = SalesOrderSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "customer", "docstatus", "dispatch_status"]
    search_fields = ["name", "customer__name"]
    ordering_fields = ["transaction_date", "created_at"]

    @action(detail=False, methods=["get"], url_path="pending")
    def pending(self, request):
        company = getattr(self, "active_company", None) or resolve_active_company(
            request
        )
        from selling.services import dispatch as dispatch_service

        data = dispatch_service.pending_orders(company)
        return Response(data)

    @action(detail=True, methods=["post"], url_path="make-sales-invoice")
    def make_sales_invoice(self, request, pk=None):
        """Draft Sales Invoice for the not-yet-invoiced quantity of this submitted order."""
        return self.map_document(
            request,
            mapping.make_sales_invoice,
            "selling.SalesInvoice",
            SalesInvoiceSerializer,
        )

    def _update_rates(self, request):
        doc = self.get_object()
        if doc.docstatus != DocStatus.DRAFT:
            return Response(
                {"error": _("Rates can only be refreshed on a draft.")},
                status=409,
            )
        changed = pricing_service.reapply_rates(doc)
        doc.calculate_taxes_and_totals()
        doc.save()
        data = self.get_serializer(doc).data
        return Response({"changed": changed, "document": data})

    @action(detail=True, methods=["post"], url_path="update-rates")
    def update_rates(self, request, pk=None):
        return self._update_rates(request)


class SalesInvoiceViewSet(
    CompanyScopedMixin,
    MappedDocumentActionMixin,
    SubmittableViewSetMixin,
    viewsets.ModelViewSet,
):
    action_permission_flags = {
        "make_payment_entry": "can_read",
        "make_sales_return": "can_read",
        "update_rates": "can_write",
    }
    queryset = (
        SalesInvoice.objects.all()
        .select_related("customer", "company", "place_of_supply", "taxes_and_charges")
        .prefetch_related("items", "taxes")
    )
    serializer_class = SalesInvoiceSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = [
        "company",
        "customer",
        "docstatus",
        "update_stock",
        "is_return",
        "return_against",
    ]
    search_fields = ["name", "customer__name"]
    ordering_fields = ["posting_date", "created_at"]

    def _update_rates(self, request):
        doc = self.get_object()
        if doc.docstatus != DocStatus.DRAFT:
            return Response(
                {"error": _("Rates can only be refreshed on a draft.")},
                status=409,
            )
        if doc.is_return:
            return Response(
                {"error": _("A return keeps the rates of the original invoice.")},
                status=409,
            )
        changed = pricing_service.reapply_rates(doc)
        doc.calculate_taxes_and_totals()
        doc.save()
        data = self.get_serializer(doc).data
        return Response({"changed": changed, "document": data})

    @action(detail=True, methods=["post"], url_path="update-rates")
    def update_rates(self, request, pk=None):
        return self._update_rates(request)

    @action(detail=True, methods=["post"], url_path="make-sales-return")
    def make_sales_return(self, request, pk=None):
        """Draft Sales Return (credit note) for the not-yet-returned quantity of this
        submitted invoice (negative quantities)."""
        return self.map_document(
            request,
            mapping.make_sales_return,
            "selling.SalesInvoice",
            SalesInvoiceSerializer,
        )

    @action(detail=True, methods=["post"], url_path="make-payment-entry")
    def make_payment_entry(self, request, pk=None):
        """Draft 'Receive' Payment Entry for this submitted invoice's outstanding amount."""
        return self.map_document(
            request,
            mapping.make_payment_entry,
            "accounts.PaymentEntry",
            PaymentEntrySerializer,
        )


class CanReadSalesAnalytics(permissions.BasePermission):
    """Allow read access if user has can_read on selling.SalesOrder, selling.SalesInvoice, or accounts.GLEntry."""

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        return (
            user_has_model_permission(user, "selling.SalesOrder", "can_read")
            or user_has_model_permission(user, "selling.SalesInvoice", "can_read")
            or user_has_model_permission(user, "accounts.GLEntry", "can_read")
        )


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated, CanReadSalesAnalytics])
def sales_analytics_overview(request):
    """Marketing and Sales KPI Analytics overview: comprehensive metrics, conversion, growth, and trends."""
    company = resolve_active_company(request)
    return Response(kpis.build_analytics_summary(company, request.query_params))


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated, CanReadSalesAnalytics])
def sales_conversion_analytics(request):
    """Sales conversion & velocity: Sales Order -> Sales Invoice conversion rate, days to convert, and pipeline funnel."""
    company = resolve_active_company(request)
    from_date, to_date, _ = kpis.resolve_period(request.query_params)
    return Response(
        kpis.compute_sales_conversion_and_velocity(company, from_date, to_date)
    )


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated, CanReadSalesAnalytics])
def sales_revenue_analytics(request):
    """Revenue & volume metrics: net/gross revenue, tax, total volume, AOV, MoM trends and growth vs prior period."""
    company = resolve_active_company(request)
    from_date, to_date, period_name = kpis.resolve_period(request.query_params)
    return Response(
        kpis.compute_revenue_and_volume(company, from_date, to_date, period_name)
    )


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated, CanReadSalesAnalytics])
def sales_products_analytics(request):
    """Top selling products & spice blends by quantity and revenue with category / blend flags."""
    company = resolve_active_company(request)
    from_date, to_date, _ = kpis.resolve_period(request.query_params)
    limit = int(request.query_params.get("limit", 20))
    return Response(
        kpis.compute_top_products_and_spices(company, from_date, to_date, limit)
    )


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated, CanReadSalesAnalytics])
def sales_customers_analytics(request):
    """Customer analytics: acquisition, churn, retention rate, Pareto 80/20 and HHI concentration analysis."""
    company = resolve_active_company(request)
    from_date, to_date, _ = kpis.resolve_period(request.query_params)
    return Response(kpis.compute_customer_analytics(company, from_date, to_date))


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated, CanReadSalesAnalytics])
def sales_receivables_analytics(request):
    """Outstanding receivable ageing summary & Days Sales Outstanding (DSO)."""
    company = resolve_active_company(request)
    from_date, to_date, _ = kpis.resolve_period(request.query_params)
    return Response(kpis.compute_receivables_and_dso(company, from_date, to_date))
