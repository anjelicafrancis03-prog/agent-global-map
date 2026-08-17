# -*- coding: utf-8 -*-
"""
validate_relation_samples.py
-----------------------------
固化校验器（codex-69lp 管线固化，2026-07-28），与 manual_pointers_resolve 对称的门闸。

relation-samples-v0.json 是 27 条**人工策展**关系边（from/relation/to/derivation）。
策展数据不会自动过期，但引用的节点可能被删——本校验器在快照建成后运行，
对照快照实体清单验证每条边的 from/to 都存在、字段完整、derivation 合法。

退出码：0 = 全部通过；1 = 有坏引用（**阻断镜像**，先修 relation-samples 或查节点为何消失）。

校验宇宙 = 快照实体 ∪ map-nodes-v0.json 规范 view 节点（map.* 是网站拓扑层，
本就不是快照实体；2026-07-28 管线固化后两层都承认）。
# E-94 注记：view 层历史值坐标解释查 view-to-coord-map-v0.json（坐标层查 manifest）——不改消费逻辑。

用法：python3 tools/agent-system-map/validate_relation_samples.py
      （需在 build_multi_source_snapshot.py 之后运行，读 catalog/multi-source-snapshot-v2.json）
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATALOG = os.path.join(ROOT, "tools", "agent-system-map", "catalog")
SAMPLES = os.path.join(CATALOG, "relation-samples-v0.json")
SNAPSHOT = os.path.join(CATALOG, "multi-source-snapshot-v2.json")
MAP_NODES = os.path.join(CATALOG, "map-nodes-v0.json")

VALID_DERIVATIONS = {"high", "medium", "low"}
REQUIRED_FIELDS = ("from", "relation", "to")


def main():
    with open(SAMPLES, encoding="utf-8") as f:
        samples = json.load(f)
    edges = samples.get("edges", [])

    if not os.path.exists(SNAPSHOT):
        print(f"FAIL: 快照不存在 {SNAPSHOT}——先跑 build_multi_source_snapshot.py")
        sys.exit(1)
    with open(SNAPSHOT, encoding="utf-8") as f:
        snap = json.load(f)
    known_ids = {e.get("id") for e in snap.get("entities", []) if e.get("id")}

    # view 层规范节点（map.*）——网站拓扑层真实存在，非快照实体
    map_node_count = 0
    if os.path.exists(MAP_NODES):
        with open(MAP_NODES, encoding="utf-8-sig") as f:
            for n in json.load(f).get("nodes", []):
                if n.get("id"):
                    known_ids.add(n["id"])
                    map_node_count += 1
    else:
        print(f"WARN: {MAP_NODES} 缺失，map.* 引用将全部判悬空")

    problems = []
    for i, e in enumerate(edges):
        for field in REQUIRED_FIELDS:
            if not e.get(field):
                problems.append(f"edge[{i}] 缺字段 {field}: {e}")
                break
        else:
            if e["from"] not in known_ids:
                problems.append(f"edge[{i}] from 不在快照: {e['from']}  (relation={e.get('relation')})")
            if e["to"] not in known_ids:
                problems.append(f"edge[{i}] to 不在快照: {e['to']}  (relation={e.get('relation')})")
            deriv = e.get("derivation")
            if deriv is not None and deriv not in VALID_DERIVATIONS:
                problems.append(f"edge[{i}] derivation 非法: {deriv!r}（合法值 {sorted(VALID_DERIVATIONS)}）")

    print(f"relation-samples 校验: {len(edges)} 条边, 校验宇宙 {len(known_ids)} 个（快照实体 + {map_node_count} 个 map.* 规范节点）")
    if problems:
        print(f"FAIL: {len(problems)} 个问题——阻断镜像，先修 relation-samples-v0.json 或查节点去向")
        for p in problems:
            print("  " + p)
        sys.exit(1)
    print("OK: 全部边的 from/to 均在快照实体中，字段完整")
    sys.exit(0)


if __name__ == "__main__":
    main()
