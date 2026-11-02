import datetime
from typing import Any, Optional, Union


def calculate_due_date(
    party_type: Optional[str],
    party: Any,
    posting_date: Union[datetime.date, datetime.datetime, str],
) -> datetime.date:
    """Calculate bill due date from party payment terms and posting date.

    Credit days resolution hierarchy:
    1. Party explicit override (Customer.credit_days or Supplier.credit_days) if not None.
    2. Company default:
       - Customer: Company.default_customer_credit_days (defaults to 90 if unset)
       - Supplier: Company.default_supplier_credit_days (defaults to 0/None if unset)
    3. If credit days resolves to 0 or None, the due date equals the posting date.
    """
    if isinstance(posting_date, datetime.datetime):
        base_date = posting_date.date()
    elif isinstance(posting_date, datetime.date):
        base_date = posting_date
    elif isinstance(posting_date, str):
        base_date = datetime.date.fromisoformat(posting_date)
    else:
        raise TypeError(
            f"Expected date, datetime, or ISO string, got {type(posting_date).__name__}"
        )

    ptype = (party_type or "").strip().lower()
    if not ptype and party is not None:
        ptype = party.__class__.__name__.lower()

    if ptype not in ("customer", "supplier"):
        raise ValueError(
            f"party_type must be 'Customer' or 'Supplier', got: {party_type!r}"
        )

    days: Optional[int] = None
    if party is not None and getattr(party, "credit_days", None) is not None:
        days = party.credit_days
    else:
        company = getattr(party, "company", None)
        if ptype == "customer":
            if (
                company is not None
                and getattr(company, "default_customer_credit_days", None) is not None
            ):
                days = company.default_customer_credit_days
            else:
                days = 90
        elif ptype == "supplier":
            if (
                company is not None
                and getattr(company, "default_supplier_credit_days", None) is not None
            ):
                days = company.default_supplier_credit_days
            else:
                days = 0

    if not days:  # None or 0
        return base_date

    return base_date + datetime.timedelta(days=int(days))


def due_date_for(
    party: Any,
    base_date: Union[datetime.date, datetime.datetime, str],
    party_type: Optional[str] = None,
) -> datetime.date:
    """Convenience alias for calculate_due_date matching planning/04-collections-and-payables.md."""
    return calculate_due_date(party_type, party, base_date)


def _as_date(value: Any) -> Optional[datetime.date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))


def validate_due_date(serializer: Any, attrs: dict) -> None:
    """Reject a due date before the posting (or supplier bill) date with a 400.

    The DB CHECK ``*_due_date_gte_posting`` is the real guarantee; this turns
    the violation into a field error the user can read instead of a 500.
    """
    from django.utils.translation import gettext as _
    from rest_framework import serializers

    instance = getattr(serializer, "instance", None)

    def current(field: str) -> Any:
        return attrs.get(field, getattr(instance, field, None))

    due = _as_date(current("due_date"))
    if due is None:
        return
    posting = _as_date(current("posting_date"))
    if posting and due < posting:
        raise serializers.ValidationError(
            {"due_date": _("Due date cannot be before posting date.")}
        )
    bill = _as_date(current("bill_date")) if "bill_date" in serializer.fields else None
    if bill and due < bill:
        raise serializers.ValidationError(
            {"due_date": _("Due date cannot be before the bill date.")}
        )
