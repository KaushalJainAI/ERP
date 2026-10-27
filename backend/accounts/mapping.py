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
from django.db import transaction
from django.db.models import Sum

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    PaymentEntry,
    PaymentEntryReference,
    PaymentType,
)
from core.models import DocStatus, NamingSeries

ZERO = Decimal("0.00")
QTY_QUANTUM = Decimal("0.001")


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
            f"No {what} account is configured for company '{company.name}'."
        )
    return account


def default_receivable_account(company):
    qs = _postable_accounts(company).filter(account_type=AccountType.RECEIVABLE)
    return _require(_pick(qs, ("debtor", "receivable")), "Receivable", company)


def default_payable_account(company):
    qs = _postable_accounts(company).filter(account_type=AccountType.PAYABLE)
    return _require(_pick(qs, ("creditor", "payable")), "Payable", company)


def default_income_account(company):
    qs = _postable_accounts(company).filter(root_type=AccountRootType.INCOME)
    return _require(_pick(qs, ("sales", "income")), "Income", company)


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


def default_bank_or_cash_account(company):
    """Bank account if the company has one, else a cash account (ERPNext order)."""
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
        f"No Bank or Cash account is configured for company '{company.name}'."
    )


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
                    "item": line.item,
                    "warehouse": line.warehouse,
                    "qty": qty,
                    "rate": line.rate,
                }
            )
    return remaining


def lines_update_stock(lines):
    """Default ``update_stock``: only when every line is a stock item with a warehouse
    (submit refuses a stock-updating invoice line that has no warehouse)."""
    return bool(lines) and all(
        line["warehouse"] is not None and line["item"].is_stock_item for line in lines
    )


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
    """Derived outstanding amount: grand total less submitted allocations."""
    label = f"{invoice._meta.app_label}.{invoice._meta.object_name}"
    return invoice.grand_total - allocated_amount(label, invoice.name)


def build_payment_entry(
    invoice, *, payment_type, party_type, party_name, party_account
):
    """Create a draft Payment Entry settling ``invoice``'s outstanding amount."""
    if invoice.docstatus != DocStatus.SUBMITTED:
        raise ValidationError("Payment can only be requested for a submitted invoice.")
    outstanding = invoice_outstanding(invoice)
    if outstanding <= 0:
        raise ValidationError(f"Invoice '{invoice.name}' has no outstanding amount.")
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
