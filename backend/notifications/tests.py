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
        """Seeding alert rules creates the defined rules and running again adds no duplicates."""
        # The day_summary rule arrives with a data migration; start from an empty table.
        AlertRule.objects.all().delete()

        expected_count = len(DEFAULT_ALERT_RULES)
        created_first = seed_alert_rules()
        self.assertEqual(created_first, expected_count)
        self.assertEqual(AlertRule.objects.count(), expected_count)

        # All keys exist
        existing_keys = set(AlertRule.objects.values_list("key", flat=True))
        self.assertEqual(existing_keys, set(DEFAULT_ALERT_RULES))

        # Second seed run should create 0 new rules
        created_second = seed_alert_rules()
        self.assertEqual(created_second, 0)
        self.assertEqual(AlertRule.objects.count(), expected_count)

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


class RawMaterialShortageTests(TestCase):
    def setUp(self):
        from core.models import Company
        from stock.models import Warehouse

        self.user = User.objects.create_user(
            username="manager",
            email="manager@nidhimasala.com",
            password="pass",
        )
        self.rule = AlertRule.objects.create(
            key="raw_material_shortage",
            is_active=True,
        )
        self.rule.recipients.add(self.user)

        self.company = Company.objects.create(name="Nidhi Spices Pvt Ltd")
        self.wh = Warehouse.objects.create(company=self.company, name="Stores")
        self.uom = UOM.objects.create(name="Kg")
        self.group = ItemGroup.objects.create(name="Raw Materials", is_group=False)

        # Item 1: Low stock (critical: 50, actual: 20)
        self.item_low = Item.objects.create(
            code="RM-CHILLI",
            name="Red Chilli Whole",
            item_group=self.group,
            stock_uom=self.uom,
            critical_stock=Decimal("50.000"),
            is_stock_item=True,
        )
        # Item 2: Sufficient stock (critical: 30, actual: 100)
        self.item_ok = Item.objects.create(
            code="RM-TURMERIC",
            name="Turmeric Whole",
            item_group=self.group,
            stock_uom=self.uom,
            critical_stock=Decimal("30.000"),
            is_stock_item=True,
        )
        # Item 3: Zero critical stock (critical: 0, actual: 0)
        self.item_zero_crit = Item.objects.create(
            code="RM-SALT",
            name="Salt",
            item_group=self.group,
            stock_uom=self.uom,
            critical_stock=Decimal("0.000"),
            is_stock_item=True,
        )

    def test_shortage_triggers_alert_when_below_critical(self):
        from stock.models import Bin
        from notifications.services.engine import check_raw_material_shortage

        Bin.objects.create(item=self.item_low, warehouse=self.wh, actual_qty=Decimal("20.000"))
        Bin.objects.create(item=self.item_ok, warehouse=self.wh, actual_qty=Decimal("100.000"))
        Bin.objects.create(item=self.item_zero_crit, warehouse=self.wh, actual_qty=Decimal("0.000"))

        alerts = check_raw_material_shortage(company=self.company)
        self.assertEqual(len(alerts), 1)
        alert_item, qty, notifs = alerts[0]
        self.assertEqual(alert_item.id, self.item_low.id)
        self.assertEqual(qty, Decimal("20.000"))

        notif = Notification.objects.get(rule_key="raw_material_shortage")
        self.assertEqual(notif.user, self.user)
        self.assertEqual(notif.params["item"], "Red Chilli Whole")
        self.assertEqual(notif.params["qty"], "20.000")
        self.assertEqual(notif.params["critical"], "50.000")

    def test_no_alert_when_stock_at_or_above_critical(self):
        from stock.models import Bin
        from notifications.services.engine import check_raw_material_shortage

        Bin.objects.create(item=self.item_low, warehouse=self.wh, actual_qty=Decimal("50.000"))
        Bin.objects.create(item=self.item_ok, warehouse=self.wh, actual_qty=Decimal("100.000"))

        alerts = check_raw_material_shortage(company=self.company)
        self.assertEqual(len(alerts), 0)
        self.assertEqual(Notification.objects.filter(rule_key="raw_material_shortage").count(), 0)


