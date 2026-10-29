"""Read the imported NGU catalogue and link variants to ERP items."""

from rest_framework import mixins, permissions, viewsets

from core.mixins import AuditLogMixin
from ngu.models import NguCatalogEntry
from ngu.serializers import NguCatalogEntrySerializer
from users.permissions import HasRolePermission


class NguCatalogEntryViewSet(
    AuditLogMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Rows are created by ``import_ngu_catalog``; a person only sets ``item``."""

    queryset = NguCatalogEntry.objects.all().select_related("item")
    serializer_class = NguCatalogEntrySerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    http_method_names = ["get", "patch", "head", "options"]
    filterset_fields = {
        "kind": ["exact"],
        "ngu_category_id": ["exact"],
        "is_active": ["exact"],
        "item": ["exact", "isnull"],
        "link_method": ["exact"],
    }
    search_fields = ["product_name", "category_name", "item__code", "item__name"]
    ordering_fields = ["ngu_id", "product_name", "price"]
