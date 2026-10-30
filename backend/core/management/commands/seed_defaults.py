import datetime

from django.apps import apps as django_apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Company, FiscalYear, Language
from users.models import Role, RolePermission
from users.permissions import PERMISSION_FLAGS
from stock.models import UOM, ItemGroup, Warehouse
from selling.models import CustomerGroup
from buying.models import SupplierGroup
from accounts.models import Account, AccountRootType, AccountType
from accounts.services.closing import ensure_closing_account
from gst.services.seeding import seed_gst
from hr.seed import HR_LABELS, HR_ROLE_MATRIX, HR_ROLES
from hr.services.setup import ensure_hr_setup
from buying.services.charges import ensure_charge_setup
from tally_export.seed import TALLY_EXPORT_LABELS
from payment_reconciliation.seed import PAYMENT_RECONCILIATION_LABELS


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
    ("mr", "Marathi", False),
]

UOM_SEED = ["Nos", "Kg", "Meter", "Litre", "Box", "Set"]

ROLES_SEED = [
    ("Default Role", "Base user role with read and create permissions"),
    ("Stock Manager", "Full permission over inventory and warehouses"),
    ("Accounts Manager", "Full permission over GL, invoices, and payments"),
    ("Sales Manager", "Full permission over selling chain"),
    ("Purchase Manager", "Full permission over buying chain"),
    ("Manufacturing Manager", "Full permission over BOMs, Work Orders and production entries"),
    ("Credit Controller", "May override a customer credit-limit block"),
    ("System Manager", "System administration role"),
]

# --- RBAC seed matrix ---------------------------------------------------------
#
# The matrix below is the single source of truth for what each seeded role may
# do. Two properties matter:
#
# 1. **Every label must resolve to a real model.** The previous hand-written
#    list had drifted: it seeded "accounts.SalesInvoice" when that model lives
#    in the `selling` app (and "accounts.PurchaseInvoice" for `buying`), and
#    three labels for models that do not exist at all ("selling.Quotation",
#    "selling.DeliveryNote", "buying.PurchaseReceipt"). At the same time it
#    omitted the two labels the reporting endpoints are gated on --
#    "accounts.GLEntry" and "stock.Bin" -- so *no* seeded role could read a
#    single report. The seed now validates every label against the app
#    registry and refuses to run on a bad one, so a typo fails loudly instead
#    of silently granting authority on nothing.
#
# 2. **Roles are scoped, not uniform.** Every manager role used to receive
#    full permission on every model, which made "Sales Manager" and "System
#    Manager" the same account wearing different hats. The mandate is least
#    privilege, so each role now carries only what its own domain needs.

READ = ("can_read",)
READ_CREATE = ("can_read", "can_create")
FULL = ("can_read", "can_create", "can_write", "can_submit", "can_cancel", "can_delete")

#: Master data — reference records every domain reads.
_MASTER_LABELS = (
    "core.Company",
    "accounts.Account",
    "stock.UOM",
    "stock.ItemGroup",
    "stock.Item",
    "stock.ItemAttribute",
    "stock.Batch",
    "stock.Warehouse",
    "stock.PriceList",
    "stock.ItemPrice",
    "manufacturing.BOM",
    "selling.CustomerGroup",
    "selling.Customer",
    "buying.SupplierGroup",
    "buying.Supplier",
    "buying.PurchaseChargeTemplate",
    "contacts.Address",
    # GST masters (state list, per-company tax templates, global item tax templates).
    "core.State",
    "accounts.TaxTemplate",
    "accounts.ItemTaxTemplate",
)

#: GST masters every selling/buying/stock role must be able to read to fill forms.
_GST_MASTER_LABELS = (
    "core.State",
    "accounts.TaxTemplate",
    "accounts.ItemTaxTemplate",
)

#: Transactional documents a clerk may raise.
_DOCUMENT_LABELS = (
    "accounts.JournalEntry",
    "accounts.PaymentEntry",
    "stock.StockEntry",
    "stock.StockReconciliation",
    "selling.SalesOrder",
    "selling.SalesInvoice",
    "buying.PurchaseOrder",
    "buying.PurchaseInvoice",
    "manufacturing.WorkOrder",
)

#: Immutable ledgers. Read access here is read access to the books: the report
#: endpoints are gated on `accounts.GLEntry` and `stock.Bin`.
_LEDGER_LABELS = (
    "accounts.GLEntry",
    "stock.StockLedgerEntry",
    "stock.Bin",
)

