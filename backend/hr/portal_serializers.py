"""Serializers of the employee portal (own data only) and of the admin side of
portal advance requests / issues.

Portal outputs are deliberately narrow: no Aadhaar / PAN / bank details, no
internal ids of other documents, no audit actors.
"""

from decimal import Decimal

from rest_framework import serializers

from accounts.models import Account
from accounts.services.gl import quantize_money
from hr.models import (
    AdvanceRequest,
    Attendance,
    EmployeeIssue,
    EmployeePayment,
    IssueCategory,
    IssueStatus,
    SalarySlip,
    SalarySlipDetail,
)
from hr.services import advances as advance_service


# ------------------------------------------------------------- portal: input
class PortalLoginSerializer(serializers.Serializer):
    mobile_no = serializers.CharField(max_length=20)
    pin = serializers.CharField(max_length=12, trim_whitespace=False)


class PortalChangePinSerializer(serializers.Serializer):
    current_pin = serializers.CharField(max_length=12, trim_whitespace=False)
    new_pin = serializers.CharField(max_length=12, trim_whitespace=False)


class SetPortalPinSerializer(serializers.Serializer):
    pin = serializers.CharField(max_length=12, trim_whitespace=False)


class PortalAdvanceRequestCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
        coerce_to_string=False,
    )
    reason = serializers.CharField(max_length=240)


class PortalIssueCreateSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=IssueCategory.choices)
    message = serializers.CharField(max_length=1000)
    reference_date = serializers.DateField(required=False, allow_null=True)
    reference_doc = serializers.CharField(
        max_length=60, required=False, allow_blank=True, allow_null=True
    )


# ------------------------------------------------------------ portal: output
class PortalAttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attendance
        fields = ("date", "status", "ot_hours", "remarks")


class PortalSlipRowSerializer(serializers.ModelSerializer):
    component = serializers.CharField(source="component.name")
    type = serializers.CharField(source="component.component_type")

    class Meta:
        model = SalarySlipDetail
        fields = ("component", "type", "amount", "remarks")


_SLIP_FIELDS = (
    "name",
    "posting_date",
    "start_date",
    "end_date",
    "payment_days",
    "ot_hours",
    "gross_pay",
    "total_deduction",
    "net_pay",
)


class PortalSlipSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalarySlip
        fields = _SLIP_FIELDS


class PortalSlipDetailSerializer(serializers.ModelSerializer):
    rows = PortalSlipRowSerializer(source="details", many=True, read_only=True)

    class Meta:
        model = SalarySlip
        fields = (*_SLIP_FIELDS, "rows")


class PortalPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeePayment
        fields = ("name", "posting_date", "amount", "mode", "reference_no")


class PortalAdvanceSerializer(serializers.Serializer):
    """Submitted advances: ``recovered`` is annotated by the view."""

    name = serializers.CharField()
    posting_date = serializers.DateField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    purpose = serializers.CharField()
    recovered_amount = serializers.SerializerMethodField()
    outstanding = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    def _recovered(self, obj):
        return quantize_money(obj.recovered)

    def get_recovered_amount(self, obj):
        return str(self._recovered(obj))

    def get_outstanding(self, obj):
        return str(obj.amount - self._recovered(obj))

    def get_status(self, obj):
        return advance_service.status_of(obj, self._recovered(obj))


class PortalAdvanceRequestSerializer(serializers.ModelSerializer):
    advance = serializers.CharField(source="advance.name", default=None, read_only=True)
    approved_amount = serializers.SerializerMethodField()

    class Meta:
        model = AdvanceRequest
        fields = (
            "id",
            "amount",
            "approved_amount",
            "reason",
            "status",
            "response",
            "created_at",
            "reviewed_at",
            "advance",
        )

    def get_approved_amount(self, obj):
        return str(obj.advance.amount) if obj.advance_id else None


class PortalIssueSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeIssue
        fields = (
            "id",
            "category",
            "reference_date",
            "reference_doc",
            "message",
            "status",
            "response",
            "created_at",
            "resolved_at",
        )


# ------------------------------------------------------------- admin: output
class AdvanceRequestSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.employee_name", read_only=True
    )
    employee_code = serializers.CharField(source="employee.name", read_only=True)
    advance_name = serializers.CharField(
        source="advance.name", default=None, read_only=True
    )
    reviewed_by_name = serializers.CharField(
        source="reviewed_by.username", default=None, read_only=True
    )

    class Meta:
        model = AdvanceRequest
        fields = (
            "id",
            "employee",
            "employee_code",
            "employee_name",
            "company",
            "amount",
            "reason",
            "status",
            "response",
            "reviewed_by",
            "reviewed_by_name",
            "reviewed_at",
            "advance",
            "advance_name",
            "created_at",
            "modified_at",
        )
        read_only_fields = fields


class EmployeeIssueSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.employee_name", read_only=True
    )
    employee_code = serializers.CharField(source="employee.name", read_only=True)
    resolved_by_name = serializers.CharField(
        source="resolved_by.username", default=None, read_only=True
    )

    class Meta:
        model = EmployeeIssue
        fields = (
            "id",
            "employee",
            "employee_code",
            "employee_name",
            "company",
            "category",
            "reference_date",
            "reference_doc",
            "message",
            "status",
            "response",
            "resolved_by",
            "resolved_by_name",
            "resolved_at",
            "created_at",
            "modified_at",
        )
        read_only_fields = fields


# -------------------------------------------------------------- admin: input
class ApproveAdvanceRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        coerce_to_string=False,
    )
    paid_from = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.all(), required=False
    )
    instalment_amount = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        allow_null=True,
        coerce_to_string=False,
    )
    posting_date = serializers.DateField(required=False)
    response = serializers.CharField(max_length=500, required=False, allow_blank=True)


class RejectAdvanceRequestSerializer(serializers.Serializer):
    response = serializers.CharField(max_length=500)


class ResolveIssueSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[IssueStatus.RESOLVED.value, IssueStatus.REJECTED.value]
    )
    response = serializers.CharField(max_length=1000)
