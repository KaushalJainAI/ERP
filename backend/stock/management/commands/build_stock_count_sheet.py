import csv
from django.core.management.base import BaseCommand, CommandError

from core.models import Company
from stock.models import Item, Warehouse


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


def get_warehouses_for_sheet(company):
    whs = []
    # Try configured stages on company first
    for attr in ("unprocessed_warehouse", "in_process_warehouse", "ready_warehouse"):
        wh = getattr(company, attr, None)
        if wh and not wh.is_group and wh not in whs:
            whs.append(wh)

    # If not all 3 stages configured, pick non-group warehouses of company
    if len(whs) < 3:
        existing_ids = {w.pk for w in whs}
        candidates = (
            Warehouse.objects.filter(company=company, is_group=False)
            .exclude(pk__in=existing_ids)
            .order_by("pk")
        )
        for cand in candidates:
            whs.append(cand)
            if len(whs) == 3:
                break

    if not whs:
        raise CommandError(
            f"No non-group warehouses found for company '{company.name}'."
        )
    return whs


class Command(BaseCommand):
    help = "Writes a CSV template of active items with blank counted columns for warehouses."

    def add_arguments(self, parser):
        parser.add_argument(
            "--out",
            dest="out",
            default="count_sheet.csv",
            help="Path to output CSV file (default: count_sheet.csv)",
        )
        parser.add_argument(
            "--company",
            dest="company",
            default=None,
            help="Company name, abbr, or ID",
        )

    def handle(self, *args, **options):
        company = resolve_company(options.get("company"))
        warehouses = get_warehouses_for_sheet(company)
        out_path = options.get("out") or "count_sheet.csv"

        items = (
            Item.objects.filter(is_active=True, is_stock_item=True, has_variants=False)
            .select_related("stock_uom")
            .order_by("code")
        )

        header = ["item_code", "item_name", "uom", "standard_rate"] + [
            wh.name for wh in warehouses
        ]

        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            count = 0
            for item in items:
                uom_name = item.stock_uom.name if item.stock_uom else ""
                rate_str = str(item.standard_rate) if item.standard_rate else ""
                row = [item.code, item.name, uom_name, rate_str] + [
                    "" for _ in warehouses
                ]
                writer.writerow(row)
                count += 1

        wh_names = ", ".join(f"'{w.name}'" for w in warehouses)
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {count} active items across warehouses ({wh_names}) to '{out_path}'."
            )
        )
