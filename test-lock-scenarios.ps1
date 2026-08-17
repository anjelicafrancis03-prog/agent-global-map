# test-lock-scenarios.ps1 (codex-ygjz 复验⑥: FULLY ISOLATED — tests the lock MODULE against temp paths.
# Never touches production .rebuild-lock.json, never runs rebuild, never plants locks in catalog.)
$ErrorActionPreference = "Stop"
. "F:\codex\tools\agent-system-map\rebuild-lock.ps1"
$tmp = Join-Path $env:TEMP ("lock-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
$lockPath = Join-Path $tmp ".rebuild-lock.json"
$prodLock = "F:\codex\tools\agent-system-map\catalog\.rebuild-lock.json"
$prodExistedBefore = Test-Path $prodLock
$now = [datetime]::UtcNow.ToString("o")
$old = [datetime]::UtcNow.AddHours(-2).ToString("o")
$fails = @()

function Plant([string]$owner, [int]$ownerPid, [string]$time, [string]$token) {
  [System.IO.File]::WriteAllText($lockPath, (@{ owner = $owner; pid = $ownerPid; time = $time; token = $token } | ConvertTo-Json -Compress), [System.Text.UTF8Encoding]::new($false))
}

# case 1: fresh foreign lock -> not acquired (locked-by-other)
Plant "someone-else" 99999 $now "t1"
$r1 = Acquire-RebuildLock -LockPath $lockPath -Owner "WorkBuddy" -Token "mine1" -StaleMinutes 60
$c1 = (-not $r1.acquired) -and ($r1.reason -eq "locked-by-other:someone-else")
Write-Host ("case1 foreign-fresh-blocked: " + $c1); if (-not $c1) { $fails += "case1" }

# case 2: fresh same-owner lock with ALIVE pid (this script) -> not acquired (concurrent)
# case 2: fresh same-owner lock with an ALIVE pid that is NOT this process -> not acquired
$dummy = Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile -Command Start-Sleep -Seconds 30" -PassThru -WindowStyle Hidden
try {
  Plant "WorkBuddy" $dummy.Id $now "t2"
  $r2 = Acquire-RebuildLock -LockPath $lockPath -Owner "WorkBuddy" -Token "mine2" -StaleMinutes 60
  $c2 = (-not $r2.acquired) -and ($r2.reason -like "concurrent-same-owner:*")
} finally { Stop-Process -Id $dummy.Id -Force -ErrorAction SilentlyContinue }
Write-Host ("case2 sameowner-alivepid-blocked: " + $c2); if (-not $c2) { $fails += "case2" }

# case 3: stale foreign lock -> takeover, then release with wrong token leaves it, right token removes
Plant "someone-else" 99999 $old "t3"
$r3 = Acquire-RebuildLock -LockPath $lockPath -Owner "WorkBuddy" -Token "mine3" -StaleMinutes 60
$c3 = $r3.acquired
Write-Host ("case3 stale-takeover: " + $c3); if (-not $c3) { $fails += "case3" }
$relWrong = Release-RebuildLock -LockPath $lockPath -Token "not-mine"
$c4 = (-not $relWrong.released) -and (Test-Path $lockPath)
Write-Host ("case4 wrong-token-release-kept: " + $c4); if (-not $c4) { $fails += "case4" }
$relRight = Release-RebuildLock -LockPath $lockPath -Token "mine3"
$c5 = $relRight.released -and (-not (Test-Path $lockPath))
Write-Host ("case5 right-token-release-removed: " + $c5); if (-not $c5) { $fails += "case5" }

# production lock must be untouched
$prodExistedAfter = Test-Path $prodLock
$c6 = ($prodExistedBefore -eq $prodExistedAfter)
Write-Host ("case6 production-lock-untouched: " + $c6); if (-not $c6) { $fails += "case6" }

Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
if ($fails.Count) { Write-Host ("LOCK TESTS FAIL: " + ($fails -join ",")); exit 1 }
Write-Host "LOCK TESTS ALL PASS (isolated)"; exit 0
