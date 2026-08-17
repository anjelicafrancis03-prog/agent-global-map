# -*- coding: utf-8 -*-
"""E-127 沉淀对账门（per-bead 体检·②④灯自动化）：
用法：
  python check_knowledge_reflux.py <执行单.md>          # 单任务对账
  python check_knowledge_reflux.py --scan-new           # 扫含⑥标记执行单逐一对账（D3 挂主链）
逻辑（对应收尾四灯②④）：
  ② 沉淀落点：解析执行单「⑥ 沉淀预声明」→ 预期沉淀物路径；实际=路径存在 + git 有改动
  ④ 画像源回填：预判器按声明坐标能否命中（路径在即视为可见性载体，深层命中由 D1/预判器测试兜底）
  ①③（收尾日志/矛盾扫描）= 收尾清单模板强制栏，模板必填即机器卡。
纯执行类任务声明「无沉淀（理由）」→ 直接过，不报缺。
"""
import os
import re
import json
import subprocess
import sys
import datetime

ROOT = "F:/codex"
WARN_COUNT = 0


def record_warn(msg):
    global WARN_COUNT
    WARN_COUNT += 1
    print("WARN %s" % msg)


def git_proof(path_abs, since=None):
    """三证据判定沉淀物是否真实产生（P0 修复 2026-08-15 / E-136 Fail-Loud 改造 2026-08-16）：
    证据 A：git status 有未提交改动（含 ?? 未追踪新产出）
    证据 B：最近 commit 晚于任务起草日（已提交沉淀 = 也算有，修复「已提交→误判红灯」）
    证据 C：mtime 晚于任务起草日（E-128 真实任务补：gitignore 目录文件时间戳是唯一可证）
    全不中 = 无沉淀（红灯）
    环境异常 = Fail-Loud 打印 WARN 留痕 + 计数 + 仍放行（E-128 防误杀）
    """
    try:
        rel = os.path.relpath(path_abs, ROOT).replace("\\", "/")
    except ValueError:
        rel = path_abs  # 跨盘路径直接用绝对路径
    try:
        # 证据 A：未提交改动 / 未追踪新产出
        r = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "--", rel],
                           capture_output=True, text=True, timeout=15)
        if r.stdout.strip():
            return True
        # 证据 B：已提交沉淀（commit 时间晚于任务起草日）
        if since is not None:
            r2 = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%ci", "--", rel],
                                capture_output=True, text=True, timeout=15)
            ci = r2.stdout.strip()
            if ci:
                try:
                    ct = datetime.datetime.strptime(ci[:19], "%Y-%m-%d %H:%M:%S")
                    if ct >= since:
                        return True
                except Exception as e:
                    record_warn("env-exception:%s:git-log-strptime:%s" % (rel, e))
            # 证据 C：mtime 晚于任务起草日（E-128 真实任务补 2026-08-15——
            # catalog/credential_registry 等核心沉淀载体在 gitignore 目录（从不入 git），
            # git 证据对它们不适用，文件系统时间戳是唯一可证）
            try:
                if os.path.getmtime(path_abs) >= since.timestamp():
                    return True
            except Exception as e:
                record_warn("env-exception:%s:getmtime:%s" % (rel, e))
        return False
    except Exception as e:
        record_warn("env-exception:%s:%s" % (rel, e))
        return True  # 仍放行留痕（E-128 防误杀）


def suggest_targets(exe):
    """自动建议该任务的沉淀物目标（E-132 魅惑令）：按坐标+关键词匹配手册/OKF/topic/判例，
    红灯时提示执行者「该去补哪些沉淀物」。返回建议清单字符串。"""
    text = open(exe, encoding="utf-8-sig").read()
    # 坐标提取（map.x.y / map.x.y.z → node/subnode）
    node, subnode = None, None
    m_coord = re.search(r"坐标[：:]\s*map\.([a-z0-9-]+)(?:\.([a-z0-9-]+))?", text[:400])
    if m_coord:
        node = m_coord.group(1)
        subnode = m_coord.group(2)
    # 标题关键词（E 号后第一段）
    kw = []
    m_title = re.search(r"[:：]\s*([^\n（(]+)", text[:200])
    if m_title:
        seg = m_title.group(1)
        kw = [w for w in re.split(r"[\s/、，,]+", seg) if len(w) >= 2 and not w.startswith(("E-", "E"))][:4]
    out = []
    try:
        man = json.load(open(os.path.join(ROOT, "tools/agent-system-map/catalog/manual-registry-v0.json"), encoding="utf-8-sig"))
        for e in man.get("entries", man.get("manuals", [])):
            mn = (e.get("map_node_id") or "").replace("map.", "")
            if (node and node in mn) or (kw and any(k.lower() in (e.get("title", "") + str(e.get("covers_object", ""))).lower() for k in kw)):
                out.append("手册:%s" % e.get("manual_id", e.get("title", "?")))
                if len(out) >= 5:
                    break
    except Exception as e:
        record_warn("env-exception:suggest:manual-reg:%s" % e)
    try:
        okf = json.load(open(os.path.join(ROOT, "tools/agent-system-map/catalog/okf-registry-v0.json"), encoding="utf-8-sig"))
        for b in okf.get("bundles", []):
            mn = (b.get("map_node_id") or "").replace("map.", "")
            if (node and node in mn) or (kw and any(k.lower() in (b.get("title", "") + b.get("bundle_id", "")).lower() for k in kw)):
                out.append("OKF:%s" % b.get("bundle_id", "?"))
                if len(out) >= 5:
                    break
    except Exception as e:
        record_warn("env-exception:suggest:okf-reg:%s" % e)
    try:
        tp = json.load(open(os.path.join(ROOT, "tools/agent-system-map/catalog/topic-registry-v0.json"), encoding="utf-8-sig"))
        for e in tp.get("entries", []):
            if kw and any(k.lower() in " ".join(e.get("contains", [])).lower() for k in kw):
                out.append("topic:%s" % e.get("id"))
                if len(out) >= 5:
                    break
    except Exception as e:
        record_warn("env-exception:suggest:topic-reg:%s" % e)
    return out


