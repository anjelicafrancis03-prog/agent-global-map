#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""search-tools 宪法轻量校验器 (E-26 批次4, 2026-08-03).

校验 search-tools/index.md 声明的 MCP 工具 ∈ mcp_manager registry 名单，漂移即报。
规则（零未知指向）：
- index.md 中声明为 "MCP" 的工具，其 MCP 名必须存在于 mcp_manager registry（23 名单）
- 声明为 CLI/API/渠道 的（AnySearch/Zhihu/RedFox/Agent Reach/WebClaw 等）跳过 MCP 校验
- 特殊处理：内置 web search、本地工具（webctl 是 MCP 也是 CLI）、Exa/Ref（未注册 MCP，单独标注）

用法: python search_constitution_checker.py
输出: 控制台 + 退出码（0=无漂移，1=有漂移）
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(r"F:\codex")
INDEX = ROOT / "okf-bundles" / "search-tools" / "index.md"
REGISTRY = ROOT / "tools" / "mcp_manager" / "registry.json"
REGISTRY_L3 = ROOT / "tools" / "agent-system-map" / "catalog" / "search-tools-registry-v0.json"

# index.md 中明确标注为 "MCP" 的工具名（规范化后）→ 应存在于 registry
# 注意：MCP_MENTIONS 是补充兜底名单（正则提取不到的 "XX MCP" 字样工具靠它兜底），非完整名单。
# 值域应定期对齐 mcp_manager registry.json（E-60 值域单一来源，漂移源=registry.json 的 servers 键）。
# 2026-08-10 E-86 对齐：补 codebase_memory_mcp/codegraph/docker 等注册项；exa/ref 标注未注册（保留以显式跳过）。
MCP_MENTIONS = [
    "brightdata", "firecrawl", "tavily", "context7", "github", "grok",
    "playwright", "webctl",
    # E-86 对齐注册表（mcp_manager registry.json servers，25 个）
    "codebase_memory_mcp", "codegraph", "docker", "figma", "agentmemory",
    "agent_hub", "agent_toolbox", "node_repl", "threadmerge", "vibe_conductor",
    "dbx", "onemcp_pilot", "context-mode", "skilltool", "kimi-cu-win", "glm4v-vision",
    # 未注册（明确跳过，非注册 MCP）：exa / ref 保留在 skip 集
]


def load_registry_names() -> set[str]:
    try:
        with open(REGISTRY, encoding="utf-8-sig") as f:
            reg = json.load(f)
        return set(reg.get("servers", {}).keys())
    except Exception:
        return set()


def check() -> dict:
    registry_names = load_registry_names()
    try:
        text = INDEX.read_text(encoding="utf-8")
    except Exception as e:
        return {"ok": False, "summary": {"error": "index unreadable: %s" % e}, "drifts": []}

    # 提取 index.md 中提及的 MCP 名（"XX MCP" 模式）
    mentioned = set()
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9_\-]+)\s+MCP", text):
        mentioned.add(m.group(1).lower())
    # 补充明确标注为 MCP 的工具（含 "MCP / Exa API" 等）
    mentioned.update(n.lower() for n in MCP_MENTIONS)

    drifts = []
    known_aliases = {
        "brightdata": "brightdata",
        "firecrawl": "firecrawl",
        "tavily": "tavily",
        "context7": "context7",
        "github": "github",
        "grok": "grok",
        "playwright": "playwright",
        "webctl": "webctl",
        "codegraph": "codegraph",
        "codebase": "codebase_memory_mcp",
    }
    # 明确不校验（未注册/特殊）
    skip = {"exa", "ref", "crawl4ai", "browseract", "webclaw", "anysearch", "zhihu", "redfox", "agent"}
    # 非工具词（"Codex config" / "remote MCP" / "API" 等误匹配）
    skip |= {"codex", "remote", "api", "url", "built", "ref", "exa", "not", "default", "model", "official"}

    for name in sorted(mentioned):
        if name in skip:
            continue
        canonical = known_aliases.get(name, name)
        if canonical not in registry_names:
            drifts.append({
                "tool": name,
                "canonical": canonical,
                "in_registry": False,
                "note": "index.md 声明但 mcp_manager registry 无此 MCP",
            })

    return {
        "ok": not drifts,
        "summary": {
            "mentioned_tools": len(mentioned),
            "checked_against_registry": len(mentioned) - len(mentioned & skip),
            "drifts": len(drifts),
            "registry_servers": len(registry_names),
        },
        "drifts": drifts,
    }


def check_retired_refs(text: str) -> list:
    """E-35 扩展：index.md 引用 lifecycle=retired 工具 → 报警（零未知指向）。"""
    retired_refs = []
    try:
        with open(REGISTRY_L3, encoding="utf-8-sig") as f:
            reg = json.load(f)
    except Exception as e:
        return [{"note": "search-tools-registry unreadable: %s" % e}]
    for t in reg.get("tools", []):
        if t.get("lifecycle") == "retired":
            names = {t.get("id", "").lower(), t.get("name", "").lower()}
            for n in names:
                if n and n in text.lower():
                    retired_refs.append({
                        "tool": t.get("id"),
                        "lifecycle": "retired",
                        "note": "index.md 引用了 retired 工具（改 registry 或更新 index）",
                    })
    return retired_refs


def main() -> int:
    result = check()
    # E-35：并入 retired 引用报警
    try:
        text = INDEX.read_text(encoding="utf-8")
        retired = check_retired_refs(text)
        if retired:
            result["ok"] = False
            result.setdefault("drifts", []).extend(retired)
            result["summary"]["retired_refs"] = len(retired)
        else:
            result["summary"]["retired_refs"] = 0
    except Exception as e:
        result.setdefault("drifts", []).append({"note": "retired check failed: %s" % e})
        result["ok"] = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
