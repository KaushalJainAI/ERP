"""Payment Reconciliation API (Accounts Manager territory).

List what can be matched for a party, preview an oldest-first allocation, post the
reconciliation entry, and unreconcile (cancel) it. Read-only otherwise: the entry's
rows are produced by the service, never typed by a client.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from core.company_context import CompanyScopedMixin
from core.mixins import AuditLogMixin, record_audit_log
from payment_reconciliation import services
from payment_reconciliation.models import PaymentReconciliation
from payment_reconciliation.serializers import (
    AllocatePreviewSerializer,
    PartyQuerySerializer,
    PaymentReconciliationSerializer,
    ReconcileSerializer,
)
from users.permissions import HasRolePermission


def _messages(exc):
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    return exc.messages if hasattr(exc, "messages") else str(exc)


class PaymentReconciliationViewSet(
    CompanyScopedMixin, AuditLogMixin, viewsets.ReadOnlyModelViewSet
):
    queryset = (
        PaymentReconciliation.objects.select_related("party_account")
        .prefetch_related("allocations")
        .order_by("-posting_date", "-id")
    )
    serializer_class = PaymentReconciliationSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["party_type", "party_name", "docstatus", "posting_date"]
    search_fields = ["name", "party_name"]
    #: Previewing an allocation only reads; unreconciling is a cancel.
    action_permission_flags = {"allocate": "can_read", "unreconcile": "can_cancel"}

    @action(detail=False, methods=["get"], url_path="unreconciled")
    def unreconciled(self, request):
        q = PartyQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        v = q.validated_data
        try:
            data = services.unreconciled(
                self.active_company,
                v["party_type"],
                v["party"],
                v.get("from_date"),
                v.get("to_date"),
                v["include_imported"],
            )
        except DjangoValidationError as exc:
            raise ValidationError({"error": _messages(exc)})
        return Response(data)

    @action(detail=False, methods=["post"], url_path="allocate")
    def allocate(self, request):
        s = AllocatePreviewSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        try:
            data = services.preview_allocation(
                self.active_company,
                v["party_type"],
                v["party"],
                [dict(p) for p in v["payments"]],
                [dict(i) for i in v["invoices"]],
            )
        except DjangoValidationError as exc:
            raise ValidationError({"error": _messages(exc)})
        return Response(data)

    @action(detail=False, methods=["post"], url_path="reconcile")
    def reconcile(self, request):
        s = ReconcileSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        try:
            doc = services.reconcile(
                self.active_company,
                v["party_type"],
                v["party"],
                [dict(a) for a in v["allocations"]],
                posting_date=v.get("posting_date"),
                remarks=v.get("remarks", ""),
                user=request.user,
            )
        except DjangoValidationError as exc:
            return Response(
                {"error": _messages(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        record_audit_log(request.user, "submit", doc)
        return Response(self.get_serializer(doc).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="unreconcile")
    def unreconcile(self, request, pk=None):
        doc = self.get_object()
        try:
            doc.cancel(user=request.user)
        except DjangoValidationError as exc:
            return Response(
                {"error": _messages(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        record_audit_log(request.user, "cancel", doc)
        return Response(self.get_serializer(doc).data)
