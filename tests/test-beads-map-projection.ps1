$ErrorActionPreference = 'Stop'

$sync = Join-Path $PSScriptRoot '..\Sync-BeadsMapProjection.ps1'

$selfTest = & $sync -SelfTest | Out-String | ConvertFrom-Json
if (-not $selfTest.passed) { throw 'projection self-test failed' }

$preview = & $sync -Mode Preview | Out-String | ConvertFrom-Json
if (-not $preview.read_only -or $preview.target_count -ne 6 -or $preview.conflict_count -ne 0 -or $preview.writes_performed -ne 0) {
    throw 'projection preview contract failed'
}

$metadataReady = (($preview.apply_count -eq 6 -and $preview.noop_count -eq 0) -or
    ($preview.apply_count -eq 0 -and $preview.noop_count -eq 6))
if (-not $metadataReady) { throw 'projection preview metadata state is not clean' }

# Guardian is intentionally deny-only until independent health evidence exists.
# The preview must reflect that gate rather than treating a clean metadata plan
# as permission to write.
$guardianAllowed = [bool]$preview.guardian.allowed
$expectedCanApply = ($preview.conflict_count -eq 0 -and $preview.authorization_active -and $guardianAllowed)
if ([bool]$preview.can_apply -ne $expectedCanApply) {
    throw 'projection preview Guardian gate contract failed'
}

# A clean authorized pilot is either ready for its first six metadata writes or
# fully converged after those writes.  Mixed states are not a valid baseline.
$state = if ($preview.apply_count -eq 6 -and $preview.noop_count -eq 0) {
    'ready-to-apply'
}
elseif ($preview.apply_count -eq 0 -and $preview.noop_count -eq 6) {
    'fully-converged'
}
else {
    throw 'projection preview is neither a clean pre-apply nor a fully converged state'
}

$rollbackDryRunPassed = $null
if ($state -eq 'fully-converged') {
    $receiptDirectory = [string]$preview.operations[0].before_metadata.map_projection_receipt
    if ([string]::IsNullOrWhiteSpace($receiptDirectory) -or -not (Test-Path -LiteralPath $receiptDirectory)) {
        throw 'converged projection has no reachable receipt directory'
    }
    $successfulReceipt = Get-ChildItem -LiteralPath $receiptDirectory -Filter '*.json' |
        Where-Object { $_.Name -notlike '*-prestate.json' } |
        ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json } |
        Where-Object { $_.result -eq 'applied' } |
        Select-Object -Last 1
    if ($null -eq $successfulReceipt) { throw 'converged projection has no successful apply receipt' }
    $receiptPath = Join-Path $receiptDirectory (($successfulReceipt.receipt_id -replace ':', '-') + '.json')
    $rollback = & $sync -Mode Rollback -ReceiptPath $receiptPath -DryRun | Out-String | ConvertFrom-Json
    if ($rollback.writes_performed -ne 0 -or @($rollback.operations).Count -ne 6 -or @($rollback.operations | Where-Object operation -ne 'restore').Count -ne 0) {
        throw 'projection rollback dry-run contract failed'
    }
    $rollbackDryRunPassed = $true
}

[pscustomobject]@{
    verifier = 'test-beads-map-projection.ps1'
    read_only = $true
    self_tests = @($selfTest.self_tests).Count
    target_count = $preview.target_count
    state = $state
    apply_count = $preview.apply_count
    noop_count = $preview.noop_count
    guardian_allowed = $guardianAllowed
    guardian_reason = [string]$preview.guardian.reason
    can_apply = [bool]$preview.can_apply
    rollback_dry_run_passed = $rollbackDryRunPassed
    conflict_count = $preview.conflict_count
    passed = $true
} | ConvertTo-Json -Compress
