"""Proposal builders for the assistant.

Invariant: ZERO WRITE TOOLS for documents.
Proposals build the data dictionary and dry-run the target serializer (calling
is_valid() ONLY, NEVER save()). The created proposal row is stored in
AssistantProposal for audit and form prefill handoff. The actual ERP document
is ONLY ever saved by the human user from the frontend form.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import logging
from typing import Any, Dict, List, Optional

from django.apps import apps
from django.db.models import Count
from django.utils.translation import gettext as _
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from users.permissions import user_has_model_permission
from .models import AssistantProposal, AssistantMessage
from .tools import find_item, find_party, _stock_warehouse

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


def _pick_one(found: Dict[str, Any], kind: str, query: Any, label) -> Dict[str, Any]:
    """{"record": obj} when `found` is one certain match, else a question or an error.

    A close spelling is only ever offered: the user confirms it, then the next
    call arrives with the exact name.
    """
    records = found["records"]
    if len(records) == 1 and found["certain"]:
        return {"record": records[0]}
    if records:
        return {
            "type": "question",
            "question": _("Which %(kind)s did you mean for '%(query)s'?")
            % {"kind": kind, "query": query},
            "options": [label(r) for r in records],
        }
    return {
        "error": f"{kind}_not_found",
        "message": _("%(kind)s '%(query)s' not found.")
        % {"kind": kind.capitalize(), "query": query},
    }


def _customer_choice_label(customer) -> str:
    """Format customer option with town and last order date if available."""
    parts = []
    addr = getattr(customer, "addresses", None)
    if addr:
        first_addr = addr.filter(is_active=True).first()
        if first_addr and first_addr.city:
            parts.append(first_addr.city)

    so = getattr(customer, "sales_orders", None)
    if so:
        last_date = (
            so.filter(docstatus__lt=2)
            .order_by("-transaction_date")
            .values_list("transaction_date", flat=True)
            .first()
        )
        if last_date:
            parts.append(f"last order: {last_date}")

    if parts:
        return f"{customer.name} ({', '.join(parts)})"
    return customer.name


def _pick_item_with_habit(
    found: Dict[str, Any],
    query: Any,
    customer: Any,
    warnings: List[str],
) -> Dict[str, Any]:
    """Pick item, ranking by customer's last 90 days of SalesOrderItem lines when known.

    If one item covers >= 80% of their matching lines, return it with a warning to confirm
    rather than asking. Otherwise ask, showing usual pack first.
    """
    records = found.get("records", [])
    if len(records) == 1 and found.get("certain"):
        return {"record": records[0]}

    if not records:
        return {
            "error": "item_not_found",
            "message": _("Item '%(query)s' not found.") % {"query": query},
        }

    # If customer is known and multiple records match:
    if len(records) > 1 and customer:
        SalesOrderItem = apps.get_model("selling.SalesOrderItem")
        cutoff = date.today() - timedelta(days=90)
        so_lines = SalesOrderItem.objects.filter(
            sales_order__customer=customer,
            sales_order__transaction_date__gte=cutoff,
            sales_order__docstatus__lt=2,
            item__in=records,
        )
        counts = dict(
            so_lines.values("item_id")
            .annotate(cnt=Count("id"))
            .values_list("item_id", "cnt")
        )
        total_lines = sum(counts.values())

        if total_lines > 0:
            records = sorted(records, key=lambda it: counts.get(it.pk, 0), reverse=True)
            top_item = records[0]
            top_count = counts.get(top_item.pk, 0)

            if (top_count / total_lines) >= 0.8:
                pct = int((top_count / total_lines) * 100)
                warnings.append(
                    _(
                        "Selected %(item)s based on customer's usual orders (%(pct)d%% of past 90 days). Please confirm."
                    )
                    % {"item": top_item.name, "pct": pct}
                )
                return {"record": top_item}

    return {
        "type": "question",
        "question": _("Which item did you mean for '%(query)s'?") % {"query": query},
        "options": [it.name for it in records],
    }


def _company_of(company: Optional[Any], party) -> Any:
    Company = apps.get_model("core.Company")
    co = Company.objects.filter(pk=company).first() if company else None
    return co or party.company or Company.objects.first()


def _cash_or_bank_account(co, mode_of_payment: Optional[str]):
    """(account, usual_found) for a payment. "cash" must land on a cash account;
    anything else (cheque, UPI) goes through the bank. When the usual kind is
    missing, any cash or bank account is returned with usual_found False.
    """
    Account = apps.get_model("accounts.Account")
    is_cash = "cash" in (mode_of_payment or "").lower()
    accounts = Account.objects.filter(company=co, is_group=False).order_by("id")
    usual = accounts.filter(account_type="Cash" if is_cash else "Bank").first()
    if usual is not None:
        return usual, True
    return accounts.filter(account_type__in=["Bank", "Cash"]).first(), False


def propose_sales_order(
    user,
    customer_query: str,
    items: List[Dict[str, Any]],
    transaction_date: Optional[str] = None,
    delivery_date: Optional[str] = None,
    company: Optional[Any] = None,
    message: Optional[AssistantMessage] = None,
) -> Dict[str, Any]:
    """Build a proposal for a New Sales Order.

    Never saves a SalesOrder row. Dry-runs SalesOrderSerializer.is_valid().
    """
    if not user_has_model_permission(user, "selling.SalesOrder", "can_create"):
        return {
            "error": "permission_denied",
            "message": _("You do not have permission to create Sales Orders."),
        }

    # 1. Resolve Customer
    picked = _pick_one(
        find_party("selling.Customer", customer_query, company),
        "customer",
        customer_query,
        _customer_choice_label,
    )
    if "record" not in picked:
        return picked
    customer = picked["record"]
    co = _company_of(company, customer)

    warnings: List[str] = []
    resolved_items = []
    preview_total = Decimal("0.00")
    ai_filled_keys = ["customer", "company", "transaction_date", "delivery_date", "items"]

    default_wh = _stock_warehouse(co, direction="sales")

    # 2. Resolve Items
    for line in items:
        raw_item = str(line.get("item", "")).strip()
        picked = _pick_item_with_habit(find_item(raw_item), raw_item, customer, warnings)
        if "record" not in picked:
            return picked
        item = picked["record"]
        qty = _q3(line.get("qty", 1))
        if qty <= 0:
            warnings.append(
                _("Quantity for %(item)s is zero or negative.") % {"item": item.name}
            )

        # Rate resolution
        from stock.services import pricing

        price_info = pricing.fetch_rate(
            item=item,
            selling=True,
            company=co,
            customer=customer,
            transaction_date=transaction_date,
        )
        server_rate = Decimal(price_info.get("rate") or "0.00")

        user_rate = line.get("rate")
        if user_rate is not None and str(user_rate).strip() != "":
            rate = _q2(user_rate)
            if server_rate > 0 and rate != server_rate:
                warnings.append(
                    _(
                        "Rate you specified for %(item)s: ₹%(rate)s, price list rate: ₹%(server_rate)s"
                    )
                    % {"item": item.name, "rate": rate, "server_rate": server_rate}
                )
        else:
            rate = server_rate
            if rate <= 0:
                warnings.append(
                    _("No rate found for %(item)s. Please enter it in the form.")
                    % {"item": item.name}
                )

        line_amount = _q2(qty * rate)
        preview_total += line_amount

        resolved_items.append(
            {
                "item": item.pk,
                "item_name": item.name,
                "item_code": item.code,
                "warehouse": line.get("warehouse")
                or (default_wh.pk if default_wh else None),
                "qty": str(qty),
                "rate": str(rate) if rate > 0 else None,
                "amount": str(line_amount),
            }
        )

    # Check credit limit
    if (
        hasattr(customer, "credit_limit")
        and customer.credit_limit
        and customer.credit_limit > 0
    ):
        from accounts.services import outstanding as outstanding_service

        try:
            curr_out = outstanding_service.party_balance("Customer", customer.pk, co.pk)
            if curr_out + preview_total > customer.credit_limit:
                warnings.append(
                    _(
                        "Customer credit limit is ₹%(limit)s; current outstanding is ₹%(outstanding)s."
                    )
                    % {"limit": customer.credit_limit, "outstanding": curr_out}
                )
        except Exception:
            pass

    today_str = str(date.today())
    tx_date = transaction_date or today_str
    del_date = delivery_date or tx_date
    initial_values = {
        "customer": customer.pk,
        "customer_name": customer.name,
        "company": co.pk if co else None,
        "transaction_date": tx_date,
        "delivery_date": del_date,
        "items": resolved_items,
    }

    # 3. Dry-run Serializer
    from selling.serializers import SalesOrderSerializer

    serializer_payload = {
        "customer": customer.pk,
        "company": co.pk if co else None,
        "transaction_date": tx_date,
        "delivery_date": del_date,
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
    req = factory.post("/api/selling/sales-orders/")
    req.user = user
    drf_req = Request(req)

    serializer = SalesOrderSerializer(
        data=serializer_payload, context={"request": drf_req}
    )
    if not serializer.is_valid():
        for field, errs in serializer.errors.items():
            warnings.append(f"{field}: {errs}")

    # Build summary
    item_parts = [
        f"{ri['item_name']} {ri['qty']} {item.stock_uom or 'unit'}"
        for ri in resolved_items
    ]
    summary = _(
        "Sales Order for %(customer)s: %(items)s = ₹%(total)s (before taxes)"
    ) % {
        "customer": customer.name,
        "items": ", ".join(item_parts),
        "total": preview_total,
    }

    proposal = AssistantProposal.objects.create(
        message=message,
        target="selling.SalesOrder",
        route="/selling/sales-orders/new",
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
            "message": _("You do not have permission to create Sales Invoices."),
        }

    # 1. Resolve Customer
    picked = _pick_one(
        find_party("selling.Customer", customer_query, company),
        "customer",
        customer_query,
        _customer_choice_label,
    )
    if "record" not in picked:
        return picked
    customer = picked["record"]
    co = _company_of(company, customer)

    warnings: List[str] = []
    resolved_items = []
    preview_total = Decimal("0.00")
    ai_filled_keys = ["customer", "company", "posting_date", "items"]

    default_wh = _stock_warehouse(co, direction="sales")

    # 2. Resolve Items
    for line in items:
        raw_item = str(line.get("item", "")).strip()
        picked = _pick_item_with_habit(find_item(raw_item), raw_item, customer, warnings)
        if "record" not in picked:
            return picked
        item = picked["record"]
        qty = _q3(line.get("qty", 1))
        if qty <= 0:
            warnings.append(
                _("Quantity for %(item)s is zero or negative.") % {"item": item.name}
            )

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
                warnings.append(
                    _(
                        "Rate you specified for %(item)s: ₹%(rate)s, price list rate: ₹%(server_rate)s"
                    )
                    % {"item": item.name, "rate": rate, "server_rate": server_rate}
                )
        else:
            rate = server_rate
            if rate <= 0:
                warnings.append(
                    _("No rate found for %(item)s. Please enter it in the form.")
                    % {"item": item.name}
                )

        line_amount = _q2(qty * rate)
        preview_total += line_amount

        resolved_items.append(
            {
                "item": item.pk,
                "item_name": item.name,
                "item_code": item.code,
                "warehouse": line.get("warehouse")
                or (default_wh.pk if default_wh else None),
                "qty": str(qty),
                "rate": str(rate) if rate > 0 else None,
                "amount": str(line_amount),
            }
        )

    # Check credit limit
    if (
        hasattr(customer, "credit_limit")
        and customer.credit_limit
        and customer.credit_limit > 0
    ):
        from accounts.services import outstanding as outstanding_service

        try:
            curr_out = outstanding_service.party_balance("Customer", customer.pk, co.pk)
            if curr_out + preview_total > customer.credit_limit:
                warnings.append(
                    _(
                        "Customer credit limit is ₹%(limit)s; current outstanding is ₹%(outstanding)s."
                    )
                    % {"limit": customer.credit_limit, "outstanding": curr_out}
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

    serializer = SalesInvoiceSerializer(
        data=serializer_payload, context={"request": drf_req}
    )
    if not serializer.is_valid():
        for field, errs in serializer.errors.items():
            warnings.append(f"{field}: {errs}")

    # Build summary
    item_parts = [
        f"{ri['item_name']} {ri['qty']} {item.stock_uom or 'unit'}"
        for ri in resolved_items
    ]
    summary = _(
        "Sales Invoice to %(customer)s: %(items)s = ₹%(total)s (before taxes)"
    ) % {
        "customer": customer.name,
        "items": ", ".join(item_parts),
        "total": preview_total,
    }

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
            "message": _("You do not have permission to create Purchase Invoices."),
        }

    picked = _pick_one(
        find_party("buying.Supplier", supplier_query, company),
        "supplier",
        supplier_query,
        lambda p: p.name,
    )
    if "record" not in picked:
        return picked
    supplier = picked["record"]
    co = _company_of(company, supplier)

    warnings: List[str] = []
    resolved_items = []
    preview_total = Decimal("0.00")
    ai_filled_keys = ["supplier", "company", "posting_date", "items"]

    default_wh = _stock_warehouse(co, direction="purchases")

    for line in items:
        raw_item = str(line.get("item", "")).strip()
        picked = _pick_one(find_item(raw_item), "item", raw_item, lambda it: it.name)
        if "record" not in picked:
            return picked
        item = picked["record"]
        qty = _q3(line.get("qty", 1))
        if qty <= 0:
            warnings.append(
                _("Quantity for %(item)s is zero or negative.") % {"item": item.name}
            )

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
                warnings.append(
                    _(
                        "Rate you specified for %(item)s: ₹%(rate)s, purchase rate: ₹%(server_rate)s"
                    )
                    % {"item": item.name, "rate": rate, "server_rate": server_rate}
                )
        else:
            rate = server_rate
            if rate <= 0:
                warnings.append(
                    _("No rate found for %(item)s. Please enter it in the form.")
                    % {"item": item.name}
                )

        line_amount = _q2(qty * rate)
        preview_total += line_amount

        resolved_items.append(
            {
                "item": item.pk,
                "item_name": item.name,
                "item_code": item.code,
                "warehouse": line.get("warehouse")
                or (default_wh.pk if default_wh else None),
                "qty": str(qty),
                "rate": str(rate) if rate > 0 else None,
                "amount": str(line_amount),
            }
        )

    # Bill total mismatch warning
    if bill_total is not None and str(bill_total).strip() != "":
        stated = _q2(bill_total)
        if stated > 0 and stated != preview_total:
            warnings.append(
                _(
                    "Bill total mismatch: stated ₹%(stated)s, computed items total is ₹%(total)s"
                )
                % {"stated": stated, "total": preview_total}
            )

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

    serializer = PurchaseInvoiceSerializer(
        data=serializer_payload, context={"request": drf_req}
    )
    if not serializer.is_valid():
        for field, errs in serializer.errors.items():
            warnings.append(f"{field}: {errs}")

    item_parts = [f"{ri['item_name']} {ri['qty']}" for ri in resolved_items]
    if bill_no:
        summary = _(
            "Purchase Invoice from %(supplier)s: %(items)s = ₹%(total)s (Bill #%(bill_no)s)"
        ) % {
            "supplier": supplier.name,
            "items": ", ".join(item_parts),
            "total": preview_total,
            "bill_no": bill_no,
        }
    else:
        summary = _("Purchase Invoice from %(supplier)s: %(items)s = ₹%(total)s") % {
            "supplier": supplier.name,
            "items": ", ".join(item_parts),
            "total": preview_total,
        }

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
            "message": _("You do not have permission to create Payment Entries."),
        }

    pt = str(party_type).capitalize()
    model_label = "selling.Customer" if pt == "Customer" else "buying.Supplier"
    Account = apps.get_model("accounts.Account")

    picked = _pick_one(
        find_party(model_label, party_query, company),
        pt.lower(),
        party_query,
        lambda p: p.name,
    )
    if "record" not in picked:
        return picked
    party = picked["record"]
    co = _company_of(company, party)

    parsed_amount = _q2(amount)
    warnings: List[str] = []
    if parsed_amount <= 0:
        warnings.append(_("Payment amount must be greater than zero."))

    cash_or_bank, usual_found = _cash_or_bank_account(co, mode_of_payment)
    if not usual_found:
        warnings.append(
            _("Please check the cash/bank account: the usual one was not found.")
        )
    accounts = Account.objects.filter(company=co, is_group=False).order_by("id")

    if pt == "Customer":
        party_account = (
            co.default_receivable_account
            or accounts.filter(account_type="Receivable").first()
        )
    else:
        party_account = (
            co.default_payable_account
            or accounts.filter(account_type="Payable").first()
        )

    paid_from = (
        cash_or_bank.pk
        if payment_type == "Pay" and cash_or_bank
        else (party_account.pk if party_account else None)
    )
    paid_to = (
        party_account.pk
        if payment_type == "Pay" and party_account
        else (cash_or_bank.pk if cash_or_bank else None)
    )

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

    ai_filled_keys = [
        "payment_type",
        "party_type",
        "party",
        "paid_amount",
        "received_amount",
        "posting_date",
    ]

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

    serializer = PaymentEntrySerializer(
        data=serializer_payload, context={"request": drf_req}
    )
    if not serializer.is_valid():
        for field, errs in serializer.errors.items():
            warnings.append(f"{field}: {errs}")

    summary = _(
        "Payment Entry (%(payment_type)s) for %(party_type)s %(party)s: ₹%(amount)s"
    ) % {
        "payment_type": payment_type,
        "party_type": pt,
        "party": party.name,
        "amount": parsed_amount,
    }

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


def propose_receipt(
    user,
    customer_query: str,
    amount: Any,
    mode_of_payment: Optional[str] = None,
    posting_date: Optional[str] = None,
    company: Optional[Any] = None,
    message: Optional[AssistantMessage] = None,
) -> Dict[str, Any]:
    """Build a proposal for a receipt: a customer paid money (Payment Entry, Receive).

    The amount is allocated to the customer's open sales invoices, oldest due date
    first (the same rule as the Money to Collect screen). Anything above the open
    bills stays unallocated, and the proposal says so.

    Never saves a PaymentEntry row. Dry-runs PaymentEntrySerializer.is_valid().
    """
    if not user_has_model_permission(user, "accounts.PaymentEntry", "can_create"):
        return {
            "error": "permission_denied",
            "message": _("You do not have permission to create Payment Entries."),
        }

    picked = _pick_one(
        find_party("selling.Customer", customer_query, company),
        "customer",
        customer_query,
        _customer_choice_label,
    )
    if "record" not in picked:
        return picked
    customer = picked["record"]
    co = _company_of(company, customer)

    warnings: List[str] = []
    parsed_amount = _q2(amount)
    if parsed_amount <= 0:
        warnings.append(_("Payment amount must be greater than zero."))

    cash_or_bank, usual_found = _cash_or_bank_account(co, mode_of_payment)
    if not usual_found:
        warnings.append(
            _("Please check the cash/bank account: the usual one was not found.")
        )

    references: List[Dict[str, str]] = []
    if parsed_amount > 0:
        from accounts.services.allocation import suggest_allocation

        allocation = suggest_allocation("Customer", customer, co, parsed_amount)
        references = [
            {
                "reference_doctype": ref["reference_doctype"],
                "reference_name": ref["reference_name"],
                "allocated_amount": str(ref["allocated_amount"]),
            }
            for ref in allocation.references
        ]
        unallocated = _q2(allocation.unallocated)
        if not references:
            warnings.append(
                _("%(customer)s has no open bills; the receipt stays unallocated.")
                % {"customer": customer.name}
            )
        elif unallocated > 0:
            warnings.append(
                _("₹%(amount)s is more than the open bills and stays unallocated.")
                % {"amount": unallocated}
            )

    if co is not None and co.require_proof:
        warnings.append(_("Proof attachment is required before submission."))

    posting = posting_date or str(date.today())
    paid_to = cash_or_bank.pk if cash_or_bank else None
    initial_values = {
        "payment_type": "Receive",
        "party_type": "Customer",
        "party_name": customer.name,
        "company": co.pk if co else None,
        "paid_amount": str(parsed_amount),
        "received_amount": str(parsed_amount),
        "posting_date": posting,
        "paid_to": paid_to,
        "references": references,
    }
    ai_filled_keys = [
        "payment_type",
        "party_type",
        "party_name",
        "paid_amount",
        "received_amount",
        "posting_date",
        "references",
    ]

    # Dry-run Serializer
    from accounts.serializers import PaymentEntrySerializer

    serializer_payload = {
        "payment_type": "Receive",
        "party_type": "Customer",
        "party_name": customer.name,
        "company": co.pk if co else None,
        "paid_amount": str(parsed_amount),
        "received_amount": str(parsed_amount),
        "posting_date": posting,
        "paid_to": paid_to,
        "references": references,
    }

    factory = APIRequestFactory()
    req = factory.post("/api/accounts/payment-entries/")
    req.user = user
    drf_req = Request(req)

    serializer = PaymentEntrySerializer(
        data=serializer_payload, context={"request": drf_req}
    )
    if not serializer.is_valid():
        for field, errs in serializer.errors.items():
            warnings.append(f"{field}: {errs}")

    if references:
        summary = _("Receipt from %(customer)s: ₹%(amount)s against %(bills)s") % {
            "customer": customer.name,
            "amount": parsed_amount,
            "bills": ", ".join(r["reference_name"] for r in references),
        }
    else:
        summary = _("Receipt from %(customer)s: ₹%(amount)s (no open bills)") % {
            "customer": customer.name,
            "amount": parsed_amount,
        }

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


def propose_expense(
    user,
    category_query: str,
    amount: Any,
    supplier_query: Optional[str] = None,
    expense_date: Optional[str] = None,
    bill_no: Optional[str] = None,
    paid_now: Any = False,
    mode_of_payment: Optional[str] = None,
    company: Optional[Any] = None,
    message: Optional[AssistantMessage] = None,
) -> Dict[str, Any]:
    """Build a proposal for an expense bill (electricity, rent, hamali...).

    An expense is a Purchase Invoice with is_expense set, saved by the expense
    endpoint (ExpenseViewSet.create), which takes its own fields rather than the
    invoice serializer. So this checks the rules that endpoint applies (category,
    supplier, amount, date) and reports the submit-time rules (bill number, proof)
    as warnings. Never saves a PurchaseInvoice or PaymentEntry row.
    """
    if not user_has_model_permission(user, "buying.PurchaseInvoice", "can_create"):
        return {
            "error": "permission_denied",
            "message": _("You do not have permission to create Expenses."),
        }

    picked = _pick_one(
        find_party("buying.ExpenseCategory", category_query, company),
        "category",
        category_query,
        lambda c: c.name,
    )
    if "record" not in picked:
        return picked
    category = picked["record"]
    co = _company_of(company, category)

    warnings: List[str] = []
    Supplier = apps.get_model("buying.Supplier")
    if supplier_query and str(supplier_query).strip():
        picked = _pick_one(
            find_party("buying.Supplier", supplier_query, co.pk),
            "supplier",
            supplier_query,
            lambda p: p.name,
        )
        if "record" not in picked:
            return picked
        supplier = picked["record"]
    else:
        supplier = Supplier.objects.filter(
            company=co, is_cash_expense=True, is_active=True
        ).first()
        if supplier is None:
            return {
                "error": "supplier_required",
                "message": _("Supplier is required."),
            }

    parsed_amount = _q2(amount)
    if parsed_amount <= 0:
        warnings.append(_("Expense amount must be greater than zero."))

    expense_day = str(date.today())
    if expense_date:
        try:
            expense_day = str(date.fromisoformat(str(expense_date)))
        except ValueError:
            warnings.append(_("Invalid date format."))

    is_paid_now = paid_now is True or str(paid_now).strip().lower() in (
        "true",
        "1",
        "yes",
    )
    paid_from = None
    if is_paid_now:
        paid_from, usual_found = _cash_or_bank_account(co, mode_of_payment)
        if not usual_found:
            warnings.append(
                _("Please check the cash/bank account: the usual one was not found.")
            )
        if paid_from is None:
            warnings.append(
                _("Paid from account is required when paid now is selected.")
            )

    bill_no_clean = (bill_no or "").strip()
    if not bill_no_clean and not supplier.is_cash_expense:
        warnings.append(
            _("Enter the supplier's invoice number (Bill No) before submitting.")
        )
    if co.require_proof:
        warnings.append(_("Proof attachment is required before submission."))

    initial_values = {
        "category": category.pk,
        "category_name": category.name,
        "supplier": supplier.pk,
        "supplier_name": supplier.name,
        "company": co.pk,
        "amount": str(parsed_amount),
        "date": expense_day,
        "due_date": expense_day,
        "bill_no": bill_no_clean,
        "paid_now": is_paid_now,
        "paid_from": paid_from.pk if paid_from else None,
    }
    ai_filled_keys = ["category", "supplier", "amount", "date", "paid_now"]
    if bill_no_clean:
        ai_filled_keys.append("bill_no")

    if is_paid_now:
        summary = _("Expense %(category)s from %(supplier)s: ₹%(amount)s, paid now") % {
            "category": category.name,
            "supplier": supplier.name,
            "amount": parsed_amount,
        }
    else:
        summary = _(
            "Expense %(category)s from %(supplier)s: ₹%(amount)s, to pay later"
        ) % {
            "category": category.name,
            "supplier": supplier.name,
            "amount": parsed_amount,
        }

    proposal = AssistantProposal.objects.create(
        message=message,
        target="buying.PurchaseInvoice",
        route="/accounts/expenses/new",
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


def propose_batch(
    user,
    item_query: str,
    quantity: Any,
    company: Optional[Any] = None,
    message: Optional[AssistantMessage] = None,
) -> Dict[str, Any]:
    """Build a proposal to start a production batch: grind `quantity` of a bulk item.

    Opens the Batches screen with the product and quantity filled in. The user
    presses Start there. Starting posts the grinding Work Order, so this only reads:
    it checks the rules batches.start_grinding applies (positive quantity, default
    BOM, stage warehouses) and reports them as warnings. Never saves a Batch or a
    WorkOrder row.
    """
    if not user_has_model_permission(user, "manufacturing.WorkOrder", "can_create"):
        return {
            "error": "permission_denied",
            "message": _("You do not have permission to create Production Batches."),
        }

    picked = _pick_one(find_item(item_query), "item", item_query, lambda it: it.name)
    if "record" not in picked:
        return picked
    item = picked["record"]

    Company = apps.get_model("core.Company")
    co = Company.objects.filter(pk=company).first() if company else None
    co = co or Company.objects.first()
    if co is None:
        return {"error": "company_not_found", "message": _("Company not found.")}

    warnings: List[str] = []
    try:
        parsed_qty = Decimal(str(quantity)).quantize(Decimal("0.001"))
    except (InvalidOperation, TypeError, ValueError):
        parsed_qty = Decimal("0.000")
    if not parsed_qty.is_finite() or parsed_qty <= 0:
        parsed_qty = Decimal("0.000")
        warnings.append(_("Quantity must be greater than zero."))

    BOM = apps.get_model("manufacturing.BOM")
    if not BOM.objects.filter(
        company=co, item=item, is_default=True, is_active=True
    ).exists():
        warnings.append(
            _("Item '%(code)s' has no default BOM; set one before starting the batch.")
            % {"code": item.code}
        )
    if not (
        co.unprocessed_warehouse_id and co.in_process_warehouse_id and co.ready_warehouse_id
    ):
        warnings.append(
            _(
                "Set the Unprocessed, In Process and Ready warehouses for the "
                "company first."
            )
        )

    initial_values = {
        "company": co.pk,
        "bulk_item": item.pk,
        "bulk_item_name": item.name,
        "bulk_qty": str(parsed_qty),
    }
    ai_filled_keys = ["bulk_item", "bulk_qty"]
    summary = _("Batch: %(qty)s of %(item)s") % {
        "qty": parsed_qty,
        "item": item.name,
    }

    proposal = AssistantProposal.objects.create(
        message=message,
        target="manufacturing.Batch",
        route="/manufacturing/batches",
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
    "propose_sales_order": propose_sales_order,
    "propose_sales_invoice": propose_sales_invoice,
    "propose_purchase_invoice": propose_purchase_invoice,
    "propose_payment_entry": propose_payment_entry,
    "propose_receipt": propose_receipt,
    "propose_expense": propose_expense,
    "propose_batch": propose_batch,
}
