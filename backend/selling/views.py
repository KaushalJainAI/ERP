from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from selling.models import CustomerGroup, Customer, SalesOrder, SalesInvoice
from selling.serializers import (
    CustomerGroupSerializer,
    CustomerSerializer,
    SalesOrderSerializer,
    SalesInvoiceSerializer,
)
from accounts.serializers import PaymentEntrySerializer
from core.mixins import (
    AuditLogMixin,
    MappedDocumentActionMixin,
    SubmittableViewSetMixin,
)
from core.models import DocStatus
from selling import mapping
from selling.services import credit as credit_service
from stock.services import pricing as pricing_service
from users.permissions import HasRolePermission


class CustomerGroupViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = CustomerGroup.objects.all()
    serializer_class = CustomerGroupSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    search_fields = ["name"]


class CustomerViewSet(AuditLogMixin, viewsets.ModelViewSet):
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

    @action(detail=True, methods=["get"], url_path="credit-status")
    def credit_status(self, request, pk=None):
        customer = self.get_object()
        return Response(credit_service.credit_status(customer))


class SalesOrderViewSet(
    MappedDocumentActionMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    action_permission_flags = {
        "make_sales_invoice": "can_read",
        "update_rates": "can_write",
    }
    queryset = (
        SalesOrder.objects.all()
        .select_related("customer", "company", "place_of_supply", "taxes_and_charges")
        .prefetch_related("items", "taxes")
    )
    serializer_class = SalesOrderSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "customer", "docstatus"]
    search_fields = ["name", "customer__name"]
    ordering_fields = ["transaction_date", "created_at"]

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
                {"error": "Rates can only be refreshed on a draft."},
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
    MappedDocumentActionMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    action_permission_flags = {
        "make_payment_entry": "can_read",
        "update_rates": "can_write",
    }
    queryset = (
        SalesInvoice.objects.all()
        .select_related("customer", "company", "place_of_supply", "taxes_and_charges")
        .prefetch_related("items", "taxes")
    )
    serializer_class = SalesInvoiceSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "customer", "docstatus", "update_stock"]
    search_fields = ["name", "customer__name"]
    ordering_fields = ["posting_date", "created_at"]

    def _update_rates(self, request):
        doc = self.get_object()
        if doc.docstatus != DocStatus.DRAFT:
            return Response(
                {"error": "Rates can only be refreshed on a draft."},
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

    @action(detail=True, methods=["post"], url_path="make-payment-entry")
    def make_payment_entry(self, request, pk=None):
        """Draft 'Receive' Payment Entry for this submitted invoice's outstanding amount."""
        return self.map_document(
            request,
            mapping.make_payment_entry,
            "accounts.PaymentEntry",
            PaymentEntrySerializer,
        )
