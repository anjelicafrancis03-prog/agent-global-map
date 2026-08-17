# Invoke-ControlledWriteGate.ps1
# Offline / Preview gate for codex-temr.10 controlled-write pipeline.
# Evaluates whitelist + budget + CAS preconditions + maker-checker requirements + Stop latch.
# DEFAULT: Preview only. Apply is NOT exposed.
# Does NOT mutate Beads, Desktop, MCP, Skill, or credentials (Stop only writes a local latch file).

[CmdletBinding()]
param(
    [ValidateSet('Preview', 'Stop', 'SelfTest')]
    [string]$Mode = 'Preview',
    [string]$WhitelistPath = 'F:\codex\docs\architecture\controlled-write-whitelist-v1.json',
    [string]$ContractPath = 'F:\codex\docs\architecture\controlled-write-contract-v1.json',
    [string]$WriteAdapterId = 'write.beads-projection-metadata',
    [string]$Action = 'beads-projection-metadata-write',
    [string]$TargetIdsCsv = 'projection.beads-task-tree',
    [string]$FieldsCsv = '',
    [int]$EstimatedWrites = 1,
    [int]$EstimatedWallSeconds = 30,
    [string]$MakerId = 'agent:proposing',
    [string]$CheckerId = '',
    [string]$StopStatePath = 'F:\codex\reports\run-manifests\2026-07-25-codex-temr-10-controlled-write-scaffold\stop-state.json',
    [string]$CasExpectedDigest = '',
    [string]$CasActualDigest = '',
    [string]$OutputPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "missing: $Path" }
    return (Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json)
}

function Split-Csv {
    param([string]$Value)
    $items = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        foreach ($part in $Value.Split(',')) {
            $t = $part.Trim()
            if ($t -ne '') { [void]$items.Add($t) }
        }
    }
    return ,[string[]]$items.ToArray()
}

function Get-Prop {
    param($Object, [string]$Name, $Default = $null)
    if ($null -eq $Object) { return $Default }
    $names = @($Object.PSObject.Properties | ForEach-Object { $_.Name })
    if ($names -contains $Name) { return $Object.$Name }
    return $Default
}

function Get-StopStateObject {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]@{ stopped = $false; reason = $null; stopped_at = $null; authorization_id = $null }
    }
    return (Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json)
}

function Save-Json {
    param($Value, [string]$Path)
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    if (Test-Path -LiteralPath $Path) { throw "Refusing to overwrite: $Path" }
    $json = $Value | ConvertTo-Json -Depth 14
    [System.IO.File]::WriteAllText($Path, $json, [System.Text.UTF8Encoding]::new($false))
}

