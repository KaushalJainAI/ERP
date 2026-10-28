"""Manufacturing API: BOM, Work Order, reports and Work-Order actions."""

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Sum
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response

from core.mixins import AuditLogMixin, SubmittableViewSetMixin
from manufacturing.models import BOM, WorkOrder, WorkOrderStatus
from manufacturing.serializers import BOMSerializer, WorkOrderSerializer
from manufacturing.services import costing
from manufacturing.services import work_orders as wo_service
from stock.models import StockEntry, StockEntryItem, StockEntryType
from stock.serializers import StockEntrySerializer
from stock.views import _jsonable, _params_400, _parse_date, _parse_int
from users.permissions import HasRolePermission, require_model_permission


class BOMViewSet(AuditLogMixin, viewsets.ModelViewSet):
    """Bills of Materials, with derived cost and multi-level explosion."""

    queryset = (
        BOM.objects.all()
        .select_related("company", "item")
        .prefetch_related("items__item", "items__bom_no", "scrap_items__item")
    )
    serializer_class = BOMSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "item", "is_active", "is_default", "rate_basis"]
    search_fields = ["name", "item__code", "item__name"]
    ordering_fields = ["name", "created_at"]

    @action(detail=True, methods=["get"])
    def cost(self, request, pk=None):
        """Component rows, their rates and the unit cost, computed on read."""
        bom = self.get_object()
        return Response(_jsonable(costing.bom_cost(bom)))

    @action(detail=True, methods=["get"])
    def explode(self, request, pk=None):
        """Flattened multi-level tree for one BOM basis."""
        bom = self.get_object()
        return Response(_jsonable(costing.explode(bom)))


