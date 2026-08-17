# Test-ControlledWriteContract.ps1
# Offline validator for controlled-write-contract-v1 (codex-temr.10 design scaffold).
# Read-only. No Apply, no Beads mutation, no manager writes.

[CmdletBinding()]
param(
    [ValidateSet('SelfTest', 'Validate')]
    [string]$Mode = 'SelfTest',
    [string]$ContractPath = 'F:\codex\docs\architecture\controlled-write-contract-v1.json',
    [string]$PilotScriptPath = 'F:\codex\tools\agent-system-map\Sync-BeadsMapProjection.ps1',
    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-Optional {
    param($Object, [string]$Name, $Default = $null)
    if ($null -ne $Object -and $Object.PSObject.Properties.Name -contains $Name) { return $Object.$Name }
    return $Default
}

function Add-Check {
    param(
        [System.Collections.Generic.List[object]]$List,
        [string]$Name,
        [bool]$Passed,
        [string]$Detail = ''
    )
    $List.Add([pscustomobject]@{ name = $Name; passed = $Passed; detail = $Detail }) | Out-Null
}

function Test-Contract {
    param([object]$Contract, [string]$PilotPath)

    $checks = New-Object 'System.Collections.Generic.List[object]'

    $idOk = ($Contract.contract_id -eq 'controlled-write-contract-v1' -and $Contract.schema_version -eq '1.0.0')
    Add-Check -List $checks -Name 'contract-identity' -Passed:([bool]$idOk) -Detail ([string]$Contract.contract_id)

    $scaffoldOk = ($Contract.status -eq 'design-scaffold' -and $Contract.runtime_enforcement -eq $false)
    Add-Check -List $checks -Name 'design-scaffold-not-runtime' -Passed:([bool]$scaffoldOk)

    $modes = @()
    foreach ($m in @($Contract.modes)) { $modes += [string]$m.mode }
    $requiredModes = @('Preview', 'Apply', 'Rollback', 'Stop')
    $modesOk = $true
    foreach ($rm in $requiredModes) {
        if ($modes -notcontains $rm) { $modesOk = $false }
    }
    Add-Check -List $checks -Name 'modes-preview-apply-rollback-stop' -Passed:([bool]$modesOk) -Detail ($modes -join ',')

    $preview = $null
    foreach ($m in @($Contract.modes)) {
        if ([string]$m.mode -eq 'Preview') { $preview = $m; break }
    }
    $previewOk = ($null -ne $preview -and $preview.read_only -eq $true -and $preview.mutates -eq $false)
    Add-Check -List $checks -Name 'preview-is-read-only-default' -Passed:([bool]$previewOk)

    $apply = $null
    foreach ($m in @($Contract.modes)) {
        if ([string]$m.mode -eq 'Apply') { $apply = $m; break }
    }
    $applyOk = ($null -ne $apply -and $apply.requires_authorization -eq $true -and $apply.requires_guardian_allow -eq $true)
    Add-Check -List $checks -Name 'apply-requires-auth-and-guardian' -Passed:([bool]$applyOk)

    $pipeline = @($Contract.pipeline | ForEach-Object { [string]$_ })
    $needStages = @('preview_diff', 'check_CAS_preconditions', 'check_budget', 'check_guardian_gate', 'maker_checker_second_actor_if_required', 'append_immutable_receipt')
    $pipeOk = $true
    foreach ($st in $needStages) {
        if ($pipeline -notcontains $st) { $pipeOk = $false }
    }
    Add-Check -List $checks -Name 'pipeline-has-cas-budget-guardian-maker-receipt' -Passed:([bool]$pipeOk)

    $casPolicy = [string](Get-Optional $Contract.cas 'policy')
    $casSnap = Get-Optional $Contract.cas 'pre_state_snapshot_required'
    $casOk = ($casPolicy -eq 'block-on-different-existing-value' -and $casSnap -eq $true)
    Add-Check -List $checks -Name 'cas-block-on-conflict' -Passed:([bool]$casOk)

    $budget = Get-Optional $Contract.budget 'default_v1'
    $maxTargets = 0
    $maxWrites = 0
    if ($null -ne $budget) {
        $maxTargets = [int](Get-Optional $budget 'max_targets' 0)
        $maxWrites = [int](Get-Optional $budget 'max_writes_per_run' 0)
    }
    $budgetOk = ($null -ne $budget -and $maxTargets -gt 0 -and $maxWrites -gt 0)
    Add-Check -List $checks -Name 'budget-defaults-present' -Passed:([bool]$budgetOk)

    $mc = $Contract.maker_checker
    $maker = [string](Get-Optional $mc 'maker_role')
    $checker = [string](Get-Optional $mc 'checker_role')
    $rule = [string](Get-Optional $mc 'rule')
    $mcOk = (-not [string]::IsNullOrWhiteSpace($maker) -and -not [string]::IsNullOrWhiteSpace($checker) -and $rule.Length -gt 10)
    Add-Check -List $checks -Name 'maker-checker-dual-control' -Passed:([bool]$mcOk)

    $seed = @()
    if ($null -ne $Contract.whitelist) {
        $seed = @($Contract.whitelist.seed_adapters)
    }
    $pilotSeedOk = $false
    foreach ($s in $seed) {
        if ([string]$s.write_adapter_id -eq 'write.beads-projection-metadata' -and [string]$s.status -eq 'pilot-exists') {
            $pilotSeedOk = $true
        }
    }
    Add-Check -List $checks -Name 'seed-includes-beads-pilot' -Passed:([bool]$pilotSeedOk)

    $pilotExists = Test-Path -LiteralPath $PilotPath -PathType Leaf
    Add-Check -List $checks -Name 'pilot-script-exists' -Passed:([bool]$pilotExists) -Detail $PilotPath

    if ($pilotExists) {
        $src = Get-Content -Raw -Encoding UTF8 -LiteralPath $PilotPath
        $modeOk = ($src -match "ValidateSet\('Preview', 'Apply', 'Rollback'\)") -or ($src -match "Mode = 'Preview'")
        Add-Check -List $checks -Name 'pilot-has-preview-apply-rollback' -Passed:([bool]$modeOk)
        $gOk = ($src -match 'Get-GuardianGate|GuardianAdapterPath')
        Add-Check -List $checks -Name 'pilot-has-guardian-gate' -Passed:([bool]$gOk)
        $cOk = ($src -match 'block-on-different-existing-value|conflict')
        Add-Check -List $checks -Name 'pilot-has-conflict-block' -Passed:([bool]$cOk)
    }

    $boundaryHit = 0
    foreach ($b in @($Contract.hard_boundaries)) {
        if ([string]$b -match 'cross-manager|independent write') { $boundaryHit++ }
    }
    Add-Check -List $checks -Name 'no-cross-manager-oneshot' -Passed:($boundaryHit -ge 1)

    $failedCount = 0
    foreach ($c in $checks) {
        if (-not $c.passed) { $failedCount++ }
    }

    $runtimeEnforcement = $false
    if ($null -ne $Contract.runtime_enforcement) {
        $runtimeEnforcement = [System.Convert]::ToBoolean($Contract.runtime_enforcement)
    }

    return [pscustomobject]@{
        verifier = 'Test-ControlledWriteContract.ps1'
        contract_id = [string]$Contract.contract_id
        read_only = $true
        runtime_enforcement = $runtimeEnforcement
        passed = ($failedCount -eq 0)
        check_count = $checks.Count
        failed_count = $failedCount
        checks = @($checks.ToArray())
        mutations_performed = $false
        note = 'Design scaffold validation only; does not enable Apply surfaces'
    }
}

if (-not (Test-Path -LiteralPath $ContractPath -PathType Leaf)) {
    throw "Contract missing: $ContractPath"
}
$contract = Get-Content -Raw -Encoding UTF8 -LiteralPath $ContractPath | ConvertFrom-Json
$result = Test-Contract -Contract $contract -PilotPath $PilotScriptPath

if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $dir = Split-Path -Parent $OutputPath
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    if (Test-Path -LiteralPath $OutputPath) { throw "Refusing to overwrite: $OutputPath" }
    [System.IO.File]::WriteAllText($OutputPath, ($result | ConvertTo-Json -Depth 12), [System.Text.UTF8Encoding]::new($false))
}

$result | ConvertTo-Json -Depth 12
if (-not $result.passed) { exit 1 }
exit 0
