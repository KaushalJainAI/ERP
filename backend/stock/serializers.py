import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.exceptions import ValidationError as DRFValidationError
from stock.models import (
    UOM,
    ItemGroup,
    Item,
    Warehouse,
    Batch,
    PriceList,
    ItemPrice,
    StockLedgerEntry,
    Bin,
    StockEntry,
    StockEntryItem,
    StockReconciliation,
    StockReconciliationItem,
)
from stock.services import batches as batch_service
from stock.services import reconciliation as reconciliation_service
from core.mixins import (
    DraftNestedWriteMixin,
    TranslatedSerializerMixin,
    PositiveQtyLineItemMixin,
)


class UOMSerializer(serializers.ModelSerializer):
    class Meta:
        model = UOM
        fields = ("id", "name", "gst_uqc", "created_at", "modified_at")


class ItemGroupSerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True)

    class Meta:
        model = ItemGroup
        fields = (
            "id",
            "name",
            "parent",
            "parent_name",
            "is_group",
            "created_at",
            "modified_at",
        )


class ItemSerializer(TranslatedSerializerMixin, serializers.ModelSerializer):
    item_group_name = serializers.CharField(source="item_group.name", read_only=True)
    stock_uom_name = serializers.CharField(source="stock_uom.name", read_only=True)
    item_tax_template_name = serializers.CharField(
        source="item_tax_template.name", read_only=True, allow_null=True
    )
    item_tax_rate = serializers.SerializerMethodField()

    class Meta:
        model = Item
        fields = (
            "id",
            "code",
            "name",
            "description",
            "item_group",
            "item_group_name",
            "stock_uom",
            "stock_uom_name",
            "is_stock_item",
            "valuation_method",
            "standard_rate",
            "is_active",
            "gst_hsn_code",
            "is_nil_exempt",
            "is_non_gst",
            "item_tax_template",
            "item_tax_template_name",
            "item_tax_rate",
            "has_batch_no",
            "has_expiry_date",
            "shelf_life_in_days",
            "created_at",
            "modified_at",
        )
        translatable_fields = ["name", "description"]

    def get_item_tax_rate(self, obj):
        """``{component: rate}`` from the item's tax template, e.g.
        ``{"CGST": "9.000", "SGST": "9.000", "IGST": "18.000"}`` ({} if none)."""
        template = obj.item_tax_template
        if template is None:
            return {}
        return {r.gst_component: str(r.tax_rate) for r in template.rows.all()}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        inst = self.instance

        def current(name):
            return attrs[name] if name in attrs else getattr(inst, name, False)

        if current("is_nil_exempt") and current("is_non_gst"):
            raise serializers.ValidationError(
                {
                    "is_non_gst": _(
                        "An item cannot be both nil-rated/exempt and non-GST."
                    )
                }
            )

        has_batch = current("has_batch_no")
        has_expiry = current("has_expiry_date")
        shelf_life = (
            attrs["shelf_life_in_days"]
            if "shelf_life_in_days" in attrs
            else getattr(inst, "shelf_life_in_days", None)
        )
        if has_expiry and not has_batch:
            raise serializers.ValidationError(
                {
                    "has_expiry_date": _(
                        "An item that tracks expiry must also be batch-tracked."
                    )
                }
            )
        if shelf_life is not None and not (has_expiry and shelf_life > 0):
            raise serializers.ValidationError(
                {
                    "shelf_life_in_days": _(
                        "Shelf life must be positive and needs 'has expiry date'."
                    )
                }
            )
        # Batch tracking is frozen once the item has ledger entries.
        if inst is not None and (
            has_batch != inst.has_batch_no or has_expiry != inst.has_expiry_date
        ):
            if StockLedgerEntry.objects.filter(item=inst).exists():
                raise serializers.ValidationError(
                    {
                        "has_batch_no": _(
                            "Batch tracking cannot be changed once the item has "
                            "stock ledger entries."
                        )
                    }
                )
        return attrs


class BatchSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    # Derived on read (from the ledger); in one warehouse when the list is
    # filtered by ``warehouse``, else across all warehouses.
    available_qty = serializers.DecimalField(
        max_digits=18, decimal_places=3, read_only=True, default=None
    )
    days_to_expiry = serializers.SerializerMethodField()

    class Meta:
        model = Batch
        fields = (
            "id",
            "batch_no",
            "item",
            "item_code",
            "item_name",
            "manufacturing_date",
            "expiry_date",
            "supplier_batch_no",
            "reference_doctype",
            "reference_name",
            "description",
            "is_disabled",
            "available_qty",
            "days_to_expiry",
            "created_at",
            "modified_at",
        )
        read_only_fields = ("reference_doctype", "reference_name")
        extra_kwargs = {"batch_no": {"required": False}}

    def get_days_to_expiry(self, obj):
        if not obj.expiry_date:
            return None
        ref = self.context.get("reference_date") or datetime.date.today()
        return (obj.expiry_date - ref).days

    def validate(self, attrs):
        attrs = super().validate(attrs)
        inst = self.instance

        def cur(name):
            return attrs[name] if name in attrs else getattr(inst, name, None)

        item = cur("item")
        if not item.has_batch_no:
            raise serializers.ValidationError(
                {
                    "item": _("Item '%(code)s' is not batch-tracked.")
                    % {"code": item.code}
                }
            )
        if inst is not None and item.pk != inst.item_id:
            if StockLedgerEntry.objects.filter(batch=inst).exists():
                raise serializers.ValidationError(
                    {"item": _("The item of a batch cannot change once it has stock.")}
                )
        mfg, exp = cur("manufacturing_date"), cur("expiry_date")
        if inst is None:
            # New batch: apply the shelf-life default / expiry rules.
            try:
                mfg, exp = batch_service.resolve_dates(item, mfg, exp)
            except DjangoValidationError as e:
                raise serializers.ValidationError({"expiry_date": e.messages})
            attrs["manufacturing_date"], attrs["expiry_date"] = mfg, exp
        else:
            if exp and not item.has_expiry_date:
                raise serializers.ValidationError(
                    {"expiry_date": _("Item does not track expiry.")}
                )
            if item.has_expiry_date and not exp:
                raise serializers.ValidationError(
                    {"expiry_date": _("This item tracks expiry; a date is required.")}
                )
            if exp and mfg and exp < mfg:
                raise serializers.ValidationError(
                    {"expiry_date": _("Expiry cannot be before manufacturing.")}
                )
        return attrs

    def create(self, validated_data):
        batch_no = validated_data.get("batch_no")
        try:
            return batch_service.create_batch(
                validated_data["item"],
                batch_no=batch_no,
                manufacturing_date=validated_data.get("manufacturing_date"),
                expiry_date=validated_data.get("expiry_date"),
                supplier_batch_no=validated_data.get("supplier_batch_no", ""),
                description=validated_data.get("description", ""),
                numbering_date=datetime.date.today(),
            )
        except DjangoValidationError as e:
            raise serializers.ValidationError({"batch_no": e.messages})

    def update(self, instance, validated_data):
        # The batch number is fixed after creation.
        if (
            "batch_no" in validated_data
            and validated_data["batch_no"] != instance.batch_no
        ):
            raise serializers.ValidationError(
                {"batch_no": _("The batch number cannot be changed.")}
            )
        return super().update(instance, validated_data)


class WarehouseSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    parent_name = serializers.CharField(source="parent.name", read_only=True)
    account_name = serializers.CharField(
        source="account.name", read_only=True, default=None
    )

    class Meta:
        model = Warehouse
        fields = (
            "id",
            "name",
            "company",
            "company_name",
            "parent",
            "parent_name",
            "is_group",
            "account",
            "account_name",
            "created_at",
            "modified_at",
        )


class PriceListSerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceList
        fields = (
            "id",
            "name",
            "currency",
            "selling",
            "buying",
            "enabled",
            "created_at",
            "modified_at",
        )


class ItemPriceSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    price_list_name = serializers.CharField(source="price_list.name", read_only=True)
    uom_name = serializers.CharField(source="uom.name", read_only=True, default=None)

    class Meta:
        model = ItemPrice
        fields = (
            "id",
            "item",
            "item_code",
            "item_name",
            "price_list",
            "price_list_name",
            "uom",
            "uom_name",
            "rate",
            "valid_from",
            "valid_upto",
            "created_at",
            "modified_at",
        )
        # The model's unique constraints are *partial*; DRF would otherwise turn
        # them into a global unique-together validator and refuse several UOM or
        # date rows.  The database still enforces the intended uniqueness.
        validators = []


class StockLedgerEntrySerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_name = serializers.CharField(
        source="warehouse.name", read_only=True, allow_null=True
    )
    batch_no = serializers.CharField(
        source="batch.batch_no", read_only=True, default=None
    )

    class Meta:
        model = StockLedgerEntry
        fields = (
            "id",
            "posting_date",
            "posting_time",
            "item",
            "item_code",
            "item_name",
            "warehouse",
            "warehouse_name",
            "qty",
            "valuation_rate",
            "stock_value_difference",
            "voucher_type",
            "voucher_no",
            "is_cancelled",
            "batch",
            "batch_no",
            "created_at",
        )
        read_only_fields = fields


class BinSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_name = serializers.CharField(
        source="warehouse.name", read_only=True, allow_null=True
    )

    class Meta:
        model = Bin
        fields = (
            "id",
            "item",
            "item_code",
            "item_name",
            "warehouse",
            "warehouse_name",
            "actual_qty",
            "valuation_rate",
            "stock_value",
            "modified_at",
        )
        read_only_fields = fields


class StockEntryItemSerializer(PositiveQtyLineItemMixin, serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    source_warehouse_name = serializers.CharField(
        source="source_warehouse.name", read_only=True, allow_null=True
    )
    target_warehouse_name = serializers.CharField(
        source="target_warehouse.name", read_only=True, allow_null=True
    )

    batch_no = serializers.CharField(
        source="batch.batch_no", read_only=True, default=None
    )

    def validate_valuation_rate(self, value):
        if value is not None and value < 0:
            raise DRFValidationError("Valuation rate cannot be negative.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        batch = attrs.get("batch")
        item = attrs.get("item")
        if batch is not None and item is not None:
            if batch.item_id != item.pk:
                raise DRFValidationError(
                    {"batch": _("Batch does not belong to this item.")}
                )
            if not item.has_batch_no:
                raise DRFValidationError(
                    {
                        "batch": _("Item '%(code)s' is not batch-tracked.")
                        % {"code": item.code}
                    }
                )
        new_fields = bool(
            attrs.get("supplier_batch_no")
            or attrs.get("manufacturing_date")
            or attrs.get("expiry_date")
        )
        if batch is not None and new_fields:
            raise DRFValidationError(
                _("Pick an existing batch or enter new-batch details, not both.")
            )
        mfg, exp = attrs.get("manufacturing_date"), attrs.get("expiry_date")
        if mfg and exp and exp < mfg:
            raise DRFValidationError(
                {"expiry_date": _("Expiry cannot be before manufacturing.")}
            )
        return attrs

    class Meta:
        model = StockEntryItem
        fields = (
            "id",
            "item",
            "item_code",
            "item_name",
            "source_warehouse",
            "source_warehouse_name",
            "target_warehouse",
            "target_warehouse_name",
            "qty",
            "valuation_rate",
            "batch",
            "batch_no",
            "supplier_batch_no",
            "manufacturing_date",
            "expiry_date",
            "is_finished_item",
            "is_scrap_item",
            "cost_allocation_per",
        )


class StockEntrySerializer(DraftNestedWriteMixin, serializers.ModelSerializer):
    children_field = "items"
    name_prefix = "STE-2026-"

    items = StockEntryItemSerializer(many=True)

    class Meta:
        model = StockEntry
        fields = (
            "id",
            "name",
            "stock_entry_type",
            "posting_date",
            "posting_time",
            "company",
            "work_order",
            "bom",
            "fg_completed_qty",
            "process_loss_qty",
            "docstatus",
            "amended_from",
            "items",
            "created_at",
            "modified_at",
        )
        read_only_fields = (
            "id",
            "fg_completed_qty",
            "process_loss_qty",
            "docstatus",
            "created_at",
            "modified_at",
        )


class StockReconciliationItemSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    batch_no = serializers.CharField(
        source="batch.batch_no", read_only=True, default=None
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        item = attrs.get("item")
        batch = attrs.get("batch")
        warehouse = attrs.get("warehouse")
        qty, rate = attrs.get("qty"), attrs.get("valuation_rate")
        if qty is None and rate is None:
            raise DRFValidationError(
                _("Please specify either Quantity or Valuation Rate or both.")
            )
        if qty is not None and qty < 0:
            raise DRFValidationError({"qty": _("Negative Quantity is not allowed.")})
        if rate is not None and rate < 0:
            raise DRFValidationError(
                {"valuation_rate": _("Negative Valuation Rate is not allowed.")}
            )
        if warehouse is not None and warehouse.is_group:
            raise DRFValidationError(
                {"warehouse": _("Select a leaf warehouse, not a group warehouse.")}
            )
        if item is not None:
            if not item.is_stock_item:
                raise DRFValidationError(
                    {
                        "item": _("Item '%(code)s' is not a stock item.")
                        % {"code": item.code}
                    }
                )
            if batch is None and item.has_batch_no:
                raise DRFValidationError(
                    {
                        "batch": _(
                            "Item '%(code)s' is batch-tracked: enter the batch counted."
                        )
                        % {"code": item.code}
                    }
                )
            if batch is not None and not item.has_batch_no:
                raise DRFValidationError(
                    {
                        "batch": _("Item '%(code)s' is not batch-tracked.")
                        % {"code": item.code}
                    }
                )
            if batch is not None and batch.item_id != item.pk:
                raise DRFValidationError(
                    {"batch": _("Batch does not belong to this item.")}
                )
        return attrs

    class Meta:
        model = StockReconciliationItem
        fields = (
            "id",
            "item",
            "item_code",
            "item_name",
            "warehouse",
            "warehouse_name",
            "batch",
            "batch_no",
            "qty",
            "valuation_rate",
            "current_qty",
            "current_valuation_rate",
            "quantity_difference",
            "amount",
            "current_amount",
            "amount_difference",
        )
        read_only_fields = (
            "current_qty",
            "current_valuation_rate",
            "quantity_difference",
            "amount",
            "current_amount",
            "amount_difference",
        )
        extra_kwargs = {
            "qty": {"required": False, "allow_null": True},
            "valuation_rate": {"required": False, "allow_null": True},
        }


class StockReconciliationSerializer(DraftNestedWriteMixin, serializers.ModelSerializer):
    children_field = "items"
    name_prefix = "MAT-RECO-2026-"

    items = StockReconciliationItemSerializer(many=True)
    expense_account_name = serializers.CharField(
        source="expense_account.name", read_only=True, default=None
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance
        company = attrs.get("company") or (instance.company if instance else None)
        purpose = attrs.get("purpose") or (
            instance.purpose
            if instance
            else StockReconciliation._meta.get_field("purpose").default
        )
        if "expense_account" in attrs:
            account = attrs["expense_account"]
        else:
            account = instance.expense_account if instance else None
        if account is None and company is not None:
            # ERPNext defaults the difference account to the Stock Adjustment account.
            account = company.stock_adjustment_account
            attrs["expense_account"] = account
        if company is not None:
            try:
                reconciliation_service.check_difference_account(
                    company, purpose, account
                )
            except DjangoValidationError as exc:
                raise DRFValidationError(
                    exc.message_dict if hasattr(exc, "error_dict") else exc.messages
                )

        rows = attrs.get("items")
        if rows is not None:
            seen = set()
            for index, row in enumerate(rows, start=1):
                key = (
                    row["item"].pk,
                    row["warehouse"].pk,
                    row["batch"].pk if row.get("batch") else None,
                )
                if key in seen:
                    raise DRFValidationError(
                        {
                            "items": _(
                                "Row #%(row)s: Same item and warehouse combination "
                                "already entered."
                            )
                            % {"row": index}
                        }
                    )
                seen.add(key)
                if company is not None and row["warehouse"].company_id != company.pk:
                    raise DRFValidationError(
                        {
                            "items": _(
                                "Row #%(row)s: Warehouse '%(name)s' belongs to a "
                                "different company."
                            )
                            % {"row": index, "name": row["warehouse"].name}
                        }
                    )
        return attrs

    def refresh_totals(self, instance):
        """Snapshot current qty/rate and the differences (server-owned)."""
        reconciliation_service.refresh_draft(instance)
        instance._prefetched_objects_cache = {}

    class Meta:
        model = StockReconciliation
        fields = (
            "id",
            "name",
            "company",
            "posting_date",
            "posting_time",
            "purpose",
            "expense_account",
            "expense_account_name",
            "difference_amount",
            "docstatus",
            "amended_from",
            "items",
            "created_at",
            "modified_at",
        )
        read_only_fields = (
            "id",
            "difference_amount",
            "docstatus",
            "created_at",
            "modified_at",
        )
