"""Build Tally vouchers (as plain dataclasses) from the immutable ledgers.

``GLEntry`` rows of a voucher are the accounting; ``StockLedgerEntry`` rows
are the quantities of a Stock Journal. Nothing here writes to the database.

Amounts in a ``LedgerEntry`` are *debit-positive*: ``+100`` is a debit of 100.
The XML writer converts to Tally's convention (debit = ISDEEMEDPOSITIVE Yes with
a negative AMOUNT).
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from django.apps import apps

from accounts.models import GLEntry
from tally_export import constants as C
from tally_export.models import remote_id_for
from tally_export.services.collect import VoucherRef
from tally_export.services.maps import (
    MapIndex,
    Unmapped,
    is_party_account,
    voucher_type_for,
)

ZERO = Decimal("0.00")
INVOICES = ("selling.SalesInvoice", "buying.PurchaseInvoice")
#: Vouchers whose unallocated party credit is an "On Account" amount in Tally.
ON_ACCOUNT_SOURCES = ("accounts.PaymentEntry", "accounts.JournalEntry")
#: The document attribute naming the sales / purchase account of an invoice.
TRADE_ATTR = {
    "selling.SalesInvoice": "income_account",
    "buying.PurchaseInvoice": "expense_account",
}


@dataclass
class Bill:
    kind: str  # New Ref | Agst Ref | On Account
    name: str
    amount: Decimal  # debit-positive


@dataclass
class LedgerEntry:
    ledger: str
    amount: Decimal  # debit-positive
    is_party: bool = False
    bills: List[Bill] = field(default_factory=list)


@dataclass
class StockLine:
    item_name: str
    unit: str
    qty: Decimal  # positive
    rate: Decimal
    amount: Decimal  # positive
    godown: str
    batch: str = ""
    inward: bool = False  # stock in (purchase, sales return, journal destination)
    ledger: str = ""  # accounting allocation ledger (invoices only)


@dataclass
class VoucherDraft:
    doctype: str
    name: str
    posting_date: datetime.date
    tally_type: str
    remote_id: str
    action: str  # Create | Cancel
    docstatus: int
    reference: str = ""
    reference_date: Optional[datetime.date] = None
    narration: str = ""
    party_ledger: str = ""
    party_gstin: str = ""
    place_of_supply: str = ""
    is_invoice: bool = False
    is_stock_journal: bool = False
    entries: List[LedgerEntry] = field(default_factory=list)
    stock: List[StockLine] = field(default_factory=list)
    unmapped: List[Unmapped] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    def debit_credit(self) -> Tuple[Decimal, Decimal]:
        """Total debits and credits of the money side, including the accounting
        allocations of invoice stock lines."""
        dr = cr = ZERO
        for e in self.entries:
            if e.amount > 0:
                dr += e.amount
            else:
                cr += -e.amount
        if self.is_invoice:
            for s in self.stock:
                if s.ledger:
                    # an inward line is debited to the purchase / return ledger
                    if s.inward:
                        dr += s.amount
                    else:
                        cr += s.amount
        return dr, cr

    @property
    def balanced(self) -> bool:
        if self.action == "Cancel" or self.is_stock_journal:
            return True
        dr, cr = self.debit_credit()
        return dr == cr and not self.problems


def _q(v) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"))


def _variant(doc, doctype: str) -> str:
    if doctype in INVOICES:
        if getattr(doc, "is_return", False):
            return "return"
        if getattr(doc, "is_expense", False):
            return "expense"
        return ""
    if doctype == "accounts.PaymentEntry":
        return doc.payment_type
    if doctype == "accounts.JournalEntry":
        return doc.voucher_type
    return ""


def _narration(doc, gl_rows) -> str:
    for attr in ("user_remark", "remarks"):
        text = getattr(doc, attr, None)
        if text:
            return str(text)
    if type(doc).__name__ == "PaymentEntry":
        return ""  # its GL remark is machine text ("Payment Receive: <ref>")
    for r in gl_rows:
        if r.remarks and not str(r.remarks).startswith("Reversal of"):
            return str(r.remarks)
    return ""


def _reference(doc) -> str:
    for attr in ("bill_no", "supplier_invoice_no", "reference_no", "cheque_no"):
        v = getattr(doc, attr, None)
        if v:
            return str(v)
    return ""


def _reference_date(doc) -> Optional[datetime.date]:
    """The date of the party's own document (supplier bill date)."""
    return getattr(doc, "bill_date", None) or None


def _place_and_gstin(doc) -> Tuple[str, str]:
    state = getattr(doc, "place_of_supply", None)
    return (state.name if state else "", getattr(doc, "party_gstin", None) or "")


