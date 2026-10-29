"""Admin side of the employee portal: advance requests, issues and the overview.

Plain RBAC routes (``IsAuthenticated`` + ``HasRolePermission`` on the model named
in ``queryset``).  The collections are read-only; the only writes are the audited
decision actions below, which need ``can_write`` on the model.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from hr.models import AdvanceRequest, AdvanceRequestStatus, EmployeeIssue, IssueStatus
from hr.portal_serializers import (
    AdvanceRequestSerializer,
    ApproveAdvanceRequestSerializer,
    EmployeeIssueSerializer,
    RejectAdvanceRequestSerializer,
    ResolveIssueSerializer,
)
from hr.portal_views import _validation_response
from hr.services import portal_requests
from hr.views import _require
from users.permissions import HasRolePermission

PERMS = [permissions.IsAuthenticated, HasRolePermission]


class AdvanceRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """List / retrieve portal advance requests; ``approve`` / ``reject`` decide."""

    queryset = AdvanceRequest.objects.all().select_related(
        "employee", "advance", "reviewed_by"
    )
    serializer_class = AdvanceRequestSerializer
    permission_classes = PERMS
    filterset_fields = ["status", "employee", "company"]
    search_fields = ["employee__name", "employee__employee_name", "reason"]
    ordering_fields = ["created_at", "amount", "status"]
    # Deciding a request edits it; it needs ``can_write``, not ``can_create``.
    action_permission_flags = {"approve": "can_write", "reject": "can_write"}

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Create the DRAFT advance (the admin submits it to pay out) and link it."""
        _require(request, "hr.AdvanceRequest", "can_write")
        req = self.get_object()
        ser = ApproveAdvanceRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            req = portal_requests.approve_advance_request(
                req,
                request.user,
                amount=data.get("amount"),
                paid_from=data.get("paid_from"),
                instalment_amount=data.get("instalment_amount"),
                posting_date=data.get("posting_date"),
                response=data.get("response", ""),
            )
        except DjangoValidationError as exc:
            return _validation_response(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=req.pk)).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        _require(request, "hr.AdvanceRequest", "can_write")
        req = self.get_object()
        ser = RejectAdvanceRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            req = portal_requests.reject_advance_request(
                req, request.user, ser.validated_data["response"]
            )
        except DjangoValidationError as exc:
            return _validation_response(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=req.pk)).data)


class EmployeeIssueViewSet(viewsets.ReadOnlyModelViewSet):
    """List / retrieve issues raised from the portal; ``resolve`` closes one."""

    queryset = EmployeeIssue.objects.all().select_related("employee", "resolved_by")
    serializer_class = EmployeeIssueSerializer
    permission_classes = PERMS
    filterset_fields = ["status", "category", "employee", "company"]
    search_fields = ["employee__name", "employee__employee_name", "message"]
    ordering_fields = ["created_at", "status"]
    action_permission_flags = {"resolve": "can_write"}

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        _require(request, "hr.EmployeeIssue", "can_write")
        issue = self.get_object()
        ser = ResolveIssueSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            issue = portal_requests.resolve_issue(
                issue,
                request.user,
                ser.validated_data["status"],
                ser.validated_data["response"],
            )
        except DjangoValidationError as exc:
            return _validation_response(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=issue.pk)).data)


class PortalOverviewView(APIView):
    """GET ``/api/hr/portal-overview/`` -- counts for a dashboard badge."""

    permission_classes = PERMS
    model_label = "hr.AdvanceRequest"

    def get(self, request):
        _require(request, "hr.EmployeeIssue", "can_read")
        return Response(
            {
                "pending_advance_requests": AdvanceRequest.objects.filter(
                    status=AdvanceRequestStatus.PENDING
                ).count(),
                "open_issues": EmployeeIssue.objects.filter(
                    status=IssueStatus.OPEN
                ).count(),
            },
            status=status.HTTP_200_OK,
        )
