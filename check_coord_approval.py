# -*- coding: utf-8 -*-
"""手册/OKF 三级坐标审核闸（E-133 魅惑令：坐标审核一步不能省，越批越准）

用法：
  python check_coord_approval.py --pending      # 列出待审坐标清单（魅惑审核用）
  python check_coord_approval.py --approve <id> # 魅惑批（id=manual_id 或 bundle_id，可多个空格分隔）
  python check_coord_approval.py --check        # 门闸模式：pending>0 返回 1（D8 挂主链用）

规则：
  - coord_approval 值域：legacy（存量不追责）/ pending（新沉淀待审）/ approved（魅惑批过）
  - 沉淀手册/OKF 新坐标默认 pending；D8 门闸 pending>0=红灯（提醒魅惑审核）
  - 人只批坐标（审核动作），其余全自动
"""
import json
import os
import sys

CAT = r"F:\codex\tools\agent-system-map\catalog"
FILES = [
    (os.path.join(CAT, "manual-registry-v0.json"), "entries", "manual_id", "手册"),
    (os.path.join(CAT, "okf-registry-v0.json"), "bundles", "bundle_id", "OKF"),
]


def load_all():
    out = []
    for path, key, idf, label in FILES:
        d = json.load(open(path, encoding="utf-8-sig"))
        for it in d.get(key, []):
            out.append((path, key, idf, label, it))
    return out


def pending_list():
    rows = []
    for path, key, idf, label, it in load_all():
        if it.get("coord_approval") == "pending":
            rows.append((label, it.get(idf), it.get("map_node_id"), it.get("map_subnode"), it.get("map_l3") or "-", it.get("title", "")))
    return rows


def approve(ids):
    n = 0
    for path, key, idf, label, it in load_all():
        if it.get(idf) in ids and it.get("coord_approval") == "pending":
            it["coord_approval"] = "approved"
            it["coord_approved_by"] = "魅惑"
            it["coord_approved_at"] = "2026-08-15"
            n += 1
            # 写回
            d = json.load(open(path, encoding="utf-8-sig"))
            for x in d.get(key, []):
                if x.get(idf) == it.get(idf):
                    x.update(it)
            json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return n


def main():
    if len(sys.argv) < 2:
        print("usage: check_coord_approval.py --pending | --approve <id>... | --check")
        return 2
    cmd = sys.argv[1]
    if cmd == "--pending":
        rows = pending_list()
        if not rows:
            print("无待审坐标（全 legacy/approved）")
            return 0
        print("待审坐标 %d 条：" % len(rows))
        for r in rows:
            print("  [%s] %s | %s%s%s | %s" % (r[0], r[1], r[2], "." + r[3] if r[3] else "", "." + r[4] if r[4] != "-" else "", r[5][:30]))
        return 0
    if cmd == "--approve":
        if len(sys.argv) < 3:
            print("usage: --approve <id>...")
            return 2
        n = approve(set(sys.argv[2:]))
        print("已批准 %d 条坐标" % n)
        return 0 if n > 0 else 1
    if cmd == "--check":
        rows = pending_list()
        if rows:
            print("D8 坐标审核: pending=%d（待魅惑批）" % len(rows))
            for r in rows[:5]:
                print("  [%s] %s" % (r[0], r[1]))
            return 1
        print("D8 坐标审核: pending=0（全 legacy/approved）")
        return 0
    if cmd == "--pending-domains":
        # 按域分组列出待审（魅惑逐域点名用）
        from collections import defaultdict
        groups = defaultdict(list)
        for label, mid, node, sub, l3, title in pending_list():
            dom = "%s.%s" % (node, sub if sub else "(空)")
            groups[dom].append(mid)
        print("待审 %d 域：" % len(groups))
        for dom, mids in sorted(groups.items(), key=lambda x: -len(x[1])):
            print("  %-28s %2d 条: %s" % (dom, len(mids), " ".join(mids[:4]) + (" ..." if len(mids) > 4 else "")))
        return 0
    if cmd == "--approve-domain":
        if len(sys.argv) < 3:
            print("usage: --approve-domain <map.node.subnode>")
            return 2
        dom = sys.argv[2]
        ids = [mid for _, mid, node, sub, l3, _ in pending_list()
               if "%s.%s" % (node, sub if sub else "(空)") == dom]
        if not ids:
            print("该域无待审条目:", dom)
            return 1
        n = approve(set(ids))
        print("已批准域 %s：%d 条" % (dom, n))
        return 0 if n > 0 else 1
    print("未知命令:", cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
