# -*- coding: utf-8 -*-
"""
validate_agent_federation.py
-----------------------------
E-24 agent 身份六面联邦门闸（2026-08-03 skill manager 常驻1 立）。

架构（用户拍板）：agent-hub = agent 身份唯一注册地（agents.json 主档 + agent-families
家族层 + DAC config 实例层）；其余面（MCP/provider/desktop）只存引用，引用必须解析到
合法 id 域。

合法 id 域 = agents.json 主档 id ∪ slot-bindings profile（实例）∪ DAC config agentHubId（实例）。
理由：CLI=slot 级可扩展（实例 id 在 DAC/绑定层，不在 agents.json）；桌面版按 wrapper-cli
入主档；GUI 无 wrapper 禁入主档（agent-registration-standard-v1）。

校验三个引用字段（非 null 时）：
  - mcp registry      : backed_by_agent
  - provider-routes   : fronted_by_agent
  - slot-bindings     : seated_agent

退出码：0 = 全部通过；1 = 有未知引用（阻断，先修引用方或补注册）。

用法：python3 tools/agent-system-map/validate_agent_federation.py
"""
import json
import os
import sys

ROOT = r"F:\codex"
AGENTS = os.path.join(ROOT, r"tools\agent-hub\agents.json")
BINDINGS = os.path.join(ROOT, r"tools\agent-hub\slot-provider-bindings.json")
ROUTES = os.path.join(ROOT, r"tools\agent-hub\provider-routes.json")
MCP_REG = os.path.join(ROOT, r"tools\mcp_manager\registry.json")
DAC = os.path.join(ROOT, r"tools\desktop-agent-console\config\agents.json")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    problems = []

    valid = set()
    aj = load(AGENTS)
    valid.update(a["id"] for a in aj["agents"])
    print(f"agents.json 主档 id: {len(aj['agents'])} 个")

    sp = load(BINDINGS)
    for b in sp.get("bindings", []):
        prof = (b.get("profile") or {}).get("profileId")
        if prof and prof.startswith("profile:"):
            valid.add(prof[8:])
    print(f"slot-bindings profile 实例: 并入域")

    dac = load(DAC)
    for a in dac.get("agents", []):
        if a.get("agentHubId"):
            valid.add(a["agentHubId"])
        for ov in (a.get("slotOverrides") or {}).values():
            if ov.get("agentHubId"):
                valid.add(ov["agentHubId"])
    print(f"DAC agentHubId 实例: 并入域")
    print(f"合法 id 域合计: {len(valid)} 个")

    # ① MCP 面
    mr = load(MCP_REG)
    for name, s in mr.get("servers", {}).items():
        v = s.get("backed_by_agent")
        if v and v not in valid:
            problems.append(f"[mcp:{name}] backed_by_agent={v!r} 不在合法域")

    # ② provider 面
    pr = load(ROUTES)
    for r in pr.get("routes", []):
        v = r.get("fronted_by_agent")
        if v and v not in valid:
            problems.append(f"[route:{r.get('routeId')}] fronted_by_agent={v!r} 不在合法域")

    # ③ desktop 面
    for b in sp.get("bindings", []):
        v = b.get("seated_agent")
        if v and v not in valid:
            problems.append(f"[binding:{b.get('slotId')}] seated_agent={v!r} 不在合法域")

    print()
    if problems:
        print(f"FAIL: {len(problems)} 个未知引用——阻断，先修引用方或补注册")
        for p in problems:
            print("  " + p)
        sys.exit(1)
    print("OK: 所有 backed_by_agent / fronted_by_agent / seated_agent 引用均解析到合法 id 域（零未知指向）")
    sys.exit(0)


if __name__ == "__main__":
    main()
