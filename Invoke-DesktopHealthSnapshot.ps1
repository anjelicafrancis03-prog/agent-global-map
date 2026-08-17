[CmdletBinding()]
param(
    [ValidateSet('Snapshot', 'SelfTest')]
    [string]$Mode = 'Snapshot',
    [string]$ConsoleBaseUrl = 'http://127.0.0.1:18765',
    [string]$PhoneWatcherPath,
    [string]$OutputPath,
    [ValidateRange(1, 60)]
    [int]$RequestTimeoutSeconds = 5,
    [ValidateRange(1, 3600)]
    [int]$PhoneWatcherMaxAgeSeconds = 15,
    [ValidateRange(30, 3600)]
    [int]$FreshnessSeconds = 300
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if ([string]::IsNullOrWhiteSpace($PhoneWatcherPath)) {
    $PhoneWatcherPath = Join-Path $workspaceRoot 'reports\desktop-agent-console\phone-mirror\watcher-health.json'
}

function Get-OptionalProperty {
    param([object]$Object, [string]$Name, [object]$Default = $null)
    if ($null -ne $Object -and $Object.PSObject.Properties.Name -contains $Name) { return $Object.$Name }
    return $Default
}

function ConvertTo-UtcOrNull {
    param([object]$Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) { return $null }
    # 2026-08-16 普通15 R4 fix: [datetime]::Parse 用当前 culture 解析——zh-CN 下 'Z' 后缀经
    # culture stringify 丢失 → ToUniversalTime() 再减 8h → age 虚增 28800s（phone-watcher-stale 误报）。
    # 改用 [datetimeoffset]::Parse（InvariantSet 语义，保留 UTC 偏移）→ .UtcDateTime。
    try { return [datetimeoffset]::Parse([string]$Value, [System.Globalization.CultureInfo]::InvariantCulture).UtcDateTime } catch { return $null }
}

function Get-WorkspaceEvidenceRef {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $Path.Replace('\', '/') }
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if ($resolved.StartsWith($workspaceRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $resolved.Substring($workspaceRoot.Length).TrimStart('\', '/').Replace('\', '/')
    }
    return $resolved
}

function Get-Sha256 {
    param([string]$Value)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-ConsoleObservation {
    param([datetime]$ObservedAt)
    try {
        $state = Invoke-RestMethod -Uri (($ConsoleBaseUrl.TrimEnd('/')) + '/api/state') -TimeoutSec $RequestTimeoutSeconds -ErrorAction Stop
        return [pscustomobject]@{ reachable = $true; state = $state; error_category = $null; observed_at = $ObservedAt }
    }
    catch {
        return [pscustomobject]@{ reachable = $false; state = $null; error_category = 'console_api_unreachable'; observed_at = $ObservedAt }
    }
}

function Get-PhoneWatcherObservation {
    param([datetime]$ObservedAt)
    $base = [ordered]@{
        path_ref = Get-WorkspaceEvidenceRef $PhoneWatcherPath
        exists = $false
        parse_state = 'missing'
        status = $null
        updated_at = $null
        age_seconds = $null
        desired_count = $null
        actual_count = $null
        error_category = $null
        healthy = $false
        reasons = @()
    }
    if (-not (Test-Path -LiteralPath $PhoneWatcherPath -PathType Leaf)) {
        $base.reasons = @('phone-watcher-missing')
        return [pscustomobject]$base
    }
    $base.exists = $true
    try { $watcher = Get-Content -Raw -Encoding UTF8 -LiteralPath $PhoneWatcherPath | ConvertFrom-Json }
    catch {
        $base.parse_state = 'invalid-json'
        $base.reasons = @('phone-watcher-invalid-json')
        return [pscustomobject]$base
    }
    $base.parse_state = 'valid-json'
    $base.status = [string](Get-OptionalProperty $watcher 'status')
    $base.updated_at = Get-OptionalProperty $watcher 'updatedAtUtc'
    $base.desired_count = Get-OptionalProperty $watcher 'desiredCount'
    $base.actual_count = Get-OptionalProperty $watcher 'actualCount'
    $base.error_category = Get-OptionalProperty $watcher 'errorCategory'
    $updatedAt = ConvertTo-UtcOrNull $base.updated_at
    $reasons = [System.Collections.Generic.List[string]]::new()
    if ($null -eq $updatedAt) { [void]$reasons.Add('phone-watcher-timestamp-invalid') }
    else {
        $base.age_seconds = [math]::Round(($ObservedAt - $updatedAt).TotalSeconds, 3)
        if ($base.age_seconds -lt -5) { [void]$reasons.Add('phone-watcher-clock-ahead') }
        elseif ($base.age_seconds -gt $PhoneWatcherMaxAgeSeconds) { [void]$reasons.Add('phone-watcher-stale') }
    }
    if ($base.status -ne 'healthy') { [void]$reasons.Add('phone-watcher-not-healthy') }
    if ($null -eq $base.desired_count -or $null -eq $base.actual_count -or [int]$base.desired_count -ne [int]$base.actual_count) {
        [void]$reasons.Add('phone-watcher-count-mismatch')
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$base.error_category) -and $base.error_category -ne 'none') {
        [void]$reasons.Add('phone-watcher-error-category')
    }
    $base.reasons = @($reasons)
    $base.healthy = ($base.reasons.Count -eq 0)
    return [pscustomobject]$base
}

function New-DesktopHealthSnapshot {
    param(
        [object]$ConsoleObservation,
        [object]$PhoneObservation,
        [datetime]$ObservedAt
    )
    $reasons = [System.Collections.Generic.List[string]]::new()
    $slotEntries = @()
    $console = [ordered]@{
        reachable = [bool]$ConsoleObservation.reachable
        base_url = $ConsoleBaseUrl
        backend = $null
        preview_read_only = $null
        no_default_start = $null
        run_id_sha256 = $null
        slot_count = 0
        status_counts = [ordered]@{}
        active_slots = @()
        failed_slots = @()
        stopped_slots = @()
        error_category = $ConsoleObservation.error_category
    }
    if (-not $console.reachable) {
        [void]$reasons.Add('console-api-unreachable')
    }
    else {
        $state = $ConsoleObservation.state
        $console.backend = Get-OptionalProperty $state 'backend'
        $console.preview_read_only = Get-OptionalProperty $state 'previewReadOnly'
        $console.no_default_start = Get-OptionalProperty $state 'noDefaultStart'
        $runId = Get-OptionalProperty $state 'runId'
        if (-not [string]::IsNullOrWhiteSpace([string]$runId)) { $console.run_id_sha256 = Get-Sha256 ([string]$runId) }
        $slotMap = Get-OptionalProperty $state 'slots'
        if ($null -eq $slotMap) {
            [void]$reasons.Add('console-slot-map-missing')
        }
        else {
            $slotEntries = @($slotMap.PSObject.Properties | ForEach-Object { $_.Value })
            $console.slot_count = $slotEntries.Count
            if ($slotEntries.Count -eq 0) { [void]$reasons.Add('console-slot-map-empty') }
            foreach ($entry in $slotEntries) {
                $status = [string](Get-OptionalProperty $entry 'status' 'unknown')
                if (-not $console.status_counts.Contains($status)) { $console.status_counts[$status] = 0 }
                $console.status_counts[$status] = [int]$console.status_counts[$status] + 1
            }
            $console.active_slots = @($slotEntries | Where-Object { (Get-OptionalProperty $_ 'status') -in @('starting', 'running') } | ForEach-Object { [string](Get-OptionalProperty $_ 'name') } | Sort-Object)
            $console.failed_slots = @($slotEntries | Where-Object { (Get-OptionalProperty $_ 'status') -eq 'failed' } | ForEach-Object { [string](Get-OptionalProperty $_ 'name') } | Sort-Object)
            $console.stopped_slots = @($slotEntries | Where-Object { (Get-OptionalProperty $_ 'status') -in @('stopped', 'exited') } | ForEach-Object { [string](Get-OptionalProperty $_ 'name') } | Sort-Object)
            if ($console.failed_slots.Count -gt 0) { [void]$reasons.Add('console-failed-slots-present') }
        }
    }
    foreach ($reason in @($PhoneObservation.reasons)) { [void]$reasons.Add([string]$reason) }
    $healthState = if (-not $console.reachable) { 'unavailable' } elseif ($reasons.Count -gt 0) { 'degraded' } else { 'healthy' }
    $posture = if ($console.active_slots.Count -gt 0) { 'active' } else { 'idle' }
    $snapshot = [ordered]@{
        schema_version = 'desktop-health-snapshot-v1'
        adapter_id = 'adapter.desktop'
        snapshot_id = 'desktop-health:' + [guid]::NewGuid().ToString('n')
        read_only = $true
        observed_at = $ObservedAt.ToUniversalTime().ToString('o')
        fresh_until = $ObservedAt.ToUniversalTime().AddSeconds($FreshnessSeconds).ToString('o')
        health_state = $healthState
        runtime_posture = $posture
        blockers = @($reasons | Select-Object -Unique)
        console = [pscustomobject]$console
        phone_mirror = $PhoneObservation
        evidence_refs = @(
            'tools/desktop-agent-console/server.js',
            (Get-WorkspaceEvidenceRef $PhoneWatcherPath)
        )
        scope = 'desktop-console-and-phone-mirror-observation-only'
        does_not_prove = @(
            'provider-inference-success',
            'per-slot-tool-call-success',
            'session-content-integrity',
            'authorization-to-start-stop-restart-or-reconfigure'
        )
        guardian_allow_eligible = $false
        guardian_block_reason = 'guardian-runtime-canary-remains-deny-only; 24h soak evidence is evaluated separately'
        auto_repair_performed = $false
    }
    $canonical = $snapshot | ConvertTo-Json -Depth 16 -Compress
    $snapshot.snapshot_sha256 = Get-Sha256 $canonical
    return [pscustomobject]$snapshot
}

function Write-ImmutableJson {
    param([object]$Value, [string]$Path)
    if (Test-Path -LiteralPath $Path) { throw "Refusing to overwrite existing receipt: $Path" }
    $directory = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($directory)) { [void](New-Item -ItemType Directory -Path $directory -Force) }
    $json = $Value | ConvertTo-Json -Depth 16
    [System.IO.File]::WriteAllText($Path, $json, [System.Text.UTF8Encoding]::new($false))
}

if ($Mode -eq 'SelfTest') {
    $now = (Get-Date).ToUniversalTime()
    $slots = [ordered]@{
        R1 = [pscustomobject]@{ name = 'R1'; status = 'running' }
        CC1 = [pscustomobject]@{ name = 'CC1'; status = 'stopped' }
    }
    $state = [pscustomobject]@{ runId = 'fixture-run'; backend = 'node-pty'; previewReadOnly = $false; noDefaultStart = $false; slots = [pscustomobject]$slots }
    $consoleGood = [pscustomobject]@{ reachable = $true; state = $state; error_category = $null; observed_at = $now }
    $phoneGood = [pscustomobject]@{ path_ref = 'fixture/watcher-health.json'; exists = $true; parse_state = 'valid-json'; status = 'healthy'; updated_at = $now.ToString('o'); age_seconds = 0; desired_count = 1; actual_count = 1; error_category = 'none'; healthy = $true; reasons = @() }
    $healthy = New-DesktopHealthSnapshot -ConsoleObservation $consoleGood -PhoneObservation $phoneGood -ObservedAt $now
    $phoneBad = [pscustomobject]@{ path_ref = 'fixture/watcher-health.json'; exists = $true; parse_state = 'valid-json'; status = 'healthy'; updated_at = $now.AddMinutes(-1).ToString('o'); age_seconds = 60; desired_count = 1; actual_count = 0; error_category = 'none'; healthy = $false; reasons = @('phone-watcher-stale', 'phone-watcher-count-mismatch') }
    $degraded = New-DesktopHealthSnapshot -ConsoleObservation $consoleGood -PhoneObservation $phoneBad -ObservedAt $now
    $offline = New-DesktopHealthSnapshot -ConsoleObservation ([pscustomobject]@{ reachable = $false; state = $null; error_category = 'console_api_unreachable'; observed_at = $now }) -PhoneObservation $phoneGood -ObservedAt $now
    [pscustomobject]@{
        verifier = 'Invoke-DesktopHealthSnapshot.ps1'
        passed = ($healthy.health_state -eq 'healthy' -and $healthy.guardian_allow_eligible -eq $false -and $degraded.health_state -eq 'degraded' -and $offline.health_state -eq 'unavailable' -and $healthy.auto_repair_performed -eq $false)
        checks = @(
            [pscustomobject]@{ name = 'healthy-observation-is-read-only-and-not-guardian-allow'; passed = ($healthy.read_only -and -not $healthy.guardian_allow_eligible -and -not $healthy.auto_repair_performed) },
            [pscustomobject]@{ name = 'stale-or-mismatched-phone-watcher-degrades'; passed = ($degraded.health_state -eq 'degraded' -and $degraded.blockers -contains 'phone-watcher-stale') },
            [pscustomobject]@{ name = 'unreachable-console-is-unavailable'; passed = ($offline.health_state -eq 'unavailable' -and $offline.blockers -contains 'console-api-unreachable') }
        )
    } | ConvertTo-Json -Depth 8
    exit 0
}

$observedAt = (Get-Date).ToUniversalTime()
$consoleObservation = Get-ConsoleObservation -ObservedAt $observedAt
$phoneObservation = Get-PhoneWatcherObservation -ObservedAt $observedAt
$snapshot = New-DesktopHealthSnapshot -ConsoleObservation $consoleObservation -PhoneObservation $phoneObservation -ObservedAt $observedAt
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) { Write-ImmutableJson -Value $snapshot -Path $OutputPath }
$snapshot | ConvertTo-Json -Depth 16
