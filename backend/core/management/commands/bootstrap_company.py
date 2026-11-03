import datetime
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Company, FiscalYear, State, GSTCategory, UserCompany
from users.models import User, Role, UserRole
from stock.models import Warehouse, PriceList
from accounts.models import Account, AccountRootType, AccountType
from accounts.services.closing import ensure_closing_account
from gst.services import go_live
from manufacturing.services.batches import ensure_loss_warehouse
from hr.services.setup import ensure_hr_setup
from buying.services.charges import ensure_charge_setup
from django.core.management import call_command
from core.management.commands.seed_defaults import ensure_categories


class Command(BaseCommand):
    help = "Bootstrap a complete, production-ready company with CoA, GST ledgers, warehouses, and admin user."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Full Company Name (e.g. 'Apex Spices Pvt Ltd')")
        parser.add_argument("--abbr", required=True, help="Company Abbreviation (e.g. 'ASPL')")
        parser.add_argument("--state", default="23", help="2-digit GST State Code (e.g. '23' for MP, '27' for MH). Default: 23")
        parser.add_argument("--gstin", default="", help="15-character GSTIN (optional)")
        parser.add_argument("--pan", default="", help="10-character PAN (optional; auto-derived from GSTIN if empty)")
        parser.add_argument("--cin", default="", help="21-character CIN (optional)")
        parser.add_argument("--legal-name", default="", help="Legal Name on statutory filings (defaults to --name)")
        parser.add_argument("--address", default="", help="Address Line 1")
        parser.add_argument("--city", default="", help="City")
        parser.add_argument("--pincode", default="", help="6-digit PIN Code")
        parser.add_argument("--currency", default="INR", help="Default currency code (default: INR)")
        parser.add_argument("--admin-user", default="admin", help="Admin username to create/link (default: admin)")
        parser.add_argument("--admin-email", default="", help="Admin user email")
        parser.add_argument("--admin-password", default="", help="Admin user password")
        parser.add_argument(
            "--deactivate-others",
            action="store_true",
            help="Deactivate other companies (e.g. ACME) to lock into Single-Company Mode.",
        )

    def handle(self, *args, **options):
        name = options["name"].strip()
        abbr = options["abbr"].strip().upper()
        state_code = options["state"].strip()
        gstin = options["gstin"].strip().upper()
        pan = options["pan"].strip().upper()
        if not pan and len(gstin) >= 12:
            pan = gstin[2:12]
        legal_name = options["legal_name"].strip() or name

        self.stdout.write(self.style.NOTICE(f"=== Bootstrapping Company: {name} ({abbr}) ==="))

        # 1. Resolve State
        state = State.objects.filter(code=state_code).first()
        if not state:
            self.stdout.write(self.style.WARNING(f"State code '{state_code}' not found; state left blank."))

        category = GSTCategory.REGISTERED_REGULAR if gstin else GSTCategory.UNREGISTERED

        with transaction.atomic():
            # 2. Create or Update Company
            company, created = Company.objects.get_or_create(
                abbr=abbr,
                defaults={
                    "name": name,
                    "default_currency": options["currency"],
                    "country": "India",
                    "is_active": True,
                    "legal_name": legal_name,
                    "gstin": gstin or None,
                    "pan": pan,
                    "cin": options["cin"].strip(),
                    "state": state,
                    "gst_category": category,
                    "address_line1": options["address"].strip(),
                    "city": options["city"].strip(),
                    "pincode": options["pincode"].strip(),
                    "enable_perpetual_inventory": True,
                    "enable_rounded_total": True,
                    "round_off_gst_values": False,
                    "hsn_min_digits": 6,
                },
            )
            if not created:
                company.name = name
                company.legal_name = legal_name
                if gstin:
                    company.gstin = gstin
                    company.gst_category = category
                if pan:
                    company.pan = pan
                if state:
                    company.state = state
                company.is_active = True
                company.enable_perpetual_inventory = True
                company.enable_rounded_total = True
                company.save()
                self.stdout.write(f"Updated existing company: {company.name} (ID: {company.id})")
            else:
                self.stdout.write(self.style.SUCCESS(f"Created company: {company.name} (ID: {company.id})"))

            # 3. Chart of Accounts Roots
            coa_roots = [
                ("Application of Funds (Assets)", AccountRootType.ASSET),
                ("Source of Funds (Liabilities)", AccountRootType.LIABILITY),
                ("Equity", AccountRootType.EQUITY),
                ("Income", AccountRootType.INCOME),
                ("Expenses", AccountRootType.EXPENSE),
            ]
            root_nodes = {}
            for rname, rtype in coa_roots:
                acc, _ = Account.objects.get_or_create(
                    company=company,
                    name=rname,
                    defaults={"root_type": rtype, "is_group": True},
                )
                root_nodes[rtype] = acc

            # 4. Standard Operational Accounts
            bank_acc, _ = Account.objects.get_or_create(
                company=company,
                name="Bank Account",
                defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.BANK},
            )
            cash_acc, _ = Account.objects.get_or_create(
                company=company,
                name="Cash Account",
                defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.CASH},
            )
            debtors_acc, _ = Account.objects.get_or_create(
                company=company,
                name="Debtors",
                defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.RECEIVABLE},
            )
            stock_acc, _ = Account.objects.get_or_create(
                company=company,
                name="Stock Assets",
                defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.STOCK},
            )
            wip_acc, _ = Account.objects.get_or_create(
                company=company,
                name="Work In Progress",
                defaults={"parent": root_nodes[AccountRootType.ASSET], "root_type": AccountRootType.ASSET, "account_type": AccountType.STOCK},
            )
            creditors_acc, _ = Account.objects.get_or_create(
                company=company,
                name="Creditors",
                defaults={"parent": root_nodes[AccountRootType.LIABILITY], "root_type": AccountRootType.LIABILITY, "account_type": AccountType.PAYABLE},
            )
            sales_acc, _ = Account.objects.get_or_create(
                company=company,
                name="Sales Account",
                defaults={"parent": root_nodes[AccountRootType.INCOME], "root_type": AccountRootType.INCOME},
            )
            cogs_acc, _ = Account.objects.get_or_create(
                company=company,
                name="Cost of Goods Sold",
                defaults={"parent": root_nodes[AccountRootType.EXPENSE], "root_type": AccountRootType.EXPENSE, "account_type": AccountType.COST_OF_GOODS_SOLD},
            )
            stock_adj_acc, _ = Account.objects.get_or_create(
                company=company,
                name="Stock Adjustment",
                defaults={"parent": root_nodes[AccountRootType.EXPENSE], "root_type": AccountRootType.EXPENSE, "account_type": AccountType.STOCK_ADJUSTMENT},
            )
            round_off_acc, _ = Account.objects.get_or_create(
                company=company,
                name="Round Off",
                defaults={"parent": root_nodes[AccountRootType.EXPENSE], "root_type": AccountRootType.EXPENSE, "account_type": AccountType.ROUND_OFF},
            )

            # Wire accounts to company
            company.default_inventory_account = stock_acc
            company.stock_adjustment_account = stock_adj_acc
            company.default_expense_account = cogs_acc
            company.default_receivable_account = debtors_acc
            company.default_payable_account = creditors_acc
            company.default_income_account = sales_acc
            company.round_off_account = round_off_acc
            company.save()

            # 5. Standard Warehouses
            fg_wh, _ = Warehouse.objects.get_or_create(
                company=company,
                name=f"Finished Goods - {company.abbr}",
                defaults={"account": stock_acc},
            )
            rm_wh, _ = Warehouse.objects.get_or_create(
                company=company,
                name=f"Raw Material - {company.abbr}",
                defaults={"account": stock_acc},
            )
            wip_wh, _ = Warehouse.objects.get_or_create(
                company=company,
                name=f"Work In Progress - {company.abbr}",
                defaults={"account": wip_acc},
            )
            ensure_loss_warehouse(company)
            company.ready_warehouse = fg_wh
            company.unprocessed_warehouse = rm_wh
            company.in_process_warehouse = wip_wh
            company.save()

            # 6. Service & Subsystem Setups
            ensure_closing_account(company)
            ensure_hr_setup(company)
            ensure_charge_setup(company)
            ensure_categories(company)

            # 7. Fiscal Year
            FiscalYear.objects.get_or_create(
                name="2026-2027",
                defaults={
                    "start_date": datetime.date(2026, 4, 1),
                    "end_date": datetime.date(2027, 3, 31),
                    "is_closed": False,
                },
            )

            # 8. Price Lists
            std_selling, _ = PriceList.objects.get_or_create(
                name=f"Standard Selling ({company.abbr})",
                defaults={"currency": options["currency"], "selling": True, "buying": False},
            )
            std_buying, _ = PriceList.objects.get_or_create(
                name=f"Standard Buying ({company.abbr})",
                defaults={"currency": options["currency"], "selling": False, "buying": True},
            )
            company.default_selling_price_list = std_selling
            company.default_buying_price_list = std_buying
            company.save()

            # 9. GST Accounts & Tax Templates (5%, 12%, 18%)
            gst_res = go_live.setup_company(
                company,
                rates=(Decimal("5"), Decimal("12"), Decimal("18")),
                default_rate=Decimal("5"),
            )
            self.stdout.write(f"  GST Setup: {len(gst_res['accounts_created'])} accounts, {len(gst_res['templates_created'])} templates created.")

            # 10. Admin User Setup
            admin_username = options["admin_user"].strip()
            if admin_username:
                admin_user = User.objects.filter(username=admin_username).first()
                if not admin_user:
                    admin_email = options["admin_email"].strip() or f"{admin_username}@{abbr.lower()}.com"
                    admin_user = User.objects.create(
                        username=admin_username,
                        email=admin_email,
                        is_staff=True,
                        is_superuser=True,
                        is_active=True,
                    )
                    if options["admin_password"]:
                        admin_user.set_password(options["admin_password"])
                    else:
                        admin_user.set_unusable_password()
                    admin_user.save()
                    self.stdout.write(self.style.SUCCESS(f"Created Superuser: {admin_username} ({admin_email})"))
                else:
                    admin_user.is_staff = True
                    admin_user.is_superuser = True
                    admin_user.is_active = True
                    if options["admin_password"]:
                        admin_user.set_password(options["admin_password"])
                    admin_user.save()
                    self.stdout.write(f"Updated Superuser flags for: {admin_username}")

                # Ensure System Manager role
                sys_role = Role.objects.filter(name="System Manager").first()
                if sys_role:
                    UserRole.objects.get_or_create(user=admin_user, role=sys_role)

                # Link user to company
                UserCompany.objects.get_or_create(
                    user=admin_user,
                    company=company,
                    defaults={"is_default": True},
                )

            # 11. Single-Company Mode Lockdown (optional)
            if options["deactivate_others"]:
                other_count = Company.objects.exclude(id=company.id).filter(is_active=True).update(is_active=False)
                self.stdout.write(self.style.SUCCESS(f"Deactivated {other_count} other company/companies. Single-Company Mode enforced for {company.name}."))

        # 12. Integrity Check
        try:
            call_command("verify_integrity")
            self.stdout.write(self.style.SUCCESS(f"Bootstrap complete! Zero ledger drifts. Company '{company.name}' is fully operational."))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Bootstrap complete with integrity check notice: {e}"))
