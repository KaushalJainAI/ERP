"""Server-side document mapping (ERPNext ``make_*`` / ``get_payment_entry``).

Each function builds a **draft** target document from a **submitted** source
document, entirely on the server, so the client never has to pick accounts or
work out what is still open. Nothing here touches a ledger: the drafts are
ordinary rows that are posted later through the normal ``submit()`` path.

Defaults. ERPNext resolves the party / income / expense / bank accounts from
``Company`` default-account fields. This ERP's ``Company`` has none, so accounts
are resolved by ``account_type`` / ``root_type`` within the company (first match
by id, preferring a fitting name). See ``docs/erpnext_divergences.md``.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from django.db import transaction
from django.db.models import Sum

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    PaymentEntry,
    PaymentEntryReference,
    PaymentMode,
    PaymentType,
)
from core.models import DocStatus, NamingSeries

ZERO = Decimal("0.00")
QTY_QUANTUM = Decimal("0.001")


def financial_year_series(prefix, posting_date):
    """Return prefix with 4-digit Indian financial year code (e.g. SINV-2627-).

    April 2026 to March 2027 is '2627'.
    Total length of prefix + year code + hyphen must be <= 12 characters
    so that 4-digit sequential padding stays <= 16 characters for GST compliance.
    """
    if posting_date.month >= 4:
        start_year = posting_date.year
    else:
        start_year = posting_date.year - 1
    end_year = start_year + 1
    code = f"{start_year % 100:02d}{end_year % 100:02d}"
    series = f"{prefix}-{code}-"
    if len(series) + 4 > 16:
        raise ValueError(
            _("Series '%(series)s' with 4-digit padding exceeds 16 characters.")
            % {"series": series}
        )
    return series


#: Input GST columns of a document line copied from order to invoice. The amounts
#: (cgst / sgst / igst, gst_rate) are recomputed by the engine.
GST_SNAPSHOT_FIELDS = (
    "gst_hsn_code",
    "gst_treatment",
    "item_tax_template",
    "item_tax_rate",
)
TAX_ROW_INPUT_FIELDS = (
    "idx",
    "charge_type",
    "account",
    "description",
    "rate",
    "add_deduct_tax",
    "category",
)


# ---------------------------------------------------------------- accounts


def _postable_accounts(company):
    return Account.objects.filter(
        company=company, is_group=False, is_active=True
    ).order_by("id")


def _pick(qs, preferred=()):
    """First account whose name contains a preferred word, else the first by id."""
    accounts = list(qs)
    for word in preferred:
        for acc in accounts:
            if word in acc.name.lower():
                return acc
    return accounts[0] if accounts else None


def _require(account, what, company):
    if account is None:
        raise ValidationError(
            _("No %(what)s account is configured for company '%(company)s'.")
            % {"what": what, "company": company.name}
        )
    return account


def _company_default(company, field):
    account = getattr(company, field, None)
    return account if account is not None and account.is_active else None


def default_receivable_account(company):
    if account := _company_default(company, "default_receivable_account"):
        return account
    qs = _postable_accounts(company).filter(account_type=AccountType.RECEIVABLE)
    return _require(
        _pick(qs, ("sundry debtor", "debtor", "receivable")), "Receivable", company
    )


def default_payable_account(company):
    if account := _company_default(company, "default_payable_account"):
        return account
    qs = _postable_accounts(company).filter(account_type=AccountType.PAYABLE)
    return _require(
        _pick(qs, ("sundry creditor", "creditor", "payable")), "Payable", company
    )


def default_income_account(company):
    if account := _company_default(company, "default_income_account"):
        return account
    qs = _postable_accounts(company).filter(root_type=AccountRootType.INCOME)
    return _require(
        _pick(qs, ("sales gst @ 5%", "sales gst", "sales account", "sales", "income")),
        "Income",
        company,
    )


def default_expense_account(company, update_stock):
    """Stock-in-hand when the invoice receives stock, else a plain expense account."""
    stock = _postable_accounts(company).filter(account_type=AccountType.STOCK)
    expense = (
        _postable_accounts(company)
        .filter(root_type=AccountRootType.EXPENSE)
        .exclude(account_type=AccountType.COST_OF_GOODS_SOLD)
    )
    first, second = (stock, expense) if update_stock else (expense, stock)
    account = _pick(first, ("stock", "purchase")) or _pick(
        second, ("stock", "purchase")
    )
    return _require(account, "Expense / Stock", company)


def _designated_bank_account(company):
    """The bank account marked default on the company's BankAccount rows, if usable."""
    from accounts.models import BankAccount

    default_ba = (
        BankAccount.objects.filter(company=company, is_default=True, is_active=True)
        .select_related("account")
        .first()
    )
    if (
        default_ba
        and default_ba.account
        and default_ba.account.is_active
        and not default_ba.account.is_group
    ):
        return default_ba.account
    return None


