# staleness_scan v0 (read-only): staging 副本 vs 现行标准 + 静态资产新鲜度
# 输出: 控制台摘要 + reports/run-manifests/2026-07-29-staleness-scan/report.md
import io, os, json, datetime, filecmp

NOW = datetime.datetime.now()
FROZEN_DAYS = 14
rows = []

def mt(p):
    return datetime.datetime.fromtimestamp(os.path.getmtime(p))

# 1) 三份标准: 现行 vs staging 副本
STD = ["普通标准.md", "常驻标准.md", "索引-普通标准-常驻标准.md"]
CUR = r"F:/codex/docs/thread-standards"
STG = r"F:/codex/cloud-memory-staging/thread-standards"
rows.append("# 陈旧扫描报告 v0")
rows.append("")
rows.append("生成: " + NOW.isoformat(timespec="seconds") + "（只读，不自动改任何文件）")
rows.append("")
rows.append("## 1) 标准文件: 现行 vs staging 副本")
rows.append("")
rows.append("| 文件 | 现行 mtime | staging mtime | 一致? |")
rows.append("|---|---|---|---|")
for f in STD:
    a, b = os.path.join(CUR, f), os.path.join(STG, f)
    if not os.path.isfile(a):
        rows.append("| " + f + " | 现行缺失! | - | - |")
        continue
    if not os.path.isfile(b):
        rows.append("| " + f + " | " + mt(a).strftime("%Y-%m-%d") + " | staging 缺失 | ⚠️ |")
        continue
    same = filecmp.cmp(a, b, shallow=False)
    rows.append("| " + f + " | " + mt(a).strftime("%Y-%m-%d %H:%M") + " | " + mt(b).strftime("%Y-%m-%d %H:%M") + " | " + ("✓ 一致" if same else "✗ 不一致") + " |")

# 2) 注册表静态资产新鲜度
rows.append("")
rows.append("## 2) 静态资产新鲜度（本地文件，> " + str(FROZEN_DAYS) + " 天未更新标 FROZEN）")
rows.append("")
rows.append("| 资产 | mtime | 年龄(天) | 判定 |")
rows.append("|---|---|---|---|")
reg = json.load(io.open(r"F:/codex/tools/agent-system-map/catalog/webpage-asset-registry-v0.json", encoding="utf-8"))
frozen = 0
for a in reg["assets"]:
    lp = a.get("local_path")
    if not lp or not os.path.isfile(lp):
        continue
    age = (NOW - mt(lp)).days
    verdict = "FROZEN" if age > FROZEN_DAYS else "ok"
    if verdict == "FROZEN":
        frozen += 1
    rows.append("| " + a["asset_id"].split(":")[-1] + " | " + mt(lp).strftime("%Y-%m-%d") + " | " + str(age) + " | " + verdict + " |")

rows.append("")
rows.append("## 摘要")
rows.append("")
rows.append("- FROZEN 资产: " + str(frozen) + " 个")
outdir = r"F:/codex/reports/run-manifests/2026-07-29-staleness-scan"
os.makedirs(outdir, exist_ok=True)
io.open(os.path.join(outdir, "report.md"), "w", encoding="utf-8").write("\n".join(rows) + "\n")
print("report:", os.path.join(outdir, "report.md"))
print("FROZEN:", frozen)
for r in rows:
    if "FROZEN" in r or "不一致" in r or "缺失" in r:
        print(r)
