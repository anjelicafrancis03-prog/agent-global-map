[CmdletBinding()]
param(
    [ValidateSet('Evaluate', 'SelfTest')]
    [string]$Mode = 'Evaluate',
    [string]$SnapshotDirectory = 'F:/codex/reports/desktop-health-soak/samples',
    [string]$OutputPath,
    [ValidateRange(1, 720)]
    [int]$RequiredCoverageHours = 24,
    [ValidateRange(1, 1440)]
    [int]$MaxGapMinutes = 10,
    [ValidateRange(1, 1440)]
    [int]$LatestSnapshotMaxAgeMinutes = 15
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertTo-UtcOrNull {
    param([object]$Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) { return $null }
    try {
        # codex-1npu.3.2: 不做字符串往返（[string] 化会丢 Kind/时区，被本地时区二次换算 -8h）
        if ($Value -is [datetime]) { return ([datetimeoffset]$Value).UtcDateTime }
        if ($Value -is [datetimeoffset]) { return $Value.UtcDateTime }
        return ([datetimeoffset]::Parse([string]$Value, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeUniversal)).UtcDateTime
    } catch { return $null }
}

function Write-ImmutableJson {
    param([object]$Value, [string]$Path)
    if (Test-Path -LiteralPath $Path) { throw "Refusing to overwrite existing assessment: $Path" }
    $directory = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($directory)) { [void](New-Item -ItemType Directory -Path $directory -Force) }
    [System.IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 16), [System.Text.UTF8Encoding]::new($false))
}

function Get-Assessment {
    param([string]$Directory, [datetime]$ReferenceTimeUtc)
    $invalid = [System.Collections.Generic.List[string]]::new()
    $records = [System.Collections.Generic.List[object]]::new()
    if ([string]::IsNullOrWhiteSpace($Directory) -or -not (Test-Path -LiteralPath $Directory -PathType Container)) {
        return [pscustomobject]@{ records = @(); invalid = @('snapshot-directory-missing'); max_gap_seconds = $null; coverage_seconds = 0; latest_age_seconds = $null }
    }
    $files = @(Get-ChildItem -LiteralPath $Directory -Filter 'desktop-health-*.json' -File | Sort-Object Name)
    foreach ($file in $files) {
        try { $value = Get-Content -Raw -Encoding UTF8 -LiteralPath $file.FullName | ConvertFrom-Json }
        catch { [void]$invalid.Add(('invalid-json:' + $file.Name)); continue }
        $observedAt = ConvertTo-UtcOrNull $value.observed_at
        if ($value.schema_version -ne 'desktop-health-snapshot-v1' -or $value.adapter_id -ne 'adapter.desktop' -or $null -eq $observedAt) {
            [void]$invalid.Add(('invalid-schema-or-time:' + $file.Name)); continue
        }
        [void]$records.Add([pscustomobject]@{ file = $file.FullName; observed_at = $observedAt; snapshot_id = [string]$value.snapshot_id; health_state = [string]$value.health_state; read_only = [bool]$value.read_only })
    }
    $sorted = @($records | Sort-Object observed_at)
    $maxGap = 0.0
    for ($i = 1; $i -lt $sorted.Count; $i++) {
        $gap = ($sorted[$i].observed_at - $sorted[$i - 1].observed_at).TotalSeconds
        if ($gap -gt $maxGap) { $maxGap = $gap }
    }
    $coverage = if ($sorted.Count -ge 2) { ($sorted[-1].observed_at - $sorted[0].observed_at).TotalSeconds } else { 0 }
    $latestAge = if ($sorted.Count -gt 0) { ($ReferenceTimeUtc - $sorted[-1].observed_at).TotalSeconds } else { $null }
    return [pscustomobject]@{ records = $sorted; invalid = @($invalid); max_gap_seconds = $maxGap; coverage_seconds = $coverage; latest_age_seconds = $latestAge }
}

