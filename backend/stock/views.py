from decimal import Decimal
from rest_framework import viewsets, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from stock.models import (
    UOM,
    ItemGroup,
    Item,
    Warehouse,
    StockLedgerEntry,
    Bin,
    StockEntry,
)
from stock.serializers import (
    UOMSerializer,
    ItemGroupSerializer,
    ItemSerializer,
    WarehouseSerializer,
    StockLedgerEntrySerializer,
    BinSerializer,
    StockEntrySerializer,
)
from core.mixins import AuditLogMixin, SubmittableViewSetMixin
from users.permissions import HasRolePermission, require_model_permission


class UOMViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = UOM.objects.all()
    serializer_class = UOMSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    search_fields = ["name"]


class ItemGroupViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = ItemGroup.objects.all().select_related("parent")
    serializer_class = ItemGroupSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    search_fields = ["name"]


class ItemViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = (
        Item.objects.all()
        .select_related("item_group", "stock_uom", "item_tax_template")
        .prefetch_related("item_tax_template__rows")
    )
    serializer_class = ItemSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = [
        "item_group",
        "is_stock_item",
        "is_active",
        "valuation_method",
        "item_tax_template",
        "is_nil_exempt",
        "is_non_gst",
    ]
    search_fields = ["code", "name", "description", "gst_hsn_code"]
    ordering_fields = ["code", "name", "created_at"]


class WarehouseViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = Warehouse.objects.all().select_related("company", "parent")
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "is_group"]
    search_fields = ["name"]


class StockLedgerEntryViewSet(viewsets.ReadOnlyModelViewSet):
    """ReadOnlyModelViewSet for immutable StockLedgerEntry facts."""

    queryset = StockLedgerEntry.objects.all().select_related("item", "warehouse")
    serializer_class = StockLedgerEntrySerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = [
        "item",
        "warehouse",
        "posting_date",
        "voucher_type",
        "voucher_no",
        "is_cancelled",
    ]
    search_fields = ["voucher_no", "item__code", "item__name"]
    ordering_fields = ["posting_date", "posting_time", "created_at"]


class BinViewSet(viewsets.ReadOnlyModelViewSet):
    """ReadOnlyModelViewSet for derived Bin stock balance projections."""

    queryset = Bin.objects.all().select_related("item", "warehouse")
    serializer_class = BinSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["item", "warehouse"]
    search_fields = ["item__code", "item__name", "warehouse__name"]
    ordering_fields = ["actual_qty", "stock_value", "modified_at"]


class StockEntryViewSet(SubmittableViewSetMixin, viewsets.ModelViewSet):
    queryset = StockEntry.objects.all().prefetch_related("items")
    serializer_class = StockEntrySerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "stock_entry_type", "posting_date", "docstatus"]
    search_fields = ["name"]
    ordering_fields = ["posting_date", "created_at"]


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("stock.Bin")]
)
def stock_balance(request):
    """Stock Balance report endpoint."""
    item_id = request.query_params.get("item")
    warehouse_id = request.query_params.get("warehouse")

    qs = Bin.objects.all().select_related("item", "warehouse")
    if item_id:
        qs = qs.filter(item_id=item_id)
    if warehouse_id:
        qs = qs.filter(warehouse_id=warehouse_id)

    report_rows = []
    total_qty_sum = Decimal("0.000")
    total_value_sum = Decimal("0.00")

    for b in qs:
        total_qty_sum += b.actual_qty
        total_value_sum += b.stock_value
        report_rows.append(
            {
                "item_code": b.item.code,
                "item_name": b.item.name,
                "warehouse_name": b.warehouse.name,
                "actual_qty": str(b.actual_qty),
                "valuation_rate": str(b.valuation_rate),
                "stock_value": str(b.stock_value.quantize(Decimal("0.01"))),
            }
        )

    return Response(
        {
            "rows": report_rows,
            "total_actual_qty": str(total_qty_sum),
            "total_stock_value": str(total_value_sum.quantize(Decimal("0.01"))),
        }
    )
