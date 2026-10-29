"""Item variants (ERPNext Item Attribute / template / create_variant) and batches
created dated today."""

import datetime
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company, DocStatus, DummySubmittableDoc
from stock.models import (
    UOM,
    Batch,
    Item,
    ItemAttribute,
    ItemAttributeValue,
    ItemGroup,
    ItemVariantAttribute,
    Warehouse,
)
from stock.services import batches as batch_service
from stock.services import ledger as stock_service
from stock.services import variants as variant_service
from users.models import Role, RolePermission, User, UserRole


class VariantBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Nidhi Test", abbr="NT", default_currency="INR"
        )
        self.uom = UOM.objects.create(name="Pack")
        self.group = ItemGroup.objects.create(name="Spices", is_group=False)
        self.wh = Warehouse.objects.create(name="Store", company=self.company)
        self.pack = ItemAttribute.objects.create(name="Pack Size")
        self.g100 = ItemAttributeValue.objects.create(
            attribute=self.pack, value="100g", abbr="100"
        )
        self.g500 = ItemAttributeValue.objects.create(
            attribute=self.pack, value="500g", abbr="500"
        )
        self.blend = ItemAttribute.objects.create(name="Blend")
        self.mild = ItemAttributeValue.objects.create(
            attribute=self.blend, value="Mild", abbr="M"
        )
        self.template = Item.objects.create(
            code="GARAM",
            name="Garam Masala",
            item_group=self.group,
            stock_uom=self.uom,
            is_stock_item=False,
            has_variants=True,
            has_batch_no=True,
            has_expiry_date=True,
            shelf_life_in_days=365,
            standard_rate=D("120"),
            gst_hsn_code="0910",
        )
        for attr in (self.pack, self.blend):
            ItemVariantAttribute.objects.create(item=self.template, attribute=attr)
        self.admin = User.objects.create_superuser("v_admin", "v@t.com", "pw")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def variant(self, value=None, **kw):
        return variant_service.create_variant(
            self.template, {self.pack.pk: (value or self.g500).pk}, **kw
        )