def main_core(exe):
    if not exe or not os.path.exists(exe):
        print("FAIL 执行单不存在: %s" % exe)
        return 1
    text = open(exe, encoding="utf-8-sig").read()
    m = re.search(r"⑥[^\n]*沉淀预声明.*?(?=\n## |\Z)", text, re.S)
    declared = []
    if m:
        block = m.group(0)
        if "无沉淀" in block or "无可复用内核" in block:
            print("PASS 无沉淀声明（纯执行类，理由在案）")
            return 0
        declared = re.findall(r"[A-Za-z]:[^\s，,；;）)（(`]+", block)
    # 待派/执行中/待审/起草/待批状态：只做格式校验（沉淀物未产出是正常态，关单时才全对账）
    # 2026-08-15 真实任务补：状态值域含「执行中」（run-manifest 四态），执行中沉淀物未产出=正常，不得误杀
    status_m = re.search(r"状态[：:](\S+)", text[:200])
    pending = status_m and any(k in status_m.group(1) for k in ("待派", "执行中", "待审", "起草", "待批"))
    if pending:
        if declared or "沉淀预声明" in text:
            print("PASS 非关单态格式合规（⑥栏在，沉淀物关单时对账）")
            return 0
    if not declared:
        print("FAIL 未找到「⑥ 沉淀预声明」栏或无可声明路径（模板必填，E-127）")
        return 1
    # P0：since = 执行单起草日（从文件名 20xxxxxx 提取，证据 B 用）
    since = None
    mdate = re.search(r"(20\d{6})", os.path.basename(exe))
    if mdate:
        try:
            since = datetime.datetime.strptime(mdate.group(1), "%Y%m%d")
        except Exception:
            since = None
    bad = []
    for p in declared:
        p_abs = p if os.path.isabs(p) else os.path.join(ROOT, p)
        if not os.path.exists(p_abs):
            bad.append("missing:%s" % p)
        elif not git_proof(p_abs, since):
            bad.append("no-proof:%s" % p)
    if bad:
        for b in bad:
            print("FAIL " + b)
        print("REFLEX-FAIL 沉淀预声明 %d 项，缺 %d 项（红灯：补沉淀或改声明）" % (len(declared), len(bad)))
        # E-132 自动建议：红灯时提示该任务可能涉及的沉淀物（手册/OKF/topic）
        sugg = suggest_targets(exe)
        if sugg:
            print("SUGGEST 建议沉淀物: " + " / ".join(sugg))
        return 1
    print("PASS 沉淀对账 %d 项全过（路径在 + git 有改动/已提交沉淀）" % len(declared))
    return 0


def scan_new():
    """--scan-new：扫工作区含「沉淀预声明」标记的执行单（新模板单），逐一跑对账（D3 挂主链用）"""
    wd = "C:/Users/64998/WorkBuddy/2026-07-27-12-30-29"
    if not os.path.isdir(wd):
        print("FAIL 工作区不存在")
        return 1
    bad_total = []
    n = 0
    for fn in sorted(os.listdir(wd)):
        if not (fn.startswith("执行单-") and fn.endswith(".md")):
            continue
        # E-127 模板标记判断（同 D2）：内容含「沉淀预声明」=新模板单才查（不用文件名日期——升级模板的旧名任务也得查）
        body = open(os.path.join(wd, fn), encoding="utf-8-sig").read()
        if "沉淀预声明" not in body:
            continue
        n += 1
        rc = main_core(os.path.join(wd, fn))
        if rc != 0:
            bad_total.append(fn)
    print("D3 沉淀对账: 新单=%d 红灯=%d" % (n, len(bad_total)))
    if WARN_COUNT > 0:
        print("D3 WARN: 进程内累计环境异常告警 %d 项（放行留痕）" % WARN_COUNT)
    if bad_total:
        print("D3 FAIL: %s" % bad_total[:5])
        return 1
    print("D3 PASS（无新单或全过）")
    return 0


def main():
    if len(sys.argv) < 2:
        print("usage: check_knowledge_reflux.py <执行单.md> | --scan-new | --suggest <执行单.md>")
        return 2
    if sys.argv[1] == "--scan-new":
        return scan_new()
    if sys.argv[1] == "--suggest":
        if len(sys.argv) < 3:
            print("usage: --suggest <执行单.md>")
            return 2
        for s in suggest_targets(sys.argv[2]):
            print(s)
        return 0
    return main_core(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