function Invoke-WriteGate {
    param(
        [string]$GateMode,
        $Whitelist,
        $Contract,
        [string]$AdapterId,
        [string]$Act,
        [string[]]$Targets,
        [string[]]$FieldList,
        [int]$Writes,
        [int]$WallSec,
        [string]$Maker,
        [string]$Checker,
        $StopState,
        [string]$CasExpected,
        [string]$CasActual
    )

    $blockers = New-Object System.Collections.Generic.List[string]
    $stages = New-Object System.Collections.Generic.List[object]
    $now = (Get-Date).ToUniversalTime()

    # stop latch
    $stopped = $false
    $stopReason = $null
    if ($null -ne $StopState) {
        $stoppedVal = Get-Prop $StopState 'stopped' $false
        $stopped = [System.Convert]::ToBoolean($stoppedVal)
        $stopReason = Get-Prop $StopState 'reason' $null
    }
    if ($stopped) {
        [void]$blockers.Add(('stop-latch-active:' + [string]$stopReason))
        [void]$stages.Add([pscustomobject]@{ stage = 'stop_latch'; passed = $false })
    } else {
        [void]$stages.Add([pscustomobject]@{ stage = 'stop_latch'; passed = $true })
    }

    # whitelist
    $adapter = $null
    foreach ($a in @($Whitelist.adapters)) {
        if ([string](Get-Prop $a 'write_adapter_id') -eq $AdapterId) {
            $adapter = $a
            break
        }
    }
    $actionOk = $false
    $targetsOk = $false
    $fieldsOk = $true
    $applyEnabled = $false
    $mcRequired = $false
    $adapterStatus = 'missing'
    if ($null -eq $adapter) {
        [void]$blockers.Add('adapter-not-on-whitelist')
        [void]$stages.Add([pscustomobject]@{ stage = 'validate_whitelist_target'; passed = $false })
    } else {
        $adapterStatus = [string](Get-Prop $adapter 'status' '')
        $applyEnabled = [System.Convert]::ToBoolean((Get-Prop $adapter 'apply_enabled' $false))
        $mcRequired = [System.Convert]::ToBoolean((Get-Prop $adapter 'maker_checker_required' $false))
        $actions = @()
        foreach ($x in @(Get-Prop $adapter 'allowed_actions' @())) { $actions += [string]$x }
        $actionOk = ($actions -contains $Act)
        if (-not $actionOk) { [void]$blockers.Add('action-not-allowed-for-adapter') }

        $adapterTarget = [string](Get-Prop $adapter 'target_id' '')
        $targetsOk = $true
        foreach ($t in $Targets) {
            if ($adapterTarget -ne '' -and [string]$t -ne $adapterTarget) {
                $targetsOk = $false
            }
        }
        if (-not $targetsOk) { [void]$blockers.Add('target-not-allowed-for-adapter') }

        $allowedFields = @()
        foreach ($f in @(Get-Prop $adapter 'allowed_fields' @())) { $allowedFields += [string]$f }
        foreach ($f in $FieldList) {
            if (@($allowedFields).Count -gt 0 -and ($allowedFields -notcontains $f)) {
                $fieldsOk = $false
                [void]$blockers.Add(('field-not-allowed:' + $f))
            }
        }
        $wlPass = ($actionOk -and $targetsOk -and $fieldsOk)
        [void]$stages.Add([pscustomobject]@{
            stage = 'validate_whitelist_target'
            passed = $wlPass
            adapter_status = $adapterStatus
            apply_enabled = $applyEnabled
        })
    }

    # budget
    $budget = Get-Prop $Whitelist 'budget_defaults' $null
    if ($null -eq $budget) {
        $cb = Get-Prop $Contract 'budget' $null
        if ($null -ne $cb) { $budget = Get-Prop $cb 'default_v1' $null }
    }
    $maxTargets = [int](Get-Prop $budget 'max_targets' 16)
    $maxWrites = [int](Get-Prop $budget 'max_writes_per_run' 32)
    $maxWall = [int](Get-Prop $budget 'max_wall_seconds' 300)
    $budgetOk = (@($Targets).Count -le $maxTargets -and $Writes -le $maxWrites -and $WallSec -le $maxWall -and $Writes -ge 0)
    if (-not $budgetOk) { [void]$blockers.Add('budget-exceeded') }
    [void]$stages.Add([pscustomobject]@{
        stage = 'check_budget'
        passed = $budgetOk
        estimated_targets = @($Targets).Count
        estimated_writes = $Writes
        estimated_wall_seconds = $WallSec
        max_targets = $maxTargets
        max_writes_per_run = $maxWrites
        max_wall_seconds = $maxWall
    })

    # CAS
    $casCompared = (-not [string]::IsNullOrWhiteSpace($CasExpected) -or -not [string]::IsNullOrWhiteSpace($CasActual))
    $casOk = $true
    if ($casCompared) {
        $casOk = ($CasExpected -eq $CasActual -and -not [string]::IsNullOrWhiteSpace($CasExpected))
        if (-not $casOk) { [void]$blockers.Add('cas-conflict') }
    }
    [void]$stages.Add([pscustomobject]@{
        stage = 'check_CAS_preconditions'
        passed = $casOk
        compared = $casCompared
        policy = 'block-on-different-existing-value'
    })

    # maker-checker
    $mcOk = $true
    if ($mcRequired) {
        if ([string]::IsNullOrWhiteSpace($Maker)) {
            $mcOk = $false
            [void]$blockers.Add('maker-missing')
        } elseif (-not [string]::IsNullOrWhiteSpace($Checker) -and $Checker -eq $Maker) {
            $mcOk = $false
            [void]$blockers.Add('maker-equals-checker')
        }
    }
    [void]$stages.Add([pscustomobject]@{
        stage = 'maker_checker_second_actor_if_required'
        passed = $mcOk
        required = $mcRequired
        maker_id = $Maker
        checker_id = $Checker
        checker_present = (-not [string]::IsNullOrWhiteSpace($Checker))
    })

    [void]$stages.Add([pscustomobject]@{
        stage = 'apply_enablement'
        passed = $true
        apply_enabled = $applyEnabled
        note = 'scaffold keeps apply_enabled=false on whitelist entries'
    })

    [void]$stages.Add([pscustomobject]@{
        stage = 'check_guardian_gate'
        passed = $true
        decision = 'not-invoked-in-offline-preview'
        note = 'Production Apply must call Invoke-GuardianDecision; deny-only canary remains default'
    })

    $hardCount = @($blockers).Count
    $canPreview = ($hardCount -eq 0)
    $canApply = $false

    return [pscustomobject]@{
        schema_version = 'controlled-write-gate-v1'
        beads_task = 'codex-temr.10'
        mode = $GateMode
        read_only = $true
        observed_at = $now.ToString('o')
        write_adapter_id = $AdapterId
        action = $Act
        target_ids = @($Targets)
        stages = @($stages.ToArray())
        blockers = @($blockers.ToArray())
        can_preview = $canPreview
        can_apply = $canApply
        apply_enabled_on_adapter = $applyEnabled
        mutations_performed = $false
        allow_granted = $false
        scheduler_registered = $false
        budget = [pscustomobject]@{
            estimated_targets = @($Targets).Count
            estimated_writes = $Writes
            estimated_wall_seconds = $WallSec
            max_targets = $maxTargets
            max_writes_per_run = $maxWrites
            max_wall_seconds = $maxWall
        }
        maker_checker = [pscustomobject]@{
            required = $mcRequired
            maker_id = $Maker
            checker_id = $Checker
        }
        stop_state = $StopState
        note = 'Offline Preview/Stop gate only. Does not call pilot Apply.'
    }
}

