"""Proposal builders for the assistant.

Invariant: ZERO WRITE TOOLS for documents.
Proposals build the data dictionary and dry-run the target serializer (calling
is_valid() ONLY, NEVER save()). The created proposal row is stored in
AssistantProposal for audit and form prefill handoff. The actual ERP document
is ONLY ever saved by the human user from the frontend form.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
import logging
from typing import Any, Dict, List, Optional

from django.apps import apps
from django.db.models import Q
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from users.permissions import user_has_model_permission
from .models import AssistantProposal, AssistantMessage
from .tools import wrap_data

logger = logging.getLogger(__name__)


def _q3(val: Any) -> Decimal:
    """Parse quantity to 3 decimal places."""
    try:
        return Decimal(str(val)).quantize(Decimal("0.001"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("1.000")


def _q2(val: Any) -> Decimal:
    """Parse money to 2 decimal places."""
    try:
        return Decimal(str(val)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def propose_sales_invoice(
    user,
    customer_query: str,
    items: List[Dict[str, Any]],
    posting_date: Optional[str] = None,
    due_date: Optional[str] = None,
    remarks: Optional[str] = None,
    company: Optional[Any] = None,
    message: Optional[AssistantMessage] = None,
) -> Dict[str, Any]:
    """Build a proposal for a New Sales Invoice.

    Never saves a SalesInvoice row. Dry-runs SalesInvoiceSerializer.is_valid().
    """
    if not user_has_model_permission(user, "selling.SalesInvoice", "can_create"):
        return {
            "error": "permission_denied",
            "message": "You do not have permission to create Sales Invoices.",
        }

    Customer = apps.get_model("selling.Customer")
    Item = apps.get_model("stock.Item")
    Company = apps.get_model("core.Company")
    Warehouse = apps.get_model("stock.Warehouse")
    FieldTranslation = apps.get_model("core.FieldTranslation")

    # 1. Resolve Customer
    cust_q = (customer_query or "").strip()
    trans_ids = FieldTranslation.objects.filter(
        model_label="selling.Customer",
        field_name="name",
        value__icontains=cust_q,
    ).values_list("object_id", flat=True)

    c_qs = Customer.objects.filter(
        Q(name__icontains=cust_q) | Q(pk__in=trans_ids),
        is_active=True,
    )
    if company:
        c_qs = c_qs.filter(company_id=company)

    cust_count = c_qs.count()
    if cust_count == 0:
        return {"error": "customer_not_found", "message": f"Customer '{customer_query}' not found."}
    if cust_count > 1:
        options = [f"{c.name} ({c.gstin or c.phone or c.pk})" for c in c_qs[:5]]
        return {
            "type": "question",
            "question": f"Which customer did you mean for '{customer_query}'?",
            "options": options,
        }

    customer = c_qs.first()

    # Resolve company
    co = None
    if company:
        co = Company.objects.filter(pk=company).first()
    if not co:
        co = customer.company or Company.objects.first()

    warnings: List[str] = []
    resolved_items = []
    preview_total = Decimal("0.00")
    ai_filled_keys = ["customer", "company", "posting_date", "items"]

    default_wh = Warehouse.objects.filter(company=co).first()

    # 2. Resolve Items
    for line in items:
        raw_item = str(line.get("item", "")).strip()
        it_trans = FieldTranslation.objects.filter(
            model_label="stock.Item",
            field_name__in=["name", "description"],
            value__icontains=raw_item,
        ).values_list("object_id", flat=True)

        i_qs = Item.objects.filter(
            Q(code__iexact=raw_item) | Q(name__icontains=raw_item) | Q(pk__in=it_trans),
            is_active=True,
        )
        i_count = i_qs.count()
        if i_count == 0:
            return {"error": "item_not_found", "message": f"Item '{raw_item}' not found."}
        if i_count > 1:
            options = [f"{it.name} [{it.code}]" for it in i_qs[:5]]
            return {
                "type": "question",
                "question": f"Which item did you mean for '{raw_item}'?",
                "options": options,
            }

        item = i_qs.first()
        qty = _q3(line.get("qty", 1))
        if qty <= 0:
            warnings.append(f"Quantity for {item.name} is zero or negative.")

        # Rate resolution
        from stock.services import pricing

        price_info = pricing.fetch_rate(
            item=item,
            selling=True,
            company=co,
            customer=customer,
            transaction_date=posting_date,
        )
        server_rate = Decimal(price_info.get("rate") or "0.00")

        user_rate = line.get("rate")
        if user_rate is not None and str(user_rate).strip() != "":
            rate = _q2(user_rate)
            if server_rate > 0 and rate != server_rate:
                warnings.append(f"Rate you specified for {item.name}: ₹{rate}, price list rate: ₹{server_rate}")
        else:
            rate = server_rate

        line_amount = _q2(qty * rate)
        preview_total += line_amount

        resolved_items.append({
            "item": item.pk,
            "item_name": item.name,
            "item_code": item.code,
            "warehouse": line.get("warehouse") or (default_wh.pk if default_wh else None),
            "qty": str(qty),
            "rate": str(rate) if rate > 0 else None,
            "amount": str(line_amount),
        })

    # Check credit limit
    if hasattr(customer, "credit_limit") and customer.credit_limit and customer.credit_limit > 0:
        from accounts.services import outstanding as outstanding_service
        try:
            curr_out = outstanding_service.party_balance("Customer", customer.pk, co.pk)
            if curr_out + preview_total > customer.credit_limit:
                warnings.append(
                    f"Customer credit limit is ₹{customer.credit_limit}; current outstanding is ₹{curr_out}."
                )
        except Exception:
            pass

    today_str = str(date.today())
    initial_values = {
        "customer": customer.pk,
        "customer_name": customer.name,
        "company": co.pk if co else None,
        "posting_date": posting_date or today_str,
        "due_date": due_date or posting_date or today_str,
        "items": resolved_items,
        "remarks": remarks or "",
    }

    # 3. Dry-run Serializer
    from selling.serializers import SalesInvoiceSerializer

    serializer_payload = {
        "customer": customer.pk,
        "company": co.pk if co else None,
        "posting_date": posting_date or today_str,
        "due_date": due_date or posting_date or today_str,
        "items": [
            {
                "item": ri["item"],
                "qty": ri["qty"],
                "rate": ri["rate"] or "0.00",
                "warehouse": ri["warehouse"],
            }
            for ri in resolved_items
        ],
    }

    factory = APIRequestFactory()
    req = factory.post("/api/selling/sales-invoices/")
    req.user = user
    drf_req = Request(req)

    serializer = SalesInvoiceSerializer(data=serializer_payload, context={"request": drf_req})
    if not serializer.is_valid():
        for field, errs in serializer.errors.items():
            warnings.append(f"{field}: {errs}")

    # Build summary
    item_parts = [f"{ri['item_name']} {ri['qty']} {item.stock_uom or 'unit'}" for ri in resolved_items]
    summary = f"Sales Invoice to {customer.name}: {', '.join(item_parts)} = ₹{preview_total} (before taxes)"

    proposal = AssistantProposal.objects.create(
        message=message,
        target="selling.SalesInvoice",
        route="/selling/sales-invoices/new",
        values=initial_values,
        summary=summary,
        ai_filled_keys=ai_filled_keys,
        warnings=warnings,
        status="proposed",
    )

    return {
        "type": "proposal",
        "proposal_id": str(proposal.id),
        "target": proposal.target,
        "route": proposal.route,
        "values": initial_values,
        "summary": summary,
        "ai_filled_keys": ai_filled_keys,
        "warnings": warnings,
    }


def propose_purchase_invoice(
    user,
    supplier_query: str,
    items: List[Dict[str, Any]],
    bill_no: Optional[str] = None,
    bill_date: Optional[str] = None,
    bill_total: Optional[Any] = None,
    posting_date: Optional[str] = None,
    remarks: Optional[str] = None,
    company: Optional[Any] = None,
    message: Optional[AssistantMessage] = None,
) -> Dict[str, Any]:
    """Build a proposal for a New Purchase Invoice.

    Never saves a PurchaseInvoice row. Dry-runs PurchaseInvoiceSerializer.is_valid().
    """
    if not user_has_model_permission(user, "buying.PurchaseInvoice", "can_create"):
        return {
            "error": "permission_denied",
            "message": "You do not have permission to create Purchase Invoices.",
        }

    Supplier = apps.get_model("buying.Supplier")
    Item = apps.get_model("stock.Item")
    Company = apps.get_model("core.Company")
    Warehouse = apps.get_model("stock.Warehouse")
    FieldTranslation = apps.get_model("core.FieldTranslation")

    supp_q = (supplier_query or "").strip()
    trans_ids = FieldTranslation.objects.filter(
        model_label="buying.Supplier",
        field_name="name",
        value__icontains=supp_q,
    ).values_list("object_id", flat=True)

    s_qs = Supplier.objects.filter(
        Q(name__icontains=supp_q) | Q(pk__in=trans_ids),
        is_active=True,
    )
    if company:
        s_qs = s_qs.filter(company_id=company)

    supp_count = s_qs.count()
    if supp_count == 0:
        return {"error": "supplier_not_found", "message": f"Supplier '{supplier_query}' not found."}
    if supp_count > 1:
        options = [f"{s.name} ({s.gstin or s.phone or s.pk})" for s in s_qs[:5]]
        return {
            "type": "question",
            "question": f"Which supplier did you mean for '{supplier_query}'?",
            "options": options,
        }

    supplier = s_qs.first()

    co = None
    if company:
        co = Company.objects.filter(pk=company).first()
    if not co:
        co = supplier.company or Company.objects.first()

    warnings: List[str] = []
    resolved_items = []
    preview_total = Decimal("0.00")
    ai_filled_keys = ["supplier", "company", "posting_date", "items"]

    default_wh = Warehouse.objects.filter(company=co).first()

    for line in items:
        raw_item = str(line.get("item", "")).strip()
        it_trans = FieldTranslation.objects.filter(
            model_label="stock.Item",
            field_name__in=["name", "description"],
            value__icontains=raw_item,
        ).values_list("object_id", flat=True)

        i_qs = Item.objects.filter(
            Q(code__iexact=raw_item) | Q(name__icontains=raw_item) | Q(pk__in=it_trans),
            is_active=True,
        )
        i_count = i_qs.count()
        if i_count == 0:
            return {"error": "item_not_found", "message": f"Item '{raw_item}' not found."}
        if i_count > 1:
            options = [f"{it.name} [{it.code}]" for it in i_qs[:5]]
            return {
                "type": "question",
                "question": f"Which item did you mean for '{raw_item}'?",
                "options": options,
            }

        item = i_qs.first()
        qty = _q3(line.get("qty", 1))
        if qty <= 0:
            warnings.append(f"Quantity for {item.name} is zero or negative.")

        from stock.services import pricing

        price_info = pricing.fetch_rate(
            item=item,
            selling=False,
            company=co,
            supplier=supplier,
            transaction_date=posting_date,
        )
        server_rate = Decimal(price_info.get("rate") or "0.00")

        user_rate = line.get("rate")
        if user_rate is not None and str(user_rate).strip() != "":
            rate = _q2(user_rate)
            if server_rate > 0 and rate != server_rate:
                warnings.append(f"Rate you specified for {item.name}: ₹{rate}, purchase rate: ₹{server_rate}")
        else:
            rate = server_rate

        line_amount = _q2(qty * rate)
        preview_total += line_amount

        resolved_items.append({
            "item": item.pk,
            "item_name": item.name,
            "item_code": item.code,
            "warehouse": line.get("warehouse") or (default_wh.pk if default_wh else None),
            "qty": str(qty),
            "rate": str(rate) if rate > 0 else None,
            "amount": str(line_amount),
        })

    # Bill total mismatch warning
    if bill_total is not None and str(bill_total).strip() != "":
        stated = _q2(bill_total)
        if stated > 0 and stated != preview_total:
            warnings.append(f"Bill total mismatch: stated ₹{stated}, computed items total is ₹{preview_total}")

    today_str = str(date.today())
    initial_values = {
        "supplier": supplier.pk,
        "supplier_name": supplier.name,
        "company": co.pk if co else None,
        "posting_date": posting_date or today_str,
        "bill_no": bill_no or "",
        "bill_date": bill_date or today_str,
        "items": resolved_items,
        "remarks": remarks or "",
    }
    if bill_no:
        ai_filled_keys.append("bill_no")
    if bill_date:
        ai_filled_keys.append("bill_date")

    # Dry-run Serializer
    from buying.serializers import PurchaseInvoiceSerializer

    serializer_payload = {
        "supplier": supplier.pk,
        "company": co.pk if co else None,
        "posting_date": posting_date or today_str,
        "bill_no": bill_no or "DRAFT-BILL",
        "bill_date": bill_date or today_str,
        "items": [
            {
                "item": ri["item"],
                "qty": ri["qty"],
                "rate": ri["rate"] or "0.00",
                "warehouse": ri["warehouse"],
            }
            for ri in resolved_items
        ],
    }

    factory = APIRequestFactory()
    req = factory.post("/api/buying/purchase-invoices/")
    req.user = user
    drf_req = Request(req)

    serializer = PurchaseInvoiceSerializer(data=serializer_payload, context={"request": drf_req})
    if not serializer.is_valid():
        for field, errs in serializer.errors.items():
            warnings.append(f"{field}: {errs}")

    item_parts = [f"{ri['item_name']} {ri['qty']}" for ri in resolved_items]
    summary = f"Purchase Invoice from {supplier.name}: {', '.join(item_parts)} = ₹{preview_total}"
    if bill_no:
        summary += f" (Bill #{bill_no})"

    proposal = AssistantProposal.objects.create(
        message=message,
        target="buying.PurchaseInvoice",
        route="/buying/purchase-invoices/new",
        values=initial_values,
        summary=summary,
        ai_filled_keys=ai_filled_keys,
        warnings=warnings,
        status="proposed",
    )

    return {
        "type": "proposal",
        "proposal_id": str(proposal.id),
        "target": proposal.target,
        "route": proposal.route,
        "values": initial_values,
        "summary": summary,
        "ai_filled_keys": ai_filled_keys,
        "warnings": warnings,
    }


def propose_payment_entry(
    user,
    party_type: str,
    party_query: str,
    amount: Any,
    payment_type: str = "Receive",
    mode_of_payment: Optional[str] = None,
    posting_date: Optional[str] = None,
    remarks: Optional[str] = None,
    company: Optional[Any] = None,
    message: Optional[AssistantMessage] = None,
) -> Dict[str, Any]:
    """Build a proposal for a New Payment Entry.

    Never saves a PaymentEntry row. Dry-runs PaymentEntrySerializer.is_valid().
    """
    if not user_has_model_permission(user, "accounts.PaymentEntry", "can_create"):
        return {
            "error": "permission_denied",
            "message": "You do not have permission to create Payment Entries.",
        }

    pt = str(party_type).capitalize()
    model_label = "selling.Customer" if pt == "Customer" else "buying.Supplier"
    PartyModel = apps.get_model(model_label)
    Company = apps.get_model("core.Company")
    Account = apps.get_model("accounts.Account")
    FieldTranslation = apps.get_model("core.FieldTranslation")

    pq = (party_query or "").strip()
    trans_ids = FieldTranslation.objects.filter(
        model_label=model_label,
        field_name="name",
        value__icontains=pq,
    ).values_list("object_id", flat=True)

    p_qs = PartyModel.objects.filter(
        Q(name__icontains=pq) | Q(pk__in=trans_ids),
        is_active=True,
    )
    if company:
        p_qs = p_qs.filter(company_id=company)

    p_count = p_qs.count()
    if p_count == 0:
        return {"error": "party_not_found", "message": f"{pt} '{party_query}' not found."}
    if p_count > 1:
        options = [f"{p.name} ({p.pk})" for p in p_qs[:5]]
        return {
            "type": "question",
            "question": f"Which {pt.lower()} did you mean for '{party_query}'?",
            "options": options,
        }

    party = p_qs.first()
    co = None
    if company:
        co = Company.objects.filter(pk=company).first()
    if not co:
        co = getattr(party, "company", None) or Company.objects.first()

    parsed_amount = _q2(amount)
    warnings: List[str] = []
    if parsed_amount <= 0:
        warnings.append("Payment amount must be greater than zero.")

    # Resolve default bank/cash account
    cash_or_bank = Account.objects.filter(
        company=co,
        account_type__in=["Bank", "Cash"],
        is_group=False,
    ).first()

    # Resolve debtor / creditor account
    party_account = None
    if pt == "Customer":
        party_account = Account.objects.filter(company=co, account_type="Receivable", is_group=False).first()
    else:
        party_account = Account.objects.filter(company=co, account_type="Payable", is_group=False).first()

    paid_from = cash_or_bank.pk if payment_type == "Pay" and cash_or_bank else (party_account.pk if party_account else None)
    paid_to = party_account.pk if payment_type == "Pay" and party_account else (cash_or_bank.pk if cash_or_bank else None)

    today_str = str(date.today())
    initial_values = {
        "payment_type": payment_type,
        "party_type": pt,
        "party": party.pk,
        "party_name": party.name,
        "company": co.pk if co else None,
        "paid_amount": str(parsed_amount),
        "received_amount": str(parsed_amount),
        "posting_date": posting_date or today_str,
        "paid_from": paid_from,
        "paid_to": paid_to,
        "remarks": remarks or "",
    }

    ai_filled_keys = ["payment_type", "party_type", "party", "paid_amount", "received_amount", "posting_date"]

    # Dry-run Serializer
    from accounts.serializers import PaymentEntrySerializer

    serializer_payload = {
        "payment_type": payment_type,
        "party_type": pt,
        "party": party.pk,
        "company": co.pk if co else None,
        "paid_amount": str(parsed_amount),
        "received_amount": str(parsed_amount),
        "posting_date": posting_date or today_str,
        "paid_from": paid_from,
        "paid_to": paid_to,
    }

    factory = APIRequestFactory()
    req = factory.post("/api/accounts/payment-entries/")
    req.user = user
    drf_req = Request(req)

    serializer = PaymentEntrySerializer(data=serializer_payload, context={"request": drf_req})
    if not serializer.is_valid():
        for field, errs in serializer.errors.items():
            warnings.append(f"{field}: {errs}")

    summary = f"Payment Entry ({payment_type}) for {pt} {party.name}: ₹{parsed_amount}"

    proposal = AssistantProposal.objects.create(
        message=message,
        target="accounts.PaymentEntry",
        route="/accounts/payment-entries/new",
        values=initial_values,
        summary=summary,
        ai_filled_keys=ai_filled_keys,
        warnings=warnings,
        status="proposed",
    )

    return {
        "type": "proposal",
        "proposal_id": str(proposal.id),
        "target": proposal.target,
        "route": proposal.route,
        "values": initial_values,
        "summary": summary,
        "ai_filled_keys": ai_filled_keys,
        "warnings": warnings,
    }


PROPOSAL_BUILDERS = {
    "propose_sales_invoice": propose_sales_invoice,
    "propose_purchase_invoice": propose_purchase_invoice,
    "propose_payment_entry": propose_payment_entry,
}
