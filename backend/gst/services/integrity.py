"""GST invariants for ``manage.py verify_integrity`` (docs/gst_design.md section 4.4).

``check()`` returns ``(errors, warnings)``. Errors are hard invariants of the
immutable facts (document totals, tax rows, lines and the GL voucher agreeing with
each other); warnings are data-quality findings (bad GSTINs from ORM / import
writers, missing HSN, legacy documents) that never fail the command.

Everything here is **read only**: derived values are never "fixed" silently.
"""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from accounts.models import Account, AccountType, GLEntry
from buying.models import PurchaseInvoice, PurchaseOrder, Supplier
from core.models import DocStatus
from gst.constants import AddDeduct, GSTDirection, GSTTreatment
from gst.validators import REGISTERED_COMPOSITION, SEZ, OVERSEAS, check_gstin
from selling.models import Customer, SalesInvoice, SalesOrder

ZERO = Decimal("0.00")
#: Half a paisa: amounts are 2-dp, so any real drift is at least one paisa.
EPS = Decimal("0.005")
GST_HEADS = ("CGST", "SGST", "IGST")
#: Findings listed per check before "... and N more".
SAMPLE = 10

DOCUMENT_MODELS = (SalesOrder, SalesInvoice, PurchaseOrder, PurchaseInvoice)
INVOICE_MODELS = (SalesInvoice, PurchaseInvoice)


def _label(doc) -> str:
    return f"{doc._meta.object_name} {doc.name}"


def _voucher_type(doc) -> str:
    return f"{doc._meta.app_label}.{doc._meta.object_name}"


def _is_memo(doc, row) -> bool:
    return bool(doc.is_reverse_charge and row.account.gst_direction)


def _signed(row) -> Decimal:
    return -row.tax_amount if row.add_deduct_tax == AddDeduct.DEDUCT else row.tax_amount


def _documents(model):
    party = model.party_field
    return (
        model.objects.exclude(docstatus=DocStatus.DRAFT)
        .select_related("company", party)
        .prefetch_related("items", "taxes__account")
        .order_by("pk")
    )


# ------------------------------------------------------------ 1. totals identity


def _check_totals(doc, items, rows):
    errors = []
    label = _label(doc)
    amounts = sum((line.amount for line in items), ZERO)
    if abs(amounts - doc.net_total) > EPS:
        errors.append(
            f"{label}: sum of line amounts ({amounts}) != net_total ({doc.net_total})"
        )
    if abs(doc.grand_total - (doc.net_total + doc.total_taxes_and_charges)) > EPS:
        errors.append(
            f"{label}: grand_total ({doc.grand_total}) != net_total ({doc.net_total}) "
            f"+ total_taxes_and_charges ({doc.total_taxes_and_charges})"
        )
    if abs(doc.rounded_total - (doc.grand_total + doc.rounding_adjustment)) > EPS:
        errors.append(
            f"{label}: rounded_total ({doc.rounded_total}) != grand_total "
            f"({doc.grand_total}) + rounding_adjustment ({doc.rounding_adjustment})"
        )
    counted = sum((_signed(r) for r in rows if not _is_memo(doc, r)), ZERO)
    if abs(counted - doc.total_taxes_and_charges) > EPS:
        errors.append(
            f"{label}: tax rows add up to {counted} but total_taxes_and_charges "
            f"is {doc.total_taxes_and_charges}"
        )
    running = doc.net_total
    for row in rows:
        if not _is_memo(doc, row):
            running += _signed(row)
        if abs(row.total - running) > EPS:
            errors.append(
                f"{label}: tax row #{row.idx} cumulative total is {row.total}, "
                f"expected {running}"
            )
            running = row.total  # report each break once
    return errors


# ------------------------------------------------------------ 2. rows tie to lines


