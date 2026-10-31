"""Unit and API tests for the AI assistant app.

Verifies:
1. No-write guarantee (CaptureQueriesContext asserts 0 write queries on non-assistant tables).
2. RBAC enforcement (user without permissions is denied).
3. Serializer dry-run validation (errors turn into warnings, no rows created).
4. Save tagging via X-Assistant-Proposal header (AuditLog + Proposal updated).
5. Model CHECK constraints (role, input_mode, status, saved fields).
6. Chat stream endpoint SSE events and error handling.
7. Matching: exact wins, close spellings are only ever offered, company is enforced.
8. Agent loop: a question ends the turn, one proposal per turn, the turn budget.

No test here reaches a real LLM: every Agent gets a stubbed completion_fn.
"""

import datetime
from decimal import Decimal
import inspect
import json
from unittest import mock
import uuid

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
import requests
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Account, AccountRootType, AccountType, PaymentEntry
from core.models import AuditLog, Company
from selling.models import Customer, CustomerGroup, SalesInvoice, SalesOrder
from buying.models import ExpenseCategory, PurchaseInvoice, Supplier, SupplierGroup
from stock.models import Item, ItemGroup, UOM, Warehouse, Bin
from hr.models import Employee

from .models import AssistantConversation, AssistantMessage, AssistantProposal
from .tools import (
    search_customers,
    search_suppliers,
    search_items,
    search_employees,
    get_stock,
    get_party_outstanding,
    get_rate,
    mask_phone,
    sales_summary,
    bills_to_pay,
    expense_summary_tool,
    gst_position_tool,
    money_to_collect,
    navigate,
    pending_orders,
    stock_stages,
    top_items,
    _stock_warehouse,
)
from .proposals import (
    PROPOSAL_BUILDERS,
    propose_expense,
    propose_payment_entry,
    propose_receipt,
    propose_sales_order,
    propose_sales_invoice,
    propose_purchase_invoice,
)
from .transliterate import to_latin
from .aliases import strip_honorifics, parse_hindi_quantity
from . import agent as agent_module
from .agent import Agent, to_wire
from .prompts import FAILURE_REPLIES, SYSTEM_PROMPT, failure_reply

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
                call()
                for q in ctx.captured_queries:
                    sql_upper = q["sql"].strip().upper()
                    self.assertFalse(sql_upper.startswith("INSERT"), f"Found forbidden INSERT query: {sql_upper}")
                    self.assertFalse(sql_upper.startswith("UPDATE"), f"Found forbidden UPDATE query: {sql_upper}")
                    self.assertFalse(sql_upper.startswith("DELETE"), f"Found forbidden DELETE query: {sql_upper}")

    def test_proposals_no_target_document_writes(self):
        """Proposals create AssistantProposal rows but zero target ERP document rows."""
        so_count = SalesOrder.objects.count()
        with CaptureQueriesContext(connection) as ctx:
            res = propose_sales_order(
                self.user,
                customer_query="Sharma",
                items=[{"item": "Haldi Powder", "qty": 10}],
            )
            self.assertEqual(res.get("type"), "proposal")
            for q in ctx.captured_queries:
                sql_upper = q["sql"].strip().upper()
                if "ASSISTANT_" in sql_upper:
                    continue
                self.assertFalse(sql_upper.startswith("INSERT"), f"Found forbidden non-assistant INSERT: {sql_upper}")
                self.assertFalse(sql_upper.startswith("UPDATE"), f"Found forbidden non-assistant UPDATE: {sql_upper}")
                self.assertFalse(sql_upper.startswith("DELETE"), f"Found forbidden non-assistant DELETE: {sql_upper}")
        self.assertEqual(SalesOrder.objects.count(), so_count)

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

    def test_propose_sales_order_denied_without_create_permission(self):
        """User without can_create on selling.SalesOrder gets permission_denied."""
        res = propose_sales_order(
            self.user,
            customer_query="Sharma",
            items=[{"item": "Haldi", "qty": 1}],
        )
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

    def test_sales_order_proposal_two_line_order(self):
        """A two-line order gives a valid SalesOrder proposal with transaction_date and delivery_date."""
        Item.objects.create(
            code="MIRCHI-" + str(uuid.uuid4())[:4],
            name="Mirchi Powder",
            item_group=self.item_grp,
            stock_uom=self.uom,
            is_active=True,
            standard_rate=Decimal("200.00"),
        )
        initial_count = SalesOrder.objects.count()

        res = propose_sales_order(
            self.user,
            customer_query="Sharma",
            items=[
                {"item": "Haldi Powder", "qty": 10, "rate": 150},
                {"item": "Mirchi Powder", "qty": 5, "rate": 200},
            ],
            transaction_date="2026-10-31",
            delivery_date="2026-11-02",
        )

        self.assertEqual(res.get("type"), "proposal")
        self.assertEqual(SalesOrder.objects.count(), initial_count)
        self.assertEqual(res["target"], "selling.SalesOrder")
        self.assertEqual(res["route"], "/selling/sales-orders/new")
        self.assertEqual(res["values"]["customer"], self.customer.pk)
        self.assertEqual(res["values"]["transaction_date"], "2026-10-31")
        self.assertEqual(res["values"]["delivery_date"], "2026-11-02")
        self.assertEqual(len(res["values"]["items"]), 2)
        self.assertIn("Haldi Powder", res["summary"])
        self.assertIn("Mirchi Powder", res["summary"])

    def test_sales_order_ambiguous_item_asks(self):
        """When an item query matches multiple items, ask user with options."""
        Item.objects.create(
            code="HALDI-WHOLE-" + str(uuid.uuid4())[:4],
            name="Haldi Whole",
            item_group=self.item_grp,
            stock_uom=self.uom,
            is_active=True,
        )
        res = propose_sales_order(
            self.user,
            customer_query="Sharma",
            items=[{"item": "Haldi", "qty": 5}],
        )
        self.assertEqual(res.get("type"), "question")
        self.assertIn("item", res.get("question", "").lower())
        self.assertTrue(len(res.get("options", [])) >= 2)

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

    def test_stock_warehouse_picks_correct_warehouse_per_direction(self):
        """_stock_warehouse returns ready_warehouse for sales, unprocessed_warehouse for purchases, and falls back if unset."""
        # Unset state
        self.company.ready_warehouse = None
        self.company.unprocessed_warehouse = None
        self.company.save()
        self.assertEqual(_stock_warehouse(self.company, "sales").pk, self.wh.pk)
        self.assertEqual(_stock_warehouse(self.company, "purchases").pk, self.wh.pk)

        # Set warehouses
        ready_wh = Warehouse.objects.create(name="Ready Store " + str(uuid.uuid4())[:4], company=self.company)
        raw_wh = Warehouse.objects.create(name="Raw Store " + str(uuid.uuid4())[:4], company=self.company)
        self.company.ready_warehouse = ready_wh
        self.company.unprocessed_warehouse = raw_wh
        self.company.save()

        self.assertEqual(_stock_warehouse(self.company, "sales").pk, ready_wh.pk)
        self.assertEqual(_stock_warehouse(self.company, "purchases").pk, raw_wh.pk)

        # Proposals use them
        sales_res = propose_sales_order(
            self.user,
            customer_query="Sharma",
            items=[{"item": "Haldi Powder", "qty": 1}],
        )
        self.assertEqual(sales_res["values"]["items"][0]["warehouse"], ready_wh.pk)

        pur_res = propose_purchase_invoice(
            self.user,
            supplier_query="Gupta",
            items=[{"item": "Haldi Powder", "qty": 1}],
        )
        self.assertEqual(pur_res["values"]["items"][0]["warehouse"], raw_wh.pk)


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

        with mock.patch.object(
            Agent, "_default_llm_completion", return_value={"content": "ok", "tool_calls": None}
        ):
            resp = self.client.post("/api/assistant/chat/stream/", request_data, format="json")
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertEqual(resp["Content-Type"], "text/event-stream")
            stream_content = b"".join(resp.streaming_content).decode("utf-8")

        self.assertIn("event: meta", stream_content)
        self.assertIn("event: done", stream_content)


