"""Tally history import: Tally vouchers -> historical Sales / Purchase Invoices and
Payment Entries (planning/11-tally-history-import.md).

Every Tally voucher is already in the books as a ``TLY-*`` Journal Entry. This
module only adds the *documents* (with their item lines) the owner wants to see,
as historical rows: submitted, ``is_historical=True``, ``source_journal`` = that
journal, status ``Historical``, outstanding 0. They are bulk-inserted, so no
``save()`` / ``submit()`` hook runs and nothing is posted: no GLEntry, no
StockLedgerEntry, no Bin change.

The journal is the truth for money, party, accounts and date. The history file
(``export_tally_history``) only adds the item lines and the supplier bill number.
A voucher is created only when everything ties to the paisa; anything else is
skipped with a reason and listed in the report. Nothing is guessed: no item,
customer or supplier is ever created, and no amount is adjusted to fit.
"""

from __future__ import annotations

import gzip
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from django.db import connection, transaction
from django.db.models import Sum

from accounts.models import (
    AccountType,
    InvoiceStatus,
    JournalEntry,
    JournalEntryLine,
    PaymentEntry,
    PaymentEntryReference,
    PaymentType,
)
from accounts.reports.tally import _REMARK, TALLY_PREFIX
from buying.models import (
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseInvoiceTax,
    Supplier,
)
from core.models import DocStatus
from selling.models import Customer, SalesInvoice, SalesInvoiceItem, SalesInvoiceTax
from stock.models import Item

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
QTY = Decimal("0.001")

SI, PI, PE = "Sales Invoice", "Purchase Invoice", "Payment Entry"

#: Tally voucher type -> (document, is_return, payment_type). Types not listed stay
#: journals only (Journal, Contra, Stock Journal, ...). Override with --type-map.
DEFAULT_TYPE_MAP: dict[str, tuple[str, bool, Any]] = {
    "Sales": (SI, False, None),
    "Sales Gst": (SI, False, None),
    "TAX INVOICE": (SI, False, None),
    "Tax Free Sales": (SI, False, None),
    "Credit Note": (SI, True, None),
    "Purchase": (PI, False, None),
    "Purchase Gst": (PI, False, None),
    "Debit Note": (PI, True, None),
    "Receipt": (PE, False, PaymentType.RECEIVE),
    "Payment": (PE, False, PaymentType.PAY),
}

_DOC_BY_KEYWORD = {
    "sales": (SI, False, None),
    "credit note": (SI, True, None),
    "purchase": (PI, False, None),
    "debit note": (PI, True, None),
    "receipt": (PE, False, PaymentType.RECEIVE),
    "payment": (PE, False, PaymentType.PAY),
}


def parse_type_map(raw: dict[str, str]) -> dict[str, tuple[str, bool, Any]]:
    """``{"My Sales": "sales", "Old Journal": "journal"}`` -> type map entries."""
    out: dict[str, tuple[str, bool, Any]] = {}
    for tally_type, kind in raw.items():
        key = (kind or "").strip().lower()
        if key in ("journal", "none", ""):
            out[tally_type] = None  # type: ignore[assignment]
        elif key in _DOC_BY_KEYWORD:
            out[tally_type] = _DOC_BY_KEYWORD[key]
        else:
            raise ValueError(
                f"Unknown document kind '{kind}' for Tally type '{tally_type}' "
                f"(use one of: {', '.join(_DOC_BY_KEYWORD)}, journal)."
            )
    return out


