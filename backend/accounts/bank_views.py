"""HTTP API for Bank Account, statement import, reconciliation and the BRS report.

Everything is company-scoped through the active company (``X-Company-ID``): a bank
account, statement line or clearance of another company is invisible (404/400).
"""

import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import (
    action,
    api_view,
    parser_classes,
    permission_classes,
)
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from accounts.models import (
    Account,
    BankAccount,
    BankClearance,
    BankStatementImport,
    BankTransaction,
    JournalEntry,
    JournalEntryLine,
)
from accounts.serializers import JournalEntrySerializer
from accounts.services import bank as bank_service
from accounts.services import bank_import
from core.company_context import CompanyScopedMixin, resolve_active_company
from core.mixins import AuditLogMixin, MappedDocumentActionMixin, record_audit_log
from core.models import NamingSeries
from users.permissions import HasRolePermission, require_model_permission

ZERO = "0.00"


def _django_errors(exc):
    if hasattr(exc, "error_dict"):
        return exc.message_dict
    return {"error": exc.messages}


def _date(request, name, required=False, source=None):
    raw = (source if source is not None else request.query_params).get(name)
    if not raw:
        if required:
            raise ValidationError({name: _("This field is required.")})
        return None
    parsed = None
    if isinstance(raw, datetime.date):
        parsed = raw
    else:
        try:
            parsed = parse_date(str(raw))
        except ValueError:
            parsed = None
    if parsed is None:
        raise ValidationError({name: _("Enter a valid date (YYYY-MM-DD).")})
    return parsed


def _bank_account(request, value):
    """The active company's bank account, or 400/404."""
    company = resolve_active_company(request)
    if value in (None, ""):
        raise ValidationError({"bank_account": _("This field is required.")})
    try:
        return BankAccount.objects.select_related("account").get(
            pk=int(value), company=company
        )
    except (ValueError, TypeError):
        raise ValidationError({"bank_account": _("Must be a bank account id.")})
    except BankAccount.DoesNotExist:
        raise NotFound(_("Bank account not found."))


def _vouchers_payload(raw):
    if not isinstance(raw, list) or not raw:
        raise ValidationError({"vouchers": _("Choose at least one voucher.")})
    out = []
    for item in raw:
        if (
            not isinstance(item, dict)
            or not item.get("voucher_type")
            or not item.get("voucher_no")
        ):
            raise ValidationError(
                {"vouchers": _("Each voucher needs a voucher_type and voucher_no.")}
            )
        out.append(item)
    return out


# ------------------------------------------------------------------ Bank Account


class BankAccountSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source="account.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    account_number = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=40
    )
    account_number_last4 = serializers.CharField(read_only=True)

    class Meta:
        model = BankAccount
        fields = (
            "id",
            "company",
            "company_name",
            "account",
            "account_name",
            "bank_name",
            "account_number",
            "account_number_last4",
            "ifsc_code",
            "is_default",
            "is_active",
            "reconciled_up_to",
            "created_at",
            "modified_at",
        )
        read_only_fields = ("id", "created_at", "modified_at")

    def validate(self, attrs):
        probe = BankAccount(
            company=attrs.get("company", getattr(self.instance, "company", None)),
            account=attrs.get("account", getattr(self.instance, "account", None)),
            ifsc_code=(
                attrs["ifsc_code"].strip().upper()
                if "ifsc_code" in attrs
                else getattr(self.instance, "ifsc_code", "")
            ),
        )
        try:
            probe.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_django_errors(exc))
        if "ifsc_code" in attrs:
            attrs["ifsc_code"] = probe.ifsc_code
        return attrs

    def _unset_other_defaults(self, instance):
        if instance.is_default:
            BankAccount.objects.filter(company_id=instance.company_id).exclude(
                pk=instance.pk
            ).update(is_default=False)

    def create(self, validated_data):
        with transaction.atomic():
            if validated_data.get("is_default"):
                BankAccount.objects.filter(
                    company=validated_data["company"], is_default=True
                ).update(is_default=False)
            return super().create(validated_data)

    def update(self, instance, validated_data):
        with transaction.atomic():
            if validated_data.get("is_default"):
                self._unset_other_defaults(
                    BankAccount(
                        pk=instance.pk, company_id=instance.company_id, is_default=True
                    )
                )
            return super().update(instance, validated_data)


