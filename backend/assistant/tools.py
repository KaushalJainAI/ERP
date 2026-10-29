"""Read-only and control tools for the AI assistant.

Invariant: NO WRITE TOOLS.
Every tool in this module is strictly read-only or control (asking the user / client navigation).
All user data returned to the LLM is wrapped in <<DATA>>...<</DATA>> to prevent prompt injection.
Every read tool checks user RBAC permissions before querying.
"""

from datetime import date, timedelta
from decimal import Decimal
import logging
import re
from typing import Any, Dict, List, Optional

from django.apps import apps
from django.db.models import Q
from django.utils import timezone

from users.permissions import user_has_model_permission

logger = logging.getLogger(__name__)


def wrap_data(val: Any) -> str:
    """Spotlight untrusted data inside <<DATA>> markers so it cannot act as instructions."""
    if val is None:
        return ""
    clean = str(val).replace("<</DATA>>", "").replace("<<DATA>>", "")
    return f"<<DATA>>{clean}<</DATA>>"


# -----------------------------------------------------------------------------
# Read Tools
# -----------------------------------------------------------------------------

def search_customers(user, query: str, company: Optional[str] = None) -> Dict[str, Any]:
    """Search customers by name, code, phone, GSTIN, and translated names."""
    if not user_has_model_permission(user, "selling.Customer", "can_read"):
        return {"error": "permission_denied", "message": "No permission to read customers."}

    query = (query or "").strip()
    if not query:
        return {"results": []}

    Customer = apps.get_model("selling.Customer")
    FieldTranslation = apps.get_model("core.FieldTranslation")

    # Match translated names
    translated_ids = FieldTranslation.objects.filter(
        model_label="selling.Customer",
        field_name="name",
        value__icontains=query,
    ).values_list("object_id", flat=True)

    q = Q(name__icontains=query) | Q(phone__icontains=query) | Q(gstin__icontains=query) | Q(pk__in=translated_ids)
    if hasattr(Customer, "customer_code"):
        q |= Q(customer_code__icontains=query)

    qs = Customer.objects.filter(q, is_active=True)
    if company:
        qs = qs.filter(company_id=company)

    customers = []
    for c in qs[:5]:
        customers.append({
            "id": c.pk,
            "name": wrap_data(c.name),
            "gstin": wrap_data(c.gstin or ""),
            "phone": wrap_data(c.phone or ""),
            "state": wrap_data(getattr(c.state, "name", "") if c.state else ""),
        })

    return {"results": customers, "count": len(customers)}


def search_suppliers(user, query: str, company: Optional[str] = None) -> Dict[str, Any]:
    """Search suppliers by name, phone, GSTIN, and translated names."""
    if not user_has_model_permission(user, "buying.Supplier", "can_read"):
        return {"error": "permission_denied", "message": "No permission to read suppliers."}

    query = (query or "").strip()
    if not query:
        return {"results": []}

    Supplier = apps.get_model("buying.Supplier")
    FieldTranslation = apps.get_model("core.FieldTranslation")

    translated_ids = FieldTranslation.objects.filter(
        model_label="buying.Supplier",
        field_name="name",
        value__icontains=query,
    ).values_list("object_id", flat=True)

    q = Q(name__icontains=query) | Q(phone__icontains=query) | Q(gstin__icontains=query) | Q(pk__in=translated_ids)
    if hasattr(Supplier, "supplier_code"):
        q |= Q(supplier_code__icontains=query)

    qs = Supplier.objects.filter(q, is_active=True)
    if company:
        qs = qs.filter(company_id=company)

    suppliers = []
    for s in qs[:5]:
        suppliers.append({
            "id": s.pk,
            "name": wrap_data(s.name),
            "gstin": wrap_data(s.gstin or ""),
            "phone": wrap_data(s.phone or ""),
            "state": wrap_data(getattr(s.state, "name", "") if s.state else ""),
        })

    return {"results": suppliers, "count": len(suppliers)}


