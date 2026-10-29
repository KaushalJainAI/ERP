"""Build the print payload of a document (read-only; nothing here writes).

Every figure is read from the stored document (``net_total``, tax rows, line GST
columns, ``rounded_total`` ...), never recalculated: the print shows exactly what the
ledger was posted from. Money is a 2dp string, quantity a 3dp string (``CLAUDE.md``
precisions); a return (credit / debit note) keeps its negative signs and the template
decides how to show them.

Translatable labels are *not* produced here: the payload carries keys
(``title_key``, ``copy.key`` ...) that the frontend maps through react-i18next. The
English ``title`` is only a fallback for non-UI consumers.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum

from accounts.models import AccountType, GLEntry, PaymentType
from accounts.services import outstanding as outstanding_service
from core.models import DocStatus
from printing.words import amount_in_words

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
MILLI = Decimal("0.001")
MICRO = Decimal("0.000001")

COPIES = ("original", "duplicate", "triplicate")
WORD_LANGS = ("en", "hi")


# ----------------------------------------------------------------- formatting


def _q(value, exp=CENT):
    return Decimal(value or 0).quantize(exp, rounding=ROUND_HALF_UP)


def money(value):
    return str(_q(value))


def qty(value):
    return str(_q(value, MILLI))


def rate(value):
    """Show a rate with 2 to 6 decimals (documents store 2 dp, price lists 6 dp)."""
    text = str(_q(value, MICRO)).rstrip("0")
    whole, _sep, frac = text.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def pct(value):
    """A GST rate as a short string: ``18``, ``2.5``."""
    return str(_q(value, MILLI)).rstrip("0").rstrip(".")


def iso(value):
    return value.isoformat() if value else None


def _first(*values):
    for v in values:
        if v:
            return v
    return ""


# ------------------------------------------------------------- shared blocks


def state_block(state):
    return {"code": state.code, "name": state.name} if state else None


def company_block(company):
    """Letterhead data. ``getattr`` keeps optional fields (bank, logo, terms, FSSAI,
    signatory) out of the payload until the Company master has them."""
    block = {
        "id": company.pk,
        "name": company.name,
        "legal_name": company.legal_name or company.name,
        "gstin": company.gstin or "",
        "pan": company.pan or "",
        "cin": company.cin or "",
        "state": state_block(company.state),
        "address_line1": company.address_line1,
        "address_line2": company.address_line2,
        "city": company.city,
        "pincode": company.pincode,
        "country": company.country,
        "phone": company.phone_no,
        "email": company.email,
        "website": company.website,
    }
    optional = {
        "fssai_license_no": getattr(company, "fssai_license_no", ""),
        "terms": getattr(company, "terms_and_conditions", ""),
        "authorised_signatory": getattr(company, "authorised_signatory", ""),
    }
    block.update({k: v for k, v in optional.items() if v})
    logo = getattr(company, "logo", None)
    if logo:
        block["logo_url"] = getattr(logo, "url", str(logo))
    bank = {
        "bank_name": getattr(company, "bank_name", ""),
        "account_no": getattr(company, "bank_account_no", ""),
        "ifsc": getattr(company, "bank_ifsc", ""),
        "branch": getattr(company, "bank_branch", ""),
        "upi_id": getattr(company, "upi_id", ""),
    }
    if any(bank.values()):
        block["bank"] = {k: v for k, v in bank.items() if v}
    return block


def _address_block(address):
    return {
        "title": address.address_title,
        "line1": address.address_line1,
        "line2": address.address_line2,
        "city": address.city,
        "pincode": address.pincode,
        "country": address.country,
        "state": state_block(address.state),
        "gstin": address.gstin or "",
        "phone": address.phone or "",
    }


def _party_addresses(party):
    """``(billing, shipping)`` Address rows of a Customer / Supplier (either may be
    ``None``): billing = the primary address, shipping = the shipping address, each
    falling back to the other."""
    rows = list(party.addresses.filter(is_active=True).select_related("state"))
    billing = next((a for a in rows if a.is_primary_address), None) or next(
        (a for a in rows if not a.is_shipping_address), None
    )
    shipping = next(
        (a for a in rows if a.is_shipping_address and a is not billing), None
    )
    billing = billing or (rows[0] if rows else None)
    return billing, shipping or billing


def _party_core(party, party_type, address):
    return {
        "party_type": party_type,
        "name": party.name,
        "gstin": _first(address.gstin if address else "", party.gstin),
        "gst_category": party.gst_category,
        "phone": _first(address.phone if address else "", party.phone),
        "email": party.email or "",
        "state": state_block(
            address.state if address and address.state else party.state
        ),
        "address": _address_block(address) if address else None,
    }


def party_blocks(party, party_type):
    """``(bill_to, ship_to)`` party blocks (name, GSTIN, state, address)."""
    billing, shipping = _party_addresses(party)
    bill = _party_core(party, party_type, billing)
    ship = _party_core(party, party_type, shipping)
    ship["same_as_billing"] = shipping is None or shipping == billing
    return bill, ship


def _place_of_supply(doc, party):
    return state_block(getattr(doc, "place_of_supply", None) or party.state)


def copy_block(raw):
    key = raw if raw in COPIES else "original"
    return {"key": key, "options": list(COPIES)}


def _meta(doc, kind, title_key, title, date, copy, **extra):
    meta = {
        "kind": kind,
        "title_key": title_key,
        "title": title,
        "name": doc.name,
        "date": iso(date),
        "docstatus": doc.docstatus,
        "is_draft": doc.docstatus == DocStatus.DRAFT,
        "is_cancelled": doc.docstatus == DocStatus.CANCELLED,
        "copy": copy_block(copy),
    }
    meta.update(extra)
    return meta


# ------------------------------------------------------------------ lines / tax


def _line(idx, line, with_prices=True, with_gst=True):
    item = line.item
    batch = getattr(line, "batch", None)
    expiry = (batch.expiry_date if batch else None) or getattr(
        line, "expiry_date", None
    )
    batch_no = batch.batch_no if batch else getattr(line, "supplier_batch_no", "")
    row = {
        "idx": idx,
        "item_code": item.code,
        "item_name": item.name,
        "description": item.description or "",
        "hsn": line.gst_hsn_code or item.gst_hsn_code,
        "batch_no": batch_no or "",
        "expiry_date": iso(expiry),
        "qty": qty(line.qty),
        "uom": item.stock_uom.name,
    }
    if with_prices:
        plr = getattr(line, "price_list_rate", None)
        per_unit = (plr - line.rate) if plr is not None and plr > line.rate else None
        row.update(
            {
                "rate": rate(line.rate),
                "price_list_rate": rate(plr) if plr is not None else None,
                "discount_amount": money(per_unit * line.qty) if per_unit else None,
                "discount_percent": (
                    str(_q(per_unit / plr * 100)) if per_unit and plr else None
                ),
                "amount": money(line.amount),
            }
        )
        if with_gst:
            row.update(
                {
                    "gst_rate": pct(line.gst_rate),
                    "gst_treatment": line.gst_treatment,
                    "cgst_amount": money(line.cgst_amount),
                    "sgst_amount": money(line.sgst_amount),
                    "igst_amount": money(line.igst_amount),
                }
            )
    return row


def _lines(doc, with_prices=True, with_gst=True):
    rows = doc.items.select_related("item", "item__stock_uom").order_by("pk")
    if hasattr(doc.item_model(), "batch"):
        rows = rows.select_related("batch")
    return [
        _line(i, line, with_prices, with_gst) for i, line in enumerate(rows, start=1)
    ]


def hsn_summary(doc):
    """Tax summary per (HSN, GST rate), summed from the stored line GST columns.

    These are the columns the tax engine wrote and GSTR-1 aggregates, so the totals
    equal the document's tax rows (a test asserts it)."""
    groups = {}
    for line in doc.items.select_related("item", "item__stock_uom").order_by("pk"):
        key = (line.gst_hsn_code or line.item.gst_hsn_code, line.gst_rate)
        g = groups.setdefault(
            key,
            {
                "qty": ZERO,
                "uom": set(),
                "taxable_value": ZERO,
                "cgst": ZERO,
                "sgst": ZERO,
                "igst": ZERO,
            },
        )
        g["qty"] += line.qty
        g["uom"].add(line.item.stock_uom.name)
        g["taxable_value"] += line.amount
        g["cgst"] += line.cgst_amount
        g["sgst"] += line.sgst_amount
        g["igst"] += line.igst_amount
    rows = []
    totals = {k: ZERO for k in ("taxable_value", "cgst", "sgst", "igst", "total_tax")}
    for hsn, gst_rate in sorted(groups):
        g = groups[(hsn, gst_rate)]
        g["total_tax"] = g["cgst"] + g["sgst"] + g["igst"]
        for name in totals:
            totals[name] += g[name]
        rows.append(
            {
                "hsn": hsn,
                "gst_rate": pct(gst_rate),
                "qty": qty(g["qty"]),
                "uom": ", ".join(sorted(g["uom"])),
                **{
                    k: money(g[k])
                    for k in ("taxable_value", "cgst", "sgst", "igst", "total_tax")
                },
            }
        )
    mode = "none"
    if totals["igst"]:
        mode = "igst"
    elif totals["cgst"] or totals["sgst"]:
        mode = "cgst_sgst"
    return {
        "mode": mode,
        "rows": rows,
        "totals": {k: money(v) for k, v in totals.items()},
    }


