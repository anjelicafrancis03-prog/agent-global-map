[CmdletBinding()]
param(
    [ValidateSet('Evaluate', 'Status', 'SelfTest')]
    [string]$Mode = 'Evaluate',
    [string]$TargetId = 'projection.beads-task-tree',
    [string]$Action = 'beads-projection-metadata-write',
    [ValidateSet('low', 'medium', 'high', 'critical')]
    [string]$Risk = 'high',
    [string]$LegacyStatusPath,
    [string]$DesktopSnapshotPath,
    [string]$OutputPath,
    [ValidateRange(30, 3600)]
    [int]$FreshnessSeconds = 300,
    [ValidateRange(30, 86400)]
    [int]$StaleAfterSeconds = 900
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if ([string]::IsNullOrWhiteSpace($LegacyStatusPath)) {
    $LegacyStatusPath = Join-Path $workspaceRoot 'logs\local-core-guardian\status.json'
}

function Get-OptionalProperty {
    param([object]$Object, [string]$Name)
    if ($null -ne $Object -and $Object.PSObject.Properties.Name -contains $Name) {
        return $Object.$Name
    }
    return $null
}

function Get-WorkspaceEvidenceRef {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $Path -replace '\\', '/'
    }
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if ($resolved.StartsWith($workspaceRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $resolved.Substring($workspaceRoot.Length).TrimStart('\', '/') -replace '\\', '/'
    }
    return $resolved
}

function Get-DesktopHealthEvidence {
    param([datetime]$ObservedAt)

    $evidence = [ordered]@{
        input_state = 'not-provided'
        path_ref = $null
        parse_state = $null
        schema_version = $null
        adapter_id = $null
        health_state = $null
        observed_at = $null
        fresh = $false
        read_only = $null
        guardian_allow_eligible = $null
        auto_repair_performed = $null
        blockers = @()
        snapshot_sha256 = $null
    }
    if ([string]::IsNullOrWhiteSpace($DesktopSnapshotPath)) { return [pscustomobject]$evidence }

    $evidence.path_ref = Get-WorkspaceEvidenceRef $DesktopSnapshotPath
    if (-not (Test-Path -LiteralPath $DesktopSnapshotPath -PathType Leaf)) {
        $evidence.input_state = 'missing'
        $evidence.parse_state = 'missing'
        return [pscustomobject]$evidence
    }
    try {
        $snapshot = Get-Content -Raw -Encoding UTF8 -LiteralPath $DesktopSnapshotPath | ConvertFrom-Json
    } catch {
        $evidence.input_state = 'invalid'
        $evidence.parse_state = 'invalid-json'
        return [pscustomobject]$evidence
    }
    $evidence.input_state = 'observed'
    $evidence.parse_state = 'valid-json'
    $evidence.schema_version = Get-OptionalProperty $snapshot 'schema_version'
    $evidence.adapter_id = Get-OptionalProperty $snapshot 'adapter_id'
    $evidence.health_state = Get-OptionalProperty $snapshot 'health_state'
    $evidence.observed_at = Get-OptionalProperty $snapshot 'observed_at'
    $evidence.read_only = Get-OptionalProperty $snapshot 'read_only'
    $evidence.guardian_allow_eligible = Get-OptionalProperty $snapshot 'guardian_allow_eligible'
    $evidence.auto_repair_performed = Get-OptionalProperty $snapshot 'auto_repair_performed'
    $evidence.blockers = @(Get-OptionalProperty $snapshot 'blockers')
    $evidence.snapshot_sha256 = Get-OptionalProperty $snapshot 'snapshot_sha256'
    try {
        $snapshotAt = ([datetime]::Parse([string]$evidence.observed_at)).ToUniversalTime()
        $evidence.fresh = ($snapshotAt -ge $ObservedAt.AddSeconds(-$StaleAfterSeconds)) -and ($snapshotAt -le $ObservedAt.AddSeconds(5))
    } catch { $evidence.fresh = $false }
    return [pscustomobject]$evidence
}

function New-DenyDecision {
    param(
        [string]$Reason,
        [string]$SourceState,
        [object]$LegacySnapshot,
        [object]$DesktopHealth,
        [datetime]$ObservedAt
    )

    $evidenceRefs = [System.Collections.Generic.List[string]]::new()
    [void]$evidenceRefs.Add((Get-WorkspaceEvidenceRef $LegacyStatusPath))
    if (-not [string]::IsNullOrWhiteSpace([string]$DesktopHealth.path_ref)) { [void]$evidenceRefs.Add([string]$DesktopHealth.path_ref) }

    return [ordered]@{
        decision_id = 'guardian-runtime-canary:' + [guid]::NewGuid().ToString('n')
        contract_id = 'guardian-decision-contract-v1'
        issuer = 'guardian-runtime-canary'
        target_id = $TargetId
        action = $Action
        risk = $Risk
        decision = 'deny'
        state = 'unknown'
        enforcement = 'deny'
        observed_at = $ObservedAt.ToUniversalTime().ToString('o')
        fresh_until = $ObservedAt.ToUniversalTime().AddSeconds($FreshnessSeconds).ToString('o')
        policy_version = 'guardian-runtime-canary-v1'
        block_reason = $Reason
        source_state = $SourceState
        evidence_refs = @($evidenceRefs | Select-Object -Unique)
        legacy_local_core_guardian = $LegacySnapshot
        desktop_health_observation = $DesktopHealth
        auto_repair_performed = $false
        next_action = 'Keep the canary deny-only; accumulate and independently verify Desktop health evidence before proposing a scoped allow path.'
    }
}

function Invoke-Evaluation {
    $observedAt = (Get-Date).ToUniversalTime()
    $desktopHealth = Get-DesktopHealthEvidence -ObservedAt $observedAt
    $legacySnapshot = [ordered]@{
        status_path = $LegacyStatusPath
        exists = $false
        last_write_utc = $null
        disabled = $null
        components = $null
        source_timestamp = $null
        parse_state = 'missing'
    }

    if (-not (Test-Path -LiteralPath $LegacyStatusPath -PathType Leaf)) {
        return New-DenyDecision -Reason 'legacy-guardian-status-missing' -SourceState 'missing' -LegacySnapshot $legacySnapshot -DesktopHealth $desktopHealth -ObservedAt $observedAt
    }

    $legacySnapshot.exists = $true
    $file = Get-Item -LiteralPath $LegacyStatusPath
    $legacySnapshot.last_write_utc = $file.LastWriteTimeUtc.ToString('o')
    try {
        $status = Get-Content -Raw -Encoding UTF8 -LiteralPath $LegacyStatusPath | ConvertFrom-Json
        $legacySnapshot.parse_state = 'valid-json'
        $legacySnapshot.disabled = Get-OptionalProperty $status 'disabled'
        $legacySnapshot.components = Get-OptionalProperty $status 'components'
        $legacySnapshot.source_timestamp = Get-OptionalProperty $status 'ts'
    }
    catch {
        $legacySnapshot.parse_state = 'invalid-json'
        return New-DenyDecision -Reason 'legacy-guardian-status-invalid' -SourceState 'invalid' -LegacySnapshot $legacySnapshot -DesktopHealth $desktopHealth -ObservedAt $observedAt
    }

    if ($legacySnapshot.disabled -eq $true) {
        return New-DenyDecision -Reason 'legacy-local-core-guardian-disabled' -SourceState 'disabled' -LegacySnapshot $legacySnapshot -DesktopHealth $desktopHealth -ObservedAt $observedAt
    }
    if ($file.LastWriteTimeUtc -lt $observedAt.AddSeconds(-$StaleAfterSeconds)) {
        return New-DenyDecision -Reason 'legacy-guardian-status-stale' -SourceState 'stale' -LegacySnapshot $legacySnapshot -DesktopHealth $desktopHealth -ObservedAt $observedAt
    }
    if ($legacySnapshot.components -eq 0) {
        return New-DenyDecision -Reason 'legacy-guardian-has-no-components' -SourceState 'empty' -LegacySnapshot $legacySnapshot -DesktopHealth $desktopHealth -ObservedAt $observedAt
    }

    # LocalCoreGuardian predates the governance contract and can restart components.
    # It is never trusted as an allow authority, even when its legacy snapshot looks healthy.
    return New-DenyDecision -Reason 'legacy-guardian-is-not-a-governance-allow-authority' -SourceState 'untrusted-legacy' -LegacySnapshot $legacySnapshot -DesktopHealth $desktopHealth -ObservedAt $observedAt
}

if ($Mode -eq 'SelfTest') {
    $originalLegacyStatusPath = $LegacyStatusPath
    $originalDesktopSnapshotPath = $DesktopSnapshotPath
    try {
        $LegacyStatusPath = Join-Path ([System.IO.Path]::GetTempPath()) ('guardian-status-missing-' + [guid]::NewGuid().ToString('n') + '.json')
        $DesktopSnapshotPath = Join-Path ([System.IO.Path]::GetTempPath()) ('desktop-health-missing-' + [guid]::NewGuid().ToString('n') + '.json')
        $missingDecision = Invoke-Evaluation
        $LegacyStatusPath = $originalLegacyStatusPath
        $DesktopSnapshotPath = $originalDesktopSnapshotPath
        $currentDecision = Invoke-Evaluation
    }
    finally {
        $LegacyStatusPath = $originalLegacyStatusPath
        $DesktopSnapshotPath = $originalDesktopSnapshotPath
    }
    $checks = @(
        [pscustomobject]@{ name = 'missing-status-defaults-to-deny'; passed = ($missingDecision.decision -eq 'deny' -and $missingDecision.state -eq 'unknown' -and $missingDecision.enforcement -eq 'deny' -and $missingDecision.block_reason -eq 'legacy-guardian-status-missing') },
        [pscustomobject]@{ name = 'current-legacy-source-never-allows'; passed = ($currentDecision.decision -eq 'deny' -and $currentDecision.enforcement -eq 'deny') },
        [pscustomobject]@{ name = 'current-legacy-decision-is-scoped'; passed = ($currentDecision.target_id -eq $TargetId -and $currentDecision.action -eq $Action -and $currentDecision.risk -eq $Risk) },
        [pscustomobject]@{ name = 'desktop-health-missing-is-explicit-but-never-allows'; passed = ($missingDecision.desktop_health_observation.input_state -eq 'missing' -and $missingDecision.decision -eq 'deny') },
        [pscustomobject]@{ name = 'adapter-never-auto-repairs'; passed = ($missingDecision.auto_repair_performed -eq $false -and $currentDecision.auto_repair_performed -eq $false) }
    )
    [pscustomobject]@{
        verifier = 'Invoke-GuardianDecision.ps1'
        read_only = $true
        passed = (@($checks | Where-Object { -not $_.passed }).Count -eq 0)
        checks = $checks
    } | ConvertTo-Json -Depth 6
    exit 0
}

$decision = Invoke-Evaluation
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $directory = Split-Path -Parent $OutputPath
    if (-not [string]::IsNullOrWhiteSpace($directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
    $decision | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
}
$decision | ConvertTo-Json -Depth 10