def search_items(user, query: str, company: Optional[str] = None) -> Dict[str, Any]:
    """Search items by item_code, name, description, HSN code, or translated names."""
    if not user_has_model_permission(user, "stock.Item", "can_read"):
        return {"error": "permission_denied", "message": "No permission to read items."}

    query = (query or "").strip()
    if not query:
        return {"results": []}

    Item = apps.get_model("stock.Item")
    FieldTranslation = apps.get_model("core.FieldTranslation")

    translated_ids = FieldTranslation.objects.filter(
        model_label="stock.Item",
        field_name__in=["name", "description"],
        value__icontains=query,
    ).values_list("object_id", flat=True)

    q = (
        Q(code__icontains=query)
        | Q(name__icontains=query)
        | Q(description__icontains=query)
        | Q(gst_hsn_code__icontains=query)
        | Q(pk__in=translated_ids)
    )

    qs = Item.objects.filter(q, is_active=True)

    items = []
    for it in qs[:5]:
        items.append({
            "id": it.pk,
            "code": wrap_data(it.code),
            "name": wrap_data(it.name),
            "stock_uom": wrap_data(it.stock_uom or ""),
            "standard_rate": str(it.standard_rate) if it.standard_rate else "0.00",
            "is_stock_item": it.is_stock_item,
        })

    return {"results": items, "count": len(items)}


def search_employees(user, query: str, company: Optional[str] = None) -> Dict[str, Any]:
    """Search employees by name, mobile number, or employee ID."""
    if not user_has_model_permission(user, "hr.Employee", "can_read"):
        return {"error": "permission_denied", "message": "No permission to read employees."}

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
        employees.append({
            "id": e.pk,
            "name": wrap_data(full_name),
            "employee_id": wrap_data(e.name),
            "department": wrap_data(getattr(e.department, "name", "") if e.department else ""),
            "mobile_no": wrap_data(e.mobile_no or ""),
        })

    return {"results": employees, "count": len(employees)}


def get_stock(user, item_code: str, warehouse: Optional[str] = None) -> Dict[str, Any]:
    """Get current stock balance from Bin table."""
    if not user_has_model_permission(user, "stock.Bin", "can_read"):
        return {"error": "permission_denied", "message": "No permission to read stock balances."}

    Bin = apps.get_model("stock.Bin")
    Item = apps.get_model("stock.Item")

    try:
        item = Item.objects.get(Q(code=item_code) | Q(name=item_code))
    except (Item.DoesNotExist, Item.MultipleObjectsReturned):
        return {"error": "item_not_found", "message": f"Item '{item_code}' not found."}

    qs = Bin.objects.filter(item=item).select_related("warehouse")
    if warehouse:
        qs = qs.filter(Q(warehouse__name=warehouse) | Q(warehouse__pk=warehouse))

    balances = []
    total_qty = Decimal("0.000")
    for b in qs:
        total_qty += b.actual_qty
        balances.append({
            "warehouse": wrap_data(b.warehouse.name),
            "actual_qty": str(b.actual_qty),
        })

    return {
        "item_code": wrap_data(item.code),
        "item_name": wrap_data(item.name),
        "total_actual_qty": str(total_qty),
        "uom": wrap_data(item.stock_uom or ""),
        "by_warehouse": balances,
    }


