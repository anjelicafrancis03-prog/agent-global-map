#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# template_surgery_v55.py - codex-ygjz §七-5/6: template restructure to 11 panels + gate v3 prep
import io, re, shutil

TPL = r"F:/codex/tools/agent-system-map/web/map_website_template.html"
BLD = r"F:/codex/tools/agent-system-map/web/build_map_website.py"
shutil.copy(TPL, TPL + ".bak-v55-20260801")
s = io.open(TPL, encoding="utf-8").read()
orig = s

# ---- 1. assetCard: data-aid + type 三分（修 L250 错标「非 database 全工具」）----
s = s.replace(
  "  const typeTag=a.asset_type==='database'?'<span class=\"asset-tag tag-db\">数据库</span>':'<span class=\"asset-tag\">工具</span>';",
  "  const TL={'tool':'工具','database':'数据库','webpage':'网页'};const typeTag=`<span class=\"asset-tag${a.asset_type==='database'?' tag-db':''}\">${TL[a.asset_type]||'资产'}</span>`;")
s = s.replace("return `<div class=\"card asset-card\">${title}",
              "return `<div class=\"card asset-card\" data-aid=\"${esc(a.id)}\">${title}")

# ---- 2. coverage IIFE → qualityStripMarkup（注入 beads）----
cov_old = s[s.index("(function(){\n  const C=D.coverage||{};"):s.index("// codex-69lp manual coverage panel")]
assert "section('coverage'" in cov_old
cov_new = cov_old.replace(
  "  const node=section('coverage','Beads 投影覆盖率 / Gate H','中央库投影；绑定以 metadata.map_node_id 为准；本页不写 Beads。');",
  "  const node={innerHTML:'',insertAdjacentHTML(_,h){this.innerHTML+=h}};")
cov_new = cov_new.replace("(function(){", "const qualityStripMarkup=(()=>{").replace("})();", "return `<div id=\"beads-quality-strip\"><h3>投影质量（覆盖/绑定）</h3>${node.innerHTML}</div>`})();")
s = s.replace(cov_old, cov_new)

# ---- 3. manual-coverage IIFE → manualsStripMarkup（注入 beads，锚 beads-manuals）----
mc_old = s[s.index("// codex-69lp manual coverage panel"):s.index("const topologyPanel=section('topology'")]
assert "section('manual-coverage'" in mc_old
mc_new = mc_old.replace("// codex-69lp manual coverage panel (usage/repair matrix; projection only)\n(function(){",
                        "const manualsStripMarkup=(()=>{")
mc_new = mc_new.replace(
  "  const node=section('manual-coverage','手册覆盖矩阵：使用 ✓/✗ · 维修 ✓/✗','每个对象应有「使用手册 + 维修手册」两本（双手册红线）；缺口如实显示，不留空白、不假装完整。');",
  "  const node={innerHTML:'',insertAdjacentHTML(_,h){this.innerHTML+=h}};")
mc_new = mc_new.replace("})();", "return `<div id=\"beads-manuals\"><h3>双手册矩阵（使用 ✓/✗ · 维修 ✓/✗）</h3>${node.innerHTML}</div>`})();", 1)
s = s.replace(mc_old, mc_new)

# ---- 4. 注入两条 strip 到 beads（toolbar 之前）----
s = s.replace("beads.querySelector('.toolbar').insertAdjacentHTML('beforebegin',`<h3>Beads 下钻：",
              "beads.insertAdjacentHTML('beforeend',qualityStripMarkup+manualsStripMarkup);\nbeads.querySelector('.toolbar').insertAdjacentHTML('beforebegin',`<h3>Beads 下钻：")

# ---- 5. 删 assets section（卡片已由新 placements 路由）----
s = re.sub(r"const assets=domainPanel\('assets'\);assets\.innerHTML\+=`<div class=\"three-col\">[^`]*`;\nassets\.innerHTML\+=`<div class=\"branch-ladder\"><div class=\"l2-vstack\">\$\{childBlock\('knowledge-assets'\)\}\$\{childBlock\('business-pages'\)\}</div></div>`;\n", "", s)
assert "domainPanel('assets')" not in s, "assets section not removed"

# ---- 6. manuals section → 并入 search ----
s = s.replace("const manuals=section('manuals','OKF 手册库','使用/维修双手册全量浏览；没有手册的对象在双手册矩阵面板暴露。');",
              "const manuals=document.getElementById('search');manuals.insertAdjacentHTML('beforeend','<h3 id=\"search-manuals\">OKF 手册库（全量浏览；缺口见 beads 面板双手册矩阵）</h3>');")

