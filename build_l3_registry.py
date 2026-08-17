# -*- coding: utf-8 -*-
"""E-135 生成式 L3 注册表生成器
从 4/5 个 taxonomy 文件的 data_asset_classes 汇总生成 l3-registry-v0.json（索引视图）。
- 幂等：重复跑结果一致（按 id 排序，无手改残留）
- 收录：status != deprecated（含 candidate，与 T8 现行一致）
- kind：按 source_taxonomy 判定（credential-taxonomy → credential，其余 → data-asset），禁猜前缀
- 空文件（0 个 L3）正常跳过
- 非硬包含：parent 仅软提示；门闸按层精确不递归
"""
import json
import glob
import os
import datetime

CAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog")
OUT = os.path.join(CAT, "l3-registry-v0.json")


def main():
    entries = []
    for tf in sorted(glob.glob(os.path.join(CAT, "*taxonomy*.json"))):
        src = os.path.basename(tf)
        d = json.load(open(tf, encoding="utf-8-sig"))
        classes = d.get("data_asset_classes", []) or []
        n = 0
        for c in classes:
            if not isinstance(c, dict) or not c.get("id"):
                continue
            if c.get("status") == "deprecated":
                continue
            kind = "credential" if src == "credential-taxonomy-v0.json" else "data-asset"
            entries.append({
                "l3_id": c["id"],
                "name": c.get("name", ""),
                "parent": c.get("parent"),  # 软提示，非硬从属
                "status": c.get("status", "active"),
                "kind": kind,
                "source_taxonomy": src,
                "note": c.get("note", "")[:120],
            })
            n += 1
        print("  %s: +%d L3" % (src, n))

    entries.sort(key=lambda x: x["l3_id"])
    reg = {
        "schema": "l3-registry-v0",
        "version": "0.1",
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "_meta": {
            "authority_of": "L3 值域全局索引（E-135 生成物，从 taxonomy 汇总；源=各域 taxonomy 权威）",
            "rules": [
                "非硬包含：panel/子域/L3 是三个独立地址层，parent 仅软提示，门闸按层精确不递归",
                "kind 值域：data-asset（数据资产）/ credential（凭据）——按 source_taxonomy 判定，不猜 id 前缀",
                "生成物：本文件由 build_l3_registry.py 生成，不手改；增删 L3 走各域 taxonomy（增长流 candidate→active）后重建",
                "status 收录：!= deprecated（含 candidate）",
            ],
            "generator": "build_l3_registry.py (E-135)",
            "growth_flow": "新 L3 先登记域 taxonomy（candidate，E-111 zhishiku 先例）→ 重建本文件 → 生效",
        },
        "counts": {"total": len(entries), "by_kind": {
            "data-asset": sum(1 for e in entries if e["kind"] == "data-asset"),
            "credential": sum(1 for e in entries if e["kind"] == "credential"),
        }},
        "entries": entries,
    }
    json.dump(reg, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("OUT:", OUT, "| 总数:", len(entries), "| by_kind:", reg["counts"]["by_kind"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
