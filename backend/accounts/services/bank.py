"""Bank reconciliation: clearance, statement matching and the reconciliation statement.

Nothing here edits a posted document or an imported statement line. Reconciling is
adding ``BankClearance`` facts (voucher X cleared the bank on D / was un-cleared);
what is "reconciled" is always derived from them:

* a voucher is *cleared* when its latest ``BankClearance`` for the bank account has a
  date;
* a statement line is *reconciled* when cleared vouchers point at it and their bank
  legs add up to its amount.

A voucher's *bank leg* is its net (debit - credit) on the bank's GL account; the
statement line's signed amount is deposit - withdrawal, so a receipt (debit the bank)
matches a deposit and a payment (credit the bank) a withdrawal.
"""

import datetime
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Min, Sum
from django.utils.translation import gettext as _

from accounts.models import (
    BankAccount,
    BankClearance,
    BankTransaction,
    GLEntry,
    JournalEntry,
    PaymentEntry,
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
DEFAULT_DATE_WINDOW = 15

Key = tuple  # (voucher_type "app.Model", voucher_no)


def _q(value):
    return (value or ZERO).quantize(CENT)


# ------------------------------------------------------------------ state


def latest_clearances(bank_account):
    """``{(voucher_type, voucher_no): latest BankClearance}`` of the account."""
    out = {}
    rows = BankClearance.objects.filter(bank_account=bank_account).order_by("id")
    for row in rows:
        out[(row.voucher_type, row.voucher_no)] = row
    return out


def cleared_map(bank_account):
    """The currently cleared vouchers: those whose latest record has a date."""
    return {
        key: row
        for key, row in latest_clearances(bank_account).items()
        if row.clearance_date is not None
    }


def transaction_links(cleared):
    """``{bank_transaction_id: [voucher keys]}`` from a ``cleared_map``."""
    links = {}
    for key, row in cleared.items():
        if row.bank_transaction_id:
            links.setdefault(row.bank_transaction_id, []).append(key)
    return links


def bank_legs(bank_account, to_date=None):
    """``{voucher key: leg}`` -- the live GL postings of each voucher on the bank's
    ledger account, ``net`` = debit - credit (positive = money into the bank)."""
    qs = GLEntry.objects.filter(
        company_id=bank_account.company_id,
        account_id=bank_account.account_id,
        is_cancelled=False,
    )
    if to_date:
        qs = qs.filter(posting_date__lte=to_date)
    rows = qs.values("voucher_type", "voucher_no").annotate(
        dr=Sum("debit"),
        cr=Sum("credit"),
        posting_date=Min("posting_date"),
        party=Max("party"),
    )
    legs = {}
    for row in rows:
        net = _q(row["dr"]) - _q(row["cr"])
        if net == 0:
            continue
        legs[(row["voucher_type"], row["voucher_no"])] = {
            "voucher_type": row["voucher_type"],
            "voucher_no": row["voucher_no"],
            "posting_date": row["posting_date"],
            "party": row["party"] or "",
            "net": net,
        }
    return legs


def _attach_references(legs):
    """Cheque / reference number and date of each leg (Journal / Payment Entry)."""
    by_type = {}
    for key in legs:
        by_type.setdefault(key[0], []).append(key[1])
    for key in legs:
        legs[key].setdefault("reference_no", "")
        legs[key].setdefault("reference_date", None)
    for je in JournalEntry.objects.filter(
        name__in=by_type.get("accounts.JournalEntry", [])
    ).only("name", "cheque_no", "cheque_date"):
        leg = legs[("accounts.JournalEntry", je.name)]
        leg["reference_no"] = je.cheque_no or ""
        leg["reference_date"] = je.cheque_date
    for pe in PaymentEntry.objects.filter(
        name__in=by_type.get("accounts.PaymentEntry", [])
    ).only("name", "reference_no"):
        legs[("accounts.PaymentEntry", pe.name)]["reference_no"] = pe.reference_no or ""
    return legs


def uncleared_vouchers(bank_account, to_date=None, from_date=None):
    """Vouchers on the bank ledger that have not cleared, oldest first. Vouchers on
    or before ``reconciled_up_to`` count as reconciled and are left out."""
    legs = bank_legs(bank_account, to_date)
    cleared = cleared_map(bank_account)
    floor = bank_account.reconciled_up_to
    out = {}
    for key, leg in legs.items():
        if key in cleared:
            continue
        if floor and leg["posting_date"] <= floor:
            continue
        if from_date and leg["posting_date"] < from_date:
            continue
        out[key] = leg
    _attach_references(out)
    return sorted(
        out.values(),
        key=lambda leg: (leg["posting_date"], leg["voucher_type"], leg["voucher_no"]),
    )


def unreconciled_transactions(bank_account, from_date=None, to_date=None):
    links = transaction_links(cleared_map(bank_account))
    qs = BankTransaction.objects.filter(bank_account=bank_account)
    if from_date:
        qs = qs.filter(transaction_date__gte=from_date)
    if to_date:
        qs = qs.filter(transaction_date__lte=to_date)
    return [
        tx
        for tx in qs.order_by("transaction_date", "statement_import_id", "row_no", "id")
        if tx.pk not in links
    ]


# ------------------------------------------------------------------ suggestions


def _norm(text):
    return re.sub(r"[^0-9a-z]", "", (text or "").lower())


def _norm_ref(text):
    return _norm(text).lstrip("0")


def _reference_match(tx, leg):
    ref = _norm_ref(leg.get("reference_no"))
    if len(ref) < 3:
        return False
    return ref == _norm_ref(tx.reference_no) or ref in _norm(tx.description)


def suggest_matches(transactions, vouchers, window=DEFAULT_DATE_WINDOW):
    """Pair each statement line with at most one voucher of the same signed amount,
    by reference / cheque number first, then date proximity. Greedy and one-to-one:
    a voucher is suggested for one line only. Only suggestions; nothing is saved."""
    scored = []
    for tx in transactions:
        for leg in vouchers:
            if leg["net"] != tx.amount:
                continue
            ref_hit = _reference_match(tx, leg)
            leg_date = leg.get("reference_date") or leg["posting_date"]
            days = abs((tx.transaction_date - leg_date).days)
            if not ref_hit and days > window:
                continue
            scored.append(
                (
                    1 if ref_hit else 0,
                    -days,
                    tx.pk,
                    (leg["voucher_type"], leg["voucher_no"]),
                    "reference" if ref_hit else "date",
                    tx,
                    leg,
                )
            )
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    used_tx, used_voucher, out = set(), set(), []
    for _ref, _neg_days, tx_pk, key, basis, tx, leg in scored:
        if tx_pk in used_tx or key in used_voucher:
            continue
        used_tx.add(tx_pk)
        used_voucher.add(key)
        out.append({"bank_transaction": tx_pk, "voucher": leg, "basis": basis})
    return out


# ------------------------------------------------------------------ writing


def _lock(bank_account):
    return BankAccount.objects.select_for_update().get(pk=bank_account.pk)


def _validate_key(legs, key):
    leg = legs.get(key)
    if leg is None:
        raise ValidationError(
            _("%(type)s %(no)s has no live posting on this bank account.")
            % {"type": key[0], "no": key[1]}
        )
    return leg


def _record(user, bank_account, key, clearance_date, transaction_obj, remarks=""):
    from core.mixins import record_audit_log

    row = BankClearance.objects.create(
        company_id=bank_account.company_id,
        bank_account=bank_account,
        voucher_type=key[0],
        voucher_no=key[1],
        clearance_date=clearance_date,
        bank_transaction=transaction_obj,
        remarks=remarks,
        set_by=user if getattr(user, "is_authenticated", False) else None,
    )
    record_audit_log(
        user,
        "clear" if clearance_date else "unclear",
        row,
        changes={
            "bank_account": bank_account.pk,
            "voucher": f"{key[0]}:{key[1]}",
            "clearance_date": clearance_date.isoformat() if clearance_date else None,
            "bank_transaction": transaction_obj.pk if transaction_obj else None,
        },
    )
    return row


def match(user, tx, vouchers):
    """Match a statement line to one or more vouchers (many-to-one): their bank legs
    must add up to the line's signed amount. Sets each voucher's clearance date to
    the line's date. All or nothing."""
    keys = [(v["voucher_type"], v["voucher_no"]) for v in vouchers]
    if not keys:
        raise ValidationError(_("Choose at least one voucher."))
    if len(set(keys)) != len(keys):
        raise ValidationError(_("A voucher is listed more than once."))
    with transaction.atomic():
        bank_account = _lock(tx.bank_account)
        cleared = cleared_map(bank_account)
        if tx.pk in transaction_links(cleared):
            raise ValidationError(_("This bank transaction is already reconciled."))
        legs = bank_legs(bank_account)
        total = ZERO
        for key in keys:
            leg = _validate_key(legs, key)
            if key in cleared:
                raise ValidationError(
                    _("%(type)s %(no)s is already cleared.")
                    % {"type": key[0], "no": key[1]}
                )
            total += leg["net"]
        if total != tx.amount:
            raise ValidationError(
                _(
                    "The selected vouchers total %(total)s but the bank transaction "
                    "is %(amount)s."
                )
                % {"total": total, "amount": tx.amount}
            )
        return [
            _record(user, bank_account, key, tx.transaction_date, tx) for key in keys
        ]


def accept_suggestions(
    user,
    bank_account,
    from_date=None,
    to_date=None,
    window=DEFAULT_DATE_WINDOW,
    only_reference=False,
):
    """Confirm every suggested match in the period in one go (old statements
    hold years of lines). Each suggestion is applied through :func:`match`, so
    the same checks and one transaction per line apply; one that no longer fits
    is skipped and reported, not forced. ``only_reference`` keeps only the
    cheque / reference-number matches, the safest kind.

    Returns ``{"matched": n, "skipped": [{"bank_transaction", "reason"}]}``."""
    transactions = unreconciled_transactions(bank_account, from_date, to_date)
    vouchers = uncleared_vouchers(bank_account, to_date)
    by_pk = {tx.pk: tx for tx in transactions}
    matched, skipped = 0, []
    for suggestion in suggest_matches(transactions, vouchers, window):
        if only_reference and suggestion["basis"] != "reference":
            continue
        tx = by_pk[suggestion["bank_transaction"]]
        voucher = suggestion["voucher"]
        try:
            match(
                user,
                tx,
                [
                    {
                        "voucher_type": voucher["voucher_type"],
                        "voucher_no": voucher["voucher_no"],
                    }
                ],
            )
            matched += 1
        except ValidationError as exc:
            skipped.append(
                {"bank_transaction": tx.pk, "reason": "; ".join(exc.messages)}
            )
    return {"matched": matched, "skipped": skipped}


def unmatch(user, tx):
    """Undo a match: every voucher linked to the line is un-cleared."""
    with transaction.atomic():
        bank_account = _lock(tx.bank_account)
        keys = transaction_links(cleared_map(bank_account)).get(tx.pk)
        if not keys:
            raise ValidationError(_("This bank transaction is not reconciled."))
        return [_record(user, bank_account, key, None, None) for key in sorted(keys)]


def set_clearance(user, bank_account, items):
    """Manual clearance (ERPNext's Bank Clearance tool): ``items`` are dicts with
    ``voucher_type``, ``voucher_no`` and ``clearance_date`` (``None`` = un-clear)."""
    if not items:
        raise ValidationError(_("Choose at least one voucher."))
    with transaction.atomic():
        bank_account = _lock(bank_account)
        cleared = cleared_map(bank_account)
        legs = bank_legs(bank_account)
        seen, rows = set(), []
        for item in items:
            key = (item["voucher_type"], item["voucher_no"])
            if key in seen:
                raise ValidationError(_("A voucher is listed more than once."))
            seen.add(key)
            leg = _validate_key(legs, key)
            date = item.get("clearance_date")
            current = cleared.get(key)
            if date is None:
                if current is None:
                    raise ValidationError(
                        _("%(type)s %(no)s is not cleared.")
                        % {"type": key[0], "no": key[1]}
                    )
                if current.bank_transaction_id:
                    raise ValidationError(
                        _(
                            "%(no)s was matched to a bank transaction; unmatch that "
                            "transaction instead."
                        )
                        % {"no": key[1]}
                    )
            else:
                if current is not None:
                    raise ValidationError(
                        _("%(type)s %(no)s is already cleared.")
                        % {"type": key[0], "no": key[1]}
                    )
                if date < leg["posting_date"]:
                    raise ValidationError(
                        _(
                            "The clearance date of %(no)s cannot be before its posting date."
                        )
                        % {"no": key[1]}
                    )
            rows.append((key, date))
        return [_record(user, bank_account, key, date, None) for key, date in rows]


def assert_not_cleared(voucher_type, voucher_no):
    """Cancelling a voucher the bank already cleared would silently orphan the
    reconciliation: un-clear it first."""
    latest = {}
    for row in BankClearance.objects.filter(
        voucher_type=voucher_type, voucher_no=voucher_no
    ).order_by("id"):
        latest[row.bank_account_id] = row
    if any(row.clearance_date is not None for row in latest.values()):
        raise ValidationError(
            _(
                "%(no)s has cleared the bank. Un-clear it in the bank reconciliation "
                "before cancelling."
            )
            % {"no": voucher_no}
        )


# ------------------------------------------------------------------ report


def reconciliation_statement(bank_account, as_on):
    """Bank Reconciliation Statement as on a date (ERPNext report).

    ``balance as per GL`` - uncleared deposits (in the books, not yet in the bank)
    + uncleared payments (cheques issued, not yet presented) = ``balance as per bank``.
    A voucher counts as uncleared if it is not cleared, or cleared after ``as_on``.
    """
    gl = GLEntry.objects.filter(
        company_id=bank_account.company_id,
        account_id=bank_account.account_id,
        is_cancelled=False,
        posting_date__lte=as_on,
    ).aggregate(dr=Sum("debit"), cr=Sum("credit"))
    gl_balance = _q(gl["dr"]) - _q(gl["cr"])

    legs = bank_legs(bank_account, as_on)
    cleared = cleared_map(bank_account)
    floor = bank_account.reconciled_up_to
    pending = {}
    for key, leg in legs.items():
        row = cleared.get(key)
        if row is not None and row.clearance_date <= as_on:
            continue
        if floor and leg["posting_date"] <= floor:
            continue
        pending[key] = leg
    _attach_references(pending)

    def line(leg):
        return {
            "voucher_type": leg["voucher_type"],
            "voucher_no": leg["voucher_no"],
            "posting_date": leg["posting_date"],
            "reference_no": leg["reference_no"],
            "reference_date": leg["reference_date"],
            "party": leg["party"],
            "amount": abs(leg["net"]),
        }

    order = lambda leg: (leg["posting_date"], leg["voucher_no"])  # noqa: E731
    receipts = [
        line(leg) for leg in sorted(pending.values(), key=order) if leg["net"] > 0
    ]
    payments = [
        line(leg) for leg in sorted(pending.values(), key=order) if leg["net"] < 0
    ]
    uncleared_receipts = sum((r["amount"] for r in receipts), ZERO)
    uncleared_payments = sum((p["amount"] for p in payments), ZERO)
    bank_balance = gl_balance - uncleared_receipts + uncleared_payments

    last = (
        BankTransaction.objects.filter(
            bank_account=bank_account,
            transaction_date__lte=as_on,
            balance__isnull=False,
        )
        .order_by("-transaction_date", "-statement_import_id", "-row_no", "-id")
        .first()
    )
    statement_balance = last.balance if last else None
    return {
        "as_on": as_on,
        "gl_balance": gl_balance,
        "uncleared_receipts": receipts,
        "uncleared_receipts_total": uncleared_receipts,
        "uncleared_payments": payments,
        "uncleared_payments_total": uncleared_payments,
        "bank_balance": bank_balance,
        "statement_balance": statement_balance,
        "statement_balance_date": last.transaction_date if last else None,
        "difference": (
            (statement_balance - bank_balance)
            if statement_balance is not None
            else None
        ),
    }


# ------------------------------------------------------------------ integrity


def check_integrity():
    """Errors (strings) for ``verify_integrity``: every clearance references a live
    voucher touching its bank account, and every matched statement line's amount
    equals its vouchers' bank legs."""
    errors = []
    for bank_account in BankAccount.objects.select_related("account"):
        label = f"Bank account '{bank_account.account.name}'"
        if bank_account.account.account_type != "Bank" or bank_account.account.is_group:
            errors.append(f"{label}: its GL account is not a non-group Bank account.")
        if bank_account.account.company_id != bank_account.company_id:
            errors.append(f"{label}: its GL account belongs to another company.")
        cleared = cleared_map(bank_account)
        if not cleared:
            continue
        legs = bank_legs(bank_account)
        for key, row in cleared.items():
            if row.company_id != bank_account.company_id:
                errors.append(f"{label}: clearance of {key[1]} is in another company.")
            if key not in legs:
                errors.append(
                    f"{label}: {key[0]} {key[1]} is cleared but is not a submitted "
                    "voucher touching this bank account."
                )
        for tx_pk, keys in transaction_links(cleared).items():
            tx = BankTransaction.objects.filter(pk=tx_pk).first()
            if tx is None or tx.bank_account_id != bank_account.pk:
                errors.append(
                    f"{label}: a clearance points at a foreign bank transaction."
                )
                continue
            total = sum((legs[k]["net"] for k in keys if k in legs), ZERO)
            if total != tx.amount:
                errors.append(
                    f"{label}: bank transaction {tx.transaction_date} {tx.amount} "
                    f"is matched to vouchers totalling {total}."
                )
            for key in keys:
                if cleared[key].clearance_date != tx.transaction_date:
                    errors.append(
                        f"{label}: {key[1]} is cleared on {cleared[key].clearance_date} "
                        f"but its bank transaction is dated {tx.transaction_date}."
                    )
    return errors


def parse_date(value):
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(value)
