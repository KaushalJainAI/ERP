"""The master backfill data migrations (run directly against the live models)."""

import importlib

from django.apps import apps as django_apps
from django.test import TestCase

from buying.models import Supplier, SupplierGroup
from core.models import Company, State
from selling.models import Customer, CustomerGroup
from stock.models import UOM


def run(path, func):
    getattr(importlib.import_module(path), func)(django_apps, None)


class MasterBackfillTests(TestCase):
    def test_company_party_and_uom_backfill(self):
        co = Company.objects.create(
            name="Legacy Co",
            abbr="LGC",
            gstin="27AAACR0000A1Z5",  # bad checksum is fine
        )
        no_gstin = Company.objects.create(name="Plain Co", abbr="PLN")
        cg = CustomerGroup.objects.create(name="CG")
        sg = SupplierGroup.objects.create(name="SG")
        cust = Customer.objects.create(
            name="C1", company=co, customer_group=cg, gstin="06AAACJ0000A1Z2"
        )
        cust_plain = Customer.objects.create(name="C2", company=co, customer_group=cg)
        cust_odd = Customer.objects.create(
            name="C3", company=co, customer_group=cg, gstin="99XXXXXXXXXXXXX"
        )
        sup = Supplier.objects.create(
            name="S1", company=co, supplier_group=sg, gstin="24AAACP0000A1Z2"
        )
        uom = UOM.objects.create(name="Meter")
        Company.objects.filter(pk=co.pk).update(gst_category="Unregistered")

        run("core.migrations.0008_seed_states", "seed_states")
        run("selling.migrations.0004_backfill_customer_gst", "backfill")
        run("buying.migrations.0004_backfill_supplier_gst", "backfill")
        run("stock.migrations.0005_backfill_uom_gst_uqc", "fill_uqc")

        co.refresh_from_db()
        self.assertEqual((co.gst_category, co.state_id), ("Registered Regular", "27"))
        no_gstin.refresh_from_db()
        self.assertEqual(
            (no_gstin.gst_category, no_gstin.state_id), ("Unregistered", None)
        )

        cust.refresh_from_db()
        self.assertEqual(
            (cust.gst_category, cust.state_id), ("Registered Regular", "06")
        )
        cust_plain.refresh_from_db()
        self.assertEqual(
            (cust_plain.gst_category, cust_plain.state_id), ("Unregistered", None)
        )
        cust_odd.refresh_from_db()  # unknown prefix: category set, state left empty
        self.assertEqual(
            (cust_odd.gst_category, cust_odd.state_id), ("Registered Regular", None)
        )
        sup.refresh_from_db()
        self.assertEqual((sup.gst_category, sup.state_id), ("Registered Regular", "24"))
        uom.refresh_from_db()
        self.assertEqual(uom.gst_uqc, "MTR")

        # idempotent: a second run changes nothing
        run("selling.migrations.0004_backfill_customer_gst", "backfill")
        cust.refresh_from_db()
        self.assertEqual(cust.state_id, "06")
        self.assertEqual(State.objects.count(), 40)
