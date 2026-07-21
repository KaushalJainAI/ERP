import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Company, FiscalYear, Language
from users.models import Role, RolePermission
from stock.models import UOM, ItemGroup
from selling.models import CustomerGroup
from buying.models import SupplierGroup
from accounts.models import Account, AccountRootType, AccountType


LANGUAGES_SEED = [
    ("en", "English", False),
    ("hi", "Hindi", False),
    ("ta", "Tamil", False),
    ("pa", "Punjabi", False),
    ("gu", "Gujarati", False),
    ("or", "Odia", False),
    ("bn", "Bengali", False),
    ("kn", "Kannada", False),
    ("as", "Assamese", False),
    ("te", "Telugu", False),
    ("ml", "Malayalam", False),
    ("ur", "Urdu", True),
]

UOM_SEED = ["Nos", "Kg", "Meter", "Litre", "Box", "Set"]

ROLES_SEED = [
    ("Default Role", "Base user role with read and create permissions"),
    ("Stock Manager", "Full permission over inventory and warehouses"),
    ("Accounts Manager", "Full permission over GL, invoices, and payments"),
    ("Sales Manager", "Full permission over selling chain"),
    ("Purchase Manager", "Full permission over buying chain"),
    ("System Manager", "System administration role"),
]

MODEL_LABELS_SEED = [
    "core.Company",
    "accounts.Account",
    "accounts.JournalEntry",
    "accounts.SalesInvoice",
    "accounts.PurchaseInvoice",
    "accounts.PaymentEntry",
    "stock.Item",
    "stock.Warehouse",
    "stock.StockEntry",
    "selling.Customer",
    "selling.Quotation",
    "selling.SalesOrder",
    "selling.DeliveryNote",
    "buying.Supplier",
    "buying.PurchaseOrder",
    "buying.PurchaseReceipt",
]


class Command(BaseCommand):
    help = "Idempotently seed default Company, FiscalYear, Languages, Roles, UOMs, CoA, and Master Groups."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Seeding default data...")

        # 1. Company
        company, created = Company.objects.get_or_create(
            abbr="ACME",
            defaults={
                "name": "Acme Corp",
                "default_currency": "INR",
                "country": "India",
                "is_active": True,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created default company: {company.name}"))

        # 2. FiscalYear
        fy, created = FiscalYear.objects.get_or_create(
            name="2026-2027",
            defaults={
                "start_date": datetime.date(2026, 4, 1),
                "end_date": datetime.date(2027, 3, 31),
                "is_closed": False,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created default fiscal year: {fy.name}"))

        # 3. Languages (13)
        for code, name, is_rtl in LANGUAGES_SEED:
            lang, c = Language.objects.get_or_create(
                code=code,
                defaults={"name": name, "is_rtl": is_rtl, "is_active": True},
            )

        # 4. Roles & Default Permissions
        for role_name, desc in ROLES_SEED:
            role, c = Role.objects.get_or_create(
                name=role_name,
                defaults={"description": desc},
            )

            # Assign permissions based on role
            for model_label in MODEL_LABELS_SEED:
                if role_name == "Default Role":
                    RolePermission.objects.get_or_create(
                        role=role,
                        model_label=model_label,
                        defaults={
                            "can_read": True,
                            "can_create": True,
                            "can_write": False,
                            "can_submit": False,
                            "can_cancel": False,
                            "can_delete": False,
                        },
                    )
                else:
                    # Manager roles get full permissions for MVP
                    RolePermission.objects.get_or_create(
                        role=role,
                        model_label=model_label,
                        defaults={
                            "can_read": True,
                            "can_create": True,
                            "can_write": True,
                            "can_submit": True,
                            "can_cancel": True,
                            "can_delete": True,
                        },
                    )

        # 5. UOMs
        for uom_name in UOM_SEED:
            UOM.objects.get_or_create(name=uom_name)

        # 6. Chart of Accounts Tree
        coa_roots = [
            ("Application of Funds (Assets)", AccountRootType.ASSET, True),
            ("Source of Funds (Liabilities)", AccountRootType.LIABILITY, True),
            ("Equity", AccountRootType.EQUITY, True),
            ("Income", AccountRootType.INCOME, True),
            ("Expenses", AccountRootType.EXPENSE, True),
        ]
        root_nodes = {}
        for name, rtype, is_group in coa_roots:
            acc, _ = Account.objects.get_or_create(
                company=company,
                name=name,
                defaults={"root_type": rtype, "is_group": is_group},
            )
            root_nodes[rtype] = acc

        # Sub accounts under Assets
        Account.objects.get_or_create(
            company=company,
            name="Bank Account",
            defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.BANK, "is_group": False},
        )
        Account.objects.get_or_create(
            company=company,
            name="Cash Account",
            defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.CASH, "is_group": False},
        )
        Account.objects.get_or_create(
            company=company,
            name="Debtors",
            defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.RECEIVABLE, "is_group": False},
        )
        Account.objects.get_or_create(
            company=company,
            name="Stock Assets",
            defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.STOCK, "is_group": False},
        )

        # Sub accounts under Liabilities
        Account.objects.get_or_create(
            company=company,
            name="Creditors",
            defaults={"parent": root_nodes[AccountRootType.LIABILITY], "root_type": AccountRootType.LIABILITY, "account_type": AccountType.PAYABLE, "is_group": False},
        )
        Account.objects.get_or_create(
            company=company,
            name="CGST",
            defaults={"parent": root_nodes[AccountRootType.LIABILITY], "root_type": AccountRootType.LIABILITY, "account_type": AccountType.TAX, "is_group": False},
        )
        Account.objects.get_or_create(
            company=company,
            name="SGST",
            defaults={"parent": root_nodes[AccountRootType.LIABILITY], "root_type": AccountRootType.LIABILITY, "account_type": AccountType.TAX, "is_group": False},
        )
        Account.objects.get_or_create(
            company=company,
            name="IGST",
            defaults={"parent": root_nodes[AccountRootType.LIABILITY], "root_type": AccountRootType.LIABILITY, "account_type": AccountType.TAX, "is_group": False},
        )

        # Income & Expense sub accounts
        Account.objects.get_or_create(
            company=company,
            name="Sales Account",
            defaults={"parent": root_nodes[AccountRootType.INCOME], "root_type": AccountRootType.INCOME, "is_group": False},
        )
        Account.objects.get_or_create(
            company=company,
            name="Cost of Goods Sold",
            defaults={"parent": root_nodes[AccountRootType.EXPENSE], "root_type": AccountRootType.EXPENSE, "account_type": AccountType.COST_OF_GOODS_SOLD, "is_group": False},
        )

        # 7. Master Group Roots
        ItemGroup.objects.get_or_create(name="All Item Groups", defaults={"is_group": True})
        CustomerGroup.objects.get_or_create(name="All Customer Groups", defaults={"is_group": True})
        SupplierGroup.objects.get_or_create(name="All Supplier Groups", defaults={"is_group": True})

        self.stdout.write(self.style.SUCCESS("Successfully seeded default data idempotently."))
