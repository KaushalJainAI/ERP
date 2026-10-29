"""Payment Reconciliation: list what can be matched, pair it, post the net-zero entry.

Facts and projections
---------------------
* A **payment source** is a Payment Entry or Journal Entry whose GL rows on the
  party's control account carry no ``against_voucher`` tag (an advance, a lump sum, or
  the remainder of a receipt after its allocated invoices). Its *unallocated* amount
  is the untagged payment-side net of the voucher plus the (negative) payment-side
  net of every live row tagged ``against_voucher`` = the voucher (our reconciliation
  rows). Payment side = credit for a customer, debit for a supplier.
* A **note** is a credit / debit note that keeps its own outstanding
  (``update_outstanding_for_self``): its stored ``outstanding_amount`` is negative and
  the amount available is ``-outstanding_amount``.
* An **invoice** is a submitted, non-return Sales / Purchase Invoice with a positive
  ``outstanding_amount``.

Nothing here writes a ledger row except through ``accounts.services.gl`` (via the
reconciliation document's submit / cancel), and invoice outstanding is only ever
rebuilt through ``accounts.services.outstanding.refresh``.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Min, Q, Sum
from django.utils.translation import gettext as _

from accounts.models import AccountType, GLEntry
from accounts.services import gl as gl_service
from accounts.services import outstanding as outstanding_service
from core.models import DocStatus, NamingSeries

ZERO = Decimal("0.00")

PE = "accounts.PaymentEntry"
JE = "accounts.JournalEntry"
PAYMENT_SOURCES = (PE, JE)
INVOICE_LABEL = {
    "Customer": "selling.SalesInvoice",
    "Supplier": "buying.PurchaseInvoice",
}
CONTROL_TYPE = {"Customer": AccountType.RECEIVABLE, "Supplier": AccountType.PAYABLE}
#: Vouchers imported from Tally are history (the ten years of past bills), not
#: receipts to match against new invoices.
IMPORTED_PREFIXES = ("TLY-",)


# ------------------------------------------------------------------ helpers


def invoice_model(party_type):
    if party_type not in INVOICE_LABEL:
        raise ValidationError(_("Party type must be Customer or Supplier."))
    return apps.get_model(INVOICE_LABEL[party_type])


def _pay_net(party_type, debit, credit):
    """Payment-side net of GL sums: credit - debit for a customer, debit - credit for
    a supplier."""
    debit, credit = debit or ZERO, credit or ZERO
    return credit - debit if party_type == "Customer" else debit - credit


def _party_rows(company, party_type, party, party_account=None):
    qs = GLEntry.objects.filter(
        company=company,
        party_type=party_type,
        party=party,
        is_cancelled=False,
        account__account_type=CONTROL_TYPE[party_type],
    )
    if party_account is not None:
        qs = qs.filter(account=party_account)
    return qs


def _untagged(qs):
    return qs.filter(Q(against_voucher_no__isnull=True) | Q(against_voucher_no=""))


def _money(value):
    return str(Decimal(value).quantize(Decimal("0.01")))


def require_party(company, party_type, party):
    """The party master of this company (raises if unknown)."""
    from buying.models import Supplier
    from selling.models import Customer

    model = {"Customer": Customer, "Supplier": Supplier}.get(party_type)
    if model is None:
        raise ValidationError(_("Party type must be Customer or Supplier."))
    obj = model.objects.filter(company=company, name=party).first()
    if obj is None:
        raise ValidationError(
            _("%(type)s '%(party)s' does not exist in this company.")
            % {"type": party_type, "party": party}
        )
    return obj


# ------------------------------------------------------------------ listings


def source_available(company, party_type, party, account, doctype, name):
    """Unallocated amount of one payment source, or ``None`` when the voucher has no
    live row for this party on this account."""
    base = _party_rows(company, party_type, party, account)
    own = base.filter(voucher_type=doctype, voucher_no=name)
    if not own.exists():
        return None
    u = _untagged(own).aggregate(dr=Sum("debit"), cr=Sum("credit"))
    c = base.filter(against_voucher_type=doctype, against_voucher_no=name).aggregate(
        dr=Sum("debit"), cr=Sum("credit")
    )
    return _pay_net(party_type, u["dr"], u["cr"]) + _pay_net(
        party_type, c["dr"], c["cr"]
    )


def list_payments(
    company,
    party_type,
    party,
    from_date=None,
    to_date=None,
    party_account=None,
    include_imported=False,
):
    """Payment Entries / Journal Entries with an unallocated amount, oldest first."""
    base = _party_rows(company, party_type, party, party_account)
    untagged = (
        _untagged(base)
        .filter(voucher_type__in=PAYMENT_SOURCES)
        .values("voucher_type", "voucher_no", "account_id", "account__name")
        .annotate(dr=Sum("debit"), cr=Sum("credit"), first=Min("posting_date"))
    )
    consumed = defaultdict(lambda: ZERO)
    for r in (
        base.filter(against_voucher_type__in=PAYMENT_SOURCES)
        .values("against_voucher_type", "against_voucher_no", "account_id")
        .annotate(dr=Sum("debit"), cr=Sum("credit"))
    ):
        key = (r["against_voucher_type"], r["against_voucher_no"], r["account_id"])
        consumed[key] += _pay_net(party_type, r["dr"], r["cr"])

    found = []
    for r in untagged:
        if not include_imported and r["voucher_no"].startswith(IMPORTED_PREFIXES):
            continue
        original = _pay_net(party_type, r["dr"], r["cr"])
        available = (
            original + consumed[(r["voucher_type"], r["voucher_no"], r["account_id"])]
        )
        if available <= 0:
            continue
        if from_date and r["first"] < from_date:
            continue
        if to_date and r["first"] > to_date:
            continue
        found.append((r, original, available))

    pe_names = [r["voucher_no"] for r, _o, _a in found if r["voucher_type"] == PE]
    je_names = [r["voucher_no"] for r, _o, _a in found if r["voucher_type"] == JE]
    pe_docs = {
        d.name: d
        for d in apps.get_model(PE).objects.filter(name__in=pe_names, docstatus=1)
    }
    je_docs = {
        d.name: d
        for d in apps.get_model(JE).objects.filter(name__in=je_names, docstatus=1)
    }
    out = []
    for r, original, available in found:
        is_pe = r["voucher_type"] == PE
        doc = (pe_docs if is_pe else je_docs).get(r["voucher_no"])
        if doc is None:
            continue
        if not is_pe and doc.voucher_type == "Opening Entry":
            continue
        out.append(
            {
                "doctype": r["voucher_type"],
                "name": r["voucher_no"],
                "kind": "Payment Entry" if is_pe else "Journal Entry",
                "posting_date": str(r["first"]),
                "party_account": r["account_id"],
                "party_account_name": r["account__name"],
                "amount": _money(original),
                "unallocated": _money(available),
                "reference": (doc.reference_no if is_pe else doc.cheque_no) or "",
                "remarks": "" if is_pe else (doc.user_remark or ""),
            }
        )
    out.sort(key=lambda x: (x["posting_date"], x["name"]))
    return out


def _invoice_rows(
    company,
    party_type,
    party,
    *,
    returns,
    from_date,
    to_date,
    party_account,
):
    model = invoice_model(party_type)
    qs = model.objects.filter(
        company=company,
        docstatus=DocStatus.SUBMITTED,
        is_return=returns,
        **{f"{model.party_field}__name": party},
    )
    if returns:
        qs = qs.filter(update_outstanding_for_self=True, outstanding_amount__lt=0)
    else:
        qs = qs.filter(outstanding_amount__gt=0)
    if party_account is not None:
        qs = qs.filter(**{model.PARTY_ACCOUNT_FIELD: party_account})
    if from_date:
        qs = qs.filter(posting_date__gte=from_date)
    if to_date:
        qs = qs.filter(posting_date__lte=to_date)
    out = []
    for inv in qs.select_related(model.PARTY_ACCOUNT_FIELD, "return_against").order_by(
        "posting_date", "name"
    ):
        account = inv.party_account
        out.append(
            {
                "doctype": model.voucher_label(),
                "name": inv.name,
                "kind": (
                    "Credit Note"
                    if returns and party_type == "Customer"
                    else ("Debit Note" if returns else "Invoice")
                ),
                "posting_date": str(inv.posting_date),
                "due_date": str(inv.due_date) if inv.due_date else None,
                "party_account": account.pk,
                "party_account_name": account.name,
                "amount": _money(abs(inv.rounded_total)),
                # Positive for both: what can still be applied / settled.
                "unallocated" if returns else "outstanding": _money(
                    abs(inv.outstanding_amount)
                ),
                "return_against": (
                    inv.return_against.name if inv.return_against_id else None
                ),
            }
        )
    return out


def list_notes(
    company, party_type, party, from_date=None, to_date=None, party_account=None
):
    """Credit / debit notes that keep their own outstanding and still have an
    unused balance."""
    return _invoice_rows(
        company,
        party_type,
        party,
        returns=True,
        from_date=from_date,
        to_date=to_date,
        party_account=party_account,
    )


def list_invoices(
    company, party_type, party, from_date=None, to_date=None, party_account=None
):
    """Open invoices (outstanding > 0), oldest first."""
    return _invoice_rows(
        company,
        party_type,
        party,
        returns=False,
        from_date=from_date,
        to_date=to_date,
        party_account=party_account,
    )


def unreconciled(
    company, party_type, party, from_date=None, to_date=None, include_imported=False
):
    """Everything the tool shows for a party."""
    require_party(company, party_type, party)
    payments = list_payments(
        company,
        party_type,
        party,
        from_date,
        to_date,
        include_imported=include_imported,
    )
    notes = list_notes(company, party_type, party, from_date, to_date)
    invoices = list_invoices(company, party_type, party, from_date, to_date)
    return {
        "party_type": party_type,
        "party": party,
        "payments": payments,
        "notes": notes,
        "invoices": invoices,
        "totals": {
            "payments": _money(
                sum((Decimal(p["unallocated"]) for p in payments), ZERO)
            ),
            "notes": _money(sum((Decimal(n["unallocated"]) for n in notes), ZERO)),
            "invoices": _money(
                sum((Decimal(i["outstanding"]) for i in invoices), ZERO)
            ),
        },
    }


# ---------------------------------------------------------------- allocation


def auto_allocate(sources, invoices):
    """Pair sources with invoices oldest first (ERPNext ``allocate_entries``).

    ``sources`` / ``invoices`` are ``[{doctype, name, posting_date, amount}]`` where
    ``amount`` is the most that may be taken from / applied to it. For each invoice
    (oldest first) the sources are consumed oldest first until the invoice is
    covered. Returns ``[{source_doctype, source_name, invoice_doctype, invoice_name,
    amount}]`` as strings.
    """
    src = sorted(sources, key=lambda s: (str(s.get("posting_date", "")), s["name"]))
    inv = sorted(invoices, key=lambda s: (str(s.get("posting_date", "")), s["name"]))
    left = {(s["doctype"], s["name"]): Decimal(str(s["amount"])) for s in src}
    rows = []
    for i in inv:
        need = Decimal(str(i["amount"]))
        for s in src:
            if need <= 0:
                break
            key = (s["doctype"], s["name"])
            take = min(need, left[key])
            if take <= 0:
                continue
            rows.append(
                {
                    "source_doctype": s["doctype"],
                    "source_name": s["name"],
                    "invoice_doctype": i["doctype"],
                    "invoice_name": i["name"],
                    "amount": _money(take),
                }
            )
            left[key] -= take
            need -= take
    return rows


def preview_allocation(company, party_type, party, payments, invoices):
    """Server-side "Allocate": cap the picked items at what is really available now
    (their optional ``amount`` can only lower it) and pair them oldest first."""
    current = unreconciled(company, party_type, party, include_imported=True)
    avail_src = {
        (r["doctype"], r["name"]): r for r in current["payments"] + current["notes"]
    }
    avail_inv = {(r["doctype"], r["name"]): r for r in current["invoices"]}

    def pick(selection, pool, field, what):
        chosen = []
        for sel in selection:
            key = (sel.get("doctype"), sel.get("name"))
            row = pool.get(key)
            if row is None:
                raise ValidationError(
                    _(
                        "%(what)s '%(name)s' has nothing left to reconcile for this party."
                    )
                    % {"what": what, "name": sel.get("name")}
                )
            cap = Decimal(row[field])
            amount = (
                Decimal(str(sel["amount"]))
                if sel.get("amount") not in (None, "")
                else cap
            )
            if amount <= 0:
                continue
            chosen.append({**row, "amount": min(amount, cap)})
        return chosen

    sources = pick(payments, avail_src, "unallocated", _("Payment / note"))
    targets = pick(invoices, avail_inv, "outstanding", _("Invoice"))
    accounts = {r["party_account"] for r in sources + targets}
    if len(accounts) > 1:
        raise ValidationError(
            _(
                "The selected rows are booked to different party accounts; reconcile "
                "them separately."
            )
        )
    rows = auto_allocate(sources, targets)
    total = sum((Decimal(r["amount"]) for r in rows), ZERO)
    return {
        "allocations": rows,
        "total_allocated": _money(total),
        "unallocated_sources": _money(
            sum((Decimal(str(s["amount"])) for s in sources), ZERO) - total
        ),
        "unpaid_invoices": _money(
            sum((Decimal(str(t["amount"])) for t in targets), ZERO) - total
        ),
    }


# ----------------------------------------------------------- the document


def _lock(label, names):
    """Lock the rows of ``label`` named in ``names`` (serialises concurrent
    reconciliations of the same voucher) and return them keyed by name."""
    if not names:
        return {}
    model = apps.get_model(label)
    return {
        d.name: d
        for d in model.objects.select_for_update().filter(name__in=sorted(names))
    }


def validate_and_lock(doc):
    """Check every allocation row of ``doc`` against the live ledger. Runs inside the
    submit transaction; raises ``ValidationError`` on the first problem."""
    allocations = list(doc.allocations.order_by("pk"))
    if not allocations:
        raise ValidationError(_("Allocate at least one payment against an invoice."))
    party_type = doc.party_type
    label = INVOICE_LABEL[party_type]
    account = doc.party_account
    if (
        account.company_id != doc.company_id
        or account.account_type != CONTROL_TYPE[party_type]
    ):
        raise ValidationError(
            _("The party account must be a %(type)s account of this company.")
            % {"type": CONTROL_TYPE[party_type].value}
        )
    require_party(doc.company, party_type, doc.party_name)

    invoice_names, note_names, pe_names, je_names = set(), set(), set(), set()
    for a in allocations:
        if a.invoice_doctype != label:
            raise ValidationError(
                _("Invoices of a %(type)s are '%(label)s', not '%(got)s'.")
                % {"type": party_type, "label": label, "got": a.invoice_doctype}
            )
        if a.source_doctype not in (PE, JE, label):
            raise ValidationError(
                _("'%(doctype)s' cannot be reconciled against an invoice.")
                % {"doctype": a.source_doctype}
            )
        if a.source_doctype == label and a.source_name == a.invoice_name:
            raise ValidationError(_("A document cannot be reconciled against itself."))
        if a.amount is None or a.amount <= 0:
            raise ValidationError(
                _("Every allocated amount must be greater than zero.")
            )
        invoice_names.add(a.invoice_name)
        if a.source_doctype == label:
            note_names.add(a.source_name)
        elif a.source_doctype == PE:
            pe_names.add(a.source_name)
        else:
            je_names.add(a.source_name)

    invoices = _lock(label, invoice_names | note_names)
    _lock(PE, pe_names)
    _lock(JE, je_names)

    def check_doc(inv, *, want_return, what):
        if inv is None:
            raise ValidationError(_("%(what)s does not exist.") % {"what": what})
        if inv.docstatus != DocStatus.SUBMITTED:
            raise ValidationError(_("%(what)s is not submitted.") % {"what": what})
        if inv.company_id != doc.company_id:
            raise ValidationError(
                _("%(what)s belongs to a different company.") % {"what": what}
            )
        if inv.party.name != doc.party_name:
            raise ValidationError(
                _("%(what)s belongs to a different party.") % {"what": what}
            )
        if inv.party_account.pk != account.pk:
            raise ValidationError(
                _("%(what)s is booked to a different party account.") % {"what": what}
            )
        if bool(inv.is_return) != want_return:
            raise ValidationError(
                _("%(what)s is %(kind)s.")
                % {
                    "what": what,
                    "kind": (
                        _("a return, not an invoice")
                        if inv.is_return
                        else _("not a credit / debit note")
                    ),
                }
            )
        if not outstanding_service._is_tagged(inv):
            raise ValidationError(
                _(
                    "%(what)s was posted before bill-wise tracking and cannot be "
                    "reconciled; allocate a payment entry against it instead."
                )
                % {"what": what}
            )

    open_invoice = {}
    for name in sorted(invoice_names):
        inv = invoices.get(name)
        what = _("Invoice '%(name)s'") % {"name": name}
        check_doc(inv, want_return=False, what=what)
        open_invoice[name] = outstanding_service.current_outstanding(inv)

    open_source = {}
    for name in sorted(note_names):
        inv = invoices.get(name)
        what = _("Note '%(name)s'") % {"name": name}
        check_doc(inv, want_return=True, what=what)
        if not inv.update_outstanding_for_self:
            raise ValidationError(
                _("Note '%(name)s' reduces its original invoice, not its own balance.")
                % {"name": name}
            )
        open_source[(label, name)] = -outstanding_service.current_outstanding(inv)
    for doctype, names in ((PE, pe_names), (JE, je_names)):
        docs = _lock(doctype, names)
        for name in sorted(names):
            src = docs.get(name)
            if src is None or src.docstatus != DocStatus.SUBMITTED:
                raise ValidationError(
                    _("'%(name)s' does not exist or is not submitted.") % {"name": name}
                )
            avail = source_available(
                doc.company, party_type, doc.party_name, account, doctype, name
            )
            if avail is None:
                raise ValidationError(
                    _(
                        "'%(name)s' has no entry for %(party)s on account "
                        "'%(account)s'."
                    )
                    % {"name": name, "party": doc.party_name, "account": account.name}
                )
            open_source[(doctype, name)] = avail

    used_invoice, used_source = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for a in allocations:
        used_invoice[a.invoice_name] += a.amount
        used_source[(a.source_doctype, a.source_name)] += a.amount
    for name, used in used_invoice.items():
        if used > open_invoice[name]:
            raise ValidationError(
                _(
                    "Allocated %(used)s exceeds the outstanding amount %(open)s of "
                    "invoice '%(name)s'."
                )
                % {"used": used, "open": open_invoice[name], "name": name}
            )
    for (_doctype, name), used in used_source.items():
        have = open_source[(_doctype, name)]
        if used > have:
            raise ValidationError(
                _(
                    "Allocated %(used)s exceeds the unallocated amount %(open)s of "
                    "'%(name)s'."
                )
                % {"used": used, "open": have, "name": name}
            )


def _entries(doc):
    """GL lines of the entry: invoice side vs source side, one line per document."""
    customer = doc.party_type == "Customer"
    by_invoice, by_source = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for a in doc.allocations.order_by("pk"):
        by_invoice[(a.invoice_doctype, a.invoice_name)] += a.amount
        by_source[(a.source_doctype, a.source_name)] += a.amount
    party = {"party_type": doc.party_type, "party": doc.party_name}
    entries = []
    for side, tagged in ((True, by_invoice), (False, by_source)):
        for (doctype, name), amount in sorted(tagged.items()):
            # Customer: invoices are credited (their debit is released), sources
            # debited (their credit is consumed). A supplier is the mirror image.
            credit = side == customer
            entries.append(
                {
                    "account": doc.party_account,
                    "debit": ZERO if credit else amount,
                    "credit": amount if credit else ZERO,
                    "against_voucher_type": doctype,
                    "against_voucher_no": name,
                    **party,
                }
            )
    return entries


def _refresh_projections(doc):
    seen = set()
    for a in doc.allocations.order_by("pk"):
        for doctype, name in (
            (a.invoice_doctype, a.invoice_name),
            (a.source_doctype, a.source_name),
        ):
            if doctype in PAYMENT_SOURCES or (doctype, name) in seen:
                continue
            seen.add((doctype, name))
            inv = apps.get_model(doctype).objects.filter(name=name).first()
            if inv is not None:
                outstanding_service.refresh(inv)


def post(doc):
    gl_service.post(
        doc,
        _entries(doc),
        posting_date=doc.posting_date,
        remarks=doc.remarks or f"Payment reconciliation {doc.party_name}",
    )
    _refresh_projections(doc)


def unpost(doc):
    gl_service.reverse(doc)
    _refresh_projections(doc)


def reconcile(
    company,
    party_type,
    party,
    allocations,
    posting_date=None,
    remarks="",
    user=None,
):
    """Create and submit a reconciliation entry; returns it.

    ``allocations`` is ``[{source_doctype, source_name, invoice_doctype,
    invoice_name, amount}]``. Everything happens in one transaction; a failed check
    leaves nothing behind.
    """
    from payment_reconciliation.models import (
        PaymentReconciliation,
        PaymentReconciliationAllocation,
    )

    require_party(company, party_type, party)
    if not allocations:
        raise ValidationError(_("Allocate at least one payment against an invoice."))
    posting_date = posting_date or datetime.date.today()
    label = INVOICE_LABEL[party_type]
    model = apps.get_model(label)
    first = allocations[0]
    with transaction.atomic():
        invoice = model.objects.filter(
            name=first["invoice_name"], company=company
        ).first()
        if invoice is None:
            raise ValidationError(
                _("Invoice '%(name)s' does not exist.")
                % {"name": first["invoice_name"]}
            )
        doc = PaymentReconciliation(
            company=company,
            name=NamingSeries.next(f"PREC-{posting_date.year}-"),
            posting_date=posting_date,
            party_type=party_type,
            party_name=party,
            party_account=invoice.party_account,
            remarks=remarks or "",
        )
        if user is not None and getattr(user, "is_authenticated", False):
            doc.created_by = user
            doc.modified_by = user
        doc.save()
        for row in allocations:
            try:
                amount = Decimal(str(row["amount"])).quantize(Decimal("0.01"))
            except Exception:
                raise ValidationError(_("Invalid amount."))
            PaymentReconciliationAllocation.objects.create(
                reconciliation=doc,
                source_doctype=row["source_doctype"],
                source_name=row["source_name"],
                invoice_doctype=row["invoice_doctype"],
                invoice_name=row["invoice_name"],
                amount=amount,
            )
        doc.submit(user=user)
    return doc


# ---------------------------------------------------------------- integrity


def integrity_errors():
    """Compare each reconciliation document with the immutable ledger.

    * a submitted entry has live rows that equal its allocations (per tagged document),
      balance, and its ``total_allocated`` equals the rows;
    * a cancelled entry has no live rows;
    * no payment source / note is consumed beyond what it holds, and no live
      reconciliation refers to a document that is no longer submitted.
    """
    from payment_reconciliation.models import PaymentReconciliation

    errors = []
    label = PaymentReconciliation.voucher_label()
    for doc in PaymentReconciliation.objects.exclude(
        docstatus=DocStatus.DRAFT
    ).order_by("pk"):
        live = GLEntry.objects.filter(
            voucher_type=label, voucher_no=doc.name, is_cancelled=False
        )
        if doc.docstatus == DocStatus.CANCELLED:
            if live.exists():
                errors.append(
                    f"Payment Reconciliation {doc.name} is cancelled but has live GL rows"
                )
            continue
        rows = list(doc.allocations.all())
        total = sum((r.amount for r in rows), ZERO)
        if total != doc.total_allocated:
            errors.append(
                f"Payment Reconciliation {doc.name}: total_allocated "
                f"({doc.total_allocated}) != sum of allocations ({total})"
            )
        agg = live.aggregate(dr=Sum("debit"), cr=Sum("credit"))
        if (agg["dr"] or ZERO) != total or (agg["cr"] or ZERO) != total:
            errors.append(
                f"Payment Reconciliation {doc.name}: GL rows (Dr {agg['dr'] or ZERO} / "
                f"Cr {agg['cr'] or ZERO}) do not equal the allocations ({total})"
            )
        tagged = defaultdict(lambda: ZERO)
        for r in live:
            tagged[(r.against_voucher_type, r.against_voucher_no)] += r.debit + r.credit
        expected = defaultdict(lambda: ZERO)
        for r in rows:
            expected[(r.invoice_doctype, r.invoice_name)] += r.amount
            expected[(r.source_doctype, r.source_name)] += r.amount
        if dict(tagged) != dict(expected):
            errors.append(
                f"Payment Reconciliation {doc.name}: GL rows are not tagged against the "
                "allocated documents"
            )
        for r in rows:
            for doctype, name in (
                (r.invoice_doctype, r.invoice_name),
                (r.source_doctype, r.source_name),
            ):
                target = apps.get_model(doctype).objects.filter(name=name).first()
                if target is None or target.docstatus != DocStatus.SUBMITTED:
                    errors.append(
                        f"Payment Reconciliation {doc.name} refers to {doctype} {name} "
                        "which is not submitted"
                    )
        # Sources not over-consumed.
        for (doctype, name), _amt in expected.items():
            if doctype not in PAYMENT_SOURCES:
                continue
            avail = source_available(
                doc.company,
                doc.party_type,
                doc.party_name,
                doc.party_account,
                doctype,
                name,
            )
            if avail is not None and avail < 0:
                errors.append(
                    f"{doctype} {name} is reconciled beyond its unallocated amount "
                    f"({avail})"
                )
    return errors