def build_cancel(ref: VoucherRef, company) -> VoucherDraft:
    """A Cancel carries only the identity of what Tally already holds."""
    prev = ref.prev
    return VoucherDraft(
        doctype=ref.doctype,
        name=ref.name,
        posting_date=prev.posting_date,
        tally_type=prev.tally_voucher_type,
        remote_id=prev.remote_id,
        action="Cancel",
        docstatus=2,
    )


def build_voucher(ref: VoucherRef, company, settings, index: MapIndex) -> VoucherDraft:
    if ref.action == "Cancel":
        return build_cancel(ref, company)
    model = apps.get_model(ref.doctype)
    doc = model.objects.filter(name=ref.name).first()
    draft = VoucherDraft(
        doctype=ref.doctype,
        name=ref.name,
        posting_date=ref.posting_date,
        tally_type="",
        remote_id=remote_id_for(company, ref.doctype, ref.name),
        action="Create",
        docstatus=1,
    )
    if doc is None:
        draft.problems.append("document_missing")
        return draft
    draft.posting_date = getattr(doc, "posting_date", ref.posting_date)
    draft.tally_type = voucher_type_for(
        settings, ref.doctype, _variant(doc, ref.doctype)
    )
    draft.reference = _reference(doc)
    draft.reference_date = _reference_date(doc) if draft.reference else None
    draft.place_of_supply, draft.party_gstin = _place_and_gstin(doc)
    if draft.tally_type == "Stock Journal":
        _build_stock_journal(draft, doc, index)
    else:
        _build_accounting(draft, doc, index)
    return draft


# --------------------------------------------------------------------------
# Accounting vouchers (Sales, Purchase, notes, Receipt, Payment, Contra, Journal)
# --------------------------------------------------------------------------


@dataclass(eq=False)
class _Line:
    account: object
    party_type: Optional[str]
    party: Optional[str]
    amount: Decimal  # debit-positive
    against_type: Optional[str] = None
    against_no: Optional[str] = None


def _gl_lines(draft: VoucherDraft, company) -> Tuple[List[_Line], list]:
    rows = list(
        GLEntry.objects.filter(
            company=company,
            voucher_type=draft.doctype,
            voucher_no=draft.name,
            is_cancelled=False,
        )
        .select_related("account")
        .order_by("id")
    )
    lines = [
        _Line(
            r.account,
            r.party_type,
            r.party,
            r.debit - r.credit,
            r.against_voucher_type,
            r.against_voucher_no,
        )
        for r in rows
    ]
    return lines, rows


def _eiiv_pks(doc) -> set:
    """Pks of the *Expenses Included In Valuation* account when ``doc`` has bill
    charges: with perpetual inventory its credit mirrors the capitalised part of the
    stock leg, so it is folded away together with it (Tally values stock itself)."""
    if not hasattr(doc, "charges_on_bill"):
        return set()
    from buying.services.charges import eiiv_account

    account = eiiv_account(doc.company)
    return {account.pk} if account is not None else set()


def _kind(line: _Line) -> str:
    at = line.account.account_type
    if is_party_account(line.account, line.party):
        return "party"
    if at == "Tax":
        return "tax"
    if at in ("Stock", "Cost of Goods Sold"):
        return "stock"
    if at == "Round Off":
        return "roundoff"
    return "trade"