class AssistantMatchingTests(TestCase):
    """Real catalogues hold "Haldi" next to "Haldi 1kg", and "Sonf" next to "Souf"."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="matcher", email="matcher@erp.local", password="password"
        )
        setup_test_masters(self)
        self.item.name = "Haldi"
        self.item.save()
        self.pack = Item.objects.create(
            code="HALDI-1KG-" + str(uuid.uuid4())[:4],
            name="Haldi 1kg",
            item_group=self.item_grp,
            stock_uom=self.uom,
            is_active=True,
        )
        Warehouse.objects.create(
            name="All Warehouses " + str(uuid.uuid4())[:6],
            company=self.company,
            is_group=True,
        )

    def test_exact_name_wins_over_longer_names(self):
        """Picking "Haldi" from a list must resolve, or the agent asks for ever."""
        res = propose_sales_invoice(
            self.user, customer_query="Sharma", items=[{"item": "haldi", "qty": 5}]
        )
        self.assertEqual(res.get("type"), "proposal")
        self.assertEqual(res["values"]["items"][0]["item"], self.item.pk)

    def test_partial_name_still_asks(self):
        res = search_items(self.user, "hald")
        self.assertEqual(res["count"], 2)
        self.assertFalse(res["close_spelling"])

    def test_close_spelling_is_offered_never_picked(self):
        res = propose_sales_invoice(
            self.user, customer_query="Sharma", items=[{"item": "Haldee", "qty": 5}]
        )
        self.assertEqual(res.get("type"), "question")
        self.assertIn("Haldi", res["options"])

        found = search_items(self.user, "Haldee")
        self.assertTrue(found["close_spelling"])

    def test_nothing_close_is_not_found(self):
        res = propose_sales_invoice(
            self.user, customer_query="Sharma", items=[{"item": "Xylophone", "qty": 5}]
        )
        self.assertEqual(res.get("error"), "item_not_found")

    def test_words_in_any_order_match(self):
        res = search_customers(self.user, "Traders Sharma")
        self.assertEqual(res["count"], 1)
        self.assertFalse(res["close_spelling"])

    def test_party_name_lookups_do_not_crash_on_text(self):
        """A name where an id goes used to raise ValueError on the integer pk."""
        res = get_party_outstanding(self.user, "Customer", "Sharma")
        self.assertIn("Sharma Traders", res["party_name"])

        rate = get_rate(self.user, self.item.code, "Customer", "Sharma")
        self.assertNotIn("error", rate)

    def test_ambiguous_stock_item_is_reported(self):
        res = get_stock(self.user, "hald")
        self.assertEqual(res.get("error"), "ambiguous_item")

    def test_line_never_uses_a_group_warehouse(self):
        res = propose_sales_invoice(
            self.user, customer_query="Sharma", items=[{"item": "haldi", "qty": 5}]
        )
        self.assertEqual(res["values"]["items"][0]["warehouse"], self.wh.pk)

    def test_missing_rate_is_flagged(self):
        res = propose_sales_invoice(
            self.user, customer_query="Sharma", items=[{"item": "haldi 1kg", "qty": 5}]
        )
        self.assertTrue(any("No rate found" in w for w in res["warnings"]))

    def test_phones_are_masked_for_the_model(self):
        self.assertEqual(mask_phone("+91 93000 05040"), "******5040")
        self.customer.phone = "9300005040"
        self.customer.save()
        res = search_customers(self.user, "Sharma")
        self.assertEqual(res["results"][0]["phone"], "******5040")


class AssistantPaymentAccountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="cashier", email="cashier@erp.local", password="password"
        )
        setup_test_masters(self)
        self.bank = Account.objects.create(
            name="Bank-" + str(uuid.uuid4())[:6],
            company=self.company,
            parent=self.asset_root,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
        )
        self.cash = Account.objects.create(
            name="Cash-" + str(uuid.uuid4())[:6],
            company=self.company,
            parent=self.asset_root,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.CASH,
        )

    def test_cash_goes_to_the_cash_account(self):
        res = propose_payment_entry(
            self.user, "Customer", "Sharma", 15000, mode_of_payment="Cash"
        )
        self.assertEqual(res["values"]["paid_to"], self.cash.pk)
        self.assertEqual(res["values"]["paid_from"], self.debtors_acc.pk)

    def test_other_modes_go_to_the_bank(self):
        res = propose_payment_entry(
            self.user, "Customer", "Sharma", 15000, mode_of_payment="Cheque"
        )
        self.assertEqual(res["values"]["paid_to"], self.bank.pk)


class AssistantCompanyScopeTests(TestCase):
    """The active company comes from the request; the model cannot change it."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="scoped", email="scoped@erp.local", password="password"
        )
        setup_test_masters(self)
        self.other = Company.objects.create(
            name="Other Co " + str(uuid.uuid4())[:6],
            abbr="OC" + str(uuid.uuid4())[:4],
            default_currency="INR",
        )

    def test_party_of_another_company_is_invisible(self):
        res = search_customers(self.user, "Sharma", company=self.other.pk)
        self.assertEqual(res["count"], 0)

    def test_agent_injects_company_and_drops_model_supplied_one(self):
        agent = Agent(user=self.user, company=self.other, completion_fn=lambda *a, **k: {})
        res = agent._execute_tool(
            "search_customers", {"query": "Sharma", "company": self.company.pk, "bogus": 1}
        )
        self.assertEqual(res["count"], 0)

        agent = Agent(user=self.user, company=self.company, completion_fn=lambda *a, **k: {})
        res = agent._execute_tool("search_customers", {"query": "Sharma"})
        self.assertEqual(res["count"], 1)


class AssistantReportToolTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="reporter", email="reporter@erp.local", password="password"
        )
        setup_test_masters(self)
        self.today = datetime.date.today()

    def _invoice(self, docstatus):
        return SalesInvoice.objects.create(
            name=f"SI-TEST-{docstatus}",
            customer=self.customer,
            company=self.company,
            posting_date=self.today,
            due_date=self.today,
            debit_to=self.debtors_acc,
            income_account=self.sales_acc,
            net_total=Decimal("100.00"),
            grand_total=Decimal("100.00"),
            rounded_total=Decimal("100.00"),
            outstanding_amount=Decimal("40.00"),
            docstatus=docstatus,
        )

    def test_sales_summary_counts_only_submitted_invoices(self):
        self._invoice(1)
        self._invoice(0)
        res = sales_summary(self.user, "7d", company=self.company.pk)
        self.assertEqual(res["invoices"], 1)
        self.assertEqual(res["grand_total"], "100.00")
        self.assertEqual(res["outstanding"], "40.00")

    def test_reports_need_read_permission(self):
        plain = User.objects.create_user(username="plain", email="p@erp.local", password="password")
        self.assertEqual(sales_summary(plain).get("error"), "permission_denied")
        self.assertEqual(top_items(plain).get("error"), "permission_denied")

    def test_new_read_tools_need_read_permission(self):
        plain = User.objects.create_user(username="plain2", email="p2@erp.local", password="password")
        for tool in (pending_orders, money_to_collect, bills_to_pay, expense_summary_tool,
                     gst_position_tool, stock_stages):
            self.assertEqual(tool(plain).get("error"), "permission_denied", tool.__name__)

    def test_money_to_collect_matches_screen_and_wraps_text(self):
        from accounts.services import collections as collections_service

        self._invoice(1)
        res = money_to_collect(self.user, company=self.company.pk)
        self.assertNotIn("error", res)
        self.assertIn("<<DATA>>", json.dumps(res))
        screen = collections_service.collections(self.company.pk)
        self.assertEqual(len(res["customers"]), len(screen["customers"]))

    def test_pending_orders_matches_screen(self):
        from selling.services import dispatch

        res = pending_orders(self.user, company=self.company.pk)
        self.assertEqual(len(res["orders"]), len(dispatch.pending_orders(self.company.pk)))