if ($Mode -eq 'SelfTest') {
    $wl = Read-JsonFile $WhitelistPath
    $ct = Read-JsonFile $ContractPath
    $stopClear = [pscustomobject]@{ stopped = $false; reason = $null; stopped_at = $null; authorization_id = $null }

    $okPreview = Invoke-WriteGate -GateMode 'Preview' -Whitelist $wl -Contract $ct `
        -AdapterId 'write.beads-projection-metadata' -Act 'beads-projection-metadata-write' `
        -Targets @('projection.beads-task-tree') -FieldList @('map_node_id') `
        -Writes 1 -WallSec 10 -Maker 'agent:a' -Checker 'root:user' -StopState $stopClear `
        -CasExpected 'abc' -CasActual 'abc'

    $badBudget = Invoke-WriteGate -GateMode 'Preview' -Whitelist $wl -Contract $ct `
        -AdapterId 'write.beads-projection-metadata' -Act 'beads-projection-metadata-write' `
        -Targets @('projection.beads-task-tree') -FieldList @() `
        -Writes 9999 -WallSec 10 -Maker 'agent:a' -Checker 'root:user' -StopState $stopClear `
        -CasExpected '' -CasActual ''

    $badCas = Invoke-WriteGate -GateMode 'Preview' -Whitelist $wl -Contract $ct `
        -AdapterId 'write.beads-projection-metadata' -Act 'beads-projection-metadata-write' `
        -Targets @('projection.beads-task-tree') -FieldList @() `
        -Writes 1 -WallSec 10 -Maker 'agent:a' -Checker 'root:user' -StopState $stopClear `
        -CasExpected 'x' -CasActual 'y'

    $badMc = Invoke-WriteGate -GateMode 'Preview' -Whitelist $wl -Contract $ct `
        -AdapterId 'write.beads-projection-metadata' -Act 'beads-projection-metadata-write' `
        -Targets @('projection.beads-task-tree') -FieldList @() `
        -Writes 1 -WallSec 10 -Maker 'same' -Checker 'same' -StopState $stopClear `
        -CasExpected '' -CasActual ''

    $unknown = Invoke-WriteGate -GateMode 'Preview' -Whitelist $wl -Contract $ct `
        -AdapterId 'write.does-not-exist' -Act 'nope' `
        -Targets @('x') -FieldList @() -Writes 1 -WallSec 1 -Maker 'a' -Checker 'b' -StopState $stopClear `
        -CasExpected '' -CasActual ''

    $stopActive = [pscustomobject]@{ stopped = $true; reason = 'fixture-stop'; stopped_at = '2026-07-25T00:00:00Z'; authorization_id = $null }
    $afterStop = Invoke-WriteGate -GateMode 'Preview' -Whitelist $wl -Contract $ct `
        -AdapterId 'write.beads-projection-metadata' -Act 'beads-projection-metadata-write' `
        -Targets @('projection.beads-task-tree') -FieldList @() `
        -Writes 1 -WallSec 10 -Maker 'agent:a' -Checker 'root:user' -StopState $stopActive `
        -CasExpected '' -CasActual ''

    $checks = @(
        [pscustomobject]@{ name = 'happy-preview-can-preview'; passed = (($okPreview.can_preview -eq $true) -and ($okPreview.can_apply -eq $false)) },
        [pscustomobject]@{ name = 'budget-exceeded-blocks'; passed = ($badBudget.blockers -contains 'budget-exceeded') },
        [pscustomobject]@{ name = 'cas-conflict-blocks'; passed = ($badCas.blockers -contains 'cas-conflict') },
        [pscustomobject]@{ name = 'maker-equals-checker-blocks'; passed = ($badMc.blockers -contains 'maker-equals-checker') },
        [pscustomobject]@{ name = 'unknown-adapter-blocks'; passed = ($unknown.blockers -contains 'adapter-not-on-whitelist') },
        [pscustomobject]@{ name = 'stop-latch-blocks'; passed = ($afterStop.can_preview -eq $false) },
        [pscustomobject]@{ name = 'never-can-apply'; passed = ($okPreview.can_apply -eq $false) },
        [pscustomobject]@{ name = 'no-mutations'; passed = ($okPreview.mutations_performed -eq $false) }
    )
    $failed = 0
    foreach ($c in $checks) { if (-not $c.passed) { $failed++ } }
    $result = [pscustomobject]@{
        verifier = 'Invoke-ControlledWriteGate.ps1'
        read_only = $true
        passed = ($failed -eq 0)
        checks = $checks
        mutations_performed = $false
    }
    $result | ConvertTo-Json -Depth 10
    if ($failed -gt 0) { exit 1 }
    exit 0
}

if ($Mode -eq 'Stop') {
    $stopState = [pscustomobject]@{
        stopped = $true
        reason = 'operator-stop-via-controlled-write-gate'
        stopped_at = (Get-Date).ToUniversalTime().ToString('o')
        authorization_id = $null
        write_adapter_id = $WriteAdapterId
        note = 'Scaffold stop latch for gate evaluation only. Does not stop Desktop slots or managers.'
    }
    $dir = Split-Path -Parent $StopStatePath
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    # Stop state is intentionally overwritable so operators can re-assert stop
    $json = $stopState | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($StopStatePath, $json, [System.Text.UTF8Encoding]::new($false))
    $result = [pscustomobject]@{
        schema_version = 'controlled-write-stop-receipt-v1'
        mode = 'Stop'
        stop_state_path = $StopStatePath
        stop_state = $stopState
        mutations_performed = $true
        mutation_scope = 'local-stop-latch-file-only'
        can_apply = $false
        allow_granted = $false
        note = 'Stop latch written for subsequent Preview gates. Clear by deleting stop-state.json.'
    }
    if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
        Save-Json -Value $result -Path $OutputPath
    }
    $result | ConvertTo-Json -Depth 12
    exit 0
}

# Preview
$whitelist = Read-JsonFile $WhitelistPath
$contract = Read-JsonFile $ContractPath
$stopState = Get-StopStateObject -Path $StopStatePath
$targets = Split-Csv $TargetIdsCsv
$fields = Split-Csv $FieldsCsv
if (@($targets).Count -eq 0) { $targets = @('projection.beads-task-tree') }

$result = Invoke-WriteGate -GateMode 'Preview' -Whitelist $whitelist -Contract $contract `
    -AdapterId $WriteAdapterId -Act $Action -Targets $targets -FieldList $fields `
    -Writes $EstimatedWrites -WallSec $EstimatedWallSeconds -Maker $MakerId -Checker $CheckerId `
    -StopState $stopState -CasExpected $CasExpectedDigest -CasActual $CasActualDigest

if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    Save-Json -Value $result -Path $OutputPath
}
$result | ConvertTo-Json -Depth 14
