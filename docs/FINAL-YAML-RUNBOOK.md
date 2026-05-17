# 最终 YAML 运行手册（`rf_layout_simplified.yaml`）

> 适用对象：首次接入本仓库的算法/验证/集成同学。  
> 目标：从零到可复现地跑通默认主线（v2 Skeleton-First）。

---

## 1. 环境准备

### 1.1 必要条件

- Python >= 3.11
- Git

### 1.2 推荐安装步骤

```bash
git clone https://github.com/gogogonow/pcb-algo-wiki.git
cd pcb-algo-wiki

python3 -m venv .venv
. .venv/bin/activate

python3 -m pip install -U pip
python3 -m pip install -e ".[dev]"
```

---

## 2. 输入检查

```bash
schema_check rf_layout_simplified.yaml
```

预期：输出 project/components/footprints/nodes/terminals/edges 计数，且命令返回 0。

---

## 3. 默认主流程运行

```bash
pcb_solve_v2 rf_layout_simplified.yaml
# 或:
# pcb_solve rf_layout_simplified.yaml
```

运行后会在 `out/` 目录生成阶段产物。

---

## 4. 关键产物说明

### 4.1 建议首先查看

1. `out/PA_Module_Simplified.summary.json`
2. `out/PA_Module_Simplified.phaseA.svg`
3. `out/PA_Module_Simplified.phaseB.svg`
4. `out/PA_Module_Simplified.phaseC.svg`
5. `out/PA_Module_Simplified.final.svg`

### 4.2 summary 关键字段

- `status`
- `phase_a.routed / phase_a.total`
- `phase_b.uv_placed / phase_b.uv_total`
- `phase_c.flex_routed / phase_c.flex_failed`
- `wall_total_s`

---

## 5. 全量验证

```bash
./scripts/verify_m1.sh
```

该命令会串行执行：

- black
- ruff
- mypy
- pytest
- schema_check
- topology_viz

---

## 6. 常见问题

### Q1: `python3.11` 不存在

Ubuntu 22.04 可安装：

```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv
```

### Q2: 运行成功但结果质量波动

- 先检查 `summary.json` 的 phase 指标；
- 再查看 `phaseC.svg` 是否存在重叠/交叉热点；
- 最后结合 floating placer 参数做针对性调优。

### Q3: 为什么还有 `pcb_solve_v1` / `cpsat_solve`

它们是历史对照链路，主线请使用 `pcb_solve_v2`（或 `pcb_solve`）。

