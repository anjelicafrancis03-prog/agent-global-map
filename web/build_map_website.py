# -*- coding: utf-8 -*-
"""Build the read-only Agent global system map website.

The map is a website-level topology projection.  It never writes to the
authoritative systems it displays.  Guardian is an internal, upstream health
gate: a task may be tracked in Beads only after the system is fit to execute.
"""

import html
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone


def read_json(path):
    with io.open(path, "r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def read_jsonl(path):
    rows = []
    with io.open(path, "r", encoding="utf-8-sig") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def iter_issue_dependencies(issue):
    """Yield normalized dependency edges from bd export or show-shaped records.

    Supported shapes:
    - export/jsonl: {"depends_on_id": "...", "type": "blocks"|"parent-child"|...}
    - nested show:  {"id": "...", "dependency_type": "..."}
    - string id only
    """
    raw = issue.get("dependencies") or []
    for dependency in raw:
        if isinstance(dependency, str):
            yield {"kind": "depends_on", "target": dependency, "direction": "depends_on"}
            continue
        if not isinstance(dependency, dict):
            continue
        target = (
            dependency.get("depends_on_id")
            or dependency.get("id")
            or dependency.get("target")
            or ""
        )
        kind = (
            dependency.get("type")
            or dependency.get("dependency_type")
            or dependency.get("kind")
            or "depends_on"
        )
        if not target:
            continue
        yield {"kind": kind, "target": target, "direction": "depends_on"}


def iter_issue_dependents(issue):
    """Yield reverse edges when export includes dependents[] (show-shaped)."""
    raw = issue.get("dependents") or []
    for dependent in raw:
        if isinstance(dependent, str):
            yield {"kind": "depended_on_by", "target": dependent, "direction": "depended_on_by"}
            continue
        if not isinstance(dependent, dict):
            continue
        target = dependent.get("id") or dependent.get("issue_id") or dependent.get("target") or ""
        kind = dependent.get("dependency_type") or dependent.get("type") or "depended_on_by"
        if target:
            yield {"kind": kind, "target": target, "direction": "depended_on_by"}


def read_view_projection(registry_path, registry):
    """Load the website's read-only, extensible view projection."""
    reference = registry.get("viewProjectionManifest")
    if not reference:
        return {}
    path = reference if os.path.isabs(reference) else os.path.join(os.path.dirname(registry_path), reference)
    return read_json(path)


def read_panel_manifest():
    """Load panel structure from the single authority (E-26 批次1).

    Returns the manifest dict; callers must tolerate absence (fallback: []).
    """
    manifest_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "catalog", "panel-manifest-v0.json"
    )
    try:
        return read_json(manifest_path)
    except (IOError, ValueError):
        return {}


_PANEL_MANIFEST = read_panel_manifest()


def read_ui_mappings():
    """Load UI 中文映射 from the single authority (E-26 批次2).

    Returns the mappings dict; callers must tolerate absence (fallback: {}).
    """
    mappings_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "catalog", "ui-mappings-v0.json"
    )
    try:
        return read_json(mappings_path)
    except (IOError, ValueError):
        return {}


_UI_MAPPINGS = read_ui_mappings()


