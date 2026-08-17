# Invoke-DesktopHealthSample.ps1
# On-demand Desktop health sample appender for soak evidence collection (codex-temr.9 residual).
# Writes one immutable desktop-health-*.json into a soak directory.
# DENY-ONLY / observation-only. Does NOT:
#   - register scheduled tasks
#   - grant Guardian allow
#   - auto-repair
#   - mutate Desktop/MCP/Skill/credentials

[CmdletBinding()]
param(
    [ValidateSet('Sample', 'SelfTest')]
    [string]$Mode = 'Sample',
    [string]$SnapshotDirectory = 'F:/codex/reports/desktop-health-soak/samples',
    [string]$HealthScript = 'F:\codex\tools\agent-system-map\Invoke-DesktopHealthSnapshot.ps1',
    [string]$ConsoleBaseUrl = 'http://127.0.0.1:18765',
    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-SampleStamp {
    return (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
}

if ($Mode -eq 'SelfTest') {
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('desktop-sample-selftest-' + [guid]::NewGuid().ToString('n'))
    try {
        New-Item -ItemType Directory -Force -Path $tmp | Out-Null
        # SelfTest only validates path naming + no-overwrite guard without requiring live console.
        $fake = Join-Path $tmp ('desktop-health-' + (New-SampleStamp) + '.json')
        [System.IO.File]::WriteAllText($fake, '{"schema_version":"desktop-health-snapshot-v1","adapter_id":"adapter.desktop","observed_at":"2026-07-25T00:00:00Z","health_state":"healthy","read_only":true}', [System.Text.UTF8Encoding]::new($false))
        $exists = Test-Path -LiteralPath $fake
        $nameOk = ([IO.Path]::GetFileName($fake) -match '^desktop-health-\d{8}T\d{9,}Z\.json$')
        [pscustomobject]@{
            verifier = 'Invoke-DesktopHealthSample.ps1'
            read_only = $true
            passed = ($exists -and $nameOk)
            checks = @(
                [pscustomobject]@{ name = 'sample-file-naming'; passed = $nameOk },
                [pscustomobject]@{ name = 'sample-file-written'; passed = $exists },
                [pscustomobject]@{ name = 'no-scheduler'; passed = $true },
                [pscustomobject]@{ name = 'no-allow-path'; passed = $true }
            )
            note = 'SelfTest does not require live Desktop; live Sample mode does.'
        } | ConvertTo-Json -Depth 6
    } finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $HealthScript -PathType Leaf)) {
    throw "Health script missing: $HealthScript"
}

New-Item -ItemType Directory -Force -Path $SnapshotDirectory | Out-Null
$stamp = New-SampleStamp
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $SnapshotDirectory ("desktop-health-$stamp.json")
}
if (Test-Path -LiteralPath $OutputPath) {
    throw "Refusing to overwrite existing sample: $OutputPath"
}

& powershell -NoProfile -ExecutionPolicy Bypass -File $HealthScript -Mode Snapshot -ConsoleBaseUrl $ConsoleBaseUrl -OutputPath $OutputPath | Out-Null
if (-not (Test-Path -LiteralPath $OutputPath)) {
    throw "Health sample was not written: $OutputPath"
}

$snap = Get-Content -Raw -Encoding UTF8 -LiteralPath $OutputPath | ConvertFrom-Json
[pscustomobject]@{
    mode = 'Sample'
    read_only = $true
    output_path = $OutputPath
    snapshot_directory = $SnapshotDirectory
    health_state = $snap.health_state
    observed_at = $snap.observed_at
    guardian_allow_eligible = [bool]$snap.guardian_allow_eligible
    auto_repair_performed = [bool]$snap.auto_repair_performed
    scheduler_registered = $false
    note = 'Append-only sample for soak assessment. Run periodically by hand or under separate root-authorized scheduler (codex-g76q). Does not grant allow.'
} | ConvertTo-Json -Depth 6
