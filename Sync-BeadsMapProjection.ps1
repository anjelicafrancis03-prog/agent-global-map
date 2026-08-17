[CmdletBinding()]
param(
    [ValidateSet('Preview', 'Apply', 'Rollback')]
    [string]$Mode = 'Preview',
    [string]$ProjectionPath,
    [string]$MapPath,
    [string]$AuthorizationPath,
    [string]$ReceiptDirectory,
    [string]$ReceiptPath,
    [string]$GuardianAdapterPath,
    [string]$ControlledWriteGatePath,
    [string]$ControlledWriteWhitelistPath,
    [string]$ControlledWriteContractPath,
    [string]$ControlledWriteStopStatePath,
    [string]$ControlledWriteMakerId = 'agent:sync-beads-map-projection',
    [string]$ControlledWriteCheckerId = 'root:design-only',
    [string]$BdCommand = 'bd',
    [switch]$DryRun,
    [switch]$SkipControlledWriteGate,
    [switch]$SelfTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if ([string]::IsNullOrWhiteSpace($ProjectionPath)) { $ProjectionPath = Join-Path $workspaceRoot 'docs\architecture\beads-task-projections-v1.json' }
if ([string]::IsNullOrWhiteSpace($MapPath)) { $MapPath = Join-Path $workspaceRoot 'docs\architecture\global-map-v1.json' }
# 2026-08-16 普通15 P0-5 fix: 默认授权指向批次 2 追认授权（用户裁定）；
# 原 07-25 批次 1 授权（六任务 pilot）已过期，保留归档不删除。
if ([string]::IsNullOrWhiteSpace($AuthorizationPath)) { $AuthorizationPath = Join-Path $workspaceRoot 'docs\architecture\authorizations\root-authorization-2026-08-16-beads-projection-write-pilot-b2.json' }
if ([string]::IsNullOrWhiteSpace($ReceiptDirectory)) { $ReceiptDirectory = Join-Path $workspaceRoot 'reports\run-manifests\2026-07-25-codex-temr-16-beads-map-write-pilot\receipts' }
if ([string]::IsNullOrWhiteSpace($GuardianAdapterPath)) { $GuardianAdapterPath = Join-Path $PSScriptRoot 'Invoke-GuardianDecision.ps1' }
if ([string]::IsNullOrWhiteSpace($ControlledWriteGatePath)) { $ControlledWriteGatePath = Join-Path $PSScriptRoot 'Invoke-ControlledWriteGate.ps1' }
if ([string]::IsNullOrWhiteSpace($ControlledWriteWhitelistPath)) { $ControlledWriteWhitelistPath = Join-Path $workspaceRoot 'docs\architecture\controlled-write-whitelist-v1.json' }
if ([string]::IsNullOrWhiteSpace($ControlledWriteContractPath)) { $ControlledWriteContractPath = Join-Path $workspaceRoot 'docs\architecture\controlled-write-contract-v1.json' }
if ([string]::IsNullOrWhiteSpace($ControlledWriteStopStatePath)) { $ControlledWriteStopStatePath = Join-Path $workspaceRoot 'reports\run-manifests\2026-07-25-codex-temr-10-controlled-write-scaffold\stop-state.json' }

$metadataKeys = @(
    'map_node_id',
    'map_subnode',
    'map_path',
    'map_projection_id',
    'map_projection_version',
    'map_projection_registry',
    'map_projection_authorization',
    'map_projection_receipt'
)

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "missing input: $Path" }
    return Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
}

