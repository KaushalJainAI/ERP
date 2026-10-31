"""Read-only and control tools for the AI assistant.

Invariant: NO WRITE TOOLS.
Every tool in this module is strictly read-only or control (asking the user / client navigation).
All user data returned to the LLM is wrapped in <<DATA>>...<</DATA>> to prevent prompt injection.
Every read tool checks user RBAC permissions before querying.
"""

from datetime import timedelta
import json
from decimal import Decimal
from difflib import SequenceMatcher
import logging
import re
from typing import Any, Dict, List, Optional

from django.apps import apps
from django.db.models import Count, Q, Sum
from django.utils import timezone

from users.permissions import user_has_model_permission
from .aliases import ITEM_ALIASES, strip_honorifics
from .transliterate import to_latin

logger = logging.getLogger(__name__)


def wrap_data(val: Any) -> str:
    """Spotlight untrusted data inside <<DATA>> markers so it cannot act as instructions."""
    if val is None:
        return ""
    clean = str(val).replace("<<", "«").replace(">>", "»")
    return f"<<DATA>>{clean}<</DATA>>"


def mask_phone(val: Any) -> str:
    """Last four digits only: tool output goes to a third-party model."""
    digits = re.sub(r"\D", "", str(val or ""))
    return f"******{digits[-4:]}" if len(digits) >= 4 else ""


# Below this score a spelling is a different word, not a typo. "saunf"/"sonf" is 0.67.
SPELLING_CUTOFF = 0.65
MAX_MATCHES = 5


def _spelling_score(query: str, name: str) -> float:
    """How closely every word of `query` matches some word of `name` (0 to 1)."""
    norm_query = to_latin(strip_honorifics(query))
    norm_name = to_latin(strip_honorifics(name))
    name_words = re.findall(r"\w+", norm_name)
    query_words = re.findall(r"\w+", norm_query)
    if not name_words or not query_words:
        return 0.0

    def word_similarity(qw: str, nw: str) -> float:
        base = SequenceMatcher(None, qw, nw).ratio()
        if base >= 0.8:
            return base
        for canonical, aliases in ITEM_ALIASES.items():
            canonical_latin = to_latin(canonical)
            alias_latins = [to_latin(a) for a in aliases]
            all_forms = {canonical_latin, *alias_latins}
            if qw in all_forms and nw in all_forms:
                return 1.0
        return base

    best = [
        max(word_similarity(q, n) for n in name_words)
        for q in query_words
    ]
    return sum(best) / len(best)


def find_matches(
    queryset, query: str, fields: List[str], model_label: str
) -> Dict[str, Any]:
    """Rank `queryset` against what the user said, strictest rule first.

    Returns {"records": [...], "certain": bool}. Exact matches beat partial ones,
    so a name picked from a list always resolves even when longer names contain
    it ("Haldi" vs "Haldi 1kg"). `certain` is False for close spellings: those are
    suggestions to show the user, and a proposal must never pick one by itself.
    """
    query = (query or "").strip()
    if not query:
        return {"records": [], "certain": True}

    clean_query = strip_honorifics(query).strip() or query

    exact = Q()
    for field in fields:
        exact |= Q(**{f"{field}__iexact": clean_query})
    records = list(queryset.filter(exact)[:MAX_MATCHES])
    if records:
        return {"records": records, "certain": True}

    FieldTranslation = apps.get_model("core.FieldTranslation")
    translated_ids = FieldTranslation.objects.filter(
        model_label=model_label,
        field_name__in=["name", "description"],
        value__icontains=clean_query,
    ).values_list("object_id", flat=True)

    partial = Q(pk__in=translated_ids)
    for field in fields:
        partial |= Q(**{f"{field}__icontains": clean_query})
    records = list(queryset.filter(partial).distinct()[:MAX_MATCHES])
    if records:
        return {"records": records, "certain": True}

    all_words = Q()
    for word in clean_query.split():
        any_field = Q()
        for field in fields:
            any_field |= Q(**{f"{field}__icontains": word})
        all_words &= any_field
    records = list(queryset.filter(all_words).distinct()[:MAX_MATCHES])
    if records:
        return {"records": records, "certain": True}

    scored = [(_spelling_score(query, obj.name), obj) for obj in queryset]
    scored = [row for row in scored if row[0] >= SPELLING_CUTOFF]
    scored.sort(key=lambda row: row[0], reverse=True)
    return {"records": [obj for _, obj in scored[:MAX_MATCHES]], "certain": False}


