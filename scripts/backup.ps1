<#
.SYNOPSIS
    Nightly PostgreSQL backup script with retention cleanup.
.DESCRIPTION
    Creates a timestamped PostgreSQL backup using pg_dump (custom format or gzipped SQL)
    and removes backups older than the retention period (default: 30 days).
.PARAMETER DatabaseUrl
    Database connection string. Defaults to $env:DATABASE_URL.
.PARAMETER BackupDir
    Directory to store backups. Defaults to $env:BACKUP_DIR or "./backups".
.PARAMETER RetentionDays
    Number of days to keep backups. Defaults to 30.
.PARAMETER DryRun
    If set, prints actions without executing pg_dump or deleting files.
.PARAMETER Mock
    If set, generates a mock backup file instead of executing pg_dump (for testing).
#>
[CmdletBinding()]
param(
    [string]$DatabaseUrl = $env:DATABASE_URL,
    [string]$BackupDir = $(if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { Join-Path $PSScriptRoot "..\backups" }),
    [int]$RetentionDays = 30,
    [switch]$DryRun,
    [switch]$Mock
)

$ErrorActionPreference = "Stop"

Write-Host "=========================================="
Write-Host "Nidhi Masala ERP - Nightly Database Backup"
Write-Host "=========================================="
Write-Host "Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "Backup Directory: $BackupDir"
Write-Host "Retention: $RetentionDays days"

if (-not $DryRun -and -not (Test-Path -Path $BackupDir)) {
    Write-Host "Creating backup directory: $BackupDir"
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupFileName = "erp_db_$timestamp.dump"
$backupFilePath = Join-Path $BackupDir $backupFileName

# 1. Execute Backup
if ($DryRun) {
    Write-Host "[DRY-RUN] Would run pg_dump to $backupFilePath"
} elseif ($Mock) {
    Write-Host "[MOCK] Generating mock backup file at $backupFilePath"
    Set-Content -Path $backupFilePath -Value "-- Mock PostgreSQL Backup Generated $timestamp" -Encoding UTF8
    Write-Host "[OK] Mock backup created: $backupFilePath"
} else {
    if (-not $DatabaseUrl) {
        throw "DATABASE_URL is not set. Please provide -DatabaseUrl or set the environment variable."
    }
    Write-Host "Starting pg_dump..."
    & pg_dump --dbname="$DatabaseUrl" --format=custom --file="$backupFilePath"
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump failed with exit code $LASTEXITCODE"
    }
    Write-Host "[OK] Backup completed successfully: $backupFilePath (Size: $((Get-Item $backupFilePath).Length) bytes)"
}

# 2. Retention Cleanup
Write-Host "Checking for backups older than $RetentionDays days..."
$cutoffDate = (Get-Date).AddDays(-$RetentionDays)

if (Test-Path -Path $BackupDir) {
    $oldFiles = Get-ChildItem -Path $BackupDir -File | Where-Object {
        ($_.Name -like "erp_db_*.dump" -or $_.Name -like "erp_db_*.sql*") -and ($_.LastWriteTime -lt $cutoffDate)
    }

    $deletedCount = 0
    foreach ($file in $oldFiles) {
        if ($DryRun) {
            Write-Host "[DRY-RUN] Would delete old backup: $($file.FullName) (Last Modified: $($file.LastWriteTime))"
        } else {
            Write-Host "Removing old backup: $($file.FullName) (Last Modified: $($file.LastWriteTime))"
            Remove-Item -Path $file.FullName -Force
            $deletedCount++
        }
    }

    if ($deletedCount -gt 0) {
        Write-Host "[OK] Cleaned up $deletedCount expired backup file(s)."
    } else {
        Write-Host "No expired backup files found to delete."
    }
}

Write-Host "Backup job completed at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')."
