"""Nightly database backup with retention and an optional off-site copy.

Cross-platform (no PowerShell needed on a Linux server):

* PostgreSQL: ``pg_dump --format=custom`` (restore with ``pg_restore``). The
  password goes through ``PGPASSWORD``, never the command line.
* SQLite (development): the sqlite3 online-backup API, safe while the app runs.

A file is written under a ``.partial`` name and renamed only once complete, so a
crash never leaves a truncated file that looks like a good backup. Retention
never deletes the newest backup. With ``BACKUP_RCLONE_REMOTE`` set, the new file
is copied off the server with ``rclone copy`` (S3, Google Drive, a NAS...).

Every run is recorded as a ``SystemHealthCheck`` named ``backup``; a failure
also raises the ``backup_failed`` alert (``notifications``).
"""

import os
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

CHECK_NAME = "backup"
PREFIX = "erp_db_"
SUFFIXES = (".dump", ".sqlite3")


class BackupError(Exception):
    """A backup step failed; the message says which and why."""


@dataclass
class BackupResult:
    path: Path
    size: int
    deleted: list
    offsite: str | None


def default_dir() -> Path:
    return Path(getattr(settings, "BACKUP_DIR", settings.BASE_DIR / "backups"))


def _db():
    # The live connection's settings (the test database during tests).
    return connection.settings_dict


def _pg_major(version_text: str) -> int:
    # "pg_dump (PostgreSQL) 16.4 (Debian 16.4-1)" -> 16
    for token in version_text.split():
        head = token.split(".")[0]
        if head.isdigit():
            return int(head)
    raise BackupError(f"Cannot read the pg_dump version from: {version_text!r}")


def _dump_postgres(target: Path) -> None:
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise BackupError(
            "pg_dump is not installed. Install the PostgreSQL client "
            "(apt install postgresql-client) on the machine running backups."
        )
    client = _pg_major(
        subprocess.run(
            [pg_dump, "--version"], capture_output=True, text=True, check=True
        ).stdout
    )
    server = connection.pg_version // 10000  # e.g. 160004 -> 16
    if client < server:
        raise BackupError(
            f"pg_dump {client} cannot back up a PostgreSQL {server} server. "
            f"Install postgresql-client-{server}."
        )
    db = _db()
    env = {**os.environ, "PGPASSWORD": str(db.get("PASSWORD") or "")}
    cmd = [pg_dump, "--format=custom", f"--file={target}", "--no-password"]
    for flag, key in (("--host", "HOST"), ("--port", "PORT"), ("--username", "USER")):
        if db.get(key):
            cmd.append(f"{flag}={db[key]}")
    cmd.append(str(db["NAME"]))
    done = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if done.returncode != 0:
        raise BackupError(f"pg_dump failed ({done.returncode}): {done.stderr.strip()}")


def _dump_sqlite(target: Path) -> None:
    # Back up through Django's own connection: consistent while the app runs,
    # and it also works for an in-memory database.
    if connection.in_atomic_block:
        # SQLite's online backup waits forever for a lock held by an open
        # write transaction; refuse instead of hanging the scheduler.
        raise BackupError("Cannot back up SQLite from inside a transaction.")
    connection.ensure_connection()
    dest = sqlite3.connect(str(target))
    try:
        connection.connection.backup(dest)
    finally:
        dest.close()


def _backups(directory: Path) -> list[Path]:
    return sorted(
        (
            p
            for p in directory.glob(f"{PREFIX}*")
            if p.suffix in SUFFIXES and p.is_file()
        ),
        key=lambda p: p.stat().st_mtime,
    )


def prune(directory: Path, retention_days: int) -> list[Path]:
    """Delete backups older than ``retention_days``; always keep the newest."""
    files = _backups(directory)
    cutoff = time.time() - retention_days * 86400
    deleted = []
    for path in files[:-1]:  # never the newest
        if path.stat().st_mtime < cutoff:
            path.unlink()
            deleted.append(path)
    return deleted


def _offsite(path: Path, remote: str) -> str:
    rclone = shutil.which("rclone")
    if not rclone:
        raise BackupError(
            "BACKUP_RCLONE_REMOTE is set but rclone is not installed "
            "(https://rclone.org/install/)."
        )
    done = subprocess.run(
        [rclone, "copy", str(path), remote], capture_output=True, text=True
    )
    if done.returncode != 0:
        raise BackupError(
            f"Off-site copy to {remote} failed ({done.returncode}): "
            f"{done.stderr.strip()}"
        )
    return remote


def run_backup(directory=None, retention_days=None, offsite_remote=None):
    """Make one backup, prune old ones and copy off-site. Returns a
    :class:`BackupResult`; raises :class:`BackupError` on any failure."""
    directory = Path(directory or default_dir())
    retention_days = (
        retention_days
        if retention_days is not None
        else getattr(settings, "BACKUP_RETENTION_DAYS", 30)
    )
    offsite_remote = (
        offsite_remote
        if offsite_remote is not None
        else getattr(settings, "BACKUP_RCLONE_REMOTE", "")
    )
    directory.mkdir(parents=True, exist_ok=True)

    engine = _db()["ENGINE"]
    stamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
    if "postgresql" in engine:
        final = directory / f"{PREFIX}{stamp}.dump"
        dump = _dump_postgres
    elif "sqlite" in engine:
        final = directory / f"{PREFIX}{stamp}.sqlite3"
        dump = _dump_sqlite
    else:
        raise BackupError(f"Backups are not supported for database engine {engine}.")

    partial = final.with_name(final.name + ".partial")
    try:
        dump(partial)
        size = partial.stat().st_size if partial.exists() else 0
        if size == 0:
            raise BackupError("The backup file is empty.")
        os.replace(partial, final)
    finally:
        if partial.exists():
            partial.unlink()

    deleted = prune(directory, retention_days)
    remote = _offsite(final, offsite_remote) if offsite_remote else None
    return BackupResult(path=final, size=size, deleted=deleted, offsite=remote)


def record(status: str, details: dict) -> None:
    from core.models import SystemHealthCheck

    SystemHealthCheck.objects.create(
        check_name=CHECK_NAME, status=status, details=details
    )