function Get-GuardianGate {
    param([string]$DecisionOutputPath)

    if (-not (Test-Path -LiteralPath $GuardianAdapterPath -PathType Leaf)) {
        return [pscustomobject]@{ allowed = $false; reason = 'guardian-runtime-adapter-missing'; decision = $null; receipt_path = $null }
    }
    try {
        # Parameters held in an array are not reparsed as PowerShell parameter
        # tokens by the call operator. Invoke the script with explicit named
        # parameters so the adapter receives the intended scope.
        if ([string]::IsNullOrWhiteSpace($DecisionOutputPath)) {
            $raw = & $GuardianAdapterPath -Mode Evaluate -TargetId 'projection.beads-task-tree' -Action 'beads-projection-metadata-write' -Risk high 2>&1 | Out-String
        }
        else {
            $raw = & $GuardianAdapterPath -Mode Evaluate -TargetId 'projection.beads-task-tree' -Action 'beads-projection-metadata-write' -Risk high -OutputPath $DecisionOutputPath 2>&1 | Out-String
        }
        # The adapter is a PowerShell script, not a native executable. A
        # preceding `bd` query can leave $LASTEXITCODE non-zero even when the
        # adapter produces a valid decision. Therefore accept only strictly
        # parseable and complete JSON below; any exception, noise, or malformed
        # output is converted to deny/unreachable by the catch block.
        $decision = $raw | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        return [pscustomobject]@{ allowed = $false; reason = 'guardian-runtime-adapter-unreachable-or-invalid'; decision = $null; receipt_path = $DecisionOutputPath }
    }

    $required = @('decision_id', 'target_id', 'action', 'risk', 'observed_at', 'fresh_until', 'policy_version', 'block_reason', 'evidence_refs')
    $missing = @($required | Where-Object { -not ($decision.PSObject.Properties.Name -contains $_) -or $null -eq $decision.$_ -or [string]::IsNullOrWhiteSpace([string]$decision.$_) })
    $fresh = $false
    try { $fresh = ([datetime]$decision.fresh_until).ToUniversalTime() -gt (Get-Date).ToUniversalTime() } catch { $fresh = $false }
    $matches = ($decision.target_id -eq 'projection.beads-task-tree' -and $decision.action -eq 'beads-projection-metadata-write' -and $decision.risk -eq 'high')
    $allowed = ($missing.Count -eq 0 -and $fresh -and $matches -and $decision.decision -eq 'allow' -and $decision.enforcement -eq 'allow')
    $reason = if ($allowed) { 'guardian-allowed' } elseif ($null -eq $decision) { 'guardian-decision-missing' } else { [string]$decision.block_reason }
    return [pscustomobject]@{
        allowed = $allowed
        reason = $reason
        decision = $decision
        receipt_path = $DecisionOutputPath
        missing_fields = $missing
        fresh = $fresh
        scope_matches = $matches
    }
}

function Invoke-ControlledWritePreviewGate {
    param(
        [int]$EstimatedWrites,
        [string]$OutputPath
    )

    if ($SkipControlledWriteGate) {
        return [pscustomobject]@{
            invoked = $false
            skipped = $true
            can_preview = $true
            can_apply = $false
            reason = 'skip-controlled-write-gate'
            receipt_path = $null
            raw = $null
        }
    }

    if (-not (Test-Path -LiteralPath $ControlledWriteGatePath -PathType Leaf)) {
        return [pscustomobject]@{
            invoked = $false
            skipped = $false
            can_preview = $false
            can_apply = $false
            reason = 'controlled-write-gate-script-missing'
            receipt_path = $null
            raw = $null
        }
    }

    $args = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $ControlledWriteGatePath,
        '-Mode', 'Preview',
        '-WhitelistPath', $ControlledWriteWhitelistPath,
        '-ContractPath', $ControlledWriteContractPath,
        '-WriteAdapterId', 'write.beads-projection-metadata',
        '-Action', 'beads-projection-metadata-write',
        '-TargetIdsCsv', 'projection.beads-task-tree',
        '-FieldsCsv', 'map_node_id,map_subnode,map_path,map_projection_id,map_projection_version,map_projection_registry,map_projection_authorization,map_projection_receipt',
        '-EstimatedWrites', ([string]$EstimatedWrites),
        '-EstimatedWallSeconds', '120',
        '-MakerId', $ControlledWriteMakerId,
        '-CheckerId', $ControlledWriteCheckerId,
        '-StopStatePath', $ControlledWriteStopStatePath
    )
    if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
        $args += @('-OutputPath', $OutputPath)
    }

    $raw = & powershell.exe @args 2>&1 | Out-String
    $parsed = $null
    try {
        $parsed = $raw | ConvertFrom-Json
    }
    catch {
        return [pscustomobject]@{
            invoked = $true
            skipped = $false
            can_preview = $false
            can_apply = $false
            reason = 'controlled-write-gate-invalid-json'
            receipt_path = $OutputPath
            raw = $raw
        }
    }

    $canPreview = $false
    $canApply = $false
    if ($null -ne $parsed) {
        if ($parsed.PSObject.Properties.Name -contains 'can_preview') { $canPreview = [bool]$parsed.can_preview }
        if ($parsed.PSObject.Properties.Name -contains 'can_apply') { $canApply = [bool]$parsed.can_apply }
    }

    return [pscustomobject]@{
        invoked = $true
        skipped = $false
        can_preview = $canPreview
        can_apply = $canApply
        reason = $(if ($canPreview) { 'preview-ok' } else { 'preview-blocked' })
        receipt_path = $OutputPath
        raw = $parsed
        maker_id = $ControlledWriteMakerId
        checker_id = $ControlledWriteCheckerId
        note = 'Apply requires can_preview only; can_apply stays false while whitelist apply_enabled=false'
    }
}

