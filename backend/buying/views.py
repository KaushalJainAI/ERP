from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from buying.models import (
    SupplierGroup,
    Supplier,
    ExpenseCategory,
    RecurringExpense,
    PurchaseOrder,
    PurchaseInvoice,
    PurchaseChargeTemplate,
)
from buying.serializers import (
    SupplierGroupSerializer,
    SupplierSerializer,
    ExpenseCategorySerializer,
    RecurringExpenseSerializer,
    PurchaseOrderSerializer,
    PurchaseInvoiceSerializer,
    PurchaseChargeTemplateSerializer,
)
from accounts.models import Account, TaxTemplate
from accounts.serializers import PaymentEntrySerializer
from buying import mapping
from buying.services.expenses import create_expense
from core.company_context import CompanyScopedMixin
from core.mixins import (
    AuditLogMixin,
    MappedDocumentActionMixin,
    SubmittableViewSetMixin,
)
from core.models import Company, DocStatus
from stock.services import pricing as pricing_service
from users.permissions import HasRolePermission


class SupplierGroupViewSet(AuditLogMixin, viewsets.ModelViewSet):
    queryset = SupplierGroup.objects.all()
    serializer_class = SupplierGroupSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    search_fields = ["name"]


class SupplierViewSet(CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet):
    queryset = Supplier.objects.all().select_related(
        "company", "supplier_group", "state"
    )
    serializer_class = SupplierSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = [
        "company",
        "supplier_group",
        "is_active",
        "gst_category",
        "state",
    ]
    search_fields = ["name", "gstin", "email", "phone"]
    ordering_fields = ["name", "created_at"]


class ExpenseCategoryViewSet(CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet):
    queryset = ExpenseCategory.objects.all().select_related(
        "company", "account", "item"
    )
    serializer_class = ExpenseCategorySerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "is_active"]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]


class RecurringExpenseViewSet(CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet):
    queryset = RecurringExpense.objects.all().select_related(
        "company", "category", "supplier"
    )
    serializer_class = RecurringExpenseSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "is_active", "category", "supplier"]
    search_fields = ["category__name", "supplier__name", "description", "remarks"]
    ordering_fields = ["day_of_month", "amount", "created_at"]


class PurchaseChargeTemplateViewSet(
    CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet
):
    """Presets for the extra-charge rows of a purchase bill (Freight, Hamali...)."""

    queryset = PurchaseChargeTemplate.objects.all().select_related("account")
    serializer_class = PurchaseChargeTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    action_permission_flags = {"ensure_defaults": "can_create"}
    filterset_fields = ["company", "is_active"]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]

    @action(detail=False, methods=["post"], url_path="ensure-defaults")
    def ensure_defaults(self, request):
        """Create the standard charge accounts and templates of the active company
        (idempotent; for companies that were not set up by ``seed_defaults``)."""
        from buying.services.charges import ensure_charge_setup

        ensure_charge_setup(self.active_company)
        rows = self.get_queryset().order_by("name")
        return Response(self.get_serializer(rows, many=True).data)


class PurchaseOrderViewSet(
    CompanyScopedMixin,
    MappedDocumentActionMixin,
    SubmittableViewSetMixin,
    viewsets.ModelViewSet,
):
    action_permission_flags = {
        "make_purchase_invoice": "can_read",
        "update_rates": "can_write",
    }
    queryset = (
        PurchaseOrder.objects.all()
        .select_related("supplier", "company", "place_of_supply", "taxes_and_charges")
        .prefetch_related("items", "taxes")
    )
    serializer_class = PurchaseOrderSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "supplier", "docstatus"]
    search_fields = ["name", "supplier__name"]
    ordering_fields = ["transaction_date", "created_at"]

    @action(detail=True, methods=["post"], url_path="make-purchase-invoice")
    def make_purchase_invoice(self, request, pk=None):
        """Draft Purchase Invoice for the not-yet-invoiced quantity of this submitted order."""
        return self.map_document(
            request,
            mapping.make_purchase_invoice,
            "buying.PurchaseInvoice",
            PurchaseInvoiceSerializer,
        )

    def _update_rates(self, request):
        doc = self.get_object()
        if doc.docstatus != DocStatus.DRAFT:
            return Response(
                {"error": _("Rates can only be refreshed on a draft.")}, status=409
            )
        changed = pricing_service.reapply_rates(doc)
        doc.calculate_taxes_and_totals()
        doc.save()
        data = self.get_serializer(doc).data
        return Response({"changed": changed, "document": data})

    @action(detail=True, methods=["post"], url_path="update-rates")
    def update_rates(self, request, pk=None):
        return self._update_rates(request)


