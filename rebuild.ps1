# rebuild.ps1 v2 - B1 standard rebuild chain (codex-ygjz P1-2/3/4: atomic lock, single generation, atomic publish)
# Usage:  powershell -File rebuild.ps1 [-Owner "name"] [-NoPublish] [-StaleMinutes 60]
# Lock:   catalog/.rebuild-lock.json via CreateNew (atomic), owner token verified on release; fresh lock by another owner -> abort.
# Input:  one beads export per run at catalog/generations/<gen>/ with generation.json (count + sha256); chain consumes only it.
# Output: map-build written to tmp, gates (4.5/5.5/5.6) run on tmp, atomic Move-Item into place; publish via same-volume tmp + atomic replace.
param(
  [string]$Owner = $env:USERNAME,
  [switch]$NoPublish,
  [int]$StaleMinutes = 60,
  [string]$SimulateFailureAt = ""  # test hook: throw at the named step (rollback tests; no production file sabotage needed)
)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$root = "F:\codex"
$cat = Join-Path $root "tools\agent-system-map\catalog"
$web = Join-Path $root "tools\agent-system-map\web"
$mapOut = Join-Path $cat "map-build.html"
$publishTarget = "C:\html\agent-global-map.html"
$lockPath = Join-Path $cat ".rebuild-lock.json"

function Resolve-Exe([string[]]$candidates) {
  foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
  $cmd = Get-Command $candidates[-1] -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  throw "exe not found: $($candidates -join ' | ')"
}
$PY = Resolve-Exe @("C:\Users\64998\.workbuddy\binaries\python\versions\3.13.12\python.exe", "python3", "python")
$NODE = Resolve-Exe @("C:\Users\64998\.workbuddy\binaries\node\versions\22.22.2\node.exe", "node")

# ---- P1-2: atomic coordination lock (module: rebuild-lock.ps1; testable against temp paths) ----
. (Join-Path $PSScriptRoot "rebuild-lock.ps1")
$myToken = [guid]::NewGuid().ToString("N")
$lockRes = Acquire-RebuildLock -LockPath $lockPath -Owner $Owner -Token $myToken -StaleMinutes $StaleMinutes
if (-not $lockRes.acquired) {
  Write-Host ("ABORT: rebuild lock not acquired ({0}). Wait for finish or stale ({1} min)." -f $lockRes.reason, $StaleMinutes)
  exit 2
}
Write-Host "lock acquired: $Owner (pid $PID, token $($myToken.Substring(0,8)))"

function Step([string]$name, [scriptblock]$fn) {
  Write-Host "== $name =="
  if ($SimulateFailureAt -eq $name) { throw "simulated failure at step: $name (test hook)" }
  & $fn
  if ($LASTEXITCODE -ne 0) { throw "step failed: $name (exit $LASTEXITCODE)" }
}

# generation retention (2026-07-30, codex-ygjz P2 follow): keep newest 10 + first-of-each-day within
# 30 days; prune the rest with a printed list. Runs only after a fully successful chain.
function Invoke-GenerationRetention {
  $groot = Join-Path $cat "generations"
  if (-not (Test-Path $groot)) { return }
  $dirs = @(Get-ChildItem $groot -Directory | Sort-Object Name)
  if ($dirs.Count -le 10) { Write-Host ("generation retention: " + $dirs.Count + " generations, nothing to prune"); return }
  $cutoff = [datetime]::UtcNow.AddDays(-30)
  $keep = @{}
  foreach ($d in ($dirs | Select-Object -Last 10)) { $keep[$d.Name] = $true }
  $seenDay = @{}
  foreach ($d in $dirs) {
    $dTime = [datetime]::MinValue
    [void][datetime]::TryParseExact($d.Name, "yyyyMMddTHHmmssZ", $null, [System.Globalization.DateTimeStyles]::AssumeUniversal, [ref]$dTime)
    $day = $d.Name.Substring(0, 8)
    if ($dTime -gt $cutoff -and -not $seenDay.ContainsKey($day)) { $seenDay[$day] = $true; $keep[$d.Name] = $true }
  }
  $pruned = @()
  foreach ($d in $dirs) {
    if ($keep.ContainsKey($d.Name)) { continue }
    Remove-Item $d.FullName -Recurse -Force
    $pruned += $d.Name
  }
  if ($pruned.Count) { Write-Host ("generation retention: pruned " + $pruned.Count + " -> " + ($pruned -join ", ")) }
  Write-Host ("generation retention: kept " + ($dirs.Count - $pruned.Count) + "/" + $dirs.Count)
}

