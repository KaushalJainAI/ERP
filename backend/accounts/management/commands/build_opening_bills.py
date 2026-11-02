import csv
import datetime

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from accounts.services import opening as opening_service
from core.models import Company

CSV_COLUMNS = [
    "kind",
    "party_type",
    "party",
    "bill_date",
    "amount",
    "due_date",
    "reference",
    "method",
]


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
            raise CommandError(
                _("Company '%(company)s' not found.") % {"company": company_arg}
            )
        return c
    c = Company.objects.first()
    if not c:
        raise CommandError(_("No company found."))
    return c


def _csv_row(row):
    return [
        row["kind"],
        row["party_type"],
        row["party"],
        row["bill_date"].isoformat() if row.get("bill_date") else "",
        f"{row['amount']:.2f}",
        row["due_date"].isoformat() if row.get("due_date") else "",
        row.get("reference", ""),
        row.get("method", ""),
    ]


class Command(BaseCommand):
    help = (
        "Builds the opening bills (planning 09) from the ledger as on a date. Dry run "
        "by default; --commit creates the submitted bills."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--as-on",
            dest="as_on",
            required=True,
            help="Cutover date (YYYY-MM-DD): the ledger balance is taken up to here.",
        )
        parser.add_argument(
            "--company", dest="company", default=None, help="Company name, abbr or ID"
        )
        parser.add_argument(
            "--dry-run",
            dest="dry_run",
            action="store_true",
            default=False,
            help="Show the plan and write nothing to the database (the default).",
        )
        parser.add_argument(
            "--commit",
            dest="commit",
            action="store_true",
            default=False,
            help="Create the submitted opening bills. Refused unless every total ties.",
        )
        parser.add_argument(
            "--from-csv",
            dest="from_csv",
            default=None,
            help=(
                "Use the accountant's corrected bills from this CSV (same columns as "
                "--out) instead of the estimate. The same total checks apply."
            ),
        )
        parser.add_argument(
            "--out",
            dest="out",
            default=None,
            help="Write the plan to this CSV file (for the accountant), dry run or not.",
        )

    def handle(self, *args, **options):
        if options["commit"] and options["dry_run"]:
            raise CommandError(_("Use either --dry-run or --commit, not both."))
        try:
            as_on = datetime.date.fromisoformat(options["as_on"])
        except ValueError:
            raise CommandError(
                _("Invalid --as-on date '%(date)s'. Expected YYYY-MM-DD.")
                % {"date": options["as_on"]}
            )
        company = resolve_company(options.get("company"))

        csv_rows = None
        if options.get("from_csv"):
            csv_rows = self._read_csv(options["from_csv"])
            plan = opening_service.plan_from_csv(company, as_on, csv_rows)
        else:
            plan = opening_service.plan_opening_bills(company, as_on)
        self._summary(company, as_on, plan)

        if options["out"]:
            with open(options["out"], "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_COLUMNS)
                for row in plan["rows"]:
                    writer.writerow(_csv_row(row))
            self.stdout.write(_("Plan written to %(path)s.") % {"path": options["out"]})

        if not options["commit"]:
            self.stdout.write(
                self.style.WARNING(
                    _(
                        "[DRY-RUN] No opening bills created. Use --commit to create them."
                    )
                )
            )
            return

        try:
            created = opening_service.commit_opening_bills(
                company, as_on, csv_rows=csv_rows
            )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages))
        self.stdout.write(
            self.style.SUCCESS(
                _(
                    "Created %(count)s submitted opening bills for %(company)s as on %(as_on)s."
                )
                % {"count": len(created), "company": company.name, "as_on": as_on}
            )
        )

    def _read_csv(self, path):
        try:
            # utf-8-sig: Excel saves CSV with a byte-order mark.
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                header = [h.strip() for h in (reader.fieldnames or [])]
                missing = [c for c in opening_service.CSV_REQUIRED if c not in header]
                if missing:
                    raise CommandError(
                        _("%(path)s is missing the column(s): %(columns)s.")
                        % {"path": path, "columns": ", ".join(missing)}
                    )
                return list(reader)
        except OSError as exc:
            raise CommandError(
                _("Cannot read %(path)s: %(error)s") % {"path": path, "error": exc}
            )

    def _summary(self, company, as_on, plan):
        t = plan["totals"]
        self.stdout.write(
            _("Opening bills for %(company)s as on %(as_on)s:")
            % {"company": company.name, "as_on": as_on}
        )
        self.stdout.write(
            _("  Bills: %(count)s, total %(total)s.")
            % {"count": t["bill_count"], "total": f"{t['bills_total']:.2f}"}
        )
        self.stdout.write(
            _("  Advances (credit balances, not bills): %(count)s, total %(total)s.")
            % {"count": t["advance_count"], "total": f"{t['advances_total']:.2f}"}
        )
        if t["review_count"]:
            self.stdout.write(
                self.style.WARNING(
                    _("  Parties to review by hand: %(count)s.")
                    % {"count": t["review_count"]}
                )
            )
        if plan["problems"]:
            self.stdout.write(self.style.WARNING(_("  Problems that block --commit:")))
            for problem in plan["problems"]:
                self.stdout.write(self.style.WARNING(f"    - {problem}"))