function New-SoakResult {
    param([object]$Assessment, [datetime]$ReferenceTimeUtc)
    $reasons = [System.Collections.Generic.List[string]]::new()
    $records = @($Assessment.records)
    $requiredCoverageSeconds = $RequiredCoverageHours * 3600
    $maxGapSeconds = $MaxGapMinutes * 60
    $latestAgeSeconds = $LatestSnapshotMaxAgeMinutes * 60
    if (@($Assessment.invalid).Count -gt 0) { [void]$reasons.Add('invalid-snapshot-receipts-present') }
    if ($records.Count -lt 2) { [void]$reasons.Add('insufficient-snapshot-count') }
    if ($Assessment.coverage_seconds -lt $requiredCoverageSeconds) { [void]$reasons.Add('coverage-window-insufficient') }
    if ($records.Count -ge 2 -and $Assessment.max_gap_seconds -gt $maxGapSeconds) { [void]$reasons.Add('snapshot-gap-exceeds-threshold') }
    if ($null -eq $Assessment.latest_age_seconds -or $Assessment.latest_age_seconds -gt $latestAgeSeconds) { [void]$reasons.Add('latest-snapshot-stale') }
    if (@($records | Where-Object { $_.health_state -ne 'healthy' -or -not $_.read_only }).Count -gt 0) { [void]$reasons.Add('unhealthy-or-nonreadonly-snapshot-present') }
    $status = if ($reasons.Count -eq 0) { 'passed' } elseif ($records.Count -lt 2 -or $Assessment.coverage_seconds -lt $requiredCoverageSeconds) { 'insufficient' } else { 'failed' }
    return [ordered]@{
        schema_version = 'desktop-health-soak-assessment-v1'
        assessor = 'adapter.desktop'
        assessment_id = 'desktop-soak:' + [guid]::NewGuid().ToString('n')
        evaluated_at = $ReferenceTimeUtc.ToUniversalTime().ToString('o')
        read_only = $true
        snapshot_count = $records.Count
        first_observed_at = if ($records.Count) { $records[0].observed_at.ToString('o') } else { $null }
        latest_observed_at = if ($records.Count) { $records[-1].observed_at.ToString('o') } else { $null }
        coverage_seconds = [math]::Round($Assessment.coverage_seconds, 3)
        required_coverage_seconds = $requiredCoverageSeconds
        max_gap_seconds = [math]::Round($Assessment.max_gap_seconds, 3)
        allowed_gap_seconds = $maxGapSeconds
        latest_age_seconds = if ($null -eq $Assessment.latest_age_seconds) { $null } else { [math]::Round($Assessment.latest_age_seconds, 3) }
        allowed_latest_age_seconds = $latestAgeSeconds
        invalid_receipts = @($Assessment.invalid)
        status = $status
        blockers = @($reasons | Select-Object -Unique)
        guardian_allow_eligible = $false
        guardian_block_reason = 'guardian-runtime-canary-remains-deny-only even when a Desktop soak passes'
        auto_repair_performed = $false
    }
}

if ($Mode -eq 'SelfTest') {
    $root = Join-Path ([System.IO.Path]::GetTempPath()) ('desktop-soak-' + [guid]::NewGuid().ToString('n'))
    try {
        [void](New-Item -ItemType Directory -Path $root -Force)
        $end = [datetime]::Parse('2026-07-25T00:00:00Z').ToUniversalTime()
        foreach ($offset in @(-24, -12, 0)) {
            $stamp = $end.AddHours($offset)
            [ordered]@{ schema_version = 'desktop-health-snapshot-v1'; adapter_id = 'adapter.desktop'; snapshot_id = ('fixture-' + $offset); observed_at = $stamp.ToString('o'); health_state = 'healthy'; read_only = $true } | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $root ('desktop-health-' + ($offset + 24) + '.json'))
        }
        $Assessment = Get-Assessment -Directory $root -ReferenceTimeUtc $end
        $oldRequired = $RequiredCoverageHours; $oldGap = $MaxGapMinutes; $oldLatest = $LatestSnapshotMaxAgeMinutes
        $RequiredCoverageHours = 24; $MaxGapMinutes = 720; $LatestSnapshotMaxAgeMinutes = 1
        $pass = New-SoakResult -Assessment $Assessment -ReferenceTimeUtc $end
        $RequiredCoverageHours = $oldRequired; $MaxGapMinutes = $oldGap; $LatestSnapshotMaxAgeMinutes = $oldLatest
        [pscustomobject]@{ verifier = 'Test-DesktopHealthSoakEvidence.ps1'; passed = ($pass.status -eq 'passed' -and -not $pass.guardian_allow_eligible -and -not $pass.auto_repair_performed); checks = @([pscustomobject]@{ name = '24h-fixture-can-pass-within-allowed-gap'; passed = ($pass.status -eq 'passed') }, [pscustomobject]@{ name = 'soak-never-turns-guardian-into-allow-authority'; passed = (-not $pass.guardian_allow_eligible -and -not $pass.auto_repair_performed) }) } | ConvertTo-Json -Depth 8
    } finally { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue }
    exit 0
}

$referenceTime = (Get-Date).ToUniversalTime()
$assessment = Get-Assessment -Directory $SnapshotDirectory -ReferenceTimeUtc $referenceTime
$result = New-SoakResult -Assessment $assessment -ReferenceTimeUtc $referenceTime
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) { Write-ImmutableJson -Value $result -Path $OutputPath }
$result | ConvertTo-Json -Depth 16
