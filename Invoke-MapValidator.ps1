<#
.SYNOPSIS
  codex-temr.5 read-only global-map validator (first source: Beads).
.DESCRIPTION
  Consumes the accepted core contract (v1.1-core JSON projection) and a Beads
  JSONL export, maps issues+dependencies+labels into the core entity/edge model,
  runs the contract gates, and emits a report-only findings file.

  READ-ONLY / REPORT-ONLY. Does not mutate Beads, config, credentials, Desktop,
  schedulers, or production data. No network. No raw secrets.
.PARAMETER ContractPath
  Path to core-contract-v1.1.json.
.PARAMETER BeadsJsonl
  Path to a `bd export` JSONL file (caller produces it read-only).
.PARAMETER OutDir
  Directory for the JSON + markdown report.
.NOTES
  PowerShell 5.1 compatible. Contract gate 12: first validator is read-only,
  produces reports only.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $ContractPath,
    [Parameter(Mandatory = $true)] [string] $BeadsJsonl,
    [Parameter(Mandatory = $true)] [string] $OutDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-JsonFile {
    param([string] $Path)
    # utf-8-sig safe: strip BOM if present
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ($raw.Length -gt 0 -and $raw[0] -eq [char]0xFEFF) { $raw = $raw.Substring(1) }
    return $raw | ConvertFrom-Json
}