class PurchaseInvoiceViewSet(
    CompanyScopedMixin,
    MappedDocumentActionMixin,
    SubmittableViewSetMixin,
    viewsets.ModelViewSet,
):
    action_permission_flags = {
        "make_payment_entry": "can_read",
        "make_purchase_return": "can_read",
        "update_rates": "can_write",
    }
    queryset = (
        PurchaseInvoice.objects.all()
        .select_related("supplier", "company", "place_of_supply", "taxes_and_charges")
        .prefetch_related("items", "taxes", "charges")
    )
    serializer_class = PurchaseInvoiceSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = [
        "company",
        "supplier",
        "docstatus",
        "update_stock",
        "is_return",
        "return_against",
    ]
    search_fields = ["name", "supplier__name", "bill_no"]
    ordering_fields = ["posting_date", "created_at", "bill_date"]

    def _update_rates(self, request):
        doc = self.get_object()
        if doc.docstatus != DocStatus.DRAFT:
            return Response(
                {"error": _("Rates can only be refreshed on a draft.")}, status=409
            )
        if doc.is_return:
            return Response(
                {"error": _("A return keeps the rates of the original invoice.")},
                status=409,
            )
        changed = pricing_service.reapply_rates(doc)
        doc.calculate_taxes_and_totals()
        doc.save()
        data = self.get_serializer(doc).data
        return Response({"changed": changed, "document": data})

    @action(detail=True, methods=["post"], url_path="update-rates")
    def update_rates(self, request, pk=None):
        return self._update_rates(request)

    @action(detail=True, methods=["post"], url_path="make-purchase-return")
    def make_purchase_return(self, request, pk=None):
        """Draft Purchase Return (debit note) for the not-yet-returned quantity of this
        submitted invoice (negative quantities)."""
        return self.map_document(
            request,
            mapping.make_purchase_return,
            "buying.PurchaseInvoice",
            PurchaseInvoiceSerializer,
        )

    @action(detail=True, methods=["post"], url_path="make-payment-entry")
    def make_payment_entry(self, request, pk=None):
        """Draft 'Pay' Payment Entry for this submitted invoice's outstanding amount."""
        return self.map_document(
            request,
            mapping.make_payment_entry,
            "accounts.PaymentEntry",
            PaymentEntrySerializer,
        )