def default_bank_or_cash_account(company):
    """Bank account if the company has one, else a cash account (ERPNext order)."""
    if account := _designated_bank_account(company):
        return account
    for account_type, words in (
        (AccountType.BANK, ("bank",)),
        (AccountType.CASH, ("cash",)),
    ):
        account = _pick(
            _postable_accounts(company).filter(account_type=account_type), words
        )
        if account is not None:
            return account
    raise ValidationError(
        _("No Bank or Cash account is configured for company '%(company)s'.")
        % {"company": company.name}
    )


def default_cash_account(company):
    """The company's postable cash-in-hand account."""
    qs = _postable_accounts(company).filter(account_type=AccountType.CASH)
    account = _pick(qs, ("cash",))
    if account is None:
        raise ValidationError(
            _("No Cash account is configured for company '%(company)s'.")
            % {"company": company.name}
        )
    return account


def default_bank_account(company):
    """The company's default bank account, or its first postable bank account.
    Unlike ``default_bank_or_cash_account`` it never falls back to cash."""
    if account := _designated_bank_account(company):
        return account
    account = _pick(
        _postable_accounts(company).filter(account_type=AccountType.BANK), ("bank",)
    )
    if account is None:
        raise ValidationError(
            _("No Bank account is configured for company '%(company)s'.")
            % {"company": company.name}
        )
    return account


def default_account_for_mode(company, mode):
    """The bank-side account for a payment mode: the cash account for Cash, else the
    default bank (or cash) account."""
    if mode == PaymentMode.CASH:
        return default_cash_account(company)
    return default_bank_or_cash_account(company)


def party_account_for(company, party_type):
    """The company's default control account for a party type, or ``None`` when the
    type has none (the caller must then be told to pick an account)."""
    if party_type == "Customer":
        return default_receivable_account(company)
    if party_type == "Supplier":
        return default_payable_account(company)
    return None


def infer_party_type(company, payment_type, party_name):
    """Customer / Supplier for a payment whose party type was left blank.

    Receive -> Customer and Pay -> Supplier, unless the named party exists only as
    the other kind in this company (a refund from a supplier, a payment to a
    customer). Internal transfers have no party."""
    from buying.models import Supplier
    from selling.models import Customer

    if payment_type not in (PaymentType.RECEIVE, PaymentType.PAY):
        return None
    usual = "Customer" if payment_type == PaymentType.RECEIVE else "Supplier"
    other = "Supplier" if usual == "Customer" else "Customer"
    models = {"Customer": Customer, "Supplier": Supplier}
    if (
        party_name
        and not models[usual].objects.filter(company=company, name=party_name).exists()
    ):
        if models[other].objects.filter(company=company, name=party_name).exists():
            return other
    return usual


# ------------------------------------------------------------- order -> invoice


