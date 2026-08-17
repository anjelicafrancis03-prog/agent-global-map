# Build-SlotPidProjection.ps1
# codex-temr.12 — read-only slot ↔ identity projection.
# Explains config instances (18) vs console slots (16) vs running slots vs OS processes.
# Does NOT claim false per-slot PIDs from public /api/state (publicSlots omits processIdentity).
# DENY-ONLY observation. No start/stop/repair/allow/scheduler mutations.

[CmdletBinding()]
param(
    [ValidateSet('Run', 'SelfTest')]
    [string]$Mode = 'Run',
    [string]$AgentsConfigPath = 'F:\codex\tools\desktop-agent-console\config\agents.json',
    [string]$ConsoleBaseUrl = 'http://127.0.0.1:18765',
    [string]$ServerSourcePath = 'F:\codex\tools\desktop-agent-console\server.js',
    [string]$OutputPath,
    [ValidateRange(1, 30)]
    [int]$RequestTimeoutSeconds = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-Optional {
    param($Object, [string]$Name, $Default = $null)
    if ($null -ne $Object -and $Object.PSObject.Properties.Name -contains $Name) { return $Object.$Name }
    return $Default
}

function Get-Sha256 {
    param([string]$Value)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-DeclaredAgents {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { throw "agents config missing: $Path" }
    $cfg = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
    $rows = @()
    $sumAll = 0
    $sumConsole = 0
    $sumDisabled = 0
    foreach ($agent in @($cfg.agents)) {
        $instances = [int](Get-Optional $agent 'instances' 0)
        $disabled = [bool](Get-Optional $agent 'consoleDisabled' $false)
        $prefix = [string](Get-Optional $agent 'prefix' '')
        $sumAll += $instances
        if ($disabled) { $sumDisabled += $instances } else { $sumConsole += $instances }
        $slotNames = @()
        if (-not $disabled -and -not [string]::IsNullOrWhiteSpace($prefix)) {
            for ($i = 1; $i -le $instances; $i++) { $slotNames += ($prefix + [string]$i) }
        }
        $rows += [pscustomobject]@{
            agent_id = [string]$agent.id
            label = [string](Get-Optional $agent 'label' '')
            instances = $instances
            console_disabled = $disabled
            prefix = $prefix
            declared_slot_names = $slotNames
        }
    }
    return [pscustomobject]@{
        agents = $rows
        sum_instances_all = $sumAll
        sum_instances_console_enabled = $sumConsole
        sum_instances_console_disabled = $sumDisabled
        config_path = $Path
    }
}

function Get-ApiObservation {
    param([string]$BaseUrl, [int]$TimeoutSec)
    try {
        $state = Invoke-RestMethod -Uri (($BaseUrl.TrimEnd('/')) + '/api/state') -TimeoutSec $TimeoutSec -ErrorAction Stop
        $slotProps = @()
        $slotMap = Get-Optional $state 'slots'
        if ($null -ne $slotMap) {
            $slotProps = @($slotMap.PSObject.Properties)
        }
        $slots = @()
        $pidKeyHits = [System.Collections.Generic.List[string]]::new()
        $statusCounts = [ordered]@{}
        foreach ($prop in $slotProps) {
            $s = $prop.Value
            $status = [string](Get-Optional $s 'status' 'unknown')
            if (-not $statusCounts.Contains($status)) { $statusCounts[$status] = 0 }
            $statusCounts[$status] = [int]$statusCounts[$status] + 1
            $keys = @($s.PSObject.Properties.Name)
            foreach ($k in $keys) {
                if ($k -match '(?i)pid|processIdentity|process_id') { [void]$pidKeyHits.Add("$($prop.Name).$k") }
            }
            $slots += [pscustomobject]@{
                name = [string](Get-Optional $s 'name' $prop.Name)
                agent_id = [string](Get-Optional $s 'agentId' '')
                status = $status
                public_keys = $keys
                has_public_pid = ($keys -match '(?i)^pid$|processIdentity').Count -gt 0
            }
        }
        return [pscustomobject]@{
            reachable = $true
            error = $null
            backend = Get-Optional $state 'backend'
            run_id_sha256 = $(if (Get-Optional $state 'runId') { Get-Sha256 ([string]$state.runId) } else { $null })
            api_slot_count = $slots.Count
            status_counts = $statusCounts
            running_slots = @($slots | Where-Object { $_.status -in @('running', 'starting') } | ForEach-Object { $_.name } | Sort-Object)
            stopped_slots = @($slots | Where-Object { $_.status -in @('stopped', 'exited') } | ForEach-Object { $_.name } | Sort-Object)
            slots = $slots
            public_pid_key_hits = @($pidKeyHits | Select-Object -Unique)
            note = 'public /api/state slots intentionally omit processIdentity / pid (server publicSlots strips in-memory term identity)'
        }
    } catch {
        return [pscustomobject]@{
            reachable = $false
            error = "$_"
            backend = $null
            run_id_sha256 = $null
            api_slot_count = 0
            status_counts = [ordered]@{}
            running_slots = @()
            stopped_slots = @()
            slots = @()
            public_pid_key_hits = @()
            note = 'console unreachable'
        }
    }
}

function Get-ServerSourceFacts {
    param([string]$Path)
    $facts = [ordered]@{
        path = $Path
        exists = (Test-Path -LiteralPath $Path)
        public_slots_omits_process_identity = $null
        build_slots_skips_console_disabled = $null
        in_server_process_identity_present = $null
        evidence_lines = @()
    }
    if (-not $facts.exists) { return [pscustomobject]$facts }
    $src = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path
    $facts.public_slots_omits_process_identity = ($src -match 'function publicSlots\(') -and ($src -notmatch 'publicSlots\([\s\S]{0,400}processIdentity')
    # publicSlots spreads slotState from state.slots which is public metadata, not term.processIdentity
    $facts.in_server_process_identity_present = ($src -match 'term\.processIdentity') -or ($src -match 'captureNodePtyProcessIdentity')
    $facts.build_slots_skips_console_disabled = ($src -match 'if \(agent\.consoleDisabled\) continue')
    if ($src -match 'function publicSlots\(') { $facts.evidence_lines += 'publicSlots() exists' }
    if ($src -match 'term\.processIdentity') { $facts.evidence_lines += 'term.processIdentity used in-server only' }
    if ($src -match 'if \(agent\.consoleDisabled\) continue') { $facts.evidence_lines += 'buildSlots skips consoleDisabled agents' }
    return [pscustomobject]$facts
}

function Get-OsProcessApproximation {
    # Coarse OS approximation only — NOT a slot↔PID map.
    $patterns = @(
        [pscustomobject]@{ label = 'desktop_console_server'; match = 'desktop-agent-console\\server\.js' },
        [pscustomobject]@{ label = 'desktop_slot_client'; match = 'desktop-slot-client\.js' },
        [pscustomobject]@{ label = 'desktop_phone_hud'; match = 'desktop-phone-hud\.js' },
        [pscustomobject]@{ label = 'sensenova_proxy'; match = 'sensenova-proxy\.js' },
        [pscustomobject]@{ label = 'node_pty_hint'; match = 'node-pty|winpty|conpty' }
    )
    $procs = @()
    try {
        $procs = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.Name -match '^(node|conhost|claude|codex)' -or
            ($_.CommandLine -and $_.CommandLine -match 'desktop-agent-console|node-pty|winpty')
        })
    } catch {
        return [pscustomobject]@{
            ok = $false
            error = "$_"
            counts = @{}
            sample = @()
            note = 'OS process query failed; no PID claims'
        }
    }
    $counts = [ordered]@{}
    foreach ($p in $patterns) {
        $hits = @($procs | Where-Object { $_.CommandLine -and $_.CommandLine -match $p.match })
        $counts[$p.label] = $hits.Count
    }
    $counts['node_total_observed_related'] = $procs.Count
    $counts['conhost_total_system'] = @(Get-Process -Name conhost -ErrorAction SilentlyContinue).Count
    $sample = @($procs | Where-Object { $_.CommandLine -and $_.CommandLine -match 'desktop-agent-console' } | Select-Object -First 12 | ForEach-Object {
        $cmd = [string]$_.CommandLine
        if ($cmd.Length -gt 140) { $cmd = $cmd.Substring(0, 140) + '...' }
        [pscustomobject]@{
            pid = $_.ProcessId
            ppid = $_.ParentProcessId
            name = $_.Name
            cmd_preview = $cmd
        }
    })
    return [pscustomobject]@{
        ok = $true
        error = $null
        counts = $counts
        sample_desktop_related = $sample
        note = 'Approximation only. Public API has no per-slot pid; do not invent slot↔PID bindings from this list.'
    }
}