def find_item(query: Any) -> Dict[str, Any]:
    Item = apps.get_model("stock.Item")
    return find_matches(
        Item.objects.filter(is_active=True),
        str(query or ""),
        ["code", "name"],
        "stock.Item",
    )


def find_party(
    model_label: str, query: Any, company: Optional[Any] = None
) -> Dict[str, Any]:
    Party = apps.get_model(model_label)
    queryset = Party.objects.filter(is_active=True)
    if company:
        queryset = queryset.filter(company_id=company)
    return find_matches(queryset, str(query or ""), ["name"], model_label)


def _one_item(query: Any) -> Dict[str, Any]:
    """The single item `query` means: {"item": obj}, or {"error", "message"}."""
    found = find_item(query)
    if len(found["records"]) == 1 and found["certain"]:
        return {"item": found["records"][0]}
    if found["records"]:
        names = ", ".join(wrap_data(it.name) for it in found["records"])
        return {"error": "ambiguous_item", "message": f"'{query}' could mean: {names}"}
    return {"error": "item_not_found", "message": f"Item '{query}' not found."}


def _one_party(model_label: str, query: Any, company: Optional[Any]) -> Dict[str, Any]:
    """The single party `query` (an id or a name) means."""
    if str(query).strip().isdigit():
        Party = apps.get_model(model_label)
        party = Party.objects.filter(pk=int(query))
        if company:
            party = party.filter(company_id=company)
        party = party.first()
        if party:
            return {"party": party}
        return {"error": "party_not_found", "message": f"Party '{query}' not found."}
    found = find_party(model_label, query, company)
    if len(found["records"]) == 1 and found["certain"]:
        return {"party": found["records"][0]}
    if found["records"]:
        names = ", ".join(wrap_data(p.name) for p in found["records"])
        return {"error": "ambiguous_party", "message": f"'{query}' could mean: {names}"}
    return {"error": "party_not_found", "message": f"Party '{query}' not found."}


# -----------------------------------------------------------------------------
# Read Tools
# -----------------------------------------------------------------------------


def search_customers(user, query: str, company: Optional[str] = None) -> Dict[str, Any]:
    """Search customers by name, tolerating spelling mistakes and translated names."""
    if not user_has_model_permission(user, "selling.Customer", "can_read"):
        return {
            "error": "permission_denied",
            "message": "No permission to read customers.",
        }

    found = find_party("selling.Customer", query, company)
    customers = []
    for c in found["records"]:
        customers.append(
            {
                "id": c.pk,
                "name": wrap_data(c.name),
                "gstin": wrap_data(c.gstin or ""),
                "phone": mask_phone(c.phone),
                "state": wrap_data(getattr(c.state, "name", "") if c.state else ""),
            }
        )

    return {
        "results": customers,
        "count": len(customers),
        "close_spelling": not found["certain"],
    }


def search_suppliers(user, query: str, company: Optional[str] = None) -> Dict[str, Any]:
    """Search suppliers by name, tolerating spelling mistakes and translated names."""
    if not user_has_model_permission(user, "buying.Supplier", "can_read"):
        return {
            "error": "permission_denied",
            "message": "No permission to read suppliers.",
        }

    found = find_party("buying.Supplier", query, company)
    suppliers = []
    for s in found["records"]:
        suppliers.append(
            {
                "id": s.pk,
                "name": wrap_data(s.name),
                "gstin": wrap_data(s.gstin or ""),
                "phone": mask_phone(s.phone),
                "state": wrap_data(getattr(s.state, "name", "") if s.state else ""),
            }
        )

    return {
        "results": suppliers,
        "count": len(suppliers),
        "close_spelling": not found["certain"],
    }


