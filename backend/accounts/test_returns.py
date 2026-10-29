"""Sales Return (credit note) / Purchase Return (debit note): ERPNext ``is_return``.

ORM-level tests on a perpetual-inventory company (stock, valuation, batches, GL,
outstanding, cancel, integrity). The GST / API side is in ``gst/tests/test_returns_gst.py``.
"""

from buying.testing import next_bill_no
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    GLEntry,
    InvoiceStatus,
    PaymentEntry,
    PaymentEntryReference,
    PaymentType,
)
from accounts.services import outstanding as outstanding_service
from buying import mapping as buying_mapping
from buying.models import PurchaseInvoice, PurchaseInvoiceItem, Supplier, SupplierGroup
from core.models import Company, DocStatus
from selling import mapping as selling_mapping
from selling.models import Customer, CustomerGroup, SalesInvoice, SalesInvoiceItem
from stock.models import (
    UOM,
    Batch,
    Bin,
    Item,
    ItemGroup,
    StockLedgerEntry,
    Warehouse,
)

D = Decimal
DATE = datetime.date(2026, 4, 1)


def _acc(company, name, root, parent=None, account_type=None, is_group=False):
    return Account.objects.create(
        name=name,
        company=company,
        parent=parent,
        root_type=root,
        account_type=account_type,
        is_group=is_group,
    )


class ReturnBase(TestCase):
    def setUp(self):
        c = self.company = Company.objects.create(name="Nidhi Spice", abbr="NS")
        assets = _acc(c, "Assets", AccountRootType.ASSET, is_group=True)
        expenses = _acc(c, "Expenses", AccountRootType.EXPENSE, is_group=True)
        income = _acc(c, "Income", AccountRootType.INCOME, is_group=True)
        liab = _acc(c, "Liabilities", AccountRootType.LIABILITY, is_group=True)
        self.inventory = _acc(
            c, "Stock In Hand", AccountRootType.ASSET, assets, AccountType.STOCK
        )
        self.debtors = _acc(
            c, "Debtors", AccountRootType.ASSET, assets, AccountType.RECEIVABLE
        )
        self.bank = _acc(c, "Bank", AccountRootType.ASSET, assets, AccountType.BANK)
        self.cogs = _acc(
            c,
            "COGS",
            AccountRootType.EXPENSE,
            expenses,
            AccountType.COST_OF_GOODS_SOLD,
        )
        self.adjustment = _acc(c, "Stock Adjustment", AccountRootType.EXPENSE, expenses)
        self.purchases = _acc(c, "Purchases", AccountRootType.EXPENSE, expenses)
        self.sales = _acc(c, "Sales", AccountRootType.INCOME, income)
        self.creditors = _acc(
            c, "Creditors", AccountRootType.LIABILITY, liab, AccountType.PAYABLE
        )
        c.enable_perpetual_inventory = True
        c.default_inventory_account = self.inventory
        c.default_expense_account = self.cogs
        c.stock_adjustment_account = self.adjustment
        c.save()

        uom = UOM.objects.create(name="Kg")
        grp = ItemGroup.objects.create(name="Spices", is_group=False)
        self.chilli = Item.objects.create(
            code="CHILLI", name="Chilli Powder", item_group=grp, stock_uom=uom
        )
        self.haldi = Item.objects.create(
            code="HALDI",
            name="Turmeric",
            item_group=grp,
            stock_uom=uom,
            has_batch_no=True,
            has_expiry_date=True,
            shelf_life_in_days=365,
        )
        self.wh = Warehouse.objects.create(name="Store", company=c)
        self.supplier = Supplier.objects.create(
            name="Supp",
            company=c,
            supplier_group=SupplierGroup.objects.create(name="SG"),
        )
        self.customer = Customer.objects.create(
            name="Cust",
            company=c,
            customer_group=CustomerGroup.objects.create(name="CG"),
        )
        self._n = 0

    def name(self, prefix):
        self._n += 1
        return f"{prefix}-{self._n:04d}"

    def purchase(self, lines, submit=True, update_stock=True, date=DATE):
        pi = PurchaseInvoice.objects.create(
            bill_no=next_bill_no(),
            name=self.name("PINV"),
            supplier=self.supplier,
            company=self.company,
            posting_date=date,
            update_stock=update_stock,
            credit_to=self.creditors,
            expense_account=self.purchases,
        )
        for item, qty, rate, *rest in lines:
            PurchaseInvoiceItem.objects.create(
                purchase_invoice=pi,
                item=item,
                warehouse=self.wh,
                qty=D(qty),
                rate=D(rate),
                **(rest[0] if rest else {}),
            )
        if submit:
            pi.submit()
        return pi

    def sell(self, lines, submit=True, update_stock=True, date=DATE):
        si = SalesInvoice.objects.create(
            name=self.name("SINV"),
            customer=self.customer,
            company=self.company,
            posting_date=date,
            update_stock=update_stock,
            debit_to=self.debtors,
            income_account=self.sales,
        )
        for item, qty, rate, *rest in lines:
            SalesInvoiceItem.objects.create(
                sales_invoice=si,
                item=item,
                warehouse=self.wh,
                qty=D(qty),
                rate=D(rate),
                **(rest[0] if rest else {}),
            )
        if submit:
            si.submit()
        return si

    def gl(self, doc):
        return sorted(
            (e.account.name, e.debit, e.credit)
            for e in GLEntry.objects.filter(voucher_no=doc.name, is_cancelled=False)
        )

    def fresh(self, doc):
        return type(doc).objects.get(pk=doc.pk)

    def integrity_ok(self):
        from io import StringIO

        from django.core.management import call_command

        call_command("verify_integrity", stdout=StringIO(), stderr=StringIO())

    def stock_in(self, item, qty, rate):
        """Stock via a purchase of ``item``."""
        return self.purchase([(item, qty, rate)])


