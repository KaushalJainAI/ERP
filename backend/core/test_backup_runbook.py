import os
import shutil
import subprocess
import tempfile
import time
from django.test import SimpleTestCase


class BackupRunbookTests(SimpleTestCase):
    """Verifies Card G-7 backup scripts (scripts/backup.ps1 and scripts/verify_backup.ps1)."""

    def setUp(self):
        self.repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        self.backup_script = os.path.join(self.repo_root, "scripts", "backup.ps1")
        self.verify_script = os.path.join(
            self.repo_root, "scripts", "verify_backup.ps1"
        )

    def test_scripts_exist(self):
        self.assertTrue(os.path.exists(self.backup_script), "backup.ps1 must exist")
        self.assertTrue(
            os.path.exists(self.verify_script), "verify_backup.ps1 must exist"
        )

    def test_backup_and_retention_mock(self):
        tmp_dir = tempfile.mkdtemp(prefix="test_erp_backup_")
        try:
            # Create an expired backup file (35 days old)
            old_file = os.path.join(tmp_dir, "erp_db_20260901_000000.dump")
            with open(old_file, "w", encoding="utf-8") as f:
                f.write("-- old backup")
            past_time = time.time() - (35 * 86400)
            os.utime(old_file, (past_time, past_time))

            # Run backup.ps1 in Mock mode
            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                self.backup_script,
                "-BackupDir",
                tmp_dir,
                "-Mock",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            self.assertIn("[OK] Mock backup created", result.stdout)
            self.assertIn("Cleaned up 1 expired backup file(s)", result.stdout)

            # Old file must be deleted
            self.assertFalse(
                os.path.exists(old_file),
                "Old backup was not deleted by retention policy",
            )

            # New backup file must exist
            files = [
                f
                for f in os.listdir(tmp_dir)
                if f.startswith("erp_db_") and f.endswith(".dump")
            ]
            self.assertEqual(len(files), 1)
            new_backup_file = os.path.join(tmp_dir, files[0])

            # Run verify_backup.ps1 on the newly created backup
            verify_cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                self.verify_script,
                "-BackupFile",
                new_backup_file,
                "-Mock",
            ]
            verify_res = subprocess.run(
                verify_cmd, capture_output=True, text=True, check=True
            )
            self.assertIn("[OK] Backup file exists and is non-empty", verify_res.stdout)
            self.assertIn("[OK] Mock verification drill successful", verify_res.stdout)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_backup_dry_run(self):
        tmp_dir = tempfile.mkdtemp(prefix="test_erp_dryrun_")
        try:
            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                self.backup_script,
                "-BackupDir",
                tmp_dir,
                "-DryRun",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            self.assertIn("[DRY-RUN] Would run pg_dump", result.stdout)

            # In dry-run, no files are written
            files = os.listdir(tmp_dir)
            self.assertEqual(len(files), 0)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_verify_rejects_missing_file(self):
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            self.verify_script,
            "-BackupFile",
            "C:\\nonexistent_backup_file.dump",
            "-Mock",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Backup file not found", result.stderr + result.stdout)
