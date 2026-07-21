from rest_framework import viewsets, permissions
from selling.models import CustomerGroup, Customer, SalesOrder, SalesInvoice
from selling.serializers import (
    CustomerGroupSerializer,
    CustomerSerializer,
    SalesOrderSerializer,
    SalesInvoiceSerializer,
)
from core.mixins import AuditLogMixin, SubmittableViewSetMixin
from users.permissions import HasRolePermission


class CustomerGroupViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = CustomerGroup.objects.all()
    serializer_class = CustomerGroupSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    search_fields = ["name"]


class CustomerViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = Customer.objects.all().select_related("company", "customer_group")
    serializer_class = CustomerSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "customer_group", "is_active"]
    search_fields = ["name", "gstin", "email", "phone"]
    ordering_fields = ["name", "created_at"]


class SalesOrderViewSet(SubmittableViewSetMixin, viewsets.ModelViewSet):
    queryset = SalesOrder.objects.all().select_related("customer", "company").prefetch_related("items")
    serializer_class = SalesOrderSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "customer", "docstatus"]
    search_fields = ["name", "customer__name"]
    ordering_fields = ["transaction_date", "created_at"]


class SalesInvoiceViewSet(SubmittableViewSetMixin, viewsets.ModelViewSet):
    queryset = SalesInvoice.objects.all().select_related("customer", "company").prefetch_related("items")
    serializer_class = SalesInvoiceSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "customer", "docstatus", "update_stock"]
    search_fields = ["name", "customer__name"]
    ordering_fields = ["posting_date", "created_at"]
