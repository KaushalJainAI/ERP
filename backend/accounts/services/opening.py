"""Opening bills: the outstanding bills carried in from the books before go-live.

An :class:`accounts.models.OpeningBill` posts no GL, so its ``outstanding_amount`` is
a projection of the payment allocations made against it, exactly as an invoice's is
projected from the ledger:

    outstanding = amount - sum(allocated_amount of submitted payment references)

It is written only here, under the bill's row lock, and can be rebuilt at any time
with :func:`repost` (``repost_outstanding``).
"""

from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils.translation import gettext as _

from accounts.models import AccountType, GLEntry, OpeningBill, PaymentEntryReference
from core.models import DocStatus, NamingSeries

ZERO = Decimal("0.00")


def allocated(bill, exclude_payment=None):
    """Sum of the submitted payment allocations against this opening bill, not
    counting ``exclude_payment`` (the payment being submitted, whose status is already
    SUBMITTED when its allocations are checked)."""
    refs = PaymentEntryReference.objects.filter(
        payment_entry__docstatus=DocStatus.SUBMITTED,
        reference_doctype=OpeningBill.voucher_label(),
        reference_name=bill.name,
    )
    if exclude_payment is not None:
        refs = refs.exclude(payment_entry_id=exclude_payment.pk)
    return refs.aggregate(t=Sum("allocated_amount"))["t"] or ZERO


def derive(bill):
    """The outstanding amount a bill must carry, from its docstatus and allocations.

    A draft or cancelled bill owes nothing. A submitted bill owes its amount less what
    submitted payments have allocated to it.
    """
    if bill.docstatus != DocStatus.SUBMITTED:
        return ZERO
    return bill.amount - allocated(bill)


def current_outstanding(bill, exclude_payment=None):
    """Outstanding of a bill before ``exclude_payment`` is counted, i.e. what a payment
    being submitted may still allocate (the caller holds the row lock)."""
    if bill.docstatus != DocStatus.SUBMITTED:
        return ZERO
    return bill.amount - allocated(bill, exclude_payment)


def refresh(bill):
    """Recompute and store the outstanding amount of one opening bill.

    Locks the row first so concurrent payments serialise. Updates the in-memory
    ``bill`` too, so callers see the new value.
    """
    with transaction.atomic():
        locked = OpeningBill.objects.select_for_update().get(pk=bill.pk)
        outstanding = derive(locked)
        if locked.outstanding_amount != outstanding:
            OpeningBill.objects.filter(pk=locked.pk).update(
                outstanding_amount=outstanding
            )
    bill.outstanding_amount = outstanding
    return outstanding


def repost(company=None):
    """Rebuild the stored outstanding of every opening bill (optionally of one
    company). Returns the number of bills whose stored value changed."""
    changed = 0
    qs = OpeningBill.objects.all()
    if company is not None:
        qs = qs.filter(company=company)
    for pk in list(qs.order_by("pk").values_list("pk", flat=True)):
        with transaction.atomic():
            locked = OpeningBill.objects.select_for_update().get(pk=pk)
            before = locked.outstanding_amount
            if before != refresh(locked):
                changed += 1
    return changed


# --------------------------------------------------------------- build (G-2)

PARTY_TYPES = ("Customer", "Supplier")
KIND_BILL = "bill"
KIND_ADVANCE = "advance"
KIND_REVIEW = "review"


def _party_model(party_type):
    if party_type == "Customer":
        return apps.get_model("selling.Customer")
    return apps.get_model("buying.Supplier")


def _ledger_by_party(company, as_on):
    """Live GL rows on the parties' control accounts up to ``as_on``, grouped by
    ``(party_type, party)``, newest first (the order the walk needs)."""
    rows = (
        GLEntry.objects.filter(
            company=company,
            is_cancelled=False,
            posting_date__lte=as_on,
            party_type__in=PARTY_TYPES,
            account__account_type__in=(AccountType.RECEIVABLE, AccountType.PAYABLE),
        )
        .select_related("account")
        .order_by("-posting_date", "-id")
    )
    grouped = {}
    for row in rows:
        if row.party:
            grouped.setdefault((row.party_type, row.party), []).append(row)
    return grouped


