from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext as _
from core.models import (
    Company,
    DocStatus,
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


class DispatchStatus(models.TextChoices):
    PENDING = "Pending", "Pending"
    PART_DISPATCHED = "Part Dispatched", "Part Dispatched"
    DISPATCHED = "Dispatched", "Dispatched"


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
    default_price_list = models.ForeignKey(
        "stock.PriceList",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

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
    default_price_list = models.ForeignKey(
        "stock.PriceList",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # 0 means "no limit" (ERPNext's convention).
    credit_limit = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    bypass_credit_limit_check = models.BooleanField(default=False)
    credit_days = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            # One party per name, ignoring case: "sharma traders" is "Sharma Traders".
            models.UniqueConstraint(
                Lower("name"), "company", name="unique_customer_name_per_company_ci"
            ),
            models.CheckConstraint(
                check=models.Q(credit_limit__gte=0),
                name="check_customer_credit_limit_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.company.abbr})"


class SalesOrder(TaxableDocument, SubmittableDocument):
    """Sales Order document."""

    GST_DIRECTION = "Sales"
    party_field = "customer"

    class Meta(TaxableDocument.Meta):
        constraints = [
            *TaxableDocument.Meta.constraints,
            models.CheckConstraint(
                check=models.Q(dispatch_status__in=DispatchStatus.values),
                name="check_sales_order_dispatch_status_valid",
            ),
        ]

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
    price_list = models.ForeignKey(
        "stock.PriceList",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    dispatch_status = models.CharField(
        max_length=20,
        choices=DispatchStatus.choices,
        default=DispatchStatus.PENDING,
    )

    def submit(self, user=None):
        # ``SubmittableDocument.submit`` does not pass the user into
        # ``validate_before_submit``; the credit override needs it (design 6.2).
        self._submit_user = user
        return super().submit(user=user)

    def validate_before_submit(self):
        items = self.items.all()
        if not items.exists():
            raise ValidationError(_("Sales Order must have at least one line item."))
        for line in items:
            if line.batch_id and line.batch.item_id != line.item_id:
                raise ValidationError(
                    _("Item '%(code)s': batch '%(batch)s' belongs to a different item.")
                    % {"code": line.item.code, "batch": line.batch.batch_no}
                )
        self.calculate_taxes_and_totals(for_submit=True)
        from selling.services import credit as credit_service

        credit_service.enforce(self, getattr(self, "_submit_user", None))

    def ensure_no_linked_invoices(self):
        active_invoices = self.invoices.filter(docstatus=DocStatus.SUBMITTED)
        if active_invoices.exists():
            names = ", ".join(active_invoices.values_list("name", flat=True)[:3])
            raise ValidationError(
                _(
                    "Cannot cancel Sales Order '%(order)s' because it is referenced by submitted Sales Invoices: %(invoices)s. "
                    "Cancel the linked invoices first."
                )
                % {"order": self.name, "invoices": names}
            )

    def on_submit(self):
        pass  # Submitting reserves order intent; stock/GL posted on invoice or delivery

    def on_cancel(self):
        self.ensure_no_linked_invoices()
        self.dispatch_status = DispatchStatus.PENDING

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
    # Snapshot of the price-list rate at fetch time (6 dp; design 5.1).
    price_list_rate = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
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

    class Meta(TaxableLine.Meta):
        constraints = [
            *TaxableLine.Meta.constraints,
            models.CheckConstraint(
                check=models.Q(qty__gt=0), name="check_salesorderitem_qty_gt_0"
            ),
            models.CheckConstraint(
                check=models.Q(rate__gte=0), name="check_salesorderitem_rate_gte_0"
            ),
        ]

    def save(self, *args, **kwargs):
        if (
            not self.warehouse_id
            and self.sales_order_id
            and self.sales_order.company_id
        ):
            from stock.models import Warehouse

            company = self.sales_order.company
            default_wh = getattr(company, "ready_warehouse", None)
            if not default_wh:
                default_wh = (
                    Warehouse.objects.filter(company=company, is_group=False)
                    .order_by("pk")
                    .first()
                )
            if default_wh:
                self.warehouse = default_wh
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
            *PayableDocument.return_constraints(),
            *PayableDocument.historical_constraints(),
            models.CheckConstraint(
                check=models.Q(due_date__isnull=True)
                | models.Q(due_date__gte=models.F("posting_date")),
                name="check_sales_invoice_due_date_gte_posting",
            ),
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
    price_list = models.ForeignKey(
        "stock.PriceList",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    def save(self, *args, **kwargs):
        if not self.due_date and self.posting_date and self.customer_id:
            from accounts.services import terms as terms_service

            self.due_date = terms_service.calculate_due_date(
                "Customer", self.customer, self.posting_date
            )
        super().save(*args, **kwargs)

    def submit(self, user=None):
        # Stash the user for the credit override (design 6.2).
        self._submit_user = user
        return super().submit(user=user)

    def validate_before_submit(self):
        if not self.due_date:
            raise ValidationError(_("Due date is required at submit."))
        # Normalise: objects built in code (imports, scripts) may still hold ISO strings.
        self.due_date = self._meta.get_field("due_date").to_python(self.due_date)
        self.posting_date = self._meta.get_field("posting_date").to_python(
            self.posting_date
        )
        if self.due_date < self.posting_date:
            raise ValidationError(_("Due date cannot be before posting date."))
        items = self.items.all()
        if not items.exists():
            raise ValidationError(_("Sales Invoice must have at least one line item."))
        if self.debit_to.is_group or self.income_account.is_group:
            raise ValidationError(_("Posting to group accounts is not allowed."))
        # Multi-tenant integrity: an invoice must only touch its own company's
        # ledger and customer. Nothing else stops the API from referencing another
        # company's account/customer, which would post into their books.
        if (
            self.debit_to.company_id != self.company_id
            or self.income_account.company_id != self.company_id
        ):
            raise ValidationError(
                _("Debit-to and income accounts must belong to the invoice's company.")
            )
        if self.customer.company_id != self.company_id:
            raise ValidationError(_("Customer must belong to the invoice's company."))
        if self.update_stock:
            self.company.get_perpetual_accounts(
                need_cogs=True
            )  # raises if perpetual is on but misconfigured
        self._validate_batch_lines()
        self.calculate_taxes_and_totals(for_submit=True)
        if self.is_return:
            # A credit note only reduces what the customer owes: no credit check.
            from accounts.services import returns as returns_service

            returns_service.validate_return(self)
        else:
            from selling.services import credit as credit_service

            credit_service.enforce(self, getattr(self, "_submit_user", None))
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
                if self.is_return:
                    # Stock coming back needs the lot it returns to.
                    raise ValidationError(
                        batch_service.batch_required_message(
                            item, line.warehouse, None, self.posting_date
                        )
                    )
                # A sale may leave the batch blank: FIFO picks the oldest lots.
                continue
            if line.batch.item_id != item.pk:
                raise ValidationError(
                    _("Item '%(code)s': batch '%(batch)s' belongs to a different item.")
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
        #    For a return (credit note) the stock comes back IN at the original
        #    outgoing valuation, so ``outgoing_value`` comes out negative and the
        #    COGS / inventory legs below are mirrored (Dr Stock In Hand / Cr COGS).
        outgoing_value = Decimal("0.00")
        if self.update_stock:
            sle_list = []
            for item in self.items.all():
                if not item.item.is_stock_item:
                    continue
                if not item.warehouse:
                    raise ValidationError(
                        _(
                            "Warehouse required for item '%(code)s' when update_stock is enabled."
                        )
                        % {"code": item.item.code}
                    )
                if self.is_return:
                    from accounts.services import returns as returns_service

                    sle_list.append(
                        {
                            "item": item.item,
                            "warehouse": item.warehouse,
                            "qty": abs(item.qty),
                            "incoming_value": returns_service.sales_return_incoming_value(
                                self, item
                            ),
                            "batch": item.batch,
                        }
                    )
                    continue
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
        against_type, against_no = self.against_voucher
        gl_entries = [
            {
                "account": self.debit_to,
                "debit": self.rounded_total,
                "credit": Decimal("0.00"),
                "party_type": "Customer",
                "party": self.customer.name,
                # ERPNext: the invoice's own party line is "against" itself, so the
                # payments allocated to it net against it in the ledger. A credit
                # note that updates the original outstanding is tagged against the
                # original instead.
                "against_voucher_type": against_type,
                "against_voucher_no": against_no,
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
        if self.is_return:
            # Built from negative amounts: the exact mirror of the original voucher.
            gl_entries = gl_service.mirror_entries(gl_entries)
        gl_service.post(self, gl_entries, posting_date=self.posting_date)
        outstanding_service.refresh(self)
        if self.return_against_id:
            outstanding_service.refresh(self.return_against)
        if self.sales_order_id:
            from selling.services import dispatch as dispatch_service

            dispatch_service.refresh_status(self.sales_order)

    def on_cancel(self):
        from accounts.services import gl as gl_service
        from accounts.services import outstanding as outstanding_service
        from stock.services import ledger as stock_service

        self.ensure_no_linked_payments()
        self.ensure_no_linked_returns()
        gl_service.reverse(self)
        if self.update_stock:
            stock_service.reverse(self)
        outstanding_service.refresh(self)
        if self.return_against_id:
            outstanding_service.refresh(self.return_against)
        if self.sales_order_id:
            from selling.services import dispatch as dispatch_service

            dispatch_service.refresh_status(self.sales_order)

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
    # Snapshot of the price-list rate at fetch time (6 dp; design 5.1).
    price_list_rate = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
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
    # On a return line: the original invoice line it returns (ERPNext
    # ``sales_invoice_item``), so the returned quantity is enforceable per line.
    sales_invoice_item = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="return_items",
    )

    class Meta(TaxableLine.Meta):
        constraints = [
            *TaxableLine.signed_constraints(),
            models.CheckConstraint(
                check=~models.Q(sales_invoice_item=models.F("id")),
                name="check_salesinvoiceitem_not_its_own_return",
            ),
            # Returns carry a negative quantity, so only zero is refused.
            models.CheckConstraint(
                check=~models.Q(qty=0), name="check_salesinvoiceitem_qty_not_zero"
            ),
            models.CheckConstraint(
                check=models.Q(rate__gte=0), name="check_salesinvoiceitem_rate_gte_0"
            ),
        ]

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
        constraints = TaxLine.constraints_for("sales_invoice", signed=True)