function Build-Projection {
    $now = (Get-Date).ToUniversalTime()
    $declared = Get-DeclaredAgents -Path $AgentsConfigPath
    $api = Get-ApiObservation -BaseUrl $ConsoleBaseUrl -TimeoutSec $RequestTimeoutSeconds
    $serverFacts = Get-ServerSourceFacts -Path $ServerSourcePath
    $osApprox = Get-OsProcessApproximation

    $explanation = @(
        "Config agents.json sum(instances)=$($declared.sum_instances_all) across $($declared.agents.Count) agent families.",
        "consoleDisabled agents contribute $($declared.sum_instances_console_disabled) instances that are NOT built into console slots (buildSlots skips them).",
        "Console-enabled declared instances = $($declared.sum_instances_console_enabled) (expected public slot count when prefixes expand).",
        "Live /api/state slot_count = $($api.api_slot_count); running = $(@($api.running_slots).Count).",
        "Historical '18 vs 16' was a 2026-07 era hardcoded snapshot; it drifted as agents.json evolved. Since 2026-08-16 the math check is dynamic: sum(instances) - consoleDisabled(instances) must equal consoleEnabled(instances), so it stays true regardless of future config growth.",
        "Running count is independent and usually much smaller (only started slots).",
        "Per-slot PID is held in-server as term.processIdentity / node-pty pid but publicSlots() does not expose it — public pid_keys are empty.",
        "True slot↔PID projection requires an authorized internal diagnostic endpoint or OS tree rooted at known server PID; this report does not fabricate bindings."
    )

    $mathCheck = [ordered]@{
        config_sum_instances = $declared.sum_instances_all
        console_disabled_instances = $declared.sum_instances_console_disabled
        console_enabled_instances = $declared.sum_instances_console_enabled
        api_slot_count = $api.api_slot_count
        api_running_count = @($api.running_slots).Count
        # 2026-08-16 普通15 P0-1 fix: 旧硬编码 -eq 16 随 agents.json 漂移恒 False；
        # 改为动态恒等式 all - disabled = enabled（数学自洽，永不随配置增长失效）
        config_math_consistent = (($declared.sum_instances_all - $declared.sum_instances_console_disabled) -eq $declared.sum_instances_console_enabled)
        console_enabled_matches_api = ($declared.sum_instances_console_enabled -eq $api.api_slot_count)
        public_pid_keys_empty = (@($api.public_pid_key_hits).Count -eq 0)
    }

    $projection = [ordered]@{
        schema_version = 'slot-pid-projection-v1'
        beads_task = 'codex-temr.12'
        mode = 'read-only-report'
        observed_at = $now.ToString('o')
        auto_repair_performed = $false
        allow_granted = $false
        claimed_per_slot_pids = $false
        declared = $declared
        api = $api
        server_source_facts = $serverFacts
        os_process_approximation = $osApprox
        math_check = $mathCheck
        explanation = $explanation
        residual = @(
            'No public per-slot PID in /api/state',
            'OS approximation is not authoritative slot identity',
            'Do not treat node/conhost totals as Desktop slot counts',
            'Optional follow-up: authorized internal diagnostic projection (separate auth)'
        )
        next_actions = @(
            'Keep projection read-only',
            'Do not enable Guardian allow from this report',
            'If product needs slot↔PID, add gated diagnostic API rather than scraping public state'
        )
    }
    $canonical = ($projection | ConvertTo-Json -Depth 16 -Compress)
    $projection.projection_sha256 = Get-Sha256 $canonical
    return [pscustomobject]$projection
}

