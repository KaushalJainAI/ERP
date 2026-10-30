from django.utils.translation import gettext as _
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from buying.models import (
    SupplierGroup,
    Supplier,
    PurchaseOrder,
    PurchaseInvoice,
    PurchaseChargeTemplate,
)
from buying.serializers import (
    SupplierGroupSerializer,
    SupplierSerializer,
    PurchaseOrderSerializer,
    PurchaseInvoiceSerializer,
    PurchaseChargeTemplateSerializer,
)
from accounts.serializers import PaymentEntrySerializer
from buying import mapping
from core.company_context import CompanyScopedMixin
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


class SupplierViewSet(CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet):
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


class PurchaseChargeTemplateViewSet(
    CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet
):
    """Presets for the extra-charge rows of a purchase bill (Freight, Hamali...)."""

    queryset = PurchaseChargeTemplate.objects.all().select_related("account")
    serializer_class = PurchaseChargeTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    action_permission_flags = {"ensure_defaults": "can_create"}
    filterset_fields = ["company", "is_active"]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]

    @action(detail=False, methods=["post"], url_path="ensure-defaults")
    def ensure_defaults(self, request):
        """Create the standard charge accounts and templates of the active company
        (idempotent; for companies that were not set up by ``seed_defaults``)."""
        from buying.services.charges import ensure_charge_setup

        ensure_charge_setup(self.active_company)
        rows = self.get_queryset().order_by("name")
        return Response(self.get_serializer(rows, many=True).data)


class PurchaseOrderViewSet(
    CompanyScopedMixin,
    MappedDocumentActionMixin,
    SubmittableViewSetMixin,
    viewsets.ModelViewSet,
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
                {"error": _("Rates can only be refreshed on a draft.")}, status=409
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
    CompanyScopedMixin,
    MappedDocumentActionMixin,
    SubmittableViewSetMixin,
    viewsets.ModelViewSet,
):
    action_permission_flags = {
        "make_payment_entry": "can_read",
        "make_purchase_return": "can_read",
        "update_rates": "can_write",
    }
    queryset = (
        PurchaseInvoice.objects.all()
        .select_related("supplier", "company", "place_of_supply", "taxes_and_charges")
        .prefetch_related("items", "taxes", "charges")
    )
    serializer_class = PurchaseInvoiceSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = [
        "company",
        "supplier",
        "docstatus",
        "update_stock",
        "is_return",
        "return_against",
    ]
    search_fields = ["name", "supplier__name", "bill_no"]
    ordering_fields = ["posting_date", "created_at", "bill_date"]

    def _update_rates(self, request):
        doc = self.get_object()
        if doc.docstatus != DocStatus.DRAFT:
            return Response(
                {"error": _("Rates can only be refreshed on a draft.")}, status=409
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

    @action(detail=True, methods=["post"], url_path="make-purchase-return")
    def make_purchase_return(self, request, pk=None):
        """Draft Purchase Return (debit note) for the not-yet-returned quantity of this
        submitted invoice (negative quantities)."""
        return self.map_document(
            request,
            mapping.make_purchase_return,
            "buying.PurchaseInvoice",
            PurchaseInvoiceSerializer,
        )

    @action(detail=True, methods=["post"], url_path="make-payment-entry")
    def make_payment_entry(self, request, pk=None):
        """Draft 'Pay' Payment Entry for this submitted invoice's outstanding amount."""
        return self.map_document(
            request,
            mapping.make_payment_entry,
            "accounts.PaymentEntry",
            PaymentEntrySerializer,
        )