_STOCK_LABELS = (
    "stock.UOM",
    "stock.ItemGroup",
    "stock.Item",
    "stock.ItemAttribute",
    "stock.Batch",
    "stock.Warehouse",
    "stock.PriceList",
    "stock.ItemPrice",
    "stock.StockLedgerEntry",
    "stock.Bin",
    "stock.StockEntry",
    "stock.StockReconciliation",
    "manufacturing.BOM",
    "manufacturing.WorkOrder",
)

_ACCOUNTS_LABELS = (
    "accounts.Account",
    "accounts.GLEntry",
    "accounts.JournalEntry",
    "accounts.PaymentEntry",
    "accounts.TaxTemplate",
    "accounts.ItemTaxTemplate",
)

_SELLING_LABELS = (
    "selling.CustomerGroup",
    "selling.Customer",
    "contacts.Address",
    "selling.SalesOrder",
    "selling.SalesInvoice",
    "stock.PriceList",
    "stock.ItemPrice",
)

_BUYING_LABELS = (
    "buying.SupplierGroup",
    "buying.Supplier",
    "buying.PurchaseChargeTemplate",
    "contacts.Address",
    "buying.PurchaseOrder",
    "buying.PurchaseInvoice",
    "stock.PriceList",
    "stock.ItemPrice",
)

#: Bank reconciliation and year-end closing. Statement lines are written only by
#: the import; clearances only by the reconcile endpoints.
_BANK_FULL_LABELS = (
    "accounts.BankAccount",
    "accounts.BankStatementImport",
    "accounts.BankClearance",
    "accounts.PeriodClosingVoucher",
)
_BANK_READ_LABELS = ("accounts.BankTransaction", "core.FiscalYear")

#: NGU online-store catalogue crosswalk (rows written by import_ngu_catalog;
#: people only link variants to items).
_NGU_LABELS = ("ngu.NguCatalogEntry",)

#: The RBAC control plane -- who may grant authority.
_ADMIN_LABELS = (
    "users.Role",
    "users.RolePermission",
    "users.User",
    "users.UserRole",
    # Integrity / health results shown on the dashboard (no CRUD route exists).
    "core.SystemHealthCheck",
)

#: Every model the API exposes and a role may therefore be granted authority
#: over. Derived from the groups above so it cannot drift out of sync with
#: them; every entry is validated against the app registry before seeding.
EXPOSED_MODEL_LABELS = tuple(
    sorted(
        set(_MASTER_LABELS)
        | set(_DOCUMENT_LABELS)
        | set(_LEDGER_LABELS)
        | set(_BANK_FULL_LABELS)
        | set(_BANK_READ_LABELS)
        | set(_NGU_LABELS)
        | set(_ADMIN_LABELS)
        | set(HR_LABELS)
        | set(TALLY_EXPORT_LABELS)
        | set(PAYMENT_RECONCILIATION_LABELS)
    )
)


def _scope(model_labels, flags):
    """Build a {model_label: flags} fragment for the permission matrix."""
    return {label: flags for label in model_labels}


ROLE_PERMISSION_MATRIX = {
    # Base role: read the masters, raise draft documents, submit nothing and
    # touch no ledger. Deliberately excludes `_LEDGER_LABELS` -- read on
    # `accounts.GLEntry` is exactly what opens the Trial Balance / P&L /
    # Balance Sheet reports, so a "read + create" clerk must not get it by
    # default.
    "Default Role": {
        **_scope(_DOCUMENT_LABELS, READ_CREATE),
        **_scope(_MASTER_LABELS, READ),
    },
    # Inventory authority, including the stock ledger and the stock balance.
    "Stock Manager": {
        **_scope(_STOCK_LABELS, FULL),
        **_scope(_NGU_LABELS, FULL),
        **_scope(
            ("core.Company", "accounts.ItemTaxTemplate"),
            READ,
        ),
    },
    # Financial authority: the GL, and therefore every accounts report.
    "Accounts Manager": {
        **_scope(_ACCOUNTS_LABELS, FULL),
        **_scope(_BANK_FULL_LABELS, FULL),
        **_scope(_BANK_READ_LABELS, READ),
        **_scope(
            ("core.Company", "core.State", "selling.Customer", "buying.Supplier"),
            READ,
        ),
        **_scope(TALLY_EXPORT_LABELS, FULL),
        **_scope(PAYMENT_RECONCILIATION_LABELS, FULL),
    },
    # Selling authority, plus read access to the catalogue it sells from.
    "Sales Manager": {
        **_scope(_SELLING_LABELS, FULL),
        **_scope(_NGU_LABELS, READ),
        **_scope(_GST_MASTER_LABELS, READ),
        **_scope(
            (
                "core.Company",
                "stock.UOM",
                "stock.ItemGroup",
                "stock.Item",
                "stock.Batch",
                "stock.Warehouse",
            ),
            READ,
        ),
    },
    # Buying authority, plus read access to the catalogue it buys.
    "Purchase Manager": {
        **_scope(_BUYING_LABELS, FULL),
        **_scope(_GST_MASTER_LABELS, READ),
        **_scope(
            (
                "core.Company",
                "stock.UOM",
                "stock.ItemGroup",
                "stock.Item",
                "stock.Batch",
                "stock.Warehouse",
            ),
            READ,
        ),
    },
    # Production authority: recipes, work orders, production entries and the
    # batch/bin projections the yield report reads.
    "Manufacturing Manager": {
        **_scope(
            (
                "manufacturing.BOM",
                "manufacturing.WorkOrder",
                "stock.StockEntry",
                "stock.Batch",
                "stock.Bin",
            ),
            FULL,
        ),
        **_scope(_MASTER_LABELS, READ),
        **_scope(("accounts.GLEntry",), READ),
    },
    # The control plane: every business model, plus authority to grant it.
    "System Manager": {
        **_scope(EXPOSED_MODEL_LABELS, FULL),
    },
    # May override the credit gate, but reads only the records the decision
    # needs (the customer, the documents and the masters behind them).
    "Credit Controller": {
        **_scope(
            (
                "selling.Customer",
                "selling.SalesOrder",
                "selling.SalesInvoice",
                "accounts.GLEntry",
            ),
            READ,
        ),
        **_scope(_MASTER_LABELS, READ),
    },
}
# HR roles (hr/seed.py) -- appended so the matrix above stays untouched.
ROLES_SEED += HR_ROLES
ROLE_PERMISSION_MATRIX.update(HR_ROLE_MATRIX)


