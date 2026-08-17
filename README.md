# Agent Global System Map（大地图）

Agent 全局系统地图——多 Agent 生态的图谱化统一视图。将任务（beads）、手册、Skill、工具、数据库、网页资产等实体投影为 11 面板 + 实体图谱，并通过静态 HTML 单文件呈现。

> 本仓库为**代码 + 宪法/注册表**镜像（私有）。运行时数据（快照/投影/beads 导出/凭据分类法）不入库——跑构建脚本可再生成。

## 目录结构

```
agent-system-map/
├── rebuild.ps1                # 全量构建与发布调度（原子锁 + 后置归档钩子）
├── build_*.py                 # 构建管线：快照合成/任务投影/关系注册表/网页生成/双向注册表/L3
├── check_*.py  validate_*.py  # 门闸：沉淀对账/坐标审核/源闭包/关系样本/分类法/E 编号撞号检测
├── archive_bak_files.py       # .bak 历史碎片归档（幂等 + dry-run）
├── contract/                  # 机器合同（core-contract-v1.1）
├── catalog/                   # 宪法 + 注册表（panel-manifest / taxonomy 系列 / 各域 registry）
│   └── multi-source-snapshot-v2.json   # ⚠️ 运行时产物（rebuild 生成，不入库）
├── web/                       # 前端：模板 + 构建 + 渲染门闸（node_modules 不入库）
├── tests/                     # 测试（ps1 场景 + py 回归）
└── test_map_integrity.py      # 主链完整性门闸（T1~T10 数据 + D1~D10 治理门）
```

## 构建

```powershell
# 全量构建（含发布；-NoPublish 仅构建不发布）
powershell -File rebuild.ps1 -NoPublish

# 分步（顺序：快照 → 投影 → 关系注册表 → 网页）
python build_multi_source_snapshot.py
python build_task_projection.py
python build_unified_relation_registry.py
python web/build_map_website.py
```

## 验证

```powershell
python test_map_integrity.py   # 主链：T1~T10 数据完整性 + D1~D10 治理门（含 E 编号撞号检测）
```

## 不入库内容与原因

| 内容 | 原因 |
|---|---|
| `catalog/multi-source-snapshot-v2.json` 等运行时产物 | rebuild 可再生成，避免数据漂移 |
| `catalog/generations/` `_archive/` | 历史快照/备份（体积 215M+） |
| `catalog/beads-export.jsonl` | beads 内部任务数据 |
| `catalog/credential-taxonomy*.json` | 凭据分类法，含服务凭据引用——**永不提交** |
| `web/node_modules/` | 依赖（package.json 声明，npm install 可装） |

## 环境

- Python 3.12+（build/check/validate 脚本）
- PowerShell 5.1+（rebuild.ps1 及 ps1 工具）
- Node 18+（web 渲染门闸 render_test_gate.js）
