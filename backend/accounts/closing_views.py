"""HTTP API for the Period Closing Voucher (and the fiscal years it closes)."""

from django.utils.translation import gettext as _
from rest_framework import permissions, serializers, viewsets

from accounts.models import PeriodClosingVoucher
from accounts.services import closing
from core.company_context import CompanyScopedMixin
from core.mixins import SubmittableViewSetMixin
from core.models import FiscalYear, NamingSeries
from users.permissions import HasRolePermission


class PeriodClosingVoucherSerializer(serializers.ModelSerializer):
    fiscal_year_name = serializers.CharField(source="fiscal_year.name", read_only=True)
    closing_account_head_name = serializers.CharField(
        source="closing_account_head.name", read_only=True
    )

    class Meta:
        model = PeriodClosingVoucher
        fields = (
            "id",
            "name",
            "company",
            "fiscal_year",
            "fiscal_year_name",
            "period_end_date",
            "closing_account_head",
            "closing_account_head_name",
            "remarks",
            "net_profit",
            "docstatus",
            "amended_from",
            "created_at",
            "modified_at",
        )
        read_only_fields = (
            "id",
            "net_profit",
            "docstatus",
            "created_at",
            "modified_at",
        )
        extra_kwargs = {
            "name": {"required": False},
            # Defaults: the fiscal year's last day and the company's Retained Earnings.
            "period_end_date": {"required": False},
            "closing_account_head": {"required": False},
        }

    def validate(self, attrs):
        inst = self.instance
        company = attrs.get("company") or getattr(inst, "company", None)
        fy = attrs.get("fiscal_year") or getattr(inst, "fiscal_year", None)
        if "period_end_date" not in attrs and inst is None and fy is not None:
            attrs["period_end_date"] = fy.end_date
        if not attrs.get("closing_account_head") and inst is None:
            head = closing.ensure_closing_account(company) if company else None
            if head is None:
                raise serializers.ValidationError(
                    {
                        "closing_account_head": _(
                            "Choose the closing account (Retained Earnings / Reserves)."
                        )
                    }
                )
            attrs["closing_account_head"] = head
        # Early feedback; the same rules are enforced again on submit.
        probe = PeriodClosingVoucher(
            company=company,
            fiscal_year=fy,
            period_end_date=attrs.get("period_end_date")
            or getattr(inst, "period_end_date", None),
            closing_account_head=attrs.get("closing_account_head")
            or getattr(inst, "closing_account_head", None),
        )
        head = probe.closing_account_head
        if head is not None:
            if head.company_id != company.pk:
                raise serializers.ValidationError(
                    {
                        "closing_account_head": _(
                            "The closing account must belong to the company."
                        )
                    }
                )
            if head.is_group or head.root_type not in ("Liability", "Equity"):
                raise serializers.ValidationError(
                    {
                        "closing_account_head": _(
                            "The closing account must be a non-group Liability / Equity account."
                        )
                    }
                )
        end = probe.period_end_date
        if (
            fy is not None
            and end is not None
            and not (fy.start_date < end <= fy.end_date)
        ):
            raise serializers.ValidationError(
                {
                    "period_end_date": _(
                        "The period end date must fall within the fiscal year."
                    )
                }
            )
        return attrs

    def create(self, validated_data):
        if not validated_data.get("name"):
            validated_data["name"] = NamingSeries.next(
                f"PCV-{validated_data['fiscal_year'].name}-"
            )
        return super().create(validated_data)


class PeriodClosingVoucherViewSet(
    CompanyScopedMixin, SubmittableViewSetMixin, viewsets.ModelViewSet
):
    queryset = PeriodClosingVoucher.objects.all().select_related(
        "fiscal_year", "closing_account_head"
    )
    serializer_class = PeriodClosingVoucherSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "fiscal_year", "docstatus"]
    search_fields = ["name", "remarks"]
    ordering_fields = ["period_end_date", "created_at"]


class FiscalYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = FiscalYear
        fields = ("id", "name", "start_date", "end_date", "is_closed")
        read_only_fields = fields


class FiscalYearViewSet(viewsets.ReadOnlyModelViewSet):
    """Fiscal years (global), newest first -- the picker of the Period Closing Voucher."""

    queryset = FiscalYear.objects.all().order_by("-start_date")
    serializer_class = FiscalYearSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["is_closed"]
    search_fields = ["name"]
