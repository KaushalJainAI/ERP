from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from notifications.models import AlertRule, Notification
from notifications.seed import DEFAULT_ALERT_RULES, seed_alert_rules
from stock.models import Item, ItemGroup, UOM
from stock.serializers import ItemSerializer

User = get_user_model()


class NotificationModelTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(
            username="user1", email="user1@example.com", password="password123"
        )
        self.user2 = User.objects.create_user(
            username="user2", email="user2@example.com", password="password123"
        )

    def test_notification_creation_and_unique_constraint(self):
        """Notification can be created and (user, dedupe_key) is unique."""
        n1 = Notification.objects.create(
            user=self.user1,
            rule_key="raw_material_shortage",
            message_key="alert.raw_material_shortage",
            params={"item": "Jeera"},
            link="/stock/items/1",
            dedupe_key="raw_material_shortage:1:2026-10-31",
        )
        self.assertIsNotNone(n1.pk)
        self.assertIsNone(n1.read_at)
        self.assertEqual(
            str(n1), f"{self.user1} - raw_material_shortage ({n1.dedupe_key})"
        )

        # Same user, same dedupe_key must fail
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Notification.objects.create(
                    user=self.user1,
                    rule_key="raw_material_shortage",
                    message_key="alert.raw_material_shortage",
                    dedupe_key="raw_material_shortage:1:2026-10-31",
                )

        # Different user, same dedupe_key should succeed
        n2 = Notification.objects.create(
            user=self.user2,
            rule_key="raw_material_shortage",
            message_key="alert.raw_material_shortage",
            dedupe_key="raw_material_shortage:1:2026-10-31",
        )
        self.assertIsNotNone(n2.pk)

    def test_alert_rule_seed_idempotency(self):
        """Seeding alert rules creates the 9 defined rules and running again adds no duplicates."""
        self.assertEqual(AlertRule.objects.count(), 0)

        created_first = seed_alert_rules()
        self.assertEqual(created_first, 9)
        self.assertEqual(AlertRule.objects.count(), 9)

        # All 9 keys exist
        existing_keys = set(AlertRule.objects.values_list("key", flat=True))
        self.assertEqual(existing_keys, set(DEFAULT_ALERT_RULES))

        # Second seed run should create 0 new rules
        created_second = seed_alert_rules()
        self.assertEqual(created_second, 0)
        self.assertEqual(AlertRule.objects.count(), 9)

    def test_alert_rule_recipients_and_days(self):
        """AlertRule supports M2M recipients and threshold days."""
        rule = AlertRule.objects.create(key="test_rule", days=7)
        rule.recipients.add(self.user1, self.user2)
        self.assertEqual(rule.recipients.count(), 2)
        self.assertEqual(rule.days, 7)
        self.assertEqual(str(rule), "test_rule")


class ItemCriticalStockTests(TestCase):
    def setUp(self):
        self.uom = UOM.objects.create(name="Kg")
        self.group = ItemGroup.objects.create(name="Raw Materials", is_group=False)

    def test_item_critical_stock_default_and_valid(self):
        """Item defaults critical_stock to 0.000 and accepts non-negative decimal."""
        item = Item.objects.create(
            code="RM-JEERA",
            name="Jeera Whole",
            item_group=self.group,
            stock_uom=self.uom,
        )
        self.assertEqual(item.critical_stock, Decimal("0.000"))

        item.critical_stock = Decimal("50.500")
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.critical_stock, Decimal("50.500"))

    def test_item_critical_stock_check_constraint_negative_fails(self):
        """Negative critical_stock violates CHECK constraint check_item_critical_stock_non_negative."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Item.objects.create(
                    code="RM-BAD",
                    name="Bad Stock",
                    item_group=self.group,
                    stock_uom=self.uom,
                    critical_stock=Decimal("-1.000"),
                )

    def test_item_serializer_exposes_critical_stock(self):
        """ItemSerializer includes critical_stock."""
        item = Item.objects.create(
            code="RM-DHANIYA",
            name="Dhaniya",
            item_group=self.group,
            stock_uom=self.uom,
            critical_stock=Decimal("25.000"),
        )
        serializer = ItemSerializer(item)
        self.assertEqual(serializer.data["critical_stock"], "25.000")