def get_party_outstanding(user, party_type: str, party_id: Any, company: Optional[str] = None) -> Dict[str, Any]:
    """Get party total outstanding and open invoices."""
    pt = str(party_type).strip().capitalize()
    model_label = "selling.Customer" if pt == "Customer" else "buying.Supplier"
    if not user_has_model_permission(user, model_label, "can_read"):
        return {"error": "permission_denied", "message": f"No permission to read {pt}."}

    from accounts.services import outstanding as outstanding_service

    PartyModel = apps.get_model(model_label)
    try:
        party = PartyModel.objects.get(pk=party_id)
    except PartyModel.DoesNotExist:
        try:
            party = PartyModel.objects.get(name=party_id)
        except (PartyModel.DoesNotExist, PartyModel.MultipleObjectsReturned):
            return {"error": "party_not_found", "message": f"{pt} '{party_id}' not found."}

    company_id = company or getattr(party, "company_id", None)
    if not company_id:
        Company = apps.get_model("core.Company")
        first_co = Company.objects.first()
        company_id = first_co.pk if first_co else None

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
        inv_qs = SI.objects.filter(customer=party, docstatus=1, outstanding_amount__gt=0).order_by("posting_date")[:10]
        for inv in inv_qs:
            open_invoices.append({
                "invoice_no": wrap_data(inv.name),
                "posting_date": str(inv.posting_date),
                "grand_total": str(inv.grand_total),
                "outstanding_amount": str(inv.outstanding_amount),
            })
    else:
        PI = apps.get_model("buying.PurchaseInvoice")
        inv_qs = PI.objects.filter(supplier=party, docstatus=1, outstanding_amount__gt=0).order_by("posting_date")[:10]
        for inv in inv_qs:
            open_invoices.append({
                "invoice_no": wrap_data(inv.name),
                "posting_date": str(inv.posting_date),
                "grand_total": str(inv.grand_total),
                "outstanding_amount": str(inv.outstanding_amount),
            })

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

    Item = apps.get_model("stock.Item")
    try:
        item = Item.objects.get(Q(code=item_code) | Q(name=item_code))
    except (Item.DoesNotExist, Item.MultipleObjectsReturned):
        return {"error": "item_not_found", "message": f"Item '{item_code}' not found."}

    Company = apps.get_model("core.Company")
    co = None
    if company:
        co = Company.objects.filter(Q(pk=company) | Q(name=company)).first()
    if not co:
        co = Company.objects.first()

    customer = None
    supplier = None
    selling = True
    if party_type and party_id:
        pt = str(party_type).capitalize()
        if pt == "Customer":
            Customer = apps.get_model("selling.Customer")
            customer = Customer.objects.filter(Q(pk=party_id) | Q(name=party_id)).first()
            selling = True
        elif pt == "Supplier":
            Supplier = apps.get_model("buying.Supplier")
            supplier = Supplier.objects.filter(Q(pk=party_id) | Q(name=party_id)).first()
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
    qs = DocModel.objects.filter(**{f"{date_field}__gte": since.date() if date_field == "posting_date" else since})

    if party_id:
        qs = qs.filter(**{f"{party_attr}_id": party_id})

    results = []
    for d in qs.order_by(f"-{date_field}")[:10]:
        results.append({
            "id": d.pk,
            "name": wrap_data(d.name),
            "docstatus": d.docstatus,
            "date": str(getattr(d, date_field, "")),
            "total": str(getattr(d, "grand_total", getattr(d, "paid_amount", "0.00"))),
        })

    return {"doctype": dt, "count": len(results), "documents": results}


def get_document(user, doctype: str, id: Any) -> Dict[str, Any]:
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
    try:
        doc = DocModel.objects.get(Q(pk=id) | Q(name=id))
    except (DocModel.DoesNotExist, DocModel.MultipleObjectsReturned):
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
            items.append({
                "item": wrap_data(getattr(line.item, "code", "")),
                "qty": str(line.qty),
                "rate": str(line.rate),
                "amount": str(line.amount),
            })
        summary["items"] = items

    return summary


# -----------------------------------------------------------------------------
# Control Tools
# -----------------------------------------------------------------------------

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
    "/buying/purchase-invoices",
    "/buying/purchase-invoices/new",
    "/buying/purchase-orders",
    "/accounts/payment-entries",
    "/accounts/payment-entries/new",
    "/stock/items",
    "/stock/stock-entries",
    "/reports/general-ledger",
}


def navigate(route: str, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Navigate to a known route with optional filters."""
    clean_route = (route or "").split("?")[0].strip()
    if clean_route not in SAFE_ROUTES:
        return {"error": "invalid_route", "message": f"Route '{route}' is not in the allowed list."}

    return {
        "type": "navigate",
        "route": clean_route,
        "filters": filters or {},
    }


# Closed Registry of Read & Control Tools (No write tools allowed)
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
}

CONTROL_TOOLS = {
    "ask_user": ask_user,
    "navigate": navigate,
}
