"""The document-totals backfill (selling/buying 0006), run through the migration graph.

Walks selling and buying back to their pre-GST-document state, creates documents
there, then migrates forward: legacy documents must come out with
``net_total == rounded_total == grand_total``, zero tax, the legacy ``gst_category``
marker "", and satisfy every new CHECK constraint (added after the backfill).
"""

from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [
    ("selling", "0004_backfill_customer_gst"),
    ("buying", "0004_backfill_supplier_gst"),
]


def latest_targets(executor):
    return [
        key
        for key in executor.loader.graph.leaf_nodes()
        if key[0] in ("selling", "buying")
    ]


class DocumentBackfillTests(TransactionTestCase):
    def test_legacy_documents_get_zero_tax_and_valid_totals(self):
        executor = MigrationExecutor(connection)
        targets = latest_targets(executor)
        try:
            executor.migrate(BEFORE)
            old = MigrationExecutor(connection).loader.project_state(BEFORE).apps

            Company = old.get_model("core", "Company")
            company = Company.objects.create(name="Legacy Co", abbr="LGC")
            CustomerGroup = old.get_model("selling", "CustomerGroup")
            Customer = old.get_model("selling", "Customer")
            Account = old.get_model("accounts", "Account")
            cust = Customer.objects.create(
                name="C",
                company=company,
                customer_group=CustomerGroup.objects.create(name="G"),
            )
            debtors = Account.objects.create(
                name="D", company=company, root_type="Asset"
            )
            SalesOrder = old.get_model("selling", "SalesOrder")
            SalesInvoice = old.get_model("selling", "SalesInvoice")
            so = SalesOrder.objects.create(
                name="SO-L-1",
                customer=cust,
                company=company,
                transaction_date="2026-01-01",
                grand_total=Decimal("1234.56"),
            )
            si = SalesInvoice.objects.create(
                name="SI-L-1",
                customer=cust,
                company=company,
                posting_date="2026-01-01",
                debit_to=debtors,
                income_account=debtors,
                grand_total=Decimal("99.99"),
            )
            SupplierGroup = old.get_model("buying", "SupplierGroup")
            Supplier = old.get_model("buying", "Supplier")
            sup = Supplier.objects.create(
                name="S",
                company=company,
                supplier_group=SupplierGroup.objects.create(name="SG"),
            )
            PurchaseOrder = old.get_model("buying", "PurchaseOrder")
            po = PurchaseOrder.objects.create(
                name="PO-L-1",
                supplier=sup,
                company=company,
                transaction_date="2026-01-01",
                grand_total=Decimal("0.10"),
            )
            ids = (so.pk, si.pk, po.pk)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(targets)

        new = MigrationExecutor(connection).loader.project_state(targets).apps
        for model, pk, grand in (
            ("SalesOrder", ids[0], "1234.56"),
            ("SalesInvoice", ids[1], "99.99"),
        ):
            row = new.get_model("selling", model).objects.get(pk=pk)
            self.assertEqual(
                (
                    row.grand_total,
                    row.net_total,
                    row.rounded_total,
                    row.total_taxes_and_charges,
                    row.rounding_adjustment,
                    row.gst_category,
                ),
                (Decimal(grand), Decimal(grand), Decimal(grand), 0, 0, ""),
            )
        row = new.get_model("buying", "PurchaseOrder").objects.get(pk=ids[2])
        self.assertEqual((row.net_total, row.rounded_total), (Decimal("0.10"),) * 2)
        # the CHECK constraints now guard the table
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            new.get_model("selling", "SalesOrder").objects.filter(pk=ids[0]).update(
                grand_total=Decimal("1.00")
            )
