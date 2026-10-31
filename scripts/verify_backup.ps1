<#
.SYNOPSIS
    Backup verification and restore drill script.
.DESCRIPTION
    Verifies the integrity of a database backup file and optionally executes a
    restore drill into an isolated scratch database (NEVER the production database).
.PARAMETER BackupFile
    Path to the backup file (.dump or .sql/.sql.gz). Defaults to latest backup in backup directory.
.PARAMETER ScratchDbUrl
    Connection string for the scratch/test database. NEVER supply the production database URL!
.PARAMETER DryRun
    If set, previews the restore steps without executing pg_restore.
.PARAMETER Mock
    If set, validates file presence and simulates the drill without requiring Postgres tools.
#>
[CmdletBinding()]
param(
    [string]$BackupFile,
    [string]$ScratchDbUrl,
    [switch]$DryRun,
    [switch]$Mock
)

$ErrorActionPreference = "Stop"

Write-Host "=========================================="
Write-Host "Nidhi Masala ERP - Backup Verification & Drill"
Write-Host "=========================================="

# Find latest backup if not specified
if (-not $BackupFile) {
    $backupDir = Join-Path $PSScriptRoot "..\backups"
    if (Test-Path $backupDir) {
        $latest = Get-ChildItem -Path $backupDir -File | Where-Object {
            $_.Name -like "erp_db_*.dump" -or $_.Name -like "erp_db_*.sql*"
        } | Sort-Object LastWriteTime -Descending | Select-Object -First 1

        if ($latest) {
            $BackupFile = $latest.FullName
        }
    }
}

if (-not $BackupFile -or -not (Test-Path $BackupFile)) {
    throw "Backup file not found: '$BackupFile'"
}

$fileItem = Get-Item $BackupFile
Write-Host "Target Backup: $($fileItem.FullName)"
Write-Host "File Size: $($fileItem.Length) bytes"
Write-Host "Last Modified: $($fileItem.LastWriteTime)"

if ($fileItem.Length -le 0) {
    throw "Backup file is empty (0 bytes): $($fileItem.FullName)"
}

Write-Host "[OK] Backup file exists and is non-empty."

# Safety guard on ScratchDbUrl
if ($ScratchDbUrl) {
    if ($ScratchDbUrl -match "5432/erp_db" -or $ScratchDbUrl -match "5433/erp_db") {
        # Check if user accidentally pointed at main database
        if (-not ($ScratchDbUrl -match "scratch" -or $ScratchDbUrl -match "test")) {
            throw "SAFETY ERROR: ScratchDbUrl '$ScratchDbUrl' appears to be a production or main database! Name must contain 'scratch' or 'test'."
        }
    }
}

if ($DryRun) {
    Write-Host "[DRY-RUN] Backup verification passed. Would restore into scratch DB if specified."
    return
}

if ($Mock) {
    Write-Host "[MOCK] Verifying backup contents..."
    $content = Get-Content -Path $BackupFile -TotalCount 5
    Write-Host "[MOCK] Header preview: $($content -join ' ')"
    Write-Host "[MOCK] Drill simulation: restore into scratch DB passed."
    Write-Host "[OK] Mock verification drill successful."
    return
}

# If ScratchDbUrl provided, perform live restore drill
if ($ScratchDbUrl) {
    Write-Host "Executing restore drill into scratch database..."
    Write-Host "Scratch DB: $ScratchDbUrl"

    if ($BackupFile -like "*.dump") {
        & pg_restore --dbname="$ScratchDbUrl" --clean --if-exists --no-owner "$BackupFile"
    } elseif ($BackupFile -like "*.gz") {
        & gunzip -c "$BackupFile" | & psql "$ScratchDbUrl"
    } else {
        & psql "$ScratchDbUrl" -f "$BackupFile"
    }

    if ($LASTEXITCODE -ne 0) {
        throw "Restore drill failed with exit code $LASTEXITCODE"
    }

    Write-Host "[OK] Restore drill completed successfully into scratch database."
} else {
    Write-Host "Notice: No -ScratchDbUrl provided. Backup file presence and size verified. To perform a full restore drill, supply -ScratchDbUrl."
}