if (-not (Test-Path -LiteralPath $ContractPath)) { throw "Contract not found: $ContractPath" }
if (-not (Test-Path -LiteralPath $BeadsJsonl))   { throw "Beads export not found: $BeadsJsonl" }
if (-not (Test-Path -LiteralPath $OutDir))       { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$contract = Read-JsonFile -Path $ContractPath
$nowUtc   = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')

# --- Ingest Beads JSONL (one issue per line) ---
$issues = New-Object System.Collections.ArrayList
foreach ($line in [System.IO.File]::ReadLines((Resolve-Path -LiteralPath $BeadsJsonl))) {
    $t = $line.Trim()
    if ([string]::IsNullOrWhiteSpace($t)) { continue }
    [void]$issues.Add(($t | ConvertFrom-Json))
}

$statusAllow = @('open', 'in_progress', 'blocked', 'closed', 'deferred')
$edgeTypeAllow = @{}
foreach ($p in $contract.beadsAdapter.edgeTypeMap.PSObject.Properties) { $edgeTypeAllow[$p.Name] = $p.Value }

# index for dangling-edge checks
$idSet = @{}
foreach ($i in $issues) { $idSet[$i.id] = $true }

$findings = New-Object System.Collections.ArrayList
function Add-Finding {
    param([string] $Gate, [string] $Severity, [string] $Subject, [string] $Detail)
    [void]$findings.Add([PSCustomObject]@{ gate = $Gate; severity = $Severity; subject = $Subject; detail = $Detail })
}

# --- G01 id-unique-prefixed ---
$dupIds = $issues | Group-Object id | Where-Object { $_.Count -gt 1 }
foreach ($d in $dupIds) { Add-Finding 'G01' 'error' $d.Name "duplicate issue id x$($d.Count)" }
$badPrefix = $issues | Where-Object { $_.id -notmatch '^codex-' }
foreach ($b in $badPrefix) { Add-Finding 'G01' 'error' $b.id 'id lacks codex- prefix' }

# --- G02/G03 edges: type allowlist + dangling ---
$edgeCount = 0
foreach ($i in $issues) {
    if (-not ($i.PSObject.Properties.Name -contains 'dependencies')) { continue }
    if ($null -eq $i.dependencies) { continue }
    foreach ($e in $i.dependencies) {
        $edgeCount++
        if (-not $edgeTypeAllow.ContainsKey($e.type)) {
            Add-Finding 'G02' 'error' $e.issue_id "unknown edge type '$($e.type)' -> $($e.depends_on_id)"
        }
        if (-not $idSet.ContainsKey($e.depends_on_id)) {
            Add-Finding 'G03' 'error' $e.issue_id "dangling edge: depends_on '$($e.depends_on_id)' not found"
        }
        if (-not $idSet.ContainsKey($e.issue_id)) {
            Add-Finding 'G03' 'error' $e.issue_id "dangling edge: source '$($e.issue_id)' not found"
        }
    }
}

# --- G04 classification-coverage (active tasks) + G05 namespace drift ---
$canonicalPillars = @('pillar:agent-workbench', 'pillar:business-loop', 'pillar:data-assets', 'pillar:overseas-infra')
$active = $issues | Where-Object { $_.status -in @('open', 'in_progress', 'blocked') }
$unclassified = 0
foreach ($i in $active) {
    $labels = @()
    if ($i.PSObject.Properties.Name -contains 'labels' -and $i.labels) { $labels = @($i.labels) }
    $pillarLabels = $labels | Where-Object { $_ -like 'pillar:*' }
    if (-not $pillarLabels) {
        $unclassified++
        Add-Finding 'G04' 'warn' $i.id 'active task has no pillar: label (unclassified)'
    }
    foreach ($pl in $pillarLabels) {
        if ($canonicalPillars -notcontains $pl) {
            Add-Finding 'G05' 'warn' $i.id "non-canonical pillar label '$pl'"
        }
    }
}

# --- G06 status-valid ---
foreach ($i in $issues) {
    if ($statusAllow -notcontains $i.status) {
        Add-Finding 'G06' 'error' $i.id "invalid status '$($i.status)'"
    }
}

# --- G07 count-provenance (build the counts with source/method/scope) ---
$counts = [PSCustomObject]@{
    total_issues     = $issues.Count
    active_issues    = $active.Count
    edges_total      = $edgeCount
    unclassified_active = $unclassified
    source           = 'bd export (JSONL)'
    method           = 'line-count / group-object, no grep'
    observed_at      = $nowUtc
    scope            = 'all exported issues (default export scope, memories excluded)'
    state_plane      = 'declared'
}

# --- G08 state-plane-honesty: assert we never emitted runtime_proof for Beads ---
Add-Finding 'G08' 'info' 'beads' 'all Beads-derived facts fixed to declared plane; no runtime_proof promotion'

# --- Summarize + write reports ---
$errCount  = @($findings | Where-Object { $_.severity -eq 'error' }).Count
$warnCount = @($findings | Where-Object { $_.severity -eq 'warn' }).Count
$report = [PSCustomObject]@{
    validator       = 'codex-temr.5 map-validator'
    contract        = $contract.contractId
    contractVersion = $contract.contractVersion
    beadsExport     = (Resolve-Path -LiteralPath $BeadsJsonl).Path
    observed_at     = $nowUtc
    readOnly        = $true
    counts          = $counts
    findingSummary  = [PSCustomObject]@{ error = $errCount; warn = $warnCount; total = $findings.Count }
    findings        = $findings
}

$jsonOut = Join-Path $OutDir 'validator-report.json'
$mdOut   = Join-Path $OutDir 'validator-report.md'
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $jsonOut -Encoding UTF8

$md = New-Object System.Text.StringBuilder
[void]$md.AppendLine('# codex-temr.5 Map Validator Report (Beads source, read-only)')
[void]$md.AppendLine('')
[void]$md.AppendLine("- observed_at: $nowUtc")
[void]$md.AppendLine("- contract: $($contract.contractId) $($contract.contractVersion)")
[void]$md.AppendLine("- total_issues: $($counts.total_issues) | active: $($counts.active_issues) | edges: $($counts.edges_total)")
[void]$md.AppendLine("- findings: error=$errCount warn=$warnCount total=$($findings.Count)")
[void]$md.AppendLine('- read_only: true; no mutation, no network, no secrets.')
[void]$md.AppendLine('')
[void]$md.AppendLine('| gate | severity | subject | detail |')
[void]$md.AppendLine('|---|---|---|---|')
foreach ($f in $findings) {
    [void]$md.AppendLine("| $($f.gate) | $($f.severity) | $($f.subject) | $($f.detail) |")
}
$md.ToString() | Set-Content -LiteralPath $mdOut -Encoding UTF8

Write-Output "VALIDATOR OK: error=$errCount warn=$warnCount total=$($findings.Count)"
Write-Output "JSON: $jsonOut"
Write-Output "MD:   $mdOut"
