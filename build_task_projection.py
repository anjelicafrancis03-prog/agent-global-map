#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
build_task_projection — 任务实体投影生成器（固化版，替代手改的 gen_step0.py）

范围（A+7天层，2026-07-28 用户拍板 codex-69lp.10）：
  metadata-bound + active(open/in_progress/blocked) + 近7天关闭(closed_at<=7d，历史层不计活跃)；
  无节点归属(map_node_id/lane-derived)的不进；**明确不扩全量 closed**。

自包含：直接调 bd.exe 取新鲜 issue，写稳定 catalog/task-projection-v0.json。
快照 builder(build_multi_source_snapshot.py) 从 catalog 读本产物。
"""
import json, os, sys, io, datetime, subprocess

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = r"F:\codex"
BD = os.path.join(ROOT, r"tools\beads\bd.exe")
OUT = os.path.join(ROOT, r"tools\agent-system-map\catalog\task-projection-v0.json")

LANE2NODE = {
    # DEPRECATED (E-87, 2026-08-11): lane 平行体系废弃，坐标三级（面板.子域.L3）是正式分类轴。
    # 保留仅防旧数据兜底触发投影出旧名节点；新任务不再写 lane:* 标签。
    # 2026-08-17 fix: 兜底值从 view 层（map-nodes-v0）改为坐标层（panel-manifest）——
    # 按 view-to-coord-map-v0.json 映射，防 T8 门把兜底节点判 unknown（root-governance/desktop
    # 并入 topology、capability-management→skill-manager、business-assets→projects、
    # search-retrieval→search、hardware-collaboration→hardware）。
    "agent-governance": "map.topology",
    "agent-control-surface": "map.topology",
    "agent-search-crawler": "map.search",
    "agent-tooling": "map.skill-manager",
    "business-content": "map.projects",
    "business-conversion": "map.projects",
    "business-growth": "map.projects",
    "business-product-rd": "map.projects",
    "data-business-assets": "map.projects",
    "data-cognitive-kb": "map.projects",
    "data-knowledge-retrieval": "map.search",
    "data-media-assets": "map.projects",
    "overseas-connectivity": "map.hardware",
    "overseas-finance": "map.projects",
    "overseas-webapp-backend": "map.projects",
}
ACTIVE = ("open", "in_progress", "blocked")
RECENT_CLOSED_DAYS = 7


def lanes(i):
    return [l[5:] for l in (i.get("labels") or []) if str(l).startswith("lane:")]


def within_days(ts, days):
    if not ts:
        return False
    try:
        d = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        # codex-ygjz P3-3 七天边界：timedelta.days 截断会把 7d23h 也算进 7 天窗口，改秒级比较
        return (datetime.datetime.now(datetime.timezone.utc) - d).total_seconds() <= days * 86400
    except Exception:
        return False


def load_issues():
    # codex-ygjz P1-3: single-generation input — when BEADS_EXPORT_JSONL is set (rebuild.ps1
    # generation export with sha256 manifest), consume that immutable file instead of re-querying bd
    # (prevents two different Beads moments mixing into one page).
    exp = os.environ.get("BEADS_EXPORT_JSONL")
    if exp and os.path.isfile(exp):
        with io.open(exp, "r", encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]
    raw = subprocess.run([BD, "list", "--all", "--json"], capture_output=True, text=True, encoding="utf-8").stdout
    d = json.loads(raw)
    return d if isinstance(d, list) else d.get("issues", d)


def main():
    items = load_issues()
    entities, counts = [], {}
    bound_n = active_n = recent_closed_n = 0
    for i in items:
        md = i.get("metadata") or {}
        node = md.get("map_node_id")
        deriv = None
        if node:
            deriv = "metadata-binding"
            bound_n += 1
        else:
            ls = lanes(i)
            if ls:
                node = LANE2NODE.get(ls[0])
                deriv = "lane-derived" if node else None
        is_active = i.get("status") in ACTIVE
        is_recent_closed = (i.get("status") == "closed" and within_days(i.get("closed_at"), RECENT_CLOSED_DAYS))
        if not node or (not is_active and not is_recent_closed and deriv != "metadata-binding"):
            continue
        if is_active:
            active_n += 1
        if is_recent_closed:
            recent_closed_n += 1
        entities.append({
            "id": f"task:{i.get('id')}", "kind": "task", "display_name": i.get("title"),
            "status": i.get("status"), "priority": i.get("priority"), "issue_type": i.get("issue_type"),
            "map_node_id": node, "map_subnode": md.get("map_subnode"), "map_l3": md.get("map_l3"), "node_derivation": deriv, "lanes": lanes(i),
            "source": "beads", "authority": "F:/codex/.beads",
        })
        c = counts.setdefault(node, {"open": 0, "in_progress": 0, "blocked": 0, "closed": 0, "other": 0})
        c[i.get("status") if i.get("status") in c else "other"] += 1

    entities += [
        {"id": "capability:beads-cli", "kind": "capability", "display_name": "bd CLI",
         "map_node_id": "map.beads", "path": "F:/codex/tools/beads/bd.exe", "source": "beads", "authority": "tools/beads"},
        {"id": "system:beads-store", "kind": "system", "display_name": ".beads 任务数据仓",
         "map_node_id": "map.beads", "path": "F:/codex/.beads", "source": "beads", "authority": ".beads repo"},
    ]

    # E-67（2026-08-10）：手册/OKF 实体挂载——search.manuals + search.okf-entry 不再空壳
    # 坐标粒度：现 panel 级挂载（有总比没有好）；E-64 阶段 4 三级定稿后重跑本生成器自动升级（投影是生成物，源升则投影升）
    # E-67 打回修复（2026-08-10）：okf 类实体优先从包 index.md 坐标行解析三级坐标（数据已迁），解析失败/旧长名回退 registry
    import re as _re
    _VALID_PANELS = {"map.topology", "map.beads", "map.harness", "map.provider-manager", "map.agent-hub",
                     "map.skill-manager", "map.mcp-manager", "map.search", "map.hardware", "map.projects", "map.guardian"}

    def _parse_okf_coord(path):
        """解析 OKF index.md 坐标行 → {map_node_id, map_subnode, map_l3}；失败返回 None"""
        try:
            _txt = io.open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            return None
        _m = _re.search(r">\s*坐标[：:]\s*([^\n|]+)", _txt)
        if not _m:
            return None
        _main = _m.group(1).strip().split("·")[0].strip()  # 主坐标（·前）
        _m2 = _re.match(r"map\.([a-z0-9-]+)\.([a-z0-9-]+)", _main)
        if _m2:
            _node = "map." + _m2.group(1)
            if _node not in _VALID_PANELS:  # 旧长名方言 → 回退
                return None
            _l3 = None
            _m4 = _re.search(r"L3[：:]\s*([a-z0-9:-]+)", _main)
            if _m4:
                _l3 = _m4.group(1).strip()
            return {"map_node_id": _node, "map_subnode": _m2.group(2), "map_l3": _l3}
        _m3 = _re.match(r"map\.([a-z0-9-]+)", _main)
        if _m3:
            _node = "map." + _m3.group(1)
            if _node in _VALID_PANELS:
                return {"map_node_id": _node, "map_subnode": None, "map_l3": None}
        return None

    manual_n = 0
    okf_coord_hits = 0
    try:
        _mr = json.load(open(os.path.join(ROOT, "tools/agent-system-map/catalog/manual-registry-v0.json"), encoding="utf-8"))
        for _me in _mr.get("entries", []):
            _form = _me.get("form", "")
            _sub = "manuals" if _form in ("docs", "repair", "both") else "okf-entry"
            _coord = None
            if _sub == "okf-entry" and _me.get("path"):
                _coord = _parse_okf_coord(os.path.join(ROOT, _me["path"]))
            _node = (_coord or {}).get("map_node_id") or _me.get("map_node_id")
            _msub = (_coord or {}).get("map_subnode") or _sub
            _ml3 = (_coord or {}).get("map_l3") or _me.get("map_l3")
            if _coord and _coord.get("map_subnode"):
                okf_coord_hits += 1
            entities.append({
                "id": _me.get("manual_id"), "kind": "manual",
                "display_name": _me.get("title") or _me.get("manual_id"),
                "status": "registered", "manual_type": _me.get("manual_type"),
                "map_node_id": _node, "map_subnode": _msub,
                "map_l3": _ml3, "lanes": [], "source": "manual-registry",
                "authority": "tools/agent-system-map/catalog/manual-registry-v0.json",
                "path": _me.get("path"),
            })
            manual_n += 1
    except Exception as _e:
        print("manual mount failed (continue):", _e)

    # E-113（2026-08-12）：凭据实体挂载——credential-inventory 坐标化后投影（仅指针，零明文）
    cred_n = 0
    try:
        _ci = json.load(open(os.path.join(ROOT, "tools/credential_registry/credential-inventory-v0.json"), encoding="utf-8"))
        for _ce in _ci.get("credentials", []):
            if not _ce.get("map_node_id") or not _ce.get("map_subnode"):
                continue  # 未填坐标（❓ 待魅惑裁）不挂载，裁决后重跑即升
            _ref = _ce["storage"][0]["ref"] if _ce.get("storage") else _ce.get("service")
            entities.append({
                "id": "cred:" + _ce["cred_id"], "kind": "credential",
                "display_name": _ce.get("service") or _ref,
                "status": _ce.get("status"), "cred_type": _ce.get("type"),
                "map_node_id": _ce.get("map_node_id"), "map_subnode": _ce.get("map_subnode"),
                "map_l3": _ce.get("map_l3"), "lanes": [], "source": "credential-inventory",
                "authority": "tools/credential_registry/credential-inventory-v0.json",
                "storage_ref": _ref,  # 仅指针，零明文
            })
            cred_n += 1
    except Exception as _e:
        print("credential mount failed (continue):", _e)

    doc = {
        "projection_id": "task-snapshot-projection-v0",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "A+7天层：metadata-bound + active(open/in_progress/blocked) + 近7天关闭(<=7d，历史层不计活跃)；不含无归属；明确不扩全量 closed",
        "read_only": True,
        "counts": {"task_entities": sum(1 for e in entities if e["kind"] == "task"),
                   "metadata_bound": bound_n, "active_included": active_n,
                   "recent_closed_included": recent_closed_n, "beads_system_entities": 3,
                   "manual_entities": manual_n, "credential_entities": cred_n},
        "branch_status_counts": counts,
        "entities": entities,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print("task entities:", doc["counts"]["task_entities"],
          "| metadata_bound:", bound_n, "| active:", active_n, "| recent_closed:", recent_closed_n)
    print("branches:", len(counts), "| OUT:", OUT)


if __name__ == "__main__":
    main()
