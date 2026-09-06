param(
    [string]$BackupDirectory = (Join-Path $PSScriptRoot "..\backups"),
    [switch]$KeepRestoreDatabase
)

$ErrorActionPreference = "Stop"

$sourceDatabase = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "scholar_agent" }
$databaseUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "scholar" }
$databasePassword = if ($env:POSTGRES_PASSWORD) { $env:POSTGRES_PASSWORD } else { "scholar" }
$restoreDatabase = "${sourceDatabase}_restore_check_$PID"

if ($restoreDatabase -eq $sourceDatabase) {
    throw "Restore database must never equal the source database."
}
if ($sourceDatabase -notmatch '^[A-Za-z0-9_]+$' -or $restoreDatabase -notmatch '^[A-Za-z0-9_]+$') {
    throw "Database names may contain only letters, digits, and underscores."
}

$dockerServer = docker version --format '{{json .Server}}' 2>$null
if (
    $LASTEXITCODE -ne 0 -or
    [string]::IsNullOrWhiteSpace($dockerServer) -or
    $dockerServer -eq "null"
) {
    throw "Docker engine is unavailable. Start or repair Docker Desktop before running the rehearsal."
}
docker compose up -d db
if ($LASTEXITCODE -ne 0) { throw "PostgreSQL service failed to start." }

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupName = "${sourceDatabase}_${timestamp}.dump"
$backupRoot = [System.IO.Path]::GetFullPath($BackupDirectory)
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
$backupPath = Join-Path $backupRoot $backupName
$containerBackup = "/tmp/$backupName"
$containerRestore = "/tmp/scholar_restore_check.dump"
$restoreCreated = $false

try {
    docker compose exec -T db pg_dump -U $databaseUser -d $sourceDatabase --format=custom --no-owner --no-acl --file=$containerBackup
    if ($LASTEXITCODE -ne 0) { throw "pg_dump failed." }
    docker compose cp "db:$containerBackup" $backupPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $backupPath)) {
        throw "Backup copy failed."
    }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $backupPath).Hash

    docker compose cp $backupPath "db:$containerRestore"
    if ($LASTEXITCODE -ne 0) { throw "Restore fixture copy failed." }
    docker compose exec -T db pg_restore --list $containerRestore | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Backup archive validation failed." }

    docker compose exec -T db dropdb --if-exists -U $databaseUser $restoreDatabase
    docker compose exec -T db createdb -U $databaseUser $restoreDatabase
    if ($LASTEXITCODE -ne 0) { throw "Disposable restore database creation failed." }
    $restoreCreated = $true

    docker compose exec -T db pg_restore -U $databaseUser -d $restoreDatabase --exit-on-error --no-owner --no-acl $containerRestore
    if ($LASTEXITCODE -ne 0) { throw "pg_restore failed." }
    docker compose exec -T db psql -U $databaseUser -d $restoreDatabase -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector; SELECT extversion FROM pg_extension WHERE extname='vector'; SELECT COUNT(*) AS papers FROM papers; SELECT COUNT(*) AS chunks FROM paper_chunks;"
    if ($LASTEXITCODE -ne 0) { throw "Restored database verification failed." }

    $migrationUrl = "postgresql+psycopg://${databaseUser}:${databasePassword}@db:5432/${restoreDatabase}"
    docker compose run --rm -e "SCHOLAR_DATABASE_URL=$migrationUrl" migrate python -m alembic downgrade -1
    if ($LASTEXITCODE -ne 0) { throw "Alembic rollback rehearsal failed." }
    docker compose run --rm -e "SCHOLAR_DATABASE_URL=$migrationUrl" migrate python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Alembic recovery to head failed." }
    docker compose run --rm -e "SCHOLAR_DATABASE_URL=$migrationUrl" migrate python -m alembic current
    if ($LASTEXITCODE -ne 0) { throw "Alembic current verification failed." }

    Write-Host "PostgreSQL disaster rehearsal passed."
    Write-Host "Backup: $backupPath"
    Write-Host "SHA256: $hash"
    Write-Host "Disposable restore database: $restoreDatabase"
}
finally {
    docker compose exec -T db sh -lc "rm -f '$containerBackup' '$containerRestore'" | Out-Null
    if ($restoreCreated -and -not $KeepRestoreDatabase) {
        docker compose exec -T db dropdb --if-exists -U $databaseUser $restoreDatabase | Out-Null
    }
}
