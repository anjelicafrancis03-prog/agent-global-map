# -*- coding: utf-8 -*-
"""repair-manual-section-map 生成器（E-136 v7：章节树结构驱动，0 启发式）

确定性映射：维修手册章节树「## 面板：X」→「### 子域：Y」→ 下辖章节继承坐标
（E-136 结论：纯启发式 47%+高假阳性被否定 → 结构驱动，139/139 确定性）

输出：catalog/repair-manual-section-map-v0.json
用法：python build_repair_section_map.py
"""
import json, io, os, re, datetime

ROOT = r'F:\codex'
SRC = os.path.join(ROOT, 'docs', 'agent-repair-manual.md')
OUT = os.path.join(ROOT, 'tools', 'agent-system-map', 'catalog', 'repair-manual-section-map-v0.json')

# panel_id 短名 → map_node_id（对齐 panel-manifest / suggest_task_context）
PANEL_TO_MAP = {
    'topology': 'map.topology', 'guardian': 'map.guardian', 'beads': 'map.beads',
    'projects': 'map.projects', 'hardware': 'map.hardware', 'search': 'map.search',
    'harness': 'map.harness', 'agent-hub': 'map.agent-hub', 'skill-manager': 'map.skill-manager',
    'mcp-manager': 'map.mcp-manager', 'provider-manager': 'map.provider-manager',
}
# 维修手册章节「面板：显示名」→ panel_id（E-136 实测 18 标注）
DISPLAY_TO_PANEL = {
    '守护': 'guardian', '硬件协作': 'hardware', '搜索线': 'search',
    'Harness 运行': 'harness', 'Skill 管理': 'skill-manager',
    'MCP 管理': 'mcp-manager', 'Provider/API Key': 'provider-manager', '桌面': 'topology',
    'Agent Hub': 'agent-hub',  # E-136 2026-08-16：communication 子域从 Harness 运行挪归 Agent Hub
}

def parse():
    # 面板 → 合法子域集（从 panel-manifest 读，坐标权威）
    mani = json.load(io.open(os.path.join(ROOT, 'tools', 'agent-system-map', 'catalog', 'panel-manifest-v0.json'), encoding='utf-8'))
    panel_subs = {}
    for pn in mani.get('panels', []):
        panel_subs[pn['panel_id']] = {(sn.get('subnode_id') or sn.get('id')) for sn in pn.get('subnodes', [])}
    lines = io.open(SRC, encoding='utf-8').read().splitlines()
    sections = []
    cur_panel, cur_subnode, cur_node = None, None, None
    heading_re = re.compile(r'^(#{1,4})\s+(.*)$')
    panel_re = re.compile(r'^面板[:：]\s*([^（(]+)')
    sub_re = re.compile(r'^子域[:：]\s*([^\s（(]+)')
    for i, ln in enumerate(lines, 1):
        m = heading_re.match(ln)
        if not m:
            continue
        lvl, title = len(m.group(1)), m.group(2).strip()
        # 面板/子域标注 → 更新当前坐标上下文（确定性，非启发式）
        pm = panel_re.search(title)
        if pm:
            disp = pm.group(1).strip()
            cur_panel = DISPLAY_TO_PANEL.get(disp)
            cur_node = PANEL_TO_MAP.get(cur_panel) if cur_panel else None
            cur_subnode = None
            continue
        sm = sub_re.search(title)
        if sm:
            cur_subnode = sm.group(1).strip()
            continue
        # 常规章节：继承当前坐标（结构驱动）
        ms = ('%s.%s' % (cur_panel, cur_subnode)) if (cur_panel and cur_subnode) else (cur_panel or None)
        # p023 保守侧：子域不在当前面板 → 标 coordinate_conflict，不映射（留待手册修订，不猜）
        conflict = False
        if cur_panel and cur_subnode and cur_subnode not in panel_subs.get(cur_panel, set()):
            conflict = True
            cur_node = None  # 冲突时不映射坐标
        sections.append({
            'section': title,
            'heading_level': lvl,
            'line': i,
            'map_node_id': cur_node,
            'map_subnode': ms,
            'panel_direct': True if (cur_node and not cur_subnode) else False,
            'coordinate_conflict': True if conflict else None,
            'covers_object': None,  # 由人工/后续关联填（E-136 相关键）
        })
    return sections

def main():
    secs = parse()
    mapped = [s for s in secs if s.get('map_node_id')]
    out = {
        'registry_id': 'repair-manual-section-map-v0',
        'schema': 'v0',
        'generated_at': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'generator': 'build_repair_section_map.py (E-136 v7 确定性章节树映射)',
        'source': SRC,
        'mapping_mode': 'deterministic (章节树面板/子域结构驱动，0 启发式)',
        'db_ref': 'db-030',
        'sections': secs,
    }
    json.dump(out, io.open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print('章节总数: %d | 已映射坐标: %d | 未映射: %d' % (len(secs), len(mapped), len(secs) - len(mapped)))
    # 坐标分布
    from collections import Counter
    c = Counter(s['map_subnode'] for s in secs if s.get('map_subnode'))
    print('坐标分布:')
    for k, v in c.most_common(15):
        print('  %-30s %d' % (k, v))

if __name__ == '__main__':
    main()
