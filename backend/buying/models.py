from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models, transaction
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
    AccountRootType,
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
    is_cash_expense = models.BooleanField(default=False)
    credit_days = models.PositiveIntegerField(null=True, blank=True)
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


class ExpenseCategory(TimeStampedModel):
    """Expense Category master (Electricity, Rent, Hamali...) binding
    a user-friendly expense category to a non-group Expense account and
    an optional non-stock Item."""

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="expense_categories"
    )
    name = models.CharField(max_length=140)
    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="expense_categories"
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expense_categories",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"], name="unique_company_expense_category_name"
            )
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.account_id:
            account = self.account
            if account.root_type != AccountRootType.EXPENSE:
                errors["account"] = _("The GL account must be of root type Expense.")
            if account.is_group:
                errors["account"] = _("The GL account must not be a group.")
            if self.company_id and account.company_id != self.company_id:
                errors["account"] = _("The GL account must belong to the company.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

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
            raise ValidationError(_("Purchase Order must have at least one line item."))
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


class ChargeDistribution(models.TextChoices):
    """How a purchase charge is spread over the stock lines (ERPNext
    ``distribute_charges_based_on``)."""

    AMOUNT = "Amount", _("Amount")
    QTY = "Qty", _("Qty")


class PurchaseChargeTemplate(TimeStampedModel):
    """Preset for a purchase-bill charge row (Freight Inward, Hamali / Loading...)."""

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="purchase_charge_templates"
    )
    name = models.CharField(max_length=100)
    account = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    distribute_by = models.CharField(
        max_length=8,
        choices=ChargeDistribution.choices,
        default=ChargeDistribution.AMOUNT,
    )
    included_in_bill = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"], name="unique_charge_template_per_company"
            ),
            models.CheckConstraint(
                check=models.Q(included_in_bill=False)
                | models.Q(account__isnull=False),
                name="buying_chargetemplate_bill_needs_account",
            ),
        ]

    def __str__(self):
        return self.name


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
            *PayableDocument.return_constraints(),
            # ERPNext ``validate_supplier_invoice``: one live bill number per
            # supplier (a cancelled invoice frees it; blank is "not entered").
            models.UniqueConstraint(
                Lower("bill_no"),
                "company",
                "supplier",
                condition=~models.Q(docstatus=DocStatus.CANCELLED)
                & ~models.Q(bill_no=""),
                name="buying_pi_unique_supplier_bill_no",
            ),
            models.CheckConstraint(
                check=models.Q(bill_date__isnull=True)
                | models.Q(bill_date__lte=models.F("posting_date")),
                name="buying_pi_bill_date_not_after_posting",
            ),
            models.CheckConstraint(
                check=models.Q(due_date__isnull=True)
                | models.Q(due_date__gte=models.F("posting_date")),
                name="check_purchase_invoice_due_date_gte_posting",
            ),
            models.CheckConstraint(
                check=models.Q(is_expense=False)
                | models.Q(expense_category__isnull=False),
                name="check_expense_requires_category",
            ),
        ]

    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name="purchase_invoices"
    )
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="purchase_invoices"
    )
    posting_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    # The supplier's own invoice number / date (ERPNext ``bill_no`` / ``bill_date``):
    # required at submit (not on a return), the GST input-credit reference.
    bill_no = models.CharField(max_length=140, blank=True, default="")
    bill_date = models.DateField(null=True, blank=True)
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoices",
    )
    is_expense = models.BooleanField(default=False)
    expense_category = models.ForeignKey(
        "buying.ExpenseCategory",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="purchase_invoices",
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

    def save(self, *args, **kwargs):
        self.bill_no = (self.bill_no or "").strip()
        if (
            self.is_expense
            and self.expense_category_id
            and not getattr(self, "expense_account_id", None)
        ):
            self.expense_account = self.expense_category.account
        if not self.due_date and self.supplier_id:
            from accounts.services import terms as terms_service

            base_date = self.bill_date or self.posting_date
            if base_date:
                calc = terms_service.calculate_due_date(
                    "Supplier", self.supplier, base_date
                )
                self.due_date = max(calc, self.posting_date)
        super().save(*args, **kwargs)

    @property
    def charges_on_bill(self):
        """Charges the supplier put on the bill (part of ``grand_total``)."""
        from buying.services import charges as charges_service

        return charges_service.totals(self)[0]

    @property
    def charges_off_bill(self):
        """Capitalised charges paid to a third party (stock value only)."""
        from buying.services import charges as charges_service

        return charges_service.totals(self)[1]

    def calculate_taxes_and_totals(self, **kwargs):
        # GST is computed by the engine on the line amounts only; the bill charges
        # are added on top of its result, so no tax is ever charged on them.
        from buying.services import charges as charges_service

        with transaction.atomic():
            outcome = super().calculate_taxes_and_totals(**kwargs)
            charges_service.apply_bill_charges(self)
        return outcome

    def validate_before_submit(self):
        if self.company.require_proof:
            from core.attachments import has_proof

            if not has_proof(self) and not (self.name and self.name.startswith("TLY-")):
                raise ValidationError(
                    _("Proof attachment is required before submission.")
                )
        if not self.due_date:
            raise ValidationError(_("Due date is required at submit."))
        if self.due_date < self.posting_date:
            raise ValidationError(_("Due date cannot be before posting date."))
        if self.bill_date and self.due_date < self.bill_date:
            raise ValidationError(_("Due date cannot be before the bill date."))
        items = self.items.all()
        if not items.exists():
            raise ValidationError(
                _("Purchase Invoice must have at least one line item.")
            )
        if self.is_expense:
            if self.update_stock:
                raise ValidationError(_("Expenses cannot update stock."))
            if (
                self.expense_category
                and self.expense_account != self.expense_category.account
            ):
                raise ValidationError(
                    _("Expense line account must match category account.")
                )
            for line in items:
                line_acc = getattr(line, "expense_account", None)
                if (
                    line_acc
                    and self.expense_category
                    and line_acc != self.expense_category.account
                ):
                    raise ValidationError(
                        _("Expense line account must match category account.")
                    )
        if not self.is_return and not (self.bill_no or "").strip():
            if not getattr(self.supplier, "is_cash_expense", False):
                raise ValidationError(
                    _(
                        "Enter the supplier's invoice number (Bill No) before submitting."
                    )
                )
        if self.bill_date and self.bill_date > self.posting_date:
            raise ValidationError(_("Bill Date cannot be after the posting date."))
        if self.credit_to.is_group or self.expense_account.is_group:
            raise ValidationError(_("Posting to group accounts is not allowed."))
        # Multi-tenant integrity: keep the posting inside this invoice's company.
        if (
            self.credit_to.company_id != self.company_id
            or self.expense_account.company_id != self.company_id
        ):
            raise ValidationError(
                _(
                    "Credit-to and expense accounts must belong to the invoice's company."
                )
            )
        if self.supplier.company_id != self.company_id:
            raise ValidationError(_("Supplier must belong to the invoice's company."))
        if self.update_stock:
            self.company.get_perpetual_accounts()  # raises if perpetual is on but misconfigured
        self._validate_batch_lines()
        from buying.services import charges as charges_service

        charges_service.validate(self)
        self.calculate_taxes_and_totals(for_submit=True)
        if self.is_return:
            from accounts.services import returns as returns_service

            returns_service.validate_return(self)
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
            if self.is_return:
                # A debit note takes a lot back out: it must name the existing
                # batch (the one the purchase created), never a new one.
                if new_fields or not line.batch_id:
                    raise ValidationError(
                        _(
                            "Item '%(code)s': a return must use the batch of the "
                            "purchase (pick the existing batch)."
                        )
                        % {"code": item.code}
                    )
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
        from buying.services import charges as charges_service

        posting.validate_posting(self)
        perpetual = self.company.get_perpetual_accounts() if self.update_stock else None
        charge_rows = [] if self.is_return else charges_service.charge_rows(self)
        landed = charges_service.allocation(self, rows=charge_rows)
        charges_total = sum((c.amount for c in charge_rows), Decimal("0.00"))

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
                        _(
                            "Warehouse required for item '%(code)s' when update_stock is enabled."
                        )
                        % {"code": item.item.code}
                    )
                batch = item.batch
                if self.is_return:
                    # Stock goes back OUT (also of an expired lot: returning bad
                    # raw material to the supplier is the point).
                    sle_list.append(
                        {
                            "item": item.item,
                            "warehouse": item.warehouse,
                            "qty": -abs(item.qty),
                            "valuation_rate": item.rate,
                            "batch": batch,
                            "allow_expired": True,
                        }
                    )
                    continue
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
                entry = {
                    "item": item.item,
                    "warehouse": item.warehouse,
                    "qty": abs(item.qty),
                    "valuation_rate": item.rate,
                    "batch": batch,
                }
                if landed.get(item.pk):
                    # Landed cost: the line's exact incoming value (amount + its
                    # share of the charges); the rate becomes value / qty.
                    entry["incoming_value"] = item.amount + landed[item.pk]
                sle_list.append(entry)
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
            # Non-stock lines plus any rounding difference stay on the expense
            # account; the capitalised charges are not part of net_total.
            expense_amount = self.net_total - (stock_value - charges_total)
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
        against_type, against_no = self.against_voucher
        gl_entries.append(
            {
                "account": self.credit_to,
                "debit": Decimal("0.00"),
                "credit": self.rounded_total,
                "party_type": "Supplier",
                "party": self.supplier.name,
                # A debit note that updates the original outstanding is tagged
                # against the original instead of itself.
                "against_voucher_type": against_type,
                "against_voucher_no": against_no,
            }
        )
        gl_entries.extend(posting.purchase_tax_entries(self))
        gl_entries.extend(charges_service.gl_entries(self, charge_rows))
        if self.is_return:
            # Built from negative amounts: the exact mirror of the purchase.
            gl_entries = gl_service.mirror_entries(gl_entries)
        gl_service.post(self, gl_entries, posting_date=self.posting_date)
        outstanding_service.refresh(self)
        if self.return_against_id:
            outstanding_service.refresh(self.return_against)

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
    # On a return line: the original invoice line it returns (ERPNext
    # ``purchase_invoice_item``), so the returned quantity is enforceable per line.
    purchase_invoice_item = models.ForeignKey(
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
                check=~models.Q(purchase_invoice_item=models.F("id")),
                name="check_purchaseinvoiceitem_not_its_own_return",
            ),
            models.CheckConstraint(
                check=models.Q(expiry_date__isnull=True)
                | models.Q(manufacturing_date__isnull=True)
                | models.Q(expiry_date__gte=models.F("manufacturing_date")),
                name="check_purchaseinvoiceitem_expiry_after_manufacturing",
            ),
        ]

    def __init__(self, *args, **kwargs):
        expense_acc = kwargs.pop("expense_account", None)
        invoice = kwargs.pop("invoice", None)
        if invoice is not None and "purchase_invoice" not in kwargs:
            kwargs["purchase_invoice"] = invoice
        kwargs.pop("uom", None)
        super().__init__(*args, **kwargs)
        if expense_acc is not None:
            self._expense_account = expense_acc

    @property
    def expense_account(self):
        if hasattr(self, "_expense_account"):
            return self._expense_account
        if hasattr(self, "purchase_invoice") and self.purchase_invoice:
            return self.purchase_invoice.expense_account
        return None

    @expense_account.setter
    def expense_account(self, val):
        self._expense_account = val

    def save(self, *args, **kwargs):
        self.amount = self.compute_amount()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.purchase_invoice.name} - {self.item.code} ({self.qty} @ {self.rate})"


