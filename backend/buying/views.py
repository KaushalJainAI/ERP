from rest_framework import viewsets, permissions
from buying.models import SupplierGroup, Supplier, PurchaseOrder, PurchaseInvoice
from buying.serializers import (
    SupplierGroupSerializer,
    SupplierSerializer,
    PurchaseOrderSerializer,
    PurchaseInvoiceSerializer,
)
from accounts.serializers import PaymentEntrySerializer
from buying import mapping
from core.mixins import (
    AuditLogMixin,
    MappedDocumentActionMixin,
    SubmittableViewSetMixin,
)
from rest_framework.decorators import action
from users.permissions import HasRolePermission


class SupplierGroupViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = SupplierGroup.objects.all()
    serializer_class = SupplierGroupSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    search_fields = ["name"]


class SupplierViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = Supplier.objects.all().select_related(
        "company", "supplier_group", "state"
    )
    serializer_class = SupplierSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = [
        "company",
        "supplier_group",
        "is_active",
        "gst_category",
        "state",
    ]
    search_fields = ["name", "gstin", "email", "phone"]
    ordering_fields = ["name", "created_at"]


class PurchaseOrderViewSet(
    MappedDocumentActionMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    action_permission_flags = {"make_purchase_invoice": "can_read"}
    queryset = (
        PurchaseOrder.objects.all()
        .select_related("supplier", "company")
        .prefetch_related("items")
    )
    serializer_class = PurchaseOrderSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "supplier", "docstatus"]
    search_fields = ["name", "supplier__name"]
    ordering_fields = ["transaction_date", "created_at"]

    @action(detail=True, methods=["post"], url_path="make-purchase-invoice")
    def make_purchase_invoice(self, request, pk=None):
        """Draft Purchase Invoice for the not-yet-invoiced quantity of this submitted order."""
        return self.map_document(
            request,
            mapping.make_purchase_invoice,
            "buying.PurchaseInvoice",
            PurchaseInvoiceSerializer,
        )


class PurchaseInvoiceViewSet(
    MappedDocumentActionMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    action_permission_flags = {"make_payment_entry": "can_read"}
    queryset = (
        PurchaseInvoice.objects.all()
        .select_related("supplier", "company")
        .prefetch_related("items")
    )
    serializer_class = PurchaseInvoiceSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "supplier", "docstatus", "update_stock"]
    search_fields = ["name", "supplier__name"]
    ordering_fields = ["posting_date", "created_at"]

    @action(detail=True, methods=["post"], url_path="make-payment-entry")
    def make_payment_entry(self, request, pk=None):
        """Draft 'Pay' Payment Entry for this submitted invoice's outstanding amount."""
        return self.map_document(
            request,
            mapping.make_payment_entry,
            "accounts.PaymentEntry",
            PaymentEntrySerializer,
        )
