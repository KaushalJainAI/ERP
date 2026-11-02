"""Alert emails are readable sentences in the recipient's language."""

from django.test import TestCase, override_settings
from django.utils import translation

from core.models import Outbox
from notifications.messages import EMAILS, render_email
from notifications.models import AlertRule
from notifications.seed import DEFAULT_ALERT_RULES
from notifications.services.engine import evaluate_rule
from users.models import User


@override_settings(FRONTEND_URL="https://erp.example.com")
class RenderEmailTests(TestCase):
    def setUp(self):
        translation.trans_real._translations = {}  # pick up a fresh .mo

    def test_english_sentence_and_absolute_link(self):
        subject, body = render_email(
            "backup_failed", {"error": "disk full"}, "/health", language="en"
        )
        self.assertEqual(subject, "ERP: Backup failed")
        self.assertIn("database backup did not complete: disk full", body)
        self.assertIn("https://erp.example.com/health", body)

    def test_hindi_for_a_hindi_user(self):
        subject, body = render_email(
            "payment_delay",
            {
                "invoice": "SINV-1",
                "customer": "Sharma",
                "outstanding": "500.00",
                "days_unpaid": "95",
            },
            language="hi",
        )
        self.assertEqual(subject, "ERP: भुगतान में देरी")
        self.assertIn("SINV-1, Sharma: ₹500.00, 95 दिन से बाकी", body)

    def test_missing_parameter_falls_back_instead_of_crashing(self):
        subject, body = render_email("payment_delay", {"invoice": "SINV-1"})
        self.assertEqual(subject, "ERP: payment_delay")
        self.assertIn("invoice: SINV-1", body)

    def test_every_seeded_rule_has_an_email(self):
        self.assertEqual(set(DEFAULT_ALERT_RULES) - set(EMAILS), set())

    def test_evaluate_rule_queues_the_readable_email(self):
        user = User.objects.create_user(
            username="acct", email="acct@example.com", password="x-Strong-pass-1"
        )
        rule = AlertRule.objects.create(key="backup_failed", is_active=True)
        rule.recipients.add(user)
        evaluate_rule(
            "backup_failed", "alert.backup_failed", {"error": "disk full"}, "/health"
        )
        mail = Outbox.objects.get(payload__recipient="acct@example.com")
        self.assertEqual(mail.payload["subject"], "ERP: Backup failed")
        self.assertNotIn("Rule:", mail.payload["body"])
