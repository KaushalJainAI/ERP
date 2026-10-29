"""NGU catalogue crosswalk: idempotent import, no Item side-effects, link API + RBAC."""

from decimal import Decimal

from django.db import IntegrityError, transaction
from rest_framework.test import APITestCase

from ngu.models import NguCatalogEntry
from ngu.sync import sync_catalog
from stock.models import Item, ItemGroup, UOM
from users.models import Role, RolePermission, User, UserRole

DATA = {
    "categories": [{"id": 1, "name": "Masala"}],
    "products": [
        {"id": 10, "name": "Pav Bhaji masala", "category_id": 1, "is_active": True},
        {"id": 11, "name": "Chana papad", "category_id": 1, "is_active": True},
    ],
    "variants": [
        {
            "id": 1,
            "product_id": 10,
            "weight": "100.00",
            "unit": "g",
            "price": "96.00",
            "discount_price": "79.00",
            "sku": "",
            "is_active": True,
        },
        {
            "id": 2,
            "product_id": 11,
            "weight": "200.00",
            "unit": "g",
            "price": "70.00",
            "discount_price": None,
            "sku": "",
            "is_active": True,
        },
    ],
    "combos": [
        {
            "id": 5,
            "name": "Starter pack",
            "weight": None,
            "unit": None,
            "discount_price": "150.00",
            "is_active": True,
        }
    ],
    "combo_items": [{"combo_id": 5, "variant_id": 1, "quantity": 2}],
}


def make_item(code, name):
    group, _ = ItemGroup.objects.get_or_create(name="All")
    uom, _ = UOM.objects.get_or_create(name="Nos")
    return Item.objects.create(code=code, name=name, item_group=group, stock_uom=uom)


class NguSyncTests(APITestCase):
    def test_import_is_idempotent_and_never_touches_items(self):
        make_item("PB", "pav  bhaji MASALA")
        make_item("OTHER", "Something else")
        before = list(Item.objects.values_list("code", "name"))
        first = sync_catalog(DATA)
        self.assertEqual(
            (first["variants"], first["combos"], first["linked"]), (2, 1, 1)
        )
        second = sync_catalog(DATA)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["linked"], 0)
        self.assertEqual(NguCatalogEntry.objects.count(), 3)
        self.assertEqual(list(Item.objects.values_list("code", "name")), before)
        v1 = NguCatalogEntry.objects.get(kind="variant", ngu_id=1)
        self.assertEqual(v1.item.code, "PB")
        self.assertEqual(v1.link_method, "exact_name")
        self.assertEqual(v1.price, Decimal("96.00"))
        self.assertIsNone(NguCatalogEntry.objects.get(kind="variant", ngu_id=2).item)
        combo = NguCatalogEntry.objects.get(kind="combo", ngu_id=5)
        self.assertEqual(combo.components, [{"variant_id": 1, "quantity": 2}])

    def test_resync_keeps_manual_link_and_refreshes_price(self):
        item = make_item("CP", "Chana papad (Tally)")
        sync_catalog(DATA)
        NguCatalogEntry.objects.filter(ngu_id=2, kind="variant").update(
            item=item, link_method="manual"
        )
        changed = {
            **DATA,
            "variants": [
                {**DATA["variants"][0]},
                {**DATA["variants"][1], "price": "75.00"},
            ],
        }
        sync_catalog(changed)
        e = NguCatalogEntry.objects.get(kind="variant", ngu_id=2)
        self.assertEqual((e.item_id, e.price), (item.pk, Decimal("75.00")))

    def test_ambiguous_name_is_not_auto_linked(self):
        make_item("A", "Chana papad")
        make_item("B", "chana papad")
        self.assertEqual(sync_catalog(DATA)["linked"], 0)

    def test_db_constraints(self):
        item = make_item("X", "X")
        sync_catalog(DATA)
        v1, v2 = NguCatalogEntry.objects.filter(kind="variant").order_by("ngu_id")
        v1.item = item
        v1.link_method = "manual"
        v1.save()
        v2.item = item
        v2.link_method = "manual"
        with self.assertRaises(IntegrityError), transaction.atomic():
            v2.save()
        combo = NguCatalogEntry.objects.get(kind="combo")
        combo.item = make_item("Y", "Y")
        combo.link_method = "manual"
        with self.assertRaises(IntegrityError), transaction.atomic():
            combo.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            NguCatalogEntry.objects.create(kind="variant", ngu_id=1, product_name="dup")


class NguApiTests(APITestCase):
    URL = "/api/ngu/catalog/"

    def client_for(self, name, role_name, flags):
        user = User.objects.create_user(username=name, email=f"{name}@x.com")
        role, _ = Role.objects.get_or_create(name=role_name)
        UserRole.objects.create(user=user, role=role)
        RolePermission.objects.get_or_create(
            role=role,
            model_label="ngu.NguCatalogEntry",
            defaults={f: True for f in flags},
        )
        self.client.force_authenticate(user)

    def setUp(self):
        sync_catalog(DATA)
        self.entry = NguCatalogEntry.objects.get(kind="variant", ngu_id=2)
        self.item = make_item("CP", "Chana papad (Tally)")

    def test_reader_can_list_but_not_link(self):
        self.client_for("rd", "NguReader", ["can_read"])
        self.assertEqual(self.client.get(self.URL).status_code, 200)
        r = self.client.patch(
            f"{self.URL}{self.entry.pk}/", {"item": self.item.pk}, format="json"
        )
        self.assertEqual(r.status_code, 403)

    def test_writer_links_and_unlinks_only_item(self):
        self.client_for("wr", "NguWriter", ["can_read", "can_write"])
        r = self.client.patch(
            f"{self.URL}{self.entry.pk}/",
            {"item": self.item.pk, "price": "1.00", "product_name": "hacked"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.entry.refresh_from_db()
        self.assertEqual(
            (self.entry.item_id, self.entry.link_method), (self.item.pk, "manual")
        )
        self.assertEqual(
            (self.entry.price, self.entry.product_name),
            (Decimal("70.00"), "Chana papad"),
        )
        r = self.client.patch(
            f"{self.URL}{self.entry.pk}/", {"item": None}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.entry.refresh_from_db()
        self.assertEqual((self.entry.item_id, self.entry.link_method), (None, ""))

    def test_no_create_or_delete_and_combo_not_linkable(self):
        self.client_for(
            "wr2", "NguWriter2", ["can_read", "can_write", "can_create", "can_delete"]
        )
        self.assertEqual(self.client.post(self.URL, {}, format="json").status_code, 405)
        self.assertEqual(
            self.client.delete(f"{self.URL}{self.entry.pk}/").status_code, 405
        )
        combo = NguCatalogEntry.objects.get(kind="combo")
        r = self.client.patch(
            f"{self.URL}{combo.pk}/", {"item": self.item.pk}, format="json"
        )
        self.assertEqual(r.status_code, 400)

    def test_second_variant_cannot_take_same_item(self):
        self.client_for("wr3", "NguWriter3", ["can_read", "can_write"])
        other = NguCatalogEntry.objects.get(kind="variant", ngu_id=1)
        self.client.patch(
            f"{self.URL}{self.entry.pk}/", {"item": self.item.pk}, format="json"
        )
        r = self.client.patch(
            f"{self.URL}{other.pk}/", {"item": self.item.pk}, format="json"
        )
        self.assertEqual(r.status_code, 400)
