import logging
import time
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from notifications.management.commands.send_outbox import process_outbox
from notifications.services.digests import (
    send_day_summaries,
    send_daily_summary,
    send_overdue_bills_digest,
    send_weekly_digest,
)
from notifications.services.engine import check_raw_material_shortage

logger = logging.getLogger(__name__)

# The evening day summary goes out at 19:00 local time (and in a one-off pass after it).
EVENING_HOUR = 19


def safe_execute(name, func, *args, **kwargs):
    """
    Executes a scheduled task safely, logging any exceptions without crashing.
    """
    try:
        logger.info("Scheduler: starting job '%s'", name)
        result = func(*args, **kwargs)
        logger.info("Scheduler: finished job '%s'", name)
        return result
    except Exception as exc:
        logger.error(
            "Scheduler: job '%s' raised an exception: %s", name, exc, exc_info=True
        )
        return None


def run_recurring_expenses():
    """
    Calls recurring expense generation if available.
    """
    try:
        from buying.services.recurring import process_recurring_expenses

        return process_recurring_expenses()
    except (ImportError, AttributeError):
        try:
            from buying.services.expenses import generate_due_recurring_expenses

            return generate_due_recurring_expenses()
        except (ImportError, AttributeError):
            return None


def run_all_jobs(force_weekly=False):
    """
    Executes one pass of all scheduled jobs.
    Returns dict with job execution results.
    """
    results = {}
    results["shortage"] = safe_execute(
        "raw_material_shortage", check_raw_material_shortage
    )
    results["recurring_expenses"] = safe_execute(
        "recurring_expenses", run_recurring_expenses
    )
    results["daily_summary"] = safe_execute("daily_summary", send_daily_summary)
    results["overdue_bills"] = safe_execute(
        "overdue_bills_digest", send_overdue_bills_digest
    )
    if timezone.localtime().hour >= EVENING_HOUR:
        results["day_summary"] = safe_execute("day_summary", send_day_summaries)

    today = timezone.localdate()
    if force_weekly or today.weekday() == 0:  # Monday is 0
        results["weekly_digest"] = safe_execute("weekly_digest", send_weekly_digest)

    results["fiscal_years"] = safe_execute(
        "ensure_fiscal_years", lambda: call_command("ensure_fiscal_years")
    )
    results["outbox"] = safe_execute("send_outbox", process_outbox)
    return results


class Command(BaseCommand):
    help = "Run the ERP background task scheduler"

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one pass of all due jobs and exit immediately.",
        )
        parser.add_argument(
            "--force-weekly",
            action="store_true",
            help="Force running the weekly digest regardless of day of week.",
        )

    def handle(self, *args, **options):
        run_once = options.get("once", False)
        force_weekly = options.get("force_weekly", False)

        if run_once:
            self.stdout.write("Running one-off scheduler cycle...")
            results = run_all_jobs(force_weekly=force_weekly)
            self.stdout.write(self.style.SUCCESS(f"Scheduler pass complete: {results}"))
            return

        self.stdout.write(self.style.SUCCESS("Starting ERP scheduler daemon loop..."))

        last_min = -1
        last_daily_date = None
        last_evening_date = None
        last_weekly_date = None
        last_integrity_date = None
        last_backup_date = None

        while True:
            try:
                now = timezone.localtime()
                current_date = now.date()
                hour = now.hour
                minute = now.minute

                # 1. Every minute: outbox dispatch
                if minute != last_min:
                    last_min = minute
                    safe_execute("send_outbox", process_outbox)

                # 2. Daily jobs at 08:00
                if hour == 8 and minute == 0 and last_daily_date != current_date:
                    last_daily_date = current_date
                    safe_execute("raw_material_shortage", check_raw_material_shortage)
                    safe_execute("recurring_expenses", run_recurring_expenses)
                    safe_execute("daily_summary", send_daily_summary)
                    safe_execute("overdue_bills_digest", send_overdue_bills_digest)
                    safe_execute(
                        "ensure_fiscal_years",
                        lambda: call_command("ensure_fiscal_years"),
                    )

                # 2b. Evening day summary at 19:00
                if (
                    hour == EVENING_HOUR
                    and minute == 0
                    and last_evening_date != current_date
                ):
                    last_evening_date = current_date
                    safe_execute("day_summary", send_day_summaries)

                # 3. Weekly digest on Monday at 08:30
                if (
                    now.weekday() == 0
                    and hour == 8
                    and minute == 30
                    and last_weekly_date != current_date
                ):
                    last_weekly_date = current_date
                    safe_execute("weekly_digest", send_weekly_digest)

                # 4. Database backup at 01:30, before the integrity check
                if (
                    settings.BACKUP_ENABLED
                    and hour == 1
                    and minute == 30
                    and last_backup_date != current_date
                ):
                    last_backup_date = current_date
                    safe_execute("backup_db", lambda: call_command("backup_db"))

                # 5. Integrity check at 02:00
                if hour == 2 and minute == 0 and last_integrity_date != current_date:
                    last_integrity_date = current_date
                    safe_execute(
                        "verify_integrity", lambda: call_command("verify_integrity")
                    )

                time.sleep(30)
            except KeyboardInterrupt:
                self.stdout.write("Scheduler stopped by user.")
                break
            except Exception as e:
                logger.error("Unexpected error in scheduler loop: %s", e, exc_info=True)
                time.sleep(5)
