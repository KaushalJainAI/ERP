"""Price-list resolution and the server-side line-rate fill (design section 5).

Resolution order for a selling document: explicit header list, the customer's
own list, the first ancestor customer group with a list, the company default.
Buying is the same with the supplier and the buying default.  Item prices are
matched on item + list + UOM (blank = stock UOM) and a date window, with the
latest ``valid_from`` winning; ties are broken by a UOM-specific row and then by
the newest primary key, so the answer is deterministic even when windows overlap.
"""

import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Case, IntegerField, Q, Value, When
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError

from stock.models import ItemPrice

TWO = Decimal("0.01")
SIX = Decimal("0.000001")


def q2(value):
    return Decimal(value).quantize(TWO, rounding=ROUND_HALF_UP)


def q6(value):
    return Decimal(value).quantize(SIX, rounding=ROUND_HALF_UP)


def _group_chain(group):
    """The group and its ancestors, nearest first (cycle-safe)."""
    seen = set()
    while group is not None and group.pk not in seen:
        seen.add(group.pk)
        yield group
        group = group.parent


def price_list_for(*, company, selling, customer=None, supplier=None, explicit=None):
    """Return ``(price_list, source)``; ``source`` is the design's source label."""
    if explicit is not None:
        return explicit, "explicit"
    if selling and customer is not None:
        if customer.default_price_list_id:
            return customer.default_price_list, "customer"
        for group in _group_chain(customer.customer_group):
            if group.default_price_list_id:
                return group.default_price_list, "customer_group"
        if company.default_selling_price_list_id:
            return company.default_selling_price_list, "company"
    if not selling and supplier is not None:
        if supplier.default_price_list_id:
            return supplier.default_price_list, "supplier"
        if company.default_buying_price_list_id:
            return company.default_buying_price_list, "company"
    return None, None


def validate_list(price_list, company, *, selling):
    """Refuse a list that cannot price this company's document."""
    if price_list is None:
        return
    if not price_list.enabled:
        raise ValidationError(
            _("Price List '%(name)s' is disabled.") % {"name": price_list.name}
        )
    if selling and not price_list.selling:
        raise ValidationError(
            _("Price List '%(name)s' is not a selling list.")
            % {"name": price_list.name}
        )
    if not selling and not price_list.buying:
        raise ValidationError(
            _("Price List '%(name)s' is not a buying list.")
            % {"name": price_list.name}
        )
    if price_list.currency != company.default_currency:
        raise ValidationError(
            _(
                "Price List '%(name)s' is in %(currency)s but the company uses "
                "%(company_currency)s; v1 has no currency conversion."
            )
            % {
                "name": price_list.name,
                "currency": price_list.currency,
                "company_currency": company.default_currency,
            }
        )