class SalesReturnTests(ReturnBase):
    def setUp(self):
        super().setUp()
        self.stock_in(self.chilli, 10, 40)

    def make_return(self, si, submit=True, **overrides):
        ret = selling_mapping.make_sales_return(si)
        for key, value in overrides.items():
            setattr(ret, key, value)
        if overrides:
            ret.save()
        if submit:
            ret.submit()
            ret = self.fresh(ret)
        return ret

    def test_full_return_mirrors_the_sale(self):
        si = self.sell([(self.chilli, 4, 100)])
        self.assertEqual(self.fresh(si).outstanding_amount, D("400.00"))
        ret = self.make_return(si)
        # ERPNext sign convention: negative lines and totals.
        self.assertEqual(ret.items.get().qty, D("-4.000"))
        self.assertEqual(ret.items.get().amount, D("-400.00"))
        self.assertEqual(
            (ret.net_total, ret.grand_total, ret.rounded_total), (D("-400.00"),) * 3
        )
        self.assertTrue(ret.is_return)
        self.assertEqual(ret.return_against_id, si.pk)
        self.assertTrue(ret.name.startswith("SRET-"))
        # GL: Cr Debtors, Dr Sales, Dr Stock In Hand, Cr COGS (all positive amounts).
        self.assertEqual(
            self.gl(ret),
            sorted(
                [
                    ("Debtors", D("0.00"), D("400.00")),
                    ("Sales", D("400.00"), D("0.00")),
                    ("Stock In Hand", D("160.00"), D("0.00")),
                    ("COGS", D("0.00"), D("160.00")),
                ]
            ),
        )
        # SLE: in, at the ORIGINAL outgoing valuation rate (40), not the sale rate.
        sle = StockLedgerEntry.objects.get(voucher_no=ret.name, is_cancelled=False)
        self.assertEqual(
            (sle.qty, sle.valuation_rate, sle.stock_value_difference),
            (D("4.000"), D("40.000000"), D("160.00")),
        )
        self.assertEqual(Bin.objects.get(item=self.chilli).actual_qty, D("10.000"))
        # Own outstanding: negative, status Return; the original is untouched.
        self.assertEqual(ret.outstanding_amount, D("-400.00"))
        self.assertEqual(ret.status, InvoiceStatus.RETURN)
        self.assertEqual(self.fresh(si).outstanding_amount, D("400.00"))
        self.assertEqual(
            outstanding_service.party_balance("Customer", "Cust", self.company.pk),
            D("0.00"),
        )
        self.integrity_ok()

    def test_return_valued_at_the_original_rate_not_the_current_average(self):
        si = self.sell([(self.chilli, 4, 100)])  # sold out at avg 40
        self.stock_in(self.chilli, 10, 100)  # average moves to ~ 73.3
        ret = self.make_return(si)
        sle = StockLedgerEntry.objects.get(voucher_no=ret.name, is_cancelled=False)
        self.assertEqual(sle.valuation_rate, D("40.000000"))
        self.assertEqual(sle.stock_value_difference, D("160.00"))
        self.integrity_ok()

    def test_partial_returns_and_over_return_rejected(self):
        si = self.sell([(self.chilli, 4, 100)])
        first = selling_mapping.make_sales_return(si)
        line = first.items.get()
        line.qty = D("-3")
        line.save()
        first.calculate_taxes_and_totals(resolve_lines=False)
        first.submit()
        self.assertEqual(first.net_total, D("-300.00"))
        # Only 1 left: the mapping offers exactly that.
        second = selling_mapping.make_sales_return(si)
        self.assertEqual(second.items.get().qty, D("-1.000"))
        # An over-return is refused at submit (cumulative per original line).
        line = second.items.get()
        line.qty = D("-2")
        line.save()
        second.calculate_taxes_and_totals(resolve_lines=False)
        with self.assertRaisesMessage(ValidationError, "Cannot return more than 1"):
            second.submit()
        self.assertEqual(self.fresh(second).docstatus, DocStatus.DRAFT)
        line.qty = D("-1")
        line.save()
        second.calculate_taxes_and_totals(resolve_lines=False)
        second.submit()
        # Fully returned now.
        with self.assertRaisesMessage(ValidationError, "already been returned in full"):
            selling_mapping.make_sales_return(si)
        self.integrity_ok()

    def test_item_not_on_original_rejected(self):
        si = self.sell([(self.chilli, 4, 100)])
        self.stock_in(self.haldi_plain(), 5, 10)
        ret = selling_mapping.make_sales_return(si)
        SalesInvoiceItem.objects.create(
            sales_invoice=ret,
            item=self.other,
            warehouse=self.wh,
            qty=D("-1"),
            rate=D("10"),
        )
        with self.assertRaisesMessage(ValidationError, "does not exist in"):
            ret.submit()

    def haldi_plain(self):
        self.other = Item.objects.create(
            code="OTHER",
            name="Other",
            item_group=ItemGroup.objects.get(name="Spices"),
            stock_uom=UOM.objects.get(name="Kg"),
        )
        return self.other

    def test_party_company_and_positive_qty_checks(self):
        si = self.sell([(self.chilli, 4, 100)])
        ret = selling_mapping.make_sales_return(si)
        other = Customer.objects.create(
            name="Other",
            company=self.company,
            customer_group=self.customer.customer_group,
        )
        ret.customer = other
        ret.save()
        with self.assertRaisesMessage(ValidationError, "party does not match"):
            ret.submit()
        # A draft original cannot be returned.
        draft = self.sell([(self.chilli, 1, 100)], submit=False)
        with self.assertRaises(ValidationError):
            selling_mapping.make_sales_return(draft)

    def test_update_outstanding_on_the_original(self):
        si = self.sell([(self.chilli, 4, 100)])
        ret = self.make_return(si, update_outstanding_for_self=False)
        self.assertFalse(ret.update_outstanding_for_self)
        # The credit note reduces the invoice instead of carrying its own balance.
        self.assertEqual(ret.outstanding_amount, D("0.00"))
        self.assertEqual(self.fresh(si).outstanding_amount, D("0.00"))
        self.assertEqual(self.fresh(si).status, InvoiceStatus.CREDIT_NOTE_ISSUED)
        party = GLEntry.objects.get(
            voucher_no=ret.name, account=self.debtors, is_cancelled=False
        )
        self.assertEqual(
            (party.against_voucher_type, party.against_voucher_no),
            ("selling.SalesInvoice", si.name),
        )
        self.assertEqual(
            outstanding_service.party_balance("Customer", "Cust", self.company.pk),
            D("0.00"),
        )
        self.integrity_ok()

    def test_partial_credit_against_the_original(self):
        si = self.sell([(self.chilli, 4, 100)])
        ret = selling_mapping.make_sales_return(si)
        line = ret.items.get()
        line.qty = D("-1")
        line.save()
        ret.calculate_taxes_and_totals(resolve_lines=False)
        ret.update_outstanding_for_self = False
        ret.save()
        ret.submit()
        self.assertEqual(self.fresh(si).outstanding_amount, D("300.00"))
        self.assertEqual(self.fresh(si).status, InvoiceStatus.PARTLY_PAID)
        self.integrity_ok()

    def test_return_larger_than_open_outstanding_flips_to_self(self):
        si = self.sell([(self.chilli, 4, 100)])
        pay = PaymentEntry.objects.create(
            name="PE-1",
            payment_type=PaymentType.RECEIVE,
            posting_date=DATE,
            company=self.company,
            party_type="Customer",
            party_name="Cust",
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=D("300.00"),
            received_amount=D("300.00"),
        )
        PaymentEntryReference.objects.create(
            payment_entry=pay,
            reference_doctype="selling.SalesInvoice",
            reference_name=si.name,
            allocated_amount=D("300.00"),
        )
        pay.submit()
        self.assertEqual(self.fresh(si).outstanding_amount, D("100.00"))
        ret = self.make_return(si, submit=False, update_outstanding_for_self=False)
        ret.submit()
        ret = self.fresh(ret)
        self.assertTrue(ret.update_outstanding_for_self)  # flipped, as ERPNext does
        self.assertEqual(ret.outstanding_amount, D("-400.00"))
        self.assertEqual(self.fresh(si).outstanding_amount, D("100.00"))
        self.integrity_ok()

    def test_refund_payment_settles_the_credit_note(self):
        si = self.sell([(self.chilli, 4, 100)])
        pay = PaymentEntry.objects.create(
            name="PE-1",
            payment_type=PaymentType.RECEIVE,
            posting_date=DATE,
            company=self.company,
            party_type="Customer",
            party_name="Cust",
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=D("400.00"),
            received_amount=D("400.00"),
        )
        PaymentEntryReference.objects.create(
            payment_entry=pay,
            reference_doctype="selling.SalesInvoice",
            reference_name=si.name,
            allocated_amount=D("400.00"),
        )
        pay.submit()
        ret = self.make_return(si)
        self.assertEqual(ret.outstanding_amount, D("-400.00"))
        # The mapping builds the opposite-flow payment (a refund).
        refund = selling_mapping.make_payment_entry(ret)
        self.assertEqual(refund.payment_type, PaymentType.PAY)
        self.assertEqual(refund.paid_amount, D("400.00"))
        refund.submit()
        ret = self.fresh(ret)
        self.assertEqual(ret.outstanding_amount, D("0.00"))
        self.assertEqual(ret.status, InvoiceStatus.RETURN)
        self.assertEqual(
            outstanding_service.party_balance("Customer", "Cust", self.company.pk),
            D("0.00"),
        )
        # An invoice payment cannot be allocated to a credit note.
        bad = PaymentEntry.objects.create(
            name="PE-2",
            payment_type=PaymentType.RECEIVE,
            posting_date=DATE,
            company=self.company,
            party_type="Customer",
            party_name="Cust",
            paid_from=self.debtors,
            paid_to=self.bank,
            paid_amount=D("10.00"),
            received_amount=D("10.00"),
        )
        PaymentEntryReference.objects.create(
            payment_entry=bad,
            reference_doctype="selling.SalesInvoice",
            reference_name=ret.name,
            allocated_amount=D("10.00"),
        )
        with self.assertRaises(ValidationError):
            bad.submit()
        # Cancelling a return that has a refund is blocked like an invoice.
        with self.assertRaises(ValidationError):
            ret.cancel()
        self.integrity_ok()

    def test_ageing_shows_the_credit_note_as_a_negative_row(self):
        si = self.sell([(self.chilli, 4, 100)])
        self.sell([(self.chilli, 1, 100)])
        ret = self.make_return(si)
        report = outstanding_service.ageing_report(
            "Customer", as_on=datetime.date.today(), company_id=self.company.pk
        )
        rows = {r["invoice"]: r for r in report["rows"]}
        self.assertEqual(rows[ret.name]["outstanding"], "-400.00")
        self.assertTrue(rows[ret.name]["is_return"])
        self.assertEqual(rows[ret.name]["return_against"], si.name)
        self.assertEqual(report["total"]["outstanding"], str(D("100.00")))
        self.assertEqual(
            outstanding_service.party_balance("Customer", "Cust", self.company.pk),
            D("100.00"),
        )

    def test_cancel_return_reverses_gl_and_stock_and_restores_outstanding(self):
        si = self.sell([(self.chilli, 4, 100)])
        ret = self.make_return(si, update_outstanding_for_self=False)
        self.assertEqual(self.fresh(si).outstanding_amount, D("0.00"))
        ret.cancel()
        ret = self.fresh(ret)
        self.assertEqual(ret.docstatus, DocStatus.CANCELLED)
        self.assertEqual(ret.status, InvoiceStatus.CANCELLED)
        self.assertEqual(self.fresh(si).outstanding_amount, D("400.00"))
        self.assertEqual(self.fresh(si).status, InvoiceStatus.UNPAID)
        self.assertFalse(
            GLEntry.objects.filter(voucher_no=ret.name, is_cancelled=False).exists()
        )
        self.assertEqual(Bin.objects.get(item=self.chilli).actual_qty, D("6.000"))
        # The quantity is returnable again.
        again = selling_mapping.make_sales_return(si)
        self.assertEqual(again.items.get().qty, D("-4.000"))
        self.integrity_ok()

    def test_original_cannot_be_cancelled_while_a_return_is_submitted(self):
        si = self.sell([(self.chilli, 4, 100)])
        ret = self.make_return(si)
        with self.assertRaisesMessage(ValidationError, "cancel the linked returns"):
            si.cancel()
        ret.cancel()
        si.cancel()
        self.assertEqual(self.fresh(si).docstatus, DocStatus.CANCELLED)
        self.integrity_ok()

    def test_standalone_return_uses_the_bin_rate(self):
        ret = SalesInvoice.objects.create(
            name=self.name("SINV-RET"),
            customer=self.customer,
            company=self.company,
            posting_date=DATE,
            update_stock=True,
            debit_to=self.debtors,
            income_account=self.sales,
            is_return=True,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=ret,
            item=self.chilli,
            warehouse=self.wh,
            qty=D("-2"),
            rate=D("90"),
        )
        ret.submit()
        sle = StockLedgerEntry.objects.get(voucher_no=ret.name, is_cancelled=False)
        self.assertEqual((sle.qty, sle.valuation_rate), (D("2.000"), D("40.000000")))
        self.assertEqual(self.fresh(ret).outstanding_amount, D("-180.00"))
        self.integrity_ok()

    def test_return_without_stock_update(self):
        si = self.sell([(self.chilli, 4, 100)], update_stock=False)
        ret = self.make_return(si)
        self.assertFalse(StockLedgerEntry.objects.filter(voucher_no=ret.name).exists())
        self.assertEqual(
            self.gl(ret),
            sorted(
                [("Debtors", D("0.00"), D("400.00")), ("Sales", D("400.00"), D("0.00"))]
            ),
        )
        # Stock cannot come back through a return of an invoice that never took it out.
        bad = selling_mapping.make_sales_return(
            self.sell([(self.chilli, 1, 100)], update_stock=False)
        )
        bad.update_stock = True
        bad.save()
        with self.assertRaisesMessage(ValidationError, "Update Stock"):
            bad.submit()

    def test_rate_above_the_original_rate_rejected(self):
        si = self.sell([(self.chilli, 4, 100)])
        ret = selling_mapping.make_sales_return(si)
        line = ret.items.get()
        line.rate = D("120")
        line.save()
        with self.assertRaisesMessage(ValidationError, "rate cannot be greater"):
            ret.submit()

    def test_credit_limit_is_not_enforced_on_a_credit_note(self):
        si = self.sell([(self.chilli, 4, 100)])
        Customer.objects.filter(pk=self.customer.pk).update(credit_limit=D("100"))
        ret = self.make_return(si)
        self.assertEqual(ret.docstatus, DocStatus.SUBMITTED)


