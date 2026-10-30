from django.db.models import Q
from django.utils.translation import gettext as _
from rest_framework import permissions, viewsets
from rest_framework.exceptions import ValidationError

from contacts.models import Address
from contacts.serializers import AddressSerializer
from core.company_context import CompanyScopedMixin
from core.mixins import AuditLogMixin
from users.permissions import HasRolePermission


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
