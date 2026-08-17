# -*- coding: utf-8 -*-
"""repair-manual-section-map 自校验（E-136 v7，不挂主链，按需跑）
校验：坐标合法性（node ∈ 11 面板 / subnode ∈ 32 子域或面板级）、covers_object 非空（关联后）、related 端点存在
用法：python check_repair_section_map.py
"""
import json, io, os, sys

ROOT = r'F:\codex'
CAT = os.path.join(ROOT, 'tools', 'agent-system-map', 'catalog')
SMAP = os.path.join(CAT, 'repair-manual-section-map-v0.json')
PMAN = os.path.join(CAT, 'panel-manifest-v0.json')

def main():
    smap = json.load(io.open(SMAP, encoding='utf-8'))
    mani = json.load(io.open(PMAN, encoding='utf-8'))
    valid_nodes = {'map.' + pn.get('panel_id') for pn in mani.get('panels', []) if pn.get('panel_id')}
    valid_sub = set()
    for pn in mani.get('panels', []):
        for sn in pn.get('subnodes', []):
            valid_sub.add(pn.get('panel_id') + '.' + (sn.get('subnode_id') or sn.get('id')))
    bad = []
    mapped = [s for s in smap['sections'] if s.get('map_node_id')]
    for s in mapped:
        if s['map_node_id'] not in valid_nodes:
            bad.append('L%d node非法:%s' % (s['line'], s['map_node_id']))
        ms = s.get('map_subnode')
        if ms and '.' in ms and ms not in valid_sub:
            bad.append('L%d subnode非法:%s' % (s['line'], ms))
    # 关联后校验：covers_object 端点存在性（若已填；支持单值或数组，E-136 2026-08-16 一章节可关联多手册）
    manual = json.load(io.open(os.path.join(CAT, 'manual-registry-v0.json'), encoding='utf-8'))['entries']
    covers = {e.get('covers_object') for e in manual if e.get('covers_object')}
    # covers_object 值域（manual-registry 条目 id 全集，rule/gap 也收）
    all_ids = {e.get('manual_id') for e in manual if e.get('manual_id')}
    for s in mapped:
        co = s.get('covers_object')
        if not co:
            continue
        covs = co if isinstance(co, list) else [co]
        for c in covs:
            if c not in all_ids:
                bad.append('L%d covers_object 无端点:%s' % (s['line'], c))
    print('章节索引自校验: 总=%d 映射=%d 坏=%d' % (len(smap['sections']), len(mapped), len(bad)))
    if bad:
        print('  ' + '\n  '.join(bad[:8]))
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