class BankAccountViewSet(CompanyScopedMixin, AuditLogMixin, viewsets.ModelViewSet):
    queryset = BankAccount.objects.all().select_related("account", "company")
    serializer_class = BankAccountSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["company", "is_default", "is_active"]
    search_fields = ["bank_name", "account__name"]
    ordering_fields = ["bank_name", "created_at"]

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if (
            instance.transactions.exists()
            or instance.clearances.exists()
            or instance.statement_imports.exists()
        ):
            return Response(
                {
                    "error": _(
                        "This bank account has statement or reconciliation history; "
                        "deactivate it instead."
                    )
                },
                status=409,
            )
        return super().destroy(request, *args, **kwargs)


# ------------------------------------------------------------------ transactions


class BankTransactionSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    bank_account_name = serializers.CharField(
        source="bank_account.account.name", read_only=True
    )
    reconciled = serializers.SerializerMethodField()
    matched_vouchers = serializers.SerializerMethodField()

    class Meta:
        model = BankTransaction
        fields = (
            "id",
            "company",
            "bank_account",
            "bank_account_name",
            "statement_import",
            "row_no",
            "transaction_date",
            "description",
            "reference_no",
            "withdrawal",
            "deposit",
            "amount",
            "balance",
            "reconciled",
            "matched_vouchers",
        )
        read_only_fields = fields

    def _links(self, obj):
        cache = self.context.setdefault("_bank_links", {})
        if obj.bank_account_id not in cache:
            cache[obj.bank_account_id] = bank_service.transaction_links(
                bank_service.cleared_map(obj.bank_account)
            )
        return cache[obj.bank_account_id].get(obj.pk, [])

    def get_reconciled(self, obj):
        return bool(self._links(obj))

    def get_matched_vouchers(self, obj):
        return [{"voucher_type": k[0], "voucher_no": k[1]} for k in self._links(obj)]


class BankTransactionViewSet(
    CompanyScopedMixin, MappedDocumentActionMixin, viewsets.ReadOnlyModelViewSet
):
    """Imported statement lines (immutable; written only by the import)."""

    queryset = BankTransaction.objects.all().select_related(
        "bank_account", "bank_account__account"
    )
    serializer_class = BankTransactionSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = {
        "bank_account": ["exact"],
        "transaction_date": ["exact", "gte", "lte"],
    }
    search_fields = ["description", "reference_no"]
    ordering_fields = ["transaction_date", "id"]
    action_permission_flags = {"make_journal_entry": "can_read"}

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        wanted = self.request.query_params.get("status")
        if wanted in ("reconciled", "unreconciled"):
            matched = set()
            for ba in BankAccount.objects.filter(
                pk__in=queryset.values_list("bank_account_id", flat=True).distinct()
            ):
                matched |= set(
                    bank_service.transaction_links(bank_service.cleared_map(ba))
                )
            queryset = (
                queryset.filter(pk__in=matched)
                if wanted == "reconciled"
                else queryset.exclude(pk__in=matched)
            )
        return queryset

    @action(detail=True, methods=["post"], url_path="make-journal-entry")
    def make_journal_entry(self, request, pk=None):
        """Draft Journal Entry for a bank-only line (charges, interest): the bank
        leg plus the chosen counter account. The user reviews and submits it; once
        submitted it matches this line like any voucher."""
        account_id = request.data.get("account")

        def build(tx):
            return _draft_journal_for(tx, account_id)

        return self.map_document(
            request, build, "accounts.JournalEntry", JournalEntrySerializer
        )