class Skip(Exception):
    """The voucher stays a journal only; ``reason`` is reported."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def norm(s: str | None) -> str:
    return " ".join((s or "").split()).lower()


def dec(v: Any) -> Decimal:
    if v is None or v == "":
        return Decimal(0)
    try:
        return Decimal(str(v))
    except InvalidOperation as exc:
        raise Skip("unreadable number in the history file", str(v)) from exc


def money(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def read_source(path: str) -> Iterable[dict[str, Any]]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:  # type: ignore[operator]
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


# --------------------------------------------------------------------- context


@dataclass
class JLine:
    account_id: int
    account_name: str
    account_type: str | None
    v: Decimal  # debit - credit
    party_type: str
    party: str


@dataclass
class Journal:
    id: int
    name: str
    posting_date: date
    docstatus: int
    tally_type: str
    tally_no: str
    cheque_no: str
    bill_no: str
    lines: list[JLine] = field(default_factory=list)


class Context:
    """Everything the import looks up, loaded once per run (company-scoped)."""

    def __init__(self, company, placeholder_item: Item | None = None):
        self.company = company
        self.placeholder_item = placeholder_item
        self.journals: dict[str, Journal] = {}
        for (
            jid,
            name,
            pdate,
            status,
            remark,
            cheque,
            bill,
        ) in JournalEntry.objects.filter(
            company=company, name__startswith=TALLY_PREFIX
        ).values_list(
            "id",
            "name",
            "posting_date",
            "docstatus",
            "user_remark",
            "cheque_no",
            "bill_no",
        ):
            m = _REMARK.match(remark or "")
            self.journals[name] = Journal(
                id=jid,
                name=name,
                posting_date=pdate,
                docstatus=status,
                tally_type=m.group("type").strip() if m else "",
                tally_no=m.group("no").strip() if m else "",
                cheque_no=cheque or "",
                bill_no=bill or "",
            )
        by_id = {j.id: j for j in self.journals.values()}
        rows = (
            JournalEntryLine.objects.filter(journal_entry_id__in=list(by_id))
            .values_list(
                "journal_entry_id",
                "account_id",
                "account__name",
                "account__account_type",
                "debit",
                "credit",
                "party_type",
                "party",
            )
            .order_by("journal_entry_id", "id")
        )
        for jid, acc, acc_name, acc_type, dr, cr, ptype, party in rows.iterator(
            chunk_size=10000
        ):
            by_id[jid].lines.append(
                JLine(
                    acc,
                    acc_name,
                    acc_type,
                    (dr or ZERO) - (cr or ZERO),
                    ptype or "",
                    party or "",
                )
            )

        self.linked: set[int] = set()
        self.names_taken: dict[str, set[str]] = {}
        for label, model in (
            (SI, SalesInvoice),
            (PI, PurchaseInvoice),
            (PE, PaymentEntry),
        ):
            self.linked.update(
                model.objects.exclude(source_journal__isnull=True).values_list(
                    "source_journal_id", flat=True
                )
            )
            self.names_taken[label] = set(model.objects.values_list("name", flat=True))

        self.customers = _by_norm_name(Customer.objects.filter(company=company))
        self.suppliers = _by_norm_name(Supplier.objects.filter(company=company))

        # Tally stock item -> ERP item: the Tally item map first, then the exact item
        # name, then the exact item code. More than one candidate = ambiguous.
        self.item_map: dict[str, set[int]] = defaultdict(set)
        from tally_export.models import TallyStockItemMap

        for tally_item, item_id in TallyStockItemMap.objects.filter(
            company=company
        ).values_list("tally_item", "item_id"):
            self.item_map[norm(tally_item)].add(item_id)
        self.item_names: dict[str, set[int]] = defaultdict(set)
        self.item_codes: dict[str, set[int]] = defaultdict(set)
        for iid, code, name in Item.objects.values_list("id", "code", "name"):
            self.item_names[norm(name)].add(iid)
            self.item_codes[norm(code)].add(iid)

        self.bills_used: set[tuple[str, int]] = {
            (b.lower(), s)
            for b, s in PurchaseInvoice.objects.exclude(docstatus=DocStatus.CANCELLED)
            .exclude(bill_no="")
            .values_list("bill_no", "supplier_id")
        }

    def item_for(self, tally_name: str) -> int | None | str:
        """The ERP item id, ``None`` if unknown, ``"ambiguous"`` if not unique."""
        key = norm(tally_name)
        for source in (self.item_map, self.item_names, self.item_codes):
            ids = source.get(key)
            if ids:
                return next(iter(ids)) if len(ids) == 1 else "ambiguous"
        return None


def _by_norm_name(qs) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for obj in qs:
        k = norm(obj.name)
        out[k] = "ambiguous" if k in out else obj
    return out


# --------------------------------------------------------------------- planning


@dataclass
class Plan:
    kind: str
    journal: Journal
    header: dict[str, Any]
    items: list[dict[str, Any]] = field(default_factory=list)
    taxes: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    party_type: str = ""
    party_name: str = ""
    amount: Decimal = ZERO


def plan_voucher(rec: dict[str, Any], mapping, ctx: Context) -> Plan:
    """Build the document for one voucher, or raise :class:`Skip`."""
    kind, is_return, payment_type = mapping
    journal = _journal_for(rec, ctx)
    if kind == PE:
        return _plan_payment(rec, journal, payment_type, ctx)
    return _plan_invoice(rec, journal, kind, is_return, ctx)


def _journal_for(rec, ctx: Context) -> Journal:
    name = rec.get("journal_name")
    if not name:
        raise Skip("no TLY journal matched in the export", rec.get("match_error") or "")
    journal = ctx.journals.get(name)
    if journal is None:
        raise Skip("TLY journal not found in this database", name)
    if journal.docstatus != DocStatus.SUBMITTED:
        raise Skip("TLY journal is not submitted", name)
    if journal.id in ctx.linked:
        raise Skip("already imported")
    if (
        journal.tally_type != (rec.get("vchtype") or "").strip()
        or journal.tally_no != (rec.get("vouchernumber") or "").strip()
    ):
        raise Skip(
            "journal remark does not match the voucher type / number",
            f"{name}: {journal.tally_type} {journal.tally_no}",
        )
    if str(journal.posting_date) != str(rec.get("date")):
        raise Skip(
            "voucher date differs from the journal date",
            f"{name}: {rec.get('date')} vs {journal.posting_date}",
        )
    return journal


def _party(journal: Journal) -> tuple[str, str, list[JLine]]:
    party_lines = [ln for ln in journal.lines if ln.party_type and ln.party]
    parties = {(ln.party_type, ln.party) for ln in party_lines}
    if not parties:
        raise Skip("journal has no party line (e.g. a cash sale)")
    if len(parties) > 1:
        raise Skip(
            "journal has more than one party", "; ".join(sorted(p for _, p in parties))
        )
    ptype, party = parties.pop()
    return ptype, party, party_lines


def _check_tally_party_amount(rec, amount: Decimal, name: str):
    tally = rec.get("party_amount")
    if tally not in (None, "") and money(abs(dec(tally))) != money(abs(amount)):
        raise Skip(
            "Tally party amount differs from the journal",
            f"{name}: Tally {money(abs(dec(tally)))} vs journal {money(abs(amount))}",
        )


def _plan_invoice(
    rec, journal: Journal, kind: str, is_return: bool, ctx: Context
) -> Plan:
    expected = "Customer" if kind == SI else "Supplier"
    ptype, party_name, party_lines = _party(journal)
    if ptype != expected:
        raise Skip(f"party is a {ptype}, not a {expected}", party_name)
    party = (ctx.customers if kind == SI else ctx.suppliers).get(norm(party_name))
    if party is None:
        raise Skip(f"{expected.lower()} not found in the ERP", party_name)
    if party == "ambiguous":
        raise Skip(f"more than one {expected.lower()} has this name", party_name)

    # Orientation: +1 when the party line is a debit on a normal invoice (sales).
    o = Decimal(1) if kind == SI else Decimal(-1)
    party_ids = {id(ln) for ln in party_lines}
    P = o * sum((ln.v for ln in party_lines), ZERO)
    tax_by_acc: dict[int, tuple[str, Decimal]] = {}
    other_by_acc: dict[int, tuple[str, Decimal]] = {}
    R = ZERO
    for ln in journal.lines:
        if id(ln) in party_ids:
            continue
        amt = -o * ln.v
        if ln.account_type == AccountType.TAX:
            prev = tax_by_acc.get(ln.account_id, (ln.account_name, ZERO))[1]
            tax_by_acc[ln.account_id] = (ln.account_name, prev + amt)
        elif ln.account_type == AccountType.ROUND_OFF:
            R += amt
        else:
            prev = other_by_acc.get(ln.account_id, (ln.account_name, ZERO))[1]
            other_by_acc[ln.account_id] = (ln.account_name, prev + amt)
    T = sum((a for _, a in tax_by_acc.values()), ZERO)
    O = sum((a for _, a in other_by_acc.values()), ZERO)
    sign = Decimal(-1) if is_return else Decimal(1)
    if P == ZERO or (P > 0) != (sign > 0):
        raise Skip(
            "party line is on the wrong side for this voucher type",
            f"{journal.name}: {P}",
        )
    _check_tally_party_amount(rec, P, journal.name)

    items = _item_lines(rec, sign, O, ctx)
    net = money(sum((it["amount"] for it in items), ZERO))
    if net != money(O):
        others = ", ".join(sorted(n for n, a in other_by_acc.values() if a))
        raise Skip(
            "item lines do not tie to the journal",
            f"{journal.name}: items {net} vs journal {money(O)} ({others})",
        )
    grand = net + money(T)
    if money(grand + R) != money(P):
        raise Skip("journal does not balance", journal.name)
    for v in (net, grand, P):
        if v != ZERO and (v > 0) != (sign > 0):
            raise Skip(
                "totals have mixed signs",
                f"{journal.name}: net {net}, total {grand}, party {P}",
            )

    party_account = max(party_lines, key=lambda ln: abs(ln.v)).account_id
    if other_by_acc:
        main_account = max(
            other_by_acc.items(), key=lambda kv: (abs(kv[1][1]), -kv[0])
        )[0]
    else:
        main_account = getattr(
            ctx.company,
            "default_income_account_id" if kind == SI else "default_expense_account_id",
        )
        if not main_account:
            raise Skip("journal has no income / expense line", journal.name)

    taxes, running = [], net
    for idx, (acc_id, (acc_name, amt)) in enumerate(
        sorted(((k, v) for k, v in tax_by_acc.items() if v[1]), key=lambda kv: kv[0]),
        start=1,
    ):
        running += money(amt)
        taxes.append(
            {
                "idx": idx,
                "account_id": acc_id,
                "description": acc_name[:200],
                "net_amount": net,
                "tax_amount": money(amt),
                "total": running,
            }
        )

    header: dict[str, Any] = {
        "name": journal.name,
        "company": ctx.company,
        "posting_date": journal.posting_date,
        "docstatus": DocStatus.SUBMITTED,
        "is_historical": True,
        "source_journal_id": journal.id,
        "is_return": is_return,
        "update_stock": False,
        "net_total": net,
        "total_taxes_and_charges": money(T),
        "grand_total": grand,
        "rounding_adjustment": money(R),
        "rounded_total": money(P),
        "outstanding_amount": ZERO,
        "status": InvoiceStatus.HISTORICAL,
    }
    plan = Plan(
        kind,
        journal,
        header,
        items,
        taxes,
        party_type=expected,
        party_name=party.name,
        amount=money(P),
    )
    if kind == SI:
        header.update(
            customer=party, debit_to_id=party_account, income_account_id=main_account
        )
    else:
        header.update(
            supplier=party, credit_to_id=party_account, expense_account_id=main_account
        )
        bill = ((rec.get("reference") or "").strip() or journal.bill_no.strip())[:140]
        if bill and (bill.lower(), party.pk) in ctx.bills_used:
            plan.notes.append(
                "supplier bill no. already used for this supplier; left blank"
            )
            bill = ""
        header["bill_no"] = bill
    return plan


def _item_lines(rec, sign: Decimal, O: Decimal, ctx: Context) -> list[dict[str, Any]]:
    entries = rec.get("inventory_entries") or []
    if not entries:
        if ctx.placeholder_item is None:
            raise Skip("voucher has no item lines")
        amt = money(abs(O))
        return [
            {
                "item_id": ctx.placeholder_item.pk,
                "qty": sign * Decimal("1.000"),
                "rate": amt,
                "amount": sign * amt,
            }
        ]
    raw_amounts = [dec(e.get("amount")) for e in entries]
    if any(a > 0 for a in raw_amounts) and any(a < 0 for a in raw_amounts):
        raise Skip("item lines have mixed signs (e.g. a return inside an invoice)")
    out, unknown, ambiguous = [], [], []
    for e, raw in zip(entries, raw_amounts):
        tally_name = (e.get("stockitemname") or "").strip()
        if not tally_name:
            raise Skip("item line without a stock item name")
        item_id = ctx.item_for(tally_name)
        if item_id is None:
            unknown.append(tally_name)
            continue
        if item_id == "ambiguous":
            ambiguous.append(tally_name)
            continue
        qty_raw = (
            e.get("billedqty")
            if e.get("billedqty") not in (None, "")
            else e.get("actualqty")
        )
        qty = abs(dec(qty_raw)).quantize(QTY, rounding=ROUND_HALF_UP)
        amount = money(abs(raw))
        if e.get("rate") not in (None, ""):
            rate = money(abs(dec(e.get("rate"))))
        else:
            rate = money(amount / qty) if qty else amount
        out.append(
            {
                "item_id": item_id,
                "qty": sign * qty,
                "rate": rate,
                "amount": sign * amount,
            }
        )
    if unknown:
        raise Skip("unmatched Tally item", "; ".join(sorted(set(unknown))))
    if ambiguous:
        raise Skip(
            "Tally item matches more than one ERP item",
            "; ".join(sorted(set(ambiguous))),
        )
    return out


def _plan_payment(rec, journal: Journal, payment_type: str, ctx: Context) -> Plan:
    ptype, party_name, party_lines = _party(journal)
    book = (
        ctx.customers
        if ptype == "Customer"
        else ctx.suppliers if ptype == "Supplier" else {}
    )
    party = book.get(norm(party_name))
    if party is None or party == "ambiguous":
        raise Skip(f"{ptype.lower() or 'party'} not found in the ERP", party_name)
    party_ids = {id(ln) for ln in party_lines}
    bank = [
        ln
        for ln in journal.lines
        if id(ln) not in party_ids
        and ln.account_type in (AccountType.BANK, AccountType.CASH)
    ]
    other = [
        ln
        for ln in journal.lines
        if id(ln) not in party_ids and ln not in bank and ln.v
    ]
    if other:
        raise Skip(
            "journal has lines other than the party and one bank / cash account",
            f"{journal.name}: {', '.join(sorted({ln.account_name for ln in other}))}",
        )
    bank_accounts = {ln.account_id for ln in bank if ln.v}
    if len(bank_accounts) != 1:
        raise Skip(
            "journal does not have exactly one bank / cash account", journal.name
        )
    v_party = sum((ln.v for ln in party_lines), ZERO)
    v_bank = sum((ln.v for ln in bank), ZERO)
    amount = -v_party if payment_type == PaymentType.RECEIVE else v_party
    if amount <= 0:
        raise Skip(
            "party line is on the wrong side for this voucher type",
            f"{journal.name}: {v_party}",
        )
    if money(v_bank + v_party) != ZERO:
        raise Skip("journal does not balance", journal.name)
    _check_tally_party_amount(rec, amount, journal.name)
    party_account = max(party_lines, key=lambda ln: abs(ln.v)).account_id
    bank_account = bank_accounts.pop()
    if payment_type == PaymentType.RECEIVE:
        paid_from, paid_to = party_account, bank_account
    else:
        paid_from, paid_to = bank_account, party_account
    header = {
        "name": journal.name,
        "company": ctx.company,
        "payment_type": payment_type,
        "posting_date": journal.posting_date,
        "party_type": ptype,
        "party_name": party.name,
        "paid_from_id": paid_from,
        "paid_to_id": paid_to,
        "paid_amount": money(amount),
        "received_amount": money(amount),
        "reference_no": journal.cheque_no[:100] or None,
        "docstatus": DocStatus.SUBMITTED,
        "is_historical": True,
        "source_journal_id": journal.id,
    }
    return Plan(
        PE,
        journal,
        header,
        party_type=ptype,
        party_name=party.name,
        amount=money(amount),
    )


# --------------------------------------------------------------------- writing


def write_plans(plans: list[Plan]) -> None:
    """Bulk-insert one batch of planned documents (no save()/submit() hooks)."""
    sis = [p for p in plans if p.kind == SI]
    pis = [p for p in plans if p.kind == PI]
    pes = [p for p in plans if p.kind == PE]
    for group, model, item_model, tax_model, fk in (
        (sis, SalesInvoice, SalesInvoiceItem, SalesInvoiceTax, "sales_invoice"),
        (
            pis,
            PurchaseInvoice,
            PurchaseInvoiceItem,
            PurchaseInvoiceTax,
            "purchase_invoice",
        ),
    ):
        if not group:
            continue
        model.objects.bulk_create([model(**p.header) for p in group])
        ids = dict(
            model.objects.filter(name__in=[p.journal.name for p in group]).values_list(
                "name", "id"
            )
        )
        item_model.objects.bulk_create(
            [
                item_model(**{f"{fk}_id": ids[p.journal.name]}, **it)
                for p in group
                for it in p.items
            ]
        )
        tax_model.objects.bulk_create(
            [
                tax_model(**{f"{fk}_id": ids[p.journal.name]}, **tx)
                for p in group
                for tx in p.taxes
            ]
        )
    if pes:
        PaymentEntry.objects.bulk_create([PaymentEntry(**p.header) for p in pes])


def allocate(company, parties: set[tuple[str, str]]) -> int:
    """Display-only links: each historical receipt / payment that has none yet is
    allocated to the party's historical invoices oldest-first (invoices dated on or
    before the payment). Historical invoices derive outstanding 0 regardless
    (``outstanding.derive``), and opening bills are never referenced, so no
    outstanding anywhere changes."""
    created = 0
    for party_type, party_name in sorted(parties):
        if party_type == "Customer":
            model, flow, filt = (
                SalesInvoice,
                PaymentType.RECEIVE,
                {"customer__name": party_name},
            )
        elif party_type == "Supplier":
            model, flow, filt = (
                PurchaseInvoice,
                PaymentType.PAY,
                {"supplier__name": party_name},
            )
        else:
            continue
        label = model.voucher_label()
        invoices = list(
            model.objects.filter(
                company=company, is_historical=True, is_return=False, **filt
            )
            .order_by("posting_date", "name")
            .values_list("name", "posting_date", "rounded_total")
        )
        if not invoices:
            continue
        used = dict(
            PaymentEntryReference.objects.filter(
                payment_entry__is_historical=True,
                reference_doctype=label,
                reference_name__in=[n for n, _, _ in invoices],
            )
            .values_list("reference_name")
            .annotate(t=Sum("allocated_amount"))
            .values_list("reference_name", "t")
        )
        remaining = {n: total - (used.get(n) or ZERO) for n, _, total in invoices}
        payments = (
            PaymentEntry.objects.filter(
                company=company,
                is_historical=True,
                payment_type=flow,
                party_type=party_type,
                party_name=party_name,
                references__isnull=True,
            )
            .order_by("posting_date", "name")
            .values_list("id", "posting_date", "paid_amount")
        )
        refs = []
        for pe_id, pdate, amount in payments:
            left = amount
            for name, idate, _ in invoices:
                if left <= 0 or idate > pdate:
                    break
                take = min(left, remaining[name])
                if take > 0:
                    refs.append(
                        PaymentEntryReference(
                            payment_entry_id=pe_id,
                            reference_doctype=label,
                            reference_name=name,
                            allocated_amount=take,
                        )
                    )
                    remaining[name] -= take
                    left -= take
        PaymentEntryReference.objects.bulk_create(refs)
        created += len(refs)
    return created


def books_fingerprint() -> tuple:
    """GL / stock state that must not move: counts and sums of GLEntry,
    StockLedgerEntry and Bin."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*), coalesce(sum(debit),0), coalesce(sum(credit),0) FROM accounts_glentry"
        )
        gl = tuple(cur.fetchone())
        cur.execute(
            "SELECT count(*), coalesce(sum(qty),0), coalesce(sum(stock_value_difference),0) "
            "FROM stock_stockledgerentry"
        )
        sle = tuple(cur.fetchone())
        cur.execute(
            "SELECT count(*), coalesce(sum(actual_qty),0), coalesce(sum(stock_value),0) FROM stock_bin"
        )
        bins = tuple(cur.fetchone())
    return tuple(Decimal(str(x)) for x in (*gl, *sle, *bins))


