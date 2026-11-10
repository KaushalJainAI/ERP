from django.db.models import Q
from django.utils.translation import gettext as _
from rest_framework import permissions, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from buying.models import Supplier
from contacts.models import Address
from contacts.parties import (
    REGISTERED_CATEGORIES,
    ROOT_CUSTOMER_GROUP,
    ROOT_SUPPLIER_GROUP,
)
from contacts.serializers import AddressSerializer
from core.company_context import CompanyScopedMixin, resolve_active_company
from core.mixins import AuditLogMixin
from selling.models import Customer
from stock.models import Item
from users.permissions import HasRolePermission, user_has_model_permission


class AddressViewSet(CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet):
    queryset = Address.objects.all().select_related("state", "customer", "supplier")
    serializer_class = AddressSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = [
        "customer",
        "supplier",
        "address_type",
        "is_primary_address",
        "is_active",
        "state",
    ]
    search_fields = ["address_title", "address_line1", "city", "pincode", "gstin"]
    ordering_fields = ["address_title", "created_at"]

    def get_queryset(self):
        company = self.active_company
        return (
            super(CompanyScopedMixin, self)
            .get_queryset()
            .filter(Q(customer__company=company) | Q(supplier__company=company))
        )

    def _check_party(self, serializer):
        data = serializer.validated_data
        party = data.get("customer") or data.get("supplier")
        if party is None or party.company_id != self.active_company.pk:
            raise ValidationError(
                {"customer": _("The party must belong to the active company.")}
            )

    def perform_create(self, serializer):
        self._check_party(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._check_party(serializer)
        super().perform_update(serializer)


class NamesToCompleteView(APIView):
    """Masters with a field still missing (U-2). Derived on every call, no stored flag.

    Active parties of the active company: no state; a registered party with no GSTIN; a
    party still in the seeded root group. Active stock items: no HSN code; no item
    tax template. Each kind is listed only when the caller can read its model.
    """

    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    model_label = "selling.Customer"

    def get(self, request):
        company = resolve_active_company(request)
        user = request.user
        items = []
        if user_has_model_permission(user, "selling.Customer"):
            items += self._parties(
                Customer, company, "customer_group", ROOT_CUSTOMER_GROUP, "customer"
            )
        if user_has_model_permission(user, "buying.Supplier"):
            items += self._parties(
                Supplier, company, "supplier_group", ROOT_SUPPLIER_GROUP, "supplier"
            )
        if user_has_model_permission(user, "stock.Item"):
            items += self._items()
        return Response({"items": items, "count": len(items)})

    @staticmethod
    def _parties(model, company, group_field, root_name, kind):
        registered_no_gstin = Q(gst_category__in=REGISTERED_CATEGORIES) & (
            Q(gstin__isnull=True) | Q(gstin="")
        )
        qs = (
            model.objects.filter(company=company, is_active=True)
            .filter(
                Q(state__isnull=True)
                | registered_no_gstin
                | Q(**{f"{group_field}__name": root_name})
            )
            .select_related(group_field)
            .order_by("name", "pk")
        )
        rows = []
        for party in qs:
            missing = []
            if party.state_id is None:
                missing.append("state")
            if party.gst_category in REGISTERED_CATEGORIES and not party.gstin:
                missing.append("gstin")
            if getattr(party, group_field).name == root_name:
                missing.append("group")
            if missing:
                rows.append(
                    {
                        "kind": kind,
                        "id": party.pk,
                        "name": party.name,
                        "missing": missing,
                    }
                )
        return rows

    @staticmethod
    def _items():
        qs = (
            Item.objects.filter(is_stock_item=True, is_active=True)
            .filter(
                Q(gst_hsn_code="")
                | Q(gst_hsn_code__isnull=True)
                | Q(item_tax_template__isnull=True)
            )
            .order_by("name", "pk")
        )
        rows = []
        for item in qs:
            missing = []
            if not item.gst_hsn_code:
                missing.append("hsn")
            if item.item_tax_template_id is None:
                missing.append("tax_template")
            if missing:
                rows.append(
                    {
                        "kind": "item",
                        "id": item.pk,
                        "name": item.name,
                        "missing": missing,
                    }
                )
        return rows