def _check_rows_tie_to_lines(doc, items, rows):
    errors = []
    tolerance = EPS + Decimal("0.01") * len(items) * (
        1 if doc.company.round_off_gst_values else 0
    )
    for row in rows:
        component = row.account.gst_component
        if not row.account.gst_direction or component not in GST_HEADS:
            continue
        field = f"{component.lower()}_amount"
        on_lines = sum((getattr(line, field) for line in items), ZERO)
        if abs(on_lines - row.tax_amount) > tolerance:
            errors.append(
                f"{_label(doc)}: {component} row #{row.idx} is {row.tax_amount} but "
                f"line {component} amounts add up to {on_lines}"
            )
    return errors


# ------------------------------------------------------------------ 3. GL ties


def _gl_by_voucher(models):
    """``{(voucher_type, voucher_no): {account_id: (debit, credit)}}`` plus the party
    lines ``{(vt, vn): (debit, credit)}`` of the live (non-cancelled) rows."""
    types = [_voucher_type(m) for m in models]
    by_account = defaultdict(dict)
    by_party = defaultdict(lambda: [ZERO, ZERO])
    rows = (
        GLEntry.objects.filter(is_cancelled=False, voucher_type__in=types)
        .values("voucher_type", "voucher_no", "account_id", "party_type")
        .annotate(dr=Sum("debit"), cr=Sum("credit"))
    )
    for r in rows:
        key = (r["voucher_type"], r["voucher_no"])
        cur = by_account[key].get(r["account_id"], (ZERO, ZERO))
        by_account[key][r["account_id"]] = (cur[0] + r["dr"], cur[1] + r["cr"])
        if r["party_type"]:
            by_party[key][0] += r["dr"]
            by_party[key][1] += r["cr"]
    return by_account, by_party


def _net(accounts, account_id, *, credit_positive):
    dr, cr = accounts.get(account_id, (ZERO, ZERO))
    return (cr - dr) if credit_positive else (dr - cr)


def _check_gl(doc, rows, accounts, party):
    """The invoice's live GL voucher agrees with its totals, tax rows and round-off."""
    label = _label(doc)
    if not accounts:
        return [f"{label}: submitted but has no GL entries"]
    errors = []
    is_sale = doc.GST_DIRECTION == "Sales"
    # Credit-positive lines of a sale; debit-positive lines of a purchase.
    party_dr, party_cr = party
    party_amount = (party_dr - party_cr) if is_sale else (party_cr - party_dr)
    if abs(party_amount - doc.rounded_total) > EPS:
        errors.append(
            f"{label}: party GL line is {party_amount} but rounded_total is "
            f"{doc.rounded_total}"
        )

    handled = set()
    for row in rows:
        account_id = row.account_id
        handled.add(account_id)
        if _is_memo(doc, row):
            got = _net(accounts, account_id, credit_positive=False)  # Dr Input
            expected = row.tax_amount
        else:
            deduct = row.add_deduct_tax == AddDeduct.DEDUCT
            # Sales: Add -> Cr, Deduct -> Dr. Purchase: Add -> Dr, Deduct -> Cr.
            got = _net(accounts, account_id, credit_positive=is_sale != deduct)
            expected = row.tax_amount
        if abs(got - expected) > EPS:
            errors.append(
                f"{label}: tax row #{row.idx} ({row.account.name}) is {expected} but "
                f"the GL voucher carries {got}"
            )

    if doc.is_reverse_charge:
        memo_by_component = defaultdict(lambda: ZERO)
        for row in rows:
            if _is_memo(doc, row):
                memo_by_component[row.account.gst_component] += row.tax_amount
        rcm = {
            a.gst_component: a.pk
            for a in Account.objects.filter(
                company_id=doc.company_id,
                gst_direction=GSTDirection.REVERSE_CHARGE,
                gst_component__in=list(memo_by_component),
            )
        }
        for component, expected in memo_by_component.items():
            account_id = rcm.get(component)
            handled.add(account_id)
            got = (
                _net(accounts, account_id, credit_positive=True) if account_id else ZERO
            )
            if abs(got - expected) > EPS:
                errors.append(
                    f"{label}: RCM Payable {component} in the GL is {got}, the "
                    f"reverse-charge rows add up to {expected}"
                )

    round_off = doc.company.round_off_account_id
    if round_off:
        handled.add(round_off)
        # Sale: A > 0 is a credit; purchase: A > 0 is a debit.
        got = _net(accounts, round_off, credit_positive=is_sale)
        if abs(got - doc.rounding_adjustment) > EPS:
            errors.append(
                f"{label}: round-off line in the GL is {got}, rounding_adjustment is "
                f"{doc.rounding_adjustment}"
            )
    elif doc.rounding_adjustment:
        errors.append(
            f"{label}: has a rounding adjustment but the company has no round-off account"
        )

    # Everything else (income / expense / inventory / COGS) must net to net_total.
    party_accounts = {
        doc.debit_to_id if is_sale else doc.credit_to_id,
    }
    rest = ZERO
    for account_id, (dr, cr) in accounts.items():
        if account_id in handled or account_id in party_accounts:
            continue
        rest += (cr - dr) if is_sale else (dr - cr)
    if abs(rest - doc.net_total) > EPS:
        errors.append(
            f"{label}: income / expense lines in the GL add up to {rest}, "
            f"net_total is {doc.net_total}"
        )
    return errors


