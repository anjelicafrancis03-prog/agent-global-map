# test-partial-write-rollback.ps1 (codex-ygjz 复验⑥: isolated sabotage via -SimulateFailureAt hook.
# NO production file hiding, NO lock planting. Asserts: rebuild fails at the simulated step,
# rollback restores ALL protected catalog files, production gate/lock state untouched.)
$ErrorActionPreference = "Stop"
$cat = "F:\codex\tools\agent-system-map\catalog"
$gate = "F:\codex\tools\agent-system-map\web\render_test_gate.js"
$prodLock = Join-Path $cat ".rebuild-lock.json"
$protected = @("beads-export.jsonl","task-projection-v0.json","unified-relation-registry-v0.json","multi-source-snapshot-v2.json","manual-registry-v0.json","map-build.html")

function Get-Sha256([string]$filePath) {
  if (-not (Test-Path $filePath)) { return $null }
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $stream = [System.IO.File]::OpenRead($filePath)
  try {
    $bytes = $sha.ComputeHash($stream)
    return [BitConverter]::ToString($bytes) -replace '-'
  } finally {
    $stream.Close()
  }
}

function Hash-All {
  $h = @{}
  foreach ($f in $protected) { $p = Join-Path $cat $f; if (Test-Path $p) { $h[$f] = (Get-Sha256 $p) } }
  return $h
}

$before = Hash-All
$gateHashBefore = (Get-Sha256 $gate)
$out = ""
try {
  $ErrorActionPreference = "Continue"  # child fails by design; capture, do not terminate
  $out = powershell -NoProfile -ExecutionPolicy Bypass -File "F:\codex\tools\agent-system-map\rebuild.ps1" -Owner "WorkBuddy-rollback-test" -NoPublish -SimulateFailureAt "5.6 gate render test" 2>&1 | Out-String
} finally { $ErrorActionPreference = "Stop" }
$after = Hash-All

$failed = ($out -match "REBUILD FAILED") -and ($out -match "simulated failure at step")
$restoredMsg = ($out -match "rollback restored")
$same = $true
foreach ($f in $protected) {
  if ($before[$f] -and $after[$f] -and $before[$f] -ne $after[$f]) { $same = $false; Write-Host ("DIFF: " + $f) }
  if (($before[$f] -and -not $after[$f]) -or (-not $before[$f] -and $after[$f])) { $same = $false; Write-Host ("EXISTENCE-DIFF: " + $f) }
}
$gateSame = ((Get-Sha256 $gate) -eq $gateHashBefore)
$noLock = (-not (Test-Path $prodLock))
$noHiddenLeft = (-not (Test-Path "$gate.hidden-by-test"))
Write-Output ("rebuild-failed-at-simulated-step: " + $failed)
Write-Output ("rollback-message                 : " + $restoredMsg)
Write-Output ("protected-6-files-identical      : " + $same)
Write-Output ("production-gate-untouched        : " + $gateSame)
Write-Output ("no-lock-left                     : " + $noLock)
Write-Output ("no-sabotage-artifacts            : " + $noHiddenLeft)
if ($failed -and $restoredMsg -and $same -and $gateSame -and $noLock -and $noHiddenLeft) {
  Write-Output "PARTIAL-WRITE ROLLBACK TEST PASS (isolated)"
  [Environment]::Exit(0)
}
Write-Output "PARTIAL-WRITE ROLLBACK TEST FAIL"
[Environment]::Exit(1)
