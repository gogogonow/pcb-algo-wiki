# 功放 PCB 自动布局布线引擎（当前主线：v2 Skeleton-First）

> 当前默认求解入口：`pcb_solve` / `pcb_solve_v2`  
> 输入：`rf_layout_simplified.yaml`（v3.3 结构化数据）  
> 输出：分阶段 JSON/SVG + 最终结果 + summary

---

## 1. 当前状态（请先看）

本仓库**当前生产路径不再使用 CP-SAT 主求解**。默认架构为：

1. **Frontend 编译**（容错 lint + 组件展开 + edge triage + node 归一化）
2. **PreA 位置求解**
3. **Phase A 骨架路由**（A* + rip-up + 长度补偿）
4. **Phase B UV 吸附**
5. **Phase C 浮动器件布局 + flexible_path 路由 + DRC 校验**
6. **分阶段产物与最终 SVG 输出**

`pcb_solve_v1`（旧 CP-SAT 流程）仅保留作历史对照，不是默认路径。

---

## 2. 环境配置

### 2.1 基础要求

- Python **>= 3.11**
- Linux/macOS（Windows 建议 WSL2）

### 2.2 推荐安装方式（venv）

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -U pip
python3 -m pip install -e ".[dev]"
```

> Ubuntu 22.04 若默认无 3.11，可先安装：  
> `sudo apt-get update && sudo apt-get install -y python3.11 python3.11-venv`

---

## 3. 最终 YAML 运行指导（`rf_layout_simplified.yaml`）

### 3.1 输入预检查

```bash
schema_check rf_layout_simplified.yaml
```

### 3.2 拓扑可视化（可选）

```bash
topology_viz rf_layout_simplified.yaml
# 输出: out/PA_Module_Simplified.topology.svg
```

### 3.3 一键运行主流程（推荐）

```bash
pcb_solve rf_layout_simplified.yaml
# 等价于:
# pcb_solve_v2 rf_layout_simplified.yaml
```

典型输出文件：

- `out/PA_Module_Simplified.preA.json`
- `out/PA_Module_Simplified.phaseA.json`
- `out/PA_Module_Simplified.phaseB.json`
- `out/PA_Module_Simplified.summary.json`
- `out/PA_Module_Simplified.viewer.json`  ← 交互式查看器数据包
- `out/PA_Module_Simplified.preA.svg`
- `out/PA_Module_Simplified.phaseA.svg`
- `out/PA_Module_Simplified.phaseB.svg`
- `out/PA_Module_Simplified.phaseC.svg`
- `out/PA_Module_Simplified.final.svg`

### 3.4 分阶段运行（调试用）

使用 `--stop-after` 可在指定阶段后提前退出，便于逐阶段评估：

```bash
# 仅运行到 PreA（位置求解）
pcb_solve rf_layout_simplified.yaml --stop-after preA

# 仅运行到 Phase A（骨架路由）
pcb_solve rf_layout_simplified.yaml --stop-after phaseA --rip-up-rounds 2

# 仅运行到 Phase B（UV 吸附）
pcb_solve rf_layout_simplified.yaml --stop-after phaseB

# 完整流程（默认，等价于 --stop-after phaseC）
pcb_solve rf_layout_simplified.yaml
```

每个阶段均输出对应的 `.json` / `.svg` 产物和 `viewer.json`，可独立评判。

### 3.5 交互式查看器（推荐）

运行完成后，直接在浏览器打开 `viewer/viewer.html`，点击 **Load JSON ▶** 加载 `out/PA_Module_Simplified.viewer.json`：

| 功能 | 操作 |
|------|------|
| 切换阶段 | 顶部 Tab：preA / phaseA / phaseB / phaseC |
| 缩放 | 鼠标滚轮（以光标为中心） |
| 平移 | 左键拖拽 |
| 查看属性 | 点击走线或器件 → 右侧面板显示详情 |
| Hover 预览 | 悬停走线/器件 → 显示 Net/宽度/型号 |
| 图层过滤 | 左侧 checkbox：控制走线/UV器件/浮动器件/Pad 等显隐 |
| 取消选中 | 点击空白区域 |

颜色编码：RF 走线=蓝色 `#4a90d9`、flex 走线=橙色 `#f5a623`、失败走线=红色 `#e74c3c`、preA 连接=灰色虚线。

### 3.6 推荐查看顺序

1. `out/PA_Module_Simplified.summary.json` — 全局统计（路由成功率、DRC 违规数）
2. `viewer/viewer.html` + `out/PA_Module_Simplified.viewer.json` — **交互式四阶段全览**
3. `out/PA_Module_Simplified.phaseA.svg` / `phaseC.svg` — 快速静态对比

---

## 4. 常用质量命令

```bash
# 全仓主质量门（black + ruff + mypy + pytest + schema_check + topology_viz）
./scripts/verify_m1.sh

# 仅运行测试
python3 -m pytest -q
```

---

## 5. CLI 入口

`pyproject.toml` 当前脚本映射：

- `schema_check` → `tools.schema_check:main`
- `topology_viz` → `tools.topology_viz:main`
- `frontend_compile` → `tools.frontend_compile:main`
- `solver_ir` → `tools.solver_ir:main`
- `pcb_solve` → `tools.pcb_solve_v2:main`（默认）
- `pcb_solve_v2` → `tools.pcb_solve_v2:main`
- `pcb_solve_v1` → `tools.pcb_solve:main`（历史）
- `cpsat_solve` → `tools.cpsat_solve:main`（历史实验链路）

---

## 6. 文档地图（已刷新）

### 当前有效（主线）

- `README.md`（本文件）
- `ALGORITHM-OVERVIEW.md`（当前架构与阶段职责）
- `ITERATION-PLAN.md`（当前阶段状态与后续方向）
- `docs/FINAL-YAML-RUNBOOK.md`（最终 YAML 端到端操作手册）
- `concepts/skeleton-first-router.md`（主线算法细节）
- `concepts/frontend-compiler-spec.md`（frontend 产物/字段）
- `docs/superpowers/specs/2026-05-17-pcb-viewer-design.md`（交互式查看器设计规格）
- `viewer/viewer.html`（交互式 PCB Phase 查看器，PixiJS 8）

### 历史归档（保留对照）

以下文档保留用于回溯，不代表当前默认执行路径：

- `concepts/cpsat-model.md`
- `concepts/routing-algorithm-comparison.md`
- `concepts/sa-and-astar.md`
- `concepts/postproc-bend-drc-lvs.md`
- `concepts/uv-and-junction-templates.md`
- `concepts/microstrip-topology-matching.md`
- `concepts/placement-problem-formulation.md`
- `concepts/output-stub-mapping.md`

---

## 7. 输入规范

- 上游数据规范：`射频微波版图结构化数据规范 (v3.3).md`
- 仓库主案例：`rf_layout_simplified.yaml`