function Get-Duplicates {

    param([object[]]$Values)
    return @($Values | Where-Object { $_ -ne $null -and [string]$_ -ne '' } | Group-Object | Where-Object Count -gt 1 | ForEach-Object Name)
}

function Test-ExactSet {
    param([object[]]$Actual, [object[]]$Expected)
    $actualValues = @($Actual | ForEach-Object { [string]$_ })
    $expectedValues = @($Expected | ForEach-Object { [string]$_ })
    return (@($actualValues | Where-Object { $_ -notin $expectedValues }).Count -eq 0 -and @($expectedValues | Where-Object { $_ -notin $actualValues }).Count -eq 0 -and @(Get-Duplicates $actualValues).Count -eq 0)
}

function Get-ValidationErrors {
    param([object]$Projection, [object]$Map, [object]$Authorization)

    $errors = New-Object System.Collections.Generic.List[string]
    if ($Projection.registry_id -ne 'beads-task-projections-v1' -or $Projection.schema_version -ne '1.0.0') { [void]$errors.Add('projection registry identity mismatch') }
    if ($Projection.status -ne 'authorized-write-pilot' -or $Projection.projection_id -ne 'projection.beads-task-tree') { [void]$errors.Add('projection registry status or projection id mismatch') }
    if ($Projection.central_authority -ne 'authority.global-map-governance' -or $Projection.beads_lifecycle_authority -ne 'authority.beads') { [void]$errors.Add('projection authority boundary mismatch') }
    if ($Projection.write_boundary.metadata_only -ne $true -or $Projection.write_boundary.preserves_beads_lifecycle_fields -ne $true -or $Projection.write_boundary.default_mode -ne 'preview' -or $Projection.write_boundary.conflict_policy -ne 'block-on-different-existing-value') { [void]$errors.Add('projection write boundary mismatch') }
    if (-not (Test-ExactSet @($Projection.metadata_contract.PSObject.Properties.Name) $metadataKeys)) { [void]$errors.Add('projection metadata contract mismatch') }
    if ($Map.authority.runtime_cutover -ne $false -or $Map.authority.task_lifecycle_authority -ne 'beads') { [void]$errors.Add('global map lifecycle authority boundary mismatch') }
    $mapNodeIds = @($Map.nodes | ForEach-Object { [string]$_.map_node_id })
    $beadsProjection = @($Map.projection_registry | Where-Object { $_.projection_id -eq 'projection.beads-task-tree' })
    if ($beadsProjection.Count -ne 1 -or $beadsProjection[0].write_mode -ne 'authorized-metadata-pilot' -or -not (Test-ExactSet @($beadsProjection[0].metadata_fields) $metadataKeys)) { [void]$errors.Add('global map Beads write-pilot registration mismatch') }
    $beadsAdapter = @($Map.source_adapters | Where-Object { $_.adapter_id -eq 'adapter.beads' })
    if ($beadsAdapter.Count -ne 1 -or $beadsAdapter[0].write_mode -ne 'authorized-metadata-pilot' -or $beadsAdapter[0].write_scope -ne 'approved-task-metadata-only') { [void]$errors.Add('global map Beads adapter write boundary mismatch') }

    if ($Authorization.status -ne 'approved' -or $Authorization.action -ne 'beads-projection-metadata-write') { [void]$errors.Add('root authorization is not approved for this action') }
    if (-not (Test-ExactSet @($Authorization.scope.allowed_metadata_keys) $metadataKeys)) { [void]$errors.Add('root authorization metadata allowlist mismatch') }
    if ($Authorization.scope.rollback_authorized -ne $true) { [void]$errors.Add('root authorization lacks rollback permission') }

    $records = @($Projection.projections)
    $issueIds = @($records | ForEach-Object { [string]$_.beads_issue_id })
    if ($records.Count -eq 0 -or @(Get-Duplicates $issueIds).Count -gt 0) { [void]$errors.Add('projection records need unique Beads issue ids') }
    if (-not (Test-ExactSet $issueIds @($Authorization.scope.target_issue_ids))) { [void]$errors.Add('projection target set differs from root authorization') }
    foreach ($record in $records) {
        $issueId = [string]$record.beads_issue_id
        if ([string]$record.projection_record_id -eq '' -or [string]$record.relation_type -ne 'task-classified-under' -or [string]$record.status -ne 'approved-for-write') { [void]$errors.Add("projection record $issueId has invalid identity or state") }
        if ([string]$record.map_node_id -notin $mapNodeIds) { [void]$errors.Add("projection record $issueId targets unknown map node") }
        $expectedPath = if ($record.map_node_id -eq 'map.root-governance') { 'map.root-governance' } else { "map.root-governance/$($record.map_node_id)" }
        if ([string]$record.map_path -ne $expectedPath) { [void]$errors.Add("projection record $issueId has invalid v1 map_path") }
        if (@($record.evidence_refs).Count -eq 0) { [void]$errors.Add("projection record $issueId lacks evidence reference") }
    }
    return @($errors)
}

