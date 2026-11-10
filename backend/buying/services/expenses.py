import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from accounts import mapping as account_mapping
from accounts.models import (
    PaymentEntry,
    PaymentEntryReference,
    PaymentMode,
    PaymentType,
    TaxTemplate,
)
from buying.models import (
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseInvoiceTax,
)
from core.models import DocStatus, NamingSeries
from stock.models import Item, ItemGroup, UOM


def create_expense(
    company,
    category,
    supplier,
    amount,
    date=None,
    has_gst=False,
    tax_template=None,
    bill_no="",
    paid_now=False,
    paid_from=None,
    due_date=None,
    user=None,
    proof=None,
    as_draft=False,
    docstatus=None,
    recurring_expense=None,
    mode=None,
    reference_no=None,
):
    """Create and submit an expense invoice, optionally with immediate payment.

    Wrap in atomic transaction:
    - Validates amount > 0 and category is present.
    - Resolves date (defaults to today) and due_date (defaults to date).
    - If has_gst and tax_template, calculates reverse GST base and tax rows.
    - Creates PurchaseInvoice with is_expense=True, update_stock=False.
    - Ensures non-stock item exists on category.
    - Creates PurchaseInvoiceItem and PurchaseInvoiceTax rows.
    - Submits invoice.
    - If paid_now, creates and submits allocated PaymentEntry.
    - Returns (invoice, pe).
    """
    if category is None:
        raise ValidationError(_("Expense category is required."))

    if amount is None:
        raise ValidationError(_("Expense amount is required."))

    if not isinstance(amount, Decimal):
        try:
            amount = Decimal(str(amount))
        except Exception:
            raise ValidationError(_("Invalid expense amount."))

    if amount <= Decimal("0.00"):
        raise ValidationError(_("Expense amount must be greater than zero."))

    if paid_now and not paid_from and not mode:
        raise ValidationError(
            _("Paid from account is required when paid now is selected.")
        )
    if paid_now and mode and mode not in PaymentMode.values:
        raise ValidationError(_("Unknown payment mode."))
    if paid_now and mode == PaymentMode.CHEQUE and not reference_no:
        raise ValidationError(_("A cheque needs its number."))

    if isinstance(date, str):
        try:
            date = datetime.date.fromisoformat(date)
        except ValueError:
            raise ValidationError(_("Invalid date format."))
    date = date or datetime.date.today()

    if isinstance(due_date, str):
        try:
            due_date = datetime.date.fromisoformat(due_date)
        except ValueError:
            raise ValidationError(_("Invalid due date format."))
    due_date = due_date or date

    with transaction.atomic():
        # Ensure category has an item
        if not category.item:
            uom = UOM.objects.filter(name="Nos").first() or UOM.objects.first()
            if not uom:
                uom = UOM.objects.create(name="Nos")
            item_group = (
                ItemGroup.objects.filter(is_group=False).first()
                or ItemGroup.objects.first()
            )
            if not item_group:
                item_group = ItemGroup.objects.create(
                    name="All Item Groups", is_group=False
                )
            item, _created = Item.objects.get_or_create(
                name=f"Expense - {category.name}",
                defaults={
                    "code": f"EXP-{category.id}",
                    "item_group": item_group,
                    "stock_uom": uom,
                    "is_stock_item": False,
                    "gst_hsn_code": "998211",
                },
            )
            category.item = item
            category.save(update_fields=["item"])

        if getattr(supplier, "is_cash_expense", False):
            has_gst = False

        if has_gst and not category.item.gst_hsn_code:
            category.item.gst_hsn_code = "998211"
            category.item.save(update_fields=["gst_hsn_code"])

        net_amount = amount
        rate_pct = Decimal("0")
        if has_gst and tax_template:
            if hasattr(tax_template, "rows"):
                rows = list(tax_template.rows.all())
                if rows:
                    if hasattr(rows[0], "rate"):
                        rate_pct = sum((r.rate for r in rows), Decimal("0"))
                    elif hasattr(rows[0], "tax_rate"):
                        igst = next(
                            (
                                r.tax_rate
                                for r in rows
                                if getattr(r, "gst_component", "") == "IGST"
                            ),
                            None,
                        )
                        if igst is not None:
                            rate_pct = igst
                        else:
                            rate_pct = sum((r.tax_rate for r in rows), Decimal("0"))
            elif hasattr(tax_template, "rate"):
                rate_pct = Decimal(str(tax_template.rate))

            if rate_pct > 0:
                net_amount = (
                    amount / (Decimal("1") + (rate_pct / Decimal("100")))
                ).quantize(Decimal("0.01"))

        payable_account = getattr(
            company, "default_payable_account", None
        ) or account_mapping.default_payable_account(company)

        invoice_name = NamingSeries.next("PINV-2026-")

        invoice = PurchaseInvoice.objects.create(
            name=invoice_name,
            company=company,
            supplier=supplier,
            posting_date=date,
            bill_date=date,
            bill_no=bill_no or "",
            is_expense=True,
            expense_category=category,
            update_stock=False,
            due_date=due_date,
            credit_to=payable_account,
            expense_account=category.account,
            taxes_and_charges=(
                tax_template
                if (has_gst and isinstance(tax_template, TaxTemplate))
                else None
            ),
            recurring_expense=recurring_expense,
        )

        PurchaseInvoiceItem.objects.create(
            purchase_invoice=invoice,
            item=category.item,
            qty=Decimal("1.000"),
            rate=net_amount,
            amount=net_amount,
        )

        if has_gst and tax_template and isinstance(tax_template, TaxTemplate):
            for row in tax_template.rows.all():
                row_tax = (net_amount * row.rate / Decimal("100")).quantize(
                    Decimal("0.01")
                )
                PurchaseInvoiceTax.objects.create(
                    purchase_invoice=invoice,
                    idx=row.idx,
                    charge_type=row.charge_type,
                    account=row.account,
                    description=row.description,
                    rate=row.rate,
                    tax_amount=row_tax,
                    total=net_amount + row_tax,
                )

        invoice.calculate_taxes_and_totals()

        if proof:
            from core import attachments as att_rules
            from core.models import Attachment
            from users.models import User

            checked = att_rules.validate_upload(proof)
            att_user = user if (user and getattr(user, "pk", None)) else None
            if not att_user:
                att_user = (
                    User.objects.filter(is_superuser=True).first()
                    or User.objects.first()
                )
            att = Attachment(
                model_label="buying.PurchaseInvoice",
                object_id=str(invoice.pk),
                original_filename=checked.filename,
                content_type=checked.content_type,
                size=checked.size,
                sha256=checked.sha256,
                description="Expense Proof",
                uploaded_by=att_user,
            )
            att.file.save(checked.filename, proof, save=False)
            att.save()

        is_draft = as_draft or (docstatus == DocStatus.DRAFT)

        if not is_draft:
            invoice.submit(user=user)

        pe = None
        if paid_now and not is_draft:
            pe_name = NamingSeries.next("PE-2026-")
            party_name = supplier.name if hasattr(supplier, "name") else str(supplier)
            if paid_from is None:
                paid_from = account_mapping.default_account_for_mode(company, mode)
            pe = PaymentEntry.objects.create(
                name=pe_name,
                company=company,
                payment_type=PaymentType.PAY,
                party_type="Supplier",
                party_name=party_name,
                paid_to=payable_account,
                paid_from=paid_from,
                paid_amount=amount,
                received_amount=amount,
                posting_date=date,
                mode=mode or None,
                reference_no=reference_no or None,
            )
            PaymentEntryReference.objects.create(
                payment_entry=pe,
                reference_doctype="buying.PurchaseInvoice",
                reference_name=invoice.name,
                allocated_amount=amount,
            )
            pe.submit(user=user)
            invoice.refresh_from_db()

        return invoice, pe