class DatabaseConstraintTests(ReturnBase):
    def invoice(self, **kw):
        return SalesInvoice(
            name=self.name("SINV"),
            customer=self.customer,
            company=self.company,
            posting_date=DATE,
            debit_to=self.debtors,
            income_account=self.sales,
            update_stock=False,
            **kw,
        )

    def bad(self, **kw):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.invoice(**kw).save()

    def test_a_normal_invoice_cannot_go_negative(self):
        self.bad(net_total=D("-1"), grand_total=D("-1"), rounded_total=D("-1"))

    def test_a_return_cannot_be_positive(self):
        self.bad(
            is_return=True, net_total=D("1"), grand_total=D("1"), rounded_total=D("1")
        )

    def test_outstanding_sign_follows_is_return(self):
        self.bad(outstanding_amount=D("-5"))
        self.bad(is_return=True, outstanding_amount=D("5"))
        self.invoice(is_return=True, outstanding_amount=D("-5")).save()

    def test_return_against_needs_is_return_and_cannot_be_self(self):
        original = self.invoice()
        original.save()
        self.bad(return_against=original)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesInvoice.objects.filter(pk=original.pk).update(
                is_return=True, return_against=original.pk
            )

    def test_original_with_returns_is_protected_from_delete(self):
        original = self.invoice()
        original.save()
        self.invoice(is_return=True, return_against=original).save()
        with self.assertRaises(Exception):
            SalesInvoice.objects.filter(pk=original.pk).delete()


