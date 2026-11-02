"""Cross-document invariants for ``verify_integrity`` (plan.md §6, #4 and #6).

* **Orphan postings (#6).** Every live (not cancelled) ``GLEntry`` and
  ``StockLedgerEntry`` row belongs to a document that exists and is submitted.
  Cancelling a document flags its rows and their reversals ``is_cancelled``, so
  a live row on a draft or cancelled document means the books and the
  documents disagree: an error. A row whose document cannot be found is a
  warning, because imported history may reference vouchers that were never
  created as ERP documents.
* **Order fulfilment (#4).** On a submitted Sales / Purchase Order, the quantity
  billed per item by its submitted, non-return invoices never exceeds the
  quantity ordered.
"""

from collections import defaultdict
from decimal import Decimal

from django.apps import apps
from django.db.models import Sum
from django.utils.translation import gettext as _

from core.models import DocStatus

CHUNK = 500


def _ledger_models():
    return (
        apps.get_model("accounts", "GLEntry"),
        apps.get_model("stock", "StockLedgerEntry"),
    )


def _vouchers_by_type(model):
    grouped = defaultdict(set)
    rows = (
        model.objects.filter(is_cancelled=False)
        .values_list("voucher_type", "voucher_no")
        .distinct()
    )
    for voucher_type, voucher_no in rows:
        grouped[voucher_type].add(voucher_no)
    return grouped


def orphan_postings():
    """``(errors, warnings)`` for live ledger rows whose document is missing or
    not submitted."""
    errors, warnings = [], []
    for ledger in _ledger_models():
        label = ledger._meta.object_name
        for voucher_type, names in sorted(_vouchers_by_type(ledger).items()):
            try:
                model = apps.get_model(voucher_type)
            except (LookupError, ValueError):
                warnings.append(
                    _("%(ledger)s rows reference an unknown document type '%(type)s'.")
                    % {"ledger": label, "type": voucher_type}
                )
                continue
            field_names = {f.name for f in model._meta.get_fields()}
            if "name" not in field_names:
                continue
            has_status = "docstatus" in field_names
            names = sorted(names)
            found = {}
            for start in range(0, len(names), CHUNK):
                chunk = names[start : start + CHUNK]
                values = ("name", "docstatus") if has_status else ("name",)
                for row in model.objects.filter(name__in=chunk).values_list(*values):
                    found[row[0]] = row[1] if has_status else DocStatus.SUBMITTED
            missing = [n for n in names if n not in found]
            if missing:
                warnings.append(
                    _(
                        "%(count)s %(ledger)s voucher(s) of type %(type)s have no "
                        "document, e.g. %(examples)s."
                    )
                    % {
                        "count": len(missing),
                        "ledger": label,
                        "type": voucher_type,
                        "examples": ", ".join(missing[:5]),
                    }
                )
            for name, status in sorted(found.items()):
                if status != DocStatus.SUBMITTED:
                    errors.append(
                        _(
                            "Orphan posting: %(ledger)s has live rows for %(type)s "
                            "%(name)s, which is not submitted (docstatus %(status)s)."
                        )
                        % {
                            "ledger": label,
                            "type": voucher_type,
                            "name": name,
                            "status": status,
                        }
                    )
    return errors, warnings


def _over_billed(order_model, invoice_model, order_fk, item_fk):
    """Errors for submitted orders billed beyond the ordered quantity."""
    errors = []
    item_model = invoice_model._meta.get_field("items").related_model
    billed = (
        item_model.objects.filter(
            **{
                f"{item_fk}__docstatus": DocStatus.SUBMITTED,
                f"{item_fk}__is_return": False,
                f"{item_fk}__{order_fk}__isnull": False,
            }
        )
        .values(f"{item_fk}__{order_fk}", "item_id", "item__code")
        .annotate(qty=Sum("qty"))
    )
    order_item_model = order_model._meta.get_field("items").related_model
    parent = order_item_model._meta.get_field(order_fk).name
    ordered = {
        (row[parent], row["item_id"]): row["qty"]
        for row in order_item_model.objects.filter(
            **{f"{parent}__docstatus": DocStatus.SUBMITTED}
        )
        .values(parent, "item_id")
        .annotate(qty=Sum("qty"))
    }
    names = dict(
        order_model.objects.filter(docstatus=DocStatus.SUBMITTED).values_list(
            "pk", "name"
        )
    )
    for row in billed:
        order_id = row[f"{item_fk}__{order_fk}"]
        if order_id not in names:
            continue  # draft/cancelled order: nothing to compare against
        limit = ordered.get((order_id, row["item_id"]), Decimal("0"))
        if row["qty"] > limit:
            errors.append(
                _(
                    "Over-billed order: %(order)s ordered %(ordered)s of %(item)s "
                    "but its submitted invoices bill %(billed)s."
                )
                % {
                    "order": names[order_id],
                    "ordered": limit,
                    "item": row["item__code"],
                    "billed": row["qty"],
                }
            )
    return errors


def order_fulfilment():
    """Errors for Sales / Purchase Orders billed beyond what was ordered."""
    return _over_billed(
        apps.get_model("selling", "SalesOrder"),
        apps.get_model("selling", "SalesInvoice"),
        "sales_order",
        "sales_invoice",
    ) + _over_billed(
        apps.get_model("buying", "PurchaseOrder"),
        apps.get_model("buying", "PurchaseInvoice"),
        "purchase_order",
        "purchase_invoice",
    )


def check():
    errors, warnings = orphan_postings()
    errors += order_fulfilment()
    return errors, warnings
