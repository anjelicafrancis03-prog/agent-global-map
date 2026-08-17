# Invoke-SourceAdapters.ps1
# Read-only multi-source adapters for the global system map (codex-temr.8).
# Reads active file-based authority sources and emits contract-conforming
# entity/edge records with provenance (observed_at / fresh_until / confidence).
# ASCII-only source (PS 5.1 reads this without a BOM; no multibyte comments).
# Report-only: no config/credential/Desktop/schedule mutation.

[CmdletBinding()]
param(
    [string] $ContractPath       = 'F:\codex\tools\agent-system-map\contract\core-contract-v1.1.json',
    [string] $RegistryPath       = 'F:\codex\tools\agent-system-map\catalog\source-registry.json',
    [string] $AgentFamiliesPath  = 'F:\codex\tools\agent-hub\agent-families.json',
    [string] $OkfRoot            = 'F:\codex\okf-bundles',
    [string] $HarnessRoot        = 'F:\codex\docs\harness-modules',
    [string] $OutDir             = 'F:\codex\reports\run-manifests\2026-07-17-codex-temr-8-source-adapters'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-JsonFile {
    param([string] $Path)
    $raw = [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath $Path))
    # strip UTF-8 BOM if present
    if ($raw.Length -gt 0 -and $raw[0] -eq [char]0xFEFF) { $raw = $raw.Substring(1) }
    return ($raw | ConvertFrom-Json)
}

if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$contract = Read-JsonFile -Path $ContractPath
$registry = Read-JsonFile -Path $RegistryPath

$nowUtc     = (Get-Date).ToUniversalTime()
$nowStr     = $nowUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')
$freshUntil = $nowUtc.AddDays(7).ToString('yyyy-MM-ddTHH:mm:ssZ')

$entities = New-Object System.Collections.ArrayList
$edges    = New-Object System.Collections.ArrayList
$bySource = @{}

function Add-Entity {
    param([string] $Id, [string] $Kind, [string] $Display, [string] $Source,
          [string] $Authority, [string] $StatePlane, [string] $Confidence, [hashtable] $Extra)
    $obj = [ordered]@{
        id           = $Id
        kind         = $Kind
        display_name = $Display
        state        = 'active'
        state_plane  = $StatePlane
        source       = $Source
        authority    = $Authority
        observed_at  = $nowStr
        fresh_until  = $freshUntil
        confidence   = $Confidence
    }
    if ($Extra) { foreach ($k in $Extra.Keys) { $obj[$k] = $Extra[$k] } }
    [void]$entities.Add([PSCustomObject]$obj)
    if (-not $bySource.ContainsKey($Source)) { $bySource[$Source] = 0 }
    $bySource[$Source]++
}

function Add-Edge {
    param([string] $Kind, [string] $From, [string] $To, [string] $Source, [string] $Authority)
    [void]$edges.Add([PSCustomObject]([ordered]@{
        kind        = $Kind
        from        = $From
        to          = $To
        source      = $Source
        authority   = $Authority
        observed_at = $nowStr
        fresh_until = $freshUntil
    }))
}

# ---------------------------------------------------------------------------
# Adapter A: Agent Hub (agent-families.json) -> agent_family / agent_profile / runtime_slot
# interface_kind derived from evidence: name contains 'cli' => cli, else unspecified.
# ---------------------------------------------------------------------------
$af = Read-JsonFile -Path $AgentFamiliesPath
foreach ($f in $af.families) {
    $famId = 'agent-family:' + $f.familyId
    $ifKind = 'unspecified'
    if (($f.familyId -match 'cli') -or ($f.displayName -match 'CLI')) { $ifKind = 'cli' }
    $slotCount = 0
    if ($f.PSObject.Properties.Name -contains 'desktopSlots' -and $f.desktopSlots) { $slotCount = @($f.desktopSlots).Count }
    Add-Entity -Id $famId -Kind 'agent_family' -Display $f.displayName -Source 'agent-hub' `
        -Authority 'Agent Hub' -StatePlane 'declared' -Confidence 'high' `
        -Extra @{ interface_kind = $ifKind; manager_target = ([string]$f.managerTarget); declared_slot_count = $slotCount }

    if ($f.PSObject.Properties.Name -contains 'profileIds' -and $f.profileIds) {
        foreach ($p in @($f.profileIds)) {
            $profId = 'agent-profile:' + $p
            Add-Entity -Id $profId -Kind 'agent_profile' -Display $p -Source 'agent-hub' `
                -Authority 'Agent Hub' -StatePlane 'declared' -Confidence 'high' -Extra $null
            Add-Edge -Kind 'HAS_PROFILE' -From $famId -To $profId -Source 'agent-hub' -Authority 'Agent Hub'
        }
    }
    if ($f.PSObject.Properties.Name -contains 'desktopSlots' -and $f.desktopSlots) {
        foreach ($s in @($f.desktopSlots)) {
            $slotId = 'slot:' + ($s.ToString().ToLower())
            Add-Entity -Id $slotId -Kind 'runtime_slot' -Display $s -Source 'agent-hub' `
                -Authority 'Agent Hub' -StatePlane 'declared' -Confidence 'high' -Extra $null
            Add-Edge -Kind 'DECLARES_SLOT' -From $famId -To $slotId -Source 'agent-hub' -Authority 'Agent Hub'
        }
    }
}

# ---------------------------------------------------------------------------
# Adapter B: OKF bundles -> manual (one per bundle directory that has index.md)
# ---------------------------------------------------------------------------
Get-ChildItem -LiteralPath $OkfRoot -Directory | ForEach-Object {
    $idx = Join-Path $_.FullName 'index.md'
    if (Test-Path -LiteralPath $idx) {
        $mid = 'okf:' + $_.Name
        Add-Entity -Id $mid -Kind 'manual' -Display $_.Name -Source 'okf' `
            -Authority 'OKF bundle index' -StatePlane 'declared' -Confidence 'high' -Extra $null
    }
}

# ---------------------------------------------------------------------------
# Adapter C: Harness modules -> module (one per module-card.md directory)
# ---------------------------------------------------------------------------
Get-ChildItem -LiteralPath $HarnessRoot -Directory | ForEach-Object {
    $card = Join-Path $_.FullName 'module-card.md'
    if (Test-Path -LiteralPath $card) {
        $mid = 'module:' + $_.Name
        Add-Entity -Id $mid -Kind 'module' -Display $_.Name -Source 'harness' `
            -Authority 'Harness module index' -StatePlane 'declared' -Confidence 'high' -Extra $null
    }
}

# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------
$snapshot = [ordered]@{
    snapshotId    = 'snapshot:multi-source:v1'
    contractRef   = 'tools/agent-system-map/contract/core-contract-v1.1.json'
    registryRef   = 'tools/agent-system-map/catalog/source-registry.json'
    generatedAt   = $nowStr
    activeSources = @('agent-hub', 'okf', 'harness')
    note          = 'Read-only multi-source snapshot. Beads is ingested by Invoke-MapValidator (codex-temr.5) and not duplicated here. Desktop-live excluded_until_repair; hardware declared-only; managers/communication/multi-device are pending sources.'
    counts        = [ordered]@{
        entities = $entities.Count
        edges    = $edges.Count
    }
    entities      = $entities
    edges         = $edges
}

$outFile = Join-Path $OutDir 'multi-source-snapshot.json'
$snapshot | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $outFile -Encoding UTF8

$pairs = $bySource.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }
Write-Output "ADAPTERS OK: entities=$($entities.Count) edges=$($edges.Count)"
Write-Output ("by_source: " + ($pairs -join ' '))
Write-Output ("JSON: " + $outFile)
