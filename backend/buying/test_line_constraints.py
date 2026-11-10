"""DB-level rules on purchase lines and supplier names (S-3, S-8).

Each rule is checked with a direct ORM write and a ``QuerySet.update``: both must
raise ``IntegrityError``, whatever the serializers accept.
"""

import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from accounts.models import Account, AccountRootType, AccountType
from buying.models import (
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
    SupplierGroup,
)
from core.models import Company
from stock.models import UOM, Item, ItemGroup

pytestmark = pytest.mark.django_db


@pytest.fixture
def base():
    company = Company.objects.create(name="Buy Co", abbr="BYC", default_currency="INR")
    group = SupplierGroup.objects.create(name="Buy SG")
    supplier = Supplier.objects.create(
        name="Buy Supplier", company=company, supplier_group=group
    )
    uom = UOM.objects.create(name="Nos")
    item_group = ItemGroup.objects.create(name="Buy IG", is_group=True)
    item = Item.objects.create(
        code="BUY-1", name="Buy item", item_group=item_group, stock_uom=uom
    )
    creditors = Account.objects.create(
        name="Creditors",
        company=company,
        root_type=AccountRootType.LIABILITY,
        account_type=AccountType.PAYABLE,
    )
    expense = Account.objects.create(
        name="Purchases", company=company, root_type=AccountRootType.EXPENSE
    )
    po = PurchaseOrder.objects.create(
        name="PO-LINE-1",
        supplier=supplier,
        company=company,
        transaction_date=datetime.date(2026, 4, 1),
    )
    pi = PurchaseInvoice.objects.create(
        name="PINV-LINE-1",
        supplier=supplier,
        company=company,
        posting_date=datetime.date(2026, 4, 1),
        credit_to=creditors,
        expense_account=expense,
    )
    return {
        "company": company,
        "supplier": supplier,
        "group": group,
        "item": item,
        "po": po,
        "pi": pi,
    }


def _po_line(b, **kw):
    data = {
        "purchase_order": b["po"],
        "item": b["item"],
        "qty": Decimal(1),
        "rate": Decimal(10),
    }
    data.update(kw)
    return PurchaseOrderItem.objects.create(**data)


def _pi_line(b, **kw):
    data = {
        "purchase_invoice": b["pi"],
        "item": b["item"],
        "qty": Decimal(1),
        "rate": Decimal(10),
    }
    data.update(kw)
    return PurchaseInvoiceItem.objects.create(**data)


def _refused(fn):
    with pytest.raises(IntegrityError), transaction.atomic():
        fn()


def test_purchase_order_line_qty_must_be_positive(base):
    _po_line(base)
    _refused(lambda: _po_line(base, qty=Decimal(0)))
    _refused(lambda: _po_line(base, qty=Decimal(-2)))
    valid = PurchaseOrderItem.objects.first()
    _refused(
        lambda: PurchaseOrderItem.objects.filter(pk=valid.pk).update(qty=Decimal(0))
    )


def test_purchase_order_line_rate_must_not_be_negative(base):
    _refused(lambda: _po_line(base, rate=Decimal("-0.01")))
    _po_line(base, rate=Decimal(0))
    valid = PurchaseOrderItem.objects.first()
    _refused(
        lambda: PurchaseOrderItem.objects.filter(pk=valid.pk).update(rate=Decimal(-1))
    )


def test_purchase_invoice_line_qty_must_not_be_zero_but_return_may_be_negative(base):
    _pi_line(base)
    _refused(lambda: _pi_line(base, qty=Decimal(0)))
    _pi_line(base, qty=Decimal(-1))
    valid = PurchaseInvoiceItem.objects.filter(qty__gt=0).first()
    _refused(
        lambda: PurchaseInvoiceItem.objects.filter(pk=valid.pk).update(qty=Decimal(0))
    )


def test_purchase_invoice_line_rate_must_not_be_negative(base):
    _pi_line(base)
    _refused(lambda: _pi_line(base, rate=Decimal(-1)))
    valid = PurchaseInvoiceItem.objects.first()
    _refused(
        lambda: PurchaseInvoiceItem.objects.filter(pk=valid.pk).update(rate=Decimal(-1))
    )


def test_supplier_name_is_unique_ignoring_case_per_company(base):
    company, group = base["company"], base["group"]
    Supplier.objects.create(name="Gupta Metals", company=company, supplier_group=group)
    _refused(
        lambda: Supplier.objects.create(
            name="GUPTA METALS", company=company, supplier_group=group
        )
    )
    other = Company.objects.create(name="Other Buy", abbr="OBY", default_currency="INR")
    Supplier.objects.create(name="GUPTA METALS", company=other, supplier_group=group)
