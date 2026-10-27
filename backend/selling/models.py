from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models
from core.models import TimeStampedModel, SubmittableDocument, Company
from accounts.models import Account
from stock.models import Item, Warehouse


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
    gstin = models.CharField(max_length=20, null=True, blank=True)
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


class SalesOrder(SubmittableDocument):
    """Sales Order document."""

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
        self.grand_total = sum(item.amount for item in items)
        self.save(update_fields=["grand_total"])

    def on_submit(self):
        pass  # Submitting reserves order intent; stock/GL posted on invoice or delivery

    def on_cancel(self):
        pass

    def __str__(self):
        return f"{self.name} - {self.customer.name}"


class SalesOrderItem(models.Model):
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

    def save(self, *args, **kwargs):
        self.amount = (self.qty * self.rate).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sales_order.name} - {self.item.code} ({self.qty} @ {self.rate})"


class SalesInvoice(SubmittableDocument):
    """Sales Invoice document."""

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
        self.grand_total = sum(item.amount for item in items)
        self.save(update_fields=["grand_total"])

    def on_submit(self):
        from accounts.services import gl as gl_service
        from stock.services import ledger as stock_service

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
                    }
                )
            if sle_list:
                sles = stock_service.post(
                    self, sle_list, posting_date=self.posting_date
                )
                outgoing_value = -sum(
                    (s.stock_value_difference for s in sles), Decimal("0.00")
                )

        # 2. Post GL Entries (one voucher): Debit Debtors, Credit Income and, with
        #    perpetual inventory, Debit COGS / Credit Stock In Hand at valuation cost.
        gl_entries = [
            {
                "account": self.debit_to,
                "debit": self.grand_total,
                "credit": Decimal("0.00"),
                "party_type": "Customer",
                "party": self.customer.name,
            },
            {
                "account": self.income_account,
                "debit": Decimal("0.00"),
                "credit": self.grand_total,
            },
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

    def on_cancel(self):
        from accounts.services import gl as gl_service
        from stock.services import ledger as stock_service

        gl_service.reverse(self)
        if self.update_stock:
            stock_service.reverse(self)

    def __str__(self):
        return f"{self.name} - {self.customer.name} ({self.grand_total})"


class SalesInvoiceItem(models.Model):
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

    def save(self, *args, **kwargs):
        self.amount = (self.qty * self.rate).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.sales_invoice.name} - {self.item.code} ({self.qty} @ {self.rate})"
        )
