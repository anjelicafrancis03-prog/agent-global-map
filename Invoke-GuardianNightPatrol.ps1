# Invoke-GuardianNightPatrol.ps1
# On-demand Guardian night-patrol classifier for codex-temr.9.
# Classifies sources into: unreachable | abnormal | stale | unknown | healthy-declared.
# DENY-ONLY / report-only. Does NOT:
#   - register scheduled tasks
#   - auto-repair
#   - grant allow
#   - clear blocks
#   - mutate Desktop/MCP/Skill/credentials

[CmdletBinding()]
param(
    [ValidateSet('Run', 'SelfTest')]
    [string]$Mode = 'Run',
    [string]$RegistryPath = 'F:\codex\tools\agent-system-map\catalog\source-registry.json',
    [string]$GuardianDecisionScript = 'F:\codex\tools\agent-system-map\Invoke-GuardianDecision.ps1',
    [string]$LegacyStatusPath = 'F:\codex\logs\local-core-guardian\status.json',
    [string]$DesktopHealthScript = 'F:\codex\tools\agent-system-map\Invoke-DesktopHealthSnapshot.ps1',
    [string]$ConsoleBaseUrl = 'http://127.0.0.1:18765',
    [switch]$SkipDesktopHealth,
    [string]$OutputPath,
    [int]$StaleAfterSeconds = 900,
    [string]$CredentialScanRoot = 'F:\codex\tools'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-SourceClass {
    param(
        [string]$SourceId,
        [string]$Status,
        [string]$PathHint,
        [datetime]$NowUtc
    )

    # Non-active registry statuses are explicit classes
    switch ($Status) {
        'deferred' { return [pscustomobject]@{ class = 'unknown'; reason = 'registry-deferred'; path = $PathHint } }
        'pending' { return [pscustomobject]@{ class = 'unknown'; reason = 'registry-pending'; path = $PathHint } }
        'excluded_until_repair' { return [pscustomobject]@{ class = 'abnormal'; reason = 'excluded-until-repair'; path = $PathHint } }
        'declared-only' { return [pscustomobject]@{ class = 'unknown'; reason = 'declared-only-no-probe'; path = $PathHint } }
    }

    if ([string]::IsNullOrWhiteSpace($PathHint)) {
        return [pscustomobject]@{ class = 'unknown'; reason = 'no-path-hint'; path = $null }
    }

    # Expand simple globs for existence check (first match)
    $checkPath = $PathHint
    if ($PathHint -match '[\*\?]') {
        # e.g. F:\codex\okf-bundles\*\index.md -> search under F:\codex\okf-bundles
        $starIdx = $PathHint.IndexOf('*')
        $root = $PathHint.Substring(0, $starIdx).TrimEnd('\', '/')
        if (-not (Test-Path -LiteralPath $root)) {
            return [pscustomobject]@{ class = 'unreachable'; reason = 'glob-root-missing'; path = $PathHint }
        }
        $hit = Get-ChildItem -Path $root -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -like $PathHint } |
            Select-Object -First 1
        if (-not $hit) {
            # fallback: any index.md / module-card.md under root
            $leaf = Split-Path -Leaf ($PathHint -replace '\*', 'x')
            $hit = Get-ChildItem -Path $root -Recurse -Filter $leaf -File -ErrorAction SilentlyContinue | Select-Object -First 1
        }
        if ($hit) { $checkPath = $hit.FullName } else {
            return [pscustomobject]@{ class = 'unreachable'; reason = 'glob-no-match'; path = $PathHint }
        }
    }

    if ($checkPath -like 'http*' -or $checkPath -like '*127.0.0.1*') {
        return [pscustomobject]@{ class = 'unknown'; reason = 'live-http-not-probed-in-report-mode'; path = $checkPath }
    }

    if (-not (Test-Path -LiteralPath $checkPath)) {
        return [pscustomobject]@{ class = 'unreachable'; reason = 'path-missing'; path = $checkPath }
    }

    try {
        $item = Get-Item -LiteralPath $checkPath
        $age = ($NowUtc - $item.LastWriteTimeUtc).TotalSeconds
        if ($age -gt $StaleAfterSeconds -and $Status -eq 'active') {
            # declared config sources use long freshness (7d); only flag extreme silence if mtime absurdly old for live-like paths
            if ($age -gt (7 * 24 * 3600)) {
                return [pscustomobject]@{ class = 'stale'; reason = "mtime-age-seconds=$([int]$age)"; path = $checkPath }
            }
        }
        return [pscustomobject]@{ class = 'healthy-declared'; reason = 'path-present'; path = $checkPath }
    } catch {
        return [pscustomobject]@{ class = 'abnormal'; reason = 'stat-failed'; path = $checkPath }
    }
}

function Resolve-WorkspacePath {
    param([string]$Hint)
    if ([string]::IsNullOrWhiteSpace($Hint)) { return $null }
    if ($Hint -match '^[A-Za-z]:\\' -or $Hint -match '^\\\\' -or $Hint.StartsWith('/')) { return $Hint }
    if ($Hint.StartsWith('~')) {
        return ($Hint -replace '^~', $env:USERPROFILE)
    }
    return (Join-Path 'F:\codex' ($Hint -replace '/', '\'))
}

function Get-PathHintFromSource {
    param([object]$Source)
    $iface = [string](Get-Optional $Source 'interface')
    $hint = $null
    if ($iface -match 'file:\s*(.+)$') { $hint = $Matches[1].Trim() }
    elseif ($iface -match 'dir:\s*(.+)$') { $hint = $Matches[1].Trim() }
    elseif ($iface -match 'bd export') { $hint = 'F:\codex\.beads' }
    else {
        switch ([string]$Source.sourceId) {
            'agent-hub' { $hint = 'F:\codex\tools\agent-hub\agent-families.json' }
            'okf' { $hint = 'F:\codex\okf-bundles' }
            'harness' { $hint = 'F:\codex\docs\harness-modules' }
            'mcp-manager' { $hint = 'F:\codex\tools\mcp_manager\registry.json' }
            'skill-manager' { $hint = (Join-Path $env:USERPROFILE '.agents\skills') }
            'apikey-provider-manager' { $hint = 'F:\codex\tools\agent-hub\provider-routes.json' }
            'beads' { $hint = 'F:\codex\.beads' }
            default { $hint = $null }
        }
    }
    return (Resolve-WorkspacePath $hint)
}

function Get-Optional {
    param($Object, [string]$Name)
    if ($null -ne $Object -and $Object.PSObject.Properties.Name -contains $Name) { return $Object.$Name }
    return $null
}

# E-03: plaintext credential scan (report-only). Scans tools scripts only
# (*.js/*.cjs/*.ps1/*.py) for credential-shaped patterns. NEVER echoes the matched
# value (would leak the very credential). Report file+line+pattern-class only.
function Invoke-CredentialScan {
    param([string]$Root = 'F:\codex\tools')
    $patterns = @(
        '(?<![A-Za-z0-9-])sk-[A-Za-z0-9]{8}',
        'password\s*=\s*[''"]',
        'api[_-]?key\s*=\s*[''"]'
    )
    if (-not (Test-Path -LiteralPath $Root)) {
        return [pscustomobject]@{ scanned_files = 0; hit_count = 0; clean = $true; hits = @(); error = 'root-missing' }
    }
    $files = Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Extension -in '.js', '.cjs', '.ps1', '.py' -and
            $_.FullName -notmatch '\\node_modules\\' -and
            $_.FullName -notmatch '\\__pycache__\\' -and
            $_.FullName -notmatch '\\.git\\' -and
            $_.FullName -notmatch '\\Lib\\site-packages\\' -and
            $_.FullName -notmatch '\\_backups\\' -and
            $_.FullName -notmatch '\\dist\\'
        }
    $hits = @()
    foreach ($f in @($files)) {
        try {
            $matches = Select-String -LiteralPath $f.FullName -Pattern $patterns -ErrorAction Stop
        } catch {
            continue  # unreadable/binary-sniffed file: skip (never fail the patrol)
        }
        foreach ($x in @($matches)) {
            $cls = 'unknown-pattern'
            if ($x.Pattern -eq $patterns[0]) { $cls = 'sk-key' }
            elseif ($x.Pattern -eq $patterns[1]) { $cls = 'password-assign' }
            elseif ($x.Pattern -eq $patterns[2]) { $cls = 'api-key-assign' }
            $hits += [pscustomobject]@{ file = $f.FullName; line = $x.LineNumber; pattern = $cls }
        }
    }
    return [pscustomobject]@{
        scanned_files = @($files).Count
        hit_count = @($hits).Count
        clean = (@($hits).Count -eq 0)
        hits = @($hits)
        error = $null
    }
}

function Invoke-Patrol {
    $now = (Get-Date).ToUniversalTime()
    if (-not (Test-Path -LiteralPath $RegistryPath)) {
        throw "Registry missing: $RegistryPath"
    }
    $registry = Get-Content -Raw -Encoding UTF8 -LiteralPath $RegistryPath | ConvertFrom-Json
    $sourceRows = @()
    foreach ($src in @($registry.sources)) {
        $hint = Get-PathHintFromSource -Source $src
        $cls = Get-SourceClass -SourceId $src.sourceId -Status $src.status -PathHint $hint -NowUtc $now
        $sourceRows += [pscustomobject]@{
            source_id = $src.sourceId
            registry_status = $src.status
            class = $cls.class
            reason = $cls.reason
            path = $cls.path
            authority = $src.authority
        }
    }

    # Guardian decision canary (deny-only)
    $decisionPath = Join-Path ([System.IO.Path]::GetTempPath()) ("guardian-patrol-decision-" + [guid]::NewGuid().ToString('n') + ".json")
    $decision = $null
    $decisionError = $null
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $GuardianDecisionScript -Mode Evaluate -OutputPath $decisionPath | Out-Null
        if (Test-Path -LiteralPath $decisionPath) {
            $decision = Get-Content -Raw -Encoding UTF8 -LiteralPath $decisionPath | ConvertFrom-Json
        }
    } catch {
        $decisionError = "$_"
    }

    $legacyClass = 'unknown'
    $legacyReason = 'not-checked'
    if (Test-Path -LiteralPath $LegacyStatusPath) {
        try {
            $st = Get-Content -Raw -Encoding UTF8 -LiteralPath $LegacyStatusPath | ConvertFrom-Json
            if ($st.disabled -eq $true) { $legacyClass = 'abnormal'; $legacyReason = 'legacy-disabled' }
            else { $legacyClass = 'abnormal'; $legacyReason = 'legacy-present-but-untrusted' }
        } catch {
            $legacyClass = 'abnormal'; $legacyReason = 'legacy-invalid-json'
        }
    } else {
        $legacyClass = 'unreachable'; $legacyReason = 'legacy-status-missing'
    }

    # Desktop health link (read-only observation; never grants allow)
    $desktopHealth = $null
    $desktopHealthError = $null
    $desktopHealthPath = $null
    if (-not $SkipDesktopHealth -and (Test-Path -LiteralPath $DesktopHealthScript)) {
        try {
            $desktopHealthPath = Join-Path ([System.IO.Path]::GetTempPath()) ("desktop-health-patrol-" + [guid]::NewGuid().ToString('n') + ".json")
            & powershell -NoProfile -ExecutionPolicy Bypass -File $DesktopHealthScript -Mode Snapshot -ConsoleBaseUrl $ConsoleBaseUrl -OutputPath $desktopHealthPath | Out-Null
            if (Test-Path -LiteralPath $desktopHealthPath) {
                $desktopHealth = Get-Content -Raw -Encoding UTF8 -LiteralPath $desktopHealthPath | ConvertFrom-Json
            }
        } catch {
            $desktopHealthError = "$_"
        }
    } elseif ($SkipDesktopHealth) {
        $desktopHealthError = 'skipped-by-flag'
    } else {
        $desktopHealthError = 'desktop-health-script-missing'
    }

    # Reclassify desktop-live from on-demand health when available (registry may still say excluded)
    if ($null -ne $desktopHealth) {
        $dhClass = 'unknown'
        $dhReason = 'desktop-health-observed'
        switch ([string]$desktopHealth.health_state) {
            'healthy' { $dhClass = 'healthy-declared'; $dhReason = 'desktop-health-snapshot-healthy' }
            'degraded' { $dhClass = 'abnormal'; $dhReason = 'desktop-health-snapshot-degraded' }
            'unavailable' { $dhClass = 'unreachable'; $dhReason = 'desktop-health-snapshot-unavailable' }
            default { $dhClass = 'unknown'; $dhReason = "desktop-health-state=$($desktopHealth.health_state)" }
        }
        for ($i = 0; $i -lt $sourceRows.Count; $i++) {
            if ($sourceRows[$i].source_id -eq 'desktop-live') {
                $sourceRows[$i] = [pscustomobject]@{
                    source_id = 'desktop-live'
                    registry_status = $sourceRows[$i].registry_status
                    class = $dhClass
                    reason = $(if ($sourceRows[$i].registry_status -eq 'excluded_until_repair' -and $dhClass -eq 'healthy-declared') { 'excluded-registry-but-ondemand-health-ok' } else { $dhReason })
                    path = $ConsoleBaseUrl
                    authority = $sourceRows[$i].authority
                }
            }
        }
    }

    $classCounts = @{}
    foreach ($row in $sourceRows) {
        if (-not $classCounts.ContainsKey($row.class)) { $classCounts[$row.class] = 0 }
        $classCounts[$row.class]++
    }

    # E-03: plaintext credential scan (report-only; never repairs, never echoes values)
    $credScan = Invoke-CredentialScan -Root $CredentialScanRoot

    $report = [ordered]@{
        report_id = 'guardian-night-patrol:' + [guid]::NewGuid().ToString('n')
        beads_task = 'codex-temr.9'
        mode = 'on-demand-report-only'
        observed_at = $now.ToString('o')
        auto_repair_performed = $false
        allow_granted = $false
        scheduler_registered = $false
        classes = [ordered]@{
            unreachable = @($sourceRows | Where-Object { $_.class -eq 'unreachable' })
            abnormal = @($sourceRows | Where-Object { $_.class -eq 'abnormal' })
            stale = @($sourceRows | Where-Object { $_.class -eq 'stale' })
            unknown = @($sourceRows | Where-Object { $_.class -eq 'unknown' })
            healthy_declared = @($sourceRows | Where-Object { $_.class -eq 'healthy-declared' })
        }
        class_counts = $classCounts
        sources = $sourceRows
        legacy_local_core_guardian = [ordered]@{
            path = $LegacyStatusPath
            class = $legacyClass
            reason = $legacyReason
        }
        guardian_decision_canary = [ordered]@{
            decision = $(if ($decision) { $decision.decision } else { 'unavailable' })
            state = $(if ($decision) { $decision.state } else { 'unknown' })
            block_reason = $(if ($decision) { $decision.block_reason } else { $decisionError })
            enforcement = $(if ($decision) { $decision.enforcement } else { 'deny' })
        }
        desktop_health = [ordered]@{
            linked = ($null -ne $desktopHealth)
            error = $desktopHealthError
            health_state = $(if ($desktopHealth) { $desktopHealth.health_state } else { $null })
            runtime_posture = $(if ($desktopHealth) { $desktopHealth.runtime_posture } else { $null })
            guardian_allow_eligible = $(if ($desktopHealth) { [bool]$desktopHealth.guardian_allow_eligible } else { $false })
            guardian_block_reason = $(if ($desktopHealth) { $desktopHealth.guardian_block_reason } else { $null })
            console = $(if ($desktopHealth) { $desktopHealth.console } else { $null })
            phone_mirror_healthy = $(if ($desktopHealth -and $desktopHealth.phone_mirror) { [bool]$desktopHealth.phone_mirror.healthy } else { $null })
            snapshot_id = $(if ($desktopHealth) { $desktopHealth.snapshot_id } else { $null })
            snapshot_sha256 = $(if ($desktopHealth) { $desktopHealth.snapshot_sha256 } else { $null })
            temp_path = $desktopHealthPath
            note = 'Observation only; healthy desktop does not grant Guardian allow'
        }
        credential_scan = [ordered]@{
            scanned_root = $CredentialScanRoot
            scanned_files = $credScan.scanned_files
            hit_count = $credScan.hit_count
            clean = $credScan.clean
            hits = @($credScan.hits)
            note = 'E-03 report-only plaintext credential scan (sk-key/password-assign/api-key-assign in tools scripts); matched values never echoed; no auto-repair'
        }
        next_actions = @(
            'Do not enable allow from this report',
            'Do not register Night Watch scheduler without separate root authorization',
            'Investigate unreachable/abnormal sources manually',
            'codex-g76q remains the daily maintenance epic for production patrol design',
            'Desktop health is linked read-only; design allow criteria separately (not enabled)'
        )
    }
    return [pscustomobject]$report
}

if ($Mode -eq 'SelfTest') {
    $report = Invoke-Patrol
    $checks = @(
        [pscustomobject]@{ name = 'has-sources'; passed = (@($report.sources).Count -ge 1) },
        [pscustomobject]@{ name = 'no-allow'; passed = ($report.allow_granted -eq $false) },
        [pscustomobject]@{ name = 'no-auto-repair'; passed = ($report.auto_repair_performed -eq $false) },
        [pscustomobject]@{ name = 'no-scheduler'; passed = ($report.scheduler_registered -eq $false) },
        [pscustomobject]@{ name = 'classes-present'; passed = ($null -ne $report.classes) },
        [pscustomobject]@{ name = 'decision-not-allow'; passed = ($report.guardian_decision_canary.decision -ne 'allow') },
        [pscustomobject]@{ name = 'desktop-health-section-present'; passed = ($null -ne $report.desktop_health) },
        [pscustomobject]@{ name = 'desktop-health-not-allow-eligible'; passed = ($report.desktop_health.guardian_allow_eligible -eq $false) },
        [pscustomobject]@{ name = 'credential-scan-section-present'; passed = ($null -ne $report.credential_scan) },
        [pscustomobject]@{ name = 'credential-scan-no-value-echo'; passed = (@($report.credential_scan.hits | Where-Object { $_.PSObject.Properties.Name -contains 'value' }).Count -eq 0) }
    )
    [pscustomobject]@{
        verifier = 'Invoke-GuardianNightPatrol.ps1'
        read_only = $true
        passed = ((@($checks | Where-Object { -not $_.passed }).Count) -eq 0)
        checks = $checks
        sample_class_counts = $report.class_counts
    } | ConvertTo-Json -Depth 8
    exit 0
}

$report = Invoke-Patrol
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $dir = Split-Path -Parent $OutputPath
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
}
$report | ConvertTo-Json -Depth 10