def search_items(user, query: str) -> Dict[str, Any]:
    """Search items by code or name, tolerating spelling mistakes and translated names."""
    if not user_has_model_permission(user, "stock.Item", "can_read"):
        return {"error": "permission_denied", "message": "No permission to read items."}

    found = find_item(query)
    items = []
    for it in found["records"]:
        items.append(
            {
                "id": it.pk,
                "code": wrap_data(it.code),
                "name": wrap_data(it.name),
                "stock_uom": wrap_data(it.stock_uom or ""),
                "standard_rate": str(it.standard_rate) if it.standard_rate else "0.00",
                "is_stock_item": it.is_stock_item,
            }
        )

    return {
        "results": items,
        "count": len(items),
        "close_spelling": not found["certain"],
    }


def search_employees(user, query: str, company: Optional[str] = None) -> Dict[str, Any]:
    """Search employees by name, mobile number, or employee ID."""
    if not user_has_model_permission(user, "hr.Employee", "can_read"):
        return {
            "error": "permission_denied",
            "message": "No permission to read employees.",
        }

    query = (query or "").strip()
    if not query:
        return {"results": []}

    Employee = apps.get_model("hr.Employee")
    q = (
        Q(name__icontains=query)
        | Q(first_name__icontains=query)
        | Q(last_name__icontains=query)
        | Q(mobile_no__icontains=query)
    )

    qs = Employee.objects.filter(q, status="Active")
    if company:
        qs = qs.filter(company_id=company)

    employees = []
    for e in qs[:5]:
        full_name = f"{e.first_name} {e.last_name}".strip() or e.name
        employees.append(
            {
                "id": e.pk,
                "name": wrap_data(full_name),
                "employee_id": wrap_data(e.name),
                "department": wrap_data(
                    getattr(e.department, "name", "") if e.department else ""
                ),
                "mobile_no": mask_phone(e.mobile_no),
            }
        )

    return {"results": employees, "count": len(employees)}


def get_stock(
    user,
    item_code: str,
    warehouse: Optional[str] = None,
    company: Optional[str] = None,
) -> Dict[str, Any]:
    """Get current stock balance from Bin table."""
    if not user_has_model_permission(user, "stock.Bin", "can_read"):
        return {
            "error": "permission_denied",
            "message": "No permission to read stock balances.",
        }

    Bin = apps.get_model("stock.Bin")

    match = _one_item(item_code)
    if "error" in match:
        return match
    item = match["item"]

    qs = Bin.objects.filter(item=item).select_related("warehouse")
    if company:
        qs = qs.filter(warehouse__company_id=company)
    if warehouse:
        qs = qs.filter(warehouse__name__iexact=str(warehouse).strip())

    balances = []
    total_qty = Decimal("0.000")
    for b in qs:
        total_qty += b.actual_qty
        balances.append(
            {
                "warehouse": wrap_data(b.warehouse.name),
                "actual_qty": str(b.actual_qty),
            }
        )

    return {
        "item_code": wrap_data(item.code),
        "item_name": wrap_data(item.name),
        "total_actual_qty": str(total_qty),
        "uom": wrap_data(item.stock_uom or ""),
        "by_warehouse": balances,
    }


