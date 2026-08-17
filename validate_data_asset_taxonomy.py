# -*- coding: utf-8 -*-
"""
validate_data_asset_taxonomy.py
-------------------------------
E-23 数字资产分类门闸（2026-08-03 skill manager 常驻1 立）。
E-27 扩展（2026-08-03 WorkBuddy，总控裁决②）——职责升级为「分类值域门」：
  在原有 taxonomy/dac 校验基础上，新增 beads 任务 metadata 校验：
  - effort 值域：metadata.effort ∈ 六格（simple/medium/complex × fast/slow）
  - dac 值域：metadata.dac ⊆ taxonomy.data_asset_classes（任务侧）
  - actual_minutes：非负整数（如已标注）
  - event 排除：issue_type=event 的记录不得携带 effort/dac/panel 分类标注（防判例池污染）
  - map_node_id 值域：metadata.map_node_id ∈ 权威大地图面板集合（10 个 map.*，2026-08-03 加严版）
  - panel 方言禁用：metadata.panel 字段不得存在（2026-08-03 总控加严裁决：方言 panel 整个删除，
    不转换；canonical 字段是 map_node_id）——panel 自判错误根因见
    reports/E27-panel-error-rootcause-review-20260803.md
  - 禁项（强制）：分类任务（E-27 类）的 map_node_id/panel 必须从权威反查——
    优先级 task-projection-v0.json（投影内）→ OKF 大地图编号映射规则（投影外，标 panel_derivation: rule-derived）
    → 都没有的进 candidates 不硬塞；禁止自判。

校验「data-asset-taxonomy-v0.json」为数字资产子类唯一定义点，且所有引用方的
data_asset_class 值零未知指向：
  - taxonomy 自身：data_asset_classes 无重复 id、parent 存在且指向 panels 子域
  - 引用方1：webpage-asset-registry-v0.json 所有 database 资产的 data_asset_class
             ⊆ taxonomy.data_asset_classes（含 candidate 状态——值存在即可引用）
  - 引用方2：skill-panel-mapping-v0.json 所有 dac 值 ⊆ taxonomy.data_asset_classes
  - 引用方3：.beads/issues.jsonl 任务 metadata（effort/dac/actual_minutes/event 排除）

退出码：0 = 全部通过；1 = 有坏引用（阻断镜像/渲染，先修引用方或 taxonomy）。

用法：python3 tools/agent-system-map/validate_data_asset_taxonomy.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATALOG = os.path.join(ROOT, "tools", "agent-system-map", "catalog")
TAXONOMY = os.path.join(CATALOG, "data-asset-taxonomy-v0.json")
ASSET_REG = os.path.join(CATALOG, "webpage-asset-registry-v0.json")
SKILL_MAP = os.path.join(CATALOG, "skill-panel-mapping-v0.json")
BEADS = os.path.join(ROOT, ".beads", "issues.jsonl")

EFFORT_VALUES = {
    "simple-fast", "simple-slow", "medium-fast", "medium-slow",
    "complex-fast", "complex-slow",
}

# 权威大地图面板集合——2026-08-08 E-60 验收修：值域不再硬编码。旧硬编码集是 11 面板重构前
# 长名（business-assets/hardware-collaboration 等），E-46 方言迁移后 beads 全是新短名，
# 硬编码旧集对 507 条任务误报。改为从 panel-manifest-v0.json 推导，与 T8 门闸同源（值域单一来源原则：
# 一切门闸值域从同一权威推导，禁止硬编码副本）。
PANEL_MANIFEST = os.path.join(CATALOG, "panel-manifest-v0.json")


def _load_map_node_values():
    with open(PANEL_MANIFEST, encoding="utf-8") as f:
        mani = json.load(f)
    return {"map." + p["panel_id"] for p in mani.get("panels", []) if p.get("panel_id")}


MAP_NODE_VALUES = _load_map_node_values()



# E-64（2026-08-09）：map_l3 值域从全部 taxonomy 文件推导（L3 唯一定义点，缺省合法）
# E-64（2026-08-09）：map_l3 值域从全部 taxonomy 文件推导（L3 唯一定义点，缺省合法）
L3_VALUES = set()
import glob as _glob
for _tf in _glob.glob('F:/codex/tools/agent-system-map/catalog/*taxonomy*.json'):
    try:
        _td = json.load(open(_tf, encoding="utf-8"))
        for _cls in _td.get("data_asset_classes", []):
            if _cls.get("status") != "deprecated" and _cls.get("id"):
                L3_VALUES.add(_cls["id"])
    except Exception:
        pass

def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    problems = []

    if not os.path.exists(TAXONOMY):
        print(f"FAIL: taxonomy 不存在 {TAXONOMY}")
        sys.exit(1)
    tax = load(TAXONOMY)

    classes = tax.get("data_asset_classes", [])
    ids = [c.get("id") for c in classes]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        problems.append(f"taxonomy 子类 id 重复: {sorted(dup)}")

    sub_ids = {s["subnode_id"] for p in tax.get("panels", []) for s in p.get("subnodes", [])}
    sub_full = {f"{p.get('panel_id')}.{s['subnode_id']}" for p in tax.get("panels", []) for s in p.get("subnodes", [])}
    for c in classes:
        parent = c.get("parent")
        if parent not in sub_ids and parent not in sub_full:
            problems.append(f"子类 {c.get('id')} 的 parent={parent!r} 不在 panels 子域（{sub_ids | sub_full}）")

    valid_ids = {c.get("id") for c in classes}

    # 引用方1：webpage-asset-registry database 资产
    dbs = []
    if os.path.exists(ASSET_REG):
        ar = load(ASSET_REG)
        dbs = [a for a in ar.get("assets", []) if a.get("type") == "database"]
        for a in dbs:
            v = a.get("data_asset_class")
            if not v:
                problems.append(f"[asset] {a.get('asset_id')} 缺 data_asset_class")
            elif v not in valid_ids:
                problems.append(f"[asset] {a.get('asset_id')} data_asset_class={v!r} 不在 taxonomy")
    else:
        problems.append(f"引用方缺失: {ASSET_REG}")

    # 引用方2：skill-panel-mapping dac
    if os.path.exists(SKILL_MAP):
        sm = load(SKILL_MAP)
        for m in sm.get("mappings", []):
            d = m.get("dac")
            if d and d not in valid_ids:
                problems.append(f"[skill] {m.get('skill')} dac={d!r} 不在 taxonomy")
    else:
        problems.append(f"引用方缺失: {SKILL_MAP}")

    # 引用方3（E-27 扩展）：beads 任务 metadata 分类值域门
    task_count = event_count = 0
    effort_problems = dac_problems = minute_problems = event_tag_problems = 0
    panel_problems = 0
    mapnode_problems = 0
    l3_problems = 0
    if os.path.exists(BEADS):
        for line in open(BEADS, encoding="utf-8"):
            try:
                t = json.loads(line)
            except Exception:
                continue
            it = t.get("issue_type")
            if it == "event":
                event_count += 1
                # event 不得携带分类标注（判例池污染）
                m = t.get("metadata") or {}
                if isinstance(m, str):
                    try:
                        m = json.loads(m)
                    except Exception:
                        m = {}
                tagged = [k for k in ("effort", "dac", "panel") if m.get(k)]
                if tagged:
                    event_tag_problems += 1
                    problems.append(f"[beads/event] {t.get('id')} event 记录携带分类标注 {tagged}——污染判例池")
                continue
            task_count += 1
            m = t.get("metadata") or {}
            if isinstance(m, str):
                try:
                    m = json.loads(m)
                except Exception:
                    m = {}
            ef = m.get("effort")
            if ef and ef not in EFFORT_VALUES:
                effort_problems += 1
                problems.append(f"[beads/effort] {t.get('id')} effort={ef!r} 不在六格 {sorted(EFFORT_VALUES)}")
            d = m.get("dac")
            if d and d not in valid_ids:
                dac_problems += 1
                problems.append(f"[beads/dac] {t.get('id')} dac={d!r} 不在 taxonomy")
            am = m.get("actual_minutes")
            if am is not None:
                if not isinstance(am, (int, float)) or am < 0:
                    minute_problems += 1
                    problems.append(f"[beads/actual_minutes] {t.get('id')} actual_minutes={am!r} 非非负数字")
            # 2026-08-03 加严：方言 panel 字段不得存在（canonical 是 map_node_id）
            if m.get("panel") is not None and "panel" in m:
                panel_problems += 1
                problems.append(f"[beads/panel] {t.get('id')} 存在方言 panel 字段={m.get('panel')!r}——必须删除，改用 map_node_id（canonical）")
            # 2026-08-03 加严：map_node_id 值域 ∈ 权威面板集合
            mn = m.get("map_node_id")
            if mn and mn not in MAP_NODE_VALUES:
                mapnode_problems += 1
                problems.append(f"[beads/map_node_id] {t.get('id')} map_node_id={mn!r} 不在权威面板集合 {sorted(MAP_NODE_VALUES)}")
            # E-64（2026-08-09）：map_l3 值域 ∈ 全部 taxonomy L3（值域单一来源；缺省=无 L3 合法）
            ml3 = m.get("map_l3")
            if ml3 and ml3 not in L3_VALUES:
                l3_problems += 1
                problems.append(f"[beads/map_l3] {t.get('id')} map_l3={ml3!r} 不在 taxonomy L3 值域 {sorted(L3_VALUES)}")
    else:
        problems.append(f"引用方缺失: {BEADS}")

    print(f"taxonomy 校验: {len(classes)} 个子类 (ids={sorted(valid_ids)})")
    if os.path.exists(ASSET_REG):
        print(f"  asset 引用: {len(dbs)} 个 database")
    if os.path.exists(SKILL_MAP):
        print(f"  skill 引用: {len(load(SKILL_MAP).get('mappings', []))} 条映射")
    if os.path.exists(BEADS):
        print(f"  beads 任务: {task_count} 个（排除 event {event_count} 条）| effort 坏值 {effort_problems} / dac 坏值 {dac_problems} / actual_minutes 坏值 {minute_problems} / event 带标 {event_tag_problems} / 方言 panel {panel_problems} / map_node_id 坏值 {mapnode_problems} / map_l3 坏值 {l3_problems}")
    if problems:
        print(f"FAIL: {len(problems)} 个问题——阻断，先修引用方或 taxonomy")
        for p in problems[:20]:
            print("  " + p)
        if len(problems) > 20:
            print(f"  ... 等 {len(problems) - 20} 个未列出")
        sys.exit(1)
    print("OK: 所有 data_asset_class / dac / effort 值均合法（零未知指向），event 记录无分类标注")
    sys.exit(0)


if __name__ == "__main__":
    main()
