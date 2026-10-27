"""Idempotently seed realistic Indian demo data for local development.

Every ledger-affecting document is created as a normal draft and then pushed
through the same ``.submit()`` path the API uses, so ``GLEntry`` /
``StockLedgerEntry`` / ``Bin`` are only ever written by the posting services.
Documents carry fixed ``*-DEMO-*`` names, which is what makes a re-run a no-op.

DEV ONLY: creates a known superuser (``admin`` / ``admin123``) and refuses to
run unless ``settings.DEBUG`` is true.
"""

import datetime
from decimal import Decimal

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import (
    Account,
    AccountRootType,
    JournalEntry,
    JournalEntryLine,
    PaymentEntry,
    PaymentEntryReference,
    PaymentType,
)
from buying.models import (
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
    SupplierGroup,
)
from core.models import Company, FiscalYear
from selling.models import (
    Customer,
    CustomerGroup,
    SalesInvoice,
    SalesInvoiceItem,
    SalesOrder,
    SalesOrderItem,
)
from stock.models import (
    UOM,
    Item,
    ItemGroup,
    StockEntry,
    StockEntryItem,
    StockEntryType,
    ValuationMethod,
    Warehouse,
)
from users.models import Role, User, UserRole

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
ADMIN_EMAIL = "admin@example.com"

ITEM_GROUPS = ("Groceries", "Stationery")
CUSTOMER_GROUPS = ("Retail", "Wholesale")
SUPPLIER_GROUPS = ("Local Suppliers", "Distributors")

# code, name, group, uom, selling (standard) rate, purchase rate, opening qty
ITEMS = [
    ("RICE-001", "Basmati Rice", "Groceries", "Kg", "95", "80", "500"),
    ("SUGAR-001", "Sugar", "Groceries", "Kg", "48", "40", "400"),
    ("ATTA-001", "Wheat Flour (Atta)", "Groceries", "Kg", "42", "34", "400"),
    ("TEA-001", "Assam Tea", "Groceries", "Box", "320", "250", "60"),
    ("NB-001", "Notebook A4 (200 pages)", "Stationery", "Nos", "60", "42", "600"),
    ("PEN-001", "Ball Pen Blue", "Stationery", "Box", "120", "85", "100"),
    ("STAP-001", "Stapler", "Stationery", "Nos", "150", "105", "80"),
    ("PAPER-001", "A4 Copier Paper Ream", "Stationery", "Box", "350", "290", "120"),
]

# name, group, GSTIN-format string, email, phone
CUSTOMERS = [
    (
        "Sharma Traders",
        "Wholesale",
        "07AAGCS4567K1ZP",
        "accounts@sharmatraders.example",
        "+91 98100 11111",
    ),
    (
        "Patel General Stores",
        "Retail",
        "24AAFPP8821M1Z3",
        "patelstores@example.com",
        "+91 98250 22222",
    ),
    (
        "Iyer & Sons Supermart",
        "Retail",
        "33AABCI5512R1ZK",
        "billing@iyersons.example",
        "+91 94440 33333",
    ),
    (
        "Banerjee Stationers",
        "Wholesale",
        "19AAKFB3390D1Z8",
        "info@banerjeestationers.example",
        "+91 98300 44444",
    ),
]

SUPPLIERS = [
    (
        "Ganga Agro Foods Pvt Ltd",
        "Distributors",
        "09AABCG7741N1Z2",
        "sales@gangaagro.example",
        "+91 94150 55555",
    ),
    (
        "Mahalaxmi Paper Mills",
        "Local Suppliers",
        "27AAACM2209L1ZV",
        "orders@mahalaxmipaper.example",
        "+91 98200 66666",
    ),
    (
        "Sundaram Wholesale Distributors",
        "Distributors",
        "33AAECS6038J1Z6",
        "sales@sundaramwholesale.example",
        "+91 94430 77777",
    ),
]


def _d(value):
    return Decimal(str(value))