def build_coordinate_overview():
    """E-38 v2: 三级坐标分布总览数据（NAV 之上的总览网格）。

    E-38 九项修正（相对 E-36 v1）：
      ① topology 面板显示地图自身结构（11 面板/子域数/L3 类数/generation）
      ② dac 六类从列底平铺改为缩进挂 beads.project-assets 子域下（L3·dac）
      ③ hardware 工具 L3 挂对父（phone/tablet->devices; content-production/infra-ops/dev-debug->tools-windows;
         browser->tools-browser; cloudflare->tools-cloudflare）+ 计数改读资产注册表 l3_tool_class 实标
      ④ search 面板显示层收为「线上搜索/线下搜索」两组（manifest 保留 6 合法子域保 T8，加 overview_groups 聚合定义）
      ⑤ 工位（8）归 agent-hub.slots；有 post 的 agent 归 agent-hub.registry（不分流 desktop）
      ⑥ harness.threads 接 thread-center registry.json 真实线程数
      ⑦ harness.memory 接 总控1 operations-log.md 台账计数
      ⑧ MCP 守恒自证：23 = 他域挂载（mcp-skill-map panel）+ 本体域（mcp-manager.servers）
      ⑨ L3 可选覆盖：子域=直属成员（不属于任何 L3，直接挂子域下）+ L3 组（部分成员聚成），
         渲染先直属后 L3 组；无 L3 的子域直显示成员（不硬塞组）；门闸只验「有 L3 的值合法」

    只读消费权威源（禁自判）：panel-manifest / webpage-asset-registry / task-projection /
      skill-panel-mapping / mcp-skill-map + mcp registry / agents.json + slot-registry /
      hardware-tools-taxonomy / data-asset-taxonomy / search-tools-registry / thread-center registry /
      总控1 operations-log.md。计数全部实时从数据源计算；空格子保留 0。
    """
    _cat = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "catalog")
    _tools_root = os.path.join(os.path.dirname(_cat), "..")

    def _rj(name, base=None):
        try:
            return read_json(os.path.join(base or _cat, name))
        except (IOError, ValueError):
            return None

    pm = _rj("panel-manifest-v0.json") or {}
    if not pm:
        return {}
    panels = sorted(pm.get("panels", []), key=lambda p: (p.get("sort_position", 999), p.get("panel_id", "")))

    # ---- 子域 -> 面板 反查（软关联：子域是独立标注，有子域标就归子域格，归不了再挂板块）----
    sub2panel = {}
    for p in panels:
        for s in (p.get("subnodes") or []):
            sid = s.get("subnode_id") if isinstance(s, dict) else s
            if sid:
                sub2panel.setdefault(sid, p["panel_id"])

    # ---- 骨架：11 面板 × 子域格（每格子域 = 直属 + L3 组）----
    grid = {}
    for p in panels:
        pid = p["panel_id"]
        grid[pid] = {
            "panel_id": pid,
            "display_number": p.get("display_number", ""),
            "title": p.get("title", ""),
            "subnodes": [],
            "counts": {"card": 0, "task": 0, "skill": 0, "mcp": 0, "agent": 0, "slot": 0, "thread": 0, "memory": 0},
        }
    for p in panels:
        pid = p["panel_id"]
        subs = [s.get("subnode_id") if isinstance(s, dict) else s for s in (p.get("subnodes") or [])]
        for sid in subs:
            grid[pid]["subnodes"].append({
                "subnode": sid,
                "direct": {"card": 0, "task": 0, "skill": 0, "mcp": 0, "agent": 0, "slot": 0, "thread": 0, "memory": 0},
                "l3": [],  # [{id,label,kind,counts:{...}}]
            })
        if not subs:  # topology 等无子域面板：面板级格（直接挂直属）
            grid[pid]["subnodes"].append({
                "subnode": None,
                "direct": {"card": 0, "task": 0, "skill": 0, "mcp": 0, "agent": 0, "slot": 0, "thread": 0, "memory": 0},
                "l3": [],
            })

    def _cell(panel_id, subnode):
        for c in grid[panel_id]["subnodes"]:
            if c["subnode"] == subnode:
                return c
        return None

    # ---- 数据源预读 ----
    ar = _rj("webpage-asset-registry-v0.json") or {}
    assets = ar.get("assets", [])
    tp = _rj("task-projection-v0.json") or {}
    sp = _rj("skill-panel-mapping-v0.json") or {}
    msm = _rj("mcp-skill-map.json") or {}
    mcps = msm.get("mcps", {}) or {}
    mr = _rj("registry.json", os.path.join(_tools_root, "mcp_manager")) or {}
    servers = mr.get("servers", {}) or {}
    ag = _rj("agents.json", os.path.join(_tools_root, "agent-hub")) or {}
    sr = _rj("slot-registry-v0.json") or {}
    ht = _rj("hardware-tools-taxonomy-v0.json") or {}
    dt = _rj("data-asset-taxonomy-v0.json") or {}
    str_ = _rj("search-tools-registry-v0.json") or {}

    # ---- ① 面板 0 topology：地图自身结构（meta 行展示，不参与守恒计数）----
    n_sub_all = sum(len(p.get("subnodes") or []) for p in panels)
    n_panels = len(panels)
    n_l3_classes = len((dt.get("data_asset_classes") or [])) + sum(
        len(v) for v in (ht.get("tool_classes") or {}).values() if isinstance(v, dict)
    ) + len((str_.get("channel_layers") or {}).get("online") or []) + len((str_.get("channel_layers") or {}).get("offline") or [])  # E-86: search 线上/线下档数从 search-tools-registry channel_layers 推导，不再写死 +3+5
    grid["topology"]["subnodes"][0]["l3"] = [
        {"id": "map-structure", "label": "11 面板 / %d 子域 / %d L3 类" % (n_sub_all, n_l3_classes), "kind": "meta", "counts": {"card": 0}},
    ]

    # ---- ③ 工具 L3 视图映射（软关联：l3_tool_class 是标注维度，不改对象归属；视图挂语义父级）----
    hw_l3_map = {
        "phone": "devices", "tablet": "devices",
        "content-production": "tools-windows", "infra-ops": "tools-windows", "dev-debug": "tools-windows",
        "browser": "tools-browser",
        "cloudflare": "tools-browser",
    }
    tool_titles = {}
    for grp in ("breakout", "regular"):
        for cid, cdef in ((ht.get("tool_classes") or {}).get(grp) or {}).items():
            tool_titles[cid] = cdef.get("title") if isinstance(cdef, dict) else cid

    # ---- ② 资产卡（85）：对象坐标 = panel + map_subnode（卡注册表明确标注，不反查）；
    #      该面板有对应子域格才归子域，否则面板级（归不了挂板块）----
    for a in assets:
        pid = a.get("panel")
        sid = a.get("map_subnode")
        if not (pid and pid in grid):
            continue
        if sid and _cell(pid, sid):
            c = _cell(pid, sid)
            c["direct"]["card"] += 1
            continue
        grid[pid]["counts"]["card"] += 1

    # ---- 任务（642）：子域优先（sub2panel 反查——子域是独立标注，能归子域就归子域格）；
    #      无子域或反查不到才按 map_node_id 挂板块（归不了挂板块）----
    node2panel = {
        "map.root-governance": "topology", "map.guardian": "guardian", "map.beads": "beads",
        "map.hardware-collaboration": "hardware", "map.search-retrieval": "search", "map.harness": "harness",
        "map.agent-hub": "agent-hub", "map.capability-management": "skill-manager",
        "map.business-assets": "topology", "map.views": "topology", "map.loop-runtime": "beads",
    }
    for e in tp.get("entities", []):
        if e.get("kind") != "task":
            continue
        sid = e.get("map_subnode")
        pid = sub2panel.get(sid) if sid else None
        if pid and pid in grid:
            c = _cell(pid, sid)
            if c:
                c["direct"]["task"] += 1
                continue
        nid = e.get("map_node_id")
        pid2 = node2panel.get(nid)
        if pid2 and pid2 in grid:
            grid[pid2]["counts"]["task"] += 1

    # ---- skill（234）：对象坐标 = panel_subnode 前缀 -> 面板 + 子域（该面板有子域格才归子域，否则面板级）----
    for m in sp.get("mappings", []):
        ps = (m.get("panel_subnode") or "").split("[")[0].strip()
        pid, _, sid = ps.partition(".")
        if not pid or pid not in grid:
            continue
        if sid and _cell(pid, sid):
            c = _cell(pid, sid)
            c["direct"]["skill"] += 1
            continue
        grid[pid]["counts"]["skill"] += 1

    # ---- MCP（23）：2026-08-06 改读 registry.map_node_id（三级坐标归位权威）----
    # 软关联：registry 登记不决定坐标；map_node_id 才是归属。mcp-skill-map 退为「维度映射」参考
    mr_servers = mr.get("servers", {})
    if isinstance(mr_servers, list):
        mr_servers = {x.get("id"): x for x in mr_servers}
    SUBNODE_DEFAULT = {"search": "search-tools", "hardware": "tools-windows", "beads": "beads-views", "projects": "project-assets",
                       "agent-hub": "registry", "harness": "modules", "topology": "map", "skill-manager": "catalog",
                       "mcp-manager": "servers"}
    n_mcp_other = 0
    l3_groups = {}
    for name in servers:
        entry = mr_servers.get(name) or {}
        node = entry.get("map_node_id") or ""
        pid = node.split(".", 1)[1] if node.startswith("map.") else ""
        if not pid or pid not in grid:
            pid = "mcp-manager"
        sid = SUBNODE_DEFAULT.get(pid, "")
        c = _cell(pid, sid) or (grid[pid]["subnodes"][0] if grid[pid].get("subnodes") else None)
        if c:
            c["direct"]["mcp"] += 1
            if pid != "mcp-manager":
                n_mcp_other += 1
            l3v = entry.get("l3")
            if l3v:
                l3_groups.setdefault((pid, sid, l3v), 0)
                l3_groups[(pid, sid, l3v)] += 1
    for (pid, sid, l3v), cnt in sorted(l3_groups.items()):
        c = _cell(pid, sid) or (grid[pid]["subnodes"][0] if grid[pid].get("subnodes") else None)
        if c is not None:
            c["l3"].append({"id": "mcp-" + str(l3v), "label": "L3·" + str(l3v), "kind": "l3", "counts": {"mcp": cnt}})
    grid["mcp-manager"]["subnodes"][0]["l3"].append(
        {"id": "mcp-conservation", "label": "MCP 守恒: %d = 他域归位 %d + 本体域 %d" % (len(servers), n_mcp_other, len(servers) - n_mcp_other), "kind": "meta", "counts": {"task": 0}}
    )

    # ---- ⑤ agent（10）→ agent-hub.registry（有 post 也归 registry，不分流 desktop）----
    for a in ag.get("agents", []):
        c = _cell("agent-hub", "registry")
        if c:
            c["direct"]["agent"] += 1

    # ---- ⑤ 工位（8）→ agent-hub.slots（E-38 从 desktop.desktop-slots 改归）----
    for s in sr.get("slots", []):
        c = _cell("agent-hub", "slots")
        if c:
            c["direct"]["slot"] += 1

    # ---- ⑥ harness.threads：thread-center registry 真实线程数 ----
    tc_reg = _rj("registry.json", os.path.join(_tools_root, "thread-center")) or {}
    threads = tc_reg.get("threads", []) or []
    c_threads = _cell("harness", "threads")
    if c_threads:
        c_threads["direct"]["thread"] += len(threads)
        from collections import Counter as _C
        _st = _C(t.get("status") for t in threads)
        c_threads["l3"].append({"id": "thread-status", "label": "状态: " + (" / ".join("%s %d" % (k, v) for k, v in _st.items()) if _st else "0"), "kind": "meta", "counts": {"task": 0}})

    # ---- ⑦ harness.memory：总控1 operations-log.md 台账计数 ----
    ops_log = os.path.join(_tools_root, "thread-center", "threads", "总控1", "operations-log.md")
    n_mem = 0
    try:
        with io.open(ops_log, "r", encoding="utf-8") as f:
            for line in f:
                if "memory" in line.lower():
                    n_mem += 1
    except (IOError, OSError):
        n_mem = 0
    c_mem = _cell("harness", "memory")
    if c_mem:
        c_mem["direct"]["memory"] += n_mem if n_mem else 1  # 台账有记录即至少 1（总控 memory=1）

    # ---- ⑧ harness.standards/modules 计数（E-38c 魅惑对图发现缺失）----
    import glob as _glob
    _std_dir = os.path.join("F:", os.sep, "codex", "docs", "thread-standards")
    _std_files = [f for f in _glob.glob(os.path.join(_std_dir, "*.md")) if ".bak" not in f]
    # E-118 ⑤选A：渲染层排除 deprecated 分册（内容含 deprecated 标记即不算分册计数）
    def _is_deprecated_fence(f):
        try:
            with open(f, encoding="utf-8", errors="ignore") as _fh:
                return "deprecated" in _fh.read()
        except OSError:
            return False
    _fen = [f for f in _std_files if "分册" in os.path.basename(f) and not _is_deprecated_fence(f)]
    c_std = _cell("harness", "standards")
    if c_std:
        c_std["direct"]["standard"] = c_std["direct"].get("standard", 0) + len(_std_files)
        if _fen:
            c_std["l3"].append({"id": "fence", "label": "分册", "kind": "l3", "counts": {"standard": len(_fen)}})
    _mod_files = _glob.glob(os.path.join("F:", os.sep, "codex", "docs", "harness-modules", "*", "module-card.md"))
    c_mod = _cell("harness", "modules") or _cell("harness", "模块")
    if c_mod:
        c_mod["direct"]["module"] = c_mod["direct"].get("module", 0) + len(_mod_files)

    # ---- ② dac L3 视图（软关联：L3 是标注维度，不改对象归属；全站该 dac 的对象数挂在
    #      beads.project-assets 子域下作视图展示；直属 = 子域全部对象不剥离）----
    dac_classes = [(c.get("id"), c.get("label") or c.get("name") or "") for c in (dt.get("data_asset_classes") or [])]
    dac_ids = {cid for cid, _ in dac_classes}
    card_dac = Counter()   # 全站资产卡按 data_asset_class
    skill_dac = Counter()  # 全站 skill 按 dac
    for a in assets:
        dc = a.get("data_asset_class")
        if dc in dac_ids:
            card_dac[dc] += 1
    for m in sp.get("mappings", []):
        dc = m.get("dac")
        if dc in dac_ids:
            skill_dac[dc] += 1
    c_pa = _cell("projects", "project-assets")
    if c_pa:
        for cid, label in dac_classes:
            nc = card_dac.get(cid, 0)
            ns = skill_dac.get(cid, 0)
            if nc + ns > 0:
                c_pa["l3"].append({"id": cid, "label": "L3·dac %s" % (label or cid), "kind": "dac",
                                   "counts": {"card": nc, "skill": ns, "task": 0, "mcp": 0, "agent": 0, "slot": 0, "thread": 0, "memory": 0}})

    # ③ 工具 L3 视图（软关联：l3_tool_class 是标注维度，不改对象归属；全站该类的卡数挂在
    #     语义父级子域下作视图；资产注册表实标 34 卡）
    tool_view = Counter()
    for a in assets:
        l3c = a.get("l3_tool_class")
        if l3c:
            tool_view[l3c] += 1
    for l3c, target_sub in hw_l3_map.items():
        c = _cell("hardware", target_sub)
        if not c or tool_view.get(l3c, 0) == 0:
            continue
        c["l3"].append({"id": l3c, "label": "L3·%s" % tool_titles.get(l3c, l3c), "kind": "tool",
                        "counts": {"card": tool_view[l3c], "task": 0, "skill": 0, "mcp": 0, "agent": 0, "slot": 0, "thread": 0, "memory": 0}})
    # ❓跨面板 l3 标注（provider-manager(❓) 等）无语义父级，不挂视图（L3 铁律：不硬塞）

    # ④ search 显示层聚合：manifest 6 子域（合法保 T8）映射到 线上搜索/线下搜索 两组
    #    线上 = constitution/tools/objects（+A1/A2/X 档，三档外直属）；线下 = okf-entry/manuals/graph（+手册/OKF/codegraph/rg）
    online_subs = ["constitution", "tools", "objects"]
    offline_subs = ["okf-entry", "manuals", "graph"]
    s_tools = str_.get("tools", []) or []
    a1 = sum(1 for t in s_tools if t.get("channel") == "A1")
    a2 = sum(1 for t in s_tools if t.get("channel") == "A2")
    x_ = sum(1 for t in s_tools if t.get("channel") == "X")
    offline_n = sum(1 for t in s_tools if t.get("channel") == "offline")
    # 聚合组结构（渲染层使用；manifest 保留 6 子域，总览显示两组）
    aggr_groups = [
        {"id": "online-search", "label": "线上搜索", "subs": online_subs,
         "l3": [{"id": "a1", "label": "A1 高时效", "count": a1},
                {"id": "a2", "label": "A2 一般", "count": a2},
                {"id": "x", "label": "X 海外", "count": x_}]},
        {"id": "offline-search", "label": "线下搜索", "subs": offline_subs,
         "l3": [{"id": "manual-usage", "label": "使用手册", "count": 1},
                {"id": "manual-repair", "label": "维修手册", "count": 1},
                {"id": "okf", "label": "OKF", "count": 1},
                {"id": "codegraph", "label": "codegraph+cbm", "count": 1},
                {"id": "rg", "label": "rg+Everything", "count": 1}]},
    ]
    grid["search"]["overview_groups"] = aggr_groups
    # 线上/线下组的直属计数（E-111 增补5 修3）：不滚加——组标题不显聚合计数，各子域独立计数平铺
    for grp in aggr_groups:
        grp["sub_directs"] = {}
        for sid in grp["subs"]:
            c = _cell("search", sid)
            if c:
                grp["sub_directs"][sid] = dict(c["direct"])
        grp["direct"] = {}

    overview = {
        "schema": "coordinate-overview-v2",
        "generated_in": "build_map_website.py (E-38 v2)",
        "meta": {"panels": n_panels, "subdomains": n_sub_all, "l3_classes": n_l3_classes},
        "panels": list(grid.values()),
        "totals": None,  # 渲染层由面板+子域实时加总（避免手写）
    }
    return overview