class ExpenseViewSet(CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet):
    queryset = (
        PurchaseInvoice.objects.filter(is_expense=True)
        .select_related(
            "supplier",
            "company",
            "expense_category",
            "place_of_supply",
            "taxes_and_charges",
            "credit_to",
            "expense_account",
        )
        .prefetch_related("items", "taxes", "charges")
    )
    serializer_class = PurchaseInvoiceSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "expense_category", "docstatus"]
    search_fields = ["name", "supplier__name", "bill_no"]
    ordering_fields = ["posting_date", "created_at"]

    def get_queryset(self):
        qs = super().get_queryset().filter(is_expense=True)
        month = self.request.query_params.get("month")
        if month:
            try:
                parts = month.split("-")
                year, m = int(parts[0]), int(parts[1])
                qs = qs.filter(posting_date__year=year, posting_date__month=m)
            except (ValueError, IndexError):
                pass

        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(expense_category_id=category)

        status_param = self.request.query_params.get(
            "status"
        ) or self.request.query_params.get("payment_status")
        if status_param:
            st = status_param.strip().lower()
            today = timezone.localdate()
            if st == "paid":
                qs = qs.filter(outstanding_amount=0, docstatus=DocStatus.SUBMITTED)
            elif st == "overdue":
                qs = qs.filter(
                    outstanding_amount__gt=0,
                    due_date__lt=today,
                    docstatus=DocStatus.SUBMITTED,
                )
            elif st == "due":
                qs = qs.filter(outstanding_amount__gt=0).filter(
                    Q(due_date__gte=today) | Q(due_date__isnull=True),
                    docstatus=DocStatus.SUBMITTED,
                )
            elif st == "draft":
                qs = qs.filter(docstatus=DocStatus.DRAFT)
            elif st == "cancelled":
                qs = qs.filter(docstatus=DocStatus.CANCELLED)

        return qs.order_by("-posting_date", "-created_at")

    def create(self, request, *args, **kwargs):
        data = request.data
        company_id = data.get("company")
        if company_id:
            try:
                company = Company.objects.get(pk=company_id)
            except Company.DoesNotExist:
                return Response({"company": [_("Company not found.")]}, status=400)
        else:
            company = getattr(self, "active_company", None)
            if not company:
                return Response({"company": [_("Company is required.")]}, status=400)

        category_id = data.get("category")
        if not category_id:
            return Response(
                {"category": [_("Expense category is required.")]}, status=400
            )
        try:
            category = ExpenseCategory.objects.get(pk=category_id)
        except ExpenseCategory.DoesNotExist:
            return Response(
                {"category": [_("Expense category not found.")]}, status=400
            )

        supplier_id = data.get("supplier")
        supplier = None
        if supplier_id:
            try:
                supplier = Supplier.objects.get(pk=supplier_id)
            except Supplier.DoesNotExist:
                return Response({"supplier": [_("Supplier not found.")]}, status=400)
        else:
            supplier = Supplier.objects.filter(
                company=company, is_cash_expense=True
            ).first()
            if not supplier:
                return Response({"supplier": [_("Supplier is required.")]}, status=400)

        amount_raw = data.get("amount")
        if amount_raw is None or str(amount_raw).strip() == "":
            return Response({"amount": [_("Amount is required.")]}, status=400)
        try:
            amount = Decimal(str(amount_raw))
        except Exception:
            return Response({"amount": [_("Invalid amount.")]}, status=400)

        if amount <= Decimal("0.00"):
            return Response(
                {"amount": [_("Expense amount must be greater than zero.")]},
                status=400,
            )

        tax_template_id = data.get("tax_template")
        tax_template = None
        if tax_template_id:
            try:
                tax_template = TaxTemplate.objects.get(pk=tax_template_id)
            except TaxTemplate.DoesNotExist:
                return Response(
                    {"tax_template": [_("Tax template not found.")]}, status=400
                )

        paid_from_id = data.get("paid_from")
        paid_from = None
        if paid_from_id:
            try:
                paid_from = Account.objects.get(pk=paid_from_id)
            except Account.DoesNotExist:
                return Response(
                    {"paid_from": [_("Paid from account not found.")]}, status=400
                )

        has_gst = data.get("has_gst", False)
        if isinstance(has_gst, str):
            has_gst = has_gst.lower() in ("true", "1", "yes")
        else:
            has_gst = bool(has_gst)

        paid_now = data.get("paid_now", False)
        if isinstance(paid_now, str):
            paid_now = paid_now.lower() in ("true", "1", "yes")
        else:
            paid_now = bool(paid_now)

        bill_no = str(data.get("bill_no", "")).strip()
        date = data.get("date") or None
        due_date = data.get("due_date") or None
        proof = request.FILES.get("proof") or request.FILES.get("file")

        try:
            invoice, pe = create_expense(
                company=company,
                category=category,
                supplier=supplier,
                amount=amount,
                date=date,
                has_gst=has_gst,
                tax_template=tax_template,
                bill_no=bill_no,
                paid_now=paid_now,
                paid_from=paid_from,
                due_date=due_date,
                user=request.user,
                proof=proof,
            )
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                return Response(exc.message_dict, status=400)
            return Response(
                {"error": exc.messages if hasattr(exc, "messages") else str(exc)},
                status=400,
            )

        serializer = self.get_serializer(invoice)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
