import datetime
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework import serializers
from core.models import (
    AuditLog,
    Company,
    DocStatus,
    DummySubmittableDoc,
    FieldTranslation,
    FiscalYear,
    Language,
    NamingSeries,
    Outbox,
    SystemHealthCheck,
)
from core.mixins import TranslatedSerializerMixin, get_active_language
from users.models import User, Role
from stock.models import UOM, ItemGroup
from selling.models import CustomerGroup
from buying.models import SupplierGroup
from accounts.models import Account



class DummySerializer(TranslatedSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ("id", "name", "abbr", "country")
        translatable_fields = ["name"]


class CoreModelTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Acme Corp",
            abbr="ACME",
            default_currency="INR",
        )

    # -------------------------------------------------------------------------
    # Good Unit Tests
    # -------------------------------------------------------------------------

    def test_company_creation_success(self):
        """GOOD: Creating a valid company succeeds with default currency INR."""
        c = Company.objects.create(name="Beta Ltd", abbr="BETA")
        self.assertEqual(c.default_currency, "INR")
        self.assertEqual(c.country, "India")
        self.assertTrue(c.is_active)

    def test_submittable_document_lifecycle(self):
        """GOOD: Submittable document state transitions (Draft -> Submitted -> Cancelled -> Amend)."""
        doc = DummySubmittableDoc.objects.create(name="DOC-001", title="First Invoice", docstatus=DocStatus.DRAFT)
        self.assertEqual(doc.docstatus, DocStatus.DRAFT)

        # Submit
        doc.submit()
        self.assertEqual(doc.docstatus, DocStatus.SUBMITTED)

        # Cancel
        doc.cancel()
        self.assertEqual(doc.docstatus, DocStatus.CANCELLED)

        # Amend
        amended = doc.amend(new_name="DOC-002")
        amended.save()
        self.assertEqual(amended.docstatus, DocStatus.DRAFT)
        self.assertEqual(amended.amended_from, doc)
        self.assertEqual(amended.title, "First Invoice")

    def test_fiscal_year_valid(self):
        """GOOD: FiscalYear with end_date > start_date saves properly."""
        fy = FiscalYear(
            name="2026-2027",
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2027, 3, 31),
        )
        fy.full_clean()
        fy.save()
        self.assertEqual(FiscalYear.objects.filter(name="2026-2027").count(), 1)

    def test_naming_series_atomic_next(self):
        """GOOD: NamingSeries.next generates sequential zero-padded names."""
        n1 = NamingSeries.next("TEST-2026-", padding=4)
        n2 = NamingSeries.next("TEST-2026-", padding=4)
        self.assertEqual(n1, "TEST-2026-0001")
        self.assertEqual(n2, "TEST-2026-0002")

    def test_audit_log_creation_success(self):
        """GOOD: AuditLog record is created successfully."""
        log = AuditLog.objects.create(
            action="create",
            model_label="core.Company",
            object_id="ACME",
            changes={"abbr": "ACME"},
        )
        self.assertIsNotNone(log.pk)
        self.assertEqual(log.action, "create")

    def test_outbox_deduplication(self):
        """GOOD: Outbox entry created with unique dedupe_key."""
        out = Outbox.objects.create(
            dedupe_key="EMAIL-INV-001",
            channel="email",
            payload={"to": "test@example.com"},
        )
        self.assertEqual(out.status, "pending")

    def test_language_and_field_translation(self):
        """GOOD: Language creation and FieldTranslation dynamic lookup."""
        lang = Language.objects.create(code="hi", name="Hindi", is_rtl=False)
        self.assertEqual(lang.code, "hi")

        ft = FieldTranslation.objects.create(
            model_label="core.Company",
            object_id=self.company.pk,
            field_name="name",
            language="hi",
            value="एक्मे कॉर्प",
        )
        self.assertIsNotNone(ft.pk)

    def test_language_resolution_hierarchy(self):
        """GOOD: Language resolution resolves user.language -> Accept-Language -> 'en'."""
        user = User.objects.create_user(username="u1", email="u1@test.com", language="ta")
        request = type("Request", (), {"user": user, "META": {"HTTP_ACCEPT_LANGUAGE": "hi-IN,hi;q=0.9"}})()
        self.assertEqual(get_active_language(request), "ta")

        # Unauthenticated user uses Accept-Language header
        request_anon = type("Request", (), {"user": None, "META": {"HTTP_ACCEPT_LANGUAGE": "hi-IN,hi;q=0.9"}})()
        self.assertEqual(get_active_language(request_anon), "hi")

        # Fallback to English
        request_none = type("Request", (), {"user": None, "META": {}})()
        self.assertEqual(get_active_language(request_none), "en")

    def test_translated_serializer_mixin(self):
        """GOOD: TranslatedSerializerMixin replaces field with translation when active lang != 'en'."""
        FieldTranslation.objects.create(
            model_label="core.Company",
            object_id=self.company.pk,
            field_name="name",
            language="hi",
            value="एक्मे कॉर्प",
        )
        user = User.objects.create_user(username="u2", email="u2@test.com", language="hi")
        request = type("Request", (), {"user": user, "META": {}})()

        serializer = DummySerializer(self.company, context={"request": request})
        data = serializer.data
        self.assertEqual(data["name"], "एक्मे कॉर्प")

    def test_seed_defaults_command_idempotent(self):
        """GOOD: seed_defaults runs cleanly multiple times and creates all initial default records."""
        from django.core.management import call_command
        call_command("seed_defaults")
        call_command("seed_defaults")

        self.assertEqual(Company.objects.filter(abbr="ACME").count(), 1)
        self.assertEqual(FiscalYear.objects.filter(name="2026-2027").count(), 1)
        self.assertEqual(Language.objects.count(), 12)  # 12 Indian + English = 12 in list + en
        self.assertTrue(Role.objects.filter(name="Default Role").exists())
        self.assertTrue(UOM.objects.filter(name="Nos").exists())
        self.assertTrue(Account.objects.filter(name="Bank Account").exists())
        self.assertTrue(ItemGroup.objects.filter(name="All Item Groups").exists())
        self.assertTrue(CustomerGroup.objects.filter(name="All Customer Groups").exists())
        self.assertTrue(SupplierGroup.objects.filter(name="All Supplier Groups").exists())

    # -------------------------------------------------------------------------
    # Bad / Error Unit Tests
    # -------------------------------------------------------------------------

    def test_company_duplicate_name_fails(self):
        """BAD: Duplicate company name or abbr violates UNIQUE constraint."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Company.objects.create(name="Acme Corp", abbr="ACME2")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Company.objects.create(name="Acme Corp 2", abbr="ACME")

    def test_fiscal_year_invalid_dates_fails(self):
        """BAD: FiscalYear where end_date <= start_date raises ValidationError."""
        fy = FiscalYear(
            name="BadYear",
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2026, 3, 31),
        )
        with self.assertRaises(ValidationError):
            fy.full_clean()

    def test_submittable_document_illegal_transitions_and_edit_lock(self):
        """BAD: Illegal state transitions or editing submitted/cancelled doc raises ValidationError."""
        doc = DummySubmittableDoc.objects.create(name="DOC-BAD-01", title="Draft Doc", docstatus=DocStatus.DRAFT)

        # BAD: Cannot cancel a draft doc
        with self.assertRaises(ValidationError):
            doc.cancel()

        # BAD: Cannot amend a draft doc
        with self.assertRaises(ValidationError):
            doc.amend(new_name="DOC-BAD-02")

        # Submit doc
        doc.submit()

        # BAD: Cannot submit an already submitted doc
        with self.assertRaises(ValidationError):
            doc.submit()

        # BAD: Cannot edit submitted document fields
        doc.title = "Modified Title"
        with self.assertRaises(ValidationError):
            doc.save()

        # BAD: Cannot delete submitted document
        with self.assertRaises(ValidationError):
            doc.delete()

    def test_audit_log_update_and_delete_forbidden(self):
        """BAD: AuditLog rows are append-only; updating or deleting raises ValidationError."""
        log = AuditLog.objects.create(
            action="create",
            model_label="core.Company",
            object_id="ACME",
        )
        with self.assertRaises(ValidationError):
            log.save()

        with self.assertRaises(ValidationError):
            log.delete()

    def test_outbox_duplicate_dedupe_key_fails(self):
        """BAD: Duplicate dedupe_key violates UNIQUE constraint."""
        Outbox.objects.create(dedupe_key="KEY-123", channel="sms")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Outbox.objects.create(dedupe_key="KEY-123", channel="email")

    def test_duplicate_field_translation_fails(self):
        """BAD: Duplicate FieldTranslation for same model, object, field, language violates UNIQUE constraint."""
        FieldTranslation.objects.create(
            model_label="core.Company",
            object_id=self.company.pk,
            field_name="name",
            language="hi",
            value="एक्मे 1",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FieldTranslation.objects.create(
                    model_label="core.Company",
                    object_id=self.company.pk,
                    field_name="name",
                    language="hi",
                    value="एक्मे 2",
                )

    def test_verify_integrity_and_repost_self_healing_flow(self):
        """GOOD: verify_integrity detects Bin drifts and repost_stock restores ledger consistency."""
        from django.core.management import call_command, CommandError
        from stock.models import UOM, ItemGroup, Item, Warehouse, Bin
        from stock.services import ledger as stock_service

        uom = UOM.objects.create(name="Boxes")
        group = ItemGroup.objects.create(name="Finished Goods", is_group=True)
        item = Item.objects.create(code="FG-001", name="Widget A", item_group=group, stock_uom=uom)
        wh = Warehouse.objects.create(name="Finished Goods Store", company=self.company)
        doc = DummySubmittableDoc.objects.create(name="REC-001", title="Receipt", docstatus=DocStatus.SUBMITTED)

        # Post SLE (+50 qty)
        stock_service.post(doc, [{"item": item, "warehouse": wh, "qty": Decimal("50.000"), "valuation_rate": Decimal("10.00")}], posting_date=datetime.date(2026, 4, 1))

        # Initial verify passes
        call_command("verify_integrity")

        # Corrupt Bin
        bin_obj = Bin.objects.get(item=item, warehouse=wh)
        bin_obj.actual_qty = Decimal("999.000")
        bin_obj.save()

        # verify_integrity raises CommandError due to drift
        with self.assertRaises(CommandError):
            call_command("verify_integrity")

        # Self-healing repost restores Bin
        call_command("repost_stock", item=item.id, warehouse=wh.id)

        # verify_integrity passes again
        call_command("verify_integrity")

