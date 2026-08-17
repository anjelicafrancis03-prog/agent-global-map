#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
validate_source_closure.py — 来源准入全链闭合校验器（codex-ygjz P1-5）

对 source-registry 里每个 active 源，四环必须闭合，缺一即 fail-closed：
  1) admission：在 source-admissions/admission-index-v1.json 有 root-approved 条目
  2) handler：build_multi_source_snapshot.py 的 HANDLERS 表有它，或声明的外部消费方
  3) view matrix：view-responsibility-matrix-v1.json 至少一个 surface 的
     authority_sources_admitted 含它
  4) 反向：admission index 里 root-approved 的源也必须在 registry 且非 deferred

出口：全闭 exit 0；任一缺口 exit 1 并打印缺口清单。只读，不改任何文件。
"""
import json, io, os, re, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = r"F:\codex"
REG = os.path.join(ROOT, r"tools\agent-system-map\catalog\source-registry.json")
IDX = os.path.join(ROOT, r"docs\architecture\source-admissions\admission-index-v1.json")
MATRIX = os.path.join(ROOT, r"docs\architecture\view-responsibility-matrix-v1.json")
SNAP = os.path.join(ROOT, r"tools\agent-system-map\build_multi_source_snapshot.py")
REBUILD = os.path.join(ROOT, r"tools\agent-system-map\rebuild.ps1")

# beads 不进 HANDLERS，真实消费方（复验修正：必须覆盖 rebuild 链上的真实调用，不是字符串声明）：
#   build_task_projection.py（step 2，经 BEADS_EXPORT_JSONL）+ build_map_website.py（step 5，argv[3]）
# 校验器会核对这两个脚本 (a) 真实存在 (b) 被 rebuild.ps1 实际引用——删掉引用即 FAIL。
DECLARED_EXTERNAL_CONSUMERS = {"beads": ["build_task_projection.py", "build_map_website.py"]}


def load(p):
    return json.load(io.open(p, encoding="utf-8"))


def main():
    reg = load(REG)
    idx = load(IDX)
    matrix = load(MATRIX)
    snap_src = io.open(SNAP, encoding="utf-8").read()
    rebuild_src = io.open(REBUILD, encoding="utf-8").read()

    m = re.search(r"HANDLERS\s*=\s*\{(.*?)\}", snap_src, re.S)
    handlers = set(re.findall(r"'([a-z0-9\-]+)'\s*:", m.group(1))) if m else set()

    approved = {}
    for a in idx.get("admissions", []):
        sid = a.get("source_id") or a.get("sourceId")
        if sid:
            approved[sid] = a.get("status")

    admitted_views = {}
    for s in matrix.get("surfaces", []):
        for sid in s.get("authority_sources_admitted", []) or []:
            admitted_views.setdefault(sid, []).append(s.get("view_id"))

    problems = []
    checked = 0
    for s in reg.get("sources", []):
        sid = s.get("sourceId")
        if s.get("status") != "active":
            continue
        checked += 1
        st = approved.get(sid)
        if st != "root-approved":
            problems.append("FAIL: %s active but admission status=%s (need root-approved entry in admission-index-v1.json)" % (sid, st or "MISSING"))
        if sid not in handlers and sid not in DECLARED_EXTERNAL_CONSUMERS:
            problems.append("FAIL: %s active but no handler in HANDLERS and no declared external consumer (fail-closed)" % sid)
        for consumer in DECLARED_EXTERNAL_CONSUMERS.get(sid, []):
            cpath = os.path.join(ROOT, "tools", "agent-system-map", consumer)
            if not (os.path.isfile(cpath) or os.path.isfile(os.path.join(ROOT, "tools", "agent-system-map", "web", consumer))):
                problems.append("FAIL: %s declared consumer %s does not exist" % (sid, consumer))
            elif consumer not in rebuild_src:
                problems.append("FAIL: %s declared consumer %s exists but is NOT invoked by rebuild.ps1 (fake closure)" % (sid, consumer))
        if sid not in admitted_views:
            problems.append("FAIL: %s active but not in any view-matrix surface authority_sources_admitted" % sid)

    for sid, st in approved.items():
        if st == "root-approved":
            rs = next((x for x in reg.get("sources", []) if x.get("sourceId") == sid), None)
            if rs is None:
                problems.append("FAIL: %s root-approved in index but absent from source-registry" % sid)

    print("closure check: %d active sources, handlers=%d, index-approved=%d, view-admitted=%d"
          % (checked, len(handlers), len(approved), len(admitted_views)))
    if problems:
        for p in problems:
            print(p)
        print("CLOSURE FAIL (%d problems)" % len(problems))
        return 1
    print("CLOSURE OK: all active sources closed (admission + handler + view matrix)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
