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


class CustomerGroup(TimeStampedModel):
    """Customer Group tree node."""

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


class Customer(TimeStampedModel):
    """Customer master record."""

    name = models.CharField(max_length=200)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="customers"
    )
    customer_group = models.ForeignKey(
        CustomerGroup, on_delete=models.PROTECT, related_name="customers"
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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"], name="unique_customer_per_company"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.company.abbr})"


class SalesOrder(TaxableDocument, SubmittableDocument):
    """Sales Order document."""

    GST_DIRECTION = "Sales"
    party_field = "customer"

    class Meta(TaxableDocument.Meta):
        pass

    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="sales_orders"
    )
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="sales_orders"
    )
    transaction_date = models.DateField()
    delivery_date = models.DateField(null=True, blank=True)
    grand_total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )

    def validate_before_submit(self):
        items = self.items.all()
        if not items.exists():
            raise ValidationError("Sales Order must have at least one line item.")
        for line in items:
            if line.batch_id and line.batch.item_id != line.item_id:
                raise ValidationError(
                    _(
                        "Item '%(code)s': batch '%(batch)s' belongs to a different item."
                    )
                    % {"code": line.item.code, "batch": line.batch.batch_no}
                )
        self.calculate_taxes_and_totals(for_submit=True)

    def on_submit(self):
        pass  # Submitting reserves order intent; stock/GL posted on invoice or delivery

    def on_cancel(self):
        pass

    def __str__(self):
        return f"{self.name} - {self.customer.name}"


class SalesOrderItem(TaxableLine):
    sales_order = models.ForeignKey(
        SalesOrder, on_delete=models.CASCADE, related_name="items"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="so_items")
    warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT
    )
    qty = models.DecimalField(max_digits=18, decimal_places=3)
    rate = models.DecimalField(max_digits=18, decimal_places=2)
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    # Informational preferred lot: a Sales Order moves no stock, so this is only
    # validated for the item match and copied to the invoice line.
    batch = models.ForeignKey(
        Batch,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="so_items",
    )

    def save(self, *args, **kwargs):
        self.amount = self.compute_amount()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sales_order.name} - {self.item.code} ({self.qty} @ {self.rate})"


