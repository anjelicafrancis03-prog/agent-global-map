#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
augment_manual_registry — 双手册注册表自动补全器（固化版，替代手加 manual-registry 条目）

作用：扫描手册源，把**缺失的**使用/维修手册幂等补进 catalog/manual-registry-v0.json。
**保留人工校准的现有条目**（covers_object/map_node_id/derivation 不动），只新增缺失项。
这样以后新写 repair.md / 新手册会被自动注册，不再靠手改。

手册源：
  - okf-bundles/*/index.md   → usage（manual_id okf:<name>，读 frontmatter manual_type）
  - okf-bundles/*/repair.md  → repair（manual_id repair:<name>，covers 复用 okf:<name> 的 map_node_id）
  - DOCS_MANUALS 清单里的 docs 手册（usage/repair 各自指定 covers/map_node）

快照 builder 从 catalog/manual-registry-v0.json 读 manual_type/covers/map_node 投影双手册。
"""
import json, os, sys, io, re, datetime, glob

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = r"F:\codex"
REG = os.path.join(ROOT, r"tools\agent-system-map\catalog\manual-registry-v0.json")
OKF = os.path.join(ROOT, "okf-bundles")

# docs 手册清单：(manual_id, path, title, manual_type, covers_object, map_node_id)
DOCS_MANUALS = [
    ("docs:agent-workbench-manual", "docs/agent-workbench-manual.md", "Agent 工作台手册", "usage", "agent-workbench", "map.views"),
    ("docs:video-production-workflow", "docs/video-production-workflow.md", "Video Production 使用手册", "usage", "video-production", "map.business-assets"),
    ("docs:video-production:repair", "docs/harness-modules/video-production/repair.md", "Video Production 维修手册", "repair", "video-production", "map.business-assets"),
    ("docs:app-ui-skill-workflow", "docs/app-ui-skill-workflow.md", "App UI Skill 使用手册", "usage", "app-html-visual", "map.views"),
    ("docs:app-html-visual:repair", "docs/app-ui-skill-repair.md", "App HTML Visual 维修手册", "repair", "app-html-visual", "map.views"),
    ("docs:windows-agent-repair:usage", "docs/windows-agent-repair-usage.md", "Windows Agent 维修系统 使用手册", "usage", "windows-agent-repair", "map.desktop"),
]


def read_manual_type(path):
    """从手册 frontmatter 读 manual_type（无则 None）。"""
    try:
        head = open(path, encoding="utf-8", errors="ignore").read(2000)
    except Exception:
        return None
    m = re.search(r"^manual_type:\s*([\w-]+)", head, re.M)
    return m.group(1) if m else None


def main():
    d = json.load(open(REG, encoding="utf-8"))
    ents = d.setdefault("entries", [])
    have = {e.get("manual_id") for e in ents}
    node_by_obj = {}
    for e in ents:
        if e.get("covers_object") and e.get("map_node_id"):
            node_by_obj.setdefault(e["covers_object"], e["map_node_id"])

    added = []

    # 1) OKF bundles: index.md -> usage, repair.md -> repair
    for b in sorted(glob.glob(os.path.join(OKF, "*"))):
        if not os.path.isdir(b):
            continue
        name = os.path.basename(b)
        idx = os.path.join(b, "index.md")
        rep = os.path.join(b, "repair.md")
        if os.path.exists(idx):
            mid = f"okf:{name}"
            if mid not in have:
                mt = read_manual_type(idx) or "usage"
                ents.append({"manual_id": mid, "form": "okf", "path": f"okf-bundles/{name}/index.md",
                             "title": name, "manual_type": mt, "covers_object": name,
                             "map_node_id": node_by_obj.get(name), "canonical": True,
                             "derivation": "auto-augment okf usage"})
                have.add(mid); added.append(mid)
        if os.path.exists(rep):
            mid = f"repair:{name}"
            if mid not in have:
                ents.append({"manual_id": mid, "form": "okf", "path": f"okf-bundles/{name}/repair.md",
                             "title": f"{name} 维修手册", "manual_type": "repair", "covers_object": name,
                             "map_node_id": node_by_obj.get(name), "canonical": True,
                             "derivation": "auto-augment okf repair"})
                have.add(mid); added.append(mid)

    # 2) docs 手册清单
    for mid, path, title, mt, cov, node in DOCS_MANUALS:
        if mid in have:
            continue
        if not os.path.exists(os.path.join(ROOT, path)):
            continue
        ents.append({"manual_id": mid, "form": "docs", "path": path, "title": title,
                     "manual_type": mt, "covers_object": cov,
                     "map_node_id": node or node_by_obj.get(cov), "canonical": True,
                     "derivation": "auto-augment docs"})
        have.add(mid); added.append(mid)

    d["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # codex-ygjz P2: counts 永远从 entries 重算，禁止手写漂移（66/62 vs 85/81 教训）
    from collections import Counter as _C
    _canon = [e for e in ents if e.get("canonical")]
    d["counts"] = {
        "total_entries": len(ents),
        "canonical": len(_canon),
        "stubs_alias": len(ents) - len(_canon),
        "by_type": dict(_C(e.get("manual_type") for e in _canon)),
        "by_form": dict(_C(e.get("form") for e in _canon)),
        "recompute_rule": "codex-ygjz P2: always recomputed from entries, never hand-written",
    }
    json.dump(d, open(REG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"added {len(added)} manuals (total {len(ents)}); new:", added[:20])


if __name__ == "__main__":
    main()
