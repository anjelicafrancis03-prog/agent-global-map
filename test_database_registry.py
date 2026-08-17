# -*- coding: utf-8 -*-
"""E-123 阶段5：database-registry 门闸（独立脚本挂链，不塞 T 编号）
校验：①JSON 合法 ②必填字段非空 ③path 存在 ④category 值域 ⑤lifecycle 值域 ⑥map_coordinate 结构
退出码：0=全过；1=有 FAIL
"""
import json
import os
import sys

ROOT = r"F:\codex"
REG_PATH = os.path.join(ROOT, "tools", "agent-system-map", "catalog", "database-registry-v0.json")

CATEGORIES = {"business-db", "system-db", "registry-json", "file-corpus"}
LIFECYCLES = {"active", "pilot", "deprecated", "archived"}
REQUIRED = ("id", "name", "engine", "path", "lifecycle", "category",
            "map_coordinate", "database_ref", "updated_at")


def main():
    fails = []
    try:
        d = json.load(open(REG_PATH, encoding="utf-8-sig"))
    except Exception as e:
        print("FAIL unreadable: %s" % e)
        return 1
    entries = d.get("entries") or []
    print("D1 database-registry: 条目=%d" % len(entries))
    for e in entries:
        eid = e.get("id", "?")
        for f in REQUIRED:
            if f not in e or e[f] in (None, ""):
                fails.append("%s:missing-%s" % (eid, f))
        if e.get("category") not in CATEGORIES:
            fails.append("%s:bad-category-%s" % (eid, e.get("category")))
        if e.get("lifecycle") not in LIFECYCLES:
            fails.append("%s:bad-lifecycle-%s" % (eid, e.get("lifecycle")))
        path = e.get("path")
        if path and not os.path.exists(path):
            fails.append("%s:path-missing-%s" % (eid, path))
        coord = e.get("map_coordinate") or {}
        if isinstance(coord, str):
            coord = {"map_node_id": coord.split(".")[1], "map_subnode": ".".join(coord.split(".")[2:])} if len(coord.split(".")) >= 3 else {}
        if not (coord.get("map_node_id") and coord.get("map_subnode")):
            fails.append("%s:bad-coordinate" % eid)
    if fails:
        for f in fails[:15]:
            print("FAIL " + f)
        print("D1 FAIL count=%d" % len(fails))
        return 1
    print("D1 PASS: 全字段合法，路径全在，分类/生命周期值域合规")
    return 0


if __name__ == "__main__":
    sys.exit(main())
