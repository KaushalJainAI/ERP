"""Unit and API tests for the AI assistant app.

Verifies:
1. No-write guarantee (CaptureQueriesContext asserts 0 write queries on non-assistant tables).
2. RBAC enforcement (user without permissions is denied).
3. Serializer dry-run validation (errors turn into warnings, no rows created).
4. Save tagging via X-Assistant-Proposal header (AuditLog + Proposal updated).
5. Model CHECK constraints (role, input_mode, status, saved fields).
6. Chat stream endpoint SSE events and error handling.
"""

import datetime
from decimal import Decimal
import json
import uuid

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Account, AccountRootType, AccountType
from core.models import AuditLog, Company, State
from selling.models import Customer, CustomerGroup, SalesInvoice
from buying.models import Supplier, SupplierGroup, PurchaseInvoice
from stock.models import Item, ItemGroup, UOM, Warehouse, Bin, ValuationMethod
from hr.models import Employee
from users.models import Role, RolePermission, UserRole

from .models import AssistantConversation, AssistantMessage, AssistantProposal
from .tools import (
    READ_TOOLS,
    search_customers,
    search_suppliers,
    search_items,
    search_employees,
    get_stock,
    get_party_outstanding,
    get_rate,
    wrap_data,
)
from .proposals import (
    propose_sales_invoice,
    propose_purchase_invoice,
    propose_payment_entry,
)
from .agent import Agent

User = get_user_model()


def setup_test_masters(test_instance):
    """Create shared baseline masters needed for documents and tests."""
    test_instance.company = Company.objects.create(
        name="Acme Spices " + str(uuid.uuid4())[:8],
        abbr="AC" + str(uuid.uuid4())[:4],
        default_currency="INR",
    )
    test_instance.uom = UOM.objects.create(name="Kg-" + str(uuid.uuid4())[:6])
    test_instance.item_grp = ItemGroup.objects.create(name="Spices-" + str(uuid.uuid4())[:6], is_group=True)
    test_instance.cust_grp = CustomerGroup.objects.create(name="Standard-" + str(uuid.uuid4())[:6], is_group=True)
    test_instance.supp_grp = SupplierGroup.objects.create(name="Raw-" + str(uuid.uuid4())[:6], is_group=True)

    # Accounts
    test_instance.asset_root = Account.objects.create(
        name="Assets-" + str(uuid.uuid4())[:6],
        company=test_instance.company,
        root_type=AccountRootType.ASSET,
        is_group=True,
    )
    test_instance.debtors_acc = Account.objects.create(
        name="Debtors-" + str(uuid.uuid4())[:6],
        company=test_instance.company,
        parent=test_instance.asset_root,
        root_type=AccountRootType.ASSET,
        account_type=AccountType.RECEIVABLE,
    )
    test_instance.income_root = Account.objects.create(
        name="Income-" + str(uuid.uuid4())[:6],
        company=test_instance.company,
        root_type=AccountRootType.INCOME,
        is_group=True,
    )
    test_instance.sales_acc = Account.objects.create(
        name="Sales-" + str(uuid.uuid4())[:6],
        company=test_instance.company,
        parent=test_instance.income_root,
        root_type=AccountRootType.INCOME,
    )

    test_instance.company.default_receivable_account = test_instance.debtors_acc
    test_instance.company.default_income_account = test_instance.sales_acc
    test_instance.company.save()

    test_instance.customer = Customer.objects.create(
        name="Sharma Traders",
        company=test_instance.company,
        customer_group=test_instance.cust_grp,
        is_active=True,
    )
    test_instance.supplier = Supplier.objects.create(
        name="Gupta Suppliers",
        company=test_instance.company,
        supplier_group=test_instance.supp_grp,
        is_active=True,
    )
    test_instance.item = Item.objects.create(
        code="HALDI-" + str(uuid.uuid4())[:4],
        name="Haldi Powder",
        item_group=test_instance.item_grp,
        stock_uom=test_instance.uom,
        is_active=True,
        standard_rate=Decimal("150.00"),
    )
    test_instance.wh = Warehouse.objects.create(
        name="Main Store " + str(uuid.uuid4())[:6],
        company=test_instance.company,
    )
    Bin.objects.create(item=test_instance.item, warehouse=test_instance.wh, actual_qty=Decimal("50.000"))


