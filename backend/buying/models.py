from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from core.models import (
    Company,
    GSTCategory,
    State,
    SubmittableDocument,
    TimeStampedModel,
)
from gst.services import posting
from gst.validators import validate_gstin_format
from accounts.models import (
    Account,
    PayableDocument,
    TaxableDocument,
    TaxableLine,
    TaxLine,
)
from stock.models import Batch, Item, Warehouse


class SupplierGroup(TimeStampedModel):
    """Supplier Group tree node."""

    name = models.CharField(max_length=200, unique=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="children",
    )
    is_group = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Supplier(TimeStampedModel):
    """Supplier master record."""

    name = models.CharField(max_length=200)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="suppliers"
    )
    supplier_group = models.ForeignKey(
        SupplierGroup, on_delete=models.PROTECT, related_name="suppliers"
    )
    # GSTIN is validated at the API layer (validators + serializer), never in
    # save() or the DB: ORM/import writers may store legacy values.
    gstin = models.CharField(
        max_length=20, null=True, blank=True, validators=[validate_gstin_format]
    )
    gst_category = models.CharField(
        max_length=24,
        choices=GSTCategory.choices,
        default=GSTCategory.UNREGISTERED,
    )
    # The party's location; also the default place of supply.
    state = models.ForeignKey(
        State, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    tax_id = models.CharField(max_length=40, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(max_length=30, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    default_price_list = models.ForeignKey(
        "stock.PriceList",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"], name="unique_supplier_per_company"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.company.abbr})"


class PurchaseOrder(TaxableDocument, SubmittableDocument):
    """Purchase Order document."""

    GST_DIRECTION = "Purchase"
    party_field = "supplier"

    class Meta(TaxableDocument.Meta):
        pass

    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name="purchase_orders"
    )
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="purchase_orders"
    )
    transaction_date = models.DateField()
    schedule_date = models.DateField(null=True, blank=True)
    grand_total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    price_list = models.ForeignKey(
        "stock.PriceList",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    def validate_before_submit(self):
        items = self.items.all()
        if not items.exists():
            raise ValidationError("Purchase Order must have at least one line item.")
        self.calculate_taxes_and_totals(for_submit=True)

    def on_submit(self):
        pass  # Submitting reserves purchase intent

    def on_cancel(self):
        pass

    def __str__(self):
        return f"{self.name} - {self.supplier.name}"


class PurchaseOrderItem(TaxableLine):
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="items"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="po_items")
    warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT
    )
    qty = models.DecimalField(max_digits=18, decimal_places=3)
    rate = models.DecimalField(max_digits=18, decimal_places=2)
    price_list_rate = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )

    def save(self, *args, **kwargs):
        self.amount = self.compute_amount()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.purchase_order.name} - {self.item.code} ({self.qty} @ {self.rate})"
        )


