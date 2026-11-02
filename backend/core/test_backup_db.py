"""core.backup_db: nightly backup, retention, off-site copy and failure alert."""

import os
import sqlite3
import tempfile
import time
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from core.models import SystemHealthCheck
from core.services import backup as backup_service
from notifications.models import AlertRule, Notification
from users.models import User


class _BackupMixin:
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="erp_backup_test_"))
        self.addCleanup(lambda: [p.unlink() for p in self.dir.iterdir()])

    def run_cmd(self, **options):
        out = StringIO()
        call_command("backup_db", dir=str(self.dir), offsite="", stdout=out, **options)
        return out.getvalue()

    def old_file(self, name, days):
        path = self.dir / name
        path.write_text("old")
        past = time.time() - days * 86400
        os.utime(path, (past, past))
        return path


class BackupTestBase(_BackupMixin, TestCase):
    pass


class BackupTests(_BackupMixin, TransactionTestCase):
    """No wrapping transaction: SQLite's online backup needs the lock free."""

    def setUp(self):
        super().setUp()
        if connection.vendor != "sqlite":
            self.skipTest("SQLite backup path; PostgreSQL uses pg_dump")

    def test_refuses_inside_a_transaction_instead_of_hanging(self):
        from django.db import transaction

        with transaction.atomic():
            with self.assertRaises(backup_service.BackupError):
                backup_service.run_backup(self.dir, 30, "")

    def test_backup_is_a_readable_database_and_is_recorded(self):
        out = self.run_cmd()
        files = list(self.dir.glob("erp_db_*.sqlite3"))
        self.assertEqual(len(files), 1, out)
        db = sqlite3.connect(str(files[0]))
        try:
            tables = db.execute(
                "select count(*) from sqlite_master where type='table'"
            ).fetchone()[0]
        finally:
            db.close()
        self.assertGreater(tables, 10)
        check = SystemHealthCheck.objects.filter(check_name="backup").latest("id")
        self.assertEqual(check.status, "ok")
        self.assertEqual(check.details["file"], files[0].name)
        self.assertFalse(list(self.dir.glob("*.partial")))

    def test_retention_deletes_old_files_but_keeps_the_newest(self):
        old = self.old_file("erp_db_20200101_000000.sqlite3", 60)
        recent = self.old_file("erp_db_20200102_000000.sqlite3", 5)
        other = self.old_file("notes.txt", 90)  # not a backup: untouched
        self.run_cmd(retention_days=30)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(other.exists())

    def test_prune_never_deletes_the_only_backup(self):
        only = self.old_file("erp_db_20200101_000000.dump", 365)
        self.assertEqual(backup_service.prune(self.dir, 30), [])
        self.assertTrue(only.exists())

    def test_offsite_copy_runs_rclone(self):
        done = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(
            backup_service.shutil, "which", return_value="/usr/bin/rclone"
        ), mock.patch.object(
            backup_service.subprocess, "run", return_value=done
        ) as run:
            result = backup_service.run_backup(self.dir, 30, "gdrive:erp")
        self.assertEqual(result.offsite, "gdrive:erp")
        args = run.call_args[0][0]
        self.assertEqual(args[:2], ["/usr/bin/rclone", "copy"])
        self.assertEqual(args[-1], "gdrive:erp")


class BackupFailureTests(BackupTestBase):
    def test_failure_records_alerts_and_raises(self):
        admin = User.objects.create_user(
            username="admin1", email="admin1@example.com", password="x-Strong-pass-1"
        )
        rule = AlertRule.objects.create(key="backup_failed", is_active=True)
        rule.recipients.add(admin)
        with mock.patch.object(
            backup_service,
            "run_backup",
            side_effect=backup_service.BackupError("disk full"),
        ):
            with self.assertRaises(CommandError) as ctx:
                self.run_cmd()
        self.assertIn("disk full", str(ctx.exception))
        check = SystemHealthCheck.objects.filter(check_name="backup").latest("id")
        self.assertEqual((check.status, check.details["error"]), ("alert", "disk full"))
        notif = Notification.objects.get(rule_key="backup_failed", user=admin)
        self.assertEqual(notif.params["error"], "disk full")

    def test_missing_rclone_is_a_clear_error(self):
        with mock.patch.object(backup_service.shutil, "which", return_value=None):
            with self.assertRaises(backup_service.BackupError) as ctx:
                backup_service._offsite(self.dir / "x.dump", "gdrive:erp")
        self.assertIn("rclone is not installed", str(ctx.exception))

    def test_pg_dump_version_parsing(self):
        self.assertEqual(
            backup_service._pg_major("pg_dump (PostgreSQL) 16.4 (Debian 16.4-1)"), 16
        )

    @override_settings(BACKUP_ENABLED=False)
    def test_setting_exists_for_the_scheduler(self):
        from django.conf import settings

        self.assertFalse(settings.BACKUP_ENABLED)