def generate_due_recurring_expenses(company=None, as_on=None):
    """
    Generate draft PurchaseInvoices for recurring expenses due as of `as_on`.

    A recurring expense is due if:
    - day_of_month == as_on.day
    - OR as_on is the last day of the month AND day_of_month > last_day_of_month

    Deduplication:
    Only creates if no non-cancelled invoice already exists for this
    recurring expense in (year, month).
    """
    import calendar
    from django.utils import timezone
    from buying.models import RecurringExpense

    if as_on is None:
        as_on = timezone.localdate()
    elif isinstance(as_on, str):
        as_on = datetime.date.fromisoformat(as_on)
    elif hasattr(as_on, "date") and callable(as_on.date):
        as_on = as_on.date()

    _, last_day = calendar.monthrange(as_on.year, as_on.month)
    is_last_day = as_on.day == last_day

    qs = RecurringExpense.objects.filter(is_active=True).select_related(
        "company", "category", "supplier", "category__account"
    )
    if company is not None:
        qs = qs.filter(company=company)

    created_invoices = []

    for rec in qs:
        is_due = (rec.day_of_month == as_on.day) or (
            is_last_day and rec.day_of_month > last_day
        )
        if not is_due:
            continue

        already_exists = (
            PurchaseInvoice.objects.filter(
                recurring_expense=rec,
                posting_date__year=as_on.year,
                posting_date__month=as_on.month,
            )
            .exclude(docstatus=DocStatus.CANCELLED)
            .exists()
        )

        if already_exists:
            continue

        inv, _ = create_expense(
            company=rec.company,
            category=rec.category,
            supplier=rec.supplier,
            amount=rec.amount,
            date=as_on,
            due_date=as_on,
            as_draft=True,
            docstatus=DocStatus.DRAFT,
            recurring_expense=rec,
        )
        created_invoices.append(inv)

    return created_invoices