def _draft_journal_for(tx, account_id):
    try:
        counter = Account.objects.get(pk=int(account_id), company_id=tx.company_id)
    except (TypeError, ValueError, Account.DoesNotExist):
        raise DjangoValidationError(_("Choose the account to book the amount to."))
    if counter.is_group or not counter.is_active:
        raise DjangoValidationError(_("Choose an active, non-group account."))
    if counter.pk == tx.bank_account.account_id:
        raise DjangoValidationError(_("The counter account must differ from the bank."))
    if bank_service.transaction_links(bank_service.cleared_map(tx.bank_account)).get(
        tx.pk
    ):
        raise DjangoValidationError(_("This bank transaction is already reconciled."))
    amount = abs(tx.amount)
    deposit = tx.amount > 0
    with transaction.atomic():
        je = JournalEntry.objects.create(
            name=NamingSeries.next("JV-2026-"),
            posting_date=tx.transaction_date,
            company_id=tx.company_id,
            voucher_type="Bank Entry",
            cheque_no=tx.reference_no or None,
            cheque_date=tx.transaction_date,
            user_remark=(tx.description or "")[:1000] or None,
        )
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=tx.bank_account.account,
            debit=amount if deposit else 0,
            credit=0 if deposit else amount,
        )
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=counter,
            debit=0 if deposit else amount,
            credit=amount if deposit else 0,
        )
        je.recompute_totals()
        je.save(update_fields=["total_debit", "total_credit"])
    return je


class BankClearanceSerializer(serializers.ModelSerializer):
    set_by_name = serializers.CharField(
        source="set_by.username", read_only=True, default=None
    )

    class Meta:
        model = BankClearance
        fields = (
            "id",
            "company",
            "bank_account",
            "voucher_type",
            "voucher_no",
            "clearance_date",
            "bank_transaction",
            "remarks",
            "set_by",
            "set_by_name",
            "set_at",
        )
        read_only_fields = fields


class BankClearanceViewSet(CompanyScopedMixin, viewsets.ReadOnlyModelViewSet):
    """The append-only clearance history (who cleared / un-cleared what, when)."""

    queryset = BankClearance.objects.all().select_related("set_by")
    serializer_class = BankClearanceSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = {
        "bank_account": ["exact"],
        "voucher_type": ["exact"],
        "voucher_no": ["exact"],
        "bank_transaction": ["exact"],
    }
    ordering_fields = ["id", "set_at"]


# ------------------------------------------------------------------ import


class BankStatementImportSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankStatementImport
        fields = (
            "id",
            "company",
            "bank_account",
            "file_name",
            "column_mapping",
            "row_count",
            "imported_count",
            "duplicate_count",
            "skipped_count",
            "from_date",
            "to_date",
            "imported_by",
            "imported_at",
        )
        read_only_fields = fields