def _walk_bills(party_type, lines, balance):
    """Newest-first walk: the bills are the newest debit (customer) or credit
    (supplier) entries that add up to the balance, the last one part of its amount.
    Anything the entries do not cover is one bill at the oldest date.

    Returns ``[(bill_date, amount, reference)]``.
    """
    side = "debit" if party_type == "Customer" else "credit"
    entries = [ln for ln in lines if getattr(ln, side) > 0]
    remaining = balance
    picked = []
    for ln in entries:
        if remaining <= 0:
            break
        take = min(getattr(ln, side), remaining)
        picked.append((ln.posting_date, take, ln.voucher_no))
        remaining -= take
    if remaining > 0:
        oldest = entries[-1].posting_date if entries else lines[-1].posting_date
        picked.append((oldest, remaining, _("Balance carried forward")))
    return picked


def plan_opening_bills(company, as_on):
    """The opening bills that would carry each party's ledger balance up to ``as_on``.

    Read-only. Returns ``{"rows", "totals", "problems"}``. ``rows`` are dicts with a
    ``kind`` (bill, advance or review), the party, dates and amount. ``problems`` are
    messages that stop a commit: a party to review, or a total that does not tie.
    """
    from accounts.services import outstanding as outstanding_service
    from accounts.services.terms import calculate_due_date

    rows, problems = [], []
    bill_total = advance_total = expected_total = Decimal("0.00")
    counts = {KIND_BILL: 0, KIND_ADVANCE: 0, KIND_REVIEW: 0}

    for (party_type, party), lines in sorted(_ledger_by_party(company, as_on).items()):
        balance = outstanding_service.party_balance(
            party_type, party, company.pk, as_on=as_on
        )
        if balance == 0:
            continue
        accounts = {ln.account_id for ln in lines}
        master = (
            _party_model(party_type).objects.filter(company=company, name=party).first()
        )

        if len(accounts) > 1 or master is None:
            reason = (
                _("balances on more than one control account")
                if len(accounts) > 1
                else _("not a %(party_type)s of this company")
                % {"party_type": party_type}
            )
            problems.append(
                _("%(party)s: %(reason)s. Review by hand.")
                % {"party": party, "reason": reason}
            )
            rows.append(
                {
                    "kind": KIND_REVIEW,
                    "party_type": party_type,
                    "party": party,
                    "amount": abs(balance),
                    "method": reason,
                }
            )
            counts[KIND_REVIEW] += 1
            continue

        if balance < 0:
            rows.append(
                {
                    "kind": KIND_ADVANCE,
                    "party_type": party_type,
                    "party": party,
                    "amount": -balance,
                    "method": _(
                        "Credit balance: an advance, not a bill. Nothing is created."
                    ),
                }
            )
            counts[KIND_ADVANCE] += 1
            advance_total += -balance
            continue

        account_id = accounts.pop()
        expected_total += balance
        covered = Decimal("0.00")
        for bill_date, amount, reference in _walk_bills(party_type, lines, balance):
            rows.append(
                {
                    "kind": KIND_BILL,
                    "party_type": party_type,
                    "party": party,
                    "account_id": account_id,
                    "bill_date": bill_date,
                    "due_date": calculate_due_date(party_type, master, bill_date),
                    "amount": amount,
                    "reference": reference,
                    "method": _("Estimate: newest entries that add up to the balance"),
                }
            )
            counts[KIND_BILL] += 1
            bill_total += amount
            covered += amount
        if covered != balance:
            problems.append(
                _(
                    "%(party)s: bills of %(bills)s do not add up to the balance of %(balance)s."
                )
                % {"party": party, "bills": covered, "balance": balance}
            )

    if bill_total != expected_total:
        problems.append(
            _(
                "Total of the bills (%(bills)s) does not equal the ledger balances (%(balance)s)."
            )
            % {"bills": bill_total, "balance": expected_total}
        )
    totals = {
        "bill_count": counts[KIND_BILL],
        "bills_total": bill_total,
        "advance_count": counts[KIND_ADVANCE],
        "advances_total": advance_total,
        "review_count": counts[KIND_REVIEW],
        "balance_total": expected_total,
    }
    return {"rows": rows, "totals": totals, "problems": problems}