def remaining_order_lines(order_items, invoiced_items):
    """Order lines (item, warehouse, qty, rate) still to be invoiced.

    ``invoiced_items`` are the lines of the order's **submitted** invoices (as in
    ERPNext, only submitted invoices count as billed). Invoice lines carry no
    link to an order line, so billed quantity is matched per item and consumed
    against the order lines in order. Lines with nothing left are dropped.
    """
    billed = {}
    for line in invoiced_items:
        billed[line.item_id] = billed.get(line.item_id, ZERO) + line.qty

    remaining = []
    for line in sorted(order_items, key=lambda i: i.pk):
        pool = billed.get(line.item_id, ZERO)
        used = min(line.qty, pool)
        billed[line.item_id] = pool - used
        qty = (line.qty - used).quantize(QTY_QUANTUM)
        if qty > 0:
            remaining.append(
                {
                    "order_item_id": line.pk,
                    "item": line.item,
                    "warehouse": line.warehouse,
                    "qty": qty,
                    "rate": line.rate,
                    "price_list_rate": getattr(line, "price_list_rate", None),
                    "batch": getattr(line, "batch", None),
                    # GST snapshots of the order line (as ERPNext copies
                    # item_tax_template / item_tax_rate onto the invoice line).
                    **{name: getattr(line, name) for name in GST_SNAPSHOT_FIELDS},
                }
            )
    return remaining


def copy_order_taxes(order, invoice, tax_model, fk_name):
    """Copy the order's tax rows onto ``invoice`` as **inputs only** (computed
    amounts start at 0), then recompute taxes and totals on the invoice's lines.

    ``calculate_taxes_and_totals`` re-derives every row amount from the quantity
    actually being invoiced, so partial invoicing needs no special handling. The
    supply kind, party snapshots and rounding are refreshed from the masters.
    """
    tax_model.objects.bulk_create(
        [
            tax_model(
                **{fk_name: invoice},
                **{name: getattr(row, name) for name in TAX_ROW_INPUT_FIELDS},
            )
            for row in order.taxes.order_by("idx")
        ]
    )
    invoice.calculate_taxes_and_totals(resolve_lines=False)


def return_line_values(original):
    """Field values for the lines of a draft return of ``original``: every line
    (or part of one) not yet returned, with a NEGATIVE quantity, linked to the
    original line (ERPNext ``make_return_doc``). Raises when nothing is left."""
    from accounts.services import returns as returns_service

    link = returns_service.link_field(type(original))
    rows = []
    for line, remaining in returns_service.returnable_lines(original):
        rows.append(
            {
                "item": line.item,
                "warehouse": line.warehouse,
                "qty": -remaining,
                "rate": line.rate,
                "price_list_rate": line.price_list_rate,
                "batch": line.batch,
                link: line,
                **{name: getattr(line, name) for name in GST_SNAPSHOT_FIELDS},
            }
        )
    if not rows:
        raise ValidationError(
            _("'%(name)s' has already been returned in full.") % {"name": original.name}
        )
    return rows


def require_returnable(original):
    if original.docstatus != DocStatus.SUBMITTED:
        raise ValidationError(_("A return can only be made from a submitted invoice."))
    if original.is_return:
        raise ValidationError(_("A return cannot be made from another return."))


def lines_update_stock(lines):
    """Default ``update_stock``: only when every line is a stock item with a warehouse
    (submit refuses a stock-updating invoice line that has no warehouse)."""
    return bool(lines) and all(
        line["warehouse"] is not None and line["item"].is_stock_item for line in lines
    )


def default_stock_warehouse(company, preferred_field):
    """The warehouse a blank stock line falls back to: ``company.<preferred_field>``
    (Ready for sales, Unprocessed for purchases), else the first leaf warehouse."""
    from stock.models import Warehouse

    warehouse = getattr(company, preferred_field, None)
    if warehouse is None:
        warehouse = (
            Warehouse.objects.filter(company=company, is_group=False)
            .order_by("pk")
            .first()
        )
    return warehouse


def fill_blank_stock_warehouses(company, lines, preferred_field):
    """Set the default warehouse on every line that names none and whose item is a
    stock item. Lines the user filled, and non-stock lines, are left alone."""
    blank = [
        line
        for line in lines
        if not line.get("warehouse")
        and line.get("item") is not None
        and line["item"].is_stock_item
    ]
    if blank:
        default = default_stock_warehouse(company, preferred_field)
        if default is not None:
            for line in blank:
                line["warehouse"] = default
    return lines