class AssistantModelConstraintTests(TestCase):
    """Test database CHECK constraints on assistant models."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@erp.local",
            password="password123",
        )
        self.conversation = AssistantConversation.objects.create(
            user=self.user,
            title="Test Thread",
        )

    def test_assistant_message_role_constraint(self):
        """Invalid role must violate CHECK constraint."""
        with self.assertRaises(Exception):
            with transaction.atomic():
                AssistantMessage.objects.create(
                    conversation=self.conversation,
                    role="invalid_role",
                    content="Hello",
                )

    def test_assistant_message_input_mode_constraint(self):
        """Invalid input_mode must violate CHECK constraint."""
        with self.assertRaises(Exception):
            with transaction.atomic():
                AssistantMessage.objects.create(
                    conversation=self.conversation,
                    role="user",
                    input_mode="telepathy",
                    content="Hello",
                )

    def test_assistant_proposal_saved_constraint(self):
        """Status=saved requires saved_model_label and saved_object_id."""
        with self.assertRaises(Exception):
            with transaction.atomic():
                AssistantProposal.objects.create(
                    target="selling.SalesInvoice",
                    status="saved",
                    saved_model_label=None,
                    saved_object_id=None,
                )

        with self.assertRaises(Exception):
            with transaction.atomic():
                AssistantProposal.objects.create(
                    target="selling.SalesInvoice",
                    status="proposed",
                    saved_model_label="selling.salesinvoice",
                    saved_object_id="1",
                )

        prop = AssistantProposal.objects.create(
            target="selling.SalesInvoice",
            status="saved",
            saved_model_label="selling.salesinvoice",
            saved_object_id="1",
        )
        self.assertEqual(prop.status, "saved")


class AssistantNoWriteGuaranteeTests(TestCase):
    """Verify that every read tool performs zero non-assistant INSERT/UPDATE/DELETE queries."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="adminuser",
            email="admin@erp.local",
            password="adminpassword",
        )
        setup_test_masters(self)
        self.employee = Employee.objects.create(
            name="EMP-001",
            first_name="Ramesh",
            last_name="Kumar",
            company=self.company,
            date_of_joining=datetime.date(2026, 1, 1),
            status="Active",
        )

    def test_read_tools_zero_database_writes(self):
        """All registered read tools must never execute INSERT/UPDATE/DELETE."""
        test_calls = [
            lambda: search_customers(self.user, "Sharma"),
            lambda: search_suppliers(self.user, "Gupta"),
            lambda: search_items(self.user, "Haldi"),
            lambda: search_employees(self.user, "Ramesh"),
            lambda: get_stock(self.user, self.item.code),
            lambda: get_party_outstanding(self.user, "Customer", self.customer.pk),
            lambda: get_rate(self.user, self.item.code, "Customer", self.customer.pk),
        ]

        for call in test_calls:
            with CaptureQueriesContext(connection) as ctx:
                res = call()
                for q in ctx.captured_queries:
                    sql_upper = q["sql"].strip().upper()
                    self.assertFalse(sql_upper.startswith("INSERT"), f"Found forbidden INSERT query: {sql_upper}")
                    self.assertFalse(sql_upper.startswith("UPDATE"), f"Found forbidden UPDATE query: {sql_upper}")
                    self.assertFalse(sql_upper.startswith("DELETE"), f"Found forbidden DELETE query: {sql_upper}")

    def test_prompt_injection_spotlighting(self):
        """All user and record strings are enclosed in <<DATA>>."""
        res = search_customers(self.user, "Sharma")
        self.assertTrue(len(res["results"]) > 0)
        self.assertIn("<<DATA>>Sharma Traders<</DATA>>", res["results"][0]["name"])


class AssistantRBACTests(TestCase):
    """Verify that read tools and proposal builders strictly enforce RBAC."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="restricted",
            email="restricted@erp.local",
            password="password123",
        )
        setup_test_masters(self)
        self.employee = Employee.objects.create(
            name="EMP-002",
            first_name="Sita",
            company=self.company,
            date_of_joining=datetime.date(2026, 1, 1),
            status="Active",
        )

    def test_search_employees_denied_without_read_permission(self):
        """User without can_read on hr.Employee gets permission_denied."""
        res = search_employees(self.user, "Sita")
        self.assertEqual(res.get("error"), "permission_denied")

    def test_propose_sales_invoice_denied_without_create_permission(self):
        """User without can_create on selling.SalesInvoice gets permission_denied."""
        res = propose_sales_invoice(
            self.user,
            customer_query="Sharma",
            items=[{"item": "Haldi", "qty": 1}],
        )
        self.assertEqual(res.get("error"), "permission_denied")


class AssistantProposalBuilderTests(TestCase):
    """Test proposal builder dry-run validations and ambiguity handling."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@erp.local",
            password="adminpassword",
        )
        setup_test_masters(self)

    def test_sales_invoice_proposal_dry_run_success(self):
        """Proposal builds without creating SalesInvoice row in DB."""
        initial_count = SalesInvoice.objects.count()

        res = propose_sales_invoice(
            self.user,
            customer_query="Sharma",
            items=[{"item": "Haldi", "qty": 10, "rate": 180}],
        )

        self.assertEqual(res.get("type"), "proposal")
        self.assertEqual(SalesInvoice.objects.count(), initial_count)
        self.assertEqual(res["target"], "selling.SalesInvoice")
        self.assertIn("route", res)
        self.assertIn("values", res)
        self.assertEqual(res["values"]["customer"], self.customer.pk)

    def test_ambiguity_returns_question_not_proposal(self):
        """When multiple customers match query, return question tool result."""
        Customer.objects.create(
            name="Sharma Enterprises",
            company=self.company,
            customer_group=self.cust_grp,
            is_active=True,
        )

        res = propose_sales_invoice(
            self.user,
            customer_query="Sharma",
            items=[{"item": "Haldi", "qty": 1}],
        )

        self.assertEqual(res.get("type"), "question")
        self.assertIn("Which customer", res.get("question"))
        self.assertEqual(len(res.get("options")), 2)

    def test_purchase_invoice_bill_total_mismatch_warning(self):
        """Stated bill total differing from items total produces warning."""
        res = propose_purchase_invoice(
            self.user,
            supplier_query="Gupta",
            items=[{"item": "Haldi", "qty": 10, "rate": 100}],
            bill_total=1500,
        )

        self.assertEqual(res.get("type"), "proposal")
        self.assertTrue(any("Bill total mismatch" in w for w in res.get("warnings", [])))