class AssistantReceiptExpenseProposalTests(TestCase):
    """propose_receipt (oldest-first allocation) and propose_expense: each makes a
    valid proposal and writes no document; failures leave nothing behind."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="cashier", email="cashier@erp.local", password="password"
        )
        setup_test_masters(self)
        self.bank = Account.objects.create(
            name="Bank-" + str(uuid.uuid4())[:6],
            company=self.company,
            parent=self.asset_root,
            root_type=AccountRootType.ASSET,
            account_type=AccountType.BANK,
        )
        expense_root = Account.objects.create(
            name="Expenses-" + str(uuid.uuid4())[:6],
            company=self.company,
            root_type=AccountRootType.EXPENSE,
            is_group=True,
        )
        self.expense_account = Account.objects.create(
            name="Electricity-" + str(uuid.uuid4())[:6],
            company=self.company,
            parent=expense_root,
            root_type=AccountRootType.EXPENSE,
        )
        self.category = ExpenseCategory.objects.create(
            company=self.company, name="Bijli", account=self.expense_account
        )

    def _open_invoice(self, name, posting, due, outstanding):
        return SalesInvoice.objects.create(
            name=name,
            customer=self.customer,
            company=self.company,
            posting_date=posting,
            due_date=due,
            debit_to=self.debtors_acc,
            income_account=self.sales_acc,
            net_total=outstanding,
            grand_total=outstanding,
            rounded_total=outstanding,
            outstanding_amount=outstanding,
            docstatus=1,
        )

    def _assert_no_document_written(self, call):
        """Run `call`; no INSERT/UPDATE/DELETE may touch a non-assistant table."""
        with CaptureQueriesContext(connection) as ctx:
            res = call()
        for q in ctx.captured_queries:
            sql_upper = q["sql"].strip().upper()
            if "ASSISTANT_" in sql_upper:
                continue
            for verb in ("INSERT", "UPDATE", "DELETE"):
                self.assertFalse(
                    sql_upper.startswith(verb),
                    f"Found forbidden non-assistant {verb}: {sql_upper}",
                )
        return res

    def test_receipt_allocates_oldest_first(self):
        # Created newest first, so creation order cannot explain the result.
        self._open_invoice(
            "SI-NEW",
            datetime.date(2026, 10, 1),
            datetime.date(2026, 10, 20),
            Decimal("500.00"),
        )
        self._open_invoice(
            "SI-OLD",
            datetime.date(2026, 9, 1),
            datetime.date(2026, 9, 15),
            Decimal("300.00"),
        )

        res = self._assert_no_document_written(
            lambda: propose_receipt(self.user, customer_query="Sharma", amount="400")
        )

        self.assertEqual(res.get("type"), "proposal")
        self.assertEqual(res["target"], "accounts.PaymentEntry")
        self.assertEqual(res["route"], "/accounts/payment-entries/new")
        self.assertEqual(
            res["values"]["references"],
            [
                {
                    "reference_doctype": "selling.SalesInvoice",
                    "reference_name": "SI-OLD",
                    "allocated_amount": "300.00",
                },
                {
                    "reference_doctype": "selling.SalesInvoice",
                    "reference_name": "SI-NEW",
                    "allocated_amount": "100.00",
                },
            ],
        )
        self.assertEqual(res["values"]["payment_type"], "Receive")
        self.assertEqual(res["values"]["party_name"], self.customer.name)
        self.assertEqual(res["values"]["paid_to"], self.bank.pk)
        self.assertFalse(any("unallocated" in w for w in res["warnings"]))

    def test_receipt_above_open_bills_is_flagged(self):
        self._open_invoice(
            "SI-ONLY",
            datetime.date(2026, 9, 1),
            datetime.date(2026, 9, 15),
            Decimal("800.00"),
        )

        res = propose_receipt(self.user, customer_query="Sharma", amount="1000")

        self.assertEqual(res.get("type"), "proposal")
        self.assertEqual(
            [r["allocated_amount"] for r in res["values"]["references"]], ["800.00"]
        )
        self.assertTrue(any("stays unallocated" in w for w in res["warnings"]))

    def test_receipt_with_no_open_bills_is_still_a_proposal(self):
        res = self._assert_no_document_written(
            lambda: propose_receipt(self.user, customer_query="Sharma", amount="500")
        )

        self.assertEqual(res.get("type"), "proposal")
        self.assertEqual(res["values"]["references"], [])
        self.assertIn("no open bills", res["summary"])
        self.assertTrue(any("no open bills" in w for w in res["warnings"]))

    def test_receipt_failure_paths_write_nothing(self):
        pe_count = PaymentEntry.objects.count()
        unknown = propose_receipt(self.user, customer_query="Nobody Here", amount="500")
        self.assertEqual(unknown.get("error"), "customer_not_found")

        zero = propose_receipt(self.user, customer_query="Sharma", amount="0")
        self.assertEqual(zero.get("type"), "proposal")
        self.assertTrue(any("greater than zero" in w for w in zero["warnings"]))
        self.assertEqual(PaymentEntry.objects.count(), pe_count)

    def test_expense_proposal_is_valid_and_writes_nothing(self):
        pi_count = PurchaseInvoice.objects.count()
        pe_count = PaymentEntry.objects.count()

        res = self._assert_no_document_written(
            lambda: propose_expense(
                self.user,
                category_query="Bijli",
                amount="12400",
                supplier_query="Gupta",
                expense_date="2026-10-30",
                bill_no="EB-1042",
                paid_now=True,
                mode_of_payment="bank",
            )
        )

        self.assertEqual(res.get("type"), "proposal")
        self.assertEqual(res["target"], "buying.PurchaseInvoice")
        self.assertEqual(res["route"], "/accounts/expenses/new")
        self.assertEqual(res["values"]["category"], self.category.pk)
        self.assertEqual(res["values"]["amount"], "12400.00")
        self.assertEqual(res["values"]["date"], "2026-10-30")
        self.assertEqual(res["values"]["paid_from"], self.bank.pk)
        self.assertEqual(res["warnings"], [])
        self.assertEqual(PurchaseInvoice.objects.count(), pi_count)
        self.assertEqual(PaymentEntry.objects.count(), pe_count)

    def test_expense_without_bill_no_warns_before_submit(self):
        res = propose_expense(
            self.user, category_query="Bijli", amount="500", supplier_query="Gupta"
        )

        self.assertEqual(res.get("type"), "proposal")
        self.assertTrue(any("Bill No" in w for w in res["warnings"]))

    def test_expense_uses_cash_supplier_when_none_named(self):
        cash = Supplier.objects.create(
            name="Cash Expenses",
            company=self.company,
            supplier_group=self.supp_grp,
            is_cash_expense=True,
            is_active=True,
        )

        res = propose_expense(self.user, category_query="Bijli", amount="250")

        self.assertEqual(res.get("type"), "proposal")
        self.assertEqual(res["values"]["supplier"], cash.pk)
        self.assertFalse(any("Bill No" in w for w in res["warnings"]))
        self.assertFalse(res["values"]["paid_now"])

    def test_expense_without_any_supplier_is_refused(self):
        res = propose_expense(self.user, category_query="Bijli", amount="250")
        self.assertEqual(res.get("error"), "supplier_required")

    def test_expense_unknown_category_is_refused(self):
        pi_count = PurchaseInvoice.objects.count()
        res = propose_expense(self.user, category_query="Nonsense Head", amount="250")
        self.assertEqual(res.get("error"), "category_not_found")
        self.assertEqual(PurchaseInvoice.objects.count(), pi_count)

    def test_expense_paid_now_text_is_read_as_false(self):
        res = propose_expense(
            self.user,
            category_query="Bijli",
            amount="250",
            supplier_query="Gupta",
            bill_no="X-1",
            paid_now="false",
        )
        self.assertEqual(res["values"]["paid_now"], False)
        self.assertIsNone(res["values"]["paid_from"])
        self.assertIn("to pay later", res["summary"])

    def test_receipt_and_expense_need_create_permission(self):
        plain = User.objects.create_user(
            username="plain3", email="p3@erp.local", password="password"
        )
        self.assertEqual(
            propose_receipt(plain, customer_query="Sharma", amount="100").get("error"),
            "permission_denied",
        )
        self.assertEqual(
            propose_expense(plain, category_query="Bijli", amount="100").get("error"),
            "permission_denied",
        )


class AssistantToolRegistryTests(TestCase):
    """Every proposal builder is offered to the model with exactly its declared arguments."""

    HIDDEN = {"user", "company", "message"}

    def test_every_proposal_builder_has_a_matching_tool_schema(self):
        schemas = {
            tool["function"]["name"]: tool["function"]["parameters"]
            for tool in agent_module.TOOL_DEFINITIONS
        }
        for name, fn in PROPOSAL_BUILDERS.items():
            self.assertIn(name, schemas, f"{name} has no tool schema")
            params = set(inspect.signature(fn).parameters) - self.HIDDEN
            declared = set(schemas[name]["properties"])
            self.assertLessEqual(
                declared, params, f"{name} declares an argument it does not take"
            )
            self.assertLessEqual(set(schemas[name].get("required", [])), declared, name)

    def test_navigate_accepts_the_new_pages_and_refuses_unknown_routes(self):
        for route in (
            "/accounts/expenses/new",
            "/accounts/expenses",
            "/accounts/collections",
            "/accounts/bills-to-pay",
            "/accounts/gst-position",
            "/selling/pending-orders",
        ):
            self.assertEqual(navigate(route)["type"], "navigate", route)
        self.assertEqual(navigate("/accounts/not-a-page").get("error"), "invalid_route")


def scripted(*turns):
    """A completion_fn replaying `turns`, recording allow_tools for each call."""
    remaining = list(turns)
    calls = []

    def complete(messages, allow_tools=True):
        calls.append(allow_tools)
        return remaining.pop(0)

    complete.calls = calls
    return complete


def tool_call(name, **args):
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


class AssistantAgentLoopTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="looper", email="looper@erp.local", password="password"
        )
        setup_test_masters(self)
        self.conversation = AssistantConversation.objects.create(user=self.user)

    def _run(self, completion, language="en"):
        agent = Agent(user=self.user, company=self.company, completion_fn=completion)
        return "".join(agent.run_stream("hello", self.conversation, language=language))

    def _last_reply(self):
        return self.conversation.messages.filter(role="assistant").order_by("-id").first().content

    def test_question_ends_the_turn_and_is_remembered(self):
        completion = scripted(
            {"content": "", "tool_calls": [tool_call("ask_user", question="Kaun sa?", options=["A", "B"])]}
        )
        stream = self._run(completion)
        self.assertIn("event: question", stream)
        self.assertEqual(len(completion.calls), 1)
        self.assertEqual(self._last_reply(), "Kaun sa?")

    def test_builder_question_also_ends_the_turn(self):
        Customer.objects.create(
            name="Sharma Enterprises",
            company=self.company,
            customer_group=self.cust_grp,
            is_active=True,
        )
        completion = scripted(
            {
                "content": "",
                "tool_calls": [
                    tool_call(
                        "propose_sales_invoice",
                        customer_query="Sharma",
                        items=[{"item": "Haldi", "qty": 1}],
                    )
                ],
            }
        )
        self.assertIn("event: question", self._run(completion))
        self.assertFalse(AssistantProposal.objects.exists())

    def test_after_a_proposal_the_model_only_confirms(self):
        completion = scripted(
            {
                "content": "",
                "tool_calls": [
                    tool_call(
                        "propose_sales_invoice",
                        customer_query="Sharma",
                        items=[{"item": "Haldi", "qty": 1}],
                    )
                ],
            },
            {"content": "Form taiyar hai.", "tool_calls": None},
        )
        stream = self._run(completion)
        self.assertIn("event: proposal", stream)
        self.assertEqual(completion.calls, [True, False])
        self.assertEqual(AssistantProposal.objects.count(), 1)

    def test_turn_out_of_time_replies_instead_of_starting_a_round(self):
        completion = scripted(
            {"content": "", "tool_calls": [tool_call("search_items", query="Haldi")]},
        )
        clock = iter([0.0, 500.0, 500.0, 500.0])
        with mock.patch.object(agent_module, "_clock", lambda: next(clock)):
            stream = self._run(completion, language="hi")
        self.assertEqual(len(completion.calls), 1)
        self.assertIn(json.dumps(FAILURE_REPLIES["slow"]["hi"]), stream)

    def test_tool_crash_never_reaches_the_browser(self):
        completion = scripted(
            {"content": "", "tool_calls": [tool_call("search_items", query="x")]},
            {"content": "done", "tool_calls": None},
        )
        with mock.patch.dict(
            agent_module.READ_TOOLS,
            {"search_items": mock.Mock(side_effect=RuntimeError("secret path /etc/x"))},
        ), mock.patch.object(agent_module.inspect, "signature") as signature:
            signature.return_value.parameters = {"query": None}
            stream = self._run(completion)
        self.assertNotIn("secret path", stream)

    def test_empty_model_reply_is_not_left_blank(self):
        completion = scripted({"content": "", "tool_calls": None})
        self._run(completion)
        self.assertEqual(self._last_reply(), FAILURE_REPLIES["fallback"]["en"])

    def test_no_api_key_degrades_in_the_users_language(self):
        agent = Agent(user=self.user, company=self.company)
        with mock.patch.dict("os.environ", {"LLM_API_KEY": "", "OPENROUTER_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
            stream = "".join(agent.run_stream("hello", self.conversation, language="hi"))
        self.assertIn(json.dumps(FAILURE_REPLIES["fallback"]["hi"]), stream)


class AssistantWireFormatTests(TestCase):
    def test_claude_gets_a_cache_point_after_the_static_prompt(self):
        messages = [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\nTODAY: 2026-10-31"},
            {"role": "user", "content": "hi"},
        ]
        wire = to_wire(messages, "anthropic/claude-haiku-5.5")
        blocks = wire[0]["content"]
        self.assertEqual(blocks[0]["text"], SYSTEM_PROMPT)
        self.assertEqual(blocks[0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(blocks[1]["text"], "TODAY: 2026-10-31")
        self.assertNotIn("cache_control", blocks[1])

    def test_other_models_get_the_plain_request(self):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.assertIs(to_wire(messages, "deepseek/deepseek-v4-flash"), messages)

    def test_untranslated_language_falls_back_to_english(self):
        self.assertEqual(failure_reply("slow", "ta"), FAILURE_REPLIES["slow"]["en"])


class AssistantTranscribeTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="speaker", email="speaker@erp.local", password="password"
        )
        self.client.force_authenticate(user=self.user)

    def _post(self, language="hi"):
        audio = SimpleUploadedFile("audio.wav", b"RIFF0000WAVE", content_type="audio/wav")
        return self.client.post(
            "/api/assistant/transcribe/", {"file": audio, "language": language}, format="multipart"
        )

    def test_returns_text_and_sends_language(self):
        reply = mock.Mock(status_code=200)
        reply.json.return_value = {"text": " haldi "}
        with mock.patch.dict("os.environ", {"LLM_API_KEY": "test"}), mock.patch(
            "requests.post", return_value=reply
        ) as post:
            resp = self._post("hi")
        self.assertEqual(resp.data, {"text": "haldi"})
        self.assertEqual(post.call_args.kwargs["data"]["language"], "hi")
        self.assertEqual(post.call_args.kwargs["data"]["model"].split("/")[0], "mistralai")

    def test_unknown_language_is_left_to_autodetect(self):
        reply = mock.Mock(status_code=200)
        reply.json.return_value = {"text": "x"}
        with mock.patch.dict("os.environ", {"LLM_API_KEY": "test"}), mock.patch(
            "requests.post", return_value=reply
        ) as post:
            self._post("as")
        self.assertNotIn("language", post.call_args.kwargs["data"])

    def test_service_failure_is_a_503_not_silence(self):
        with mock.patch.dict("os.environ", {"LLM_API_KEY": "test"}), mock.patch(
            "requests.post", side_effect=requests.ConnectionError("down")
        ):
            resp = self._post()
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)


class HindiLatinMatchingTests(TestCase):
    """Test transliteration, honorific stripping, Hindi numbers, and spelling tolerance."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="hinditester",
            email="hindi@erp.local",
            password="adminpassword",
        )
        setup_test_masters(self)

    def test_transliteration_and_number_pairs_table(self):
        """Table of >=30 Hindi/Latin words and Hindi number words."""
        from difflib import SequenceMatcher

        translit_pairs = [
            ("शर्मा", "Sharma"),
            ("गुप्ता", "Gupta"),
            ("हल्दी", "Haldi"),
            ("मिर्ची", "Mirchi"),
            ("धनिया", "Dhaniya"),
            ("जीरा", "Jeera"),
            ("राई", "Rai"),
            ("रमेश", "Ramesh"),
            ("सुरेश", "Suresh"),
            ("अग्रवाल", "Agarwal"),
            ("जैन", "Jain"),
            ("पटेल", "Patel"),
            ("शाह", "Shah"),
            ("वर्मा", "Verma"),
            ("मसाला", "Masala"),
            ("पाउडर", "Powder"),
            ("किराना", "Kirana"),
            ("स्टोर", "Store"),
            ("ट्रेडर्स", "Traders"),
            ("दुकान", "Dukan"),
        ]
        for dev, lat in translit_pairs:
            dev_lat = to_latin(dev)
            lat_norm = to_latin(lat)
            sim = SequenceMatcher(None, dev_lat, lat_norm).ratio()
            self.assertGreaterEqual(
                sim,
                0.65,
                f"Devanagari '{dev}' ({dev_lat}) did not match Latin '{lat}' ({lat_norm}) with high similarity (got {sim})",
            )

        number_pairs = [
            ("dedh", Decimal("1.5")),
            ("dhai", Decimal("2.5")),
            ("sava", Decimal("1.25")),
            ("paune", Decimal("0.75")),
            ("adha", Decimal("0.5")),
            ("sava do", Decimal("2.25")),
            ("paune do", Decimal("1.75")),
            ("dedh sau", Decimal("150")),
            ("dhai sau", Decimal("250")),
            ("dedh hazaar", Decimal("1500")),
            ("dhai hazaar", Decimal("2500")),
            ("डेढ़", Decimal("1.5")),
            ("ढाई", Decimal("2.5")),
            ("सवा", Decimal("1.25")),
        ]
        for word, expected in number_pairs:
            qty = parse_hindi_quantity(word)
            self.assertEqual(
                qty,
                expected,
                f"Hindi number word '{word}' returned {qty}, expected {expected}",
            )

    def test_strip_honorifics(self):
        """Honorifics ji, sahab, bhai, seth are stripped from names."""
        self.assertEqual(strip_honorifics("Sharma ji"), "Sharma")
        self.assertEqual(strip_honorifics("Gupta sahab"), "Gupta")
        self.assertEqual(strip_honorifics("Ramesh bhai"), "Ramesh")
        self.assertEqual(strip_honorifics("Seth Agarwal"), "Agarwal")
        self.assertEqual(strip_honorifics("शर्मा जी"), "शर्मा")

    def test_devanagari_query_matches_latin_customer(self):
        """Devanagari 'शर्मा' matches 'Sharma Traders' in customer search as close spelling."""
        res = search_customers(self.user, "शर्मा")
        self.assertEqual(res["count"], 1)
        self.assertTrue(res["close_spelling"])
        self.assertIn("Sharma Traders", res["results"][0]["name"])

    def test_close_match_prompts_question_never_silent_choice(self):
        """A close transliterated match prompts ask_user, never a silent proposal."""
        res = propose_sales_invoice(
            self.user,
            customer_query="शर्मा",
            items=[{"item": "Haldi", "qty": 1}],
        )
        self.assertEqual(res.get("type"), "question")
        self.assertIn("Sharma Traders", res["options"])