# ---- 7. relations section → 拆：sources 表→topology，边+依赖→search ----
rel_old = s[s.index("const relations=section('relations'"):s.index("const desktop=section('desktop'")]
assert "注册权威源" in rel_old
rel_new = rel_old.replace(
  "const relations=section('relations','证据与依赖','Beads 依赖、地图快照边与注册权威源。关系只存一次，界面提供正向与反向阅读；缺少权威源时保留 missing，不用页面推断替代事实。');",
  "const relations=document.getElementById('search');relations.insertAdjacentHTML('beforeend','<h3>关系图谱（地图关系边 + Beads 任务依赖；来源权威见 topology 来源条）</h3>');")
src_tbl_marker = "<h3>注册权威源</h3>${table(['源','状态','状态面','实体','权威说明'],D.sources.map(source=>`<tr><td><code>${esc(source.id)}</code></td><td>${esc(source.status)}</td><td>${esc(source.plane||'n/a')}</td><td>${source.count}</td><td>${esc(source.authority)}</td></tr>`))}"
assert src_tbl_marker in rel_new
rel_new = rel_new.replace(src_tbl_marker, "")
s = s.replace(rel_old, rel_new)
# sources 表进 topology（topology 面板已有 innerHTML 构建，追加 strip）
s = s.replace("domainPanel('guardian');",
  "topologyPanel.insertAdjacentHTML('beforeend',`<div id=\"topology-sources\"><h3>注册权威源（14 源状态+准入分列）</h3>${table(['源','状态','状态面','实体','权威说明'],D.sources.map(source=>`<tr><td><code>${esc(source.id)}</code></td><td>${esc(source.status)}</td><td>${esc(source.plane||'n/a')}</td><td>${source.count}</td><td>${esc(source.authority)}</td></tr>`))}</div>`);\ndomainPanel('guardian');")

# ---- 8. workbench section → 删，核心指标并进 desktop ----
wb_old = s[s.index("const workbench=section('workbench'"):s.index("// 网页资产卡片（数据驱动")]
wb_metrics_start = wb_old.index("workbench.innerHTML+=`<div class=\"grid\">")
wb_metrics = wb_old[wb_metrics_start:].replace("workbench.innerHTML+=", "desktop.innerHTML+=`<h3>Agent Workbench（降级卡）</h3>`+")
s = s.replace(wb_old, wb_metrics + "\n")

# ---- 9. L270 手册链接 href #relations → #beads-manuals ----
s = s.replace('href="#relations"', 'href="#beads-manuals"')

# ---- 10. 硬编码卡渲染面板列表 ----
s = s.replace("['guardian','beads','hardware','search','harness','agent-hub','skill-manager','mcp-manager','provider-manager','assets','desktop','workbench']",
              "['beads','hardware','search','harness','agent-hub','skill-manager','mcp-manager','provider-manager','desktop']")

# ---- 11. 锚点 alias（script 开头，D 定义之后）----
s = s.replace("const D=JSON.parse(document.getElementById('mapdata').textContent), main=document.getElementById('main');",
  "const D=JSON.parse(document.getElementById('mapdata').textContent), main=document.getElementById('main');\nconst ANCHOR_ALIAS={'#coverage':'#beads-quality-strip','#manual-coverage':'#beads-manuals','#manuals':'#search-manuals','#workbench':'#desktop','#relations':'#topology-sources'};if(ANCHOR_ALIAS[location.hash])history.replaceState(null,'',ANCHOR_ALIAS[location.hash]);")

io.open(TPL, "w", encoding="utf-8", newline="\n").write(s)
print("template surgery done,", len(orig), "->", len(s))

# ---- 12. builder NAV → 11 面板 ----
sb = io.open(BLD, encoding="utf-8").read()
shutil.copy(BLD, BLD + ".bak-v55-20260801")
nav_old_start = sb.index("NAV = [")
nav_old_end = sb.index("]", sb.index("(\"workbench\"")) + 1
nav_new = """NAV = [
    ("topology", "体系地图"),
    ("guardian", "守护"),
    ("beads", "项目管理中心"),
    ("hardware", "硬件与团队协作"),
    ("search", "搜索线"),
    ("harness", "Harness 轨道"),
    ("agent-hub", "Agent Hub 通信"),
    ("skill-manager", "Skill Manager"),
    ("mcp-manager", "MCP Manager"),
    ("provider-manager", "Provider / API Key"),
    ("desktop", "桌面门户"),
]"""
sb = sb[:nav_old_start] + nav_new + sb[nav_old_end:]
io.open(BLD, "w", encoding="utf-8", newline="\n").write(sb)
print("builder NAV updated to 11 panels")
