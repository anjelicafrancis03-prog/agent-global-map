#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
manual_pointers_resolve — 双手册覆盖指针校验（manual-coverage-index.json 的 validator）

校验规则（仿 validate-agent-families.ps1 的 ok:true 思路）：
- source=independent      → 检查 ref 文件存在（相对 F:\codex 根）。
- source=monolith-section → 检查 anchor 标题文本存在于 docs/agent-repair-manual.md。
- status=missing          → 已知缺口，不算悬空，单列报告（不静默，但也不 fail）。

退出码：
- 0 = 所有"非 missing"指针都能解析（ok:true），缺口仅报告。
- 1 = 存在悬空指针（指向不存在的文件/section）→ fail。

接入 B1 重建：在现有 4 项 checks 后调用本脚本，非零退出即判 manual_pointers_resolve 失败。
"""
import json, os, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = r"F:\codex"
INDEX = os.environ.get("MANUAL_COVERAGE_INDEX") or os.path.join(
    ROOT, r"tools\agent-system-map\catalog\manual-coverage-index.json")
MONOLITH = os.path.join(ROOT, r"docs\agent-repair-manual.md")


def load_monolith():
    try:
        with open(MONOLITH, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except FileNotFoundError:
        return None


def main():
    if not os.path.exists(INDEX):
        print(f"[FAIL] index 不存在: {INDEX}")
        return 1
    idx = json.load(open(INDEX, encoding="utf-8"))
    mono = load_monolith()
    objects = idx.get("objects", {})

    dangles = []   # 悬空指针（fail）
    gaps = []      # 已知缺口（报告）
    resolved = 0

    for obj, manuals in objects.items():
        for kind in ("usage", "repair"):
            m = manuals.get(kind)
            if not m:
                continue
            status = m.get("status")
            src = m.get("source")
            if status == "missing":
                gaps.append(f"{obj} · {kind}: {m.get('note','missing')}")
                continue
            ref = m.get("ref")
            if src == "independent":
                path = os.path.join(ROOT, ref) if not os.path.isabs(ref) else ref
                if os.path.exists(path):
                    resolved += 1
                else:
                    dangles.append(f"{obj} · {kind}: independent ref 不存在 -> {ref}")
            elif src == "monolith-section":
                if mono is None:
                    dangles.append(f"{obj} · {kind}: 单体维修分册不存在 -> {MONOLITH}")
                    continue
                anchor = m.get("anchor", "")
                if anchor and anchor in mono:
                    resolved += 1
                else:
                    dangles.append(f"{obj} · {kind}: 单体 anchor 未命中 -> {anchor!r}")

    print("=" * 60)
    print("manual_pointers_resolve 校验")
    print(f"  解析成功: {resolved}")
    print(f"  已知缺口(missing, 报告不fail): {len(gaps)}")
    for g in gaps:
        print(f"    [gap] {g}")
    print(f"  悬空指针(dangle, FAIL): {len(dangles)}")
    for d in dangles:
        print(f"    [DANGLE] {d}")
    print("=" * 60)

    if dangles:
        print("ok:false — 存在悬空指针，需修复索引或对应手册/锚点。")
        return 1
    print("ok:true — 所有非缺口指针均可解析。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
