"""Read-only API over the Tally history (rows are written only by import_tally)."""

from django.db.models import Count
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

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
        "base_voucher_type": ["exact", "in"],
        "voucher_type": ["exact"],
        "party": ["exact"],
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

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Voucher counts per Tally base type (tabs of the past-bills screen)."""
        qs = self.filter_queryset(self.get_queryset()).filter(kind="voucher")
        rows = qs.values("base_voucher_type").annotate(count=Count("id"))
        years = sorted(
            qs.order_by().values_list("fiscal_year", flat=True).distinct(), reverse=True
        )
        return Response(
            {
                "by_type": {r["base_voucher_type"]: r["count"] for r in rows},
                "fiscal_years": years,
            }
        )


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