class InstantAlertsTests(TestCase):
    def setUp(self):
        from core.models import Company
        from stock.models import Warehouse

        self.user = User.objects.create_user(
            username="admin_user_instant",
            email="admin@nidhimasala.com",
            password="pass",
        )
        self.company = Company.objects.create(name="Nidhi Spices Pvt Ltd")
        self.wh_ready = Warehouse.objects.create(company=self.company, name="Finished Goods")
        self.company.ready_warehouse = self.wh_ready
        self.company.save()

        self.uom = UOM.objects.create(name="Nos")
        self.group = ItemGroup.objects.create(name="Finished Goods", is_group=False)
        self.item = Item.objects.create(
            code="FG-HALDI-500",
            name="Haldi 500g",
            item_group=self.group,
            stock_uom=self.uom,
            critical_stock=Decimal("100.000"),
            is_stock_item=True,
        )

        self.rule_ready_low = AlertRule.objects.create(key="ready_low", is_active=True)
        self.rule_ready_low.recipients.add(self.user)

        self.rule_integrity = AlertRule.objects.create(key="integrity_failed", is_active=True)
        self.rule_integrity.recipients.add(self.user)

        self.rule_large_order = AlertRule.objects.create(key="large_order", is_active=True)
        self.rule_large_order.recipients.add(self.user)

    def test_ready_low_triggers_and_second_drop_dedupes(self):
        from stock.models import Bin
        from notifications.services.instant import check_item_ready_low

        # Bin has 50 units (critical: 100) -> triggers ready_low
        Bin.objects.create(item=self.item, warehouse=self.wh_ready, actual_qty=Decimal("50.000"))

        notifs = check_item_ready_low(self.item, company=self.company)
        self.assertIsNotNone(notifs)
        self.assertEqual(Notification.objects.filter(rule_key="ready_low").count(), 1)
        notif = Notification.objects.filter(rule_key="ready_low").first()
        self.assertEqual(notif.params["item"], "Haldi 500g")
        self.assertEqual(notif.params["qty"], "50.000")

        # Second drop on same day does not duplicate
        notifs_second = check_item_ready_low(self.item, company=self.company)
        self.assertEqual(len(notifs_second), 0)
        self.assertEqual(Notification.objects.filter(rule_key="ready_low").count(), 1)

    def test_integrity_failed_triggers_alert(self):
        from notifications.services.instant import on_integrity_failed

        on_integrity_failed(["GL Imbalance error 1", "Bin drift error 2"])
        notif = Notification.objects.filter(rule_key="integrity_failed").first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.params["error_count"], "2")
        self.assertIn("GL Imbalance error 1", notif.params["summary"])

    def test_large_order_triggers_above_50k(self):
        from notifications.services.instant import on_sales_order_submit
        from unittest.mock import MagicMock

        # > 50,000 triggers
        order_large = MagicMock()
        order_large.id = 101
        order_large.name = "SO-101"
        order_large.grand_total = Decimal("55000.00")
        order_large.customer = MagicMock(name="Sharma Traders")
        order_large.customer.name = "Sharma Traders"

        on_sales_order_submit(order_large)
        self.assertEqual(Notification.objects.filter(rule_key="large_order").count(), 1)

        # <= 50,000 does not trigger
        order_small = MagicMock()
        order_small.id = 102
        order_small.name = "SO-102"
        order_small.grand_total = Decimal("50000.00")
        order_small.customer = MagicMock(name="Gupta Store")
        order_small.customer.name = "Gupta Store"

        on_sales_order_submit(order_small)
        self.assertEqual(Notification.objects.filter(rule_key="large_order").count(), 1)


class DigestTests(TestCase):
    def setUp(self):
        from core.models import Company

        self.user = User.objects.create_user(
            username="digest_admin",
            email="admin@nidhimasala.com",
            password="pass",
        )
        self.company = Company.objects.create(name="Nidhi Spices Pvt Ltd")

        self.r1 = AlertRule.objects.create(key="overdue_bills_digest", is_active=True)
        self.r1.recipients.add(self.user)

        self.r2 = AlertRule.objects.create(key="daily_summary", is_active=True)
        self.r2.recipients.add(self.user)

        self.r3 = AlertRule.objects.create(key="weekly_digest", is_active=True)
        self.r3.recipients.add(self.user)

    def test_overdue_bills_digest(self):
        from notifications.services.digests import send_overdue_bills_digest

        send_overdue_bills_digest(self.company)
        self.assertEqual(
            Notification.objects.filter(rule_key="overdue_bills_digest").count(), 1
        )
        notif = Notification.objects.filter(rule_key="overdue_bills_digest").first()
        self.assertEqual(notif.params["count"], "0")
        self.assertEqual(notif.params["total_amount"], "0.00")

        # Running again today does not duplicate
        send_overdue_bills_digest(self.company)
        self.assertEqual(
            Notification.objects.filter(rule_key="overdue_bills_digest").count(), 1
        )

    def test_daily_summary(self):
        from notifications.services.digests import send_daily_summary

        send_daily_summary(self.company)
        self.assertEqual(
            Notification.objects.filter(rule_key="daily_summary").count(), 1
        )
        notif = Notification.objects.filter(rule_key="daily_summary").first()
        self.assertEqual(notif.params["money_in"], "0.00")
        self.assertEqual(notif.params["bills_count"], "0")

        # Running again today does not duplicate
        send_daily_summary(self.company)
        self.assertEqual(
            Notification.objects.filter(rule_key="daily_summary").count(), 1
        )

    def test_weekly_digest(self):
        from notifications.services.digests import send_weekly_digest

        send_weekly_digest(self.company)
        self.assertEqual(
            Notification.objects.filter(rule_key="weekly_digest").count(), 1
        )
        notif = Notification.objects.filter(rule_key="weekly_digest").first()
        self.assertEqual(notif.params["collections_needed"], "0.00")
        self.assertIn("GST", notif.params["tax_due_info"])

        # Running again today does not duplicate
        send_weekly_digest(self.company)
        self.assertEqual(
            Notification.objects.filter(rule_key="weekly_digest").count(), 1
        )


