import csv
import datetime
from decimal import Decimal, InvalidOperation
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Account
from core.models import Company, DocStatus, NamingSeries
from stock.models import (
    Item,
    ReconciliationPurpose,
    StockReconciliation,
    StockReconciliationItem,
    Warehouse,
)
from stock.services import reconciliation as reconciliation_service


def resolve_company(company_arg):
    if company_arg:
        if str(company_arg).isdigit():
            c = Company.objects.filter(pk=int(company_arg)).first()
            if c:
                return c
        c = (
            Company.objects.filter(name=company_arg).first()
            or Company.objects.filter(abbr=company_arg).first()
        )
        if not c:
            raise CommandError(f"Company '{company_arg}' not found.")
        return c
    c = Company.objects.first()
    if not c:
        raise CommandError("No company found.")
    return c


def resolve_expense_account(company, account_arg, purpose):
    if account_arg:
        if str(account_arg).isdigit():
            acc = Account.objects.filter(company=company, pk=int(account_arg)).first()
        else:
            acc = (
                Account.objects.filter(company=company, name=account_arg).first()
                or Account.objects.filter(company=company, code=account_arg).first()
            )
        if not acc:
            raise CommandError(
                f"Account '{account_arg}' not found for company '{company.name}'."
            )
        return acc

    # For Opening Stock, prefer Temporary Opening / Equity / Liability / Asset account
    if purpose == ReconciliationPurpose.OPENING_STOCK:
        acc = (
            Account.objects.filter(
                company=company,
                name__icontains="Temporary Opening",
                is_group=False,
            ).first()
            or Account.objects.filter(
                company=company,
                root_type__in=["Equity", "Liability", "Asset"],
                is_group=False,
            )
            .exclude(name__icontains="Stock")
            .first()
        )
        if acc:
            return acc

    # Fallback to company default adjustment account
    return getattr(company, "stock_adjustment_account", None)


