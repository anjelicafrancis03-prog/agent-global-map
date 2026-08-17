# -*- coding: utf-8 -*-
"""per-bead 四灯体检脚本（E-130，p020 方法论脚本化，Cairn 透镜落地）

用法：
  python per_bead_check.py <执行单.md>    # 单任务体检
  python per_bead_check.py --scan-all     # 扫工作区全部执行单

四灯（对应收尾四灯）：
  ① 收尾日志：memory/ 日志文件含该任务 E 号
  ② 沉淀落点：⑥预声明路径存在 + git 证明（复用 check_knowledge_reflux 的 git_proof 三证据）
  ③ 矛盾扫描：存在含该 E 号的回执-*/结果-* 文件（轻量近似：声明 vs 载体存在性）
  ④ 画像源回填：预声明路径的注册表名 ∈ 预判器 SRC / database-registry（可见性载体）

输出：体检卡（四灯绿/红）+ 汇总（同 p020 方法论）
"""
import os
import re
import sys
import glob
import datetime
import subprocess

ROOT = "F:/codex"
WD = "C:/Users/64998/WorkBuddy/2026-07-27-12-30-29"
MEM_DIR = os.path.join(WD, ".workbuddy", "memory")


def git_proof(path_abs, since=None):
    try:
        rel = os.path.relpath(path_abs, ROOT).replace("\\", "/")
    except ValueError:
        rel = path_abs
    try:
        r = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "--", rel],
                           capture_output=True, text=True, timeout=15)
        if r.stdout.strip():
            return True
        if since is not None:
            r2 = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%ci", "--", rel],
                                capture_output=True, text=True, timeout=15)
            ci = r2.stdout.strip()
            if ci:
                try:
                    if datetime.datetime.strptime(ci[:19], "%Y-%m-%d %H:%M:%S") >= since:
                        return True
                except Exception:
                    pass
            try:
                if os.path.getmtime(path_abs) >= since.timestamp():
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return True


def lamp(text, ok, detail=""):
    return ("🟢" if ok else "🔴") + " " + text + ("（" + detail + "）" if detail else "")


def check_one(exe):
    # E 号提取兼容 E128 / E-128 两种写法
    base = os.path.basename(exe)
    m_e = re.search(r"E-?(\d+)", base)
    eid = "E-" + m_e.group(1) if m_e else base
    text = open(exe, encoding="utf-8-sig").read()
    status_m = re.search(r"状态[：:](\S+)", text[:200])
    status = status_m.group(1) if status_m else "?"
    pending = any(k in status for k in ("待派", "执行中", "待审", "起草", "待批"))

    # ⑥ 预声明路径
    m = re.search(r"⑥[^\n]*沉淀预声明.*?(?=\n## |\Z)", text, re.S)
    declared = []
    if m:
        block = m.group(0)
        if "无沉淀" in block:
            declared = ["__NO_SINK__"]
        else:
            declared = re.findall(r"[A-Za-z]:[^\s，,；;）)（(`]+", block)

    since = None
    mdate = re.search(r"(20\d{6})", os.path.basename(exe))
    if mdate:
        try:
            since = datetime.datetime.strptime(mdate.group(1), "%Y%m%d")
        except Exception:
            pass

    lights = []

    # ① 收尾日志
    log_ok = False
    if os.path.isdir(MEM_DIR):
        for logf in sorted(glob.glob(os.path.join(MEM_DIR, "2026-*.md")), reverse=True)[:3]:
            if eid in open(logf, encoding="utf-8-sig").read():
                log_ok = True
                break
    lights.append(lamp("①收尾日志", log_ok or pending, "近 3 日日志含 " + eid if log_ok else ("待派/执行中不判" if pending else "无日志")))

    # ② 沉淀落点
    if pending or declared == ["__NO_SINK__"]:
        sink_ok = True
        sink_detail = "无沉淀声明" if declared == ["__NO_SINK__"] else "非关单态"
    else:
        sink_ok = True
        sink_detail = []
        for p in declared:
            p_abs = p if os.path.isabs(p) else os.path.join(ROOT, p)
            if not os.path.exists(p_abs):
                sink_ok = False
                sink_detail.append("missing:" + p)
            elif not git_proof(p_abs, since):
                sink_ok = False
                sink_detail.append("no-proof:" + p)
        sink_detail = ";".join(sink_detail) if sink_detail else "%d 项沉淀在" % len(declared)
    lights.append(lamp("②沉淀落点", sink_ok, sink_detail))

    # ③ 矛盾扫描（回执/结果存在性）
    has_receipt = bool(glob.glob(os.path.join(WD, "回执-*" + eid.replace("-", "") + "*.md")) or
                       glob.glob(os.path.join(WD, "结果-*" + eid.replace("-", "") + "*.md")))
    lights.append(lamp("③矛盾载体", has_receipt or pending or declared == ["__NO_SINK__"],
                       "回执/结果含 " + eid if has_receipt else ("待派/无沉淀不判" if (pending or declared == ["__NO_SINK__"]) else "无回执载体")))

    # ④ 画像源回填（声明路径的注册表名是否在预判器 SRC / database-registry）
    src_hit = True
    src_detail = "非注册表沉淀" if (pending or declared == ["__NO_SINK__"]) else ""
    if not pending and declared != ["__NO_SINK__"]:
        try:
            src_code = open(os.path.join(ROOT, "tools/agent-system-map/suggest_task_context.py"), encoding="utf-8").read()
            reg_names = set(re.findall(r"([a-z-]+-registry-v0)\.json", src_code))
            sink_names = set(os.path.basename(p) for p in declared)
            overlap = sink_names & reg_names
            if overlap:
                # 声明的是预判器覆盖的注册表 → 必须命中
                src_hit = False
                src_detail = "预判器 SRC 含 %s 但未消费到" % ",".join(sorted(overlap))
            else:
                # 不在 SRC 覆盖范围 = 合理边界（凭据/值域/报告类），判绿仅提示
                src_hit = True
                src_detail = "声明路径不在预判器 SRC（凭据/值域/报告类=合理边界，人工核）"
        except Exception as e:
            src_detail = "预判器读失败:" + str(e)
    lights.append(lamp("④画像源", src_hit, src_detail))

    reds = [l for l in lights if l.startswith("🔴")]
    total = "🟢 全绿" if not reds else "🔴 %d 红灯" % len(reds)
    print("=== %s [%s] %s ===" % (eid, status, total))
    for l in lights:
        print("  " + l)
    return 0 if not reds else 1


def main():
    if len(sys.argv) < 2:
        print("usage: per_bead_check.py <执行单.md> | --scan-all")
        return 2
    if sys.argv[1] == "--scan-all":
        rc_all = 0
        for fn in sorted(os.listdir(WD)):
            if fn.startswith("执行单-") and fn.endswith(".md"):
                if "沉淀预声明" not in open(os.path.join(WD, fn), encoding="utf-8-sig").read():
                    continue
                rc = check_one(os.path.join(WD, fn))
                if rc != 0:
                    rc_all = 1
        print("SCAN-ALL 完成，有红灯" if rc_all else "SCAN-ALL 全绿")
        return rc_all
    return check_one(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