def _build_accounting(draft: VoucherDraft, doc, index: MapIndex):
    company = doc.company
    lines, rows = _gl_lines(draft, company)
    draft.narration = _narration(doc, rows)
    if not lines:
        draft.problems.append("no_gl_entries")
        return
    is_invoice = draft.doctype in INVOICES
    draft.is_invoice = is_invoice

    stock_items: List = []
    trade_account = None
    if is_invoice:
        trade_account = getattr(doc, TRADE_ATTR[draft.doctype], None)
        stock_items = [
            li
            for li in doc.items.select_related("item", "warehouse", "batch")
            if li.item.is_stock_item
        ]
        # Tally values stock itself: the ERP's stock-valuation legs (Stock in
        # Hand / COGS) are not Tally entries. What they net to is part of the
        # purchase cost and is folded into the trade (sales / purchase) ledger.
        eiiv = _eiiv_pks(doc)

        def is_stock(ln):
            return _kind(ln) == "stock" or ln.account.pk in eiiv

        stock_lines = [ln for ln in lines if is_stock(ln)]
        if stock_lines and trade_account is not None:
            net = sum((ln.amount for ln in stock_lines), ZERO)
            lines = [ln for ln in lines if not is_stock(ln)]
            if net != 0:
                lines.append(_Line(trade_account, None, None, net))

    # Resolve names once; collect what is unmapped.
    def name_of(ln: _Line) -> str:
        name, miss = index.ledger(ln.account, ln.party_type, ln.party)
        if miss and miss not in draft.unmapped:
            draft.unmapped.append(miss)
        return name or ""

    # Trade allocation of an invoice with stock lines.
    allocated: Optional[str] = None
    if is_invoice and stock_items:
        trade = [ln for ln in lines if _kind(ln) == "trade"]
        main = [
            ln for ln in trade if trade_account and ln.account.pk == trade_account.pk
        ]
        pick = main or sorted(trade, key=lambda ln: -abs(ln.amount))[:1]
        if pick:
            acct = pick[0].account
            same = [ln for ln in trade if ln.account.pk == acct.pk]
            total = sum((ln.amount for ln in same), ZERO)
            lines = [ln for ln in lines if ln not in same]
            allocated = name_of(same[0]) or f"?account:{acct.pk}"
            _stock_lines(draft, stock_items, index, total, allocated)
            residual = abs(total) - sum((s.amount for s in draft.stock), ZERO)
            if residual != 0:
                # non-stock lines / rounding: stays on the same ledger
                sign = 1 if total > 0 else -1
                lines.append(_Line(acct, None, None, sign * residual))
            if not draft.stock:
                allocated = None
                lines.append(_Line(acct, None, None, total))
    # Group the rest into ledger entries (same ledger + same side merge).
    grouped: Dict[Tuple[str, bool], LedgerEntry] = {}
    order: List[Tuple[str, bool]] = []
    for ln in lines:
        nm = name_of(ln) or f"?account:{ln.account.pk}"
        key = (nm, ln.amount > 0)
        if key not in grouped:
            grouped[key] = LedgerEntry(nm, ZERO, is_party=_kind(ln) == "party")
            order.append(key)
        entry = grouped[key]
        entry.amount += ln.amount
        if entry.is_party:
            entry.bills.append(_bill(draft, ln))
    draft.entries = [grouped[k] for k in order]
    for e in draft.entries:
        if e.is_party and not draft.party_ledger:
            draft.party_ledger = e.ledger


def _bill(draft: VoucherDraft, ln: _Line) -> Bill:
    if ln.against_no == draft.name:
        return Bill("New Ref", draft.name, ln.amount)
    if ln.against_type in ON_ACCOUNT_SOURCES:
        # A payment reconciliation consumes a receipt / advance. In Tally that money
        # sits "On Account" (no bill name), so the debit side of the reconciling
        # Journal is On Account too and nets against it.
        return Bill("On Account", "", ln.amount)
    if ln.against_no:
        return Bill("Agst Ref", ln.against_no, ln.amount)
    return Bill("On Account", "", ln.amount)


def _unit_text(index_unit: str, item) -> str:
    return index_unit or item.stock_uom.name


def _stock_lines(draft, stock_items, index: MapIndex, total: Decimal, ledger: str):
    inward = total > 0  # a debited trade ledger means stock came in
    for li in stock_items:
        mapped, miss = index.item(li.item)
        if miss:
            if miss not in draft.unmapped:
                draft.unmapped.append(miss)
            name, unit = "", ""
        else:
            name, unit = mapped
        qty = abs(li.qty)
        amount = abs(li.amount)
        draft.stock.append(
            StockLine(
                item_name=name or f"?item:{li.item_id}",
                unit=_unit_text(unit, li.item),
                qty=qty,
                rate=(amount / qty) if qty else Decimal("0"),
                amount=amount,
                godown=li.warehouse.name if li.warehouse else C.DEFAULT_GODOWN,
                batch=li.batch.batch_no if li.batch else "",
                inward=inward,
                ledger=ledger,
            )
        )


# --------------------------------------------------------------------------
# Stock Journal (quantities only, from the stock ledger)
# --------------------------------------------------------------------------


def _build_stock_journal(draft: VoucherDraft, doc, index: MapIndex):
    from stock.models import StockLedgerEntry

    draft.is_stock_journal = True
    draft.narration = getattr(doc, "remarks", None) or ""
    sles = (
        StockLedgerEntry.objects.filter(
            voucher_type=draft.doctype, voucher_no=draft.name, is_cancelled=False
        )
        .select_related("item", "item__stock_uom", "warehouse", "batch")
        .order_by("id")
    )
    for s in sles:
        mapped, miss = index.item(s.item)
        if miss:
            if miss not in draft.unmapped:
                draft.unmapped.append(miss)
            name, unit = "", ""
        else:
            name, unit = mapped
        qty = abs(s.qty)
        draft.stock.append(
            StockLine(
                item_name=name or f"?item:{s.item_id}",
                unit=_unit_text(unit, s.item),
                qty=qty,
                rate=abs(s.valuation_rate),
                amount=abs(s.stock_value_difference),
                godown=s.warehouse.name,
                batch=s.batch.batch_no if s.batch else "",
                inward=s.qty > 0,
            )
        )
    if not draft.stock:
        draft.problems.append("no_stock_entries")
