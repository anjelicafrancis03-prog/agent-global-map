# -*- coding: utf-8 -*-
"""预判器 tools 回归测试（2026-08-16 转正：沙箱模拟 → 真实数据断言）
覆盖：offline-search 补长名、板块直属（panel_direct）、任意子域命中、L3/dac 派生、asset 不膨胀
用法：python tests/test_suggest_tools_regression.py
"""
import json, os, subprocess, sys

MAP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "suggest_task_context.py")

def query(subnode, extra=None):
    cmd = [sys.executable, MAP, "--subnode", subnode, "--json"]
    if extra:
        cmd += extra
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return json.loads(r.stdout)

CASES = [
    # (查询, 额外参数, 期望 tools 数, 说明)
    ("search.offline-search", None, 3, "offline-search 补长名后 3 条命中"),
    ("skill-manager.catalog", None, 2, "板块直属 skill-manager 命中"),
    ("skill-manager.panel", None, 2, "板块直属任意子域命中"),
    ("beads.beads-views", None, 2, "板块直属 beads-dashboard+beads 命中"),
    ("hardware.tools-windows", None, 13, "长名工具不回归（E-136 值域补录 +3：everything-gui/rg-ripgrep/clash-verge）"),
    ("harness.modules", None, 3, "O9 后 harness 剩 3 条"),
    ("mcp-manager.servers", None, 3, "MCP 工具归位 servers"),
    ("topology.governance", None, 2, "tool-manager+agent-system-map 归 topology"),
    ("search.offline-search", ["--l3", "offline:everything"], 3, "offline:everything L3 派生"),
    ("harness.modules", ["--dac", "dac:ruanjian"], 3, "dac 派生"),
    ("hardware.tools-browser", ["--l3", "browser:automation"], 3, "browser:automation L3 派生（含并发登记 holehe）"),
]

fails = 0
for sub, extra, exp, note in CASES:
    try:
        d = query(sub, extra)
        got = len(d.get("tools", []))
        ok = got == exp
        print(f"  {'✅' if ok else '❌'} {sub} {extra or ''} -> {got} (exp {exp}) {note}")
        if not ok:
            fails += 1
    except Exception as e:
        print(f"  ❌ {sub} 异常: {e}")
        fails += 1

# asset 不膨胀：hardware.tools-windows 无坐标资产必须 0
d = query("hardware.tools-windows")
no_sub = sum(1 for a in d.get("assets", []) if not a.get("map_subnode"))
print(f"  {'✅' if no_sub == 0 else '❌'} asset 无膨胀: 无坐标资产={no_sub}")
if no_sub != 0:
    fails += 1

print("REGRESSION %s" % ("ALL PASS" if fails == 0 else "FAIL %d" % fails))
sys.exit(1 if fails else 0)
