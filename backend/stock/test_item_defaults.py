"""An item saved with only a name, a unit, a GST % and a price (card S-7)."""

from decimal import Decimal as D

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import ItemTaxTemplate
from core.models import NamingSeries
from gst.services.seeding import ensure_item_tax_templates
from stock.models import UOM, Item, ItemGroup
from stock.services import item_defaults
from users.models import User


class ItemMinimalInputTests(TestCase):
    def setUp(self):
        ensure_item_tax_templates()
        self.admin = User.objects.create_superuser("item_admin", "item@t.com", "pw")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)
        self.url = "/api/stock/items/"

    def post(self, **body):
        return self.c.post(self.url, body, format="json")

    def test_name_gst_rate_and_price_is_enough(self):
        r = self.post(name="Haldi", gst_rate="5", standard_rate="120")
        self.assertEqual(r.status_code, 201, r.content)
        item = Item.objects.get(pk=r.data["id"])
        self.assertTrue(item.code.startswith("ITM-"))
        self.assertEqual(item.item_group.name, "General")
        self.assertFalse(item.item_group.is_group)
        self.assertEqual(item.item_group.parent.name, "All Item Groups")
        self.assertEqual(item.item_tax_template.name, "GST 5%")
        self.assertEqual(item.standard_rate, D("120"))
        self.assertEqual(item.stock_uom.name, "Nos")
        self.assertEqual(r.data["item_tax_rate"]["IGST"], "5.000")
        self.assertNotIn("gst_rate", r.data)

    def test_generated_codes_continue_the_series_and_skip_typed_codes(self):
        first = self.post(name="A", gst_rate="0").data["code"]
        self.post(name="Typed", code="ITM-0002", gst_rate="0")
        third = self.post(name="C", gst_rate="0").data["code"]
        self.assertEqual(first, "ITM-0001")
        self.assertEqual(third, "ITM-0003")

    def test_blank_code_is_generated_and_typed_code_is_kept(self):
        r = self.post(name="Blank", code="", gst_rate="18")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.data["code"].startswith("ITM-"))
        r = self.post(name="Typed", code="HALDI-1", gst_rate="18")
        self.assertEqual(r.data["code"], "HALDI-1")

    def test_unknown_gst_rate_is_refused_and_nothing_is_saved(self):
        before = Item.objects.count()
        r = self.post(name="Odd", gst_rate="7", standard_rate="10")
        self.assertEqual(r.status_code, 400)
        self.assertIn("gst_rate", r.data)
        self.assertEqual(Item.objects.count(), before)

    def test_rate_without_a_template_is_refused(self):
        ItemTaxTemplate.objects.filter(name="GST 5%").delete()
        before = Item.objects.count()
        r = self.post(name="Haldi", gst_rate="5")
        self.assertEqual(r.status_code, 400)
        self.assertIn("gst_rate", r.data)
        self.assertEqual(Item.objects.count(), before)

    def test_explicit_template_wins_over_gst_rate(self):
        eighteen = ItemTaxTemplate.objects.get(name="GST 18%")
        r = self.post(name="Explicit", gst_rate="5", item_tax_template=eighteen.pk)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Item.objects.get(pk=r.data["id"]).item_tax_template, eighteen)

    def test_negative_price_is_refused(self):
        r = self.post(name="Neg", gst_rate="5", standard_rate="-1")
        self.assertEqual(r.status_code, 400)

    def test_update_switches_template_and_keeps_code_when_blank(self):
        r = self.post(name="Haldi", gst_rate="5")
        pk, code = r.data["id"], r.data["code"]
        r = self.c.patch(
            f"{self.url}{pk}/", {"code": "", "gst_rate": "18"}, format="json"
        )
        self.assertEqual(r.status_code, 200, r.content)
        item = Item.objects.get(pk=pk)
        self.assertEqual(item.code, code)
        self.assertEqual(item.item_tax_template.name, "GST 18%")

    def test_default_unit_is_the_one_most_items_use(self):
        kg = UOM.objects.create(name="Kg")
        self.post(name="One", gst_rate="0", stock_uom=kg.pk)
        self.post(name="Two", gst_rate="0", stock_uom=kg.pk)
        self.post(
            name="Three", gst_rate="0", stock_uom=UOM.objects.create(name="Box").pk
        )
        r = self.post(name="Four", gst_rate="0")
        self.assertEqual(r.data["stock_uom_name"], "Kg")

    def test_missing_unit_and_group_fall_back_to_defaults(self):
        r = self.post(name="Nothing", gst_rate="0")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Item.objects.get(pk=r.data["id"]).stock_uom.name, "Nos")

    def test_default_group_is_created_once(self):
        first = item_defaults.default_item_group()
        second = item_defaults.default_item_group()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            ItemGroup.objects.filter(name="General", is_group=False).count(), 1
        )

    def test_next_item_code_uses_the_item_prefix(self):
        code = item_defaults.next_item_code()
        self.assertEqual(
            code, f"ITM-{NamingSeries.objects.get(prefix='ITM-').current:04d}"
        )
