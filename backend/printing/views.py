"""Read-only print-data endpoints: ``GET /api/print/<document>/<id>/``.

Each is guarded by the ``can_read`` flag of the document's own model (RBAC) and is
scoped to the active company (``X-Company-ID``): a document of another company is a
404, as in the document viewsets. Query parameters (all optional):

* ``copy``        ``original`` (default) | ``duplicate`` | ``triplicate``
* ``words_lang``  ``en`` (default) | ``hi`` -- language of the amount in words
* ``with_prices`` delivery challan only: ``1`` includes rates, tax and totals
"""

from django.shortcuts import get_object_or_404
from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import PaymentEntry
from buying.models import PurchaseInvoice, PurchaseOrder
from core.company_context import resolve_active_company
from printing import builders
from selling.models import SalesInvoice, SalesOrder
from users.permissions import HasRolePermission

TRUE = ("1", "true", "yes", "on")


def parse_params(query):
    copy = (query.get("copy") or "original").lower()
    if copy not in builders.COPIES:
        raise ValidationError(
            {"copy": f"Must be one of: {', '.join(builders.COPIES)}."}
        )
    words_lang = (query.get("words_lang") or "en").lower()
    if words_lang not in builders.WORD_LANGS:
        words_lang = "en"  # other languages read the amount in English
    return {
        "copy": copy,
        "words_lang": words_lang,
        "with_prices": (query.get("with_prices") or "").lower() in TRUE,
    }


class PrintView(APIView):
    """One document type's print payload. Subclasses set ``model`` and ``build``."""

    permission_classes = [permissions.IsAuthenticated, HasRolePermission]
    model = None
    select_related = ("company",)

    def build(self, doc, params):  # pragma: no cover - overridden
        raise NotImplementedError

    def get(self, request, pk):
        company = resolve_active_company(request)
        doc = get_object_or_404(
            self.model.objects.select_related(*self.select_related),
            pk=pk,
            company=company,
        )
        return Response(self.build(doc, parse_params(request.query_params)))


class SalesInvoicePrint(PrintView):
    """Tax Invoice / Credit Note of a Sales Invoice."""

    model = SalesInvoice
    select_related = ("company", "customer", "return_against", "place_of_supply")

    def build(self, doc, params):
        return builders.sales_invoice(doc, params)


class PurchaseInvoicePrint(PrintView):
    """Purchase Invoice / Debit Note."""

    model = PurchaseInvoice
    select_related = ("company", "supplier", "return_against", "place_of_supply")

    def build(self, doc, params):
        return builders.purchase_invoice(doc, params)


class PurchaseOrderPrint(PrintView):
    model = PurchaseOrder
    select_related = ("company", "supplier", "place_of_supply")

    def build(self, doc, params):
        return builders.purchase_order(doc, params)


class ChallanFromInvoicePrint(PrintView):
    """Delivery Challan generated from a Sales Invoice (needs read on Sales Invoice)."""

    model = SalesInvoice
    select_related = ("company", "customer", "return_against", "place_of_supply")

    def build(self, doc, params):
        return builders.delivery_challan(doc, params, "sales-invoice")


class ChallanFromOrderPrint(PrintView):
    """Delivery Challan generated from a Sales Order (needs read on Sales Order)."""

    model = SalesOrder
    select_related = ("company", "customer", "place_of_supply")

    def build(self, doc, params):
        return builders.delivery_challan(doc, params, "sales-order")


class PaymentEntryPrint(PrintView):
    """Payment Receipt / Payment Voucher of a Payment Entry."""

    model = PaymentEntry
    select_related = ("company", "paid_from", "paid_to")

    def build(self, doc, params):
        return builders.payment_entry(doc, params)
