"""Read-only API over the Tally history (rows are written only by import_tally)."""

from rest_framework import permissions, viewsets

from legacy.models import TallyItemLine, TallyRecord
from legacy.serializers import (
    TallyItemLineSerializer,
    TallyRecordDetailSerializer,
    TallyRecordSerializer,
)
from core.company_context import CompanyScopedMixin
from users.permissions import HasRolePermission


class TallyRecordViewSet(CompanyScopedMixin, viewsets.ReadOnlyModelViewSet):
    queryset = TallyRecord.objects.all()
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = {
        "company": ["exact"],
        "kind": ["exact"],
        "fiscal_year": ["exact"],
        "base_voucher_type": ["exact"],
        "voucher_type": ["exact"],
        "is_cancelled": ["exact"],
        "erp_name": ["exact"],
        "voucher_no": ["exact"],
        "posting_date": ["exact", "gte", "lte"],
    }
    search_fields = [
        "voucher_no",
        "name",
        "party",
        "reference",
        "narration",
        "erp_name",
    ]
    ordering_fields = ["posting_date", "fiscal_year", "voucher_no", "amount", "id"]

    def get_queryset(self):
        qs = super().get_queryset()
        # The raw XML is only sent on the detail route.
        return qs if self.action == "retrieve" else qs.defer("raw")

    def get_serializer_class(self):
        if self.action == "retrieve":
            return TallyRecordDetailSerializer
        return TallyRecordSerializer


class TallyItemLineViewSet(CompanyScopedMixin, viewsets.ReadOnlyModelViewSet):
    queryset = TallyItemLine.objects.all().select_related("item")
    serializer_class = TallyItemLineSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = {
        "company": ["exact"],
        "fiscal_year": ["exact"],
        "voucher_type": ["exact"],
        "item": ["exact"],
        "customer": ["exact"],
        "supplier": ["exact"],
        "is_inward": ["exact"],
        "record": ["exact"],
        "posting_date": ["exact", "gte", "lte"],
    }
    search_fields = ["item_name", "party", "voucher_no", "ledger"]
    ordering_fields = ["posting_date", "qty", "rate", "amount", "id"]
