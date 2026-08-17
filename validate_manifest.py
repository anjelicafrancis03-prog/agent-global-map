#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_manifest.py — E-41 步1 run-manifest 三段校验器。

L1 值域（坐标/effort 反查权威） / L2 结构（三段完整） /
L3 一致性（recon.missing ∈ predict 声明） / 回读字节一致 / BOM 检查。

用法：
  python validate_manifest.py <manifest.md>          # 校验单个 manifest
  python validate_manifest.py --all                  # 校验 reports/run-manifests 全部（存量宽松模式）
  python validate_manifest.py --selftest             # 内置好/坏样本自测

退出码：0=绿，1=红（有 fails）。
"""
import io
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CAT = os.path.join(ROOT, "tools", "agent-system-map", "catalog")
EFFORT_RULES = os.path.join(ROOT, "okf-bundles", "beads-classification", "effort-rules-v0.json")

EFFORT_VALUES = None
VALID_SUBNODES = set()
VALID_NODES = set()


def load_authority():
    global EFFORT_VALUES, VALID_SUBNODES, VALID_NODES
    try:
        er = json.load(io.open(EFFORT_RULES, encoding="utf-8-sig"))
        EFFORT_VALUES = set(er.get("effort_values") or [])
    except (IOError, ValueError):
        EFFORT_VALUES = set()
    try:
        pm = json.load(io.open(os.path.join(CAT, "panel-manifest-v0.json"), encoding="utf-8-sig"))
        for p in pm.get("panels", []):
            for s in (p.get("subnodes") or []):
                sid = s.get("subnode_id") if isinstance(s, dict) else s
                if sid:
                    VALID_SUBNODES.add(sid)
        # E-86 修复：VALID_NODES 从 panels[].panel_id 推导（map. 前缀拼接），
        # 不再依赖顶层 map_nodes 键（manifest 无该键 → 旧逻辑恒空 → 越域检查永不触发）
        for p in pm.get("panels", []):
            pid = p.get("panel_id")
            if pid:
                VALID_NODES.add("map." + pid)
    except (IOError, ValueError):
        pass


SIMPLE_WAIVED = {"dac", "risk", "resource_chain", "landing", "recon"}
HEADER_FIELDS = ["status", "owner", "generated", "source"]
PREDICT_KEYS = ["map_node_id", "map_subnode", "effort", "effort_reason", "produces_asset",
                "guardian_need", "priority", "risk", "origin", "estimate_minutes", "predict_locked_at"]


def check_bom(path):
    """BOM 检查：允许 UTF-8 BOM 缺失；但文件若以 \ufeff 开头则提示（写后回读一致性用）"""
    with io.open(path, "rb") as f:
        head = f.read(3)
    return {"has_bom": head == b"\xef\xbb\xbf", "note": "BOM present" if head == b"\xef\xbb\xbf" else "no BOM"}


def check_roundtrip(path):
    """回读字节一致：读回文件并确认可解码、非空、无非法控制字符"""
    with io.open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return False, "UTF-8 解码失败: %s" % e
    bad_ctrl = [c for c in text if ord(c) < 32 and c not in "\n\r\t"]
    if bad_ctrl:
        return False, "非法控制字符: %r" % (bad_ctrl[:5],)
    return True, "roundtrip OK (%d B)" % len(raw)


def parse_predict_section(md):
    """从 ① predict 段提取键值（- key: value 行）"""
    m = re.search(r"## ① predict.*?(?=\n## |\Z)", md, re.S)
    if not m:
        return {}
    kv = {}
    for line in m.group(0).splitlines():
        line = line.strip()
        if line.startswith("- ") and ": " in line:
            k, v = line[2:].split(": ", 1)
            kv[k.strip()] = v.strip()
    return kv


def validate_one(path, strict=True):
    fails = []
    warns = []

    # 0. 存在性
    if not os.path.isfile(path):
        return {"path": path, "ok": False, "fails": ["file not found"], "warns": []}
    md = io.open(path, encoding="utf-8-sig").read()

    # 0.5 类型识别：任务 manifest（含 task_id 或 ① predict 段标题）严格校验；
    #     纯执行记录（run-manifest 传统格式）仅做 BOM/回读/头部检查
    is_task_manifest = ("task_id" in md) or ("## ① predict" in md) or ("## predict" in md)
    if not is_task_manifest and not strict:
        bom = check_bom(path)
        ok_rt, rt_msg = check_roundtrip(path)
        if not ok_rt:
            fails.append("roundtrip: " + rt_msg)
        for hf in HEADER_FIELDS:
            if not re.search(r"(?m)^[-*]\s*" + hf + r"\s*[:：]", md) and not ("## " + hf) in md:
                warns.append("header missing (execution record): %s" % hf)
        return {"path": path, "ok": not fails, "fails": fails, "warns": warns}

    # BOM + 回读
    bom = check_bom(path)
    ok_rt, rt_msg = check_roundtrip(path)
    if not ok_rt:
        fails.append("roundtrip: " + rt_msg)
    else:
        warns.append("roundtrip: " + rt_msg)

    # 1. 头部必填
    head_low = md.lower()
    for hf in HEADER_FIELDS:
        if not re.search(r"(?m)^[-*]\s*" + hf + r"\s*[:：]", md) and not ("## " + hf) in md:
            fails.append("header missing: %s" % hf)

    # 2. 三段存在（predict/landing 必在；chain/recon 一期槽位）
    has_predict = "## ① predict" in md or "## predict" in md
    has_chain = "## ② chain" in md or "## chain" in md
    has_landing = "## ③ landing" in md or "## landing" in md
    has_recon = "## ④ recon" in md or "## recon" in md
    if not has_predict:
        fails.append("L2: predict 段缺失")
    if not has_landing:
        # landing 可 simple 裁剪（标注 [simple-免填] 则可缺）
        if "[simple-免填]" not in md:
            fails.append("L2: landing 段缺失（非 simple 裁剪）")
    if not has_chain:
        warns.append("L2: chain 段缺失（一期允许）")
    if not has_recon:
        warns.append("L2: recon 段缺失（simple 或未 close）")

    # 3. predict 键值域（L1）
    p = parse_predict_section(md)
    if p.get("map_node_id") and p["map_node_id"] not in VALID_NODES and "map." in p["map_node_id"]:
        # E-86 修复后：VALID_NODES 从 panels[].panel_id 推导（map. 前缀），越域检查真触发
        if VALID_NODES:
            fails.append("L1: map_node_id 越域 %s" % p["map_node_id"])
    if p.get("map_subnode") and VALID_SUBNODES and p["map_subnode"] not in VALID_SUBNODES:
        # 允许「面板级=缺省」；子域必须 ∈ 28
        if p["map_subnode"] not in ("面板级", "none"):
            fails.append("L1: map_subnode 越域 %s" % p["map_subnode"])
    if p.get("effort") and EFFORT_VALUES and p["effort"] not in EFFORT_VALUES:
        fails.append("L1: effort 越域 %s（合法 %s）" % (p["effort"], sorted(EFFORT_VALUES)))
    if p.get("guardian_need") and p["guardian_need"] not in ("none", "candidate", "immediate"):
        fails.append("L1: guardian_need 越域 %s" % p["guardian_need"])
    if not p.get("produces_asset"):
        fails.append("L1: produces_asset 缺失（产物预言必填，防空泛任务）")
    # predict_locked_at 必填（非 simple 裁剪）
    if not p.get("predict_locked_at") and "[simple-免填]" not in md:
        fails.append("L1: predict_locked_at 缺失（锁定时刻必填）")

    # 4. L3 一致性：recon.missing_outputs ∈ predict.produces 声明
    miss = re.search(r"missing_outputs:\s*\[([^\]]*)\]", md)
    if miss and miss.group(1).strip() and miss.group(1).strip() != "[]":
        missing_items = [x.strip().strip("'\"") for x in miss.group(1).split(",") if x.strip()]
        produced = p.get("produces_asset", "")
        for item in missing_items:
            if item and produced and item.lower() not in produced.lower() and "simple-免填" not in md:
                fails.append("L3: missing_outputs %s 未在 predict.produces 声明" % item)

    # 5. 简单裁剪一致性：声明了 [simple-免填] 就不能再写详细段
    if "[simple-免填]" in md:
        for w in SIMPLE_WAIVED:
            if w == "resource_chain" and has_chain and "| 1 |" in md:
                fails.append("simple 声明免填 resource_chain 但 chain 有内容")

    return {"path": path, "ok": not fails, "fails": fails, "warns": warns}


def selftest():
    """内置好/坏样本自测（E-41 验证点）"""
    good = """# 好样本 · Run Manifest

