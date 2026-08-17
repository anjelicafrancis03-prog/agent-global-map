# rebuild-lock.ps1 - atomic coordination lock module (codex-ygjz: extracted from rebuild.ps1
# so lock behavior can be tested against TEMP paths without touching production lock/catalog).
# Dot-source this file, then call Acquire-RebuildLock / Release-RebuildLock.
$ErrorActionPreference = "Stop"

function Acquire-RebuildLock {
  param(
    [Parameter(Mandatory = $true)][string]$LockPath,
    [Parameter(Mandatory = $true)][string]$Owner,
    [Parameter(Mandatory = $true)][string]$Token,
    [int]$StaleMinutes = 60
  )
  # returns pscustomobject @{ acquired = bool; reason = string }
  # E-136 P1-4: 15 retries + stepped backoff (400ms + 200ms/attempt up to 1000ms, total ~13.8s)
  for ($attempt = 0; $attempt -lt 15; $attempt++) {
    try {
      $fs = [System.IO.File]::Open($LockPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
      try {
        $json = (@{ owner = $Owner; pid = $PID; time = ([datetime]::UtcNow.ToString("o")); token = $Token } | ConvertTo-Json -Compress)
        $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json)
        $fs.Write($bytes, 0, $bytes.Length)
      } finally { $fs.Close() }
      return [pscustomobject]@{ acquired = $true; reason = "acquired" }
    } catch [System.IO.IOException] {
      $sleepMs = [Math]::Min(400 + ($attempt * 200), 1000)
      $lock = $null
      try { $lock = [System.IO.File]::ReadAllText($LockPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json } catch {}
      if ($null -eq $lock) { Start-Sleep -Milliseconds $sleepMs; continue }
      $ageMin = ([datetime]::UtcNow - [datetime]::Parse($lock.time).ToUniversalTime()).TotalMinutes
      $lockPidAlive = $false
      try { $null = Get-Process -Id ([int]$lock.pid) -ErrorAction Stop; $lockPidAlive = $true } catch {}
      if ($ageMin -lt $StaleMinutes) {
        if ($lock.owner -ne $Owner) {
          return [pscustomobject]@{ acquired = $false; reason = ("locked-by-other:" + $lock.owner) }
        }
        if ($lockPidAlive -and [int]$lock.pid -ne $PID) {
          return [pscustomobject]@{ acquired = $false; reason = ("concurrent-same-owner:pid-" + $lock.pid) }
        }
      }
      # stale/dead-pid takeover: delete only if the lock is still the exact one evaluated (time+token match)
      $lockNow = $null
      try { $lockNow = [System.IO.File]::ReadAllText($LockPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json } catch {}
      if ($null -ne $lockNow -and $lockNow.time -eq $lock.time -and $lockNow.token -eq $lock.token) {
        Remove-Item $LockPath -Force -ErrorAction SilentlyContinue
      }
      Start-Sleep -Milliseconds $sleepMs
      # loop retries CreateNew
    }
  }
  return [pscustomobject]@{ acquired = $false; reason = "attempts-exhausted" }
}

function Release-RebuildLock {
  param(
    [Parameter(Mandatory = $true)][string]$LockPath,
    [Parameter(Mandatory = $true)][string]$Token
  )
  # release only our own lock (token match); never delete a third-party lock
  try {
    $lockNow = [System.IO.File]::ReadAllText($LockPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($lockNow.token -eq $Token) {
      Remove-Item $LockPath -Force
      return [pscustomobject]@{ released = $true; reason = "token-verified" }
    }
    return [pscustomobject]@{ released = $false; reason = "token-mismatch-left-in-place" }
  } catch { return [pscustomobject]@{ released = $false; reason = "already-gone" } }
}