class SchedulerTests(TestCase):
    def test_run_scheduler_once_runs_cleanly(self):
        from django.core.management import call_command
        from io import StringIO

        out = StringIO()
        call_command("run_scheduler", "--once", "--force-weekly", stdout=out)
        output = out.getvalue()
        self.assertIn("Scheduler pass complete", output)

    def test_job_failure_does_not_stop_next_jobs(self):
        from notifications.management.commands.run_scheduler import safe_execute

        def failing_job():
            raise RuntimeError("Database connection glitch")

        def successful_job():
            return "ok"

        res1 = safe_execute("failing_job", failing_job)
        res2 = safe_execute("successful_job", successful_job)

        self.assertIsNone(res1)
        self.assertEqual(res2, "ok")


class NotificationAPITests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username="admin_api",
            email="admin@nidhimasala.com",
            password="pass",
        )
        self.staff1 = User.objects.create_user(
            username="staff1",
            email="staff1@example.com",
            password="pass",
        )
        self.staff2 = User.objects.create_user(
            username="staff2",
            email="staff2@example.com",
            password="pass",
        )

        self.n1 = Notification.objects.create(
            user=self.staff1,
            rule_key="raw_material_shortage",
            message_key="alert.raw_material_shortage",
            params={"item": "Jeera"},
            link="/stock/items/1",
            dedupe_key="notif_1",
        )
        self.n2 = Notification.objects.create(
            user=self.staff2,
            rule_key="large_order",
            message_key="alert.large_order",
            params={"order": "SO-1"},
            link="/selling/orders/1",
            dedupe_key="notif_2",
        )
        self.rule = AlertRule.objects.create(
            key="raw_material_shortage",
            is_active=True,
            days=5,
        )

    def test_list_and_unread_count_isolated(self):
        self.client.force_authenticate(user=self.staff1)
        res = self.client.get("/api/notifications/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        results = data["results"] if isinstance(data, dict) and "results" in data else data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.n1.id)

        res_count = self.client.get("/api/notifications/unread-count/")
        self.assertEqual(res_count.status_code, 200)
        self.assertEqual(res_count.json()["count"], 1)

    def test_cannot_read_other_users_notification(self):
        self.client.force_authenticate(user=self.staff1)
        # Attempting to read staff2's notification returns 404
        res = self.client.post(f"/api/notifications/{self.n2.id}/read/")
        self.assertEqual(res.status_code, 404)

        # Reading own notification succeeds
        res_own = self.client.post(f"/api/notifications/{self.n1.id}/read/")
        self.assertEqual(res_own.status_code, 200)
        self.n1.refresh_from_db()
        self.assertIsNotNone(self.n1.read_at)

    def test_read_all(self):
        self.client.force_authenticate(user=self.staff1)
        res = self.client.post("/api/notifications/read-all/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "all_read")
        self.n1.refresh_from_db()
        self.assertIsNotNone(self.n1.read_at)

        res_count = self.client.get("/api/notifications/unread-count/")
        self.assertEqual(res_count.json()["count"], 0)

    def test_alert_rule_admin_permissions(self):
        # Non-admin cannot update alert rule
        self.client.force_authenticate(user=self.staff1)
        res_patch = self.client.patch(
            f"/api/notifications/rules/{self.rule.id}/",
            {"is_active": False, "days": 10},
            format="json",
        )
        self.assertEqual(res_patch.status_code, 403)

        # Admin can update alert rule
        self.client.force_authenticate(user=self.admin)
        res_admin = self.client.patch(
            f"/api/notifications/rules/{self.rule.id}/",
            {"is_active": False, "days": 10, "recipients": [self.staff1.id]},
            format="json",
        )
        self.assertEqual(res_admin.status_code, 200)
        self.rule.refresh_from_db()
        self.assertFalse(self.rule.is_active)
        self.assertEqual(self.rule.days, 10)
        self.assertIn(self.staff1, self.rule.recipients.all())







