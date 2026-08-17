# -*- coding: utf-8 -*-
"""Build Beads <-> business/asset/tool/system bidirectional relation registry (codex-temr.7).

Read-only. Emits a versioned JSON registry from:
  - bd export JSONL (task lifecycle authority)
  - multi-source snapshot entities (map projection)
  - deterministic label/source mapping rules

Does not write Beads, Desktop, MCP, Skill, credentials, or schedules.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone


REGISTRY_VERSION = "1.0.0"

# Label / keyword -> stable map-side entity ids (logical, not inventing secrets)
PILLAR_TO_ENTITY = {
    "pillar:agent-workbench": "domain:agent-workbench",
    "pillar:data-assets": "domain:data-assets",
    "pillar:overseas-infra": "domain:overseas-infra",
    "pillar:business-loop": "domain:business-loop",
    "pillar:desktop": "domain:desktop",
}

LANE_CLASS = {
    "lane:agent-control-surface": "system",
    "lane:agent-governance": "system",
    "lane:agent-tooling": "tool",
    "lane:agent-search-crawler": "tool",
    "lane:overseas-webapp-backend": "system",
    "lane:overseas-connectivity": "system",
    "lane:overseas-finance": "business",
    "lane:data-business-assets": "asset",
    "lane:data-knowledge-retrieval": "asset",
    "lane:data-media-assets": "asset",
    "lane:data-cognitive-kb": "asset",
    "lane:business-content": "business",
    "lane:business-conversion": "business",
    "lane:business-growth": "business",
    "lane:business-product-rd": "business",
}

LABEL_TO_ENTITY = {
    "skill-manager": "module:skill-manager",
    "mcp": "module:mcp-manager",
    "agent-hub": "module:agent-hub",
    "api-provider": "module:apikey-provider-manager",
    "desktop": "surface:desktop-console",
    "harness": "module:harness",
    "global-map": "map.global-system",
    "beads": "authority:beads",
    "loop-control": "module:loop-runtime",
    "thread-control": "module:loop-runtime",
    "data-asset-index": "domain:data-assets",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: str):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def read_jsonl(path: str):
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def issue_labels(issue) -> list[str]:
    labs = issue.get("labels") or []
    return [x for x in labs if isinstance(x, str)]


def map_links_for_issue(issue, entity_ids: set[str]) -> list[dict]:
    """Return forward links task -> entity with relation_type and rule_id."""
    links = []
    labels = issue_labels(issue)
    md = issue.get("metadata") or {}
    if not isinstance(md, dict):
        md = {}

    def add(entity_id: str, relation: str, rule: str, plane: str):
        links.append(
            {
                "from": issue.get("id"),
                "to": entity_id,
                "relation": relation,
                "rule_id": rule,
                "plane": plane,
                "direction": "task_to_entity",
            }
        )

    # map projection metadata (highest confidence structural)
    node = md.get("map_node_id")
    if node:
        eid = node if node.startswith("map.") else f"map:{node}"
        add(eid, "CLASSIFIED_UNDER", "metadata.map_node_id", "system")

    for lab in labels:
        if lab in PILLAR_TO_ENTITY:
            add(PILLAR_TO_ENTITY[lab], "BELONGS_TO_PILLAR", "label.pillar", "business")
        if lab in LANE_CLASS:
            plane = LANE_CLASS[lab]
            add(f"lane:{lab.split(':', 1)[1]}", "ROUTED_ON_LANE", "label.lane", plane)
        if lab in LABEL_TO_ENTITY:
            plane = "tool" if lab in {"mcp", "skill-manager", "api-provider"} else "system"
            if lab == "data-asset-index":
                plane = "asset"
            if lab in {"desktop"}:
                plane = "system"
            add(LABEL_TO_ENTITY[lab], "USES_OR_GOVERNED_BY", f"label.{lab}", plane)

    # title/keyword soft links only when exact entity exists in snapshot
    title = (issue.get("title") or "").lower()
    soft_rules = [
        ("skill", "module:skill-manager", "tool"),
        ("mcp", "module:mcp-manager", "tool"),
        ("provider", "module:apikey-provider-manager", "tool"),
        ("guardian", "map.guardian", "system"),
        ("harness", "module:harness", "system"),
        ("desktop", "surface:desktop-console", "system"),
    ]
    for key, eid, plane in soft_rules:
        if key in title and (eid in entity_ids or eid.startswith("map.") or eid.startswith("surface:") or eid.startswith("module:")):
            add(eid, "MENTIONS", f"title.keyword.{key}", plane)

    # dedupe by (from,to,relation)
    seen = set()
    out = []
    for link in links:
        k = (link["from"], link["to"], link["relation"])
        if k in seen or not link["from"] or not link["to"]:
            continue
        seen.add(k)
        out.append(link)
    return out


def ensure_logical_entities(snapshot_entities: list[dict]) -> dict[str, dict]:
    by_id = {e.get("id"): e for e in snapshot_entities if e.get("id")}
    # inject logical domain/lane entities if missing
    logical = {}
    for eid in list(PILLAR_TO_ENTITY.values()) + list(LABEL_TO_ENTITY.values()):
        logical[eid] = {
            "id": eid,
            "kind": "logical_domain" if eid.startswith("domain:") else "logical_module",
            "display_name": eid,
            "source": "bidirectional-registry-rules",
            "state_plane": "declared",
            "confidence": "medium",
        }
    for lab in LANE_CLASS:
        eid = f"lane:{lab.split(':', 1)[1]}"
        logical[eid] = {
            "id": eid,
            "kind": "lane",
            "display_name": lab,
            "source": "bidirectional-registry-rules",
            "state_plane": "declared",
            "plane_class": LANE_CLASS[lab],
            "confidence": "medium",
        }
    for node in ("map.root-governance", "map.harness", "map.guardian", "map.global-system"):
        logical[node] = {
            "id": node,
            "kind": "map_node",
            "display_name": node,
            "source": "global-map",
            "state_plane": "declared",
            "confidence": "high",
        }
    # snapshot wins on id collision for real entities
    merged = dict(logical)
    merged.update(by_id)
    return merged


def build(beads_path: str, snapshot_path: str) -> dict:
    issues = [i for i in read_jsonl(beads_path) if i.get("_type") != "memory"]
    snapshot = read_json(snapshot_path)
    entities = ensure_logical_entities(snapshot.get("entities") or [])
    entity_ids = set(entities)

    forward = []
    reverse_index = defaultdict(list)
    by_plane = Counter()
    by_relation = Counter()
    tasks_with_links = 0

    for issue in issues:
        links = map_links_for_issue(issue, entity_ids)
        if not links:
            continue
        tasks_with_links += 1
        for link in links:
            forward.append(link)
            by_relation[link["relation"]] += 1
            by_plane[link["plane"]] += 1
            reverse_index[link["to"]].append(
                {
                    "from": link["to"],
                    "to": link["from"],
                    "relation": link["relation"],
                    "rule_id": link["rule_id"],
                    "plane": link["plane"],
                    "direction": "entity_to_task",
                    "task_title": issue.get("title"),
                    "task_status": issue.get("status"),
                }
            )

    reverse = []
    for _eid, rows in reverse_index.items():
        reverse.extend(rows)

    # coverage: open/in_progress tasks with at least one non-dep link
    active = [i for i in issues if i.get("status") in {"open", "in_progress", "blocked"}]
    active_linked = sum(1 for i in active if map_links_for_issue(i, entity_ids))

    return {
        "registry_id": "beads-entity-bidirectional-registry-v1",
        "schema_version": REGISTRY_VERSION,
        "status": "generated-read-only",
        "beads_task": "codex-temr.7",
        "generated_at": utc_now(),
        "inputs": {
            "beads_export": beads_path.replace("\\", "/"),
            "snapshot": snapshot_path.replace("\\", "/"),
            "issue_count": len(issues),
            "snapshot_entity_count": len(snapshot.get("entities") or []),
        },
        "counts": {
            "forward_links": len(forward),
            "reverse_links": len(reverse),
            "tasks_with_links": tasks_with_links,
            "active_tasks": len(active),
            "active_tasks_with_links": active_linked,
            "by_relation": dict(by_relation),
            "by_plane": dict(by_plane),
            "entity_count_merged": len(entities),
        },
        "query": {
            "forward_task_to_entity": "filter forward_links where from == task_id",
            "reverse_entity_to_task": "filter reverse_links where from == entity_id",
        },
        "entities": list(entities.values()),
        "forward_links": forward,
        "reverse_links": reverse,
        "rules": {
            "pillar_map": PILLAR_TO_ENTITY,
            "lane_class": LANE_CLASS,
            "label_map": LABEL_TO_ENTITY,
        },
        "non_claims": [
            "Does not mutate Beads lifecycle fields",
            "Does not claim business CRM truth beyond label projection",
            "Soft title keyword links are medium confidence MENTIONS only",
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--beads", required=True)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    reg = build(args.beads, args.snapshot)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    c = reg["counts"]
    print("REGISTRY OK")
    print(f"  forward={c['forward_links']} reverse={c['reverse_links']}")
    print(f"  tasks_with_links={c['tasks_with_links']} active_linked={c['active_tasks_with_links']}/{c['active_tasks']}")
    print(f"  by_plane={c['by_plane']}")
    print(f"  by_relation={c['by_relation']}")
    print(f"  out={args.out}")


if __name__ == "__main__":
    main()
