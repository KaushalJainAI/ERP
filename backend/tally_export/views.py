"""Tally export API. Everything here is Accounts Manager territory (RBAC on the
``tally_export.*`` model labels); the XML it returns is the company's books."""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.company_context import CompanyScopedMixin, resolve_active_company
from core.mixins import AuditLogMixin, record_audit_log
from core.models import Company
from tally_export.models import (
    TallyExportBatch,
    TallyExportedVoucher,
    TallyExportSettings,
    TallyLedgerMap,
    TallyStockItemMap,
)
from tally_export.serializers import (
    TallyExportBatchSerializer,
    TallyExportedVoucherSerializer,
    TallyExportSettingsSerializer,
    TallyLedgerMapSerializer,
    TallyStockItemMapSerializer,
)
from tally_export.services import export as export_service
from tally_export.services.collect import pending_counts
from tally_export.services.maps import get_settings, seed_from_legacy
from users.permissions import HasRolePermission


def _company(request) -> Company:
    """The active company; the client cannot pick another entity for an export."""
    return resolve_active_company(request)


class _RangeInput(serializers.Serializer):
    company = serializers.IntegerField(required=False)
    from_date = serializers.DateField(required=False, allow_null=True)
    to_date = serializers.DateField(required=False, allow_null=True)
    create_masters = serializers.BooleanField(required=False, default=False)
    include_exported = serializers.BooleanField(required=False, default=False)


class _Base(CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]


class TallyExportSettingsViewSet(_Base):
    queryset = TallyExportSettings.objects.select_related("company")
    serializer_class = TallyExportSettingsSerializer
    filterset_fields = ["company"]
    http_method_names = ["get", "put", "patch", "head", "options"]

    def list(self, request, *args, **kwargs):
        # Reading a company's settings creates the row with its defaults.
        get_settings(resolve_active_company(request))
        return super().list(request, *args, **kwargs)


class TallyLedgerMapViewSet(_Base):
    queryset = TallyLedgerMap.objects.select_related("account").order_by(
        "account__name", "party"
    )
    serializer_class = TallyLedgerMapSerializer
    filterset_fields = ["company", "account", "party_type", "party", "source"]
    search_fields = ["tally_ledger", "party", "account__name"]
    ordering_fields = ["tally_ledger", "account__name", "modified_at"]

    @action(detail=False, methods=["post"], url_path="seed-from-legacy")
    def seed_from_legacy(self, request):
        counts = seed_from_legacy(_company(request))
        record_audit_log(request.user, "create", TallyLedgerMap(), changes=counts)
        return Response(counts)


class TallyStockItemMapViewSet(_Base):
    queryset = TallyStockItemMap.objects.select_related("item").order_by("item__code")
    serializer_class = TallyStockItemMapSerializer
    filterset_fields = ["company", "item", "source"]
    search_fields = ["tally_item", "item__code", "item__name"]
    ordering_fields = ["tally_item", "item__code", "modified_at"]


class TallyExportBatchViewSet(
    CompanyScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Append-only log: list / read, create (= run an export), download, re-export."""

    queryset = TallyExportBatch.objects.select_related("created_by")
    serializer_class = TallyExportBatchSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "kind"]
    ordering_fields = ["created_at", "id"]

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _download(batch, part=None):
        if part is None:
            part = "zip" if batch.masters_file else "vouchers"
        if part == "zip":
            data, ctype, fname = (
                export_service.batch_zip(batch),
                "application/zip",
                f"tally-export-{batch.pk}.zip",
            )
        elif part == "masters" and batch.masters_file:
            with batch.masters_file.open("rb") as f:
                data = f.read()
            ctype, fname = "application/xml", f"tally-masters-{batch.pk}.xml"
        else:
            with batch.file.open("rb") as f:
                data = f.read()
            ctype, fname = "application/xml", f"tally-vouchers-{batch.pk}.xml"
        resp = HttpResponse(data, content_type=f"{ctype}; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{fname}"'
        resp["X-Tally-Export-Batch"] = str(batch.pk)
        resp["Access-Control-Expose-Headers"] = (
            "X-Tally-Export-Batch, Content-Disposition"
        )
        return resp

    # -- actions ----------------------------------------------------------
    @action(detail=False, methods=["get"])
    def preflight(self, request):
        """Vouchers to export, unmapped ledgers / parties / items, per-voucher Dr = Cr."""
        inp = _RangeInput(data=request.query_params)
        inp.is_valid(raise_exception=True)
        d = inp.validated_data
        return Response(
            export_service.preflight(
                _company(request),
                d.get("from_date"),
                d.get("to_date"),
                d["include_exported"],
            )
        )

    @action(detail=False, methods=["get"])
    def pending(self, request):
        """Submitted vouchers never exported + vouchers cancelled since their export."""
        company = _company(request)
        return Response(pending_counts(company, get_settings(company)))

    def create(self, request, *args, **kwargs):
        inp = _RangeInput(data=request.data)
        inp.is_valid(raise_exception=True)
        d = inp.validated_data
        company = _company(request)
        try:
            batch = export_service.create_export(
                company,
                request.user,
                d.get("from_date"),
                d.get("to_date"),
                create_masters=d["create_masters"],
                include_exported=d["include_exported"],
            )
        except export_service.ExportBlocked:
            body = export_service.preflight(
                company, d.get("from_date"), d.get("to_date"), d["include_exported"]
            )
            return Response({"error": "export_blocked", **body}, status=409)
        except export_service.ExportError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except DjangoValidationError as exc:
            return Response({"error": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return self._download(batch)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        part = request.query_params.get("part")
        if part not in (None, "vouchers", "masters", "zip"):
            raise serializers.ValidationError({"part": "vouchers, masters or zip."})
        return self._download(self.get_object(), part)

    @action(detail=True, methods=["get"])
    def vouchers(self, request, pk=None):
        qs = self.get_object().vouchers.order_by("posting_date", "name")
        return Response(TallyExportedVoucherSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"])
    def reexport(self, request, pk=None):
        """A new batch repeating this batch's vouchers as they stand now."""
        batch = self.get_object()
        try:
            new = export_service.reexport_batch(batch, request.user)
        except export_service.ExportBlocked:
            return Response({"error": "export_blocked"}, status=409)
        except export_service.ExportError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return self._download(new)

    @action(detail=False, methods=["post"], url_path="reexport-vouchers")
    def reexport_vouchers(self, request):
        """Re-export chosen vouchers: ``{"vouchers": [{"doctype", "name"}, ...]}``."""
        company = _company(request)
        rows = request.data.get("vouchers") or []
        if not isinstance(rows, list) or not rows:
            raise serializers.ValidationError(
                {"vouchers": "Pick at least one voucher."}
            )
        try:
            keys = [(r["doctype"], r["name"]) for r in rows]
        except (KeyError, TypeError):
            raise serializers.ValidationError(
                {"vouchers": "Each needs doctype and name."}
            )
        try:
            new = export_service.reexport_vouchers(company, keys, request.user)
        except export_service.ExportBlocked:
            return Response({"error": "export_blocked"}, status=409)
        except export_service.ExportError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return self._download(new)


class TallyExportedVoucherViewSet(CompanyScopedMixin, viewsets.ReadOnlyModelViewSet):
    company_lookup = "batch__company"
    queryset = TallyExportedVoucher.objects.select_related("batch")
    serializer_class = TallyExportedVoucherSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["batch", "doctype", "name", "action", "docstatus"]
    search_fields = ["name"]
    ordering_fields = ["posting_date", "id"]