def get_party_outstanding(
    user, party_type: str, party_id: Any, company: Optional[str] = None
) -> Dict[str, Any]:
    """Get party total outstanding and open invoices."""
    pt = str(party_type).strip().capitalize()
    model_label = "selling.Customer" if pt == "Customer" else "buying.Supplier"
    if not user_has_model_permission(user, model_label, "can_read"):
        return {"error": "permission_denied", "message": f"No permission to read {pt}."}

    from accounts.services import outstanding as outstanding_service

    match = _one_party(model_label, party_id, company)
    if "error" in match:
        return match
    party = match["party"]

    company_id = company or getattr(party, "company_id", None)

    balance = Decimal("0.00")
    if company_id:
        try:
            balance = outstanding_service.party_balance(pt, party.pk, company_id)
        except Exception as exc:
            logger.warning("Failed to fetch party_balance: %s", exc)

    # Fetch open invoices
    open_invoices = []
    if pt == "Customer":
        SI = apps.get_model("selling.SalesInvoice")
        inv_qs = SI.objects.filter(
            customer=party, company_id=company_id, docstatus=1, outstanding_amount__gt=0
        ).order_by("posting_date")[:10]
        for inv in inv_qs:
            open_invoices.append(
                {
                    "invoice_no": wrap_data(inv.name),
                    "posting_date": str(inv.posting_date),
                    "grand_total": str(inv.grand_total),
                    "outstanding_amount": str(inv.outstanding_amount),
                }
            )
    else:
        PI = apps.get_model("buying.PurchaseInvoice")
        inv_qs = PI.objects.filter(
            supplier=party, company_id=company_id, docstatus=1, outstanding_amount__gt=0
        ).order_by("posting_date")[:10]
        for inv in inv_qs:
            open_invoices.append(
                {
                    "invoice_no": wrap_data(inv.name),
                    "posting_date": str(inv.posting_date),
                    "grand_total": str(inv.grand_total),
                    "outstanding_amount": str(inv.outstanding_amount),
                }
            )

    return {
        "party_type": pt,
        "party_name": wrap_data(party.name),
        "total_outstanding": str(balance),
        "open_invoices": open_invoices,
    }


def get_rate(
    user,
    item_code: str,
    party_type: Optional[str] = None,
    party_id: Optional[Any] = None,
    company: Optional[str] = None,
    date_val: Optional[str] = None,
) -> Dict[str, Any]:
    """Read-only rate resolution via stock.services.pricing.fetch_rate."""
    if not user_has_model_permission(user, "stock.Item", "can_read"):
        return {"error": "permission_denied", "message": "No permission to read items."}

    from stock.services import pricing

    match = _one_item(item_code)
    if "error" in match:
        return match
    item = match["item"]

    Company = apps.get_model("core.Company")
    co = Company.objects.filter(pk=company).first() if company else None
    if not co:
        co = Company.objects.first()

    customer = None
    supplier = None
    selling = True
    if party_type and party_id:
        pt = str(party_type).capitalize()
        model_label = "selling.Customer" if pt == "Customer" else "buying.Supplier"
        party_match = _one_party(model_label, party_id, company)
        if "error" in party_match:
            return party_match
        if pt == "Customer":
            customer = party_match["party"]
        else:
            supplier = party_match["party"]
            selling = False

    try:
        rate_info = pricing.fetch_rate(
            item=item,
            selling=selling,
            company=co,
            customer=customer,
            supplier=supplier,
            transaction_date=date_val or None,
        )
        return {
            "item_code": wrap_data(item.code),
            "item_name": wrap_data(item.name),
            "rate": rate_info.get("rate"),
            "rate_source": rate_info.get("rate_source"),
            "currency": rate_info.get("currency"),
            "price_list": wrap_data(rate_info.get("price_list_name") or ""),
        }
    except Exception as exc:
        logger.warning("fetch_rate failed: %s", exc)
        return {"error": "rate_failed", "message": str(exc)}