class WorkOrderViewSet(SubmittableViewSetMixin, viewsets.ModelViewSet):
    """Work Orders with server-generated required-material rows."""

    queryset = (
        WorkOrder.objects.all()
        .select_related("company", "production_item", "bom")
        .prefetch_related("required_items__item", "required_items__source_warehouse")
    )
    serializer_class = WorkOrderSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "production_item", "bom", "docstatus", "status"]
    search_fields = ["name", "production_item__code", "production_item__name"]
    ordering_fields = ["name", "planned_start_date", "created_at"]
    action_permission_flags = {
        "stop": "can_submit",
        "resume": "can_submit",
        "make_stock_entry": "can_read",
    }

    def _require_submitted(self, wo):
        if wo.docstatus != 1:
            return Response(
                {"error": "Only a submitted Work Order can be changed."},
                status=status.HTTP_409_CONFLICT,
            )
        return None

    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        wo = self.get_object()
        blocked = self._require_submitted(wo)
        if blocked:
            return blocked
        if wo.stopped:
            return Response(
                {"error": "This Work Order is already stopped."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if wo.produced_qty >= wo.qty:
            return Response(
                {"error": "This Work Order is already fully produced."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        WorkOrder.objects.filter(pk=wo.pk).update(stopped=True)
        wo.refresh_from_db()
        return Response(self.get_serializer(wo).data)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        wo = self.get_object()
        blocked = self._require_submitted(wo)
        if blocked:
            return blocked
        if wo.status == WorkOrderStatus.COMPLETED:
            return Response(
                {"error": "A completed Work Order cannot be resumed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        WorkOrder.objects.filter(pk=wo.pk).update(stopped=False)
        wo.refresh_from_db()
        return Response(self.get_serializer(wo).data)

    @action(detail=True, methods=["post"], url_path="make-stock-entry")
    def make_stock_entry(self, request, pk=None):
        """Draft a Material Transfer for Manufacture / Manufacture entry.

        Body: ``{"type": "material_transfer" | "manufacture", "qty": "..."}``.
        ``qty`` defaults to the whole remaining quantity.
        """
        wo = self.get_object()
        if wo.docstatus != 1:
            return Response(
                {"error": "Submit the Work Order before making stock entries."},
                status=status.HTTP_409_CONFLICT,
            )
        kind = request.data.get("type", "material_transfer")
        kind = {
            "material_transfer": StockEntryType.MATERIAL_TRANSFER_FOR_MANUFACTURE,
            "manufacture": StockEntryType.MANUFACTURE,
        }.get(kind, kind)
        qty = request.data.get("qty")
        try:
            qty = Decimal(str(qty)) if qty not in (None, "") else None
        except Exception:
            return Response(
                {"qty": "Enter a valid quantity."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            entry = wo_service.make_stock_entry(wo, kind, qty=qty)
        except DjangoValidationError as exc:
            return Response(
                {"error": exc.messages if hasattr(exc, "messages") else str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            StockEntrySerializer(entry, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("manufacturing.WorkOrder")]
)
@_params_400
def production_yield(request):
    """Planned versus actual output and loss, one row per manufacturing entry.

    Params: company, from_date, to_date, work_order, bom.
    """
    q = request.query_params
    from_date = _parse_date(q.get("from_date"), "from_date")
    to_date = _parse_date(q.get("to_date"), "to_date")
    company = _parse_int(q.get("company"), "company")
    work_order = _parse_int(q.get("work_order"), "work_order")
    bom = _parse_int(q.get("bom"), "bom")

    entries = (
        StockEntry.objects.filter(
            docstatus=1,
            stock_entry_type__in=(
                StockEntryType.MANUFACTURE,
                StockEntryType.REPACK,
            ),
        )
        .select_related("work_order", "bom", "company")
        .order_by("posting_date", "id")
    )
    if company:
        entries = entries.filter(company_id=company)
    if work_order:
        entries = entries.filter(work_order_id=work_order)
    if bom:
        entries = entries.filter(bom_id=bom)
    if from_date:
        entries = entries.filter(posting_date__gte=from_date)
    if to_date:
        entries = entries.filter(posting_date__lte=to_date)

    rows = []
    total_output = Decimal("0.000")
    total_loss = Decimal("0.000")
    for entry in entries:
        finished = StockEntryItem.objects.filter(
            stock_entry=entry, is_finished_item=True
        )
        output_qty = sum((r.qty for r in finished), Decimal("0.000"))
        output_value = sum(
            (r.qty * r.valuation_rate for r in finished), Decimal("0.00")
        )
        scrap_value = (
            StockEntryItem.objects.filter(stock_entry=entry, is_scrap_item=True)
            .aggregate(total=Sum("qty"))
            .get("total")
            or Decimal("0.000")
        )
        loss = entry.process_loss_qty
        gross = output_qty + loss
        rows.append(
            {
                "stock_entry": entry.name,
                "posting_date": entry.posting_date,
                "work_order": entry.work_order.name if entry.work_order_id else None,
                "bom": entry.bom.name if entry.bom_id else None,
                "item": finished[0].item.code if finished else entry.bom.item.code
                if entry.bom_id
                else None,
                "entry_type": entry.stock_entry_type,
                "produced_qty": output_qty,
                "process_loss_qty": loss,
                "loss_percent": (loss / gross * 100) if gross else Decimal("0"),
                "output_value": output_value.quantize(Decimal("0.01")),
                "unit_cost": (output_value / output_qty).quantize(Decimal("0.000001"))
                if output_qty
                else Decimal("0"),
                "scrap_qty": scrap_value,
                "planned_qty": entry.work_order.qty if entry.work_order_id else None,
            }
        )
        total_output += output_qty
        total_loss += loss

    gross_total = total_output + total_loss
    return Response(
        _jsonable(
            {
                "rows": rows,
                "total_produced_qty": total_output,
                "total_process_loss_qty": total_loss,
                "total_loss_percent": (total_loss / gross_total * 100)
                if gross_total
                else Decimal("0"),
            }
        )
    )


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("manufacturing.BOM")]
)
@_params_400
def bom_cost_report(request):
    """Unit cost of every BOM, plus the last actual cost from a Manufacture.

    Params: company, item, is_active (default 1).
    """
    q = request.query_params
    company = _parse_int(q.get("company"), "company")
    item = _parse_int(q.get("item"), "item")
    boms = BOM.objects.all().select_related("company", "item").order_by("name")
    if company:
        boms = boms.filter(company_id=company)
    if item:
        boms = boms.filter(item_id=item)
    if q.get("is_active", "1") in ("1", "true", "True"):
        boms = boms.filter(is_active=True)

    rows = []
    for bom in boms:
        cost = costing.bom_cost(bom)
        rows.append(
            {
                "bom": bom.name,
                "item": bom.item.code,
                "item_name": bom.item.name,
                "quantity": cost["quantity"],
                "process_loss_percentage": cost["process_loss_percentage"],
                "good_qty": cost["good_qty"],
                "rate_basis": cost["rate_basis"],
                "raw_material_cost": cost["raw_material_cost"],
                "scrap_value": cost["scrap_value"],
                "unit_cost": cost["unit_cost"],
                "last_actual_unit_cost": costing.last_actual_unit_cost(bom),
                "is_active": bom.is_active,
                "is_default": bom.is_default,
            }
        )
    return Response(_jsonable({"rows": rows}))