class Command(BaseCommand):
    help = (
        "Loads stock count sheet into a Draft Stock Reconciliation. "
        "Run without --commit or with --dry-run to preview."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file",
            nargs="?",
            default="count_sheet.csv",
            help="Path to count sheet CSV file (default: count_sheet.csv)",
        )
        parser.add_argument(
            "--file",
            dest="file_opt",
            default=None,
            help="Path to count sheet CSV file (overrides positional)",
        )
        parser.add_argument(
            "--company",
            dest="company",
            default=None,
            help="Company name, abbr, or ID",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="Show what would be posted without saving",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            dest="commit",
            default=False,
            help="Create the Draft Stock Reconciliation in the database",
        )
        parser.add_argument(
            "--purpose",
            dest="purpose",
            default=ReconciliationPurpose.OPENING_STOCK,
            help=f"Reconciliation purpose: '{ReconciliationPurpose.OPENING_STOCK}' or '{ReconciliationPurpose.STOCK_RECONCILIATION}'",
        )
        parser.add_argument(
            "--posting-date",
            dest="posting_date",
            default=None,
            help="Posting date in YYYY-MM-DD format (default: today)",
        )
        parser.add_argument(
            "--posting-time",
            dest="posting_time",
            default=None,
            help="Posting time in HH:MM:SS format",
        )
        parser.add_argument(
            "--expense-account",
            dest="expense_account",
            default=None,
            help="Difference / expense account name or ID",
        )

    def handle(self, *args, **options):
        csv_path = (
            options.get("file_opt") or options.get("csv_file") or "count_sheet.csv"
        )
        if not os.path.exists(csv_path):
            raise CommandError(f"CSV file not found: '{csv_path}'")

        company = resolve_company(options.get("company"))
        purpose = options.get("purpose") or ReconciliationPurpose.OPENING_STOCK
        if purpose not in ReconciliationPurpose.values:
            raise CommandError(
                f"Invalid purpose '{purpose}'. Choices: {list(ReconciliationPurpose.values)}"
            )

        is_dry_run = (not options.get("commit")) or options.get("dry_run")

        posting_date_str = options.get("posting_date")
        if posting_date_str:
            try:
                posting_date = datetime.date.fromisoformat(posting_date_str)
            except ValueError:
                raise CommandError(
                    f"Invalid posting date format: '{posting_date_str}'. Expected YYYY-MM-DD."
                )
        else:
            posting_date = datetime.date.today()

        posting_time = options.get("posting_time")

        expense_account = resolve_expense_account(
            company, options.get("expense_account"), purpose
        )

        entries = []
        skipped_blank_or_zero = 0

        with open(csv_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            try:
                raw_headers = next(reader)
            except StopIteration:
                raise CommandError("CSV file is empty.")

            headers = [h.strip() for h in raw_headers]
            headers_lower = [h.lower() for h in headers]

            # Detect format: long format (has 'warehouse' and 'qty') vs wide format (columns per warehouse)
            is_long_format = "warehouse" in headers_lower and (
                "qty" in headers_lower or "counted_qty" in headers_lower
            )

            if is_long_format:
                # Long format parsing
                item_code_idx = -1
                for candidate in ("item_code", "code", "item"):
                    if candidate in headers_lower:
                        item_code_idx = headers_lower.index(candidate)
                        break
                if item_code_idx == -1:
                    raise CommandError("CSV must contain an 'item_code' column.")

                wh_idx = headers_lower.index("warehouse")
                qty_idx = (
                    headers_lower.index("qty")
                    if "qty" in headers_lower
                    else headers_lower.index("counted_qty")
                )
                rate_idx = None
                for candidate in ("valuation_rate", "rate", "standard_rate"):
                    if candidate in headers_lower:
                        rate_idx = headers_lower.index(candidate)
                        break

                for row_num, row in enumerate(reader, start=2):
                    if not row or not any(cell.strip() for cell in row):
                        skipped_blank_or_zero += 1
                        continue
                    item_code = (
                        row[item_code_idx].strip() if len(row) > item_code_idx else ""
                    )
                    if not item_code:
                        skipped_blank_or_zero += 1
                        continue

                    item = Item.objects.filter(code=item_code).first()
                    if not item:
                        raise CommandError(
                            f"Unknown item code '{item_code}' on row {row_num}."
                        )
                    if not item.is_stock_item:
                        raise CommandError(
                            f"Item '{item_code}' on row {row_num} is not a stock item."
                        )
                    if not item.is_active:
                        raise CommandError(
                            f"Item '{item_code}' on row {row_num} is inactive."
                        )

                    wh_name = row[wh_idx].strip() if len(row) > wh_idx else ""
                    if not wh_name:
                        skipped_blank_or_zero += 1
                        continue
                    wh = (
                        Warehouse.objects.filter(company=company, name=wh_name).first()
                        or Warehouse.objects.filter(
                            company=company, name__iexact=wh_name
                        ).first()
                    )
                    if not wh:
                        raise CommandError(
                            f"Unknown warehouse: '{wh_name}' for company '{company.name}' on row {row_num}."
                        )
                    if wh.is_group:
                        raise CommandError(
                            f"Warehouse '{wh.name}' on row {row_num} is a group warehouse."
                        )

                    qty_val = row[qty_idx].strip() if len(row) > qty_idx else ""
                    if not qty_val:
                        skipped_blank_or_zero += 1
                        continue

                    try:
                        qty = Decimal(qty_val)
                    except InvalidOperation:
                        raise CommandError(
                            f"Invalid quantity '{qty_val}' for item '{item_code}' on row {row_num}."
                        )

                    if qty < 0:
                        raise CommandError(
                            f"Negative quantity '{qty_val}' for item '{item_code}' on row {row_num}."
                        )
                    if qty == 0:
                        skipped_blank_or_zero += 1
                        continue

                    rate = None
                    if rate_idx is not None and len(row) > rate_idx:
                        rate_val = row[rate_idx].strip()
                        if rate_val:
                            try:
                                rate = Decimal(rate_val)
                            except InvalidOperation:
                                raise CommandError(
                                    f"Invalid valuation rate '{rate_val}' on row {row_num}."
                                )
                            if rate < 0:
                                raise CommandError(
                                    f"Negative valuation rate '{rate_val}' on row {row_num}."
                                )

                    if rate is None and item.standard_rate and item.standard_rate > 0:
                        rate = item.standard_rate

                    entries.append((item, wh, qty, rate))

            else:
                # Wide format parsing
                item_code_idx = -1
                for candidate in ("item_code", "code", "item"):
                    if candidate in headers_lower:
                        item_code_idx = headers_lower.index(candidate)
                        break
                if item_code_idx == -1:
                    raise CommandError("CSV must contain an 'item_code' column.")

                rate_idx = None
                for candidate in ("valuation_rate", "rate", "standard_rate"):
                    if candidate in headers_lower:
                        rate_idx = headers_lower.index(candidate)
                        break

                meta_names = {
                    "item_code",
                    "code",
                    "item",
                    "item_name",
                    "name",
                    "uom",
                    "stock_uom",
                    "standard_rate",
                    "valuation_rate",
                    "rate",
                    "description",
                }

                wh_columns = []
                for idx, h in enumerate(headers):
                    if h.lower() in meta_names:
                        continue
                    wh = (
                        Warehouse.objects.filter(company=company, name=h).first()
                        or Warehouse.objects.filter(
                            company=company, name__iexact=h
                        ).first()
                    )
                    if not wh:
                        raise CommandError(
                            f"Unknown warehouse: '{h}' for company '{company.name}'."
                        )
                    if wh.is_group:
                        raise CommandError(
                            f"Warehouse '{wh.name}' is a group warehouse and cannot hold stock."
                        )
                    wh_columns.append((idx, wh))

                if not wh_columns:
                    raise CommandError(
                        "CSV must contain at least one warehouse column with counted quantities."
                    )

                for row_num, row in enumerate(reader, start=2):
                    if not row or not any(cell.strip() for cell in row):
                        skipped_blank_or_zero += 1
                        continue
                    item_code = (
                        row[item_code_idx].strip() if len(row) > item_code_idx else ""
                    )
                    if not item_code:
                        skipped_blank_or_zero += 1
                        continue

                    item = Item.objects.filter(code=item_code).first()
                    if not item:
                        raise CommandError(
                            f"Unknown item code '{item_code}' on row {row_num}."
                        )
                    if not item.is_stock_item:
                        raise CommandError(
                            f"Item '{item_code}' on row {row_num} is not a stock item."
                        )
                    if not item.is_active:
                        raise CommandError(
                            f"Item '{item_code}' on row {row_num} is inactive."
                        )

                    row_rate = None
                    if rate_idx is not None and len(row) > rate_idx:
                        rate_val = row[rate_idx].strip()
                        if rate_val:
                            try:
                                row_rate = Decimal(rate_val)
                            except InvalidOperation:
                                raise CommandError(
                                    f"Invalid valuation rate '{rate_val}' on row {row_num}."
                                )
                            if row_rate < 0:
                                raise CommandError(
                                    f"Negative valuation rate '{rate_val}' on row {row_num}."
                                )

                    if (
                        row_rate is None
                        and item.standard_rate
                        and item.standard_rate > 0
                    ):
                        row_rate = item.standard_rate

                    for idx, wh in wh_columns:
                        val = row[idx].strip() if len(row) > idx else ""
                        if not val:
                            skipped_blank_or_zero += 1
                            continue
                        try:
                            qty = Decimal(val)
                        except InvalidOperation:
                            raise CommandError(
                                f"Invalid quantity '{val}' for item '{item_code}' in warehouse '{wh.name}' on row {row_num}."
                            )

                        if qty < 0:
                            raise CommandError(
                                f"Negative quantity '{val}' for item '{item_code}' in warehouse '{wh.name}' on row {row_num}."
                            )
                        if qty == 0:
                            skipped_blank_or_zero += 1
                            continue

                        entries.append((item, wh, qty, row_rate))

        if not entries:
            if is_dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f"Dry-run: No counted stock entries found to post ({skipped_blank_or_zero} blank or zero row/cell entries skipped)."
                    )
                )
                return
            else:
                raise CommandError(
                    f"No counted stock entries found to import ({skipped_blank_or_zero} blank or zero entries skipped)."
                )

        if is_dry_run:
            self.stdout.write(
                f"Dry-run preview: {len(entries)} line(s) to reconcile for company '{company.name}' (purpose: {purpose}):"
            )
            for item, wh, qty, rate in entries:
                rate_display = f"@{rate}" if rate is not None else "(no rate)"
                self.stdout.write(
                    f"  - {item.code} ({item.name}) @ {wh.name}: qty={qty} {rate_display}"
                )
            self.stdout.write(f"Skipped {skipped_blank_or_zero} blank or zero entries.")
            self.stdout.write(
                self.style.WARNING(
                    "[DRY-RUN] No records created. Use --commit to create the draft Stock Reconciliation."
                )
            )
            return

        with transaction.atomic():
            doc_name = NamingSeries.next(f"MAT-RECO-{posting_date.year}-")
            reco = StockReconciliation.objects.create(
                name=doc_name,
                company=company,
                posting_date=posting_date,
                posting_time=posting_time,
                purpose=purpose,
                expense_account=expense_account,
                docstatus=DocStatus.DRAFT,
            )

            for item, wh, qty, rate in entries:
                StockReconciliationItem.objects.create(
                    reconciliation=reco,
                    item=item,
                    warehouse=wh,
                    qty=qty,
                    valuation_rate=rate,
                )

            reconciliation_service.refresh_draft(reco)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created Draft Stock Reconciliation '{reco.name}' with {len(entries)} items for purpose '{reco.purpose}'."
            )
        )