def recent_documents(
    user,
    doctype: str,
    party_id: Optional[Any] = None,
    days: int = 30,
    company: Optional[str] = None,
) -> Dict[str, Any]:
    """Get recent documents of a given doctype."""
    dt = str(doctype).strip()
    mapping = {
        "SalesInvoice": ("selling.SalesInvoice", "customer"),
        "SalesOrder": ("selling.SalesOrder", "customer"),
        "PurchaseInvoice": ("buying.PurchaseInvoice", "supplier"),
        "PurchaseOrder": ("buying.PurchaseOrder", "supplier"),
        "PaymentEntry": ("accounts.PaymentEntry", "party"),
    }
    if dt not in mapping:
        return {"error": "unsupported_doctype", "message": f"Unsupported doctype: {dt}"}

    model_label, party_attr = mapping[dt]
    if not user_has_model_permission(user, model_label, "can_read"):
        return {"error": "permission_denied", "message": f"No permission to read {dt}."}

    DocModel = apps.get_model(model_label)
    since = timezone.now() - timedelta(days=days)

    date_field = "posting_date" if hasattr(DocModel, "posting_date") else "created_at"
    qs = DocModel.objects.filter(
        **{
            f"{date_field}__gte": (
                since.date() if date_field == "posting_date" else since
            )
        }
    )

    if company:
        qs = qs.filter(company_id=company)
    if str(party_id or "").strip().isdigit():
        qs = qs.filter(**{f"{party_attr}_id": int(party_id)})

    results = []
    for d in qs.order_by(f"-{date_field}")[:10]:
        results.append(
            {
                "id": d.pk,
                "name": wrap_data(d.name),
                "docstatus": d.docstatus,
                "date": str(getattr(d, date_field, "")),
                "total": str(
                    getattr(d, "grand_total", getattr(d, "paid_amount", "0.00"))
                ),
            }
        )

    return {"doctype": dt, "count": len(results), "documents": results}


def get_document(
    user, doctype: str, id: Any, company: Optional[str] = None
) -> Dict[str, Any]:
    """Get single document details."""
    dt = str(doctype).strip()
    mapping = {
        "SalesInvoice": "selling.SalesInvoice",
        "PurchaseInvoice": "buying.PurchaseInvoice",
        "PaymentEntry": "accounts.PaymentEntry",
    }
    if dt not in mapping:
        return {"error": "unsupported_doctype", "message": f"Unsupported doctype: {dt}"}

    model_label = mapping[dt]
    if not user_has_model_permission(user, model_label, "can_read"):
        return {"error": "permission_denied", "message": f"No permission to read {dt}."}

    DocModel = apps.get_model(model_label)
    qs = (
        DocModel.objects.filter(company_id=company)
        if company
        else DocModel.objects.all()
    )
    lookup = Q(name=str(id))
    if str(id).strip().isdigit():
        lookup |= Q(pk=int(id))
    doc = qs.filter(lookup).first()
    if doc is None:
        return {"error": "not_found", "message": f"{dt} '{id}' not found."}

    summary = {
        "id": doc.pk,
        "name": wrap_data(doc.name),
        "docstatus": doc.docstatus,
        "date": str(getattr(doc, "posting_date", "")),
        "total": str(getattr(doc, "grand_total", getattr(doc, "paid_amount", "0.00"))),
    }

    if hasattr(doc, "items"):
        items = []
        for line in doc.items.all()[:20]:
            items.append(
                {
                    "item": wrap_data(getattr(line.item, "code", "")),
                    "qty": str(line.qty),
                    "rate": str(line.rate),
                    "amount": str(line.amount),
                }
            )
        summary["items"] = items

    return summary


PERIOD_DAYS = {"today": 0, "7d": 6, "30d": 29, "90d": 89, "365d": 364}


def _submitted_sales(company: Optional[str], period: str):
    """Submitted, non-return invoices in a named period, and its first date."""
    SI = apps.get_model("selling.SalesInvoice")
    start = timezone.localdate() - timedelta(days=PERIOD_DAYS.get(period, 29))
    qs = SI.objects.filter(docstatus=1, is_return=False, posting_date__gte=start)
    if company:
        qs = qs.filter(company_id=company)
    return qs, start


