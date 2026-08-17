# -*- coding: utf-8 -*-
# suggest_task_context.py — 任务创建推荐器（坐标→供给自动撮合，E-29）
# 纯查表工具：输入 map_subnode（必填）+ dac（可选）+ 标题（可选）→ 输出该子域的
# skill/MCP/资产推荐 + effort 预估提示 + 可直接拼 bd create 的 metadata 五字段。
# 权威源（只读消费，禁止现查现猜）：skill-panel-mapping-v0 / mcp-skill-map +
# mcp_manager/registry.json / webpage-asset-registry-v0 / panel-manifest-v0 /
# data-asset-taxonomy-v0 / okf effort-rules-v0。
# 不做 LLM 智能推荐；不接 bd 创建钩子（创建前人工/线程调用的助手）。

import argparse, json, os, sys, glob, re, datetime

ROOT = r'F:\codex'
CAT = os.path.join(ROOT, r'tools\agent-system-map\catalog')
REPORTS = os.path.join(ROOT, 'reports')
RUN_MANIFESTS = os.path.join(REPORTS, 'run-manifests')
OKF_BUNDLES = os.path.join(ROOT, 'okf-bundles')

SRC = {
    'skill_map': os.path.join(CAT, 'skill-panel-mapping-v0.json'),
    'mcp_skill': os.path.join(CAT, 'mcp-skill-map.json'),
    'mcp_reg':   os.path.join(ROOT, r'tools\mcp_manager\registry.json'),
    'assets':    os.path.join(CAT, 'webpage-asset-registry-v0.json'),
    'panels':    os.path.join(CAT, 'panel-manifest-v0.json'),
    'taxonomy':  os.path.join(CAT, 'data-asset-taxonomy-v0.json'),
    'effort':    os.path.join(ROOT, r'okf-bundles\beads-classification\effort-rules-v0.json'),
    'manual_reg': os.path.join(CAT, 'manual-registry-v0.json'),
    'cred_reg': os.path.join(ROOT, r'tools\credential_registry\credential-inventory-v0.json'),
    'tools_reg':  os.path.join(CAT, 'tools-registry-v0.json'),
    'chain_matrix': os.path.join(REPORTS, r'E47-supply-census\chain-matrix-fourset-20260807.json'),
}

# panel_id -> map_node_id（2026-08-07 E-46 后改短名——长名方言只准历史文档；新面板 projects）
PANEL_TO_MAP = {
    'topology': 'map.topology', 'guardian': 'map.guardian',
    'beads': 'map.beads', 'projects': 'map.projects',
    'hardware': 'map.hardware', 'search': 'map.search', 'harness': 'map.harness',
    'agent-hub': 'map.agent-hub', 'skill-manager': 'map.skill-manager',
    'mcp-manager': 'map.mcp-manager', 'provider-manager': 'map.provider-manager',
}

def load(p):
    with open(p, encoding='utf-8-sig') as f:
        return json.load(f)


def _coord_hit(obj, node, subnode_id, full_subnode, panel_level=False):
    """坐标命中判定：面板先过滤 + 短/长双条件 + 板块直属（2026-08-15 预判器 tools 修复）
    node = map.xxx 长名；subnode_id = 短名；full_subnode = panel.subnode 长名
    panel_level=True（tools 段）：map_subnode 为空 + panel_direct 显式标记 + 面板命中 → 板块直属命中
    panel_level=False（asset 段）：无子域一律不命中（防 86 条无坐标资产膨胀）"""
    mn = obj.get('map_node_id') or ''
    ms = obj.get('map_subnode') or ''
    if not ms:
        # 板块直属：无子域 + 显式 panel_direct + 面板命中（对齐 manual L96-98 / credential L325 板块级先例）
        return panel_level and bool(obj.get('panel_direct')) and bool(mn) and mn == node
    if mn and mn != node:
        return False  # 面板先过滤（防 governance/panel 等重复短名跨面板串味）
    return ms == full_subnode or ms == subnode_id  # 双条件：长名 或 短名