def tax_rows(doc):
    rows = doc.taxes.select_related("account").order_by("idx", "pk")
    return [
        {
            "description": t.description or t.account.name,
            "component": t.account.gst_component or "",
            "rate": pct(t.rate),
            "net_amount": money(t.net_amount),
            "tax_amount": money(t.tax_amount),
            "add_deduct": t.add_deduct_tax,
        }
        for t in rows
    ]


def totals_block(doc, lines, words_lang):
    total_qty = sum((Decimal(r["qty"]) for r in lines), Decimal("0"))
    return {
        "total_qty": qty(total_qty),
        "net_total": money(doc.net_total),
        "total_taxes_and_charges": money(doc.total_taxes_and_charges),
        "grand_total": money(doc.grand_total),
        "rounding_adjustment": money(doc.rounding_adjustment),
        "rounded_total": money(doc.rounded_total),
        "in_words": amount_in_words(abs(doc.rounded_total), words_lang),
        "words_lang": words_lang if words_lang in WORD_LANGS else "en",
    }


def _previous_balance(doc, party_type, party_name):
    """The party's ledger balance on its control account just before this voucher
    (rows up to the voucher date, the voucher itself excluded). Positive = the party
    owes us (customer) / we owe the party (supplier)."""
    receivable = party_type == "Customer"
    agg = (
        GLEntry.objects.filter(
            company_id=doc.company_id,
            party_type=party_type,
            party=party_name,
            account__account_type=(
                AccountType.RECEIVABLE if receivable else AccountType.PAYABLE
            ),
            is_cancelled=False,
            posting_date__lte=doc.posting_date,
        )
        .exclude(voucher_no=doc.name)
        .aggregate(dr=Sum("debit"), cr=Sum("credit"))
    )
    net = (agg["dr"] or ZERO) - (agg["cr"] or ZERO)
    return net if receivable else -net


