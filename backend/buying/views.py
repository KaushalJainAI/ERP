from rest_framework import viewsets, permissions
from buying.models import SupplierGroup, Supplier, PurchaseOrder, PurchaseInvoice
from buying.serializers import (
    SupplierGroupSerializer,
    SupplierSerializer,
    PurchaseOrderSerializer,
    PurchaseInvoiceSerializer,
)
from core.mixins import AuditLogMixin, SubmittableViewSetMixin
from users.permissions import HasRolePermission


class SupplierGroupViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = SupplierGroup.objects.all()
    serializer_class = SupplierGroupSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    search_fields = ["name"]


class SupplierViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = Supplier.objects.all().select_related("company", "supplier_group")
    serializer_class = SupplierSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "supplier_group", "is_active"]
    search_fields = ["name", "gstin", "email", "phone"]
    ordering_fields = ["name", "created_at"]


class PurchaseOrderViewSet(SubmittableViewSetMixin, viewsets.ModelViewSet):
    queryset = PurchaseOrder.objects.all().select_related("supplier", "company").prefetch_related("items")
    serializer_class = PurchaseOrderSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "supplier", "docstatus"]
    search_fields = ["name", "supplier__name"]
    ordering_fields = ["transaction_date", "created_at"]


class PurchaseInvoiceViewSet(SubmittableViewSetMixin, viewsets.ModelViewSet):
    queryset = PurchaseInvoice.objects.all().select_related("supplier", "company").prefetch_related("items")
    serializer_class = PurchaseInvoiceSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "supplier", "docstatus", "update_stock"]
    search_fields = ["name", "supplier__name"]
    ordering_fields = ["posting_date", "created_at"]