# ---- E-48 画像 2.0：四源计算式组装（全部实读，禁手写画像内容） ----

# 链矩阵意图关键词 → 链名（chain-matrix-fourset 的 key）
CHAIN_KEYWORDS = [
    ("浏览器自动化", ["browser", "playwright", "爬虫", "浏览器自动化", "webctl"]),
    ("grok 生图", ["grok", "生图", "image-generator", "x.ai", "5yuan"]),
    ("搜索", ["搜索", "search", "anysearch", "tavily", "firecrawl", "brightdata"]),
    ("figma", ["figma", "插件导出", "figmapat"]),
    ("微信发布", ["微信", "wechat", "公众号", "weixin"]),
    ("OCR", ["ocr", "识别", "文字提取", "图片转"]),
    ("视频", ["视频", "video", "youtube", "bilibili", "opencut"]),
    ("tts", ["tts", "语音", "朗读", "配音"]),
    ("cloudflare", ["cloudflare", "cf", "wrangler", "worker", "tunnel"]),
]


def match_chain(title):
    """意图关键词 → 链（chain-matrix-fourset 的 key）"""
    if not title:
        return None
    tl = title.lower()
    for cname, kws in CHAIN_KEYWORDS:
        if any(k.lower() in tl for k in kws):
            return cname
    return None


