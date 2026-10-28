import datetime
from decimal import Decimal, InvalidOperation

from django.db.models import ProtectedError, Q, RestrictedError
from django.utils.translation import gettext as _
from rest_framework import status, viewsets, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from stock.models import (
    UOM,
    ItemGroup,
    Item,
    Warehouse,
    Batch,
    StockLedgerEntry,
    Bin,
    StockEntry,
)
from stock.services import batches as batch_service
from stock.serializers import (
    UOMSerializer,
    ItemGroupSerializer,
    ItemSerializer,
    WarehouseSerializer,
    BatchSerializer,
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
        "has_batch_no",
        "has_expiry_date",
    ]
    search_fields = ["code", "name", "description", "gst_hsn_code"]
    ordering_fields = ["code", "name", "created_at"]


class WarehouseViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = Warehouse.objects.all().select_related("company", "parent")
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "is_group"]
    search_fields = ["name"]


def _jsonable(obj):
    """Decimals as strings (the report convention); dates stay for the encoder."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _parse_date(value, name):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise ParamError({name: _("Use YYYY-MM-DD.")})


def _parse_int(value, name, required=False):
    if value in (None, ""):
        if required:
            raise ParamError({name: _("This query parameter is required.")})
        return None
    try:
        return int(value)
    except ValueError:
        raise ParamError({name: _("Must be an integer.")})


class ParamError(Exception):
    """Bad query parameter; rendered as a 400 by ``_params_400``."""

    def __init__(self, detail):
        self.detail = detail


def _params_400(fn):
    """Turn ``ParamError`` raised by a function view into a 400 response."""

    def wrapper(request, *args, **kwargs):
        try:
            return fn(request, *args, **kwargs)
        except ParamError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


class BatchViewSet(AuditLogMixin, viewsets.ModelViewSet):
    """Batch master. The list doubles as the FEFO line-batch picker:
    ``?item=&warehouse=&available=1&posting_date=`` returns only usable batches
    (not disabled, not expired at ``posting_date``, balance > 0) in FEFO order,
    each annotated with ``available_qty`` and ``days_to_expiry``."""

    queryset = Batch.objects.all().select_related("item")
    serializer_class = BatchSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["item", "is_disabled"]
    search_fields = ["batch_no", "supplier_batch_no", "item__code", "item__name"]
    ordering_fields = ["batch_no", "expiry_date", "manufacturing_date", "created_at"]

    def _reference_date(self):
        try:
            return _parse_date(
                self.request.query_params.get("posting_date"), "posting_date"
            )
        except ParamError as e:
            raise DRFValidationError(e.detail)

    def get_queryset(self):
        qs = super().get_queryset()
        try:
            warehouse = _parse_int(
                self.request.query_params.get("warehouse"), "warehouse"
            )
        except ParamError as e:
            raise DRFValidationError(e.detail)
        qs = batch_service.annotate_available(qs, warehouse)
        if self.request.query_params.get("available") in ("1", "true", "True"):
            ref = self._reference_date() or datetime.date.today()
            qs = batch_service.fefo_order(
                qs.filter(is_disabled=False, available_qty__gt=0).filter(
                    Q(expiry_date__isnull=True) | Q(expiry_date__gte=ref)
                )
            )
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if getattr(self, "request", None) is not None:
            ctx["reference_date"] = self._reference_date()
        return ctx

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except (ProtectedError, RestrictedError):
            return Response(
                {
                    "detail": _(
                        "This batch is used by stock movements; disable it instead."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

    @action(detail=False, methods=["get"], url_path="fefo")
    def fefo(self, request):
        """FEFO allocation suggestion for ``?item=&warehouse=&qty=[&posting_date=]``."""
        try:
            item_id = _parse_int(request.query_params.get("item"), "item", True)
            wh_id = _parse_int(request.query_params.get("warehouse"), "warehouse", True)
            posting_date = _parse_date(
                request.query_params.get("posting_date"), "posting_date"
            )
        except ParamError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)
        try:
            qty = Decimal(request.query_params.get("qty", ""))
            if qty <= 0:
                raise InvalidOperation
        except InvalidOperation:
            return Response(
                {"qty": _("A positive quantity is required.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not Item.objects.filter(pk=item_id).exists():
            return Response({"item": _("Unknown item.")}, status=404)
        if not Warehouse.objects.filter(pk=wh_id).exists():
            return Response({"warehouse": _("Unknown warehouse.")}, status=404)
        return Response(
            _jsonable(batch_service.fefo(item_id, wh_id, qty, posting_date))
        )

    @action(detail=True, methods=["get"], url_path="balances")
    def balances(self, request, pk=None):
        """Per-warehouse balance of this batch (derived from the ledger)."""
        return Response(_jsonable(batch_service.balances(self.get_object())))

    @action(detail=True, methods=["get"], url_path="trace")
    def trace(self, request, pk=None):
        """Every ledger entry of this batch and the sibling batches moved in the
        same vouchers (recall report)."""
        return Response(_jsonable(batch_service.trace(self.get_object())))


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
        "batch",
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
@_params_400
def batch_wise_balance(request):
    """Batch-wise balance report: one row per (item, warehouse, batch) from the
    ledger. Params: item, warehouse, batch, company, as_of, include_zero."""
    q = request.query_params
    as_of = _parse_date(q.get("as_of"), "as_of")
    rows = batch_service.balance_rows(
        item=_parse_int(q.get("item"), "item"),
        warehouse=_parse_int(q.get("warehouse"), "warehouse"),
        batch=_parse_int(q.get("batch"), "batch"),
        company=_parse_int(q.get("company"), "company"),
        as_of=as_of,
        include_zero=q.get("include_zero") in ("1", "true", "True"),
    )
    return Response(
        _jsonable(
            {
                "rows": rows,
                "total_balance_qty": sum(
                    (r["balance_qty"] for r in rows), Decimal("0.000")
                ),
                "total_stock_value": sum(
                    (r["stock_value"] for r in rows), Decimal("0.00")
                ),
            }
        )
    )


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("stock.Bin")]
)
@_params_400
def expiry_report(request):
    """Expiry report: batches holding stock that expire within ``within_days``
    (default 60) or already have (``include_expired``, default 1).
    Params: company, warehouse, within_days, include_expired."""
    q = request.query_params
    within = _parse_int(q.get("within_days"), "within_days")
    if within is None:
        within = 60
    if within < 0:
        raise ParamError({"within_days": _("Must not be negative.")})
    rows = batch_service.expiry_rows(
        company=_parse_int(q.get("company"), "company"),
        warehouse=_parse_int(q.get("warehouse"), "warehouse"),
        within_days=within,
        include_expired=q.get("include_expired", "1") not in ("0", "false", "False"),
    )
    return Response(
        _jsonable(
            {
                "rows": rows,
                "total_balance_qty": sum(
                    (r["balance_qty"] for r in rows), Decimal("0.000")
                ),
                "total_stock_value": sum(
                    (r["stock_value"] for r in rows), Decimal("0.00")
                ),
            }
        )
    )


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
