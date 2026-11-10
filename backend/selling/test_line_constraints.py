"""DB-level rules on sales lines and customer names (S-3, S-8).

Each rule is checked with a direct ORM write and a ``QuerySet.update``: both must
raise ``IntegrityError``, whatever the serializers accept.
"""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from accounts.models import Account, AccountRootType, AccountType
from core.models import Company
from selling.models import (
    Customer,
    CustomerGroup,
    SalesInvoice,
    SalesInvoiceItem,
    SalesOrder,
    SalesOrderItem,
)
from stock.models import UOM, Item, ItemGroup

pytestmark = pytest.mark.django_db


@pytest.fixture
def base():
    company = Company.objects.create(name="Line Co", abbr="LNC", default_currency="INR")
    group = CustomerGroup.objects.create(name="Line CG")
    customer = Customer.objects.create(
        name="Line Customer", company=company, customer_group=group
    )
    uom = UOM.objects.create(name="Nos")
    item_group = ItemGroup.objects.create(name="Line IG", is_group=True)
    item = Item.objects.create(
        code="LINE-1", name="Line item", item_group=item_group, stock_uom=uom
    )
    debtors = Account.objects.create(
        name="Debtors",
        company=company,
        root_type=AccountRootType.ASSET,
        account_type=AccountType.RECEIVABLE,
    )
    income = Account.objects.create(
        name="Income", company=company, root_type=AccountRootType.INCOME
    )
    so = SalesOrder.objects.create(
        name="SO-LINE-1",
        customer=customer,
        company=company,
        transaction_date="2026-04-01",
    )
    si = SalesInvoice.objects.create(
        name="SINV-LINE-1",
        customer=customer,
        company=company,
        posting_date="2026-04-01",
        debit_to=debtors,
        income_account=income,
    )
    return {"company": company, "customer": customer, "item": item, "so": so, "si": si}


def _so_line(b, **kw):
    data = {
        "sales_order": b["so"],
        "item": b["item"],
        "qty": Decimal(1),
        "rate": Decimal(10),
    }
    data.update(kw)
    return SalesOrderItem.objects.create(**data)


def _si_line(b, **kw):
    data = {
        "sales_invoice": b["si"],
        "item": b["item"],
        "qty": Decimal(1),
        "rate": Decimal(10),
    }
    data.update(kw)
    return SalesInvoiceItem.objects.create(**data)


def _refused(fn):
    with pytest.raises(IntegrityError), transaction.atomic():
        fn()


def test_sales_order_line_qty_must_be_positive(base):
    _so_line(base)  # the valid line is accepted
    _refused(lambda: _so_line(base, qty=Decimal(0)))
    _refused(lambda: _so_line(base, qty=Decimal(-1)))
    valid = SalesOrderItem.objects.first()
    _refused(lambda: SalesOrderItem.objects.filter(pk=valid.pk).update(qty=Decimal(0)))


def test_sales_order_line_rate_must_not_be_negative(base):
    _refused(lambda: _so_line(base, rate=Decimal("-0.01")))
    _so_line(base, rate=Decimal(0))  # zero rate is allowed (free sample)
    valid = SalesOrderItem.objects.first()
    _refused(
        lambda: SalesOrderItem.objects.filter(pk=valid.pk).update(rate=Decimal(-5))
    )


def test_sales_invoice_line_qty_must_not_be_zero_but_return_may_be_negative(base):
    _si_line(base)
    _refused(lambda: _si_line(base, qty=Decimal(0)))
    # A return line carries a negative quantity; the rule is "not zero", not "positive".
    _si_line(base, qty=Decimal(-1))
    valid = SalesInvoiceItem.objects.filter(qty__gt=0).first()
    _refused(
        lambda: SalesInvoiceItem.objects.filter(pk=valid.pk).update(qty=Decimal(0))
    )


def test_sales_invoice_line_rate_must_not_be_negative(base):
    _si_line(base)
    _refused(lambda: _si_line(base, rate=Decimal(-1)))
    valid = SalesInvoiceItem.objects.first()
    _refused(
        lambda: SalesInvoiceItem.objects.filter(pk=valid.pk).update(rate=Decimal(-1))
    )


def test_customer_name_is_unique_ignoring_case_per_company(base):
    company, group = base["company"], CustomerGroup.objects.first()
    Customer.objects.create(
        name="Sharma Traders", company=company, customer_group=group
    )
    _refused(
        lambda: Customer.objects.create(
            name="sharma traders", company=company, customer_group=group
        )
    )
    # Same name in another company is fine.
    other = Company.objects.create(name="Other Co", abbr="OTH", default_currency="INR")
    Customer.objects.create(name="sharma traders", company=other, customer_group=group)