def build_profile(args, panel_id, subnode_id, manual_reg, chain_matrix):
    """画像 2.0：四源现场组装（知识/资源/难度/验收参考）"""
    node = PANEL_TO_MAP.get(panel_id, panel_id)
    lines = []
    lines.append(f"画像（按坐标 {node}.{subnode_id} 组装）：")
    # E-111 增补5 注记：画像建议≠归属计数——子域级查询收 L3 挂载是建议行为（取可用资源），
    # 不是归属统计（每个对象的真实坐标位以注册表为准，不因画像查询改变）

    # ---- 1) 知识：必读（manual-registry 反查 + OKF registry 匹配） ----
    # E-105（2026-08-12）：三级匹配——面板级 + 子域级 + L3 级（L3 挂载按子域两级取）
    # 匹配规则：①面板级手册（无子域）通用必读 ②精确子域匹配 ③子域下带 L3 的挂载也取（两级取）
    # ④--l3 指定时精确匹配该 L3
    l3_target = getattr(args, 'l3', None) or None
    manuals = []
    for e in manual_reg:
        if not e.get('map_number'):
            continue
        mn = e.get('map_node_id')
        ms = e.get('map_subnode')
        ml = e.get('map_l3')
        if mn != node:
            continue
        # 面板级手册（无子域）: 通用必读，保留
        if not ms:
            manuals.append(e)
            continue
        # 子域级匹配
        if ms == subnode_id:
            # --l3 指定时：只保留 L3 精确命中的（子域内 L3 过滤）
            if l3_target and ml and ml == l3_target:
                manuals.append(e)
            elif l3_target and not ml:
                pass  # 子域级无 L3 手册在 --l3 精确查询时不返回
            else:
                manuals.append(e)
            continue
        # --l3 精确匹配：L3 挂载（子域不同但 L3 相同——如 beads.intake 查 intake.wechat）
        if l3_target and ml == l3_target:
            manuals.append(e)
            continue
        # 子域下 L3 挂载两级取：该子域下带 L3 的手册（未指定 --l3 时）
        # （当前子域内已按 ms==subnode_id 全取，含 L3 的；此处覆盖跨子域 L3 同名的场景）
        if not l3_target and ml and ms.split('.')[-1] == subnode_id.split('.')[-1]:
            manuals.append(e)
    okf_hits = []
    # E-69：改读 okf-registry-v0.json（优先）——glob 扫头部为 fallback
    reg_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'catalog', 'okf-registry-v0.json')
    if os.path.isfile(reg_p):
        try:
            reg = json.load(open(reg_p, encoding='utf-8-sig'))
            okf_hits = [b['bundle_id'] for b in reg.get('bundles', [])
                        if (b.get('map_subnode') or '').strip() == subnode_id or
                        ((b.get('coords_raw') or '') and subnode_id in b.get('coords_raw', ''))]
        except Exception:
            okf_hits = []
    if not okf_hits:
        for p in glob.glob(os.path.join(OKF_BUNDLES, '*', 'index.md')):
            try:
                head = open(p, encoding='utf-8', errors='ignore').read(1200)
            except Exception:
                continue
            if '> 坐标：' in head and subnode_id in head:
                okf_hits.append(os.path.basename(os.path.dirname(p)))
    if manuals or okf_hits:
        names = []
        for e in manuals[:5]:
            names.append("%s(№%s)" % (e.get('manual_id', '?'), e.get('map_number')))
        names += ["okf:%s" % o for o in okf_hits[:5]]
        lines.append(f"  必读：{'、'.join(names)}")
    else:
        lines.append("  必读：无参考（该坐标无登记手册/OKF）")

    # ---- 2) 资源：E-47 链矩阵命中（skill/MCP/凭据环节 + 断点警示） ----
    cname = match_chain(args.title)
    if chain_matrix and cname and cname in chain_matrix.get('chains', {}):
        c = chain_matrix['chains'][cname]
        judge = c.get('判定', '?')
        mark = '🔴' if judge.startswith('🔴') else ('🟡' if judge.startswith('🟡') else '🟢')
        cred = c.get('凭据(CM/配置)') or []
        skill = c.get('skill') or []
        mcp = c.get('MCP') or []
        mcp_map = c.get('MCP映射') or []
        lines.append(f"  资源：命中「{cname}」链 {mark}（{judge}）")
        if cred:
            lines.append(f"    凭据在 CM：{'、'.join(cred[:5])}")
        if skill:
            lines.append(f"    skill：{'、'.join(skill[:5])}")
        if mcp:
            mm = "、".join(mcp)
            unmapped = [x for x in mcp if x not in mcp_map]
            if unmapped:
                lines.append(f"    MCP：{mm}——⚠️ 未挂映射：{'、'.join(unmapped)}，开工前先补挂")
            else:
                lines.append(f"    MCP：{mm}（已挂映射）")
        elif judge.startswith('🔴'):
            lines.append(f"    MCP：缺（{judge}——本地工具/CLI 型或凭据缺失）")
    else:
        lines.append("  资源：无链命中（全新意图——首单无历史参考）")

    # ---- 3) 难度：run-manifests 相似任务 actual_minutes（近 3 条） ----
    sims = []
    if os.path.isdir(RUN_MANIFESTS):
        for mp in glob.glob(os.path.join(RUN_MANIFESTS, '*', 'manifest.md')):
            try:
                txt = open(mp, encoding='utf-8', errors='ignore').read(6000)
            except Exception:
                continue
            if ('map_subnode: ' + subnode_id) not in txt and ('map_subnode: ' + args.subnode) not in txt:
                continue
            m = re.search(r'actual_minutes:\s*(\d+)', txt)
            if m:
                sims.append((os.path.basename(os.path.dirname(mp)), int(m.group(1))))
    sims = sims[-3:]
    if sims:
        parts = ["%s(%d分)" % (tid, mins) for tid, mins in sims]
        lines.append("  难度：上次类似活花了 %s——预估按 effort 六格自估" % "、".join(parts))
    else:
        lines.append("  难度：首单无历史参考（该坐标无 close 任务 manifest）——按 effort-rules 判据自估")

    # ---- 4) 验收参考：相似任务 manifest 的 landing/验收信息 ----
    acc = []
    for tid, mins in sims:
        mp = os.path.join(RUN_MANIFESTS, tid, 'manifest.md')
        if os.path.isfile(mp):
            txt = open(mp, encoding='utf-8', errors='ignore').read()
            cm = re.search(r'conclusion:\s*(.+)', txt)
            if cm:
                acc.append("%s 验收点：%s" % (tid, cm.group(1)[:80]))
    if acc:
        lines.append("  验收参考：" + " | ".join(acc[:2]))
    else:
        lines.append("  验收参考：无参考（首单，验收点自拟交总控）")

    return lines