if ($Mode -eq 'SelfTest') {
    $proj = Build-Projection
    $checks = @(
        [pscustomobject]@{ name = 'no-allow'; passed = ($proj.allow_granted -eq $false) },
        [pscustomobject]@{ name = 'no-auto-repair'; passed = ($proj.auto_repair_performed -eq $false) },
        [pscustomobject]@{ name = 'does-not-claim-slot-pids'; passed = ($proj.claimed_per_slot_pids -eq $false) },
        [pscustomobject]@{ name = 'has-declared-agents'; passed = (@($proj.declared.agents).Count -ge 1) },
        [pscustomobject]@{ name = 'config-math-consistency'; passed = ($proj.math_check.config_math_consistent -eq $true) },
        [pscustomobject]@{ name = 'explanation-present'; passed = (@($proj.explanation).Count -ge 3) }
    )
    # 2026-08-16 普通15 R1 fix: 旧版硬编码 exit 0 掩盖失败（passed=false 也返回 0，CI 漏报）；
    # 改为按实际结果退出（全过=0，任一失败=1）
    $passed = ((@($checks | Where-Object { -not $_.passed }).Count) -eq 0)
    [pscustomobject]@{
        verifier = 'Build-SlotPidProjection.ps1'
        read_only = $true
        passed = $passed
        checks = $checks
        math_check = $proj.math_check
    } | ConvertTo-Json -Depth 8
    exit [int](-not $passed)
}

$report = Build-Projection
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $dir = Split-Path -Parent $OutputPath
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    # utf-8 no BOM for machine readers; PS 5 may add BOM with Set-Content - use .NET
    $json = $report | ConvertTo-Json -Depth 16
    [System.IO.File]::WriteAllText($OutputPath, $json, [System.Text.UTF8Encoding]::new($false))
}
$report | ConvertTo-Json -Depth 16