- status: 已完成
- owner: 普通1
- generated: 2026-08-06T14:00:00+08:00
- source: reports/run-manifests/good-sample

## 状态：已完成

## ① predict（预言层 · 创建时派单方填）

- map_node_id: map.search
- map_subnode: graph
- effort: complex-slow
- effort_reason: 全局索引重建
- produces_asset: codegraph.db(rebuilt)
- guardian_need: candidate
- priority: P1
- risk: structural
- origin: appointed
- estimate_minutes: 90
- predict_locked_at: 2026-08-06T14:00:00+08:00

## ② chain（资源链 · 执行中记 · 一期留空）

> 一期槽位，内容空。

| # | t | action | coord | note |
|---|----|--------|-------|------|

## ③ landing（落地层 · close 前执行方填）

产物落点:
  - reports/e05/receipt.md
统计:
  - actual_minutes: 1271

## ④ recon（对账段 · close 时机器生成）

estimate: 90min → 1271min（14x 偏差）
missing_outputs: []
"""
    bad_missing_section = good.replace("## ③ landing", "## ③X landing")  # 缺段样本 A：landing 缺（非 simple 裁剪 → 红）
    bad_l3 = good.replace("missing_outputs: []", "missing_outputs: ['未预言的东西']")
    bad_effort = good.replace("effort: complex-slow", "effort: super-hard")
    bad_bom = good

    cases = [
        ("good", good, True),
        ("bad-missing-landing-section", bad_missing_section, False),  # landing 缺段 → 红
        ("bad-l3-unpredicted-missing", bad_l3, False),                # missing 未在 predict 声明 → 红
        ("bad-effort-domain", bad_effort, False),                     # effort 越域 → 红
    ]
    for name, content, expect_ok in cases:
        tmp = os.path.join(ROOT, "tmp", "vm_%s.md" % name)
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with io.open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        r = validate_one(tmp)
        status = "PASS" if r["ok"] == expect_ok else "FAIL"
        print("[%s] %-24s ok=%s fails=%s" % (status, name, r["ok"], r["fails"][:3]))
        os.remove(tmp)
    return 0


def check_coord_headers():
    """E-42 扩展：№↔坐标头一致性门（扫描 manual-registry 87 条对应手册文件）。
    有 № 无坐标头=红；坐标值域（sub）反查 panel-manifest 27 子域；❓ 无 node 条目=pending 不算红。
    """
    import glob as _glob
    reg_path = os.path.join(CAT, "manual-registry-v0.json")
    if not os.path.isfile(reg_path):
        print("coords: manual-registry-v0.json 缺失（跳过）")
        return True
    reg = json.load(io.open(reg_path, encoding="utf-8-sig")) or {}
    ents = reg.get("entries", [])
    fails = []
    pend = []
    ok = 0
    seen_paths = set()
    for e in ents:
        mid = e.get("manual_id", "")
        num = e.get("map_number")
        node = e.get("map_node_id")
        path = e.get("path")
        if not node:
            pend.append(mid)  # ❓ 待魅惑批，不算红
            continue
        if not path:
            continue
        # 同一文件只验一次（子条目共享父文件坐标头）
        if path in seen_paths:
            continue
        seen_paths.add(path)
        if not os.path.isfile(os.path.join(ROOT, path)):
            fails.append("%s: 手册文件缺失 %s" % (mid, path))
            continue
        head = io.open(os.path.join(ROOT, path), encoding="utf-8", errors="ignore").read(4000)
        # E-44b 收窄：坐标头行必须含 map. 坐标格式（防「坐标：」字面/说明行误抓），前 60 行内取首个
        m = None
        for _hl in head.splitlines()[:60]:
            if "> 坐标：" in _hl and re.search(r"map\.[a-z-]+(\.[a-z-]+)?", _hl):
                m = re.search(r"> 坐标：([^\n]+)", _hl)
                break
        if not m:
            fails.append("%s: 有 №%s 无坐标头" % (mid, num))
            continue
        line = m.group(1)
        if num and ("№%s" % num) not in line:
            fails.append("%s: 坐标头 № 不匹配（文件 %s vs registry %s）" % (mid, line.split("|")[-1].strip(), num))
        # 坐标值域：主坐标的 sub ∈ VALID_SUBNODES
        mm = re.search(r"map\.[a-z-]+\.([a-z-]+)", line)
        if mm:
            sub = mm.group(1)
            if sub not in VALID_SUBNODES:
                fails.append("%s: 坐标 sub=%s 越界（27 值域外）" % (mid, sub))
        ok += 1
    print("coords №↔坐标头门: 已标=%d pending(❓)=%d 坏=%d" % (ok, len(pend), len(fails)))
    for f in fails[:10]:
        print("    FAIL: " + f)
    return len(fails) == 0


def main():
    load_authority()
    if "--selftest" in sys.argv:
        return selftest()
    if "--coords" in sys.argv:
        return 0 if check_coord_headers() else 1
    paths = []
    if "--all" in sys.argv:
        base = os.path.join(ROOT, "reports", "run-manifests")
        for dirpath, _, files in os.walk(base):
            if "manifest.md" in files:
                paths.append(os.path.join(dirpath, "manifest.md"))
    else:
        paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 1
    all_ok = True
    for p in paths:
        # --all 存量宽松（传统执行记录不严格三段）；单文件严格（任务 manifest）
        r = validate_one(p, strict=("--all" not in sys.argv))
        tag = "GREEN" if r["ok"] else "RED"
        print("[%s] %s" % (tag, r["path"]))
        for f in r["fails"]:
            print("    FAIL: " + f)
        for w in r["warns"]:
            print("    warn: " + w)
        if not r["ok"]:
            all_ok = False
    return 0 if all_ok else 1


if __name__ == "__main__":
    load_authority()
    sys.exit(main())
