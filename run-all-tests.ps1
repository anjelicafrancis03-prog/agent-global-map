# run-all-tests.ps1 - unified regression entry for the agent-system-map toolchain (codex-ygjz-follow ③)
# Runs every gate/test suite and aggregates PASS/FAIL. Read-checks + controlled-write integration tests
# (apply-gate/lock/partial-write run rebuild -NoPublish under the coordination lock; they write only
# catalog generations + rollback-protected artifacts, never publish).
# Exit 0 = all pass. Usage: powershell -File run-all-tests.ps1 [-SkipHeavy]
param([switch]$SkipHeavy)
$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"
$root = "F:\codex"
$map = Join-Path $root "tools\agent-system-map"
$PY = "C:\Users\64998\.workbuddy\binaries\python\versions\3.13.12\python.exe"
$results = New-Object System.Collections.Generic.List[object]

function Run-Test([string]$name, [scriptblock]$fn) {
  $global:LASTEXITCODE = 0
  $out = & $fn 2>&1 | Out-String
  $code = $LASTEXITCODE
  $pass = ($code -eq 0) -or ($out -match "TEST PASS" -and $out -notmatch "TEST FAIL")
  $results.Add([pscustomobject]@{ name = $name; pass = $pass; exit = $code; tail = ($out -split "`n" | Select-Object -Last 2) -join " | " })
  Write-Host (("{0} {1} (exit {2})") -f ($(if ($pass) { "PASS" } else { "FAIL" }), $name, $code))
}

Push-Location $root
try {
  Run-Test "closure-gate (validate_source_closure.py)" { & $PY (Join-Path $map "validate_source_closure.py") }
  Run-Test "map-integrity (7d boundary+freshness+generation+secrets)" { & $PY (Join-Path $map "test_map_integrity.py") }
  Run-Test "agent-registration gate" { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "tools\agent-hub\validate-agent-registration.ps1") }
  Run-Test "sync selftest" { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $map "Sync-BeadsMapProjection.ps1") -SelfTest }
  # 2026-08-16 普通15 补强：①SlotPidProjection SelfTest 纳入统一门（此前缺席，P0-1 假断言无人把关）；
  # ②sync 真实 Preview 对账（fixture SelfTest 绿 ≠ 真实键集/授权健康）。
  # 注：Preview 因 nav-bind 数据冲突 exit 1 属已知业务状态（conflict_count>0，两投影体系归属待 root 裁定），
  # 本门只拦截 "projection validation failed"（契约/授权错误），业务冲突不算门失败。
  Run-Test "slot-pid-projection selftest (config-math-consistency)" { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $map "Build-SlotPidProjection.ps1") -Mode SelfTest }
  $previewOut = powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $map "Sync-BeadsMapProjection.ps1") -Mode Preview 2>&1 | Out-String
  $previewPass = ($previewOut -notmatch "projection validation failed")
  $results.Add([pscustomobject]@{ name = "sync real preview (contract key alignment)"; pass = $previewPass; exit = 0; tail = ($previewOut -split "`n" | Select-Object -Last 2) -join " | " })
  Write-Host (("{0} {1} (validation-only gate)") -f ($(if ($previewPass) { "PASS" } else { "FAIL" }), "sync real preview (contract key alignment)"))
  if (-not $SkipHeavy) {
    Run-Test "apply-gate integration" { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $map "test-apply-gate.ps1") }
    Run-Test "lock scenarios" { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $map "test-lock-scenarios.ps1") }
    Run-Test "partial-write rollback" { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $map "test-partial-write-rollback.ps1") }
  }
} finally { Pop-Location }

$failed = @($results | Where-Object { -not $_.pass })
Write-Host ("==== run-all-tests: {0}/{1} pass ====" -f ($results.Count - $failed.Count), $results.Count)
if ($failed.Count) { $failed | ForEach-Object { Write-Host ("FAILED: " + $_.name + " -- " + $_.tail) }; [Environment]::Exit(1) }
[Environment]::Exit(0)