def item_price_for(item, price_list, transaction_date=None, uom=None):
    """The single best Item Price row, or ``None``."""
    date = transaction_date or datetime.date.today()
    uom_id = uom.pk if uom is not None and hasattr(uom, "pk") else uom
    qs = ItemPrice.objects.filter(item=item, price_list=price_list).filter(
        Q(uom__isnull=True) | Q(uom_id=uom_id) if uom_id else Q(uom__isnull=True)
    )
    qs = qs.filter(Q(valid_from__isnull=True) | Q(valid_from__lte=date)).filter(
        Q(valid_upto__isnull=True) | Q(valid_upto__gte=date)
    )
    return (
        qs.annotate(
            has_from=Case(
                When(valid_from__isnull=True, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
            uom_specific=Case(
                When(uom__isnull=True, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
        )
        .order_by("-has_from", "-valid_from", "-uom_specific", "-pk")
        .first()
    )


def fetch_rate(
    item,
    *,
    selling,
    company,
    customer=None,
    supplier=None,
    price_list=None,
    transaction_date=None,
    uom=None,
):
    """Resolve the list and the item rate; never raises for a missing row."""
    resolved, source = price_list_for(
        company=company,
        selling=selling,
        customer=customer,
        supplier=supplier,
        explicit=price_list,
    )
    row = (
        item_price_for(item, resolved, transaction_date, uom or item.stock_uom)
        if resolved is not None
        else None
    )
    result = {
        "item": item.pk,
        "price_list": resolved.pk if resolved else None,
        "price_list_name": resolved.name if resolved else None,
        "price_list_source": source if resolved else None,
        "item_price": row.pk if row else None,
        "price_list_rate": str(row.rate) if row else None,
        "rate": str(q2(row.rate)) if row else None,
        "rate_source": "price_list" if row else None,
        "last_rate_date": None,
        "currency": resolved.currency if resolved else company.default_currency,
        "valid_from": str(row.valid_from) if row and row.valid_from else None,
        "valid_upto": str(row.valid_upto) if row and row.valid_upto else None,
    }
    if row is None:
        last = last_party_rate(
            item, selling=selling, customer=customer, supplier=supplier
        )
        if last is not None:
            result["rate"] = str(last[0])
            result["rate_source"] = "last_bill"
            result["last_rate_date"] = str(last[1])
        elif item.standard_rate and item.standard_rate > 0:
            result["rate"] = str(q2(item.standard_rate))
            result["rate_source"] = "standard_rate"
    return result


def last_party_rate(item, *, selling, customer=None, supplier=None):
    """Rate of the party's most recent bill for this item, or None.

    Looks at the party's submitted ERP invoices. Returns (rate, posting_date).
    """
    party = customer if selling else supplier
    if party is None:
        return None
    if selling:
        from selling.models import SalesInvoiceItem

        erp = (
            SalesInvoiceItem.objects.filter(
                item=item,
                sales_invoice__customer=party,
                sales_invoice__docstatus=1,
                sales_invoice__is_return=False,
                rate__gt=0,
            )
            .order_by("-sales_invoice__posting_date", "-id")
            .values_list("rate", "sales_invoice__posting_date")
            .first()
        )
    else:
        from buying.models import PurchaseInvoiceItem

        erp = (
            PurchaseInvoiceItem.objects.filter(
                item=item,
                purchase_invoice__supplier=party,
                purchase_invoice__docstatus=1,
                purchase_invoice__is_return=False,
                rate__gt=0,
            )
            .order_by("-purchase_invoice__posting_date", "-id")
            .values_list("rate", "purchase_invoice__posting_date")
            .first()
        )
    return (q2(erp[0]), erp[1]) if erp else None


def _value(attrs, instance, name):
    if name in attrs:
        return attrs[name]
    if instance is not None:
        return getattr(instance, name, None)
    return None


def apply_line_rates(
    serializer, attrs, *, selling, party_field, date_field, company_field="company"
):
    """Fill omitted line rates from the resolved price list, in place.

    Called from a document serializer's ``validate`` so the same code serves a
    create and a draft update.  A supplied ``rate`` is kept; ``price_list_rate``
    is always snapshotted when a row exists.  When no row exists and no rate was
    supplied, the party's last billed rate (``last_party_rate``) and then the
    item's standard rate are the fallbacks; no rate at all is
    a 400 ("No price for Item X in Price List Y"), as in the design.
    """
    rows = attrs.get("items")
    instance = serializer.instance
    company = _value(attrs, instance, company_field)
    if company is None:
        return attrs
    party = _value(attrs, instance, party_field)
    date = _value(attrs, instance, date_field) or datetime.date.today()
    explicit = _value(attrs, instance, "price_list")
    price_list, _source = price_list_for(
        company=company,
        selling=selling,
        customer=party if selling else None,
        supplier=None if selling else party,
        explicit=explicit,
    )
    if price_list is not None:
        validate_list(price_list, company, selling=selling)
    if rows is None:
        # A header-only PATCH may still point the draft at a list; the rate
        # refresh action is what recomputes the existing lines.
        return attrs
    if price_list is not None:
        attrs["price_list"] = price_list
    for row in rows:
        item = row.get("item")
        if item is None:
            continue
        price_row = (
            item_price_for(item, price_list, date, item.stock_uom)
            if price_list is not None
            else None
        )
        supplied = row.get("rate")
        if supplied in (None, ""):
            last = (
                None
                if price_row is not None
                else last_party_rate(
                    item,
                    selling=selling,
                    customer=party if selling else None,
                    supplier=None if selling else party,
                )
            )
            if price_row is not None:
                row["rate"] = q2(price_row.rate)
            elif last is not None:
                row["rate"] = last[0]
            elif item.standard_rate and item.standard_rate > 0:
                row["rate"] = q2(item.standard_rate)
            else:
                raise ValidationError(
                    {
                        "items": _(
                            "No price for Item %(code)s in Price List "
                            "%(list)s, and the item has no standard rate."
                        )
                        % {
                            "code": item.code,
                            "list": price_list.name if price_list else "-",
                        }
                    }
                )
        if price_row is not None:
            row["price_list_rate"] = price_row.rate
    return attrs


def reapply_rates(document):
    """Draft-only: recompute every line's rate and list-rate snapshot.

    Returns the number of lines whose rate changed.  The caller recalculates the
    document totals afterwards.
    """
    selling = document._meta.model_name in ("salesorder", "salesinvoice")
    company = document.company
    party = document.customer if selling else document.supplier
    date = getattr(document, "transaction_date", None) or getattr(
        document, "posting_date", None
    )
    price_list, _source = price_list_for(
        company=company,
        selling=selling,
        customer=party if selling else None,
        supplier=None if selling else party,
        explicit=document.price_list,
    )
    validate_list(price_list, company, selling=selling)
    if price_list is not None:
        document.price_list = price_list
    changed = 0
    for line in document.items.select_related("item"):
        price_row = (
            item_price_for(line.item, price_list, date, line.item.stock_uom)
            if price_list is not None
            else None
        )
        if price_row is not None:
            new_rate = q2(price_row.rate)
            new_snapshot = price_row.rate
        elif line.item.standard_rate and line.item.standard_rate > 0:
            new_rate = q2(line.item.standard_rate)
            new_snapshot = None
        else:
            raise ValidationError(
                {
                    "items": _(
                        "No price for Item %(code)s in Price List %(list)s, and "
                        "the item has no standard rate."
                    )
                    % {
                        "code": line.item.code,
                        "list": price_list.name if price_list else "-",
                    }
                }
            )
        if line.rate != new_rate:
            line.rate = new_rate
            changed += 1
        line.price_list_rate = new_snapshot
        line.save(update_fields=["rate", "price_list_rate", "amount"])
    document.save(update_fields=["price_list"])
    return changed