def commit_opening_bills(company, as_on):
    """Re-plan and create the submitted opening bills in one transaction. Refused,
    creating nothing, when the plan has problems or bills already exist for ``as_on``.
    """
    with transaction.atomic():
        plan = plan_opening_bills(company, as_on)
        if plan["problems"]:
            raise ValidationError(plan["problems"])
        if (
            OpeningBill.objects.filter(company=company, as_on=as_on)
            .exclude(docstatus=DocStatus.CANCELLED)
            .exists()
        ):
            raise ValidationError(
                _("Opening bills as on %(as_on)s already exist for this company.")
                % {"as_on": as_on}
            )
        created = []
        for row in plan["rows"]:
            if row["kind"] != KIND_BILL:
                continue
            bill = OpeningBill(
                name=NamingSeries.next(f"OB-{as_on.year}-"),
                company=company,
                party_type=row["party_type"],
                party=row["party"],
                account_id=row["account_id"],
                bill_date=row["bill_date"],
                due_date=row["due_date"],
                as_on=as_on,
                amount=row["amount"],
                reference=str(row["reference"])[:140],
            )
            bill.save()
            bill.submit()
            created.append(bill)
    return created


# --------------------------------------------------------------- integrity (G-4)


def integrity_errors():
    """Each opening bill's stored outstanding must equal its amount less the live
    allocations, and a submitted bill's party must still be a master of its company.
    Returns the drift messages (empty = consistent)."""
    errors = []
    for bill in OpeningBill.objects.order_by("pk"):
        expected = derive(bill)
        if bill.outstanding_amount != expected:
            errors.append(
                _(
                    "Opening Bill Outstanding Drift for %(name)s: stored %(stored)s "
                    "!= amount %(amount)s less allocations = %(expected)s."
                )
                % {
                    "name": bill.name,
                    "stored": bill.outstanding_amount,
                    "amount": bill.amount,
                    "expected": expected,
                }
            )
        if bill.docstatus == DocStatus.SUBMITTED and not (
            _party_model(bill.party_type)
            .objects.filter(company_id=bill.company_id, name=bill.party)
            .exists()
        ):
            errors.append(
                _(
                    "Opening Bill %(name)s: '%(party)s' is not a %(party_type)s of its company."
                )
                % {
                    "name": bill.name,
                    "party": bill.party,
                    "party_type": bill.party_type,
                }
            )
    return errors


def integrity_warnings():
    """A party's submitted opening bills should add up to its ledger balance on the
    as-on date they were built for. A difference is reported, not an error: the
    cutover balances may still be retaken from Tally before go-live (planning 09)."""
    from django.db.models import Sum

    from accounts.services import outstanding as outstanding_service

    warnings = []
    groups = (
        OpeningBill.objects.filter(docstatus=DocStatus.SUBMITTED)
        .values("company_id", "party_type", "party", "as_on")
        .annotate(total=Sum("amount"))
        .order_by("company_id", "party_type", "party", "as_on")
    )
    for group in groups:
        balance = outstanding_service.party_balance(
            group["party_type"],
            group["party"],
            group["company_id"],
            as_on=group["as_on"],
        )
        if group["total"] != balance:
            warnings.append(
                _(
                    "Opening bills for %(party)s total %(bills)s, but the ledger "
                    "balance on %(as_on)s is %(balance)s."
                )
                % {
                    "party": group["party"],
                    "bills": group["total"],
                    "as_on": group["as_on"],
                    "balance": balance,
                }
            )
    return warnings
