# -*- coding: utf-8 -*-
# codex-temr.8 inc2 multi-source snapshot producer (read-only, registry-driven).
# Iterates source-registry.json ACTIVE file/dir sources and emits contract-conforming
# entities/edges with provenance. Supersedes the inc1 per-source PS adapters
# (Invoke-SourceAdapters.ps1) with a single pluggable Python producer.
#
# Security (contract gate 13): MCP/Provider sources emit only names/counts/enabled
# flags. Never read env/args/command VALUES that may carry credential references.
# Skill source emits only directory names.

import json, os, sys, glob, datetime, hashlib, re, subprocess, io

ROOT = r'F:\codex'
REG  = os.path.join(ROOT, r'tools\agent-system-map\catalog\source-registry.json')

def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def fresh_until(path, days=7):
    try:
        mt = os.path.getmtime(path)
        return (datetime.datetime.fromtimestamp(mt, datetime.timezone.utc)
                + datetime.timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    except OSError:
        return None

def load_json(path):
    # tolerate UTF-8 BOM (provider inventory has one)
    with open(path, 'r', encoding='utf-8-sig') as f:
        return json.load(f)

NOW = now_utc()
entities = []
edges = []
by_source = {}

def add_entity(**kw):
    entities.append(kw)
    by_source[kw['source']] = by_source.get(kw['source'], 0) + 1

def add_edge(**kw):
    edges.append(kw)

# ---- agent-hub: agent_family / agent_profile / runtime_slot ----
def src_agent_hub():
    p = os.path.join(ROOT, r'tools\agent-hub\agent-families.json')
    fu = fresh_until(p)
    d = load_json(p)
    # E-24 2026-08-03：slot-provider-bindings 的 seated_agent（实例粒度）透传进 runtime_slot
    seated_map = {}
    try:
        bindings = load_json(os.path.join(ROOT, r'tools\agent-hub\slot-provider-bindings.json'))
        for b in bindings.get('bindings', []):
            if b.get('slotId') and b.get('seated_agent'):
                seated_map[b['slotId'].lower()] = b['seated_agent']
    except Exception:
        pass
    seen_profiles = set()
    for fam in d['families']:
        fid = fam['familyId']
        name = fam.get('displayName', fid)
        # interface_kind by evidence: name contains CLI -> cli, else unspecified
        ik = 'cli' if 'CLI' in name.upper() or fam.get('managerTarget') in ('codex','claude','opencode') else 'unspecified'
        if 'CLI' not in name.upper() and fam.get('managerTarget') not in ('codex','claude','opencode'):
            ik = 'unspecified'
        add_entity(id=f'agent-family:{fid}', kind='agent_family', display_name=name,
                   interface_kind=ik, state='declared', state_plane='declared',
                   source='agent-hub', authority='Agent Hub / agent-families.json',
                   observed_at=NOW, fresh_until=fu, confidence='high',
                   source_refs=['tools/agent-hub/agent-families.json'])
        for pid in fam.get('profileIds', []):
            if pid not in seen_profiles:
                seen_profiles.add(pid)
                add_entity(id=f'agent-profile:{pid}', kind='agent_profile', display_name=pid,
                           state='declared', state_plane='declared', source='agent-hub',
                           authority='Agent Hub / agent-families.json', observed_at=NOW, fresh_until=fu,
                           confidence='high', source_refs=['tools/agent-hub/agent-families.json'])
            add_edge(kind='HAS_PROFILE', **{'from': f'agent-family:{fid}', 'to': f'agent-profile:{pid}'},
                     source='agent-hub')
        for slot in fam.get('desktopSlots', []):
            sid = f'slot:{slot.lower()}'
            add_entity(id=sid, kind='runtime_slot', display_name=slot,
                       state='declared', state_plane='declared', source='agent-hub',
                       authority='Agent Hub / agent-families.json', observed_at=NOW, fresh_until=fu,
                       confidence='high', source_refs=['tools/agent-hub/agent-families.json'],
                       seated_agent=seated_map.get(sid, None),
                       note='declared slot; observed(Desktop)=21, MiMo disabled diff not merged')
            add_edge(kind='DECLARES_SLOT', **{'from': f'agent-family:{fid}', 'to': sid}, source='agent-hub')

# ---- okf: manual ----
def src_okf():
    base = os.path.join(ROOT, 'okf-bundles')
    for d in sorted(glob.glob(os.path.join(base, '*'))):
        if not os.path.isdir(d):
            continue
        idx = os.path.join(d, 'index.md')
        if not os.path.exists(idx):
            continue
        name = os.path.basename(d)
        add_entity(id=f'okf:{name}', kind='manual', display_name=name,
                   state='declared', state_plane='declared', source='okf',
                   authority='OKF bundles', observed_at=NOW, fresh_until=fresh_until(idx),
                   confidence='high', source_refs=[f'okf-bundles/{name}/index.md'])

# ---- harness: module ----
def src_harness():
    base = os.path.join(ROOT, r'docs\harness-modules')
    for d in sorted(glob.glob(os.path.join(base, '*'))):
        card = os.path.join(d, 'module-card.md')
        if not os.path.exists(card):
            continue
        name = os.path.basename(d)
        add_entity(id=f'module:{name}', kind='module', display_name=name,
                   state='declared', state_plane='declared', source='harness',
                   authority='Harness module index', observed_at=NOW, fresh_until=fresh_until(card),
                   confidence='high', source_refs=[f'docs/harness-modules/{name}/module-card.md'])

# ---- mcp-manager: capability(kind=mcp) + module ----
def src_mcp():
    p = os.path.join(ROOT, r'tools\mcp_manager\registry.json')
    fu = fresh_until(p)
    d = load_json(p)
    raw_ok = d.get('policy', {}).get('rawSecretsAllowed', None)
    # module entity (MANAGED_BY target)
    add_entity(id='module:mcp-manager', kind='module', display_name='MCP Manager',
               state='active', state_plane='managed', source='mcp-manager',
               authority='MCP Manager (tools/mcp_manager/registry.json)', observed_at=NOW,
               fresh_until=fu, confidence='high', source_refs=['tools/mcp_manager/registry.json'],
               note=f'rawSecretsAllowed={raw_ok}; write authority independent')
    # SECURITY: only server KEY names + enabled flag; never env/args/command values
    for skey, sval in d.get('servers', {}).items():
        enabled_for = []
        status = None
        if isinstance(sval, dict):
            enabled_for = sval.get('enabled_for', []) or []  # codex-ygjz v5.5 §三-⑥：逐客户端矩阵（仅客户端名，无 env）
            status = sval.get('status', None)  # codex-j38e lifecycle: pilot|managed
        cid = f'capability:mcp:{skey}'
        add_entity(id=cid, kind='capability', capability_kind='mcp', display_name=skey,
                   status=status,
                   state=('enabled' if enabled_for else 'registered'), state_plane='managed',
                   source='mcp-manager', authority='MCP Manager', observed_at=NOW, fresh_until=fu,
                   confidence='high', source_refs=['tools/mcp_manager/registry.json'],
                   enabled_for_clients=sorted(enabled_for),
                   note='name/enabled_for client names only; no env/args/command value read (gate13)')
        add_edge(kind='MANAGED_BY', **{'from': cid, 'to': 'module:mcp-manager'}, source='mcp-manager')

# ---- skill-manager: capability(kind=skill) + module ----
def src_skill():
    base = os.path.expanduser(r'~\.agents\skills')
    add_entity(id='module:skill-manager', kind='module', display_name='Skill Manager',
               state='active', state_plane='managed', source='skill-manager',
               authority='Skill Manager (tools/skill_link_manager)', observed_at=NOW,
               fresh_until=fresh_until(base), confidence='high',
               source_refs=['tools/skill_link_manager/skill_link_manager.py'],
               note='central skill library; write authority independent from MCP')
    if os.path.isdir(base):
        for d in sorted(glob.glob(os.path.join(base, '*'))):
            if not os.path.isdir(d):
                continue
            name = os.path.basename(d)
            cid = f'capability:skill:{name}'
            # codex-3gwc: read lifecycle status from SKILL.md frontmatter (test|formal)
            skill_status = None
            md = os.path.join(d, 'SKILL.md')
            if os.path.exists(md):
                try:
                    head = open(md, encoding='utf-8', errors='replace').read(1500)
                    m = re.search(r'^status:\s*(\w+)', head, re.M)
                    skill_status = m.group(1) if m else None
                except OSError:
                    pass
            add_entity(id=cid, kind='capability', capability_kind='skill', display_name=name,
                       status=skill_status, map_node_id='map.skill-manager', map_subnode='catalog',
                       state='registered', state_plane='managed', source='skill-manager',
                       authority='Skill Manager', observed_at=NOW, fresh_until=fresh_until(base),
                       confidence='high', source_refs=['~/.agents/skills/'+name],
                       note='dir name only')
            add_edge(kind='MANAGED_BY', **{'from': cid, 'to': 'module:skill-manager'}, source='skill-manager')

# ---- apikey-provider-manager: capability(kind=provider) + module ----
def src_provider():
    p = os.path.join(ROOT, r'tools\agent-hub\provider-routes.json')
    fu = fresh_until(p)
    d = load_json(p)
    add_entity(id='module:apikey-provider-manager', kind='module', display_name='APIkey/Provider Manager',
               state='active', state_plane='managed', source='apikey-provider-manager',
               authority='APIkey/Provider Manager (provider-routes.json); provider==api key',
               observed_at=NOW, fresh_until=fu, confidence='high',
               source_refs=['tools/agent-hub/provider-routes.json'],
               note='sanitized pointer only; no raw secret (gate13)')
    for i, route in enumerate(d.get('routes', [])):
        rid = route.get('routeId') or route.get('id') or route.get('name') or f'route-{i}'
        cid = f'capability:provider:{rid}'
        add_entity(id=cid, kind='capability', capability_kind='provider', display_name=str(rid),
                   state='registered', state_plane='managed', source='apikey-provider-manager',
                   authority='APIkey/Provider Manager', observed_at=NOW, fresh_until=fu,
                   confidence='high', source_refs=['tools/agent-hub/provider-routes.json'],
                   fronted_by_agent=route.get('fronted_by_agent'),
                   note='route id only; no credential value')
        add_edge(kind='MANAGED_BY', **{'from': cid, 'to': 'module:apikey-provider-manager'},
                 source='apikey-provider-manager')

# ---- relation-registry: task/asset/system entities + relation edges ----
# codex-69lp Step5 adapter (read-only). Consumes the Step 0-4 drafts produced under
# reports/run-manifests/2026-07-27-codex-69lp-step0-task-projection/.
def src_relations():
    base = os.path.join(ROOT, r'tools\agent-system-map\catalog')  # 固化(2026-07-28)：投影/注册表迁稳定 catalog，由 build_task_projection.py / augment_manual_registry.py 生成
    tp = load_json(os.path.join(base, 'task-projection-v0.json'))
    ar = load_json(os.path.join(base, 'webpage-asset-registry-v0.json'))
    rs = load_json(os.path.join(base, 'relation-samples-v0.json'))
    fu = fresh_until(os.path.join(base, 'unified-relation-registry-v0.json'))
    for e in tp.get('entities', []):
        add_entity(id=e['id'], kind=e['kind'], display_name=e.get('display_name'),
                   status=e.get('status'), map_node_id=e.get('map_node_id'),
                   node_derivation=e.get('node_derivation'), lanes=e.get('lanes'),
                   path=e.get('path'), url_local=e.get('url_local'),
                   state='declared', state_plane='projected', source='relation-registry',
                   authority='codex-69lp task-projection-v0', observed_at=NOW, fresh_until=fu,
                   confidence='high' if e.get('node_derivation') == 'metadata-binding' else 'medium',
                   source_refs=['reports/run-manifests/2026-07-27-codex-69lp-step0-task-projection/task-projection-v0.json'])
    for a in ar.get('assets', []):
        card = a.get('card') or {}
        add_entity(id=a['asset_id'], kind='asset',
                   display_name=card.get('display_name') or a['asset_id'].split(':')[-1],
                   asset_type=a.get('type'), url=a.get('url'), local_path=a.get('local_path'),
                   dashboard_path=a.get('dashboard_path'),
                   owner_beads=a.get('owner_beads'), related_beads=a.get('related_beads'),
                   map_node_id=a.get('map_node_id'), map_subnode=a.get('map_subnode'),
                   panel=a.get('panel'), subgroup=a.get('subgroup'), card=card or None,
                   lifecycle=a.get('lifecycle'), map_number=a.get('map_number'),
                   data_asset_class=a.get('data_asset_class'),
                   state='declared', state_plane='projected', source='relation-registry',
                   authority='codex-69lp webpage-asset-registry-v0', observed_at=NOW, fresh_until=fu,
                   confidence='high', source_refs=['reports/run-manifests/2026-07-27-codex-69lp-step0-task-projection/webpage-asset-registry-v0.json'])
    for e in rs.get('edges', []):
        add_edge(kind=e['relation'], **{'from': e['from'], 'to': e['to']},
                 derivation=e.get('derivation'), source='relation-registry')
    # manuals (codex-69lp manual-registry v0.1 calibrated): patch okf entities in-place
    # with manual_type/covers/map_node; emit docs/rag manual entities (new to snapshot).
    mreg = load_json(os.path.join(base, 'manual-registry-v0.json'))
    by_mid = {m['manual_id']: m for m in mreg.get('entries', []) if m.get('canonical')}
    for ent in entities:
        m = by_mid.get(ent.get('id'))
        if m:
            ent['manual_type'] = m['manual_type']
            ent['manual_map_node'] = m['map_node_id']
            ent['covers_object'] = m.get('covers_object')
            ent['map_number'] = m.get('map_number')
    for m in mreg.get('entries', []):
        if not m.get('canonical') or m['manual_id'].startswith('okf:'):
            continue  # okf 实体已由 src_okf 发出，只在上面打补丁
        add_entity(id=m['manual_id'], kind='manual', display_name=m.get('title'),
                   manual_type=m['manual_type'], form=m['form'], path=m['path'],
                   manual_map_node=m['map_node_id'], covers_object=m.get('covers_object'), map_number=m.get('map_number'),
                   state='declared', state_plane='projected', source='relation-registry',
                   authority='codex-69lp manual-registry-v0.1', observed_at=NOW, fresh_until=fu,
                   confidence='medium', source_refs=['reports/run-manifests/2026-07-27-codex-69lp-step0-task-projection/manual-registry-v0.json'])
    # manual-covers edges: harness module coverage (curated map) + name-matched objects
    existing_ids = {e.get('id') for e in entities}
    for mod, mids in (mreg.get('harness_module_manual_map') or {}).items():
        tgt = f'module:{mod}'
        if tgt not in existing_ids:
            continue
        for mid in mids:
            if mid in existing_ids:
                add_edge(kind='manual-covers', **{'from': mid, 'to': tgt}, source='relation-registry')
    for m in mreg.get('entries', []):
        if not m.get('canonical'):
            continue
        obj = m.get('covers_object') or ''
        for cand in (f'capability:mcp:{obj}', f'capability:skill:{obj}',
                     f'asset:portal:{obj}', f'asset:local:{obj}', f'system:{obj}'):
            if cand in existing_ids and cand != m['manual_id']:
                add_edge(kind='manual-covers', **{'from': m['manual_id'], 'to': cand},
                         source='relation-registry')
    # L2 层级挂载（2026-07-29 用户拍板）：relation-samples placements 按 id 给实体打
    # map_node_id/map_subnode（手册/能力/资产均可；合适有的话就放）
    # codex-ygjz v5.5 §三-C：placements 权威已迁 entity-placements-v0.json；旧字段非空=双头写，直接 FAIL
    if rs.get('placements'):
        raise SystemExit('FAIL: relation-samples-v0.json.placements 非空（旧字段未退场，防双头写复活）')
    by_eid = {e.get('id'): e for e in entities}
    ep = load_json(os.path.join(ROOT, 'tools', 'agent-system-map', 'catalog', 'entity-placements-v0.json'))
    for pl in ep.get('entityPlacements', []):
        ent = by_eid.get(pl.get('entity_id'))
        if not ent:
            raise SystemExit('FAIL: entity-placements-v0.json 未知实体 ' + str(pl.get('entity_id')) + '（placements 零未知指向）')
        ent['map_panel'] = pl.get('panel_id')
        ent['map_subnode'] = pl.get('subnode_id')

# ---- onemcp-broker: module + capability(kind=mcp-shared-backend) ----
# admission:onemcp-broker:v1 (root-approved 2026-07-28). Loopback read-only observation
# via HTTP initialize + tools/list. Never read backend env/credentials; no live mutation.
def src_onemcp_broker():
    import urllib.request
    url = 'http://127.0.0.1:3009/mcp'
    state_plane = 'observed'
    refs = ['docs/architecture/source-admissions/PROPOSED-admission-onemcp-broker-v1.json']

    def rpc(rid, method, params):
        body = json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}).encode('utf-8')
        req = urllib.request.Request(url, data=body, headers={
            'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith('data:'):
                line = line[5:].strip()
            if line.startswith('{'):
                return json.loads(line)
        return None

    try:
        init = rpc(1, 'initialize', {'protocolVersion': '2025-03-26', 'capabilities': {},
                                     'clientInfo': {'name': 'map-snapshot', 'version': '0'}})
        tl = rpc(2, 'tools/list', {})
        tools = (tl or {}).get('result', {}).get('tools', []) or []
        health = 'connected' if init and init.get('result') else 'degraded'
    except Exception as e:
        add_entity(id='module:onemcp-broker', kind='module', display_name='OneMCP Broker (shared layer)',
                   state='unreachable', state_plane=state_plane, source='onemcp-broker',
                   authority='OneMCP broker http://127.0.0.1:3009/mcp (loopback, admission:onemcp-broker:v1)',
                   observed_at=NOW, fresh_until=NOW, confidence='medium', source_refs=refs,
                   note=f'initialize failed: {type(e).__name__}; treated as observed-unreachable, not global failure')
        return

    backends = {}
    for t in tools:
        name = t.get('name', '')
        if '__' in name:
            bname, tool = name.split('__', 1)
            backends.setdefault(bname, []).append(tool)

    add_entity(id='module:onemcp-broker', kind='module', display_name='OneMCP Broker (shared layer)',
               endpoint=url, health=health, backend_count=len(backends), tool_count=len(tools),
               state='running' if health == 'connected' else 'degraded', state_plane=state_plane,
               source='onemcp-broker',
               authority='OneMCP broker http://127.0.0.1:3009/mcp (loopback, admission:onemcp-broker:v1)',
               observed_at=NOW, fresh_until=NOW, confidence='high' if health == 'connected' else 'medium',
               source_refs=refs,
               note='read-only HTTP observation; runtime source, fresh per-build (no mtime reuse)')
    add_edge(kind='MANAGED_BY', **{'from': 'module:onemcp-broker', 'to': 'module:mcp-manager'},
             source='onemcp-broker')
    for bname, bl in sorted(backends.items()):
        cid = f'capability:mcp-shared-backend:{bname}'
        add_entity(id=cid, kind='capability', capability_kind='mcp-shared-backend', display_name=bname,
                   exposed_tools=bl, tool_count=len(bl),
                   state='exposed', state_plane=state_plane, source='onemcp-broker',
                   authority='OneMCP broker tools/list', observed_at=NOW, fresh_until=NOW,
                   confidence='high', source_refs=refs,
                   note='names only from tools/list; wildcard-deny allowlist enforced at broker')
        add_edge(kind='SHARED_VIA', **{'from': cid, 'to': 'module:onemcp-broker'}, source='onemcp-broker')

# ---- guardian-manager: module + capability(kind=guardian) + guardian:<task-name> ----
# admission:guardian-manager:v1 (root-approved 2026-07-31, AUTH-20260731-gm-v1).
# Single data source: Windows Task Scheduler service, enumerated via the
# Get-ScheduledTask cmdlet (its official read-only PowerShell view; same service
# data — NOT a parallel/independent source, no drift surface). Note: Schedule.Service
# COM binding on this host does not expose ITaskFolder.GetSubFolders (DISP_E_UNKNOWNNAME),
# so the cmdlet wrapper is used as the enumeration channel instead; gate13 unchanged:
# emit task metadata only (name/enabled/trigger_type/actual_state/last_run_result);
# never action args/command values, credentials, process memory, or raw XML bodies.
# Registration never implies schedule authorization.
def src_guardian():
    refs = ['docs/architecture/source-admissions/PROPOSED-admission-guardian-manager-v1.json']
    PS_ENUM = r'''
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$ErrorActionPreference='Stop'
$all = Get-ScheduledTask | ForEach-Object {
  $trigs = @()
  foreach($tr in $_.Triggers){ $trigs += [int]$tr.Type }
  [PSCustomObject]@{ Path=[string]$_.TaskPath; Name=[string]$_.TaskName; Enabled=[bool]$_.Settings.Enabled; State=[string]$_.State; LastTaskResult=[int]$_.LastTaskResult; Triggers=@($trigs) }
}
ConvertTo-Json -InputObject @($all) -Depth 5 -Compress
'''
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', PS_ENUM],
                           capture_output=True, timeout=120, text=True,
                           encoding='utf-8', errors='replace')
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[:300])
        data = json.loads(r.stdout.strip())
        if not isinstance(data, list):
            data = [data]
    except Exception as e:
        add_entity(id='module:guardian-manager', kind='module', display_name='Guardian Manager',
                   state='unreachable', state_plane='observed', source='guardian-manager',
                   authority='Guardian Manager (Task Scheduler service enumeration)',
                   observed_at=NOW, fresh_until=NOW, confidence='medium', source_refs=refs,
                   note=f'enumeration failed: {type(e).__name__}; treated as observed-unreachable (failure_semantics.missing/failed), not global failure')
        return

    sys.path.insert(0, os.path.join(ROOT, r'tools\guardian_manager'))
    from guardian_mapper import build_guardian_entity_from_com, is_system_task
    fresh = (datetime.datetime.now(datetime.timezone.utc)
             + datetime.timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')  # draft refresh_class: +7d

    add_entity(id='module:guardian-manager', kind='module', display_name='Guardian Manager',
               state='active', state_plane='managed', source='guardian-manager',
               authority='Guardian Manager (Schedule.Service COM enumeration; admission:guardian-manager:v1)',
               observed_at=NOW, fresh_until=NOW, confidence='high', source_refs=refs,
               note='registration/admission board for Windows scheduled-task guardians; write authority independent (root-approved 2026-07-31)')
    add_entity(id='capability:guardian', kind='capability', capability_kind='guardian',
               display_name='Windows Scheduled-Task Guardians', state='registered', state_plane='managed',
               source='guardian-manager', authority='Guardian Manager', observed_at=NOW, fresh_until=NOW,
               confidence='high', source_refs=refs,
               note='name/flag only (gate13); registration never implies schedule authorization')
    excluded = 0
    for t in data:
        if is_system_task(t.get('Path', '\\')):
            excluded += 1
            continue
        ent = build_guardian_entity_from_com(t, NOW, fresh_until=fresh)
        # E-121 (2026-08-13 魅惑批)：live 枚举实体降级 raw 层，渲染读 curated；raw 标记供对账
        ent['guardian_layer'] = 'raw'
        add_entity(**ent)
        add_edge(kind='MANAGED_BY', **{'from': ent['id'], 'to': 'module:guardian-manager'},
                 source='guardian-manager')
    # E-121：注入 curated 注册表实体（渲染权威层）。curated 语义不改（红线）。
    # coordinate 嵌套字段按 curated 原样保留；lifecycle 直接透传。
    _curated = None
    try:
        _curated = json.load(io.open(os.path.join(ROOT, r'tools\guardian\guardian-tasks-registry-v0.json'), encoding='utf-8'))
    except Exception:
        _curated = None
    if _curated:
        _by_name = {}
        for _e in entities:
            if _e.get('source') == 'guardian-manager':
                _by_name[_e.get('display_name')] = _e
        for ce in _curated.get('entities', []):
            _cid = 'guardian-curated:' + str(ce.get('id', ''))
            add_entity(
                id=_cid,
                kind='guardian',
                display_name=ce.get('name') or ce.get('id'),
                state=ce.get('lifecycle', 'unknown'),
                state_plane='curated',
                source='guardian-curated',
                authority='guardian-tasks-registry-v0 (curated, E-121 渲染权威)',
                observed_at=_curated.get('created_at') or NOW,
                fresh_until=fresh,
                confidence='high',
                source_refs=refs,
                map_node_id=(ce.get('coordinate') or {}).get('map_node_id'),
                guardian_layer='curated',
                lifecycle=ce.get('lifecycle'),
                evidence=ce.get('evidence'),
                guardian_target=ce.get('guardian_target'),
                stop_reason=ce.get('stop_reason'),
                owner=ce.get('owner'),
                note='curated entity (rendered authoritative; live raw 层降级供对账)',
            )
            add_edge(kind='CURATED_BY', **{'from': _cid, 'to': 'module:guardian-manager'},
                     source='guardian-curated')
    # surface enumeration coverage on the module entity (self-documenting, no extra entities)
    for e in entities:
        if e.get('id') == 'module:guardian-manager':
            e['enumerated_total'] = len(data)
            e['system_tasks_excluded'] = excluded

HANDLERS = {
    'agent-hub': src_agent_hub, 'okf': src_okf, 'harness': src_harness,
    'mcp-manager': src_mcp, 'skill-manager': src_skill, 'apikey-provider-manager': src_provider,
    'relation-registry': src_relations, 'onemcp-broker': src_onemcp_broker,
    'guardian-manager': src_guardian,
}

def main():
    reg = load_json(REG)
    active = [s['sourceId'] for s in reg['sources'] if s['status'] == 'active']
    ran = []
    for sid in active:
        h = HANDLERS.get(sid)
        if h:
            h(); ran.append(sid)
    # beads handled separately by validator (.5); note it as active-but-external here
    snapshot = {
        'snapshotId': 'snapshot:multi-source:v2',
        'contractRef': 'tools/agent-system-map/contract/core-contract-v1.1.json',
        'registryRef': 'tools/agent-system-map/catalog/source-registry.json',
        'generatedAt': NOW,
        'producer': 'build_multi_source_snapshot.py (inc2; supersedes Invoke-SourceAdapters.ps1)',
        'activeSourcesInRegistry': active,
        'sourcesRunThisPass': ran,
        'note': 'beads source consumed by validator (.5) separately; file/dir sources here.',
        'counts': {'entities': len(entities), 'edges': len(edges), 'by_source': by_source},
        'entities': entities,
        'edges': edges,
    }
    # 稳定无日期输出路径（2026-07-28 管线固化；旧 2026-07-17 目录仅作历史留证，不再写入）
    out = os.path.join(ROOT, r'tools\agent-system-map\catalog\multi-source-snapshot-v2.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print('SNAPSHOT v2 OK')
    print(f'  active sources: {active}')
    print(f'  ran this pass : {ran}')
    print(f'  entities={len(entities)} edges={len(edges)}')
    print(f'  by_source: {by_source}')
    print(f'  out: {out}')

if __name__ == '__main__':
    main()