class SalesInvoice(PayableDocument, TaxableDocument, SubmittableDocument):
    """Sales Invoice document."""

    GST_DIRECTION = "Sales"
    party_field = "customer"
    PARTY_TYPE = "Customer"
    PARTY_ACCOUNT_FIELD = "debit_to"
    OUTSTANDING_SIGN = 1  # the party line is a debit

    class Meta(TaxableDocument.Meta):
        constraints = [
            *TaxableDocument.Meta.constraints,
            PayableDocument.outstanding_constraint(),
        ]

    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="sales_invoices"
    )
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="sales_invoices"
    )
    posting_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    sales_order = models.ForeignKey(
        SalesOrder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoices",
    )
    update_stock = models.BooleanField(default=True)
    debit_to = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="debtor_invoices"
    )
    income_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="income_invoices"
    )
    grand_total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )

    def validate_before_submit(self):
        items = self.items.all()
        if not items.exists():
            raise ValidationError("Sales Invoice must have at least one line item.")
        if self.debit_to.is_group or self.income_account.is_group:
            raise ValidationError("Posting to group accounts is not allowed.")
        # Multi-tenant integrity: an invoice must only touch its own company's
        # ledger and customer. Nothing else stops the API from referencing another
        # company's account/customer, which would post into their books.
        if (
            self.debit_to.company_id != self.company_id
            or self.income_account.company_id != self.company_id
        ):
            raise ValidationError(
                "Debit-to and income accounts must belong to the invoice's company."
            )
        if self.customer.company_id != self.company_id:
            raise ValidationError("Customer must belong to the invoice's company.")
        if self.update_stock:
            self.company.get_perpetual_accounts(
                need_cogs=True
            )  # raises if perpetual is on but misconfigured
        self._validate_batch_lines()
        self.calculate_taxes_and_totals(for_submit=True)
        posting.validate_posting(self)

    def _validate_batch_lines(self):
        """Batch rules at the submit gate (``ledger.post`` re-asserts them centrally)."""
        from stock.services import batches as batch_service

        for line in self.items.all():
            item = line.item
            if not self.update_stock:
                if line.batch_id:
                    raise ValidationError(
                        _(
                            "Sales Invoice '%(name)s' does not update stock: remove the "
                            "batch from item '%(code)s'."
                        )
                        % {"name": self.name, "code": item.code}
                    )
                continue
            if not item.is_stock_item:
                continue
            if not item.has_batch_no:
                if line.batch_id:
                    raise ValidationError(
                        _("Item '%(code)s' is not batch-tracked; remove the batch.")
                        % {"code": item.code}
                    )
                continue
            if not line.batch_id:
                raise ValidationError(
                    batch_service.batch_required_message(
                        item, line.warehouse, line.qty, self.posting_date
                    )
                )
            if line.batch.item_id != item.pk:
                raise ValidationError(
                    _(
                        "Item '%(code)s': batch '%(batch)s' belongs to a different item."
                    )
                    % {"code": item.code, "batch": line.batch.batch_no}
                )

    def on_submit(self):
        from accounts.services import gl as gl_service
        from accounts.services import outstanding as outstanding_service
        from stock.services import ledger as stock_service

        posting.validate_posting(self)
        perpetual = (
            self.company.get_perpetual_accounts(need_cogs=True)
            if self.update_stock
            else None
        )

        # 1. Post stock first (stock lines only): the outgoing valuation it computes
        #    is the cost of goods sold booked below.
        outgoing_value = Decimal("0.00")
        if self.update_stock:
            sle_list = []
            for item in self.items.all():
                if not item.item.is_stock_item:
                    continue
                if not item.warehouse:
                    raise ValidationError(
                        f"Warehouse required for item '{item.item.code}' when update_stock is enabled."
                    )
                sle_list.append(
                    {
                        "item": item.item,
                        "warehouse": item.warehouse,
                        "qty": -abs(item.qty),
                        "valuation_rate": item.rate,
                        "batch": item.batch,
                    }
                )
            if sle_list:
                sles = stock_service.post(
                    self, sle_list, posting_date=self.posting_date
                )
                outgoing_value = -sum(
                    (s.stock_value_difference for s in sles), Decimal("0.00")
                )

        # 2. Post GL Entries (one voucher): Debit Debtors for the full rounded total
        #    (one party line), Credit Income for the net total, Credit each Output tax
        #    row, the round-off, and, with perpetual inventory, Debit COGS / Credit
        #    Stock In Hand at valuation cost (tax never touches the stock legs).
        gl_entries = [
            {
                "account": self.debit_to,
                "debit": self.rounded_total,
                "credit": Decimal("0.00"),
                "party_type": "Customer",
                "party": self.customer.name,
                # ERPNext: the invoice's own party line is "against" itself, so the
                # payments allocated to it net against it in the ledger.
                "against_voucher_type": self.voucher_label(),
                "against_voucher_no": self.name,
            },
            {
                "account": self.income_account,
                "debit": Decimal("0.00"),
                "credit": self.net_total,
            },
            *posting.sales_tax_entries(self),
        ]
        if perpetual and outgoing_value:
            cogs_account = perpetual["default_expense_account"]
            inventory_account = perpetual["default_inventory_account"]
            gl_entries.append(
                {
                    "account": cogs_account,
                    "debit": outgoing_value,
                    "credit": Decimal("0.00"),
                }
            )
            gl_entries.append(
                {
                    "account": inventory_account,
                    "debit": Decimal("0.00"),
                    "credit": outgoing_value,
                }
            )
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
        return f"{self.name} - {self.customer.name} ({self.grand_total})"


class SalesInvoiceItem(TaxableLine):
    sales_invoice = models.ForeignKey(
        SalesInvoice, on_delete=models.CASCADE, related_name="items"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="si_items")
    warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT
    )
    qty = models.DecimalField(max_digits=18, decimal_places=3)
    rate = models.DecimalField(max_digits=18, decimal_places=2)
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    # The lot sold. Required for batch-tracked items when ``update_stock`` is on;
    # refused when the invoice moves no stock (no false traceability).
    batch = models.ForeignKey(
        Batch,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="si_items",
    )

    def save(self, *args, **kwargs):
        self.amount = self.compute_amount()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.sales_invoice.name} - {self.item.code} ({self.qty} @ {self.rate})"
        )


class SalesOrderTax(TaxLine):
    sales_order = models.ForeignKey(
        SalesOrder, on_delete=models.CASCADE, related_name="taxes"
    )

    class Meta(TaxLine.Meta):
        constraints = TaxLine.constraints_for("sales_order")


class SalesInvoiceTax(TaxLine):
    sales_invoice = models.ForeignKey(
        SalesInvoice, on_delete=models.CASCADE, related_name="taxes"
    )

    class Meta(TaxLine.Meta):
        constraints = TaxLine.constraints_for("sales_invoice")