class BankStatementImportViewSet(CompanyScopedMixin, viewsets.ReadOnlyModelViewSet):
    """Uploaded statements. ``preview`` reads a CSV and proposes the column mapping;
    ``import-statement`` stores its lines (needs ``can_create``)."""

    queryset = BankStatementImport.objects.all()
    serializer_class = BankStatementImportSerializer
    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    filterset_fields = ["bank_account"]
    ordering_fields = ["imported_at", "id"]

    def _file(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": _("Choose a CSV file.")})
        if upload.size > bank_import.MAX_BYTES:
            raise ValidationError({"file": _("The file is too large (limit 5 MB).")})
        return upload.name, upload.read()

    @action(
        detail=False,
        methods=["post"],
        url_path="preview",
        parser_classes=[MultiPartParser, FormParser],
    )
    def preview(self, request):
        _name, raw = self._file(request)
        try:
            return Response(bank_import.preview(raw))
        except DjangoValidationError as exc:
            raise ValidationError({"file": exc.messages})

    @action(
        detail=False,
        methods=["post"],
        url_path="import-statement",
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_statement(self, request):
        import json

        bank_account = _bank_account(request, request.data.get("bank_account"))
        name, raw = self._file(request)
        try:
            mapping = json.loads(request.data.get("mapping") or "{}")
        except ValueError:
            raise ValidationError({"mapping": _("Invalid column mapping.")})
        if not isinstance(mapping, dict):
            raise ValidationError({"mapping": _("Invalid column mapping.")})
        header_row = request.data.get("header_row")
        try:
            header_row = int(header_row) if header_row not in (None, "") else None
        except ValueError:
            raise ValidationError({"header_row": _("Must be a number.")})
        try:
            result = bank_import.run_import(
                request.user,
                bank_account,
                name,
                raw,
                mapping,
                date_format=request.data.get("date_format") or None,
                header_row=header_row,
            )
        except DjangoValidationError as exc:
            raise ValidationError(_django_errors(exc))
        if result["import_id"]:
            record_audit_log(
                request.user,
                "import",
                BankStatementImport.objects.get(pk=result["import_id"]),
                changes=result,
            )
        return Response(result, status=201 if result["imported"] else 200)


# ------------------------------------------------------------------ reconciliation


def _money(value):
    return None if value is None else str(value)


def _tx_payload(tx):
    return {
        "id": tx.pk,
        "transaction_date": tx.transaction_date.isoformat(),
        "description": tx.description,
        "reference_no": tx.reference_no,
        "withdrawal": _money(tx.withdrawal),
        "deposit": _money(tx.deposit),
        "amount": _money(tx.amount),
        "balance": _money(tx.balance),
    }


def _leg_payload(leg):
    return {
        "voucher_type": leg["voucher_type"],
        "voucher_no": leg["voucher_no"],
        "posting_date": leg["posting_date"].isoformat(),
        "party": leg["party"],
        "reference_no": leg["reference_no"],
        "reference_date": (
            leg["reference_date"].isoformat() if leg["reference_date"] else None
        ),
        "amount": _money(leg["net"]),
    }


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("accounts.BankTransaction")]
)
def bank_reconciliation(request):
    """The reconciliation tool's data: unreconciled statement lines in
    ``from_date``..``to_date``, uncleared vouchers on the bank ledger (posted up to
    ``to_date``; ``voucher_from_date`` optionally bounds them below) and suggested
    matches (same signed amount, then reference / cheque no or date within
    ``date_window`` days)."""
    bank_account = _bank_account(request, request.query_params.get("bank_account"))
    from_date = _date(request, "from_date")
    to_date = _date(request, "to_date")
    try:
        window = int(
            request.query_params.get("date_window", bank_service.DEFAULT_DATE_WINDOW)
        )
    except ValueError:
        raise ValidationError({"date_window": _("Must be a number of days.")})
    transactions = bank_service.unreconciled_transactions(
        bank_account, from_date, to_date
    )
    vouchers = bank_service.uncleared_vouchers(
        bank_account, to_date, _date(request, "voucher_from_date")
    )
    suggestions = bank_service.suggest_matches(transactions, vouchers, window)
    return Response(
        {
            "bank_account": bank_account.pk,
            "bank_account_name": bank_account.account.name,
            "from_date": from_date,
            "to_date": to_date,
            "transactions": [_tx_payload(t) for t in transactions],
            "vouchers": [_leg_payload(v) for v in vouchers],
            "suggestions": [
                {
                    "bank_transaction": s["bank_transaction"],
                    "voucher_type": s["voucher"]["voucher_type"],
                    "voucher_no": s["voucher"]["voucher_no"],
                    "basis": s["basis"],
                }
                for s in suggestions
            ],
        }
    )


def _write_view(request, runner):
    try:
        return runner()
    except DjangoValidationError as exc:
        raise ValidationError(_django_errors(exc))


@api_view(["POST"])
@permission_classes(
    [
        permissions.IsAuthenticated,
        require_model_permission("accounts.BankClearance", "can_create"),
    ]
)
@parser_classes([JSONParser])
def bank_match(request):
    """Confirm a match: ``{bank_transaction, vouchers: [{voucher_type, voucher_no}]}``.
    The vouchers' bank legs must add up to the line's amount; each gets the line's
    date as its clearance date."""
    company = resolve_active_company(request)
    tx = (
        BankTransaction.objects.filter(
            pk=request.data.get("bank_transaction") or 0, company=company
        )
        .select_related("bank_account", "bank_account__account")
        .first()
    )
    if tx is None:
        raise NotFound(_("Bank transaction not found."))
    vouchers = _vouchers_payload(request.data.get("vouchers"))
    rows = _write_view(request, lambda: bank_service.match(request.user, tx, vouchers))
    return Response(
        {"bank_transaction": tx.pk, "cleared": [r.pk for r in rows]}, status=201
    )