# ------------------------------------------------------- 4. kind consistency


def _check_kind(doc, items, rows):
    if doc.docstatus != DocStatus.SUBMITTED:
        return []
    gst_rows = [r for r in rows if r.account.gst_direction and r.tax_amount]
    if not gst_rows:
        return []
    label = _label(doc)
    errors = []
    totals = defaultdict(lambda: ZERO)
    for row in gst_rows:
        totals[row.account.gst_component] += row.tax_amount
    if totals.get("IGST") and (totals.get("CGST") or totals.get("SGST")):
        errors.append(f"{label}: IGST is combined with CGST / SGST")
    if abs(totals.get("CGST", ZERO) - totals.get("SGST", ZERO)) > Decimal("0.015"):
        errors.append(
            f"{label}: CGST ({totals.get('CGST', ZERO)}) != SGST ({totals.get('SGST', ZERO)})"
        )
    if not any(line.gst_treatment == GSTTreatment.TAXABLE for line in items):
        errors.append(f"{label}: GST charged although no line is taxable")
    category = doc.gst_category
    is_sale = doc.GST_DIRECTION == "Sales"
    if is_sale:
        if category in (SEZ, OVERSEAS):
            if not doc.is_export_with_gst:
                errors.append(
                    f"{label}: GST charged on a zero-rated (LUT) export / SEZ supply"
                )
            elif totals.get("CGST") or totals.get("SGST"):
                errors.append(f"{label}: CGST / SGST charged on an SEZ / export supply")
        elif doc.place_of_supply_id and doc.company.state_id:
            intra = doc.place_of_supply_id == doc.company.state_id
            if intra and totals.get("IGST"):
                errors.append(f"{label}: IGST charged on an intra-state supply")
            if not intra and (totals.get("CGST") or totals.get("SGST")):
                errors.append(f"{label}: CGST / SGST charged on an inter-state supply")
    else:
        if category == REGISTERED_COMPOSITION:
            errors.append(f"{label}: GST claimed on a composition supplier's invoice")
        elif category in (SEZ, OVERSEAS):
            if totals.get("CGST") or totals.get("SGST"):
                errors.append(f"{label}: CGST / SGST on an SEZ / overseas supply")
        elif doc.place_of_supply_id:
            gstin = doc.party_gstin
            location = (gstin[:2] if gstin else None) or doc.supplier.state_id
            if location:
                intra = location == doc.place_of_supply_id
                if intra and totals.get("IGST"):
                    errors.append(f"{label}: IGST on an intra-state purchase")
                if not intra and (totals.get("CGST") or totals.get("SGST")):
                    errors.append(f"{label}: CGST / SGST on an inter-state purchase")
    return errors


# --------------------------------------------------------- 5. account tagging