class BatchReturnTests(ReturnBase):
    def test_sales_return_must_use_the_original_batch(self):
        self.purchase(
            [
                (
                    self.haldi,
                    20,
                    30,
                    {
                        "supplier_batch_no": "S1",
                        "expiry_date": datetime.date(2027, 4, 1),
                    },
                )
            ]
        )
        b1 = Batch.objects.get(item=self.haldi)
        self.purchase(
            [
                (
                    self.haldi,
                    5,
                    30,
                    {
                        "supplier_batch_no": "S2",
                        "expiry_date": datetime.date(2027, 5, 1),
                    },
                )
            ]
        )
        b2 = Batch.objects.exclude(pk=b1.pk).get(item=self.haldi)
        si = self.sell([(self.haldi, 6, 80, {"batch": b1})])
        ret = selling_mapping.make_sales_return(si)
        self.assertEqual(ret.items.get().batch_id, b1.pk)
        line = ret.items.get()
        line.batch = b2
        line.save()
        with self.assertRaisesMessage(ValidationError, "batch of"):
            ret.submit()
        line.batch = b1
        line.save()
        ret.submit()
        sle = StockLedgerEntry.objects.get(voucher_no=ret.name, is_cancelled=False)
        self.assertEqual(
            (sle.batch_id, sle.qty, sle.valuation_rate),
            (b1.pk, D("6.000"), D("30.000000")),
        )
        self.integrity_ok()

    def test_expired_goods_can_be_returned_by_the_customer(self):
        self.purchase(
            [
                (
                    self.haldi,
                    10,
                    30,
                    {
                        "supplier_batch_no": "S1",
                        "expiry_date": datetime.date(2026, 6, 1),
                    },
                )
            ]
        )
        b1 = Batch.objects.get(item=self.haldi)
        si = self.sell([(self.haldi, 4, 80, {"batch": b1})])
        # Returned after the best-before date (expired packs are the point).
        ret = selling_mapping.make_sales_return(si)
        ret.posting_date = datetime.date(2026, 9, 1)
        ret.save()
        ret.submit()
        self.assertEqual(self.fresh(ret).docstatus, DocStatus.SUBMITTED)
        self.integrity_ok()