@api_view(["POST"])
@permission_classes(
    [
        permissions.IsAuthenticated,
        require_model_permission("accounts.BankClearance", "can_create"),
    ]
)
@parser_classes([JSONParser])
def bank_accept_suggestions(request):
    """Confirm all suggested matches of a period:
    ``{bank_account, from_date?, to_date?, date_window?, only_reference?}``.
    Returns ``{matched, skipped: [{bank_transaction, reason}]}``."""
    data = request.data
    bank_account = _bank_account(request, data.get("bank_account"))
    dates = {}
    for key in ("from_date", "to_date"):
        raw = data.get(key)
        if raw:
            try:
                dates[key] = datetime.date.fromisoformat(str(raw))
            except ValueError:
                raise ValidationError({key: _("Enter a valid date (YYYY-MM-DD).")})
    try:
        window = int(data.get("date_window", bank_service.DEFAULT_DATE_WINDOW))
    except (TypeError, ValueError):
        raise ValidationError({"date_window": _("Must be a number of days.")})
    result = _write_view(
        request,
        lambda: bank_service.accept_suggestions(
            request.user,
            bank_account,
            dates.get("from_date"),
            dates.get("to_date"),
            window,
            bool(data.get("only_reference")),
        ),
    )
    return Response(result)


@api_view(["POST"])
@permission_classes(
    [
        permissions.IsAuthenticated,
        require_model_permission("accounts.BankClearance", "can_create"),
    ]
)
@parser_classes([JSONParser])
def bank_unmatch(request):
    """Undo a match: ``{bank_transaction}`` un-clears every voucher linked to it."""
    company = resolve_active_company(request)
    tx = (
        BankTransaction.objects.filter(
            pk=request.data.get("bank_transaction") or 0, company=company
        )
        .select_related("bank_account", "bank_account__account")
        .first()
    )
    if tx is None:
        raise NotFound(_("Bank transaction not found."))
    rows = _write_view(request, lambda: bank_service.unmatch(request.user, tx))
    return Response({"bank_transaction": tx.pk, "uncleared": [r.pk for r in rows]})


@api_view(["POST"])
@permission_classes(
    [
        permissions.IsAuthenticated,
        require_model_permission("accounts.BankClearance", "can_create"),
    ]
)
@parser_classes([JSONParser])
def bank_clear(request):
    """Manual clearance without a statement line (ERPNext Bank Clearance):
    ``{bank_account, vouchers: [{voucher_type, voucher_no, clearance_date|null}]}``;
    a null date un-clears."""
    bank_account = _bank_account(request, request.data.get("bank_account"))
    items = _vouchers_payload(request.data.get("vouchers"))
    for item in items:
        item["clearance_date"] = _date(
            request,
            "clearance_date",
            source={"clearance_date": item.get("clearance_date")},
        )
    rows = _write_view(
        request, lambda: bank_service.set_clearance(request.user, bank_account, items)
    )
    return Response(
        {"bank_account": bank_account.pk, "records": [r.pk for r in rows]}, status=201
    )


@api_view(["GET"])
@permission_classes(
    [permissions.IsAuthenticated, require_model_permission("accounts.BankAccount")]
)
def bank_reconciliation_statement(request):
    """Bank Reconciliation Statement: balance per GL - uncleared deposits + uncleared
    payments = balance per bank, with the imported statement's balance for comparison.
    """
    bank_account = _bank_account(request, request.query_params.get("bank_account"))
    as_on = _date(request, "as_on") or datetime.date.today()
    report = bank_service.reconciliation_statement(bank_account, as_on)

    def lines(rows):
        return [
            {
                **r,
                "posting_date": r["posting_date"].isoformat(),
                "reference_date": (
                    r["reference_date"].isoformat() if r["reference_date"] else None
                ),
                "amount": str(r["amount"]),
            }
            for r in rows
        ]

    return Response(
        {
            "bank_account": bank_account.pk,
            "bank_account_name": bank_account.account.name,
            "as_on": as_on.isoformat(),
            "gl_balance": str(report["gl_balance"]),
            "uncleared_receipts": lines(report["uncleared_receipts"]),
            "uncleared_receipts_total": str(report["uncleared_receipts_total"]),
            "uncleared_payments": lines(report["uncleared_payments"]),
            "uncleared_payments_total": str(report["uncleared_payments_total"]),
            "bank_balance": str(report["bank_balance"]),
            "statement_balance": _money(report["statement_balance"]),
            "statement_balance_date": (
                report["statement_balance_date"].isoformat()
                if report["statement_balance_date"]
                else None
            ),
            "difference": _money(report["difference"]),
        }
    )