class PurchaseInvoiceCharge(models.Model):
    """Extra cost on a purchase bill (freight, hamali, unloading, cartage) that is
    added to the stock value of the invoice's stock lines.

    ``included_in_bill``: the supplier invoiced it (part of ``grand_total``, debited
    to ``account``); otherwise it was paid to someone else and only raises the stock
    value. See ``buying.services.charges``.
    """

    purchase_invoice = models.ForeignKey(
        PurchaseInvoice, on_delete=models.CASCADE, related_name="charges"
    )
    idx = models.PositiveIntegerField(default=1)
    description = models.CharField(max_length=200)
    account = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    distribute_by = models.CharField(
        max_length=8,
        choices=ChargeDistribution.choices,
        default=ChargeDistribution.AMOUNT,
    )
    included_in_bill = models.BooleanField(default=False)

    class Meta:
        ordering = ["idx", "id"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name="buying_purchaseinvoicecharge_amount_gt_0",
            ),
            models.CheckConstraint(
                check=models.Q(included_in_bill=False)
                | models.Q(account__isnull=False),
                name="buying_purchaseinvoicecharge_bill_needs_account",
            ),
        ]

    def __str__(self):
        return f"{self.purchase_invoice.name} - {self.description} ({self.amount})"


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
        constraints = TaxLine.constraints_for("purchase_invoice", signed=True)
