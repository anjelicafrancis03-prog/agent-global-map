# test-apply-gate.ps1 (codex-ygjz P1-1)
# Integration test: gate returns can_apply=false => Sync -Mode Apply must throw AT THE GATE
# and never reach a bd metadata write (proof: no *-prestate.json in the temp receipt dir).
# Uses a real beads fixture issue (no map_* metadata => operation='apply') + strict fixtures.
param()
$ErrorActionPreference = "Stop"
$ROOT = "F:\codex"
$SYNC = "$ROOT\tools\agent-system-map\Sync-BeadsMapProjection.ps1"
$MAP = "$ROOT\docs\architecture\global-map-v1.json"
$TMP = Join-Path $env:TEMP ("apply-gate-test-" + (Get-Date -Format "yyyyMMddHHmmss"))
New-Item -ItemType Directory -Path $TMP | Out-Null
$KEYS = @('map_node_id','map_subnode','map_path','map_projection_id','map_projection_version','map_projection_registry','map_projection_authorization','map_projection_receipt')

# 1) fixture issue (created 2026-07-29; keep zero map_* metadata so operation=apply)
$issueId = "codex-v6tr"

# 2) a real map node id + map fixture aligned to Sync's 8-key contract
# (real global-map-v1.json metadata_fields lacks map_subnode -> cross-config drift, codex-ygjz finding;
#  test patches the fixture map so the gate path is exercised, drift tracked separately)
$map = Get-Content $MAP -Raw -Encoding UTF8 | ConvertFrom-Json
$nodeId = 'map.guardian'
foreach ($p in @($map.projection_registry)) { if ($p.projection_id -eq 'projection.beads-task-tree') { $p.metadata_fields = $KEYS } }
$mapFixturePath = Join-Path $TMP 'map.json'
[System.IO.File]::WriteAllText($mapFixturePath, ($map | ConvertTo-Json -Depth 32), [System.Text.UTF8Encoding]::new($false))

# 3) projection fixture (strict contract)
$proj = [ordered]@{
  registry_id = 'beads-task-projections-v1'; schema_version = '1.0.0'
  status = 'authorized-write-pilot'; projection_id = 'projection.beads-task-tree'; projection_version = '1'
  central_authority = 'authority.global-map-governance'; beads_lifecycle_authority = 'authority.beads'
  receipt_directory = 'reports/run-manifests/fixture-apply-gate'
  write_boundary = [ordered]@{ metadata_only = $true; preserves_beads_lifecycle_fields = $true; default_mode = 'preview'; conflict_policy = 'block-on-different-existing-value' }
  metadata_contract = [ordered]@{}
  projections = @([ordered]@{
    projection_record_id = 'fixture-apply-gate-1'; beads_issue_id = $issueId
    relation_type = 'task-classified-under'; status = 'approved-for-write'
    map_node_id = $nodeId; map_path = ("map.root-governance/" + $nodeId)
    evidence_refs = @('receipt:fixture-apply-gate')
  })
}
foreach ($k in $KEYS) { $proj.metadata_contract[$k] = [ordered]@{ type = 'string'; required = $false } }
$projPath = Join-Path $TMP 'projection.json'
[System.IO.File]::WriteAllText($projPath, ($proj | ConvertTo-Json -Depth 12), [System.Text.UTF8Encoding]::new($false))

# 4) authorization fixture (approved, unexpired)
$auth = [ordered]@{
  authorization_id = 'fixture-apply-gate-auth'; status = 'approved'; action = 'beads-projection-metadata-write'
  issued_by = 'agent:workbuddy-test'; issued_at = (Get-Date).AddDays(-1).ToUniversalTime().ToString('o')
  expires_at = (Get-Date).AddDays(1).ToUniversalTime().ToString('o')
  scope = [ordered]@{ allowed_metadata_keys = $KEYS; rollback_authorized = $true; target_issue_ids = @($issueId) }
}
$authPath = Join-Path $TMP 'authorization.json'
[System.IO.File]::WriteAllText($authPath, ($auth | ConvertTo-Json -Depth 12), [System.Text.UTF8Encoding]::new($false))

# 5) run Apply; expect gate throw (child inherits CWD; bd locates .beads via CWD => run from F:\codex)
Push-Location $ROOT
$out = ""
try {
  $ErrorActionPreference = "Continue"  # child exits non-zero by design (gate throw); capture, do not terminate
  $out = powershell -NoProfile -ExecutionPolicy Bypass -File $SYNC -Mode Apply -ProjectionPath $projPath -MapPath $mapFixturePath -AuthorizationPath $authPath -ReceiptDirectory $TMP -BdCommand "$ROOT\tools\beads\bd.exe" 2>&1 | Out-String
} finally { Pop-Location; $ErrorActionPreference = "Stop" }
$gateReceipts = @(Get-ChildItem $TMP -Filter '*-controlled-write-gate.json' -ErrorAction SilentlyContinue)
$prestates = @(Get-ChildItem $TMP -Filter '*-prestate.json' -ErrorAction SilentlyContinue)
$blocked = ($out -match 'can_apply=False') -or ($out -match 'can_apply=false')
$gateRan = ($gateReceipts.Count -ge 1)
$noWrite = ($prestates.Count -eq 0)
Write-Host ("fixture-issue: " + $issueId + "  map-node: " + $nodeId)
Write-Host ("gate-blocked-with-can_apply : " + $blocked)
Write-Host ("gate-receipt-written        : " + $gateRan)
Write-Host ("no-prestate (no bd update)  : " + $noWrite)
if ($blocked -and $gateRan -and $noWrite) { Write-Host "TEST PASS: can_apply=false hard-blocks Apply before any bd update"; exit 0 }
Write-Host "TEST FAIL"; Write-Host $out.Substring(0, [Math]::Min(800, $out.Length)); exit 1