# ------------------------------------------------------------ invoice -> payment


def allocated_amount(reference_doctype, reference_name):
    """Total allocated to an invoice by **submitted** Payment Entries."""
    total = PaymentEntryReference.objects.filter(
        payment_entry__docstatus=DocStatus.SUBMITTED,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
    ).aggregate(t=Sum("allocated_amount"))["t"]
    return total or ZERO


def invoice_outstanding(invoice):
    """Outstanding amount of an invoice. A submitted invoice carries it as a ledger
    projection (``outstanding_amount``, kept by ``accounts.services.outstanding``).
    For any other docstatus fall back to the document formula: the rounded
    (tax-inclusive) total, which is what the party line of the voucher carries, less
    submitted allocations."""
    if invoice.docstatus == DocStatus.SUBMITTED:
        # Re-read: the projection is updated by payments after the object was loaded.
        return (
            type(invoice)
            .objects.values_list("outstanding_amount", flat=True)
            .get(pk=invoice.pk)
        )
    label = f"{invoice._meta.app_label}.{invoice._meta.object_name}"
    return invoice.rounded_total - allocated_amount(label, invoice.name)


def build_payment_entry(
    invoice, *, payment_type, party_type, party_name, party_account
):
    """Create a draft Payment Entry settling ``invoice``'s outstanding amount."""
    if invoice.docstatus != DocStatus.SUBMITTED:
        raise ValidationError(
            _("Payment can only be requested for a submitted invoice.")
        )
    outstanding = invoice_outstanding(invoice)
    if invoice.is_return:
        # A credit / debit note keeps a negative outstanding; settling it is a
        # refund, the opposite flow of an invoice payment.
        outstanding = -outstanding
        payment_type = (
            PaymentType.PAY
            if payment_type == PaymentType.RECEIVE
            else PaymentType.RECEIVE
        )
    if outstanding <= 0:
        raise ValidationError(
            _("Invoice '%(name)s' has no outstanding amount.") % {"name": invoice.name}
        )
    bank = default_bank_or_cash_account(invoice.company)

    # Receive: money comes from the party's receivable into the bank.
    # Pay: money goes from the bank to the party's payable. (The posting service
    # debits ``paid_to`` and credits ``paid_from``.)
    if payment_type == PaymentType.RECEIVE:
        paid_from, paid_to = party_account, bank
    else:
        paid_from, paid_to = bank, party_account

    with transaction.atomic():
        entry = PaymentEntry.objects.create(
            name=NamingSeries.next("PE-2026-"),
            payment_type=payment_type,
            posting_date=datetime.date.today(),
            company=invoice.company,
            party_type=party_type,
            party_name=party_name,
            paid_from=paid_from,
            paid_to=paid_to,
            paid_amount=outstanding,
            received_amount=outstanding,
        )
        PaymentEntryReference.objects.create(
            payment_entry=entry,
            reference_doctype=f"{invoice._meta.app_label}.{invoice._meta.object_name}",
            reference_name=invoice.name,
            allocated_amount=outstanding,
        )
    return entry


def fill_default_accounts(serializer, attrs, pickers):
    """Fill blank invoice accounts from the company defaults (ERPNext set_missing_values).

    ``pickers`` maps a field to ``picker(company, attrs)``. A field left out of a
    create, or sent as null, gets the default; an update that does not mention
    the field keeps the stored account.
    """
    instance = serializer.instance
    company = attrs.get("company") or getattr(instance, "company", None)
    if company is None:
        return attrs
    for field, picker in pickers.items():
        if attrs.get(field) is None and (instance is None or field in attrs):
            attrs[field] = picker(company, attrs)
    return attrs


def optional_account_fields(fields, names):
    """Let the client omit the accounts ``fill_default_accounts`` supplies."""
    for name in names:
        fields[name].required = False
        fields[name].allow_null = True
    return fields
