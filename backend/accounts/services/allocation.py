from decimal import Decimal
from typing import Any, Union

from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils.translation import gettext as _

from core.models import Company, DocStatus

ZERO = Decimal("0.00")


class AllocationSuggestion:
    def __init__(
        self, references: list, unallocated: Decimal, total_allocated: Decimal
    ):
        self.references = references
        self.unallocated = unallocated
        self.total_allocated = total_allocated

    def __getitem__(self, item):
        if isinstance(item, str):
            if hasattr(self, item):
                return getattr(self, item)
            raise KeyError(item)
        if item == 0:
            return self.references
        elif item == 1:
            return self.unallocated
        elif item == 2:
            return self.total_allocated
        raise IndexError("AllocationSuggestion index out of range")

    def __iter__(self):
        yield self.references
        yield self.unallocated
        yield self.total_allocated

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __repr__(self):
        return (
            f"AllocationSuggestion(references={self.references!r}, "
            f"unallocated={self.unallocated!r}, total_allocated={self.total_allocated!r})"
        )

    def _asdict(self):
        return {
            "references": self.references,
            "unallocated": self.unallocated,
            "total_allocated": self.total_allocated,
        }


def suggest_allocation(
    party_type: str,
    party: Any,
    company: Any,
    amount: Union[Decimal, float, int, str],
) -> AllocationSuggestion:
    """Suggest invoice allocation for a payment amount, oldest due first.

    Allocates against submitted open invoices (SalesInvoice for Customer,
    PurchaseInvoice for Supplier with outstanding_amount > 0).
    Invoices are ordered by oldest due_date first (falling back to posting_date).
    Never allocates above an invoice's outstanding amount.
    Returns allocated references and unallocated remainder amount.
    """
    if isinstance(company, Company):
        comp = company
    elif isinstance(company, (int, str)) and str(company).isdigit():
        comp = Company.objects.get(pk=int(company))
    else:
        comp = Company.objects.get(name=str(company))

    ptype = (party_type or "").strip().lower()
    if ptype == "customer":
        from selling.models import Customer, SalesInvoice

        model = SalesInvoice
        ref_doctype = "selling.SalesInvoice"
        qs = model.objects.filter(
            company=comp,
            docstatus=DocStatus.SUBMITTED,
            outstanding_amount__gt=ZERO,
            is_return=False,
        )
        if isinstance(party, Customer):
            qs = qs.filter(customer=party)
        elif isinstance(party, int) or (
            isinstance(party, str)
            and party.isdigit()
            and Customer.objects.filter(pk=int(party), company=comp).exists()
        ):
            qs = qs.filter(Q(customer__id=int(party)) | Q(customer__name=str(party)))
        else:
            qs = qs.filter(customer__name=str(party))
    elif ptype == "supplier":
        from buying.models import PurchaseInvoice, Supplier

        model = PurchaseInvoice
        ref_doctype = "buying.PurchaseInvoice"
        qs = model.objects.filter(
            company=comp,
            docstatus=DocStatus.SUBMITTED,
            outstanding_amount__gt=ZERO,
            is_return=False,
        )
        if isinstance(party, Supplier):
            qs = qs.filter(supplier=party)
        elif isinstance(party, int) or (
            isinstance(party, str)
            and party.isdigit()
            and Supplier.objects.filter(pk=int(party), company=comp).exists()
        ):
            qs = qs.filter(Q(supplier__id=int(party)) | Q(supplier__name=str(party)))
        else:
            qs = qs.filter(supplier__name=str(party))
    else:
        raise ValueError(
            _("party_type must be 'Customer' or 'Supplier', got '%(ptype)s'")
            % {"ptype": party_type}
        )

    # Order by oldest due_date first, fallback to posting_date, then tie-break by name
    invoices = qs.annotate(
        effective_due_date=Coalesce("due_date", "posting_date")
    ).order_by("effective_due_date", "posting_date", "name")

    target_amount = Decimal(str(amount)) if amount is not None else ZERO
    if target_amount < ZERO:
        target_amount = ZERO

    remaining = target_amount
    references = []

    for inv in invoices:
        if remaining <= ZERO:
            break
        alloc = min(remaining, inv.outstanding_amount)
        if alloc > ZERO:
            references.append(
                {
                    "reference_doctype": ref_doctype,
                    "reference_name": inv.name,
                    "total_amount": inv.rounded_total,
                    "outstanding_amount": inv.outstanding_amount,
                    "allocated_amount": alloc,
                    "due_date": inv.due_date,
                    "posting_date": inv.posting_date,
                }
            )
            remaining -= alloc

    unallocated = remaining
    total_allocated = target_amount - unallocated

    return AllocationSuggestion(
        references=references,
        unallocated=unallocated,
        total_allocated=total_allocated,
    )
