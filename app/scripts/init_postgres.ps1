[CmdletBinding()]
param(
    [string]$PgBin = "",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 5432,
    [string]$AdminUser = "postgres",
    [string]$DatabaseName = "paper_review",
    [string]$AppUser = "paper_review_app",
    [string]$AppPassword = "",
    [switch]$UpdateEnv = $true,
    [switch]$RecreateDatabase,
    [switch]$SkipServiceCheck
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\")).Path
$SchemaFile = Join-Path $ProjectRoot "app\database\schema.sql"
$EnvFile = Join-Path $ProjectRoot "app\.env"

function Resolve-PgTool([string]$Name) {
    if ($PgBin) {
        $candidate = Join-Path $PgBin $Name
        if (Test-Path $candidate) { return $candidate }
    }
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $known = @(
        "E:\Appdata\postgresql\bin\$Name",
        "C:\Program Files\PostgreSQL\18\bin\$Name",
        "C:\Program Files\PostgreSQL\17\bin\$Name",
        "C:\Program Files\PostgreSQL\16\bin\$Name"
    ) | Where-Object { Test-Path $_ }
    if ($known) { return $known[0] }
    throw "Cannot find $Name. Pass -PgBin <PostgreSQL bin directory>."
}

function New-RandomPassword([int]$Length = 32) {
    $alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^*-_"
    $bytes = New-Object byte[] $Length
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return (-join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] }))
}

function Invoke-Psql([string]$Psql, [string]$Db, [string]$Sql, [string]$Password) {
    $env:PGPASSWORD = $Password
    try {
        & $Psql -X -w -v ON_ERROR_STOP=1 -h $HostName -p $Port -U $AdminUser -d $Db -c $Sql
        if ($LASTEXITCODE -ne 0) { throw "psql failed with exit code $LASTEXITCODE" }
    } finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path $SchemaFile)) { throw "Schema file not found: $SchemaFile" }
if (-not $SkipServiceCheck) {
    $service = Get-Service -Name "postgresql-x64-18" -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne "Running") { throw "PostgreSQL service is not running: $($service.Status)" }
}

$Psql = Resolve-PgTool "psql.exe"
$createdPassword = $false
if (-not $AppPassword) {
    $AppPassword = New-RandomPassword
    $createdPassword = $true
}

$adminPassword = $env:PGPASSWORD
if (-not $adminPassword) {
    $secure = Read-Host "PostgreSQL administrator password for $AdminUser" -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $adminPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

Write-Host "Creating role '$AppUser' (existing role is preserved)..."
$roleSql = @"
DO `$`$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$($AppUser.Replace("'", "''"))') THEN
        CREATE ROLE "$AppUser" LOGIN PASSWORD '$($AppPassword.Replace("'", "''"))';
    ELSE
        ALTER ROLE "$AppUser" LOGIN PASSWORD '$($AppPassword.Replace("'", "''"))';
    END IF;
END
`$`$;
"@
Invoke-Psql $Psql "postgres" $roleSql $adminPassword

$dbExists = (& $Psql -X -w -t -A -h $HostName -p $Port -U $AdminUser -d "postgres" -c "SELECT 1 FROM pg_database WHERE datname = '$($DatabaseName.Replace("'", "''"))';" 2>$null)
if ($LASTEXITCODE -ne 0) { throw "Unable to query PostgreSQL. Check the administrator password." }
if (($dbExists -match "1") -and $RecreateDatabase) {
    Write-Host "Dropping only the project database '$DatabaseName' as requested..."
    $dropDbSql = 'DROP DATABASE "' + $DatabaseName + '" WITH (FORCE);'
    Invoke-Psql $Psql "postgres" $dropDbSql $adminPassword
    $dbExists = ""
}
if (-not ($dbExists -match "1")) {
    $createDbSql = 'CREATE DATABASE "' + $DatabaseName + '" OWNER "' + $AppUser + '";'
    Invoke-Psql $Psql "postgres" $createDbSql $adminPassword
} else {
    Write-Host "Database '$DatabaseName' already exists; it will not be dropped."
}

$env:PGPASSWORD = $adminPassword
try {
    & $Psql -X -w -v ON_ERROR_STOP=1 -h $HostName -p $Port -U $AdminUser -d $DatabaseName -f $SchemaFile
    if ($LASTEXITCODE -ne 0) { throw "schema.sql failed with exit code $LASTEXITCODE" }
    $grantSql = @"
GRANT CONNECT ON DATABASE "$DatabaseName" TO "$AppUser";
GRANT USAGE, CREATE ON SCHEMA public TO "$AppUser";
GRANT SELECT, INSERT, UPDATE, DELETE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public TO "$AppUser";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO "$AppUser";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE, REFERENCES, TRIGGER ON TABLES TO "$AppUser";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO "$AppUser";
"@
    & $Psql -X -w -v ON_ERROR_STOP=1 -h $HostName -p $Port -U $AdminUser -d $DatabaseName -c $grantSql
    if ($LASTEXITCODE -ne 0) { throw "grant setup failed with exit code $LASTEXITCODE" }
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

if ($UpdateEnv) {
    $lines = if (Test-Path $EnvFile) { [Collections.Generic.List[string]](Get-Content $EnvFile) } else { [Collections.Generic.List[string]]::new() }
    $values = @{
        POSTGRES_HOST = $HostName; POSTGRES_PORT = "$Port"; POSTGRES_DB = $DatabaseName; POSTGRES_USER = $AppUser; POSTGRES_PASSWORD = $AppPassword
        DB_HOST = $HostName; DB_PORT = "$Port"; DB_NAME = $DatabaseName; DB_USER = $AppUser; DB_PASSWORD = $AppPassword
        EXPERT_DB_HOST = $HostName; EXPERT_DB_PORT = "$Port"; EXPERT_DB_NAME = $DatabaseName; EXPERT_DB_USER = $AppUser; EXPERT_DB_PASSWORD = $AppPassword
    }
    foreach ($key in $values.Keys) {
        $found = $false
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match "^\s*#?\s*$([regex]::Escape($key))=") { $lines[$i] = "$key=$($values[$key])"; $found = $true; break }
        }
        if (-not $found) { $lines.Add("$key=$($values[$key])") }
    }
    Set-Content -Path $EnvFile -Value $lines -Encoding UTF8
    Write-Host "Updated local app/.env (ignored by Git)."
}

Write-Host "Database ready: postgresql://$AppUser@${HostName}:$Port/$DatabaseName"
if ($createdPassword) { Write-Host "Generated app password was written only to app/.env; it is not stored in Git." }
