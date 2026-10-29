"""``manage.py setup_gst_accounts --company <name>``: GST go-live setup (idempotent).

Creates / tags separate Input and Output CGST, SGST, IGST accounts under
``Duties & Taxes`` and the 5% / 12% / 18% Sales and Purchase tax templates
(intra-state CGST + SGST, inter-state IGST) that post to them, and reports the
company's GSTIN / state problems. Legacy Tally combined ledgers are never touched.
"""

from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from core.models import Company
from gst.services import go_live


class Command(BaseCommand):
    help = (
        "Idempotently set up separate Input / Output GST accounts and the "
        "5/12/18% tax templates of a company, and check its GSTIN and state."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--company",
            action="append",
            help="Company name or abbreviation (repeatable). Default: every company.",
        )
        parser.add_argument(
            "--tax-group",
            default=go_live.TAX_GROUP_NAME,
            help="Parent group of the GST accounts (default: 'Duties & Taxes').",
        )
        parser.add_argument(
            "--rates",
            default=",".join(str(r) for r in go_live.DEFAULT_RATES),
            help="Comma separated GST slab rates to make templates for (default 5,12,18).",
        )
        parser.add_argument(
            "--default-rate",
            default=str(go_live.DEFAULT_RATE),
            help="Rate whose templates become each slot's default (default 5). "
            "Use 'keep' to leave the current defaults alone.",
        )
        parser.add_argument(
            "--with-cess",
            action="store_true",
            help="Also create Input / Output CESS accounts (no templates).",
        )

    def handle(self, *args, **options):
        try:
            rates = tuple(
                Decimal(r.strip()) for r in options["rates"].split(",") if r.strip()
            )
            keep = options["default_rate"].strip().lower() == "keep"
            default_rate = None if keep else Decimal(options["default_rate"])
        except InvalidOperation as exc:
            raise CommandError("--rates / --default-rate must be numbers.") from exc
        if not rates or any(r <= 0 or r > 100 for r in rates):
            raise CommandError("Rates must be between 0 and 100.")
        if default_rate is not None and default_rate not in rates:
            raise CommandError("--default-rate must be one of --rates (or 'keep').")

        names = options.get("company")
        if names:
            companies = []
            for name in names:
                company = (
                    Company.objects.filter(name=name).first()
                    or Company.objects.filter(abbr=name).first()
                )
                if company is None:
                    raise CommandError(f"Company '{name}' not found.")
                companies.append(company)
        else:
            companies = list(Company.objects.order_by("id"))

        for company in companies:
            s = go_live.setup_company(
                company,
                group_name=options["tax_group"],
                rates=rates,
                default_rate=default_rate,
                with_cess=options["with_cess"],
            )
            self.stdout.write(self.style.SUCCESS(f"{company.name}:"))
            self.stdout.write(
                f"  accounts created: {', '.join(s['accounts_created']) or '-'}"
            )
            self.stdout.write(
                f"  accounts tagged:  {', '.join(s['accounts_tagged']) or '-'}"
            )
            self.stdout.write(
                f"  templates created: {', '.join(s['templates_created']) or '-'}"
            )
            self.stdout.write(
                f"  defaults set:      {', '.join(s['defaults_set']) or '-'}"
            )
            if s["legacy_accounts"]:
                self.stdout.write(
                    "  legacy combined ledgers left untouched: "
                    + ", ".join(s["legacy_accounts"])
                )
            if s["legacy_templates"]:
                self.stdout.write(
                    "  templates still posting to a legacy ledger (disable them): "
                    + ", ".join(s["legacy_templates"])
                )
            for warning in s["warnings"]:
                self.stdout.write(self.style.WARNING(f"  warning: {warning}"))
