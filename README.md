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
- `out/PA_Module_Simplified.preA.svg`
- `out/PA_Module_Simplified.phaseA.svg`
- `out/PA_Module_Simplified.phaseB.svg`
- `out/PA_Module_Simplified.phaseC.svg`
- `out/PA_Module_Simplified.final.svg`

### 3.4 推荐查看顺序

1. `out/PA_Module_Simplified.summary.json`
2. `out/PA_Module_Simplified.phaseA.svg`
3. `out/PA_Module_Simplified.phaseB.svg`
4. `out/PA_Module_Simplified.phaseC.svg`
5. `out/PA_Module_Simplified.final.svg`

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

