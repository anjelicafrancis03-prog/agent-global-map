#!/usr/bin/env python3
"""build_precedents.py: 判例站独立页生成器（E-111 R3）
输出: C:/html/precedents.html —— 三级分布图 + 注册面板 + 时间序 + 权重双轴
用法: python build_precedents.py <precedent-registry.json> <out.html>
"""
import json, sys, html, os
from collections import Counter, defaultdict

def main():
    if len(sys.argv) < 3:
        print("usage: build_precedents.py <registry.json> <out.html>")
        return 1
    reg = json.load(open(sys.argv[1], encoding="utf-8"))
    ps = reg.get("precedents", [])
    out = sys.argv[2]

    # 分布视图（增补3）：全量坐标位平铺不嵌套，每格独立计数禁滚加
    # 坐标位 = panel直属 + 每子域 + 每L3（taxonomy），全来自权威清单，空位显0
    pm = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "catalog", "panel-manifest-v0.json"), encoding="utf-8"))
    panels_meta = pm.get("panels", [])
    # taxonomy L3 清单（含 parent 引用）
    tax = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "catalog", "data-asset-taxonomy-v0.json"), encoding="utf-8"))
    l3_items = []
    def walk(x):
        if isinstance(x, dict):
            if 'id' in x and 'parent' in x:
                l3_items.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(tax)
    # 判例按坐标位独立归属
    def coord_key(p):
        nid = p.get("map_node_id", "").replace("map.", "")
        sub = p.get("map_subnode") or ""
        l3 = p.get("map_l3") or ""
        return (nid, sub, l3)
    prec_by_coord = defaultdict(list)
    for p in ps:
        prec_by_coord[coord_key(p)].append(p)

    dist_html = []
    for pmeta in panels_meta:
        pid = pmeta.get("panel_id", "?")
        cells = []
        # 1. panel 直属格
        direct_cnt = len(prec_by_coord.get((pid, "", ""), []))
        cells.append(f'<div class="coord {"" if direct_cnt else "empty"}"><span class="clabel">topology·直属</span><span class="ccnt">{direct_cnt}</span></div>'.replace("topology", pid))
        # 2. 子域格
        for s in (pmeta.get("subnodes") or []):
            sid = s.get("subnode_id") if isinstance(s, dict) else s
            scnt = len(prec_by_coord.get((pid, sid, ""), []))
            cells.append(f'<div class="coord {"" if scnt else "empty"}"><span class="clabel">{pid}.{sid}</span><span class="ccnt">{scnt}</span></div>')
        # 3. L3 格（taxonomy 中 parent 指向本面板子域的）
        for l3i in l3_items:
            parent = l3i.get("parent", "")
            if parent.startswith(pid + "."):
                l3key = l3i.get("id", "")
                lcnt = len(prec_by_coord.get((pid, parent.split(".", 1)[1], l3key), [])) + len(prec_by_coord.get((pid, "", l3key), []))
                cells.append(f'<div class="coord {"" if lcnt else "empty"}"><span class="clabel">{pid}.{parent.split(".", 1)[1]} / {l3key}</span><span class="ccnt">{lcnt}</span></div>')
        dist_html.append(f'<div class="panel"><div class="pname">{html.escape(pid)}</div><div class="coordgrid">{"".join(cells)}</div></div>')

    # 时间排序（倒序）
    ps_sorted = sorted(ps, key=lambda x: x["created_at"], reverse=True)

    rows = []
    for p in ps_sorted:
        rows.append(
            f"<tr><td>{html.escape(p['id'])}</td><td>{html.escape(p['判定内容'])}</td>"
            f"<td>{html.escape(p.get('source_bead') or '-')}</td>"
            f"<td>{html.escape(p.get('map_node_id',''))}.{html.escape(p.get('map_subnode') or '')}{('.' + html.escape(p['map_l3'])) if p.get('map_l3') else ''}</td>"
            f"<td>{p['created_at']}</td><td>{p['weight_usage']}</td>"
            f"<td>{html.escape(p['weight_authority'])}</td><td>{html.escape(p.get('status',''))}</td></tr>"
        )

    doc = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>判例库</title><style>
body{{font-family:sans-serif;margin:2rem;background:#fafafa;color:#222}}
h1{{border-bottom:2px solid #444;padding-bottom:.5rem}}
.panel{{display:inline-block;border:1px solid #ccc;border-radius:8px;padding:.8rem;margin:.5rem;background:#fff;min-width:180px}}
.pname{{font-weight:bold;border-bottom:1px dashed #aaa;margin-bottom:.4rem}}
.sub{{color:#333;font-size:.9rem;margin-left:.4rem}}.l3{{color:#888;font-size:.85rem;margin-left:1rem}}
.empty{{color:#bbb;font-style:italic}}
.coordgrid{{display:flex;flex-wrap:wrap;gap:4px;margin-top:.4rem}}
.coord{{border:1px solid #ddd;border-radius:4px;padding:2px 6px;font-size:.8rem;background:#fff;display:inline-flex;gap:6px;align-items:center}}
.coord.empty{{opacity:.45}}
.clabel{{color:#555}}.ccnt{{font-weight:700;color:var(--teal,#0a7)}}
table{{border-collapse:collapse;width:100%;margin-top:1.5rem;font-size:.85rem}}
th,td{{border:1px solid #ccc;padding:.4rem;text-align:left;vertical-align:top}}
th{{background:#eee}}.authority-铁律{{color:#c00;font-weight:bold}}
</style></head><body>
<h1>判例库 <small>({len(ps)} 条)</small></h1>
<h2>三级大地图分布</h2>{''.join(dist_html)}
<h2>注册面板（时间倒序）</h2>
<table><tr><th>ID</th><th>判定内容</th><th>出处 bead</th><th>挂载坐标</th><th>时间</th><th>使用次数</th><th>权威级</th><th>状态</th></tr>{''.join(rows)}</table>
<p><a href="agent-global-map.html">← 返回大地图</a></p>
</body></html>"""
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"PRECEDENTS OK: {len(ps)} precedents -> {out}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