class AssistantSaveTaggingTests(APITestCase):
    """Test X-Assistant-Proposal header linking and AuditLog source tagging."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="admin",
            email="admin@erp.local",
            password="adminpassword",
        )
        self.client.force_authenticate(user=self.user)
        setup_test_masters(self)

        self.conversation = AssistantConversation.objects.create(user=self.user)
        self.message = AssistantMessage.objects.create(conversation=self.conversation, role="assistant")
        self.proposal = AssistantProposal.objects.create(
            message=self.message,
            target="selling.SalesInvoice",
            route="/selling/sales-invoices/new",
            values={},
            status="proposed",
        )

    def test_save_with_valid_proposal_tags_audit_and_marks_saved(self):
        """Creating document with X-Assistant-Proposal marks proposal saved and sets AuditLog."""
        payload = {
            "customer": self.customer.pk,
            "company": self.company.pk,
            "posting_date": "2026-10-29",
            "due_date": "2026-10-29",
            "items": [
                {
                    "item": self.item.pk,
                    "qty": "5.000",
                    "rate": "200.00",
                    "warehouse": self.wh.pk,
                }
            ],
        }

        resp = self.client.post(
            "/api/selling/sales-invoices/",
            payload,
            format="json",
            headers={"X-Assistant-Proposal": str(self.proposal.id)},
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, "saved")
        self.assertEqual(self.proposal.saved_model_label, "selling.SalesInvoice")
        self.assertEqual(self.proposal.saved_object_id, str(resp.data["name"]))

        audit = AuditLog.objects.filter(
            model_label="selling.SalesInvoice",
            object_id=str(resp.data["name"]),
            action="create",
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.changes.get("source"), "assistant")
        self.assertEqual(audit.changes.get("proposal"), str(self.proposal.id))

    def test_invalid_or_foreign_proposal_id_does_not_block_save(self):
        """Bad proposal UUID does not crash document creation."""
        payload = {
            "customer": self.customer.pk,
            "company": self.company.pk,
            "posting_date": "2026-10-29",
            "due_date": "2026-10-29",
            "items": [
                {
                    "item": self.item.pk,
                    "qty": "2.000",
                    "rate": "200.00",
                    "warehouse": self.wh.pk,
                }
            ],
        }

        resp = self.client.post(
            "/api/selling/sales-invoices/",
            payload,
            format="json",
            headers={"X-Assistant-Proposal": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)


class AssistantChatStreamAPITests(APITestCase):
    """Test /api/assistant/chat/stream/ and conversation endpoints."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="chatuser",
            email="chat@erp.local",
            password="chatpassword",
        )
        self.client.force_authenticate(user=self.user)

    def test_conversations_owner_isolation(self):
        """User cannot read another user's conversation."""
        other_user = User.objects.create_user(username="other", email="other@erp.local", password="password")
        other_conv = AssistantConversation.objects.create(user=other_user, title="Secret")

        resp = self.client.get(f"/api/assistant/conversations/{other_conv.pk}/messages/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_chat_stream_mock_completion(self):
        """Chat stream endpoint emits SSE events and terminates with done."""
        request_data = {
            "message": "Sharma ko 10 kilo haldi becha",
            "language": "hi",
        }

        resp = self.client.post("/api/assistant/chat/stream/", request_data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "text/event-stream")

        stream_content = b"".join(resp.streaming_content).decode("utf-8")
        self.assertIn("event: meta", stream_content)
        self.assertIn("event: done", stream_content)