class PurchaseInvoice(PayableDocument, TaxableDocument, SubmittableDocument):
    """Purchase Invoice document."""

    GST_DIRECTION = "Purchase"
    party_field = "supplier"
    PARTY_TYPE = "Supplier"
    PARTY_ACCOUNT_FIELD = "credit_to"
    OUTSTANDING_SIGN = -1  # the party line is a credit

    class Meta(TaxableDocument.Meta):
        constraints = [
            *TaxableDocument.Meta.constraints,
            PayableDocument.outstanding_constraint(),
        ]

    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name="purchase_invoices"
    )
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="purchase_invoices"
    )
    posting_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoices",
    )
    update_stock = models.BooleanField(default=True)
    credit_to = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="creditor_invoices"
    )
    expense_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="expense_invoices"
    )
    grand_total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    price_list = models.ForeignKey(
        "stock.PriceList",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    def validate_before_submit(self):
        items = self.items.all()
        if not items.exists():
            raise ValidationError("Purchase Invoice must have at least one line item.")
        if self.credit_to.is_group or self.expense_account.is_group:
            raise ValidationError("Posting to group accounts is not allowed.")
        # Multi-tenant integrity: keep the posting inside this invoice's company.
        if (
            self.credit_to.company_id != self.company_id
            or self.expense_account.company_id != self.company_id
        ):
            raise ValidationError(
                "Credit-to and expense accounts must belong to the invoice's company."
            )
        if self.supplier.company_id != self.company_id:
            raise ValidationError("Supplier must belong to the invoice's company.")
        if self.update_stock:
            self.company.get_perpetual_accounts()  # raises if perpetual is on but misconfigured
        self._validate_batch_lines()
        self.calculate_taxes_and_totals(for_submit=True)
        posting.validate_posting(self)

    def _validate_batch_lines(self):
        """Batch rules at the submit gate (``ledger.post`` re-asserts them centrally)."""
        from stock.services import batches as batch_service

        for line in self.items.all():
            item = line.item
            new_fields = bool(
                line.supplier_batch_no or line.manufacturing_date or line.expiry_date
            )
            if not self.update_stock:
                if line.batch_id or new_fields:
                    raise ValidationError(
                        _(
                            "Purchase Invoice '%(name)s' does not update stock: remove "
                            "the batch details from item '%(code)s'."
                        )
                        % {"name": self.name, "code": item.code}
                    )
                continue
            if not item.is_stock_item:
                continue
            if not item.has_batch_no:
                if line.batch_id or new_fields:
                    raise ValidationError(
                        _(
                            "Item '%(code)s' is not batch-tracked; remove the batch "
                            "details."
                        )
                        % {"code": item.code}
                    )
                continue
            if line.batch_id:
                if new_fields:
                    raise ValidationError(
                        _(
                            "Item '%(code)s': pick an existing batch or enter "
                            "new-batch details, not both."
                        )
                        % {"code": item.code}
                    )
                if line.batch.item_id != item.pk:
                    raise ValidationError(
                        _(
                            "Item '%(code)s': batch '%(batch)s' belongs to a "
                            "different item."
                        )
                        % {"code": item.code, "batch": line.batch.batch_no}
                    )
                continue
            # A new batch is created at submit: refuse now if it cannot be dated.
            batch_service.resolve_dates(
                item,
                line.manufacturing_date,
                line.expiry_date,
                default_manufacturing_date=self.posting_date,
            )

    def on_submit(self):
        from accounts.services import gl as gl_service
        from accounts.services import outstanding as outstanding_service
        from stock.services import batches as batch_service
        from stock.services import ledger as stock_service

        posting.validate_posting(self)
        perpetual = self.company.get_perpetual_accounts() if self.update_stock else None

        # 1. Post stock first (stock lines only) so GL can book the value actually
        #    posted to the SLE, keeping GL inventory == Bin.stock_value exactly.
        stock_value = Decimal("0.00")
        if self.update_stock:
            sle_list = []
            for item in self.items.all():
                if not item.item.is_stock_item:
                    continue
                if not item.warehouse:
                    raise ValidationError(
                        f"Warehouse required for item '{item.item.code}' when update_stock is enabled."
                    )
                batch = item.batch
                if item.item.has_batch_no and batch is None:
                    batch = batch_service.create_batch(
                        item.item,
                        manufacturing_date=item.manufacturing_date,
                        expiry_date=item.expiry_date,
                        supplier_batch_no=item.supplier_batch_no,
                        default_manufacturing_date=self.posting_date,
                        numbering_date=self.posting_date,
                        reference_doctype=f"{self._meta.app_label}.{self._meta.object_name}",
                        reference_name=self.name,
                    )
                    item.batch = batch
                    item.save(update_fields=["batch"])
                sle_list.append(
                    {
                        "item": item.item,
                        "warehouse": item.warehouse,
                        "qty": abs(item.qty),
                        "valuation_rate": item.rate,
                        "batch": batch,
                    }
                )
            if sle_list:
                sles = stock_service.post(
                    self, sle_list, posting_date=self.posting_date
                )
                stock_value = sum(
                    (s.stock_value_difference for s in sles), Decimal("0.00")
                )

        # 2. Post GL Entries: Debit Expense (and, with perpetual inventory, Stock In
        #    Hand for the stock value) for the net total -- recoverable input tax is
        #    never part of it -- Debit each Input tax row (Cr a Deduct row), the
        #    reverse-charge pairs and the round-off, Credit Creditors for the full
        #    rounded total (one party line).
        gl_entries = []
        if perpetual and stock_value:
            gl_entries.append(
                {
                    "account": perpetual["default_inventory_account"],
                    "debit": stock_value,
                    "credit": Decimal("0.00"),
                }
            )
            # Non-stock lines plus any rounding difference stay on the expense account.
            expense_amount = self.net_total - stock_value
        else:
            expense_amount = self.net_total
        if expense_amount >= 0:
            gl_entries.append(
                {
                    "account": self.expense_account,
                    "debit": expense_amount,
                    "credit": Decimal("0.00"),
                }
            )
        else:
            gl_entries.append(
                {
                    "account": self.expense_account,
                    "debit": Decimal("0.00"),
                    "credit": -expense_amount,
                }
            )
        gl_entries.append(
            {
                "account": self.credit_to,
                "debit": Decimal("0.00"),
                "credit": self.rounded_total,
                "party_type": "Supplier",
                "party": self.supplier.name,
                "against_voucher_type": self.voucher_label(),
                "against_voucher_no": self.name,
            }
        )
        gl_entries.extend(posting.purchase_tax_entries(self))
        gl_service.post(self, gl_entries, posting_date=self.posting_date)
        outstanding_service.refresh(self)

    def on_cancel(self):
        from accounts.services import gl as gl_service
        from accounts.services import outstanding as outstanding_service
        from stock.services import ledger as stock_service

        self.ensure_no_linked_payments()
        gl_service.reverse(self)
        if self.update_stock:
            stock_service.reverse(self)
        outstanding_service.refresh(self)

    def __str__(self):
        return f"{self.name} - {self.supplier.name} ({self.grand_total})"


class PurchaseInvoiceItem(TaxableLine):
    purchase_invoice = models.ForeignKey(
        PurchaseInvoice, on_delete=models.CASCADE, related_name="items"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="pi_items")
    warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT
    )
    qty = models.DecimalField(max_digits=18, decimal_places=3)
    rate = models.DecimalField(max_digits=18, decimal_places=2)
    price_list_rate = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    # Batch tracking (only when ``update_stock``): pick an existing ``batch`` OR
    # fill the new-batch fields and the batch is created at submit.
    batch = models.ForeignKey(
        Batch,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pi_items",
    )
    supplier_batch_no = models.CharField(max_length=100, blank=True, default="")
    manufacturing_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    class Meta(TaxableLine.Meta):
        constraints = [
            *TaxableLine.Meta.constraints,
            models.CheckConstraint(
                check=models.Q(expiry_date__isnull=True)
                | models.Q(manufacturing_date__isnull=True)
                | models.Q(expiry_date__gte=models.F("manufacturing_date")),
                name="check_purchaseinvoiceitem_expiry_after_manufacturing",
            )
        ]

    def save(self, *args, **kwargs):
        self.amount = self.compute_amount()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.purchase_invoice.name} - {self.item.code} ({self.qty} @ {self.rate})"


class PurchaseOrderTax(TaxLine):
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="taxes"
    )

    class Meta(TaxLine.Meta):
        constraints = TaxLine.constraints_for("purchase_order")


class PurchaseInvoiceTax(TaxLine):
    purchase_invoice = models.ForeignKey(
        PurchaseInvoice, on_delete=models.CASCADE, related_name="taxes"
    )

    class Meta(TaxLine.Meta):
        constraints = TaxLine.constraints_for("purchase_invoice")
