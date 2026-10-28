"""seed_demo with GST data: fresh and already-seeded databases stay integrity-green."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import GLEntry
from buying.models import PurchaseInvoice
from core.models import Company, SystemHealthCheck
from gst.validators import check_gstin
from selling.models import Customer, SalesInvoice


@override_settings(DEBUG=True)
class SeedDemoGSTTests(TestCase):
    def run_cmd(self, name):
        call_command(name, stdout=StringIO(), stderr=StringIO())

    def test_fresh_database_seeds_taxed_documents_and_is_idempotent(self):
        self.run_cmd("seed_defaults")
        self.run_cmd("seed_demo")
        company = Company.objects.get(abbr="ACME")
        self.assertTrue(check_gstin(company.gstin).valid)
        self.assertEqual(company.state_id, "27")
        for customer in Customer.objects.filter(company=company):
            self.assertTrue(check_gstin(customer.gstin).valid, customer.name)

        intra = SalesInvoice.objects.get(name="SINV-DEMO-0002")
        inter = SalesInvoice.objects.get(name="SINV-DEMO-0003")
        purchase = PurchaseInvoice.objects.get(name="PINV-DEMO-0002")
        self.assertEqual(
            sorted(intra.taxes.values_list("account__gst_component", flat=True)),
            ["CGST", "SGST"],
        )
        self.assertEqual(
            list(inter.taxes.values_list("account__gst_component", flat=True)),
            ["IGST"],
        )
        self.assertEqual(
            sorted(purchase.taxes.values_list("account__gst_component", flat=True)),
            ["CGST", "SGST"],
        )
        for doc in (intra, inter, purchase):
            self.assertEqual(doc.docstatus, 1)
            self.assertGreater(doc.total_taxes_and_charges, 0)

        gl_count = GLEntry.objects.count()
        self.run_cmd("seed_demo")  # idempotent
        self.assertEqual(GLEntry.objects.count(), gl_count)

        self.run_cmd("verify_integrity")
        check = SystemHealthCheck.objects.filter(check_name="gst_integrity").latest(
            "pk"
        )
        self.assertEqual(check.status, "ok")
        self.assertEqual(check.details["errors"], [])
