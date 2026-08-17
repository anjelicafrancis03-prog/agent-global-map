# -*- coding: utf-8 -*-
"""check_e_number_collision.py — E 编号撞号检测门（E-编号注册制轻量替代，2026-08-17 总控1）

背景：E-136 撞号（维修手册章节索引 vs 大地图6缺陷整改，两任务同名）。
审核结论（审核-E编号注册制必要性-20260817.md）：不立独立注册表（过度工程），
真问题是取号没查 git 提交历史。最轻根治 = 提交数异常自动告警。

原理：正常一个 E 号对应一个任务，git 提交信息里该号出现次数应有限（1-5 次）；
若单号提交数 > THRESHOLD（默认 8），说明可能有多个任务共用该号 → WARN 告警。

已知边界（2026-08-17 再审核记录）：阈值法是「撞号且单号总提交数 > 8」才捕获。
低提交量撞号（如两任务各 4 次=合计 8 不触发）会漏检——该场景靠取号前人工双源查
（工作区文件名 + git log --grep）兜底，脚本只做机器自动告警，不替代人工。

用法：
  python check_e_number_collision.py            # 全量扫描最近 N 天提交，输出异常号
  python check_e_number_collision.py --days 30  # 自定义窗口
退出码：0 = 无异常（PASS）；1 = 有异常号（主链红灯）
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

ROOT = r"F:\codex"
THRESHOLD = 8  # 单号提交数上限，超过视为疑似撞号（E-127 单任务 5 次=正常多批次开发上限，5 会误报，定 8）
# 边界用「前后非 ASCII 字母数字」而非 \b：\b 在 Python 里把 CJK 当 word 字符，
# 中文贴边写法（如「E-136修复手册」「编号E-137」）会漏检（2026-08-17 再审核发现）。
# 同时仍防 E-1350 / E-135M / xE-135y / CE-136 之类非 E 号上下文误计。
E_PATTERN = re.compile(r"(?<![A-Za-z0-9])E-1\d{2}(?![A-Za-z0-9])")
# 已知撞号豁免：已通过文件互注 alias 止损的历史撞号，不重复报警（防主链常红）。
# 新增撞号在此登记（编号 → 说明）；新任务取号请先查 git log，勿新增豁免。
KNOWN_COLLISIONS = {
    "E-136": "维修手册章节索引 vs 大地图6缺陷整改（2026-08-16，双方文件已互注 alias）",
}


def scan(days: int) -> tuple[list[str], dict[str, int]]:
    """git log 近 days 天提交信息，统计每个 E 号出现次数。返回 (异常号, 全量计数)。"""
    cmd = [
        "git", "-C", ROOT, "log",
        f"--since={days} days ago",
        "--format=%s",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if proc.returncode != 0:
        raise RuntimeError("git log failed: %s" % proc.stderr.strip())

    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        for m in E_PATTERN.findall(line):
            counts[m] = counts.get(m, 0) + 1

    abnormal_raw = {num: cnt for num, cnt in counts.items() if cnt > THRESHOLD}
    # 剔除已豁免的历史撞号
    abnormal = []
    for num, cnt in abnormal_raw.items():
        if num in KNOWN_COLLISIONS:
            print(f"E-number info: {num} known collision (exempt): {KNOWN_COLLISIONS[num]}")
        else:
            abnormal.append(f"{num}={cnt}")
    return abnormal, counts


def main(argv: list[str] | None = None) -> int:
    # 挂主链（importlib 加载）时 argv 会继承主链的 sys.argv——绝不能解析它，
    # 否则主链带参运行 D9 会 SystemExit(2) 误红（2026-08-17 审核发现）。
    # 仅 CLI 直跑（__main__）才解析参数；挂主链一律默认值。
    days = 30
    show_all = False
    if argv is not None:
        parser = argparse.ArgumentParser(description="E 编号撞号检测（提交数异常告警）")
        parser.add_argument("--days", type=int, default=30, help="扫描窗口天数（默认 30）")
        parser.add_argument("--show-all", action="store_true", help="输出全部 E 号计数")
        args = parser.parse_args(argv)
        days, show_all = args.days, args.show_all

    abnormal, counts = scan(days)
    total = sum(counts.values())

    if show_all:
        for num, cnt in sorted(counts.items()):
            print(f"  {num}: {cnt}")
    print(f"E-number scan: {len(counts)} numbers, {total} refs (window {days}d, threshold {THRESHOLD})")

    if abnormal:
        print("E-NUMBER WARN: possible collision(s) (refs > %d): %s" % (THRESHOLD, ", ".join(abnormal)))
        return 1
    print("E-number PASS: no collision detected")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
