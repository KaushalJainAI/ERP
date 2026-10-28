from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
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
from core.models import DocStatus
from stock.services import pricing as pricing_service
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
    action_permission_flags = {
        "make_purchase_invoice": "can_read",
        "update_rates": "can_write",
    }
    queryset = (
        PurchaseOrder.objects.all()
        .select_related("supplier", "company", "place_of_supply", "taxes_and_charges")
        .prefetch_related("items", "taxes")
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

    def _update_rates(self, request):
        doc = self.get_object()
        if doc.docstatus != DocStatus.DRAFT:
            return Response(
                {"error": "Rates can only be refreshed on a draft."}, status=409
            )
        changed = pricing_service.reapply_rates(doc)
        doc.calculate_taxes_and_totals()
        doc.save()
        data = self.get_serializer(doc).data
        return Response({"changed": changed, "document": data})

    @action(detail=True, methods=["post"], url_path="update-rates")
    def update_rates(self, request, pk=None):
        return self._update_rates(request)


class PurchaseInvoiceViewSet(
    MappedDocumentActionMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    action_permission_flags = {
        "make_payment_entry": "can_read",
        "update_rates": "can_write",
    }
    queryset = (
        PurchaseInvoice.objects.all()
        .select_related("supplier", "company", "place_of_supply", "taxes_and_charges")
        .prefetch_related("items", "taxes")
    )
    serializer_class = PurchaseInvoiceSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "supplier", "docstatus", "update_stock"]
    search_fields = ["name", "supplier__name"]
    ordering_fields = ["posting_date", "created_at"]

    def _update_rates(self, request):
        doc = self.get_object()
        if doc.docstatus != DocStatus.DRAFT:
            return Response(
                {"error": "Rates can only be refreshed on a draft."}, status=409
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
        """Draft 'Pay' Payment Entry for this submitted invoice's outstanding amount."""
        return self.map_document(
            request,
            mapping.make_payment_entry,
            "accounts.PaymentEntry",
            PaymentEntrySerializer,
        )
