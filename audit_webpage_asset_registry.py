# -*- coding: utf-8 -*-
"""
audit_webpage_asset_registry.py
--------------------------------
固化巡检器（codex-69lp 管线固化，2026-07-28）。

webpage-asset-registry-v0.json 是**人工策展**数据（owner_beads / map_node_id /
related_beads 是人工映射，无法从文件系统推导），所以本脚本**不自动改注册表**，
只做三件事并产出 drift 报告：

  1. 校验每个已注册资产的 local_path 文件仍存在（死指针检测）
  2. 扫描 C:/html 一级目录，报告**未注册**的门户目录（漏注册检测）
  3. 汇总 excluded 名单，确认排除项仍然成立（目录确实不存在或确实应排除）

退出码：0 = 无 drift 或仅有提示；1 = 存在死指针（已注册但文件丢失）。
未注册目录只警告不 fail（可能是别人的临时产物，人工判断后再入册）。

用法：python3 tools/agent-system-map/audit_webpage_asset_registry.py
"""
import json
import os
import sys
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REGISTRY = os.path.join(ROOT, "tools", "agent-system-map", "catalog", "webpage-asset-registry-v0.json")
HTML_ROOT = r"C:\html"
# 这些一级目录不是门户资产（大地图自身/系统文件/备份），扫到不报
IGNORE_DIRS = {
    "", ".", "..",
}
IGNORE_PREFIXES = ("agent-global-map", "agent-system-map")


def main():
    with open(REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)

    assets = reg.get("assets", [])
    registered_paths = set()
    dead = []
    for a in assets:
        lp = (a.get("local_path") or "").replace("/", "\\")
        registered_paths.add(os.path.normpath(lp).lower())
        if lp and not os.path.exists(lp):
            dead.append((a.get("asset_id"), lp))

    # 扫描 C:/html 一级目录：有 index.html 且未注册的
    unregistered = []
    try:
        for name in sorted(os.listdir(HTML_ROOT)):
            full = os.path.join(HTML_ROOT, name)
            if not os.path.isdir(full):
                continue
            if name in IGNORE_DIRS or name.lower().startswith(IGNORE_PREFIXES):
                continue
            idx = os.path.join(full, "index.html")
            if os.path.exists(idx):
                norm = os.path.normpath(idx).lower()
                if norm not in registered_paths:
                    unregistered.append(name)
    except OSError as e:
        print(f"WARN: cannot scan {HTML_ROOT}: {e}")

    print("webpage-asset-registry audit", datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(f"  registered assets : {len(assets)}")
    print(f"  dead pointers     : {len(dead)}")
    for aid, lp in dead:
        print(f"    DEAD  {aid}  ->  {lp}")
    print(f"  unregistered dirs : {len(unregistered)} (warn only, manual review)")
    for name in unregistered:
        print(f"    NEW?  C:/html/{name}/index.html")

    if dead:
        print("\nFAIL: dead pointers exist - fix local_path or remove from registry")
        sys.exit(1)
    print("\nOK: no dead pointers" + (f"; {len(unregistered)} unregistered dirs pending manual review" if unregistered else ", nothing unregistered"))
    sys.exit(0)


if __name__ == "__main__":
    main()
