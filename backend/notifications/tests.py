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


class OutboxSenderTests(TestCase):
    def test_queue_email_and_deduplication(self):
        from core.models import Outbox
        from notifications.services.mail import queue_email

        entry1 = queue_email(
            to="sharma@example.com",
            subject="Invoice Reminder",
            body="Your bill is due.",
            dedupe_key="bill_reminder_101",
        )
        self.assertIsNotNone(entry1.pk)
        self.assertEqual(entry1.channel, "email")
        self.assertEqual(entry1.recipient, "sharma@example.com")
        self.assertEqual(entry1.payload["subject"], "Invoice Reminder")
        self.assertEqual(entry1.status, "Pending")
        self.assertEqual(Outbox.objects.count(), 1)

        # Calling again with same dedupe_key returns the existing row and does not duplicate
        entry2 = queue_email(
            to="sharma@example.com",
            subject="Invoice Reminder",
            body="Different body",
            dedupe_key="bill_reminder_101",
        )
        self.assertEqual(entry1.pk, entry2.pk)
        self.assertEqual(Outbox.objects.count(), 1)

    def test_send_outbox_successful_dispatch(self):
        from django.core.management import call_command
        from notifications.services.mail import queue_email

        entry = queue_email(
            to="test@example.com",
            subject="Hello",
            body="World",
            dedupe_key="test_send_1",
        )
        call_command("send_outbox")
        entry.refresh_from_db()
        self.assertEqual(entry.status, "Sent")

    def test_send_outbox_failure_retries_and_dead_after_four(self):
        from datetime import timedelta
        from unittest.mock import patch
        from django.core.management import call_command
        from django.utils import timezone
        from notifications.services.mail import queue_email

        entry = queue_email(
            to="fail@example.com",
            subject="Fail Test",
            body="Body",
            dedupe_key="fail_1",
        )

        with patch("notifications.management.commands.send_outbox.send_mail", side_effect=Exception("SMTP Connection timed out")):
            # Attempt 1
            call_command("send_outbox")
            entry.refresh_from_db()
            self.assertEqual(entry.attempts, 1)
            self.assertEqual(entry.status, "Pending")
            self.assertIn("SMTP Connection timed out", entry.last_error)
            self.assertIsNotNone(entry.next_attempt_at)
            # Should be ~5 mins from now
            self.assertTrue(entry.next_attempt_at > timezone.now() + timedelta(minutes=4))

            # Simulate next_attempt_at arrived
            entry.next_attempt_at = timezone.now() - timedelta(seconds=1)
            entry.save()

            # Attempt 2
            call_command("send_outbox")
            entry.refresh_from_db()
            self.assertEqual(entry.attempts, 2)
            self.assertEqual(entry.status, "Pending")
            # Should be ~15 mins from now
            self.assertTrue(entry.next_attempt_at > timezone.now() + timedelta(minutes=14))

            # Simulate next_attempt_at arrived
            entry.next_attempt_at = timezone.now() - timedelta(seconds=1)
            entry.save()

            # Attempt 3
            call_command("send_outbox")
            entry.refresh_from_db()
            self.assertEqual(entry.attempts, 3)
            self.assertEqual(entry.status, "Pending")
            # Should be ~60 mins from now
            self.assertTrue(entry.next_attempt_at > timezone.now() + timedelta(minutes=58))

            # Simulate next_attempt_at arrived
            entry.next_attempt_at = timezone.now() - timedelta(seconds=1)
            entry.save()

            # Attempt 4: Should mark Dead
            call_command("send_outbox")
            entry.refresh_from_db()
            self.assertEqual(entry.attempts, 4)
            self.assertEqual(entry.status, "Dead")
            self.assertIsNone(entry.next_attempt_at)

    def test_one_bad_row_does_not_stop_the_rest(self):
        from unittest.mock import patch
        from django.core.management import call_command
        from notifications.services.mail import queue_email

        bad_entry = queue_email(
            to="bad@example.com",
            subject="Bad",
            body="Bad body",
            dedupe_key="bad_entry",
        )
        good_entry = queue_email(
            to="good@example.com",
            subject="Good",
            body="Good body",
            dedupe_key="good_entry",
        )

        def mock_send(subject, message, from_email, recipient_list, **kwargs):
            if "bad@example.com" in recipient_list:
                raise RuntimeError("Failed to send bad email")

        with patch("notifications.management.commands.send_outbox.send_mail", side_effect=mock_send):
            call_command("send_outbox")

        bad_entry.refresh_from_db()
        good_entry.refresh_from_db()
        self.assertEqual(bad_entry.attempts, 1)
        self.assertEqual(bad_entry.status, "Pending")
        self.assertEqual(good_entry.status, "Sent")


class RulesEngineTests(TestCase):
    def setUp(self):
        self.user_with_email = User.objects.create_user(
            username="admin_user",
            email="admin@nidhimasala.com",
            password="pass",
        )
        self.user_no_email = User.objects.create_user(
            username="no_email_user",
            email="",
            password="pass",
        )
        self.active_rule = AlertRule.objects.create(
            key="test_active_rule",
            is_active=True,
        )
        self.active_rule.recipients.add(self.user_with_email, self.user_no_email)

        self.inactive_rule = AlertRule.objects.create(
            key="test_inactive_rule",
            is_active=False,
        )
        self.inactive_rule.recipients.add(self.user_with_email)

    def test_active_rule_creates_notifications_and_outbox(self):
        from core.models import Outbox
        from notifications.services.engine import evaluate_rule

        notifs = evaluate_rule(
            rule_key="test_active_rule",
            message_key="alert.test_active",
            params={"item": "Jeera", "qty": "10"},
            link="/stock/items/1",
            dedupe_key="active_run_1",
        )

        # Creates notification for both recipients
        self.assertEqual(len(notifs), 2)
        self.assertEqual(
            Notification.objects.filter(rule_key="test_active_rule").count(), 2
        )

        # Queues email only for the user with an email address
        outbox_entries = Outbox.objects.filter(channel="email")
        self.assertEqual(outbox_entries.count(), 1)
        self.assertEqual(outbox_entries.first().recipient, "admin@nidhimasala.com")

        # Running again with same dedupe_key creates no duplicate notifications or emails
        notifs_second = evaluate_rule(
            rule_key="test_active_rule",
            message_key="alert.test_active",
            params={"item": "Jeera", "qty": "10"},
            dedupe_key="active_run_1",
        )
        self.assertEqual(len(notifs_second), 0)
        self.assertEqual(
            Notification.objects.filter(rule_key="test_active_rule").count(), 2
        )
        self.assertEqual(Outbox.objects.filter(channel="email").count(), 1)

    def test_inactive_rule_creates_nothing(self):
        from core.models import Outbox
        from notifications.services.engine import evaluate_rule

        notifs = evaluate_rule(
            rule_key="test_inactive_rule",
            message_key="alert.test_inactive",
            params={"foo": "bar"},
            dedupe_key="inactive_run_1",
        )
        self.assertEqual(len(notifs), 0)
        self.assertEqual(
            Notification.objects.filter(rule_key="test_inactive_rule").count(), 0
        )
        self.assertEqual(Outbox.objects.filter(channel="email").count(), 0)