# ---- P1-3: single generation input ----
$gen = [datetime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$genDir = Join-Path $cat (Join-Path "generations" $gen)
New-Item -ItemType Directory -Path $genDir -Force | Out-Null
$genExport = Join-Path $genDir "beads-export.jsonl"
$mapTmp = Join-Path $cat ("map-build.tmp-" + $gen + ".html")  # keep .html ext: Chrome renders file:// by extension
$rollbackDir = Join-Path $genDir "rollback"
New-Item -ItemType Directory -Path $rollbackDir -Force | Out-Null

# backup in-place catalog artifacts for chain-level rollback (P1-4: no half-new state on failure)
$protected = @(
  "beads-export.jsonl",
  "task-projection-v0.json",
  "unified-relation-registry-v0.json",
  "multi-source-snapshot-v2.json",
  "manual-registry-v0.json",
  "map-build.html"
)
foreach ($f in $protected) {
  $p = Join-Path $cat $f
  if (Test-Path $p) { Copy-Item $p (Join-Path $rollbackDir $f) -Force }
}

function Restore-Rollback {
  foreach ($f in $protected) {
    $bak = Join-Path $rollbackDir $f
    if (Test-Path $bak) { Copy-Item $bak (Join-Path $cat $f) -Force }
  }
  Write-Host "rollback restored: catalog artifacts back to pre-run state"
}

try {
  Push-Location $root
  $env:BEADS_EXPORT_JSONL = $genExport

  Step "1 beads export (generation $gen)" {
    & $PY -c "import subprocess,json,io,hashlib,datetime,os; r=subprocess.run([r'F:/codex/tools/beads/bd.exe','list','--all','--json'],capture_output=True); raw=r.stdout.decode('utf-8',errors='replace'); raw=raw[raw.index('['):]; items=json.loads(raw); items=items if isinstance(items,list) else items.get('issues',items); body=''.join(json.dumps(i,ensure_ascii=False)+'\n' for i in items); p=os.environ['BEADS_EXPORT_JSONL']; f=io.open(p,'wb'); f.write(body.encode('utf-8')); f.close(); rawb=io.open(p,'rb').read(); sha=hashlib.sha256(rawb).hexdigest(); man={'generation_id':os.path.basename(os.path.dirname(p)),'exported_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'count':len(items),'sha256':sha,'sha_mode':'raw-bytes'}; io.open(os.path.join(os.path.dirname(p),'generation.json'),'w',encoding='utf-8').write(json.dumps(man,ensure_ascii=False,indent=2)+'\n'); print('beads exported:',len(items),'sha256:',sha[:16])"
  }
  Step "1.5 verify generation hash" {
    & $PY -c "import json,io,hashlib,os; d=os.path.dirname(os.environ['BEADS_EXPORT_JSONL']); man=json.load(io.open(os.path.join(d,'generation.json'),encoding='utf-8')); rawb=io.open(os.environ['BEADS_EXPORT_JSONL'],'rb').read(); sha=hashlib.sha256(rawb).hexdigest(); assert sha==man['sha256'], 'generation hash mismatch'; print('generation verified:', man['generation_id'], man['count'], 'items (raw-bytes sha)')"
  }
  Step "2 task projection"        { & $PY (Join-Path $root "tools\agent-system-map\build_task_projection.py") | Out-Host }
  Step "2.5 manual registry"      { & $PY (Join-Path $root "tools\agent-system-map\augment_manual_registry.py") | Out-Host }
  Step "2.7 unified relations"    { & $PY (Join-Path $root "tools\agent-system-map\build_unified_relation_registry.py") | Out-Host }
  Step "3 asset audit"            { & $PY (Join-Path $root "tools\agent-system-map\audit_webpage_asset_registry.py") | Out-Host }
  Step "3.5 gate source closure"  { & $PY (Join-Path $root "tools\agent-system-map\validate_source_closure.py") | Out-Host }
  Step "4 snapshot"               { & $PY (Join-Path $root "tools\agent-system-map\build_multi_source_snapshot.py") | Out-Host }
  Step "4.5 gate relation-samples" { & $PY (Join-Path $root "tools\agent-system-map\validate_relation_samples.py") | Out-Host }
  Step "5 build website (tmp)" {
    & $PY (Join-Path $web "build_map_website.py") (Join-Path $cat "source-registry.json") (Join-Path $cat "multi-source-snapshot-v2.json") $genExport $mapTmp | Out-Host
  }
  Step "5.5 gate manual pointers" { & $PY (Join-Path $root "tools\agent-system-map\manual_pointers_resolve.py") | Out-Host }
  Step "5.6 gate render test"     { & $NODE (Join-Path $web "render_test_gate.js") $mapTmp | Out-Host }
  Pop-Location

  # P1-4: atomic move into place (same volume rename) after all gates passed
  Move-Item $mapTmp $mapOut -Force
  Write-Host "map-build atomically replaced"

  if (-not $NoPublish) {
    $bak = "$publishTarget.bak-$(Get-Date -Format 'yyyyMMdd-HHmm')"
    Copy-Item $publishTarget $bak -ErrorAction SilentlyContinue
    $pubTmp = "C:\html\agent-global-map.tmp-$gen.html"
    Copy-Item $mapOut $pubTmp -Force
    Move-Item $pubTmp $publishTarget -Force
    Write-Host "PUBLISHED -> $publishTarget (atomic; backup: $bak)"
  } else {
    Write-Host "NoPublish: build kept at $mapOut"
  }
  Write-Host "REBUILD OK (owner=$Owner, generation=$gen)"
  Invoke-GenerationRetention
  # E-136 P2-6: archive old .bak files (failure must not fail rebuild)
  try {
    & $PY (Join-Path $root "tools\agent-system-map\archive_bak_files.py") | Out-Host
  } catch {
    Write-Warning "archive_bak_files warning: $($_.Exception.Message)"
  }
} catch {
  Write-Host "REBUILD FAILED: $_"
  Restore-Rollback
  Remove-Item $mapTmp -ErrorAction SilentlyContinue
  exit 1
} finally {
  $rel = Release-RebuildLock -LockPath $lockPath -Token $myToken
  Write-Host ("lock release: " + $rel.reason)
  Remove-Item Env:\BEADS_EXPORT_JSONL -ErrorAction SilentlyContinue
}