def balances_block(doc, party_type, party_name):
    """Outstanding of the invoice and the party's previous / current ledger balance."""
    submitted = doc.docstatus == DocStatus.SUBMITTED
    block = {
        "outstanding_amount": money(doc.outstanding_amount) if submitted else None,
        "status": doc.display_status if submitted else None,
        "previous_balance": None,
        "current_balance": None,
    }
    if submitted:
        block["previous_balance"] = money(
            _previous_balance(doc, party_type, party_name)
        )
        block["current_balance"] = money(
            outstanding_service.party_balance(party_type, party_name, doc.company_id)
        )
    return block


def _return_reference(doc):
    if not getattr(doc, "is_return", False) or not doc.return_against_id:
        return None
    original = doc.return_against
    return {"name": original.name, "date": iso(original.posting_date)}


def _gst_block(doc, party):
    return {
        "company_gstin": doc.company_gstin or doc.company.gstin or "",
        "party_gstin": doc.party_gstin or party.gstin or "",
        "is_export_with_gst": doc.is_export_with_gst,
        "is_reverse_charge": doc.is_reverse_charge,
    }


# ------------------------------------------------------------------- documents


def sales_invoice(doc, params):
    """Tax Invoice, or Credit Note when ``doc.is_return``."""
    words_lang = params.get("words_lang", "en")
    bill, ship = party_blocks(doc.customer, "Customer")
    lines = _lines(doc)
    credit = doc.is_return
    return {
        **_meta(
            doc,
            "sales_invoice",
            "credit_note" if credit else "tax_invoice",
            "Credit Note" if credit else "Tax Invoice",
            doc.posting_date,
            params.get("copy"),
            due_date=iso(doc.due_date),
            is_return=credit,
            return_against=_return_reference(doc),
        ),
        "company": company_block(doc.company),
        "bill_to": bill,
        "ship_to": ship,
        "place_of_supply": _place_of_supply(doc, doc.customer),
        "gst": _gst_block(doc, doc.customer),
        "lines": lines,
        "hsn_summary": hsn_summary(doc),
        "taxes": tax_rows(doc),
        "totals": totals_block(doc, lines, words_lang),
        "balances": balances_block(doc, "Customer", doc.customer.name),
    }