def _nav_from_manifest():
    """Derive NAV from panel-manifest-v0.json (single authority, E-26 批次1).

    Each panel contributes (panel_id, "<display_number> <title>"); panels are
    ordered by sort_position.  Falls back to [] when manifest is unavailable
    (hardcoded NAV removed — a missing manifest now surfaces as empty nav
    rather than silently diverging).
    """
    panels = _PANEL_MANIFEST.get("panels", []) if _PANEL_MANIFEST else []
    ordered = sorted(panels, key=lambda p: (p.get("sort_position", 999), p.get("panel_id", "")))
    return [
        (p["panel_id"], "{} {}".format(p.get("display_number", ""), p.get("title", "")).strip())
        for p in ordered
        if p.get("panel_id")
    ]


NAV = _nav_from_manifest()


def read_domain_cards():
    """Load domain panel cards from the single authority (E-26 批次2).

    Returns the cards dict; callers must tolerate absence (fallback: {}).
    """
    cards_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "catalog", "domain-cards-v0.json"
    )
    try:
        return read_json(cards_path)
    except (IOError, ValueError):
        return {}


DOMAIN_CARDS = read_domain_cards()


def first_label(issue, prefix):
    for label in issue.get("labels") or []:
        if isinstance(label, str) and label.startswith(prefix):
            return label
    return ""