def _check_accounts(doc, rows):
    errors = []
    want = GSTDirection.OUTPUT if doc.GST_DIRECTION == "Sales" else GSTDirection.INPUT
    for row in rows:
        account = row.account
        if account.company_id != doc.company_id:
            errors.append(
                f"{_label(doc)}: tax row #{row.idx} uses account '{account.name}' of "
                "another company"
            )
        if account.gst_direction and account.account_type != AccountType.TAX:
            errors.append(
                f"{_label(doc)}: GST account '{account.name}' is not of type Tax"
            )
        if account.gst_direction and account.gst_direction != want:
            errors.append(
                f"{_label(doc)}: tax row #{row.idx} uses {account.gst_direction} "
                f"account '{account.name}' on a {doc.GST_DIRECTION.lower()} document"
            )
    return errors


# ----------------------------------------------------------------- 6. line spot


def _check_lines(doc, items):
    return [
        f"{_label(doc)}: non-taxable line (item {line.item.code}) carries GST amounts"
        for line in items
        if line.gst_treatment != GSTTreatment.TAXABLE
        and (line.cgst_amount or line.sgst_amount or line.igst_amount)
    ]


# ----------------------------------------------------------------- warnings


def _gstin_warnings():
    warnings = []
    for model in (Customer, Supplier):
        for party in model.objects.exclude(gstin__isnull=True).exclude(gstin=""):
            result = check_gstin(party.gstin)
            if not result.valid:
                warnings.append(
                    f"{model._meta.object_name} '{party.name}': GSTIN {party.gstin} "
                    f"is invalid ({result.reason})"
                )
            elif party.state_id and result.state_code != party.state_id:
                warnings.append(
                    f"{model._meta.object_name} '{party.name}': GSTIN prefix "
                    f"{result.state_code} does not match state {party.state_id}"
                )
    return warnings


def _capped(title, findings):
    if not findings:
        return []
    shown = findings[:SAMPLE]
    more = len(findings) - len(shown)
    out = [f"{title}: {len(findings)} found"]
    out.extend(f"  {f}" for f in shown)
    if more:
        out.append(f"  ... and {more} more")
    return out


# ---------------------------------------------------------------------- entry


def check():
    """Run every GST check. Returns ``(errors, warnings)`` (lists of strings)."""
    errors = []
    gl_by_account, gl_by_party = _gl_by_voucher(INVOICE_MODELS)
    snapshot_gstin, missing_data = [], []
    legacy = 0

    for model in DOCUMENT_MODELS:
        for doc in _documents(model):
            items = list(doc.items.all())
            rows = sorted(doc.taxes.all(), key=lambda r: (r.idx, r.pk))
            errors += _check_totals(doc, items, rows)
            errors += _check_rows_tie_to_lines(doc, items, rows)
            errors += _check_kind(doc, items, rows)
            errors += _check_accounts(doc, rows)
            errors += _check_lines(doc, items)

            if doc.docstatus == DocStatus.SUBMITTED and model in INVOICE_MODELS:
                key = (_voucher_type(doc), doc.name)
                errors += _check_gl(
                    doc,
                    rows,
                    gl_by_account.get(key, {}),
                    gl_by_party.get(key, (ZERO, ZERO)),
                )

            if not doc.gst_category:
                legacy += 1
            elif doc.docstatus == DocStatus.SUBMITTED and doc.company.gstin:
                if doc.party_gstin:
                    result = check_gstin(doc.party_gstin)
                    if not result.valid:
                        snapshot_gstin.append(
                            f"{_label(doc)}: party GSTIN {doc.party_gstin} is invalid "
                            f"({result.reason})"
                        )
                if not doc.place_of_supply_id:
                    missing_data.append(f"{_label(doc)}: no place of supply")
                for line in items:
                    if (
                        line.gst_treatment != GSTTreatment.NON_GST
                        and not line.gst_hsn_code
                    ):
                        missing_data.append(
                            f"{_label(doc)}: item {line.item.code} has no HSN/SAC code"
                        )

    warnings = []
    warnings += _capped("Invalid party GSTINs on masters", _gstin_warnings())
    warnings += _capped("Invalid party GSTINs on submitted documents", snapshot_gstin)
    warnings += _capped(
        "GST-registered company documents missing place of supply / HSN", missing_data
    )
    if legacy:
        warnings.append(
            f"{legacy} legacy (pre-GST) document(s) are excluded from GST reports"
        )
    return errors, warnings