def sales_summary(
    user, period: str = "30d", company: Optional[str] = None
) -> Dict[str, Any]:
    """Invoiced sales and what is still unpaid, over a named period."""
    if not user_has_model_permission(user, "selling.SalesInvoice", "can_read"):
        return {
            "error": "permission_denied",
            "message": "No permission to read sales invoices.",
        }

    qs, start = _submitted_sales(company, period)
    totals = qs.aggregate(
        invoices=Count("pk"),
        grand_total=Sum("grand_total"),
        outstanding=Sum("outstanding_amount"),
    )
    return {
        "from_date": str(start),
        "to_date": str(timezone.localdate()),
        "invoices": totals["invoices"],
        "grand_total": f"{Decimal(str(totals['grand_total'] or '0.00')):.2f}",
        "outstanding": f"{Decimal(str(totals['outstanding'] or '0.00')):.2f}",
    }


def top_items(
    user,
    period: str = "30d",
    limit: int = 10,
    company: Optional[str] = None,
) -> Dict[str, Any]:
    """Best-selling items by billed amount over a named period."""
    if not user_has_model_permission(user, "selling.SalesInvoice", "can_read"):
        return {
            "error": "permission_denied",
            "message": "No permission to read sales invoices.",
        }

    SIItem = apps.get_model("selling.SalesInvoiceItem")
    qs, start = _submitted_sales(company, period)
    limit = max(1, min(int(limit or 10), 20))
    rows = (
        SIItem.objects.filter(sales_invoice__in=qs)
        .values("item__name")
        .annotate(qty=Sum("qty"), amount=Sum("amount"))
        .order_by("-amount")[:limit]
    )
    return {
        "from_date": str(start),
        "items": [
            {
                "name": wrap_data(r["item__name"]),
                "qty": str(r["qty"]),
                "amount": str(r["amount"]),
            }
            for r in rows
        ],
    }


# -----------------------------------------------------------------------------
# Control Tools
# -----------------------------------------------------------------------------


def _stock_warehouse(company: Any, direction: str = "sales") -> Any:
    """Group warehouses hold no stock, so a line can never point at one.

    Uses Company.ready_warehouse for sales and Company.unprocessed_warehouse for purchases,
    falling back to the first non-group warehouse if not set.
    """
    if company:
        if not hasattr(company, "ready_warehouse"):
            Company = apps.get_model("core.Company")
            company = Company.objects.filter(pk=company).first()
        if company:
            if direction in ("sales", "selling") and getattr(company, "ready_warehouse_id", None):
                return company.ready_warehouse
            elif direction in ("purchase", "purchases", "buying") and getattr(company, "unprocessed_warehouse_id", None):
                return company.unprocessed_warehouse

    Warehouse = apps.get_model("stock.Warehouse")
    return (
        Warehouse.objects.filter(company=company, is_group=False).order_by("id").first()
    )


def ask_user(question: str, options: List[str]) -> Dict[str, Any]:
    """Produce tappable choices for ambiguity."""
    return {
        "type": "question",
        "question": question,
        "options": options or [],
    }


SAFE_ROUTES = {
    "/selling/sales-invoices",
    "/selling/sales-invoices/new",
    "/selling/sales-orders",
    "/selling/sales-orders/new",
    "/buying/purchase-invoices",
    "/buying/purchase-invoices/new",
    "/buying/purchase-orders",
    "/accounts/payment-entries",
    "/accounts/payment-entries/new",
    "/stock/items",
    "/stock/stock-entries",
    "/accounts/reports/general-ledger",
}


