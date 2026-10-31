from stock.reports.stages import stock_stages_report
import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
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
    ItemAttribute,
    Warehouse,
    Batch,
    PriceList,
    ItemPrice,
    StockLedgerEntry,
    Bin,
    StockEntry,
    StockReconciliation,
)
from stock.services import batches as batch_service
from stock.services import pricing as pricing_service
from stock.services import reconciliation as reconciliation_service
from stock.services import variants as variant_service
from stock.serializers import (
    UOMSerializer,
    ItemGroupSerializer,
    ItemSerializer,
    ItemAttributeSerializer,
    WarehouseSerializer,
    BatchSerializer,
    PriceListSerializer,
    ItemPriceSerializer,
    StockLedgerEntrySerializer,
    BinSerializer,
    StockEntrySerializer,
    StockReconciliationSerializer,
)
from core.company_context import (
    CompanyScopedMixin,
    active_company_id,
    resolve_active_company,
)
from core.mixins import AuditLogMixin, SubmittableViewSetMixin
from core.models import Company
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
        .select_related("item_group", "stock_uom", "item_tax_template", "variant_of")
        .prefetch_related(
            "item_tax_template__rows", "attributes__attribute", "attributes__value"
        )
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
        "has_variants",
        "variant_of",
    ]
    search_fields = ["code", "name", "description", "gst_hsn_code"]
    ordering_fields = ["code", "name", "created_at"]

    @action(detail=True, methods=["post"], url_path="create-variant")
    def create_variant(self, request, pk=None):
        """ERPNext create_variant: make the variant of this template item for
        {"attributes": {"<attribute id>": <value id>, ...}} (a list of
        {"attribute": id, "value": id} is also accepted). The code is the
        template code plus the value abbreviations; optional item_code /
        item_name override. Returns the new item (201)."""
        template = self.get_object()
        raw = request.data.get("attributes")
        if isinstance(raw, list):
            try:
                raw = {row["attribute"]: row["value"] for row in raw}
            except (KeyError, TypeError):
                return Response(
                    {"attributes": _("Each row needs an attribute and a value.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif not isinstance(raw, dict):
            return Response(
                {"attributes": _("Select at least one attribute value.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            variant = variant_service.create_variant(
                template,
                raw,
                item_code=(request.data.get("item_code") or "").strip() or None,
                item_name=(request.data.get("item_name") or "").strip() or None,
            )
        except DjangoValidationError as e:
            return Response({"detail": e.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            self.get_serializer(variant).data, status=status.HTTP_201_CREATED
        )


class ItemAttributeViewSet(AuditLogMixin, viewsets.ModelViewSet):
    """Item Attribute master with its values (used by item templates)."""

    queryset = ItemAttribute.objects.all().prefetch_related("values")
    serializer_class = ItemAttributeSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["disabled"]
    search_fields = ["name", "values__value"]
    ordering_fields = ["name", "created_at"]

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except (ProtectedError, RestrictedError):
            return Response(
                {"detail": _("This attribute is used by items; disable it instead.")},
                status=status.HTTP_409_CONFLICT,
            )


class WarehouseViewSet(CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet):
    queryset = Warehouse.objects.all().select_related("company", "parent")
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "is_group"]
    search_fields = ["name"]


class PriceListViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = PriceList.objects.all()
    serializer_class = PriceListSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["selling", "buying", "enabled", "currency"]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]


class ItemPriceViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = ItemPrice.objects.all().select_related("item", "price_list", "uom")
    serializer_class = ItemPriceSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["item", "price_list", "uom"]
    search_fields = ["item__code", "item__name", "price_list__name"]
    ordering_fields = ["item__code", "valid_from", "rate", "created_at"]


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
    """Batch master. The list doubles as the line-batch picker:
    ``?item=&warehouse=&available=1&posting_date=`` returns only usable batches
    (not disabled, not expired at ``posting_date``, balance > 0) in FIFO order
    (oldest manufactured first, the order the server auto-picks in),
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
            qs = batch_service.fifo_order(
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

    @action(detail=False, methods=["get"], url_path="fifo")
    def fifo(self, request):
        """Preview of the automatic FIFO pick (oldest manufactured first) for
        ?item=&warehouse=&qty=[&posting_date=] -- what a stock-out line left
        without a batch will consume. Nothing is reserved; the binding allocation
        happens under lock when the document is submitted."""
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
            _jsonable(batch_service.fifo(item_id, wh_id, qty, posting_date))
        )

    @action(detail=False, methods=["get"], url_path="for-voucher")
    def for_voucher(self, request):
        """The batches a posted voucher moved, from its live ledger entries
        (?voucher_type=selling.SalesInvoice&voucher_no=...): shows what FIFO
        picked for lines left without a batch."""
        vt = request.query_params.get("voucher_type", "")
        vn = request.query_params.get("voucher_no", "")
        if not vt or not vn:
            return Response(
                {"voucher_no": _("voucher_type and voucher_no are required.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(_jsonable(batch_service.voucher_batches(vt, vn)))

    @action(detail=True, methods=["get"], url_path="balances")
    def balances(self, request, pk=None):
        """Per-warehouse balance of this batch (derived from the ledger)."""
        return Response(_jsonable(batch_service.balances(self.get_object())))

    @action(detail=True, methods=["get"], url_path="trace")
    def trace(self, request, pk=None):
        """Every ledger entry of this batch and the sibling batches moved in the
        same vouchers (recall report)."""
        return Response(_jsonable(batch_service.trace(self.get_object())))


class StockLedgerEntryViewSet(CompanyScopedMixin, viewsets.ReadOnlyModelViewSet):
    """ReadOnlyModelViewSet for immutable StockLedgerEntry facts."""

    company_lookup = "warehouse__company"
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


class BinViewSet(CompanyScopedMixin, viewsets.ReadOnlyModelViewSet):
    """ReadOnlyModelViewSet for derived Bin stock balance projections."""

    company_lookup = "warehouse__company"
    queryset = Bin.objects.all().select_related("item", "warehouse")
    serializer_class = BinSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["item", "warehouse"]
    search_fields = ["item__code", "item__name", "warehouse__name"]
    ordering_fields = ["actual_qty", "stock_value", "modified_at"]


class StockEntryViewSet(
    CompanyScopedMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    queryset = StockEntry.objects.all().prefetch_related("items")
    serializer_class = StockEntrySerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "stock_entry_type", "posting_date", "docstatus"]
    search_fields = ["name"]
    ordering_fields = ["posting_date", "created_at"]


class StockReconciliationViewSet(
    CompanyScopedMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    queryset = StockReconciliation.objects.all().prefetch_related("items")
    serializer_class = StockReconciliationSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "purpose", "posting_date", "docstatus"]
    search_fields = ["name"]
    ordering_fields = ["posting_date", "created_at"]

    @action(detail=False, methods=["get"], url_path="fetch-items")
    def fetch_items(self, request):
        """ERPNext "Fetch Items from Warehouse": current qty / rate of the stock
        items in a warehouse (a group warehouse expands to its leaf warehouses),
        one row per batch for batch-tracked items, to prefill a count sheet.
        Params: ``company`` and ``warehouse`` (required), ``item``,
        ``item_group``, ``include_zero``."""
        q = request.query_params
        try:
            company = resolve_active_company(request)
            warehouse = Warehouse.objects.get(
                pk=int(q.get("warehouse")), company=company
            )
            item = Item.objects.get(pk=int(q["item"])) if q.get("item") else None
            group = (
                ItemGroup.objects.get(pk=int(q["item_group"]))
                if q.get("item_group")
                else None
            )
        except (TypeError, ValueError):
            raise DRFValidationError(
                _("'company' and 'warehouse' are required integers.")
            )
        except (
            Company.DoesNotExist,
            Warehouse.DoesNotExist,
            Item.DoesNotExist,
            ItemGroup.DoesNotExist,
        ):
            return Response(
                {"error": _("Unknown company, warehouse, item or item group.")},
                status=status.HTTP_404_NOT_FOUND,
            )
        if warehouse.company_id != company.pk:
            raise DRFValidationError(
                {"warehouse": _("Warehouse belongs to a different company.")}
            )
        rows = reconciliation_service.fetch_items(
            company,
            warehouse,
            item=item,
            item_group=group,
            include_zero=q.get("include_zero") in ("1", "true", "True"),
        )
        return Response(_jsonable({"count": len(rows), "rows": rows}))


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
        company=active_company_id(request),
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
        company=active_company_id(request),
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

    qs = Bin.objects.filter(warehouse__company_id=active_company_id(request)).select_related(
        "item", "warehouse"
    )
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


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("stock.ItemPrice")]
)
@_params_400
def item_price_rate(request):
    """Resolve the price-list rate for one item (design 5.3).

    Params: ``item`` (required), ``type=selling|buying``, ``company``
    (required), ``customer`` | ``supplier``, ``price_list``,
    ``transaction_date`` and ``uom``.  Missing data is not an error: the rate
    fields come back null so the client can keep its own prefill.
    """
    q = request.query_params
    item_id = _parse_int(q.get("item"), "item", required=True)
    company = resolve_active_company(request)
    item = Item.objects.filter(pk=item_id).select_related("stock_uom").first()
    if item is None:
        raise DRFValidationError({"item": _("Item not found.")})
    selling = q.get("type", "selling") != "buying"
    price_list_id = _parse_int(q.get("price_list"), "price_list")
    price_list = None
    if price_list_id:
        price_list = PriceList.objects.filter(pk=price_list_id).first()
        if price_list is None:
            raise DRFValidationError({"price_list": _("Price List not found.")})
    customer = supplier = None
    if selling:
        customer_id = _parse_int(q.get("customer"), "customer")
        if customer_id:
            from selling.models import Customer

            customer = Customer.objects.filter(pk=customer_id, company=company).first()
            if customer is None:
                raise DRFValidationError({"customer": _("Customer not found.")})
    else:
        supplier_id = _parse_int(q.get("supplier"), "supplier")
        if supplier_id:
            from buying.models import Supplier

            supplier = Supplier.objects.filter(pk=supplier_id, company=company).first()
            if supplier is None:
                raise DRFValidationError({"supplier": _("Supplier not found.")})
    uom_id = _parse_int(q.get("uom"), "uom")
    uom = UOM.objects.filter(pk=uom_id).first() if uom_id else None
    transaction_date = _parse_date(q.get("transaction_date"), "transaction_date")
    result = pricing_service.fetch_rate(
        item,
        selling=selling,
        company=company,
        customer=customer,
        supplier=supplier,
        price_list=price_list,
        transaction_date=transaction_date,
        uom=uom,
    )
    if result["price_list"]:
        pricing_service.validate_list(
            PriceList.objects.get(pk=result["price_list"]), company, selling=selling
        )
    return Response(_jsonable(result))


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("stock.Bin")]
)
def stock_stages_report_view(request):
    """Stock stages report: unprocessed, in-process, ready, total and critical stock level per item."""
    company_val = request.query_params.get("company")
    if company_val:
        try:
            if str(company_val).isdigit():
                company = Company.objects.get(pk=int(company_val))
            else:
                company = Company.objects.get(name=company_val)
        except Company.DoesNotExist:
            raise DRFValidationError({"company": _("Company not found.")})
    else:
        company = resolve_active_company(request)

    rows = stock_stages_report(company)
    total_unprocessed = sum((Decimal(r["unprocessed_qty"]) for r in rows), Decimal("0.000"))
    total_in_process = sum((Decimal(r["in_process_qty"]) for r in rows), Decimal("0.000"))
    total_ready = sum((Decimal(r["ready_qty"]) for r in rows), Decimal("0.000"))
    total_qty = sum((Decimal(r["total_qty"]) for r in rows), Decimal("0.000"))

    return Response(
        _jsonable(
            {
                "rows": rows,
                "total_unprocessed_qty": total_unprocessed,
                "total_in_process_qty": total_in_process,
                "total_ready_qty": total_ready,
                "total_qty": total_qty,
            }
        )
    )
