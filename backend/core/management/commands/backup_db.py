from django.core.management.base import BaseCommand, CommandError

from core.services import backup as backup_service


class Command(BaseCommand):
    help = (
        "Back up the database (pg_dump for PostgreSQL, the online-backup API for "
        "SQLite), delete backups older than the retention period (never the "
        "newest) and copy the new file off-site when BACKUP_RCLONE_REMOTE is set. "
        "Run nightly by run_scheduler; a failure raises the backup_failed alert."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=None, help="Backup folder (BACKUP_DIR).")
        parser.add_argument(
            "--retention-days",
            type=int,
            default=None,
            help="Delete backups older than this (BACKUP_RETENTION_DAYS, 30).",
        )
        parser.add_argument(
            "--offsite",
            default=None,
            help="rclone destination such as 'gdrive:erp-backups' "
            "(BACKUP_RCLONE_REMOTE). Pass '' to skip.",
        )

    def handle(self, *args, **options):
        try:
            result = backup_service.run_backup(
                directory=options["dir"],
                retention_days=options["retention_days"],
                offsite_remote=options["offsite"],
            )
        except Exception as exc:  # any failure must reach the Admin
            message = str(exc) or exc.__class__.__name__
            backup_service.record("alert", {"error": message})
            from notifications.services.instant import on_backup_failed

            on_backup_failed(message)
            raise CommandError(f"Backup failed: {message}")

        backup_service.record(
            "ok",
            {
                "file": result.path.name,
                "bytes": result.size,
                "deleted": [p.name for p in result.deleted],
                "offsite": result.offsite,
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Backup written: {result.path} ({result.size} bytes)"
                + (f", copied to {result.offsite}" if result.offsite else "")
                + (
                    f"; removed {len(result.deleted)} old file(s)"
                    if result.deleted
                    else ""
                )
            )
        )