def navigate(route: str, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Navigate to a known route with optional filters."""
    clean_route = (route or "").split("?")[0].strip()
    if clean_route not in SAFE_ROUTES:
        return {
            "error": "invalid_route",
            "message": f"Route '{route}' is not in the allowed list.",
        }

    return {
        "type": "navigate",
        "route": clean_route,
        "filters": filters or {},
    }


# Closed Registry of Read & Control Tools (No write tools allowed)
def _wrap_text(obj: Any) -> Any:
    """Recursively spotlight every text value; numbers and keys are left as they are."""
    if isinstance(obj, dict):
        return {k: _wrap_text(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_wrap_text(v) for v in obj]
    if isinstance(obj, str):
        return wrap_data(obj)
    return obj


def _read_result(user, model_label: str, message: str, build) -> Dict[str, Any]:
    """Run a report service after the read check; return JSON-safe, text-wrapped data."""
    if not user_has_model_permission(user, model_label, "can_read"):
        return {"error": "permission_denied", "message": message}
    result = json.loads(json.dumps(build(), default=str))
    return _wrap_text(result)


def pending_orders(user, company: Optional[str] = None) -> Dict[str, Any]:
    """Sales orders not yet fully dispatched, oldest first (same as the Pending Orders page)."""
    from selling.services import dispatch

    return _read_result(
        user,
        "selling.SalesOrder",
        "No permission to read sales orders.",
        lambda: {"orders": dispatch.pending_orders(company)},
    )


def money_to_collect(
    user, as_on: Optional[str] = None, company: Optional[str] = None
) -> Dict[str, Any]:
    """Open receivables by customer (same as the Money to Collect screen)."""
    from accounts.services import collections as collections_service

    return _read_result(
        user,
        "selling.SalesInvoice",
        "No permission to read sales invoices.",
        lambda: collections_service.collections(company, as_on=as_on),
    )


def bills_to_pay(
    user,
    as_on: Optional[str] = None,
    filter_type: Optional[str] = None,
    company: Optional[str] = None,
) -> Dict[str, Any]:
    """Open supplier bills with due dates (same as the Bills to Pay screen)."""
    from accounts.services import collections as collections_service

    return _read_result(
        user,
        "buying.PurchaseInvoice",
        "No permission to read purchase invoices.",
        lambda: collections_service.bills_to_pay(
            company, as_on=as_on, filter_type=filter_type
        ),
    )


EXPENSE_PERIOD_DAYS = {"7d": 6, "30d": 29, "90d": 89, "365d": 364}


def expense_summary_tool(
    user, period: str = "30d", company: Optional[str] = None
) -> Dict[str, Any]:
    """Expenses and wages by category over a named period (same as the Expenses report)."""
    from accounts.reports.expenses import expense_summary

    today = timezone.localdate()
    start = today - timedelta(days=EXPENSE_PERIOD_DAYS.get(period, 29))
    return _read_result(
        user,
        "accounts.GLEntry",
        "No permission to read accounting entries.",
        lambda: expense_summary(company, start, today),
    )


def gst_position_tool(
    user, month: Optional[str] = None, company: Optional[str] = None
) -> Dict[str, Any]:
    """GST output, input credit and net payable for a month, YYYY-MM (default: this month)."""
    from accounts.services.gst_position import gst_position

    month = month or timezone.localdate().strftime("%Y-%m")
    return _read_result(
        user,
        "accounts.GLEntry",
        "No permission to read accounting entries.",
        lambda: gst_position(company, month),
    )


def stock_stages(user, company: Optional[str] = None) -> Dict[str, Any]:
    """Stock per item by stage: unprocessed, in process, ready (same as the Stock view)."""
    from stock.reports.stages import stock_stages_report

    return _read_result(
        user,
        "stock.Bin",
        "No permission to read stock.",
        lambda: {"items": stock_stages_report(company)},
    )


READ_TOOLS = {
    "search_customers": search_customers,
    "search_suppliers": search_suppliers,
    "search_items": search_items,
    "search_employees": search_employees,
    "get_stock": get_stock,
    "get_party_outstanding": get_party_outstanding,
    "get_rate": get_rate,
    "recent_documents": recent_documents,
    "get_document": get_document,
    "sales_summary": sales_summary,
    "top_items": top_items,
    "pending_orders": pending_orders,
    "money_to_collect": money_to_collect,
    "bills_to_pay": bills_to_pay,
    "expense_summary": expense_summary_tool,
    "gst_position": gst_position_tool,
    "stock_stages": stock_stages,
}

CONTROL_TOOLS = {
    "ask_user": ask_user,
    "navigate": navigate,
}