class BooksChanged(Exception):
    pass


# --------------------------------------------------------------------- run


@dataclass
class Result:
    counts: dict[tuple[str, str], Counter] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    reasons: Counter = field(default_factory=Counter)
    examples: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    unmatched_items: Counter = field(default_factory=Counter)
    unmapped_types: Counter = field(default_factory=Counter)
    notes: Counter = field(default_factory=Counter)
    skipped_rows: list[dict[str, str]] = field(default_factory=list)
    parties: set[tuple[str, str]] = field(default_factory=set)
    references: int = 0
    last_date: date | None = None
    party_rows: list[dict[str, Any]] = field(default_factory=list)


def run_import(
    source: str,
    company,
    *,
    year: str | None = None,
    commit: bool = False,
    batch_size: int = 500,
    type_map: dict | None = None,
    placeholder_item: Item | None = None,
) -> Result:
    """Import ``source`` into ``company``. Dry run unless ``commit``: everything is
    done inside one transaction that is rolled back, so the report is exact."""
    mapping = dict(DEFAULT_TYPE_MAP)
    mapping.update(type_map or {})
    result = Result()
    before = books_fingerprint()

    def body():
        ctx = Context(company, placeholder_item)
        batch: list[Plan] = []

        def flush():
            if not batch:
                return
            with transaction.atomic():
                write_plans(batch)
                if books_fingerprint() != before:
                    raise BooksChanged(
                        "GL / stock ledger / Bin changed while writing a batch"
                    )
            batch.clear()

        for rec in read_source(source):
            fy = rec.get("fy") or "?"
            if year and fy != year:
                continue
            vt = (rec.get("vchtype") or "").strip()
            row = result.counts[(vt, fy)]
            row["tally"] += 1
            target = mapping.get(vt)
            if target is None:
                row["journal_only"] += 1
                result.unmapped_types[vt] += 1
                continue
            try:
                plan = plan_voucher(rec, target, ctx)
            except Skip as skip:
                key = "already" if skip.reason == "already imported" else "skipped"
                row[key] += 1
                if key == "skipped":
                    result.reasons[skip.reason] += 1
                    if len(result.examples[skip.reason]) < 5:
                        result.examples[skip.reason].append(
                            f"{vt} {rec.get('vouchernumber') or ''} {rec.get('date')}: {skip.detail}".strip()
                        )
                    result.skipped_rows.append(
                        {
                            "fy": fy,
                            "type": vt,
                            "number": rec.get("vouchernumber") or "",
                            "date": str(rec.get("date")),
                            "journal": rec.get("journal_name") or "",
                            "reason": skip.reason,
                            "detail": skip.detail,
                        }
                    )
                    if skip.reason == "unmatched Tally item":
                        for name in skip.detail.split("; "):
                            result.unmatched_items[name] += 1
                continue
            ctx.linked.add(plan.journal.id)
            if plan.kind == PI and plan.header.get("bill_no"):
                ctx.bills_used.add(
                    (plan.header["bill_no"].lower(), plan.header["supplier"].pk)
                )
            for note in plan.notes:
                result.notes[note] += 1
            row["created"] += 1
            result.parties.add((plan.party_type, plan.party_name))
            if result.last_date is None or plan.journal.posting_date > result.last_date:
                result.last_date = plan.journal.posting_date
            batch.append(plan)
            if len(batch) >= batch_size:
                flush()
        flush()
        with transaction.atomic():
            result.references = allocate(company, result.parties)
        if books_fingerprint() != before:
            raise BooksChanged("GL / stock ledger / Bin changed")

    if commit:
        body()
    else:
        with transaction.atomic():
            body()
            result.party_rows = party_report(company, result.parties, result.last_date)
            transaction.set_rollback(True)
        return result
    result.party_rows = party_report(company, result.parties, result.last_date)
    return result