class Command(BaseCommand):
    help = (
        "DEV ONLY. Idempotently seed demo masters and transactions (submitted through "
        "the normal posting services) plus a superuser admin/admin123."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "seed_demo creates a known-password superuser and only runs when DEBUG is true."
            )

        if not Company.objects.filter(abbr="ACME").exists():
            self.stdout.write("Default company missing; running seed_defaults first.")
            call_command("seed_defaults", stdout=self.stdout)

        self.company = Company.objects.get(abbr="ACME")
        self.admin = self._ensure_admin()
        self._ensure_accounts()
        self._ensure_perpetual_inventory()
        self._ensure_masters()

        # Spread the documents over the last few weeks, inside the fiscal year.
        today = datetime.date.today()
        fy = FiscalYear.objects.filter(
            start_date__lte=today, end_date__gte=today
        ).first()
        floor = fy.start_date if fy else today
        self.d = lambda days_ago: max(today - datetime.timedelta(days=days_ago), floor)

        created = []
        created += self._capital_and_rent()
        created += self._opening_stock()
        created += self._purchase_chain()
        created += self._sales_chain()
        created += self._open_sales_order()
        created += self._draft_sales_order()

        if created:
            self.stdout.write(self.style.SUCCESS("Created: " + ", ".join(created)))
        else:
            self.stdout.write("Demo documents already present; nothing to do.")
        self.stdout.write(
            self.style.SUCCESS(
                f"Demo data ready. Login: {ADMIN_USERNAME} / {ADMIN_PASSWORD}"
            )
        )

    # ------------------------------------------------------------------ users

    def _ensure_admin(self):
        user, created = User.objects.get_or_create(
            username=ADMIN_USERNAME,
            defaults={"email": ADMIN_EMAIL, "is_staff": True, "is_superuser": True},
        )
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        # set_password directly: the dev password deliberately skips the validators.
        user.set_password(ADMIN_PASSWORD)
        user.save()
        # Superusers bypass RBAC; the role assignment just makes /auth/me/ and the
        # admin site show a sensible role for this account.
        role = Role.objects.filter(name="System Manager").first()
        if role:
            UserRole.objects.get_or_create(user=user, role=role)
        if created:
            self.stdout.write(f"Created superuser '{ADMIN_USERNAME}'.")
        return user

    # --------------------------------------------------------------- masters

    def _acct(self, name):
        return Account.objects.get(company=self.company, name=name)

    def _ensure_accounts(self):
        expense_root = self._acct("Expenses")
        equity_root = self._acct("Equity")
        Account.objects.get_or_create(
            company=self.company,
            name="Rent Expense",
            defaults={
                "parent": expense_root,
                "root_type": AccountRootType.EXPENSE,
                "is_group": False,
            },
        )
        Account.objects.get_or_create(
            company=self.company,
            name="Capital Account",
            defaults={
                "parent": equity_root,
                "root_type": AccountRootType.EQUITY,
                "is_group": False,
            },
        )
        Account.objects.get_or_create(
            company=self.company,
            name="Stock Adjustment",
            defaults={
                "parent": expense_root,
                "root_type": AccountRootType.EXPENSE,
                "is_group": False,
            },
        )
        # Make sure the leaf accounts the documents need are really postable.
        for name in (
            "Bank Account",
            "Debtors",
            "Creditors",
            "Sales Account",
            "Stock Assets",
            "Cost of Goods Sold",
            "Stock Adjustment",
        ):
            if self._acct(name).is_group:
                raise CommandError(
                    f"Account '{name}' is a group; run seed_defaults on a clean database."
                )

    def _ensure_perpetual_inventory(self):
        """Point the company at its stock / COGS / adjustment accounts.

        Perpetual inventory is switched on only for a fresh seed: demo stock
        documents posted before it existed carry no inventory GL, so enabling it
        afterwards would leave the GL and Bin.stock_value permanently apart.
        """
        company = self.company
        company.default_inventory_account = self._acct("Stock Assets")
        company.default_expense_account = self._acct("Cost of Goods Sold")
        company.stock_adjustment_account = self._acct("Stock Adjustment")
        fresh = not StockEntry.objects.filter(name="STE-DEMO-0001").exists()
        if fresh:
            company.enable_perpetual_inventory = True
        elif not company.enable_perpetual_inventory:
            self.stdout.write(
                "Perpetual inventory left off: demo stock documents predate it "
                "(re-seed a fresh database to enable it)."
            )
        company.save(
            update_fields=[
                "default_inventory_account",
                "default_expense_account",
                "stock_adjustment_account",
                "enable_perpetual_inventory",
            ]
        )

    def _ensure_masters(self):
        for name in ("Nos", "Kg", "Box"):
            UOM.objects.get_or_create(name=name)

        root_ig = ItemGroup.objects.get(name="All Item Groups")
        for name in ITEM_GROUPS:
            ItemGroup.objects.get_or_create(
                name=name, defaults={"parent": root_ig, "is_group": False}
            )
        root_cg = CustomerGroup.objects.get(name="All Customer Groups")
        for name in CUSTOMER_GROUPS:
            CustomerGroup.objects.get_or_create(
                name=name, defaults={"parent": root_cg, "is_group": False}
            )
        root_sg = SupplierGroup.objects.get(name="All Supplier Groups")
        for name in SUPPLIER_GROUPS:
            SupplierGroup.objects.get_or_create(
                name=name, defaults={"parent": root_sg, "is_group": False}
            )

        wh_root, _ = Warehouse.objects.get_or_create(
            company=self.company, name="All Warehouses", defaults={"is_group": True}
        )
        for name in ("Stores", "Finished Goods"):
            Warehouse.objects.get_or_create(
                company=self.company,
                name=name,
                defaults={"parent": wh_root, "is_group": False},
            )
        self.stores = Warehouse.objects.get(company=self.company, name="Stores")

        self.items = {}
        for code, name, group, uom, sell, _buy, _qty in ITEMS:
            item, _ = Item.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "item_group": ItemGroup.objects.get(name=group),
                    "stock_uom": UOM.objects.get(name=uom),
                    "is_stock_item": True,
                    "valuation_method": ValuationMethod.MOVING_AVERAGE,
                    "standard_rate": _d(sell),
                },
            )
            self.items[code] = item

        for name, group, gstin, email, phone in CUSTOMERS:
            Customer.objects.get_or_create(
                company=self.company,
                name=name,
                defaults={
                    "customer_group": CustomerGroup.objects.get(name=group),
                    "gstin": gstin,
                    "email": email,
                    "phone": phone,
                },
            )
        for name, group, gstin, email, phone in SUPPLIERS:
            Supplier.objects.get_or_create(
                company=self.company,
                name=name,
                defaults={
                    "supplier_group": SupplierGroup.objects.get(name=group),
                    "gstin": gstin,
                    "email": email,
                    "phone": phone,
                },
            )

    # ------------------------------------------------------------- documents

    def _customer(self, name):
        return Customer.objects.get(company=self.company, name=name)

    def _supplier(self, name):
        return Supplier.objects.get(company=self.company, name=name)

    def _capital_and_rent(self):
        created = []
        specs = [
            (
                "JV-DEMO-0001",
                self.d(40),
                "Owner capital introduced",
                [("Bank Account", "500000", "0"), ("Capital Account", "0", "500000")],
            ),
            (
                "JV-DEMO-0002",
                self.d(10),
                "Shop rent paid from bank",
                [("Rent Expense", "25000", "0"), ("Bank Account", "0", "25000")],
            ),
        ]
        for name, date, remark, lines in specs:
            if JournalEntry.objects.filter(name=name).exists():
                continue
            je = JournalEntry.objects.create(
                name=name,
                posting_date=date,
                company=self.company,
                user_remark=remark,
                created_by=self.admin,
            )
            for acct, dr, cr in lines:
                JournalEntryLine.objects.create(
                    journal_entry=je,
                    account=self._acct(acct),
                    debit=_d(dr),
                    credit=_d(cr),
                )
            je.recompute_totals()
            je.save(update_fields=["total_debit", "total_credit"])
            je.submit(user=self.admin)
            created.append(name)
        return created

    def _opening_stock(self):
        name = "STE-DEMO-0001"
        if StockEntry.objects.filter(name=name).exists():
            return []
        entry = StockEntry.objects.create(
            name=name,
            stock_entry_type=StockEntryType.MATERIAL_RECEIPT,
            posting_date=self.d(35),
            company=self.company,
            created_by=self.admin,
        )
        for code, _n, _g, _u, _sell, buy, qty in ITEMS:
            StockEntryItem.objects.create(
                stock_entry=entry,
                item=self.items[code],
                target_warehouse=self.stores,
                qty=_d(qty),
                valuation_rate=_d(buy),
            )
        entry.submit(user=self.admin)
        return [name]

    def _purchase_chain(self):
        created = []
        supplier = self._supplier("Ganga Agro Foods Pvt Ltd")
        lines = [("RICE-001", "200", "80"), ("SUGAR-001", "150", "40")]

        po = PurchaseOrder.objects.filter(name="PO-DEMO-0001").first()
        if po is None:
            po = PurchaseOrder.objects.create(
                name="PO-DEMO-0001",
                supplier=supplier,
                company=self.company,
                transaction_date=self.d(25),
                schedule_date=self.d(20),
                created_by=self.admin,
            )
            for code, qty, rate in lines:
                PurchaseOrderItem.objects.create(
                    purchase_order=po,
                    item=self.items[code],
                    warehouse=self.stores,
                    qty=_d(qty),
                    rate=_d(rate),
                )
            po.submit(user=self.admin)
            created.append(po.name)

        if not PurchaseInvoice.objects.filter(name="PINV-DEMO-0001").exists():
            pi = PurchaseInvoice.objects.create(
                name="PINV-DEMO-0001",
                supplier=supplier,
                company=self.company,
                posting_date=self.d(20),
                due_date=self.d(-10),
                purchase_order=po,
                update_stock=True,
                credit_to=self._acct("Creditors"),
                expense_account=self._acct("Stock Assets"),
                created_by=self.admin,
            )
            for code, qty, rate in lines:
                PurchaseInvoiceItem.objects.create(
                    purchase_invoice=pi,
                    item=self.items[code],
                    warehouse=self.stores,
                    qty=_d(qty),
                    rate=_d(rate),
                )
            pi.submit(user=self.admin)
            created.append(pi.name)
        return created

    def _sales_chain(self):
        created = []
        customer = self._customer("Sharma Traders")
        lines = [("RICE-001", "50", "95"), ("NB-001", "100", "60")]

        so = SalesOrder.objects.filter(name="SO-DEMO-0001").first()
        if so is None:
            so = SalesOrder.objects.create(
                name="SO-DEMO-0001",
                customer=customer,
                company=self.company,
                transaction_date=self.d(15),
                delivery_date=self.d(10),
                created_by=self.admin,
            )
            for code, qty, rate in lines:
                SalesOrderItem.objects.create(
                    sales_order=so,
                    item=self.items[code],
                    warehouse=self.stores,
                    qty=_d(qty),
                    rate=_d(rate),
                )
            so.submit(user=self.admin)
            created.append(so.name)

        si = SalesInvoice.objects.filter(name="SINV-DEMO-0001").first()
        if si is None:
            si = SalesInvoice.objects.create(
                name="SINV-DEMO-0001",
                customer=customer,
                company=self.company,
                posting_date=self.d(10),
                due_date=self.d(-20),
                sales_order=so,
                update_stock=True,
                debit_to=self._acct("Debtors"),
                income_account=self._acct("Sales Account"),
                created_by=self.admin,
            )
            for code, qty, rate in lines:
                SalesInvoiceItem.objects.create(
                    sales_invoice=si,
                    item=self.items[code],
                    warehouse=self.stores,
                    qty=_d(qty),
                    rate=_d(rate),
                )
            si.submit(user=self.admin)
            created.append(si.name)

        if not PaymentEntry.objects.filter(name="PE-DEMO-0001").exists():
            pe = PaymentEntry.objects.create(
                name="PE-DEMO-0001",
                payment_type=PaymentType.RECEIVE,
                posting_date=self.d(5),
                company=self.company,
                party_type="Customer",
                party_name=customer.name,
                paid_from=self._acct("Debtors"),
                paid_to=self._acct("Bank Account"),
                paid_amount=Decimal("5000.00"),
                received_amount=Decimal("5000.00"),
                reference_no="NEFT-DEMO-0001",
                created_by=self.admin,
            )
            PaymentEntryReference.objects.create(
                payment_entry=pe,
                reference_doctype="selling.SalesInvoice",
                reference_name=si.name,
                allocated_amount=Decimal("5000.00"),
            )
            pe.submit(user=self.admin)
            created.append(pe.name)
        return created

    def _open_sales_order(self):
        """A confirmed order that has not been invoiced yet (shows as 'open')."""
        name = "SO-DEMO-0003"
        if SalesOrder.objects.filter(name=name).exists():
            return []
        so = SalesOrder.objects.create(
            name=name,
            customer=self._customer("Iyer & Sons Supermart"),
            company=self.company,
            transaction_date=self.d(3),
            delivery_date=self.d(-4),
            created_by=self.admin,
        )
        for code, qty, rate in [("ATTA-001", "80", "42"), ("PAPER-001", "20", "350")]:
            SalesOrderItem.objects.create(
                sales_order=so,
                item=self.items[code],
                warehouse=self.stores,
                qty=_d(qty),
                rate=_d(rate),
            )
        so.submit(user=self.admin)
        return [name]

    def _draft_sales_order(self):
        name = "SO-DEMO-0002"
        if SalesOrder.objects.filter(name=name).exists():
            return []
        so = SalesOrder.objects.create(
            name=name,
            customer=self._customer("Patel General Stores"),
            company=self.company,
            transaction_date=self.d(1),
            delivery_date=self.d(-6),
            created_by=self.admin,
        )
        total = Decimal("0.00")
        for code, qty, rate in [("SUGAR-001", "30", "48"), ("TEA-001", "10", "320")]:
            line = SalesOrderItem.objects.create(
                sales_order=so,
                item=self.items[code],
                warehouse=self.stores,
                qty=_d(qty),
                rate=_d(rate),
            )
            total += line.amount
        so.grand_total = total
        so.save(update_fields=["grand_total"])
        return [name]