function Test-ActiveAuthorization {
    param([object]$Authorization)
    $now = Get-Date
    if ([datetime]$Authorization.expires_at -lt $now) { throw "root authorization expired: $($Authorization.authorization_id)" }
}

function Invoke-BdJson {
    param([string[]]$Arguments)
    # PowerShell 5.1 pipeline/`Out-String` capture can corrupt large `bd show --json`
    # payloads (especially issues with long embedded parent notes). Capture via cmd
    # file redirect and parse UTF-8 file contents instead.
    $outFile = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), ('bd-out-' + [guid]::NewGuid().ToString('N') + '.json'))
    $errFile = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), ('bd-err-' + [guid]::NewGuid().ToString('N') + '.txt'))
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $exitCode = 1
    $stderrText = ''
    try {
        $quotedArgs = @()
        foreach ($a in $Arguments) {
            $s = [string]$a
            if ($s -match '\s') {
                $quotedArgs += ('"' + ($s.Replace('"', '\"')) + '"')
            } else {
                $quotedArgs += $s
            }
        }
        $bdExe = [string]$BdCommand
        if ($bdExe -match '\s') { $bdExe = '"' + $bdExe + '"' }
        $cmdLine = $bdExe + ' ' + ($quotedArgs -join ' ') + ' > "' + $outFile + '" 2> "' + $errFile + '"'
        cmd.exe /c $cmdLine | Out-Null
        $exitCode = $LASTEXITCODE
        if (Test-Path -LiteralPath $errFile) {
            $stderrText = [System.IO.File]::ReadAllText($errFile)
        }
        if ($exitCode -ne 0) {
            throw "bd command failed (exit=$exitCode): $($Arguments -join ' ')`n$stderrText"
        }
        if (-not (Test-Path -LiteralPath $outFile -PathType Leaf)) {
            throw "bd command produced no stdout file: $($Arguments -join ' ')"
        }
        $raw = [System.IO.File]::ReadAllText($outFile, [System.Text.UTF8Encoding]::new($false))
        if ([string]::IsNullOrWhiteSpace($raw)) {
            throw "bd command returned empty stdout: $($Arguments -join ' ')`n$stderrText"
        }
        $jsonStart = -1
        for ($i = 0; $i -lt $raw.Length; $i++) {
            $ch = $raw[$i]
            if ($ch -eq '[' -or $ch -eq '{') { $jsonStart = $i; break }
        }
        if ($jsonStart -lt 0) {
            throw "bd command returned no parseable JSON: $($Arguments -join ' ')`n$raw"
        }
        $candidate = $raw.Substring($jsonStart)
        try {
            $parsed = $candidate | ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw "bd command returned no parseable JSON: $($Arguments -join ' ')`n$($_.Exception.Message)"
        }
        # Emit each response record so callers consistently receive one issue object.
        foreach ($item in @($parsed)) {
            Write-Output -InputObject $item
        }
        return
    }
    finally {
        $ErrorActionPreference = $previousPreference
        foreach ($f in @($outFile, $errFile)) {
            if (Test-Path -LiteralPath $f) {
                Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Get-IssueMetadata {
    param([object]$Issue)
    $metadata = @{}
    if (($Issue.PSObject.Properties.Name -contains 'metadata') -and $null -ne $Issue.metadata) {
        foreach ($property in $Issue.metadata.PSObject.Properties) {
            $metadata[[string]$property.Name] = [string]$property.Value
        }
    }
    return $metadata
}

function Get-ExpectedMetadata {
    param([object]$Record, [object]$Projection, [object]$Authorization)
    return [ordered]@{
        map_node_id = [string]$Record.map_node_id
        map_path = [string]$Record.map_path
        map_projection_id = [string]$Projection.projection_id
        map_projection_version = [string]$Projection.projection_version
        map_projection_registry = 'docs/architecture/beads-task-projections-v1.json'
        map_projection_authorization = [string]$Authorization.authorization_id
        map_projection_receipt = [string]$Projection.receipt_directory
    }
}

function New-Operation {
    param([object]$Record, [object]$Projection, [object]$Authorization)
    $issue = @(Invoke-BdJson @('show', [string]$Record.beads_issue_id, '--json'))[0]
    $before = Get-IssueMetadata $issue
    $expected = Get-ExpectedMetadata $Record $Projection $Authorization
    $conflicts = New-Object System.Collections.Generic.List[string]
    $missing = 0
    foreach ($key in $metadataKeys) {
        if (-not $before.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($before[$key])) {
            $missing++
        }
        elseif ([string]$before[$key] -ne [string]$expected[$key]) {
            [void]$conflicts.Add($key)
        }
    }
    $operation = if ($conflicts.Count -gt 0) { 'conflict' } elseif ($missing -eq 0) { 'noop' } else { 'apply' }
    return [pscustomobject]@{
        beads_issue_id = [string]$Record.beads_issue_id
        source_status = [string]$issue.status
        source_priority = [int]$issue.priority
        before_metadata = $before
        expected_metadata = $expected
        operation = $operation
        conflict_keys = @($conflicts)
    }
}

function Invoke-MetadataSet {
    param([string]$IssueId, [System.Collections.IDictionary]$Metadata)
    $arguments = New-Object System.Collections.Generic.List[string]
    [void]$arguments.Add('update')
    [void]$arguments.Add($IssueId)
    foreach ($key in $metadataKeys) {
        [void]$arguments.Add('--set-metadata')
        [void]$arguments.Add("$key=$($Metadata[$key])")
    }
    [void]$arguments.Add('--json')
    [void](Invoke-BdJson $arguments.ToArray())
}

function Restore-Metadata {
    param([string]$IssueId, [System.Collections.IDictionary]$BeforeMetadata)
    foreach ($key in $metadataKeys) {
        if ($BeforeMetadata.ContainsKey($key) -and -not [string]::IsNullOrWhiteSpace($BeforeMetadata[$key])) {
            [void](Invoke-BdJson @('update', $IssueId, '--set-metadata', "$key=$($BeforeMetadata[$key])", '--json'))
        }
        else {
            [void](Invoke-BdJson @('update', $IssueId, '--unset-metadata', $key, '--json'))
        }
    }
}

function Confirm-ExpectedMetadata {
    param([string]$IssueId, [System.Collections.IDictionary]$ExpectedMetadata)
    $issue = @(Invoke-BdJson @('show', $IssueId, '--json'))[0]
    $actual = Get-IssueMetadata $issue
    foreach ($key in $metadataKeys) {
        if (-not $actual.ContainsKey($key) -or [string]$actual[$key] -ne [string]$ExpectedMetadata[$key]) {
            throw "metadata readback mismatch for $IssueId field $key"
        }
    }
}

$projection = Read-JsonFile $ProjectionPath
$map = Read-JsonFile $MapPath
$authorization = Read-JsonFile $AuthorizationPath
$validationErrors = @(Get-ValidationErrors $projection $map $authorization)

if ($SelfTest) {
    $clone = ConvertFrom-Json (ConvertTo-Json $projection -Depth 30)
    $clone.projections[0].map_node_id = 'map.missing'
    $unknownNodeRejected = @(Get-ValidationErrors $clone $map $authorization).Count -gt 0
    $clone = ConvertFrom-Json (ConvertTo-Json $projection -Depth 30)
    $clone.projections[0].map_path = 'map.root-governance/map.beads'
    $badPathRejected = @(Get-ValidationErrors $clone $map $authorization).Count -gt 0
    $expiredAuthorization = ConvertFrom-Json (ConvertTo-Json $authorization -Depth 20)
    $expiredAuthorization.expires_at = '2000-01-01T00:00:00+00:00'
    try {
        Test-ActiveAuthorization $expiredAuthorization
        $expiredAuthorizationRejected = $false
    }
    catch {
    $expiredAuthorizationRejected = $true
    }
    $gatePresent = (Test-Path -LiteralPath $ControlledWriteGatePath -PathType Leaf)
    $guardianPresent = (Test-Path -LiteralPath $GuardianAdapterPath -PathType Leaf)
    [pscustomobject]@{
        verifier = 'Sync-BeadsMapProjection.ps1'
        read_only = $true
        self_tests = @(
            [pscustomobject]@{ name = 'unknown-map-node'; passed = $unknownNodeRejected },
            [pscustomobject]@{ name = 'invalid-v1-map-path'; passed = $badPathRejected },
            [pscustomobject]@{ name = 'expired-authorization'; passed = $expiredAuthorizationRejected },
            [pscustomobject]@{ name = 'guardian-adapter-present'; passed = $guardianPresent },
            [pscustomobject]@{ name = 'controlled-write-gate-present'; passed = $gatePresent }
        )
        passed = ($unknownNodeRejected -and $badPathRejected -and $expiredAuthorizationRejected -and $guardianPresent -and $gatePresent)
    } | ConvertTo-Json -Depth 6
    if (-not ($unknownNodeRejected -and $badPathRejected -and $expiredAuthorizationRejected -and $guardianPresent -and $gatePresent)) { exit 1 }
    exit 0
}

if ($validationErrors.Count -gt 0) { throw ("projection validation failed:`n - " + ($validationErrors -join "`n - ")) }
$authorizationActive = $true
$authorizationError = $null
try {
    Test-ActiveAuthorization $authorization
}
catch {
    $authorizationActive = $false
    $authorizationError = $_.Exception.Message
}
if ($Mode -in @('Apply', 'Rollback') -and -not $authorizationActive) { throw $authorizationError }

if ($Mode -eq 'Rollback') {
    if ([string]::IsNullOrWhiteSpace($ReceiptPath)) { throw 'Rollback requires -ReceiptPath' }
    $receipt = Read-JsonFile $ReceiptPath
    if ($receipt.authorization_id -ne $authorization.authorization_id -or $receipt.projection_id -ne $projection.projection_id) { throw 'rollback receipt does not match current authorization or projection' }
    $rollbackPlan = @()
    foreach ($operation in @($receipt.operations)) {
        $current = New-Operation ($projection.projections | Where-Object { $_.beads_issue_id -eq $operation.beads_issue_id }) $projection $authorization
        $expected = $operation.expected_metadata
        $currentMetadata = $current.before_metadata
        $mismatched = @($metadataKeys | Where-Object { -not $currentMetadata.ContainsKey($_) -or [string]$currentMetadata[$_] -ne [string]$expected.$_ })
        $rollbackPlan += [pscustomobject]@{ beads_issue_id = $operation.beads_issue_id; operation = $(if ($mismatched.Count -eq 0) { 'restore' } else { 'conflict' }); conflict_keys = $mismatched; before_metadata = $operation.before_metadata }
    }
    if (@($rollbackPlan | Where-Object operation -eq 'conflict').Count -gt 0) { throw 'rollback blocked because Beads metadata changed after the apply receipt' }
    if ($DryRun) {
        [pscustomobject]@{ mode = 'Rollback'; dry_run = $true; writes_performed = 0; operations = $rollbackPlan } | ConvertTo-Json -Depth 10
        exit 0
    }
    foreach ($operation in $rollbackPlan) { Restore-Metadata $operation.beads_issue_id $operation.before_metadata }
    [pscustomobject]@{ mode = 'Rollback'; dry_run = $false; writes_performed = $rollbackPlan.Count; operations = $rollbackPlan } | ConvertTo-Json -Depth 10
    exit 0
}

$operations = @($projection.projections | ForEach-Object { New-Operation $_ $projection $authorization })
$conflicts = @($operations | Where-Object operation -eq 'conflict')
$guardianPreview = Get-GuardianGate
$summary = [pscustomobject]@{
    mode = $Mode
    read_only = ($Mode -eq 'Preview')
    can_apply = ($conflicts.Count -eq 0 -and $authorizationActive -and $guardianPreview.allowed)
    authorization_active = $authorizationActive
    authorization_error = $authorizationError
    target_count = $operations.Count
    apply_count = @($operations | Where-Object operation -eq 'apply').Count
    noop_count = @($operations | Where-Object operation -eq 'noop').Count
    conflict_count = $conflicts.Count
    writes_performed = 0
    guardian = $guardianPreview
    operations = $operations
}

if ($Mode -eq 'Preview') {
    $summary | ConvertTo-Json -Depth 12
    if ($conflicts.Count -gt 0) { exit 1 }
    exit 0
}

if ($conflicts.Count -gt 0) { throw 'Apply blocked by existing metadata conflict' }
if (-not (Test-Path -LiteralPath $ReceiptDirectory)) { New-Item -ItemType Directory -Path $ReceiptDirectory -Force | Out-Null }

$runId = 'receipt:beads-map-write:' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
# NOTE: must not use $controlledWriteGatePath — PowerShell is case-insensitive and would
# clobber script param $ControlledWriteGatePath (path to Invoke-ControlledWriteGate.ps1).
$controlledWriteGateReceiptPath = Join-Path $ReceiptDirectory (($runId -replace ':', '-') + '-controlled-write-gate.json')
$controlledWriteGate = Invoke-ControlledWritePreviewGate -EstimatedWrites (@($operations | Where-Object operation -eq 'apply').Count) -OutputPath $controlledWriteGateReceiptPath
# codex-ygjz P1-1: Apply path requires can_apply=true (audit: previously only can_preview was checked,
# so the gate's hardcoded can_apply=false would be bypassed the day Guardian allow is enabled).
if (-not $controlledWriteGate.can_preview -or -not $controlledWriteGate.can_apply) {
    throw ("Controlled-write gate blocked Apply (require can_preview && can_apply; got can_preview=" + [string]$controlledWriteGate.can_preview + " can_apply=" + [string]$controlledWriteGate.can_apply + "; apply stays disabled without explicit root authorization): " + [string]$controlledWriteGate.reason + ". Receipt: " + [string]$controlledWriteGate.receipt_path)
}

$guardianDecisionPath = Join-Path $ReceiptDirectory (($runId -replace ':', '-') + '-guardian-decision.json')
$guardianGate = Get-GuardianGate -DecisionOutputPath $guardianDecisionPath
if (-not $guardianGate.allowed) {
    throw "Guardian denied high-risk write: $($guardianGate.reason). Decision receipt: $guardianDecisionPath"
}
$preStatePath = Join-Path $ReceiptDirectory (($runId -replace ':', '-') + '-prestate.json')
$receiptPath = Join-Path $ReceiptDirectory (($runId -replace ':', '-') + '.json')
[pscustomobject]@{
    receipt_id = $runId
    authorization_id = $authorization.authorization_id
    projection_id = $projection.projection_id
    observed_at = (Get-Date).ToUniversalTime().ToString('o')
    operations = $operations
} | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $preStatePath -Encoding UTF8

$applied = New-Object System.Collections.Generic.List[object]
try {
    foreach ($operation in @($operations | Where-Object operation -eq 'apply')) {
        Invoke-MetadataSet $operation.beads_issue_id $operation.expected_metadata
        # Once `bd update` returns successfully, the operation is recoverable
        # even if the subsequent readback itself fails.
        [void]$applied.Add($operation)
        Confirm-ExpectedMetadata $operation.beads_issue_id $operation.expected_metadata
    }
    $receipt = [pscustomobject]@{
        receipt_id = $runId
        result = $(if ($applied.Count -eq 0) { 'idempotent-noop' } else { 'applied' })
        authorization_id = $authorization.authorization_id
        projection_id = $projection.projection_id
        projection_version = $projection.projection_version
        observed_at = (Get-Date).ToUniversalTime().ToString('o')
        pre_state_path = $preStatePath
        operations = $operations
        applied_issue_ids = @($applied | ForEach-Object beads_issue_id)
        controlled_write_gate_path = $controlledWriteGate.receipt_path
        controlled_write_gate_can_preview = $controlledWriteGate.can_preview
        controlled_write_gate_can_apply = $controlledWriteGate.can_apply
        maker_id = $ControlledWriteMakerId
        checker_id = $ControlledWriteCheckerId
        maker_checker_note = 'dual-actor recorded on Apply receipt; offline gate also evaluates maker!=checker when checker present'
        rollback = 'use -Mode Rollback -ReceiptPath <this receipt>; mutation conflicts block rollback'
    }
    $receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
    $summary.read_only = $false
    $summary.writes_performed = $applied.Count
    $summary | Add-Member -NotePropertyName receipt_id -NotePropertyValue $runId
    $summary | Add-Member -NotePropertyName receipt_path -NotePropertyValue $receiptPath
    $summary | Add-Member -NotePropertyName controlled_write_gate -NotePropertyValue $controlledWriteGate
    $summary | ConvertTo-Json -Depth 12
}
catch {
    $failure = $_
    $rollbackErrors = New-Object System.Collections.Generic.List[string]
    $reverseApplied = @($applied.ToArray())
    [array]::Reverse($reverseApplied)
    foreach ($operation in $reverseApplied) {
        try { Restore-Metadata $operation.beads_issue_id $operation.before_metadata }
        catch { [void]$rollbackErrors.Add($_.Exception.Message) }
    }
    [pscustomobject]@{
        receipt_id = $runId
        result = 'failed'
        authorization_id = $authorization.authorization_id
        projection_id = $projection.projection_id
        observed_at = (Get-Date).ToUniversalTime().ToString('o')
        pre_state_path = $preStatePath
        operations = $operations
        applied_issue_ids = @($applied | ForEach-Object beads_issue_id)
        rollback_errors = @($rollbackErrors)
        failure = $failure.Exception.Message
    } | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
    throw $failure
}