class VariantCreationTests(VariantBase):
    def test_create_variant_copies_template_and_builds_code(self):
        v = variant_service.create_variant(
            self.template, {self.blend.pk: self.mild.pk, self.pack.pk: self.g500.pk}
        )
        # Code = template code + abbreviations in the template's attribute order.
        self.assertEqual(v.code, "GARAM-500-M")
        self.assertEqual(v.name, "Garam Masala - 500g / Mild")
        self.assertEqual(v.variant_of_id, self.template.pk)
        self.assertTrue(v.is_stock_item)
        self.assertFalse(v.has_variants)
        for f in ("item_group_id", "stock_uom_id", "standard_rate", "gst_hsn_code",
                  "has_batch_no", "has_expiry_date", "shelf_life_in_days"):
            self.assertEqual(getattr(v, f), getattr(self.template, f), f)
        self.assertEqual(variant_service.variant_label(v), "500g, Mild")

    def test_same_combination_twice_is_refused(self):
        first = self.variant()
        with self.assertRaisesMessage(ValidationError, "already exists"):
            self.variant()
        self.assertEqual(Item.objects.filter(variant_of=self.template).count(), 1)
        self.assertEqual(first.variant_signature, f"{self.pack.pk}:{self.g500.pk}")

    def test_db_rejects_duplicate_combination_and_template_rules(self):
        v = self.variant()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Item.objects.create(
                code="DUP",
                name="dup",
                item_group=self.group,
                stock_uom=self.uom,
                variant_of=self.template,
                variant_signature=v.variant_signature,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Item.objects.create(  # a template cannot be a stock item
                code="T2", name="t2", item_group=self.group, stock_uom=self.uom,
                has_variants=True, is_stock_item=True,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Item.objects.create(  # a template cannot itself be a variant
                code="T3", name="t3", item_group=self.group, stock_uom=self.uom,
                has_variants=True, is_stock_item=False, variant_of=self.template,
            )

    def test_value_must_belong_to_the_attribute_and_be_declared(self):
        with self.assertRaisesMessage(ValidationError, "not a value of attribute"):
            variant_service.create_variant(self.template, {self.pack.pk: self.mild.pk})
        other = ItemAttribute.objects.create(name="Grade")
        v = ItemAttributeValue.objects.create(attribute=other, value="A", abbr="A")
        with self.assertRaisesMessage(ValidationError, "not used by template"):
            variant_service.create_variant(self.template, {other.pk: v.pk})
        with self.assertRaisesMessage(ValidationError, "at least one"):
            variant_service.create_variant(self.template, {})
        with self.assertRaisesMessage(ValidationError, "does not belong"):
            ItemVariantAttribute.objects.create(
                item=self.variant(), attribute=self.blend, value=self.g100
            )

    def test_plain_item_is_not_a_template(self):
        plain = Item.objects.create(
            code="PLAIN", name="p", item_group=self.group, stock_uom=self.uom
        )
        with self.assertRaisesMessage(ValidationError, "not a template"):
            variant_service.create_variant(plain, {self.pack.pk: self.g100.pk})

    def test_attribute_in_use_cannot_be_deleted(self):
        self.variant()
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            self.g500.delete()
        with self.assertRaises(ProtectedError):
            self.pack.delete()


class VariantApiTests(VariantBase):
    def test_attribute_crud_with_values(self):
        r = self.c.post(
            "/api/stock/item-attributes/",
            {
                "name": "Roast",
                "values": [
                    {"value": "Light", "abbr": "L"},
                    {"value": "Dark", "abbr": "D"},
                ],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual([v["abbr"] for v in r.data["values"]], ["L", "D"])
        dup = self.c.post(
            "/api/stock/item-attributes/",
            {"name": "Roast2", "values": [{"value": "A", "abbr": "X"}, {"value": "B", "abbr": "x"}]},
            format="json",
        )
        self.assertEqual(dup.status_code, 400)
        # Edit: drop one value, add another.
        keep = r.data["values"][0]
        up = self.c.patch(
            f"/api/stock/item-attributes/{r.data['id']}/",
            {"values": [keep, {"value": "Medium", "abbr": "MD"}]},
            format="json",
        )
        self.assertEqual(up.status_code, 200, up.content)
        self.assertEqual([v["value"] for v in up.data["values"]], ["Light", "Medium"])

    def test_used_value_cannot_be_removed(self):
        self.variant()
        rows = [{"id": self.g100.pk, "value": "100g", "abbr": "100"}]
        r = self.c.patch(
            f"/api/stock/item-attributes/{self.pack.pk}/", {"values": rows}, format="json"
        )
        self.assertEqual(r.status_code, 400, r.content)
        self.assertTrue(ItemAttributeValue.objects.filter(pk=self.g500.pk).exists())
        gone = self.c.delete(f"/api/stock/item-attributes/{self.pack.pk}/")
        self.assertEqual(gone.status_code, 409)

    def test_template_via_api_is_never_a_stock_item(self):
        r = self.c.post(
            "/api/stock/items/",
            {
                "code": "CHAT",
                "name": "Chaat Masala",
                "item_group": self.group.pk,
                "stock_uom": self.uom.pk,
                "is_stock_item": True,
                "has_variants": True,
                "template_attributes": [self.pack.pk],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(r.data["is_stock_item"])
        self.assertEqual([a["attribute_name"] for a in r.data["attributes"]], ["Pack Size"])
        # A plain item cannot declare attributes.
        bad = self.c.post(
            "/api/stock/items/",
            {
                "code": "PL", "name": "pl", "item_group": self.group.pk,
                "stock_uom": self.uom.pk, "template_attributes": [self.pack.pk],
            },
            format="json",
        )
        self.assertEqual(bad.status_code, 400)

    def test_create_variant_endpoint(self):
        r = self.c.post(
            f"/api/stock/items/{self.template.pk}/create-variant/",
            {"attributes": {str(self.pack.pk): self.g100.pk}},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["code"], "GARAM-100")
        self.assertEqual(r.data["variant_of"], self.template.pk)
        self.assertEqual(r.data["variant_label"], "100g")
        self.assertTrue(r.data["is_stock_item"])
        again = self.c.post(
            f"/api/stock/items/{self.template.pk}/create-variant/",
            {"attributes": [{"attribute": self.pack.pk, "value": self.g100.pk}]},
            format="json",
        )
        self.assertEqual(again.status_code, 400)
        self.assertIn("already exists", str(again.data))
        none = self.c.post(
            f"/api/stock/items/{self.template.pk}/create-variant/", {}, format="json"
        )
        self.assertEqual(none.status_code, 400)
        listing = self.c.get(f"/api/stock/items/?variant_of={self.template.pk}")
        self.assertEqual(listing.json()["count"], 1)

    def test_variant_cannot_be_reparented_or_templated_and_template_is_frozen(self):
        v = self.variant()
        r = self.c.patch(
            f"/api/stock/items/{v.pk}/", {"variant_of": None}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        v.refresh_from_db()
        self.assertEqual(v.variant_of_id, self.template.pk)  # read-only
        r = self.c.patch(f"/api/stock/items/{v.pk}/", {"has_variants": True}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.c.patch(
            f"/api/stock/items/{self.template.pk}/", {"has_variants": False}, format="json"
        )
        self.assertEqual(r.status_code, 400)
        r = self.c.patch(
            f"/api/stock/items/{self.template.pk}/",
            {"template_attributes": [self.pack.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)  # attributes frozen once variants exist

    def test_create_variant_needs_create_permission_on_items(self):
        role = Role.objects.create(name="Item Reader")
        RolePermission.objects.create(role=role, model_label="stock.Item", can_read=True)
        user = User.objects.create_user("reader", "r@t.com", "pw")
        UserRole.objects.create(user=user, role=role)
        c = APIClient()
        c.force_authenticate(user)
        r = c.post(
            f"/api/stock/items/{self.template.pk}/create-variant/",
            {"attributes": {str(self.pack.pk): self.g100.pk}},
            format="json",
        )
        self.assertEqual(r.status_code, 403)


class TemplateCannotBeTransactedTests(VariantBase):
    def test_ledger_refuses_a_template(self):
        doc = DummySubmittableDoc.objects.create(
            name="D-1", title="t", docstatus=DocStatus.SUBMITTED
        )
        b = Batch(batch_no="T-B", item=self.template)
        with self.assertRaisesMessage(ValidationError, "template with variants"):
            b.save()
        with self.assertRaisesMessage(ValidationError, "template with variants"):
            stock_service.post(
                doc,
                [{"item": self.template, "warehouse": self.wh, "qty": D("1"),
                  "valuation_rate": D("5")}],
                posting_date=datetime.date(2026, 4, 1),
            )

    def test_document_line_with_a_template_is_refused(self):
        r = self.c.post(
            "/api/stock/stock-entries/",
            {
                "company": self.company.pk,
                "stock_entry_type": "Material Receipt",
                "posting_date": "2026-04-01",
                "items": [
                    {"item": self.template.pk, "target_warehouse": self.wh.pk,
                     "qty": "1.000", "valuation_rate": "5.000000"}
                ],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("template with variants", str(r.data))
        ok = self.c.post(
            "/api/stock/stock-entries/",
            {
                "company": self.company.pk,
                "stock_entry_type": "Material Receipt",
                "posting_date": "2026-04-01",
                "items": [
                    {"item": self.variant().pk, "target_warehouse": self.wh.pk,
                     "qty": "1.000", "valuation_rate": "5.000000"}
                ],
            },
            format="json",
        )
        self.assertEqual(ok.status_code, 201, ok.content)

    def test_item_with_stock_cannot_become_a_template(self):
        plain = Item.objects.create(
            code="HAS-STOCK", name="x", item_group=self.group, stock_uom=self.uom
        )
        doc = DummySubmittableDoc.objects.create(
            name="D-2", title="t", docstatus=DocStatus.SUBMITTED
        )
        stock_service.post(
            doc,
            [{"item": plain, "warehouse": self.wh, "qty": D("2"), "valuation_rate": D("5")}],
            posting_date=datetime.date(2026, 4, 1),
        )
        plain.has_variants = True
        plain.is_stock_item = False
        with self.assertRaisesMessage(ValidationError, "cannot become a template"):
            plain.save()


class BatchDatedTodayTests(VariantBase):
    def test_api_defaults_manufacturing_date_to_today_for_a_variant(self):
        v = self.variant()
        today = datetime.date.today()
        r = self.c.post("/api/stock/batches/", {"item": v.pk}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["manufacturing_date"], today.isoformat())
        self.assertEqual(
            r.data["expiry_date"], (today + datetime.timedelta(days=365)).isoformat()
        )
        self.assertEqual(r.data["item_code"], "GARAM-500")
        self.assertEqual(r.data["item_variant_label"], "500g")
        # An explicit date is kept.
        r2 = self.c.post(
            "/api/stock/batches/",
            {"item": v.pk, "manufacturing_date": "2026-01-05"},
            format="json",
        )
        self.assertEqual(r2.data["manufacturing_date"], "2026-01-05")
        # List / detail expose the variant label.
        row = self.c.get(f"/api/stock/batches/{r.data['id']}/").json()
        self.assertEqual(row["item_variant_label"], "500g")

    def test_batch_for_a_template_is_refused_via_api_and_service(self):
        r = self.c.post("/api/stock/batches/", {"item": self.template.pk}, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("template with variants", str(r.data))
        with self.assertRaisesMessage(ValidationError, "template with variants"):
            batch_service.create_batch(self.template)

    def test_service_defaults_to_today_but_keeps_a_passed_posting_date(self):
        v = self.variant()
        today = datetime.date.today()
        self.assertEqual(batch_service.create_batch(v).manufacturing_date, today)
        posted = batch_service.create_batch(
            v, default_manufacturing_date=datetime.date(2026, 4, 1)
        )
        self.assertEqual(posted.manufacturing_date, datetime.date(2026, 4, 1))
        explicit = batch_service.create_batch(
            v,
            manufacturing_date=datetime.date(2026, 3, 1),
            default_manufacturing_date=datetime.date(2026, 4, 1),
        )
        self.assertEqual(explicit.manufacturing_date, datetime.date(2026, 3, 1))

    def test_item_picker_filter_batch_tracked_non_template(self):
        v = self.variant()
        ids = {
            r["id"]
            for r in self.c.get(
                "/api/stock/items/?has_batch_no=true&has_variants=false"
            ).json()["results"]
        }
        self.assertIn(v.pk, ids)
        self.assertNotIn(self.template.pk, ids)

    def test_stock_manager_style_role_can_create_a_batch(self):
        v = self.variant()
        role = Role.objects.create(name="Batch Maker")
        RolePermission.objects.create(
            role=role, model_label="stock.Batch", can_read=True, can_create=True
        )
        user = User.objects.create_user("maker", "m@t.com", "pw")
        UserRole.objects.create(user=user, role=role)
        c = APIClient()
        c.force_authenticate(user)
        r = c.post("/api/stock/batches/", {"item": v.pk}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        nobody = User.objects.create_user("nobody", "n@t.com", "pw")
        c2 = APIClient()
        c2.force_authenticate(nobody)
        self.assertEqual(
            c2.post("/api/stock/batches/", {"item": v.pk}, format="json").status_code, 403
        )
