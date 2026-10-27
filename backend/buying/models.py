from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models
from core.models import TimeStampedModel, SubmittableDocument, Company
from accounts.models import Account
from stock.models import Item, Warehouse


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
    gstin = models.CharField(max_length=20, null=True, blank=True)
    tax_id = models.CharField(max_length=40, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(max_length=30, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"], name="unique_supplier_per_company"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.company.abbr})"


class PurchaseOrder(SubmittableDocument):
    """Purchase Order document."""

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

    def validate_before_submit(self):
        items = self.items.all()
        if not items.exists():
            raise ValidationError("Purchase Order must have at least one line item.")
        self.grand_total = sum(item.amount for item in items)
        self.save(update_fields=["grand_total"])

    def on_submit(self):
        pass  # Submitting reserves purchase intent

    def on_cancel(self):
        pass

    def __str__(self):
        return f"{self.name} - {self.supplier.name}"


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="items"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="po_items")
    warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT
    )
    qty = models.DecimalField(max_digits=18, decimal_places=3)
    rate = models.DecimalField(max_digits=18, decimal_places=2)
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )

    def save(self, *args, **kwargs):
        self.amount = (self.qty * self.rate).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.purchase_order.name} - {self.item.code} ({self.qty} @ {self.rate})"
        )


class PurchaseInvoice(SubmittableDocument):
    """Purchase Invoice document."""

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
        self.grand_total = sum(item.amount for item in items)
        self.save(update_fields=["grand_total"])

    def on_submit(self):
        from accounts.services import gl as gl_service
        from stock.services import ledger as stock_service

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
                sle_list.append(
                    {
                        "item": item.item,
                        "warehouse": item.warehouse,
                        "qty": abs(item.qty),
                        "valuation_rate": item.rate,
                    }
                )
            if sle_list:
                sles = stock_service.post(
                    self, sle_list, posting_date=self.posting_date
                )
                stock_value = sum(
                    (s.stock_value_difference for s in sles), Decimal("0.00")
                )

        # 2. Post GL Entries: Debit Expense (and, with perpetual inventory,
        #    Stock In Hand for the stock value), Credit Creditors
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
            expense_amount = self.grand_total - stock_value
        else:
            expense_amount = self.grand_total
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
                "credit": self.grand_total,
                "party_type": "Supplier",
                "party": self.supplier.name,
            }
        )
        gl_service.post(self, gl_entries, posting_date=self.posting_date)

    def on_cancel(self):
        from accounts.services import gl as gl_service
        from stock.services import ledger as stock_service

        gl_service.reverse(self)
        if self.update_stock:
            stock_service.reverse(self)

    def __str__(self):
        return f"{self.name} - {self.supplier.name} ({self.grand_total})"


class PurchaseInvoiceItem(models.Model):
    purchase_invoice = models.ForeignKey(
        PurchaseInvoice, on_delete=models.CASCADE, related_name="items"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="pi_items")
    warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT
    )
    qty = models.DecimalField(max_digits=18, decimal_places=3)
    rate = models.DecimalField(max_digits=18, decimal_places=2)
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )

    def save(self, *args, **kwargs):
        self.amount = (self.qty * self.rate).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.purchase_invoice.name} - {self.item.code} ({self.qty} @ {self.rate})"