def party_report(
    company, parties: set[tuple[str, str]], as_of: date | None
) -> list[dict[str, Any]]:
    """Per party: historical invoices minus historical receipts / payments, against
    the party's ledger balance on the same date (from the GL, which still holds
    every rupee as TLY journals plus openings). For information: the difference is
    the opening balance and the journal-only vouchers."""
    from accounts.models import GLEntry

    rows: list[dict[str, Any]] = []
    if as_of is None:
        return rows
    for party_type, name in sorted(parties):
        if party_type == "Customer":
            inv = SalesInvoice.objects.filter(
                company=company, is_historical=True, customer__name=name
            )
            flow, sign = PaymentType.RECEIVE, Decimal(1)
        else:
            inv = PurchaseInvoice.objects.filter(
                company=company, is_historical=True, supplier__name=name
            )
            flow, sign = PaymentType.PAY, Decimal(-1)
        invoiced = (
            inv.filter(posting_date__lte=as_of).aggregate(t=Sum("rounded_total"))["t"]
            or ZERO
        )
        pes = PaymentEntry.objects.filter(
            company=company,
            is_historical=True,
            party_type=party_type,
            party_name=name,
            posting_date__lte=as_of,
        )
        paid = (
            pes.filter(payment_type=flow).aggregate(t=Sum("paid_amount"))["t"] or ZERO
        )
        refunded = (
            pes.exclude(payment_type=flow).aggregate(t=Sum("paid_amount"))["t"] or ZERO
        )
        agg = GLEntry.objects.filter(
            company=company,
            party_type=party_type,
            party=name,
            is_cancelled=False,
            posting_date__lte=as_of,
        ).aggregate(dr=Sum("debit"), cr=Sum("credit"))
        ledger = sign * ((agg["dr"] or ZERO) - (agg["cr"] or ZERO))
        documents = invoiced - paid + refunded
        rows.append(
            {
                "party_type": party_type,
                "party": name,
                "as_of": str(as_of),
                "documents_net": documents,
                "ledger_balance": ledger,
                "difference": ledger - documents,
            }
        )
    return rows