def validate_model_labels(labels):
    """Fail loudly if a seeded label does not resolve to an installed model."""
    invalid = []
    for label in labels:
        try:
            django_apps.get_model(label)
        except LookupError:
            invalid.append(label)
    if invalid:
        raise CommandError(
            "seed_defaults: model_label(s) do not resolve to an installed model: "
            + ", ".join(invalid)
            + ". Fix the label or the model first -- a permission on a model "
            "that does not exist grants authority on nothing."
        )


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
            self.stdout.write(
                self.style.SUCCESS(f"Created default company: {company.name}")
            )

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
            self.stdout.write(
                self.style.SUCCESS(f"Created default fiscal year: {fy.name}")
            )

        # 3. Languages (13)
        for code, name, is_rtl in LANGUAGES_SEED:
            lang, c = Language.objects.get_or_create(
                code=code,
                defaults={"name": name, "is_rtl": is_rtl, "is_active": True},
            )

        # 4. Roles & Default Permissions
        #
        # The seed is *authoritative* for the roles it owns: a role's
        # permission rows are made to match ROLE_PERMISSION_MATRIX exactly.
        # Rows outside the matrix are removed and flags inside it are set to
        # the intended values. That is what lets a re-seed repair a database
        # seeded by an older, buggy version of this command (which granted
        # every manager role everything, on labels that pointed at models
        # that do not exist). Roles the operator created themselves are never
        # touched.
        validate_model_labels(EXPOSED_MODEL_LABELS)
        for role_name, desc in ROLES_SEED:
            role, c = Role.objects.get_or_create(
                name=role_name,
                defaults={"description": desc},
            )
            intended = ROLE_PERMISSION_MATRIX.get(role_name, {})
            created, updated, deleted = self._sync_role_permissions(role, intended)
            if created or updated or deleted:
                self.stdout.write(
                    f"  role '{role_name}': {created} granted, {updated} updated, "
                    f"{deleted} revoked"
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
            defaults={
                "parent": root_nodes[AccountRootType.ASSET],
                "root_type": AccountRootType.ASSET,
                "account_type": AccountType.BANK,
                "is_group": False,
            },
        )
        Account.objects.get_or_create(
            company=company,
            name="Cash Account",
            defaults={
                "parent": root_nodes[AccountRootType.ASSET],
                "root_type": AccountRootType.ASSET,
                "account_type": AccountType.CASH,
                "is_group": False,
            },
        )
        Account.objects.get_or_create(
            company=company,
            name="Debtors",
            defaults={
                "parent": root_nodes[AccountRootType.ASSET],
                "root_type": AccountRootType.ASSET,
                "account_type": AccountType.RECEIVABLE,
                "is_group": False,
            },
        )
        Account.objects.get_or_create(
            company=company,
            name="Stock Assets",
            defaults={
                "parent": root_nodes[AccountRootType.ASSET],
                "root_type": AccountRootType.ASSET,
                "account_type": AccountType.STOCK,
                "is_group": False,
            },
        )
        # Work In Progress is its own balance-sheet line (design §14.4). The WIP
        # warehouse, when one exists, posts here; every other warehouse stays on
        # the company default inventory account.
        wip_account, _ = Account.objects.get_or_create(
            company=company,
            name="Work In Progress",
            defaults={
                "parent": root_nodes[AccountRootType.ASSET],
                "root_type": AccountRootType.ASSET,
                "account_type": AccountType.STOCK,
                "is_group": False,
            },
        )
        for warehouse in Warehouse.objects.filter(
            company=company, name__iexact="Work In Progress", account__isnull=True
        ):
            warehouse.account = wip_account
            warehouse.save(update_fields=["account"])

        # Sub accounts under Liabilities
        Account.objects.get_or_create(
            company=company,
            name="Creditors",
            defaults={
                "parent": root_nodes[AccountRootType.LIABILITY],
                "root_type": AccountRootType.LIABILITY,
                "account_type": AccountType.PAYABLE,
                "is_group": False,
            },
        )
        # GST tax accounts (Output/Input/RCM CGST, SGST, IGST, Round Off) are seeded
        # by gst.services.seeding.seed_gst() below. The bare "CGST"/"SGST"/"IGST"
        # accounts this seed used to create are no longer created for new databases;
        # legacy ones are tagged Output by migration accounts.0006.

        # Income & Expense sub accounts
        Account.objects.get_or_create(
            company=company,
            name="Sales Account",
            defaults={
                "parent": root_nodes[AccountRootType.INCOME],
                "root_type": AccountRootType.INCOME,
                "is_group": False,
            },
        )
        Account.objects.get_or_create(
            company=company,
            name="Cost of Goods Sold",
            defaults={
                "parent": root_nodes[AccountRootType.EXPENSE],
                "root_type": AccountRootType.EXPENSE,
                "account_type": AccountType.COST_OF_GOODS_SOLD,
                "is_group": False,
            },
        )

        # 7. Master Group Roots
        item_group_root, _ = ItemGroup.objects.get_or_create(
            name="All Item Groups", defaults={"is_group": True}
        )
        for name in (
            "Raw Material",
            "Semi-Finished Goods",
            "Finished Goods",
            "Packing Material",
        ):
            ItemGroup.objects.get_or_create(
                name=name, defaults={"parent": item_group_root, "is_group": False}
            )
        CustomerGroup.objects.get_or_create(
            name="All Customer Groups", defaults={"is_group": True}
        )
        SupplierGroup.objects.get_or_create(
            name="All Supplier Groups", defaults={"is_group": True}
        )

        # 8. GST masters: states, UQCs, item tax templates, and per company the
        # tagged tax accounts, Round Off account and standard tax templates.
        gst_summary = seed_gst()
        self.stdout.write(
            f"  GST: {gst_summary['states_created']} states, "
            f"{gst_summary['item_tax_templates_created']} item tax templates created"
        )
        for comp in gst_summary["companies"]:
            for warning in comp["warnings"]:
                self.stdout.write(self.style.WARNING(f"  GST warning: {warning}"))

        # 8b. Year-end closing: the Retained Earnings account the Period Closing
        # Voucher moves profit into (an existing Reserves & Surplus is reused).
        ensure_closing_account(company)

        # 9. HR: wage/payable/advance accounts, HR Settings, system components.
        ensure_hr_setup(company)

        # 10. Purchase-bill charges: Freight / Hamali accounts, Expenses Included
        # In Valuation and the charge templates.
        ensure_charge_setup(company)

        self.stdout.write(
            self.style.SUCCESS("Successfully seeded default data idempotently.")
        )

    def _sync_role_permissions(self, role, intended):
        """Make `role`'s permission rows exactly match `intended`.

        Returns (created, updated, deleted) counts so the operator can see
        what a re-seed changed.
        """
        created = updated = deleted = 0
        existing = {p.model_label: p for p in RolePermission.objects.filter(role=role)}

        # Revoke anything no longer in scope -- this is what removes the
        # phantom labels ("selling.Quotation" and friends) and the
        # cross-domain grants ("Sales Manager" holding the GL) that an older
        # seed left behind.
        for label in list(existing):
            if label not in intended:
                existing.pop(label).delete()
                deleted += 1

        for label, flags in intended.items():
            defaults = {flag: flag in flags for flag in PERMISSION_FLAGS}
            current = existing.get(label)
            if current is None:
                RolePermission.objects.create(role=role, model_label=label, **defaults)
                created += 1
                continue
            changed = False
            for flag, value in defaults.items():
                if getattr(current, flag) != value:
                    setattr(current, flag, value)
                    changed = True
            if changed:
                current.save()
                updated += 1

        return created, updated, deleted