def purchase_invoice(doc, params):
    """Purchase Invoice, or Debit Note when ``doc.is_return``."""
    words_lang = params.get("words_lang", "en")
    bill, ship = party_blocks(doc.supplier, "Supplier")
    lines = _lines(doc)
    debit = doc.is_return
    return {
        **_meta(
            doc,
            "purchase_invoice",
            "debit_note" if debit else "purchase_invoice",
            "Debit Note" if debit else "Purchase Invoice",
            doc.posting_date,
            params.get("copy"),
            due_date=iso(doc.due_date),
            is_return=debit,
            return_against=_return_reference(doc),
            supplier_invoice={
                "no": getattr(doc, "bill_no", ""),
                "date": iso(getattr(doc, "bill_date", None)),
            },
        ),
        "company": company_block(doc.company),
        "bill_to": bill,
        "ship_to": ship,
        "place_of_supply": _place_of_supply(doc, doc.supplier),
        "gst": _gst_block(doc, doc.supplier),
        "lines": lines,
        "hsn_summary": hsn_summary(doc),
        "taxes": tax_rows(doc),
        "totals": totals_block(doc, lines, words_lang),
        "balances": balances_block(doc, "Supplier", doc.supplier.name),
    }


def purchase_order(doc, params):
    words_lang = params.get("words_lang", "en")
    bill, ship = party_blocks(doc.supplier, "Supplier")
    lines = _lines(doc)
    return {
        **_meta(
            doc,
            "purchase_order",
            "purchase_order",
            "Purchase Order",
            doc.transaction_date,
            params.get("copy"),
            required_by=iso(doc.schedule_date),
            is_return=False,
            return_against=None,
        ),
        "company": company_block(doc.company),
        "bill_to": bill,
        "ship_to": ship,
        "place_of_supply": _place_of_supply(doc, doc.supplier),
        "gst": _gst_block(doc, doc.supplier),
        "lines": lines,
        "hsn_summary": hsn_summary(doc),
        "taxes": tax_rows(doc),
        "totals": totals_block(doc, lines, words_lang),
    }