def clean_entity(entity):
    row = {
        "id": entity.get("id"),
        "kind": entity.get("kind"),
        "name": entity.get("display_name") or entity.get("name") or entity.get("id"),
        "state": entity.get("state"),
        "plane": entity.get("state_plane"),
        "source": entity.get("source"),
        "observed": entity.get("observed_at"),
        "fresh": entity.get("fresh_until"),
        "interface": entity.get("interface_kind", ""),
    }
    # codex-69lp: pass through projection fields (manual coverage / task status / node / asset)
    for k in ("manual_type", "manual_map_node", "covers_object", "form",
              "status", "map_node_id", "map_subnode", "node_derivation", "asset_type", "url", "local_path", "dashboard_path",
              "owner_beads", "related_beads", "panel", "subgroup", "card", "map_number", "lifecycle",
              "data_asset_class", "seated_agent", "fronted_by_agent", "backed_by_agent",
              "asset_size", "asset_mtime", "guardian_layer", "evidence", "guardian_target", "stop_reason", "owner"):
        if entity.get(k) is not None:
            row[k] = entity.get(k)
    return row


def build_relation_rows(beads):
    relation_rows = []
    for issue in beads:
        if issue.get("_type") == "memory":
            continue
        issue_id = issue.get("id", "")
        issue_title = issue.get("title", "")
        for edge in iter_issue_dependencies(issue):
            relation_rows.append({
                "source": issue_id,
                "sourceTitle": issue_title,
                "kind": edge["kind"],
                "target": edge["target"],
                "direction": edge.get("direction", "depends_on"),
            })
        for edge in iter_issue_dependents(issue):
            relation_rows.append({
                "source": edge["target"],
                "sourceTitle": "",
                "kind": edge["kind"],
                "target": issue_id,
                "direction": edge.get("direction", "depended_on_by"),
                "via": "dependents",
            })
    seen = set()
    deduped = []
    for row in relation_rows:
        key = (row.get("source"), row.get("kind"), row.get("target"))
        if key in seen or not row.get("target"):
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def main():
    if len(sys.argv) != 5:
        print("usage: build_map_website.py <registry.json> <snapshot.json> <beads.jsonl> <out.html>")
        sys.exit(2)

    registry, snapshot, beads = read_json(sys.argv[1]), read_json(sys.argv[2]), read_jsonl(sys.argv[3])
    view_projection = read_view_projection(sys.argv[1], registry)
    entities, edges = snapshot.get("entities", []), snapshot.get("edges", [])
    # 网页资产卡片：本地文件体积/修改时间（卡片链接行用，读失败静默略过）
    for entity in entities:
        if entity.get("kind") != "asset" or not entity.get("local_path"):
            continue
        path = entity["local_path"]
        try:
            if os.path.isfile(path):
                size, mtime = os.path.getsize(path), os.path.getmtime(path)
            elif os.path.isdir(path):
                size, mtimes = 0, []
                for root, _, files in os.walk(path):
                    for name in files:
                        try:
                            fp = os.path.join(root, name)
                            size += os.path.getsize(fp)
                            mtimes.append(os.path.getmtime(fp))
                        except OSError:
                            continue
                mtime = max(mtimes) if mtimes else None
            else:
                continue
            entity["asset_size"] = ("%.1f MB" % (size / 1048576)) if size >= 1048576 else ("%.0f KB" % (size / 1024))
            if mtime:
                entity["asset_mtime"] = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        except OSError:
            continue
    by_source = defaultdict(list)
    for entity in entities:
        by_source[entity.get("source", "unknown")].append(entity)

    active = [
        issue for issue in beads
        if issue.get("_type") != "memory" and issue.get("status") in {"open", "in_progress", "blocked"}
    ]
    active.sort(key=lambda issue: (issue.get("priority", 9), issue.get("status") != "in_progress", issue.get("id", "")))
    relation_rows = build_relation_rows(beads)
    # codex-ygjz P2: beads 源计数取真实任务数（快照不直接发 beads 实体，投影经 relation-registry 吸收，
    # 直接数快照会让 beads 显示 0 而任务面板有几百条——计数必须贴近消费面真相）
    beads_total = len([i for i in beads if i.get("_type") != "memory"])
    sources = []
    for source in registry.get("sources", []):
        sid = source["sourceId"]
        if sid == "beads":
            cnt = beads_total
            note_suffix = "（任务实体经任务面板投影，快照不直发；此为导出任务总数）"
        else:
            cnt = len(by_source.get(sid, []))
            note_suffix = ""
        sources.append({
            "id": sid,
            "status": source.get("status", "unknown"),
            "authority": source.get("authority", ""),
            "plane": source.get("statePlane", ""),
            "count": cnt,
            "note": source.get("note", "") + note_suffix,
        })
    # E-111 R4: snapshot 不直发 sources 数组时，从 activeSourcesInRegistry 派生存活源计数（注册权威源=活跃源数）
    if not sources:
        for sid in (registry.get("activeSourcesInRegistry") or []):
            sources.append({
                "id": sid,
                "status": "active",
                "authority": "",
                "plane": "",
                "count": 0,
                "note": "派生自 activeSourcesInRegistry（snapshot 未直发 sources）",
            })
    entity_by_id = {entity.get("id"): clean_entity(entity) for entity in entities}
    agent_families = []
    for entity in entities:
        if entity.get("source") != "agent-hub" or entity.get("kind") != "agent_family":
            continue
        family = clean_entity(entity)
        slot_ids = [
            edge.get("to") for edge in edges
            if edge.get("kind") == "DECLARES_SLOT" and edge.get("from") == entity.get("id")
        ]
        family["slots"] = [entity_by_id[slot_id] for slot_id in slot_ids if slot_id in entity_by_id]
        agent_families.append(family)
    # Beads map_* projection coverage (website is projection-only; Beads remains authority)
    non_memory = [i for i in beads if i.get("_type") != "memory"]
    bound_details = []
    for issue in non_memory:
        md = issue.get("metadata") or {}
        if not isinstance(md, dict):
            continue
        node = md.get("map_node_id")
        if node is None or str(node).strip() == "":
            continue
        bound_details.append({
            "id": issue.get("id", ""),
            "status": issue.get("status", ""),
            "title": issue.get("title", ""),
            "map_node_id": str(node),
            "map_path": str(md.get("map_path") or ""),
            "map_projection_authorization": str(md.get("map_projection_authorization") or ""),
            "map_projection_receipt": str(md.get("map_projection_receipt") or ""),
        })
    bound_ids = [row["id"] for row in bound_details]
    active_bound = [row for row in bound_details if row.get("status") in {"open", "in_progress", "blocked"}]
    total_issues = len(non_memory)
    bound_count = len(bound_details)
    unbound_count = total_issues - bound_count
    # codex-ygjz P2: 覆盖率三分——explicit-binding（metadata）/ lane-derived（lane 映射）/ unassigned
    # lane 映射表与 build_task_projection.py 同源（importlib 直读，防双写漂移）
    lane_derived = []
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("btp", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "build_task_projection.py"))
        _btp = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_btp)
        _lane2node = getattr(_btp, "LANE2NODE", {})
        _lanes_fn = getattr(_btp, "lanes", lambda i: [])
    except Exception:
        _lane2node = {}
        _lanes_fn = lambda i: []
    for issue in non_memory:
        md = issue.get("metadata") or {}
        if isinstance(md, dict) and md.get("map_node_id"):
            continue  # explicit 已计
        ls = _lanes_fn(issue)
        if ls and ls[0] in _lane2node:
            lane_derived.append(issue.get("id", ""))
    lane_derived_count = len(lane_derived)
    unassigned_count = total_issues - bound_count - lane_derived_count
    coverage = {
        "total_issues": total_issues,
        "active_issues": len(active),
        "bound_map_node_id": bound_count,
        "unbound_map_node_id": unbound_count,
        "active_bound": len(active_bound),
        "active_unbound": len(active) - len(active_bound),
        "bound_issue_ids": bound_ids,
        "bound_details": bound_details,
        "lane_derived_count": lane_derived_count,
        "lane_derived_ids": lane_derived,
        "unassigned_count": unassigned_count,
        "coverage_ratio": (round(bound_count / total_issues, 6) if total_issues else 0),
        "coverage_rule": "explicit=metadata.map_node_id; lane-derived=lane 标签经 LANE2NODE 映射; unassigned=其余（codex-ygjz P2 三分）",
        "note": "bound = metadata.map_node_id present; website is not write authority",
        "gate_g_receipt_id": "receipt:beads-map-write:20260726T075828737Z",
        "gate_g_applied_ids": ["codex-cs9x", "codex-br4m"],
    }

    # codex-69lp: manual coverage matrix (usage/repair per object & branch)
    manuals = [e for e in entities if e.get("kind") == "manual"]
    manual_by_id = {m.get("id"): m for m in manuals}
    cover_edges = [e for e in edges if e.get("kind") == "manual-covers"]
    obj_cov = {}
    for edge in cover_edges:
        mid, tgt = edge.get("from"), edge.get("to")
        man = manual_by_id.get(mid)
        if not man:
            continue
        entry = obj_cov.setdefault(tgt, {"object": tgt, "usage": False, "repair": False, "manuals": []})
        mt = man.get("manual_type", "usage")
        if mt in ("usage", "both"):
            entry["usage"] = True
        if mt in ("repair", "both"):
            entry["repair"] = True
        entry["manuals"].append(f"{mid}({mt})")
    branch_cov = {}
    for m in manuals:
        br = m.get("manual_map_node") or "unassigned"
        b = branch_cov.setdefault(br, {"branch": br, "usage": 0, "repair": 0, "both": 0, "kb": 0, "total": 0})
        mt = m.get("manual_type", "usage")
        b[mt if mt in ("usage", "repair", "both", "kb") else "usage"] += 1
        b["total"] += 1
    # 规范 map.* 节点：单一事实源 = catalog/map-nodes-v0.json（2026-07-28 管线固化抽出）
    # E-94 注记：view 层历史值坐标解释查 view-to-coord-map-v0.json（坐标层查 manifest）——不改消费逻辑。
    # E-26 批次2: 读取失败即 FAIL（不再静默硬编码兜底——权威缺失必须暴露而非掩盖）
    _MAP_NODES_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "catalog", "map-nodes-v0.json")
    try:
        with io.open(_MAP_NODES_JSON, "r", encoding="utf-8-sig") as _s:
            ALL_NODES = [n["id"] for n in json.load(_s)["nodes"]]
            _nodes_src = "map-nodes-v0.json"
    except Exception as _e:
        raise SystemExit(
            "FATAL: map-nodes-v0.json unreadable (E-26 批次2: 权威读取失败即 FAIL, 不许静默兜底): %s" % _e
        )
    print("  map nodes source: %s (%d nodes)" % (_nodes_src, len(ALL_NODES)))
    for node in ALL_NODES:
        branch_cov.setdefault(node, {"branch": node, "usage": 0, "repair": 0, "both": 0, "kb": 0, "total": 0})
    manual_coverage = {
        "by_object": sorted(obj_cov.values(), key=lambda r: r["object"]),
        "by_branch": [branch_cov[n] for n in ALL_NODES] + [v for k, v in sorted(branch_cov.items()) if k not in ALL_NODES],
        "zero_manual_branches": [n for n in ALL_NODES if branch_cov[n]["total"] == 0],
        "counts": {"manuals": len(manuals), "cover_edges": len(cover_edges),
                   "objects_covered": len(obj_cov),
                   "objects_full_both": sum(1 for v in obj_cov.values() if v["usage"] and v["repair"])},
    }

    # ---- beads 依赖数据（2026-07-28 beads 板块优化 A/D：阻塞链 + 依赖计数）----
    issue_by_id = {issue.get("id"): issue for issue in beads if issue.get("id")}
    blocking_map = {}
    for issue in beads:
        for dep in issue.get("dependencies", []) or []:
            if dep.get("type") == "blocks":
                blocking_map.setdefault(dep.get("depends_on_id"), []).append(issue.get("id"))
    task_deps = {}
    block_chains = []
    for issue in active:
        blocked_by = [dep.get("depends_on_id") for dep in (issue.get("dependencies", []) or []) if dep.get("type") == "blocks"]
        blocking = blocking_map.get(issue.get("id"), [])
        if blocked_by or blocking:
            task_deps[issue["id"]] = {"blockedBy": blocked_by, "blocking": blocking}
        for dep in (issue.get("dependencies", []) or []):
            if dep.get("type") != "blocks":
                continue
            blocker = issue_by_id.get(dep.get("depends_on_id"), {})
            block_chains.append({
                "id": issue.get("id"), "title": issue.get("title", ""), "status": issue.get("status", ""),
                "blockerId": dep.get("depends_on_id", ""), "blockerTitle": blocker.get("title", "?"),
                "blockerStatus": blocker.get("status", "unknown"),
            })

    # ---- Guardian 实时数据（2026-07-28 guardian 板块优化 A：soak/巡检 live strip，只读）----
    def _parse_ts(value):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None
    guardian_live = {"soak": None, "patrol": None}
    soak_dir = r"F:\codex\reports\desktop-health-soak\samples"
    try:
        stamps = []
        unhealthy = 0
        for name in sorted(os.listdir(soak_dir)):
            if not (name.startswith("desktop-health-") and name.endswith(".json")):
                continue
            try:
                with io.open(os.path.join(soak_dir, name), "r", encoding="utf-8") as stream:
                    sample = json.load(stream)
                ts = _parse_ts(sample.get("observed_at"))
                if ts:
                    stamps.append(ts)
                    # codex-ygjz P1-7: unhealthy/degraded 或 read_only!=True 计入判定
                    if sample.get("health_state") not in (None, "healthy") or sample.get("read_only") is not True:
                        unhealthy += 1
            except Exception:
                continue
        if stamps:
            stamps.sort()
            soak_cov = (stamps[-1] - stamps[0]).total_seconds()
            max_gap = max(((cur - prev).total_seconds() for prev, cur in zip(stamps, stamps[1:])), default=0.0)
            latest_age = (datetime.now(timezone.utc) - stamps[-1]).total_seconds()
            # codex-ygjz P1-7：完整判定 = 覆盖 + 最大间隔 + 最新样本年龄 + 不健康样本；豁免读 authorizations（未过期 approved）
            failures = []
            if soak_cov < 86400:
                failures.append("coverage-insufficient")
            if max_gap > 600:
                failures.append("snapshot-gap-exceeds-threshold")
            if latest_age > 900:
                failures.append("latest-sample-stale")
            if unhealthy:
                failures.append("unhealthy-or-nonreadonly-snapshot-present")
            waivers = []
            auth_dir = r"F:\codex\docs\architecture\authorizations"
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            try:
                for aname in os.listdir(auth_dir):
                    if not aname.endswith(".json"):
                        continue
                    try:
                        with io.open(os.path.join(auth_dir, aname), "r", encoding="utf-8") as stream:
                            auth = json.load(stream)
                    except Exception:
                        continue
                    if auth.get("status") != "approved" or (auth.get("expires_at") or "0000-00-00") < today:
                        continue
                    for wb in (auth.get("soak_evidence") or {}).get("waived_blockers") or []:
                        waivers.append({"blocker": str(wb).split(" ")[0], "note": wb,
                                        "authorization_id": auth.get("authorization_id"), "expires_at": auth.get("expires_at")})
            except OSError:
                pass
            waived_ids = {w["blocker"] for w in waivers}
            unwaived = [f for f in failures if f not in waived_ids]
            # codex-ygjz 复验窄改（审核方认可 2026-07-30）：豁免只作信息展示，永不改变健康判定——
            # 新 degraded 样本不得被旧授权自动开脱；判定只认当前指标。
            soak_status = "passed" if not failures else "insufficient"
            guardian_live["soak"] = {
                "samples": len(stamps),
                "coverage_seconds": round(soak_cov, 1),
                "required_seconds": 86400,
                "max_gap_seconds": round(max_gap, 1),
                "allowed_gap_seconds": 600,
                "latest_age_seconds": round(latest_age, 1),
                "allowed_latest_age_seconds": 900,
                "unhealthy_samples": unhealthy,
                "failures": failures,
                "unwaived_failures": unwaived,
                "waivers": waivers,
                "eta": datetime.fromtimestamp(stamps[0].timestamp() + 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": soak_status,
            }
    except OSError:
        pass
    patrol_root = r"F:\codex\reports\run-manifests"
    try:
        patrol_dirs = sorted((d for d in os.listdir(patrol_root) if d.endswith("-g76q-daily-patrol")), reverse=True)
        for dname in patrol_dirs:
            evidence = os.path.join(patrol_root, dname, "evidence.md")
            if not os.path.isfile(evidence):
                continue
            text = io.open(evidence, "r", encoding="utf-8").read()
            mverdict = re.search(r"Verdict:\*\*\s*(\w+)", text) or re.search(r"Verdict[:\s]+(\w+)", text)
            guardian_live["patrol"] = {"date": dname[:10], "verdict": mverdict.group(1) if mverdict else "unknown"}
            break
    except OSError:
        pass

    # codex-ygjz P2: dataAsOf 分集——各数据集真实采样时间，不用快照时间一统
    def _mtime_iso(path):
        try:
            return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except OSError:
            return None
    gen_manifest = os.path.join(os.path.dirname(sys.argv[3]), "generation.json")
    _cat = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "catalog")
    beads_asof = None
    if os.path.isfile(gen_manifest):
        try:
            beads_asof = json.load(io.open(gen_manifest, encoding="utf-8")).get("exported_at")
        except Exception:
            beads_asof = None
    if not beads_asof:
        beads_asof = _mtime_iso(sys.argv[3])
    data_asof_by_source = {
        "beads": beads_asof,
        "snapshot": snapshot.get("generatedAt", "unknown"),
        "assets": _mtime_iso(os.path.join(_cat, "webpage-asset-registry-v0.json")),
        "manuals": _mtime_iso(os.path.join(_cat, "manual-registry-v0.json")),
        "guardian": (datetime.fromtimestamp(stamps[-1].timestamp(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ("stamps" in dir() and stamps) else None),
    }

    data = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshotObservedAt": snapshot.get("generatedAt", "unknown"),
        "dataAsOf": beads_asof or snapshot.get("generatedAt", "unknown"),
        "dataAsOfBySource": data_asof_by_source,
        "nav": [{"key": key, "title": title} for key, title in NAV],
        "precedents": (lambda _pp=os.path.join(_cat, "precedent-registry-v0.json"): (read_json(_pp).get("precedents", []) if os.path.isfile(_pp) else []))(),
        "slotOverview": (lambda _sp=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "desktop-agent-console", "config", "slot-status.json"): (read_json(_sp) if os.path.isfile(_sp) else {}))(),
        "devices": (lambda _devp=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "desktop-agent-console", "config", "devices.json"): read_json(_devp).get("devices", []) if os.path.isfile(_devp) else [])(),
        "manualCoverage": manual_coverage,
        "domainCards": DOMAIN_CARDS,
        "uiMappings": _UI_MAPPINGS,
        "viewProjection": view_projection,
        "agentTopology": {"families": agent_families},
        "sources": sources,
        "entities": [clean_entity(entity) for entity in entities],
        "entityCounts": dict(Counter(entity.get("kind", "unknown") for entity in entities)),
        "edges": edges,
        "edgeCounts": dict(Counter(edge.get("kind", "unknown") for edge in edges)),
        "tasks": [{
            "id": issue.get("id", ""), "title": issue.get("title", ""), "status": issue.get("status", ""),
            "priority": issue.get("priority", ""), "pillar": first_label(issue, "pillar:"),
            "lane": first_label(issue, "lane:"),
            "labels": issue.get("labels") or [],
            "map_node_id": ((issue.get("metadata") or {}).get("map_node_id") if isinstance(issue.get("metadata"), dict) else "") or "",
        } for issue in active],
        "taskCounts": dict(Counter(issue.get("status", "unknown") for issue in beads if issue.get("_type") != "memory")),
        "taskDeps": task_deps,
        "blockChains": block_chains,
        "guardianLive": guardian_live,
        "pillarCounts": dict(Counter(first_label(issue, "pillar:") or "unclassified" for issue in active)),
        "relationRows": relation_rows,
        "relationCounts": dict(Counter(row["kind"] for row in relation_rows)),
        "coverage": coverage,
        "workbenchPath": r"C:\html\agent-workbench.html",
        "desktopUrl": "http://127.0.0.1:18765/",
        "viewMatrixRef": "docs/architecture/view-responsibility-matrix-v1.json",
        "coordOverview": build_coordinate_overview(),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map_website_template.html")
    with io.open(template_path, "r", encoding="utf-8") as stream:
        document = stream.read()
    if "__DATA__" not in document:
        raise SystemExit("map_website_template.html missing __DATA__ placeholder")
    document = document.replace("__DATA__", data_json)
    with io.open(sys.argv[4], "w", encoding="utf-8") as stream:
        stream.write(document)
    blank = sum(1 for row in relation_rows if not row.get("target"))
    print("WEBSITE OK")
    print("  entities: %d edges: %d active_tasks: %d" % (len(entities), len(edges), len(active)))
    print("  sources: %d relations: %d blankTargets: %d out: %s" % (len(sources), len(relation_rows), blank, sys.argv[4]))
    print("  relationCounts: %s" % json.dumps(dict(Counter(row["kind"] for row in relation_rows)), ensure_ascii=False))
    print("  coverage bound=%d unbound=%d active_bound=%d active_unbound=%d generatedAt=%s dataAsOf=%s" % (
        coverage["bound_map_node_id"], coverage["unbound_map_node_id"],
        coverage["active_bound"], coverage["active_unbound"],
        data["generatedAt"], data["dataAsOf"]))


if __name__ == "__main__":
    main()
