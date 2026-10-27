from rest_framework import viewsets, permissions
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
from rest_framework.decorators import action
from selling import mapping
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


class SalesOrderViewSet(
    MappedDocumentActionMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    action_permission_flags = {"make_sales_invoice": "can_read"}
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


class SalesInvoiceViewSet(
    MappedDocumentActionMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    action_permission_flags = {"make_payment_entry": "can_read"}
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

    @action(detail=True, methods=["post"], url_path="make-payment-entry")
    def make_payment_entry(self, request, pk=None):
        """Draft 'Receive' Payment Entry for this submitted invoice's outstanding amount."""
        return self.map_document(
            request,
            mapping.make_payment_entry,
            "accounts.PaymentEntry",
            PaymentEntrySerializer,
        )