def delivery_challan(doc, params, source):
    """Delivery Challan from a Sales Invoice (``source="sales-invoice"``) or a Sales
    Order. Prices, tax and totals are left out entirely unless ``with_prices``."""
    with_prices = bool(params.get("with_prices"))
    words_lang = params.get("words_lang", "en")
    bill, ship = party_blocks(doc.customer, "Customer")
    from_invoice = source == "sales-invoice"
    date = doc.posting_date if from_invoice else doc.transaction_date
    lines = _lines(doc, with_prices=with_prices, with_gst=False)
    payload = {
        **_meta(
            doc,
            "delivery_challan",
            "delivery_challan",
            "Delivery Challan",
            date,
            params.get("copy"),
            source={
                "type": "Sales Invoice" if from_invoice else "Sales Order",
                "name": doc.name,
                "date": iso(date),
            },
            is_return=bool(getattr(doc, "is_return", False)),
            return_against=_return_reference(doc) if from_invoice else None,
            with_prices=with_prices,
        ),
        "company": company_block(doc.company),
        "bill_to": bill,
        "ship_to": ship,
        "place_of_supply": _place_of_supply(doc, doc.customer),
        "lines": lines,
        "total_qty": qty(sum((Decimal(r["qty"]) for r in lines), Decimal("0"))),
    }
    if not from_invoice:
        payload["delivery_date"] = iso(doc.delivery_date)
    if with_prices:
        payload["hsn_summary"] = hsn_summary(doc)
        payload["taxes"] = tax_rows(doc)
        payload["totals"] = totals_block(doc, lines, words_lang)
    return payload


def _party_for_payment(entry):
    from buying.models import Supplier
    from selling.models import Customer

    model = {"Customer": Customer, "Supplier": Supplier}.get(entry.party_type)
    if model is None or not entry.party_name:
        return None
    return model.objects.filter(
        company_id=entry.company_id, name=entry.party_name
    ).first()


def payment_entry(entry, params):
    """Payment Receipt (Receive), Payment Voucher (Pay) or Internal Transfer voucher."""
    words_lang = params.get("words_lang", "en")
    receive = entry.payment_type == PaymentType.RECEIVE
    if receive:
        title_key, title = "payment_receipt", "Payment Receipt"
    elif entry.payment_type == PaymentType.INTERNAL_TRANSFER:
        title_key, title = "internal_transfer", "Internal Transfer"
    else:
        title_key, title = "payment_voucher", "Payment Voucher"

    party = _party_for_payment(entry)
    party_block = None
    if party is not None:
        party_block, _ship = party_blocks(party, entry.party_type)
    elif entry.party_name:
        party_block = {"party_type": entry.party_type, "name": entry.party_name}

    # The bank / cash side of the voucher is the mode of payment.
    money_account = entry.paid_to if receive else entry.paid_from
    mode = {AccountType.CASH: "cash", AccountType.BANK: "bank"}.get(
        money_account.account_type, "other"
    )

    refs = list(entry.references.order_by("pk"))
    allocations = []
    for ref in refs:
        try:
            model = entry.invoice_model(ref.reference_doctype)
        except (LookupError, ValueError):  # a draft may name an unknown doctype
            model = None
        inv = (
            model.objects.filter(
                company_id=entry.company_id, name=ref.reference_name
            ).first()
            if model is not None
            else None
        )
        allocations.append(
            {
                "reference_doctype": ref.reference_doctype,
                "reference_name": ref.reference_name,
                "invoice_date": iso(inv.posting_date) if inv else None,
                "invoice_total": money(inv.rounded_total) if inv else None,
                "allocated_amount": money(ref.allocated_amount),
                "outstanding_after": money(inv.outstanding_amount) if inv else None,
            }
        )
    allocated = sum((r.allocated_amount for r in refs), ZERO)
    return {
        **_meta(
            entry,
            "payment_entry",
            title_key,
            title,
            entry.posting_date,
            params.get("copy"),
            payment_type=entry.payment_type,
        ),
        "company": company_block(entry.company),
        "party": party_block,
        "payment": {
            "mode": mode,
            "account": money_account.name,
            "reference_no": entry.reference_no or "",
            "reference_date": iso(getattr(entry, "reference_date", None)),
            "paid_from": entry.paid_from.name,
            "paid_to": entry.paid_to.name,
        },
        "allocations": allocations,
        "totals": {
            "amount": money(entry.paid_amount),
            "allocated": money(allocated),
            "unallocated": money(entry.paid_amount - allocated),
            "in_words": amount_in_words(entry.paid_amount, words_lang),
            "words_lang": words_lang if words_lang in WORD_LANGS else "en",
        },
    }
