#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
test_map_integrity.py — 大地图完整性三测（codex-ygjz P3-3，只读）
  T1 七天边界：build_task_projection.within_days 边界（6.9 天进 / 7.1 天出）
  T2 数据新鲜度：map-build.html 内嵌 D.dataAsOfBySource 四键存在且不全相同
  T3 输入代次：最新 generations/*/generation.json 的 sha256 与导出文件一致
出口：全过 exit 0。
"""
import json, io, os, re, sys, glob, hashlib, datetime, importlib.util, subprocess

ROOT = r"F:\codex"
fails = []
# 不重包 sys.stdout（重包会让原对象被 GC 并关闭共享底层 buffer——血泪坑）；
# 以 PYTHONUTF8=1 运行，原生 stdout 即 UTF-8。
_saved_stdout = sys.stdout

# T1 七天边界（注意：btp 顶层 detach 原 stdout 并自建 UTF-8 wrapper——还原无意义，直接用它的）
spec = importlib.util.spec_from_file_location("btp", os.path.join(ROOT, r"tools\agent-system-map\build_task_projection.py"))
btp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(btp)
now = datetime.datetime.now(datetime.timezone.utc)
d69 = (now - datetime.timedelta(days=6, hours=23)).isoformat()
d71 = (now - datetime.timedelta(days=7, hours=1)).isoformat()
days = getattr(btp, "RECENT_CLOSED_DAYS", 7)
t1a = btp.within_days(d69, days) is True
t1b = btp.within_days(d71, days) is False
print("T1 七天边界: 6.9d进=%s 7.1d出=%s (窗口=%s 天)" % (t1a, t1b, days))
if not (t1a and t1b):
    fails.append("T1 within_days boundary")

# T2 数据新鲜度（数据嵌在 <script id="mapdata">JSON</script>）
html = io.open(r"C:\html\agent-global-map.html", encoding="utf-8").read()
m = re.search(r'<script type="application/json" id="mapdata">(.*?)</script>', html, re.S)
d = None
if m:
    d = json.loads(m.group(1))
if d is None:
    fails.append("T2 embedded data not found")
else:
    aos = d.get("dataAsOfBySource") or {}
    keys = [k for k in ("beads", "assets", "manuals", "guardian") if aos.get(k)]
    vals = {aos[k] for k in keys}
    t2 = (len(keys) == 4) and (len(vals) >= 2)
    print("T2 数据新鲜度: 键=%s 去重值=%d" % (keys, len(vals)))
    if not t2:
        fails.append("T2 dataAsOfBySource missing/uniform: %s" % aos)

# T3 输入代次哈希
gens = sorted(glob.glob(os.path.join(ROOT, r"tools\agent-system-map\catalog\generations\*\generation.json")))
if not gens:
    fails.append("T3 no generation found")
else:
    g = gens[-1]
    man = json.load(io.open(g, encoding="utf-8"))
    rawb = io.open(os.path.join(os.path.dirname(g), "beads-export.jsonl"), "rb").read()
    sha = hashlib.sha256(rawb).hexdigest()
    t3 = (sha == man.get("sha256"))
    print("T3 输入代次: gen=%s count=%s 原始字节sha一致=%s" % (man.get("generation_id"), man.get("count"), t3))
    if not t3:
        fails.append("T3 generation sha mismatch")

# T4 脱敏回归（codex-ygjz P3-4）：mcp registry env/static_env 的真实值绝不出现在快照或页面
sens = set()
try:
    mreg = json.load(io.open(os.path.join(ROOT, r"tools\mcp_manager\registry.json"), encoding="utf-8"))
    for sval in (mreg.get("servers") or {}).values():
        if not isinstance(sval, dict):
            continue
        for field in ("env", "static_env"):
            env = sval.get(field) or {}
            if isinstance(env, dict):
                for v in env.values():
                    v = str(v)
                    if len(v) >= 6:
                        sens.add(v)
except Exception as e:
    # codex-ygjz 复验修正：敏感源读取失败必须 FAIL，不许跳过后绿灯
    fails.append("T4 sensitive source unreadable (fail-closed): %s" % e)
    sens = set()
if not sens and not any(x.startswith("T4") for x in fails):
    fails.append("T4 no sensitive samples collected (fail-closed)")
snap_raw = io.open(os.path.join(ROOT, r"tools\agent-system-map\catalog\multi-source-snapshot-v2.json"), encoding="utf-8").read()
page_raw = io.open(r"C:\html\agent-global-map.html", encoding="utf-8").read()
leaks = [v for v in sens if v in snap_raw or v in page_raw]
print("T4 脱敏回归: 敏感值样本=%d 泄漏=%d" % (len(sens), len(leaks)))
if leaks:
    fails.append("T4 secret leak: %d values" % len(leaks))

# T5 agents.json 投影白名单（codex-ygjz v5.5 §三-D，独立于 T4）
try:
    aj = json.load(io.open(os.path.join(ROOT, r"tools\agent-hub\agents.json"), encoding="utf-8"))
    sens5 = []
    for a in aj.get("agents", []):
        for f in ("configPath", "credentialEnv", "credentialTarget"):
            v = a.get(f)
            if v and len(str(v)) >= 4:
                sens5.append(str(v))
    snap_raw5 = io.open(os.path.join(ROOT, r"tools\agent-system-map\catalog\multi-source-snapshot-v2.json"), encoding="utf-8").read()
    page_raw5 = io.open(r"C:\html\agent-global-map.html", encoding="utf-8").read()
    leaks5 = [v for v in sens5 if v in snap_raw5 or v in page_raw5]
    t5ok = (len(leaks5) == 0)
    print("T5 agents.json 白名单: 敏感值样本=%d 泄漏=%d（envelope 系统字段豁免）" % (len(sens5), len(leaks5)))
    if not t5ok:
        fails.append("T5 agents.json sensitive leak: %d" % len(leaks5))
except Exception as e:
    fails.append("T5 unreadable (fail-closed): %s" % e)

# T6 数字资产分类门闸（E-23 2026-08-03 skill manager 常驻1）：所有 data_asset_class / dac 值
# 必须存在于 data-asset-taxonomy-v0.json（零未知指向）；16 个 database 资产必须有值。
t6ok = True
try:
    tax = json.load(io.open(os.path.join(ROOT, r"tools\agent-system-map\catalog\data-asset-taxonomy-v0.json"), encoding="utf-8"))
    valid = {c["id"] for c in tax.get("data_asset_classes", [])}
    ar = json.load(io.open(os.path.join(ROOT, r"tools\agent-system-map\catalog\webpage-asset-registry-v0.json"), encoding="utf-8"))
    dbs = [a for a in ar.get("assets", []) if a.get("type") == "database"]
    bad = [a["asset_id"] for a in dbs if not a.get("data_asset_class") or a["data_asset_class"] not in valid]
    sm = json.load(io.open(os.path.join(ROOT, r"tools\agent-system-map\catalog\skill-panel-mapping-v0.json"), encoding="utf-8"))
    bad += ["%s->%s" % (m["skill"], m["dac"]) for m in sm.get("mappings", []) if m.get("dac") and m["dac"] not in valid]
    print("T6 taxonomy 门闸: 子类=%d database=%d 映射=%d 坏引用=%d" % (len(valid), len(dbs), len(sm.get("mappings", [])), len(bad)))
    if bad:
        t6ok = False
        fails.append("T6 taxonomy unknown refs: %s" % bad[:5])
except Exception as e:
    t6ok = False
    fails.append("T6 unreadable (fail-closed): %s" % e)

# T7 agent 身份联邦门闸（E-24 2026-08-03 skill manager 常驻1，总控批准挂常驻）：backed_by_agent /
# fronted_by_agent / seated_agent 引用必须解析到合法 id 域（agents.json ∪ binding profile ∪ DAC agentHubId）。
t7ok = True
try:
    aj7 = json.load(io.open(os.path.join(ROOT, r"tools\agent-hub\agents.json"), encoding="utf-8"))
    sp7 = json.load(io.open(os.path.join(ROOT, r"tools\agent-hub\slot-provider-bindings.json"), encoding="utf-8"))
    pr7 = json.load(io.open(os.path.join(ROOT, r"tools\agent-hub\provider-routes.json"), encoding="utf-8"))
    mr7 = json.load(io.open(os.path.join(ROOT, r"tools\mcp_manager\registry.json"), encoding="utf-8"))
    dac7 = json.load(io.open(os.path.join(ROOT, r"tools\desktop-agent-console\config\agents.json"), encoding="utf-8-sig"))
    valid7 = {a["id"] for a in aj7["agents"]}
    for b in sp7.get("bindings", []):
        prof = (b.get("profile") or {}).get("profileId")
        if prof and prof.startswith("profile:"):
            valid7.add(prof[8:])
    for a in dac7.get("agents", []):
        if a.get("agentHubId"):
            valid7.add(a["agentHubId"])
        for ov in (a.get("slotOverrides") or {}).values():
            if ov.get("agentHubId"):
                valid7.add(ov["agentHubId"])
    bad7 = []
    for name, s in mr7.get("servers", {}).items():
        v = s.get("backed_by_agent")
        if v and v not in valid7:
            bad7.append("mcp:%s->%s" % (name, v))
    for r in pr7.get("routes", []):
        v = r.get("fronted_by_agent")
        if v and v not in valid7:
            bad7.append("route:%s->%s" % (r.get("routeId"), v))
    for b in sp7.get("bindings", []):
        v = b.get("seated_agent")
        if v and v not in valid7:
            bad7.append("binding:%s->%s" % (b.get("slotId"), v))
    print("T7 联邦门闸: 合法域=%d backed/fronted/seated 坏引用=%d" % (len(valid7), len(bad7)))
    if bad7:
        t7ok = False
        fails.append("T7 federation unknown refs: %s" % bad7[:5])
except Exception as e:
    t7ok = False
    fails.append("T7 unreadable (fail-closed): %s" % e)


# T8 三级坐标门（2026-08-03 总控）：task-projection 的 map_node_id ∈ map.* 且 map_subnode ∈ manifest 子域
try:
    proj8 = json.load(open(os.path.join(ROOT, "tools/agent-system-map/catalog/task-projection-v0.json"), encoding="utf-8"))
    mani8 = json.load(open(os.path.join(ROOT, "tools/agent-system-map/catalog/panel-manifest-v0.json"), encoding="utf-8"))
    valid_nodes8 = set()
    for n in mani8.get("map_nodes", []):
        valid_nodes8.add(n if isinstance(n, str) else n.get("id"))
    # E-46 审核 F4 修复（2026-08-07）：map_nodes 为空时从 panels 推导，节点校验不再短路失效
    if not valid_nodes8:
        valid_nodes8 = {"map." + pn.get("panel_id") for pn in mani8.get("panels", []) if pn.get("panel_id")}
    valid_subs8 = set()
    for pn in mani8.get("panels", []):
        for sn in pn.get("subnodes", []):
            valid_subs8.add(sn.get("subnode_id") or sn.get("id"))
    # E-64（2026-08-09）：map_l3 值域从全部 taxonomy 文件推导（L3 唯一定义点，值域单一来源；缺省=无 L3 合法）
    # E-135（2026-08-15）：改读生成式 l3-registry（build_l3_registry.py 汇总，源=taxonomy 域自治；过渡双读比对一致后单读；全量值域不拆 kind）
    valid_l3_8 = set()
    _l3_reg_path = os.path.join(ROOT, "tools/agent-system-map/catalog/l3-registry-v0.json")
    if os.path.exists(_l3_reg_path):
        _l3r = json.load(open(_l3_reg_path, encoding="utf-8"))
        for _e in _l3r.get("entries", []):
            if _e.get("status") != "deprecated" and _e.get("l3_id"):
                valid_l3_8.add(_e["l3_id"])
        # 过渡双读：与 glob 旧值比对（防生成器回归 bug）
        _old_l3 = set()
        for _tf in glob.glob(os.path.join(ROOT, "tools/agent-system-map/catalog/*taxonomy*.json")):
            try:
                _td = json.load(open(_tf, encoding="utf-8"))
                for _cls in _td.get("data_asset_classes", []):
                    if _cls.get("status") != "deprecated" and _cls.get("id"):
                        _old_l3.add(_cls["id"])
            except Exception:
                pass
        if valid_l3_8 != _old_l3:
            fails.append("T8 l3-registry 与 taxonomy 源不一致（差集 old-new=%s new-old=%s，重建 build_l3_registry.py）"
                         % (sorted(_old_l3 - valid_l3_8)[:3], sorted(valid_l3_8 - _old_l3)[:3]))
    else:
        # 注册表缺失：fail-closed（l3-registry 是 E-135 核心产物，注册制唯一——家没了=红灯，与 D9 行为统一）
        # E-135 执行自查（2026-08-15）：原实现 fallback glob 不 fail 与 D9 fail 不一致，统一为 fail-closed
        fails.append("T8 l3-registry 缺失（E-135 生成物，跑 build_l3_registry.py 重建）")
    bad8 = []
    for e in proj8.get("entities", []):
        mn = e.get("map_node_id")
        ms = e.get("map_subnode")
        ml = e.get("map_l3")
        if mn and valid_nodes8 and mn not in valid_nodes8:
            bad8.append("node:%s->%s" % (e.get("id"), mn))
        # E-121（2026-08-13）：子域短名是值域权威，长名（panel.subnode）取末段归一化判断
        # ——投影既有短名也有长名历史数据，门闸只认短名会误杀（如 topology.governance）
        if ms and ms not in valid_subs8:
            _ms_short = ms.rsplit(".", 1)[-1] if "." in ms else ms
            if _ms_short not in valid_subs8:
                bad8.append("subnode:%s->%s" % (e.get("id"), ms))
        if ml and valid_l3_8 and ml not in valid_l3_8:
            bad8.append("l3:%s->%s" % (e.get("id"), ml))
    print("T8 三级坐标门: 实体=%d 子域值域=%d L3值域=%d 坏引用=%d" % (len(proj8.get("entities", [])), len(valid_subs8), len(valid_l3_8), len(bad8)))
    if bad8:
        fails.append("T8 three-level coordinate unknown refs: %s" % bad8[:5])
except Exception as e:
    fails.append("T8 unreadable (fail-closed): %s" % e)

# T8.5 manifest chain 坐标门（E-41 步3）：run-manifest 三段模板的 chain 段 coord ∈ 28 子域
# 一期 chain 槽位空 = 跳过不红；有内容则每条 coord 校验（面板.子域 格式，子域 ∈ valid_subs8）
try:
    mani85 = json.load(open(os.path.join(ROOT, "tools/agent-system-map/catalog/panel-manifest-v0.json"), encoding="utf-8"))
    valid_subs85 = set()
    for pn in mani85.get("panels", []):
        for sn in pn.get("subnodes", []):
            valid_subs85.add(sn.get("subnode_id") or sn.get("id"))
    bad85 = []
    checked85 = 0
    manifest_root = os.path.join(ROOT, "reports", "run-manifests")
    if os.path.isdir(manifest_root):
        for dirpath, _, files in os.walk(manifest_root):
            if "manifest.md" not in files:
                continue
            mpath = os.path.join(dirpath, "manifest.md")
            try:
                md85 = io.open(mpath, encoding="utf-8-sig").read()
            except (IOError, UnicodeDecodeError):
                continue
            if "## ② chain" not in md85 and "## chain" not in md85:
                continue
            # 只扫 ② chain 段区间（chain 标题行之后、下一个 ## 标题之前）——避免误抓 manifest 头部文件清单表格
            lines85 = md85.splitlines()
            seg_start = None
            for i85, l85 in enumerate(lines85):
                if l85.strip().startswith("## ② chain") or l85.strip().startswith("## chain"):
                    seg_start = i85 + 1
                    break
            if seg_start is None:
                continue
            for line in lines85[seg_start:]:
                if line.strip().startswith("## "):
                    break
                # 表格行：| # | t | action | coord | note | —— coord 是第 4 列
                m = re.match(r"^\|\s*(\d+)\s*\|[^|]*\|[^|]*\|\s*([^|]+?)\s*\|", line)
                if not m:
                    continue
                coord = m.group(2).strip()
                sub = coord.split(".")[-1]
                checked85 += 1
                if sub and sub not in valid_subs85:
                    bad85.append("%s:%s" % (os.path.basename(dirpath), coord))
    print("T8.5 manifest chain 坐标门: 链条目=%d 子域值域=%d 坏引用=%d" % (checked85, len(valid_subs85), len(bad85)))
    if bad85:
        fails.append("T8.5 manifest chain coord unknown refs: %s" % bad85[:5])
except Exception as e:
    fails.append("T8.5 unreadable (fail-closed): %s" % e)

# T8.x tools-registry 坐标门（2026-08-15 预判器 tools 修复）：
# 三形态合法：①长名子域（panel.subnode，node 与长名前缀一致）②短名子域（node 与短名所属面板一致，重复短名组合唯一）
#             ③板块直属（无子域 + panel_direct=true 显式标记 + node ∈ 11 面板）
# 红灯：半成品（无子域无 panel_direct）；矛盾标记（有子域带 panel_direct）；node 非法；子域非法
try:
    _tr8 = json.load(open(os.path.join(ROOT, "tools/agent-system-map/catalog/tools-registry-v0.json"), encoding="utf-8"))
    _tools8 = _tr8.get("tools", [])
    _valid_long8 = set()
    _valid_short8 = {}
    for _pn in mani8.get("panels", []):
        for _sn in _pn.get("subnodes", []):
            _sid = _sn.get("subnode_id") or _sn.get("id")
            _valid_long8.add(_pn.get("panel_id") + "." + _sid)
            _valid_short8.setdefault(_sid, set()).add(_pn.get("panel_id"))
    _bad_tr8 = []
    for _t in _tools8:
        _mn = _t.get("map_node_id")
        _ms = _t.get("map_subnode")
        _pd = _t.get("panel_direct")
        _tid = _t.get("tool_id")
        if not _ms:
            # 板块直属：无子域须显式 panel_direct 且 node ∈ 11 面板
            if not _pd:
                _bad_tr8.append("半成品(无子域无panel_direct):%s" % _tid)
            elif not _mn or _mn not in valid_nodes8:
                _bad_tr8.append("直属node非法:%s->%s" % (_tid, _mn))
            continue
        if _pd:
            _bad_tr8.append("矛盾标记(有子域带panel_direct):%s" % _tid)
        if not _mn or _mn not in valid_nodes8:
            _bad_tr8.append("node非法:%s->%s" % (_tid, _mn))
            continue
        _pid8 = _mn[len("map."):] if _mn.startswith("map.") else _mn
        if _ms in _valid_long8:
            if _ms.split(".", 1)[0] != _pid8:
                _bad_tr8.append("长名node不一致:%s->%s/%s" % (_tid, _ms, _mn))
        elif _ms in _valid_short8:
            if _pid8 not in _valid_short8[_ms]:
                _bad_tr8.append("短名node不符:%s->%s/%s" % (_tid, _ms, _mn))
        else:
            _bad_tr8.append("子域非法:%s->%s" % (_tid, _ms))
    print("T8.x tools 坐标门: 工具=%d 合法=%d 坏=%d %s" % (len(_tools8), len(_tools8) - len(_bad_tr8), len(_bad_tr8), _bad_tr8[:5]))
    if _bad_tr8:
        fails.append("T8.x tools-registry coordinate: %s" % _bad_tr8[:5])
except Exception as e:
    fails.append("T8.x unreadable (fail-closed): %s" % e)

# T8.y 双注册 lifecycle 映射门（2026-08-15 跨册一致性，v3 第 5 点）：
# 两册 lifecycle 是两套体系（tools=E-68 在用状态 2 档；asset=four-criteria 评估 4 档）。
# 映射语义：tools active↔asset managed/active；candidate↔candidate/pilot；deprecated↔deprecated。
# 唯一红灯 = deprecated 不一致（一方 deprecated 另一方非 deprecated）；asset None=未评估不红；维度并存差异（active↔pilot 等）合法。
try:
    _tr_y = json.load(open(os.path.join(ROOT, "tools/agent-system-map/catalog/tools-registry-v0.json"), encoding="utf-8"))
    _ar_y = json.load(open(os.path.join(ROOT, "tools/agent-system-map/catalog/webpage-asset-registry-v0.json"), encoding="utf-8"))
    _bad_ty = []
    _checked_ty = 0
    for _t in _tr_y.get("tools", []):
        _tid = _t.get("tool_id")
        if not _tid:
            continue
        _am = None
        for _a in _ar_y.get("assets", []):
            _aid = _a.get("asset_id") or ""
            # 精确匹配：asset_id 末段 == tool_id（asset:tool:quickdesk / asset:portal:rmux / asset:local:beads-dashboard）
            # 自动排除面板视图（skill-link-manager-panel）与非双注册（beads）
            if _aid.rsplit(":", 1)[-1] == _tid:
                _am = _a
                break
        if _am is None:
            continue  # 非双注册
        _tl = _t.get("lifecycle")
        _alraw = _am.get("lifecycle")
        _al = _alraw.get("status") if isinstance(_alraw, dict) else _alraw
        if _al is None:
            continue  # 未评估不红
        _checked_ty += 1
        _tdep = (_tl == "deprecated")
        _adep = (_al == "deprecated")
        if _tdep != _adep:
            _bad_ty.append("%s(tools=%s,asset=%s)" % (_tid, _tl, _al))
    print("T8.y 双注册 lifecycle 门: 双注册=%d deprecated不一致=%d %s" % (_checked_ty, len(_bad_ty), _bad_ty[:5]))
    if _bad_ty:
        fails.append("T8.y lifecycle mismatch: %s" % _bad_ty[:5])
except Exception as e:
    fails.append("T8.y unreadable (fail-closed): %s" % e)

# T8.z capability/skill 坐标完整门（E-136 P1-3）：快照中 capability:skill 实体 map_node_id 缺失数必须为 0
try:
    _snap_z = json.load(open(os.path.join(ROOT, "tools/agent-system-map/catalog/multi-source-snapshot-v2.json"), encoding="utf-8"))
    _skills_z = [e for e in _snap_z.get("entities", []) if e.get("kind") == "capability" and e.get("capability_kind") == "skill"]
    _missing_z = [e.get("id") for e in _skills_z if not e.get("map_node_id")]
    print("T8.z Skill 坐标门: 总数=%d 缺坐标=%d" % (len(_skills_z), len(_missing_z)))
    if _missing_z:
        fails.append("T8.z Skill 缺 map_node_id: %s (共 %d 个)" % (_missing_z[:3], len(_missing_z)))
except Exception as e:
    fails.append("T8.z unreadable (fail-closed): %s" % e)

# ---- T9 分册防复活门（E-118 2026-08-12）：thread-standards 下新增含「分册」文件名即 FAIL ----
# 豁免：现存三本岗位法分册 + .bak* 文件
try:
    t9_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "thread-standards")
    t9_exempt = {
        "常驻标准-分册-skill-manager.md",
        "常驻标准-分册-mcp-manager.md",
        "常驻标准-分册-provider-manager.md",
    }
    t9_bad = []
    for fname in os.listdir(t9_dir):
        if "分册" in fname and fname not in t9_exempt and not fname.startswith(".") and not fname.endswith(".bak"):
            # 允许 .bak-e118 等备份文件与隐藏文件
            if ".bak" in fname:
                continue
            t9_bad.append(fname)
    print("T9 分册防复活门: 扫描=%d 复活=%d 豁免=3+备份" % (len(os.listdir(t9_dir)), len(t9_bad)))
    if t9_bad:
        fails.append("T9 分册复活: %s" % t9_bad[:5])
except Exception as e:
    fails.append("T9 unreadable (fail-closed): %s" % e)

# T10 guardian curated 注册表门闸（E-121 2026-08-13）：每条 entity 的 coordinate.map_node_id ∈ 11 面板
# 且 lifecycle ∈ 五态 且 evidence 非空。fail-closed（读不到 registry=FAIL，同 T4/T5 风格）。
# 验收口径（cc1 审边界1）：merged/retired 空态允许（零实例不算 FAIL）；coordinate 是嵌套字段。
try:
    greg10 = json.load(io.open(os.path.join(ROOT, "tools", "guardian", "guardian-tasks-registry-v0.json"), encoding="utf-8"))
    mani10 = json.load(io.open(os.path.join(ROOT, "tools/agent-system-map/catalog/panel-manifest-v0.json"), encoding="utf-8"))
    valid_nodes10 = set()
    for n in mani10.get("map_nodes", []):
        valid_nodes10.add(n if isinstance(n, str) else n.get("id"))
    if not valid_nodes10:
        valid_nodes10 = {"map." + pn.get("panel_id") for pn in mani10.get("panels", []) if pn.get("panel_id")}
    valid_lc10 = set(greg10.get("lifecycle_states", []))
    bad10 = []
    for e in greg10.get("entities", []):
        coord = e.get("coordinate") or {}
        node = coord.get("map_node_id")
        if node and valid_nodes10 and node not in valid_nodes10:
            bad10.append("node:%s->%s" % (e.get("id"), node))
        if e.get("lifecycle") not in valid_lc10:
            bad10.append("lifecycle:%s->%s" % (e.get("id"), e.get("lifecycle")))
        if not (e.get("evidence") or "").strip():
            bad10.append("evidence-empty:%s" % e.get("id"))
    print("T10 guardian curated 门闸: 实体=%d 面板值域=%d 五态值域=%d 坏引用=%d" % (len(greg10.get("entities", [])), len(valid_nodes10), len(valid_lc10), len(bad10)))
    if bad10:
        fails.append("T10 guardian curated unknown refs: %s" % bad10[:5])
except Exception as e:
    fails.append("T10 unreadable (fail-closed): %s" % e)


# D2 执行单模板合规（E-127）：工作区「执行单-*.md」必含「沉淀预声明」栏（机器卡，不靠人提醒）
try:
    wd = "C:/Users/64998/WorkBuddy/2026-07-27-12-30-29"
    bad_d2 = []
    n_d2 = 0
    if os.path.isdir(wd):
        for fn in sorted(os.listdir(wd)):
            if fn.startswith("执行单-") and fn.endswith(".md"):
                # E-127 模板标记判断：内容含「沉淀预声明」=新模板单才查（旧单不追责，Cairn legacy 原则；
                # 2026-08-15 修正：不用文件名日期——升级模板的旧名任务（如 E-108 20260812 单 0815 升级）也得查）
                body = open(os.path.join(wd, fn), encoding="utf-8-sig").read()
                if "沉淀预声明" not in body:
                    continue
                n_d2 += 1
                if "⑥" not in body:
                    bad_d2.append(fn)
    print("D2 执行单模板合规: 扫描=%d 缺沉淀预声明=%d" % (n_d2, len(bad_d2)))
    if bad_d2:
        fails.append("D2 缺沉淀预声明: %s" % bad_d2[:5])
except Exception as e:
    fails.append("D2 unreadable: %s" % e)


# D3 沉淀对账门（E-127）：扫含⑥标记执行单跑沉淀对账（挂点②调用点，旧单不追责）
try:
    r_d3 = subprocess.run([sys.executable, os.path.join(ROOT, "tools/agent-system-map/check_knowledge_reflux.py"), "--scan-new"],
                          capture_output=True, text=True, timeout=120)
    print(r_d3.stdout.strip())
    if r_d3.returncode != 0:
        fails.append("D3 沉淀对账 FAIL (rc=%d)" % r_d3.returncode)
except Exception as e:
    fails.append("D3 unreadable: %s" % e)

# D4 收尾报告四灯门（E-127 P1 修复 2026-08-15）：扫工作区收尾报告，含「四灯」标记=新模板报告，
# 必含四类灯关键词（①收尾日志 ②沉淀落点 ③矛盾 ④画像源），缺=红灯（交报告卡机器化，旧报告无标记不追责）
try:
    wd_d4 = "C:/Users/64998/WorkBuddy/2026-07-27-12-30-29"
    bad_d4 = []
    n_d4 = 0
    lamps = ("收尾日志", "沉淀落点", "矛盾", "画像源")
    if os.path.isdir(wd_d4):
        for fn in sorted(os.listdir(wd_d4)):
            if not (fn.endswith(".md") and (fn.startswith("回执-") or fn.startswith("结果-"))):
                continue
            body = open(os.path.join(wd_d4, fn), encoding="utf-8-sig").read()
            if "四灯" not in body and "沉淀落点" not in body:
                continue  # 旧报告无标记不追责
            n_d4 += 1
            missing = [k for k in lamps if k not in body]
            if missing:
                bad_d4.append("%s缺[%s]" % (fn, "/".join(missing)))
    print("D4 收尾报告四灯: 扫描=%d 缺灯=%d" % (n_d4, len(bad_d4)))
    if bad_d4:
        fails.append("D4 收尾报告缺灯: %s" % bad_d4[:5])
except Exception as e:
    fails.append("D4 unreadable: %s" % e)

# D5 标准漂移检测挂点（E-129 P1，Cairn U1 落地）：普通标准头部必含 skill_spec_date（修订必更字段）+ 值须为合法日期（E-131 审核补强：只查 key 存在不查值=弱检查）
try:
    std_path = r"F:\codex\docs\thread-standards\普通标准.md"
    if os.path.exists(std_path):
        head = open(std_path, encoding="utf-8-sig").read()[:500]
        m_ssd = re.search(r"skill_spec_date[：:]\s*(\d{4}-\d{2}-\d{2})", head)
        if not m_ssd:
            fails.append("D5 普通标准缺/坏 skill_spec_date（须 YYYY-MM-DD，修订必更字段，E-129）")
        else:
            print("D5 标准漂移挂点: skill_spec_date=%s 合法" % m_ssd.group(1))
    else:
        fails.append("D5 普通标准文件缺失")
except Exception as e:
    fails.append("D5 unreadable: %s" % e)

# D6 topic/判例内容校验（E-131 接通点）：topic-registry type 值域+必填字段+id 唯一；判例三则 p021-023 存在防丢
try:
    tp_path = os.path.join(ROOT, "tools/agent-system-map/catalog/topic-registry-v0.json")
    if os.path.exists(tp_path):
        tp = json.load(open(tp_path, encoding="utf-8-sig"))
        ents = tp.get("entries", [])
        bad_tp = []
        ids = set()
        allowed_type = ("conclusion", "lesson", "decision-why")
        for e in ents:
            if e.get("id") in ids:
                bad_tp.append("%s:dup-id" % e.get("id"))
            ids.add(e.get("id"))
            if e.get("type") not in allowed_type:
                bad_tp.append("%s:bad-type" % e.get("id"))
            for f in ("title", "contains", "source"):
                if not e.get(f):
                    bad_tp.append("%s:missing-%s" % (e.get("id"), f))
        print("D6 topic/判例: topic=%d 坏=%d" % (len(ents), len(bad_tp)))
        if bad_tp:
            fails.append("D6 topic 坏: %s" % bad_tp[:5])
        # 判例三则防丢
        pr_path = os.path.join(ROOT, "tools/agent-system-map/catalog/precedent-registry-v0.json")
        pr = json.load(open(pr_path, encoding="utf-8-sig"))
        pr_ids = {x.get("id") for x in pr.get("precedents", [])}
        miss = [x for x in ("p021", "p022", "p023") if x not in pr_ids]
        print("D6 判例三则: p021-023 %s" % ("在位" if not miss else "缺失%s" % miss))
        if miss:
            fails.append("D6 判例三则缺失: %s" % miss)
        # E-138/Cairn P1（2026-08-16）：判例 fresh_until 必填（陈旧检测 J）+ p021-023 内容关键词校验（A/C/E 机器化防改空）
        _bad_pr = []
        for _x in pr.get("precedents", []):
            if not _x.get("fresh_until"):
                _bad_pr.append("%s:no-fresh_until" % _x.get("id"))
        _kw_map = {
            "p021": ("引述", "speaker", "角色", "日期", "脱敏"),
            "p022": ("不静默覆盖", "就地更正", "留痕", "pending"),
            "p023": ("保守侧", "可恢复"),
        }
        for _x in pr.get("precedents", []):
            _pid = _x.get("id")
            if _pid in _kw_map:
                _content = _x.get("判定内容") or ""
                if not any(k in _content for k in _kw_map[_pid]):
                    _bad_pr.append("%s:内容缺关键词" % _pid)
        if _bad_pr:
            fails.append("D6 判例字段/内容坏: %s" % _bad_pr[:5])
        print("D6 判例增强: fresh_until全=%s p021-023内容=%s" % (
            "✅" if not [b for b in _bad_pr if "no-fresh" in b] else "❌",
            "✅" if not [b for b in _bad_pr if "内容" in b] else "❌"))
    else:
        fails.append("D6 topic-registry 缺失")
except Exception as e:
    fails.append("D6 unreadable: %s" % e)

# D9 map_l3 值域门（E-135）：manual-registry 条目挂的 map_l3 必须 ∈ l3-registry（okf 当前 map_l3=0 自动覆盖；凭据类由 T8 全量覆盖，不重复管 inventory 防双头）
try:
    l3r_p = os.path.join(ROOT, "tools/agent-system-map/catalog/l3-registry-v0.json")
    if os.path.exists(l3r_p):
        _l3ids = {e.get("l3_id") for e in json.load(open(l3r_p, encoding="utf-8-sig")).get("entries", [])}
        _mreg = json.load(open(os.path.join(ROOT, "tools/agent-system-map/catalog/manual-registry-v0.json"), encoding="utf-8-sig"))
        bad_l3 = []
        for _me in _mreg.get("entries", []):
            _ml = _me.get("map_l3")
            if _ml and _ml not in _l3ids:
                bad_l3.append("%s->%s" % (_me.get("manual_id"), _ml))
        print("D9 map_l3 值域: manual 挂 L3=%d 坏=%d" % (sum(1 for e in _mreg.get("entries", []) if e.get("map_l3")), len(bad_l3)))
        if bad_l3:
            fails.append("D9 map_l3 坏值域: %s" % bad_l3[:5])
    else:
        fails.append("D9 l3-registry 缺失（E-135）")
except Exception as e:
    fails.append("D9 unreadable: %s" % e)

# D7 per-bead 体检门（E-131 接通点）：per_bead_check --scan-all 挂主链（与 D3 互补：D3 查沉淀，D7 查四灯）
try:
    r_d7 = subprocess.run([sys.executable, os.path.join(ROOT, "tools/agent-system-map/per_bead_check.py"), "--scan-all"],
                          capture_output=True, text=True, timeout=120)
    out_d7 = r_d7.stdout.strip().splitlines()
    tail_d7 = out_d7[-1] if out_d7 else ""
    print("D7 体检门: " + tail_d7)
    if r_d7.returncode != 0 or "全绿" not in tail_d7:
        fails.append("D7 体检 FAIL (rc=%d, tail=%s)" % (r_d7.returncode, tail_d7))
except Exception as e:
    fails.append("D7 unreadable: %s" % e)

# D8 坐标审核闸（E-133 魅惑令：坐标审核一步不能省，越批越准）：manual/okf 新沉淀坐标 pending>0=红灯
try:
    r_d8 = subprocess.run([sys.executable, os.path.join(ROOT, "tools/agent-system-map/check_coord_approval.py"), "--check"],
                          capture_output=True, text=True, timeout=60)
    out_d8 = r_d8.stdout.strip().splitlines()
    print("D8 " + (out_d8[0] if out_d8 else "no-output"))
    if r_d8.returncode != 0:
        fails.append("D8 坐标审核 FAIL: 有待审坐标（需魅惑批）")
except Exception as e:
    fails.append("D8 unreadable: %s" % e)

# D1 database-registry 门闸（E-123 独立脚本，E-126 体检整改②挂主链；不占 T 编号）
try:
    spec_d1 = importlib.util.spec_from_file_location("tdr", os.path.join(ROOT, r"tools\agent-system-map\test_database_registry.py"))
    tdr = importlib.util.module_from_spec(spec_d1)
    spec_d1.loader.exec_module(tdr)
    rc_d1 = tdr.main()
    if rc_d1 != 0:
        fails.append("D1 database-registry FAIL (rc=%d)" % rc_d1)
    else:
        print("D1 database-registry: PASS（挂主链）")
except Exception as e:
    fails.append("D1 unreadable (fail-closed): %s" % e)

# D10 E 编号撞号检测门（E-136 撞号教训，2026-08-17 总控1）：git 提交信息里单 E 号出现次数
# > 8 视为疑似多任务共用同号 → 红灯。机器自动，替代人工注册台账（审核-E编号注册制必要性）。
# 编号 D10（非 D9）：D9 已被 E-135 map_l3 值域门占用，初版误用 D9 = 门编号撞号，2026-08-17 再审核修正。
try:
    spec_d10 = importlib.util.spec_from_file_location("encc", os.path.join(ROOT, r"tools\agent-system-map\check_e_number_collision.py"))
    encc = importlib.util.module_from_spec(spec_d10)
    spec_d10.loader.exec_module(encc)
    rc_d10 = encc.main(argv=None)  # 挂主链不传参（防 argparse 解析主链 sys.argv 崩，2026-08-17 审核修复）
    if rc_d10 != 0:
        fails.append("D10 E-number collision WARN (rc=%d，疑似撞号，见上)" % rc_d10)
except Exception as e:
    fails.append("D10 unreadable (fail-closed): %s" % e)

if fails:
    print("INTEGRITY FAIL:", fails)
    sys.exit(1)
print("INTEGRITY TESTS ALL PASS")