def main():
    ap = argparse.ArgumentParser(description='任务创建推荐器（坐标→供给）')
    ap.add_argument('--subnode', required=True, help='map_subnode，如 search.tools / beads.project-assets')
    ap.add_argument('--l3', help='三级坐标 L3 层（E-64 新增，如 browser:cloudflare）')
    ap.add_argument('--dac', help='dac 可选，如 dac:tuwen')
    ap.add_argument('--title', help='任务标题（可选，用于 effort 规则关键词命中）')
    ap.add_argument('--effort', help='effort 六格（simple-fast 等），给则 metadata 直接填')
    ap.add_argument('--effort-reason', help='effort 理由（给 effort 时必填）')
    ap.add_argument('--produces-asset', help='产物预言一句话（E-41 步2，如 "codegraph.db(rebuilt)"）')
    ap.add_argument('--guardian-need', choices=['none', 'candidate', 'immediate'], help='守护需求（E-41 步2）')
    ap.add_argument('--priority', choices=['P0', 'P1', 'P2', 'P3'], help='优先级（E-41 步2，默认 P2）')
    ap.add_argument('--risk', choices=['readonly', 'small-write', 'structural'], help='风险级（E-41 步2，默认 readonly）')
    ap.add_argument('--origin', choices=['appointed', 'self-claimed', 'auto'], help='来源（E-41 步2，默认 appointed）')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    ap.add_argument('--profile', action='store_true', help='画像 2.0：四源现场组装（知识/资源/难度/验收参考）')
    args = ap.parse_args()

    # ---- 加载权威源（只读） ----
    try:
        sm = load(SRC['skill_map']); ms = sm['mappings']
        mcp_map = load(SRC['mcp_skill'])['mcps']
        mcp_reg = load(SRC['mcp_reg'])['servers']
        assets = load(SRC['assets'])['assets']
        panels = load(SRC['panels'])['panels']
        taxo = load(SRC['taxonomy'])
        effort = load(SRC['effort'])
        manual_reg = load(SRC['manual_reg'])['entries']
        chain_matrix = load(SRC['chain_matrix']) if os.path.isfile(SRC['chain_matrix']) else None
    except Exception as e:
        print(f'[err] 权威源加载失败: {e}', file=sys.stderr); sys.exit(1)

    # ---- 值域校验（32 子域 / 6 dac） ----
    subnodes = []
    for p in panels:
        for s in (p.get('subnodes') or []):
            subnodes.append(f"{p['panel_id']}.{s.get('subnode_id')}")
    if args.subnode not in subnodes:
        print(f"[err] 子域 '{args.subnode}' 不在合法值域（25 子域）。合法: ", file=sys.stderr)
        print('  ' + ' / '.join(subnodes), file=sys.stderr)
        sys.exit(2)
    dac_ids = [c['id'] for c in (taxo.get('data_asset_classes') or [])]
    if args.dac and args.dac not in dac_ids:
        print(f"[err] dac '{args.dac}' 不在合法值域（6 类）: {dac_ids}", file=sys.stderr)
        sys.exit(2)

    panel_id, subnode_id = args.subnode.split('.', 1)

    # ---- 1. skill 推荐（skill-panel-mapping，只读消费） ----
    skills = []
    for m in ms:
        pre = m['panel_subnode'].split('[')[0].strip()
        if pre == args.subnode:
            if args.dac and m.get('dac') and m['dac'] != args.dac:
                continue
            skills.append({'skill': m['skill'], 'dac': m.get('dac')})
    # 标题关键词补充排序（在册者为准，仅排序不新增）
    if args.title:
        def _hit(x):
            return sum(1 for w in args.title.lower().split() if w in x['skill'].lower())
        skills.sort(key=lambda x: -_hit(x))

    # ---- 2. MCP 推荐（mcp-skill-map + registry） ----
    skill_names = {s['skill'] for s in skills}
    mcps = []
    for name, meta in mcp_map.items():
        linked = set(meta.get('skills') or [])
        hit = linked & skill_names
        if hit:
            st = mcp_reg.get(name, {})
            mcps.append({'mcp': name,
                         'status': st.get('status', '?') if isinstance(st, dict) else '?',
                         'linked_skills': sorted(hit),
                         'panel': meta.get('panel')})

    # ---- 3. 资产（webpage-asset-registry + tools-registry E-68 + manual E-105） ----
    node = PANEL_TO_MAP.get(panel_id, panel_id)  # 2026-08-15 上移：asset/tools 段 _coord_hit 需先取 node
    asset_hits = [a for a in assets if _coord_hit(a, node, subnode_id, args.subnode, panel_level=False)]
    # E-105（2026-08-12）：手册挂载进资产画像——manual-registry 三级匹配（面板/子域/L3）
    for e in manual_reg:
        if not e.get('map_number'):
            continue
        mn = e.get('map_node_id')
        ms = e.get('map_subnode')
        ml = e.get('map_l3')
        l3_target = getattr(args, 'l3', None) or None
        if mn != node:
            continue
        if l3_target:
            # --l3 精确模式：只返回带该 L3 的手册（子域内外的 L3 挂载）
            if ml == l3_target:
                asset_hits.append({'asset_id': e.get('manual_id'), 'type': 'manual',
                                   'panel': mn, 'map_subnode': ms or None, 'map_l3': ml or None})
            continue
        # 非 --l3：面板级手册 + 子域级手册
        if not ms or ms == subnode_id:
            asset_hits.append({'asset_id': e.get('manual_id'), 'type': 'manual',
                               'panel': mn, 'map_subnode': ms or None, 'map_l3': ml or None})
    # E-113（2026-08-12）：凭据挂载进资产画像——credential-inventory 三级匹配（仅指针零明文）
    try:
        cred_reg = load(SRC['cred_reg'])['credentials']
    except Exception:
        cred_reg = []
    l3t = getattr(args, 'l3', None) or None
    for ce in cred_reg:
        if not ce.get('map_node_id') or not ce.get('map_subnode'):
            continue  # ❓ 未填坐标（待魅惑裁）
        cmn, cms, cml = ce.get('map_node_id'), ce.get('map_subnode'), ce.get('map_l3')
        if cmn != node:
            continue
        if l3t:
            if cml == l3t:
                asset_hits.append({'asset_id': 'cred:' + ce.get('cred_id'), 'type': 'credential',
                                   'panel': cmn, 'map_subnode': cms, 'map_l3': cml,
                                   'storage_ref': ce['storage'][0]['ref'] if ce.get('storage') else None,
                                   'cred_status': ce.get('status')})
            continue
        if not cms or cms == subnode_id or cms == args.subnode:  # 兼容短/长两种子域格式
            asset_hits.append({'asset_id': 'cred:' + ce.get('cred_id'), 'type': 'credential',
                               'panel': cmn, 'map_subnode': cms, 'map_l3': cml,
                               'storage_ref': ce['storage'][0]['ref'] if ce.get('storage') else None,
                               'cred_status': ce.get('status')})

    # E-68：tools-registry 工具并入资产画像（tools-windows/tools-browser 等坐标返回已登记工具）
    tools = []
    try:
        tools = load(SRC['tools_reg'])['tools']
    except Exception:
        tools = []
    tool_hits = [t for t in tools if _coord_hit(t, node, subnode_id, args.subnode, panel_level=True)]
    # E-123（2026-08-14）：database-registry 数据库并入资产画像（E-126 Cairn 体检整改②）
    # ——数据库按坐标匹配，返回 path/size/engine，让新注册的库对任务可见
    databases = []
    try:
        databases = load(os.path.join(CAT, 'database-registry-v0.json'))['entries']
    except Exception:
        databases = []
    db_hits = []
    for db in databases:
        coord = db.get('map_coordinate') or {}
        if isinstance(coord, str):
            parts = coord.split('.')
            cms = '.'.join(parts[2:]) if len(parts) >= 3 else None
            cmn = parts[1] if len(parts) >= 2 else None
        else:
            cms = coord.get('map_subnode')
            cmn = coord.get('map_node_id')
        cms_full = (cmn + '.' + cms) if (cmn and cms) else cms
        if cms and (cms == args.subnode or cms_full == args.subnode):
            db_hits.append({'asset_id': 'db:' + db.get('id'), 'type': 'database',
                            'panel': (coord.get('map_node_id') if isinstance(coord, dict) else (parts[1] if len(parts) >= 2 else None)),
                            'map_subnode': cms, 'path': db.get('path'),
                            'engine': db.get('engine'), 'lifecycle': db.get('lifecycle')})
    if db_hits:
        asset_hits.extend(db_hits)
    # E-122（2026-08-14）：tools-registry 已删 map_l3，工具 L3 改为从语义域派生
    # ——按 map_subnode 反查 data-asset-taxonomy 的 l3_mapping（方案①，魅惑批）
    def _tool_l3_for(subnode, tool=None):
        # taxonomy 顶层 l3_mapping：{subnode: {map: {分类: dac}}}（E-67 权威）
        # 工具无自身 L3，默认 exe/工具类=dac:ruanjian；--dac 过滤按此派生
        # E-136（2026-08-16）：细分支持——tool_class 命中 map 键时优先（如 Everything GUI
        # tool_class=本地搜索工具 → offline:everything），否则回退子域默认 exe/工具类
        entry = (taxo.get('l3_mapping') or {}).get(subnode) or {}
        inner = entry.get('map') or {}
        if not inner:
            return None
        if tool:
            cls = tool.get('tool_class')
            if cls and cls in inner:
                return inner[cls]
        return inner.get('exe/工具类') or inner.get('工具类') or next(iter(inner.values()), None)
    # E-105（2026-08-12）：工具 L3 级匹配——子域匹配外，--l3 指定时精确匹配 L3 挂载工具
    # E-122 改造：工具自身无 map_l3，用语义域派生 L3 匹配（查不到=不命中）
    l3_arg = getattr(args, 'l3', None) or None
    if l3_arg:
        l3_tool_hits = [t for t in tool_hits if _tool_l3_for(t.get('map_subnode'), t) == l3_arg]
        if not l3_tool_hits:
            print(f"  [提示] --l3 {l3_arg} 无挂载工具（精确查询返回空，不扩大到子域全量）", file=sys.stderr)
        tool_hits = l3_tool_hits
    if args.dac:
        asset_hits = [a for a in asset_hits if a.get('data_asset_class') == args.dac]
        tool_hits = [t for t in tool_hits if _tool_l3_for(t.get('map_subnode'), t) == args.dac]

    # ---- 4. effort 提示（effort-rules-v0：criteria_v3 + P1-P10 关键词命中） ----
    rules = effort.get('rules', [])
    hits = []
    if args.title:
        for r in rules:
            pat = r.get('pattern', '')
            # 从 pattern 提取关键词（按分隔符拆分，做子串匹配）
            for kw in pat.replace('（', ' ').replace('）', ' ').replace('(', ' ').replace(')', ' ').split():
                kw = kw.strip()
                if len(kw) >= 2 and kw in args.title:
                    hits.append((r['id'], r['pattern'], r.get('effort'), kw))
                    break
    effort_prompt = effort.get('criteria_v3', {}).get('text') if isinstance(effort.get('criteria_v3'), dict) else effort.get('criteria_v3')

    # ---- 5. 预言层 metadata（E-29 五字段 + E-41 步2 扩展：produces_asset/guardian_need/predict 段） ----
    import datetime as _dt
    locked_at = _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    meta = {
        'map_node_id': PANEL_TO_MAP.get(panel_id, panel_id),
        'map_subnode': args.subnode,
        'map_l3': getattr(args, 'l3', None) or None,  # E-64（2026-08-09）：三级坐标 L3 层（缺省=None 合法）
        'dac': args.dac,
        'effort': args.effort,
        'effort_reason': args.effort_reason,
        'produces_asset': args.produces_asset,
        'guardian_need': args.guardian_need or 'none',
        'priority': args.priority or 'P2',
        'risk': args.risk or 'readonly',
        'origin': args.origin or 'appointed',
        'estimate_minutes': None,  # 认领时填（步 4 派单合同强制）
        'predict_locked_at': locked_at,
    }

    out = {
        'subnode': args.subnode,
        'panel': panel_id,
        'skills': skills,
        'mcps': mcps,
        'assets': [{'asset_id': a['asset_id'], 'type': a.get('type'), 'panel': a.get('panel'),
                    'data_asset_class': a.get('data_asset_class'),
                    'map_subnode': a.get('map_subnode'), 'map_l3': a.get('map_l3'),
                    'storage_ref': a.get('storage_ref'), 'cred_status': a.get('cred_status'),
                    'db_path': a.get('path'), 'db_engine': a.get('engine'), 'db_lifecycle': a.get('lifecycle')} for a in asset_hits],
        'tools': [{'tool_id': t['tool_id'], 'name': t.get('name'), 'lifecycle': t.get('lifecycle'),
                   'map_l3': _tool_l3_for(t.get('map_subnode')), 'purpose': (t.get('purpose') or '')[:80]} for t in tool_hits],
        'effort_hint': {
            'values': effort.get('effort_values'),
            'criteria_v3': effort_prompt,
            'rule_hits': hits,
        },
        'metadata': meta,
    }

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if args.profile:
        prof = build_profile(args, panel_id, subnode_id, manual_reg, chain_matrix)
        print('\n'.join(prof))
        print(f'\n[metadata 五字段（可直接拼 bd create）]')
        print(f'  --metadata "{json.dumps(meta, ensure_ascii=False)}"')
        return

    # ---- 可读文本输出 ----
    print(f'== 坐标 {args.subnode}' + (f' / {args.dac}' if args.dac else '') + ' ==')
    print(f'\n[skill] {len(skills)} 个（skill-panel-mapping 在册）')
    for s in skills[:20]:
        print(f'  {s["skill"]}' + (f'  [{s["dac"]}]' if s.get('dac') else ''))
    if len(skills) > 20: print(f'  ... 共 {len(skills)}')
    print(f'\n[mcp] {len(mcps)} 个（mcp-skill-map 挂载 + registry 状态）')
    for m in mcps:
        print(f'  {m["mcp"]}  [{m["status"]}]  linked: {",".join(m["linked_skills"][:4])}')
    print(f'\n[asset] {len(asset_hits)} 个（webpage-asset-registry）')
    for a in out['assets'][:10]:
        print(f'  {a["asset_id"]}  [{a["type"]}]')
    print(f'\n[effort 提示] 六格: {"/".join(out["effort_hint"]["values"])}')
    if out['effort_hint']['criteria_v3']:
        print(f'  判据 v3: {out["effort_hint"]["criteria_v3"]}')
    if hits:
        print('  P 规则命中:')
        for rid, pat, ef, kw in hits:
            print(f'    {rid} [{ef}] 命中关键词"{kw}" ← {pat}')
    else:
        print('  未命中 P1-P10（按判据自估，effort 必填 + reason 必填）')
    print(f'\n[metadata 五字段（可直接拼 bd create）]')
    print(f'  --metadata "{json.dumps(meta, ensure_ascii=False)}"')
    print('  或字段值: ' + ' | '.join(f'{k}={v}' for k, v in meta.items()))

if __name__ == '__main__':
    main()
