# -*- coding: utf-8 -*-
"""
build_unified_relation_registry.py
----------------------------------
固化生成器（codex-69lp 管线固化，2026-07-28）。

从三个 catalog 数据源**确定性合并**产出 unified-relation-registry-v0.json：
  - task-projection-v0.json        （build_task_projection.py 产出）
  - webpage-asset-registry-v0.json （人工策展，audit_webpage_asset_registry.py 巡检）
  - relation-samples-v0.json       （人工策展，validate_relation_samples.py 校验）

为什么需要它：unified 曾是 2026-07-27 手工拼的一次性文件，entities_total
写死为 155；三源演进后（如 A+7天层 151 实体）即漂移。本生成器让合计数、
generated_at 永远与三源实况一致（builder 的 fresh_until 取本文件 mtime+7d）。

幂等：同输入 → 同输出（除 generated_at）。保留既有文件的 epic/contract_ref。
"""
import json
import os
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATALOG = os.path.join(ROOT, "tools", "agent-system-map", "catalog")

TASK_PROJECTION = os.path.join(CATALOG, "task-projection-v0.json")
ASSET_REGISTRY = os.path.join(CATALOG, "webpage-asset-registry-v0.json")
RELATION_SAMPLES = os.path.join(CATALOG, "relation-samples-v0.json")
OUT = os.path.join(CATALOG, "unified-relation-registry-v0.json")

NOW = datetime.datetime.now(datetime.timezone.utc).isoformat()


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    tp = load(TASK_PROJECTION)
    ar = load(ASSET_REGISTRY)
    rs = load(RELATION_SAMPLES)

    tp_entities = len(tp.get("entities", []))
    ar_assets = len(ar.get("assets", []))
    rs_edges = len(rs.get("edges", []))

    # 保留既有文件的史诗/契约引用（若存在）
    epic, contract_ref = "codex-69lp", "relation-contract-v1.json"
    if os.path.exists(OUT):
        try:
            old = load(OUT)
            epic = old.get("epic", epic)
            contract_ref = old.get("contract_ref", contract_ref)
        except Exception:
            pass

    unified = {
        "registry_id": "unified-relation-registry-v0",
        "generated_at": NOW,
        "read_only": True,
        "epic": epic,
        "contract_ref": contract_ref,
        "producer": "build_unified_relation_registry.py (deterministic merge of 3 catalog sources)",
        "parts": {
            "task_projection": "task-projection-v0.json",
            "asset_registry": "webpage-asset-registry-v0.json",
            "relation_samples": "relation-samples-v0.json",
        },
        "entities_total": tp_entities + ar_assets,
        "edges_total": rs_edges,
        "counts_detail": {
            "task_projection_entities": tp_entities,
            "asset_registry_assets": ar_assets,
            "relation_samples_edges": rs_edges,
        },
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(unified, f, ensure_ascii=False, indent=2)

    print("unified-relation-registry-v0.json rebuilt")
    print(f"  entities_total: {tp_entities} (task-projection) + {ar_assets} (assets) = {tp_entities + ar_assets}")
    print(f"  edges_total   : {rs_edges} (relation-samples)")
    print(f"  out: {OUT}")


if __name__ == "__main__":
    main()