class PurchaseReturnTests(ReturnBase):
    def test_debit_note_mirrors_the_purchase(self):
        pi = self.purchase([(self.chilli, 10, 50)])
        ret = buying_mapping.make_purchase_return(pi)
        self.assertEqual(ret.items.get().qty, D("-10.000"))
        line = ret.items.get()
        line.qty = D("-4")
        line.save()
        ret.calculate_taxes_and_totals(resolve_lines=False)
        ret.submit()
        ret = self.fresh(ret)
        self.assertTrue(ret.name.startswith("PRET-"))
        self.assertEqual(ret.grand_total, D("-200.00"))
        self.assertEqual(
            self.gl(ret),
            sorted(
                [
                    ("Creditors", D("200.00"), D("0.00")),
                    ("Stock In Hand", D("0.00"), D("200.00")),
                ]
            ),
        )
        sle = StockLedgerEntry.objects.get(voucher_no=ret.name, is_cancelled=False)
        self.assertEqual(
            (sle.qty, sle.valuation_rate, sle.stock_value_difference),
            (D("-4.000"), D("50.000000"), D("-200.00")),
        )
        self.assertEqual(Bin.objects.get(item=self.chilli).actual_qty, D("6.000"))
        # Own outstanding (a debit balance), status Return.
        self.assertEqual(
            (ret.outstanding_amount, ret.status), (D("-200.00"), InvoiceStatus.RETURN)
        )
        self.assertEqual(self.fresh(pi).outstanding_amount, D("500.00"))
        self.assertEqual(
            outstanding_service.party_balance("Supplier", "Supp", self.company.pk),
            D("300.00"),
        )
        self.integrity_ok()

    def test_outstanding_on_the_original_cancel_and_over_return(self):
        pi = self.purchase([(self.chilli, 10, 50)])
        ret = buying_mapping.make_purchase_return(pi)
        ret.update_outstanding_for_self = False
        ret.save()
        ret.submit()
        self.assertEqual(self.fresh(pi).outstanding_amount, D("0.00"))
        self.assertEqual(self.fresh(pi).status, InvoiceStatus.DEBIT_NOTE_ISSUED)
        with self.assertRaisesMessage(ValidationError, "cancel the linked returns"):
            pi.cancel()
        pi = self.fresh(pi)  # the failed cancel left the in-memory object cancelled
        with self.assertRaisesMessage(ValidationError, "already been returned in full"):
            buying_mapping.make_purchase_return(pi)
        ret.cancel()
        self.assertEqual(self.fresh(pi).outstanding_amount, D("500.00"))
        self.assertEqual(Bin.objects.get(item=self.chilli).actual_qty, D("10.000"))
        self.integrity_ok()

    def test_purchase_return_takes_stock_out_at_the_current_average(self):
        pi = self.purchase([(self.chilli, 10, 50)])
        self.purchase([(self.chilli, 10, 70)])  # average 60
        ret = buying_mapping.make_purchase_return(pi)
        ret.submit()
        sle = StockLedgerEntry.objects.get(voucher_no=ret.name, is_cancelled=False)
        self.assertEqual(
            (sle.qty, sle.valuation_rate, sle.stock_value_difference),
            (D("-10.000"), D("60.000000"), D("-600.00")),
        )
        ret = self.fresh(ret)
        # Credit note value 500 (the invoice rate); the 100 valuation difference lands
        # on the expense account so GL inventory still equals the stock value.
        self.assertEqual(
            self.gl(ret),
            sorted(
                [
                    ("Creditors", D("500.00"), D("0.00")),
                    ("Stock In Hand", D("0.00"), D("600.00")),
                    ("Purchases", D("100.00"), D("0.00")),
                ]
            ),
        )
        self.integrity_ok()

    def test_batch_purchase_return_uses_the_purchase_batch_even_if_expired(self):
        pi = self.purchase(
            [
                (
                    self.haldi,
                    10,
                    30,
                    {
                        "supplier_batch_no": "S1",
                        "expiry_date": datetime.date(2026, 6, 1),
                    },
                )
            ]
        )
        batch = Batch.objects.get(item=self.haldi)
        ret = buying_mapping.make_purchase_return(pi)
        self.assertEqual(ret.items.get().batch_id, batch.pk)
        ret.posting_date = datetime.date(2026, 9, 1)  # after the expiry
        ret.save()
        ret.submit()
        sle = StockLedgerEntry.objects.get(voucher_no=ret.name, is_cancelled=False)
        self.assertEqual((sle.batch_id, sle.qty), (batch.pk, D("-10.000")))
        self.integrity_ok()

    def test_refund_from_supplier_settles_the_debit_note(self):
        pi = self.purchase([(self.chilli, 10, 50)])
        ret = buying_mapping.make_purchase_return(pi)
        ret.submit()
        refund = buying_mapping.make_payment_entry(self.fresh(ret))
        self.assertEqual(refund.payment_type, PaymentType.RECEIVE)
        self.assertEqual(refund.paid_amount, D("500.00"))
        refund.submit()
        self.assertEqual(self.fresh(ret).outstanding_amount, D("0.00"))
        self.integrity_ok()

    def test_ageing_payable_nets_the_debit_note(self):
        pi = self.purchase([(self.chilli, 10, 50)])
        ret = buying_mapping.make_purchase_return(pi)
        line = ret.items.get()
        line.qty = D("-2")
        line.save()
        ret.calculate_taxes_and_totals(resolve_lines=False)
        ret.submit()
        report = outstanding_service.ageing_report(
            "Supplier", as_on=datetime.date.today(), company_id=self.company.pk
        )
        self.assertEqual(report["total"]["outstanding"], "400.00")
        self.assertEqual(outstanding_service.repost(), 0)  # nothing drifted


class ReturnIntegrityTests(ReturnBase):
    def test_verify_integrity_flags_a_wrong_stored_outstanding_of_a_return(self):
        self.stock_in(self.chilli, 10, 40)
        si = self.sell([(self.chilli, 4, 100)])
        ret = selling_mapping.make_sales_return(si)
        ret.submit()
        SalesInvoice.objects.filter(pk=ret.pk).update(outstanding_amount=D("0.00"))
        errors = outstanding_service.check_integrity()
        self.assertTrue(any("Outstanding Drift" in e and ret.name in e for e in errors))
        outstanding_service.repost()
        self.assertEqual(outstanding_service.check_integrity(), [])
