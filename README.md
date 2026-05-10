# 功放PCB单层自动布局布线算法 v3

> 固定微带线 + 灵活走线 | 拓扑分裂结构 | 大量并联RLC shunt接地 | 无交叉硬约束

---

## 核心改进（v2 → v3）

**约束分离原则**：SA 能量函数极简化，所有硬约束全部移交给 CP-SAT。

| v2 问题 | v3 解决方案 |
|---------|-----------|
| 6个参数相互耦合 | **SA只保留3个物理参数**（HPWL+边界+热） |
| β·C_cross 是软惩罚，可能违反硬约束 | **删除，改用 CP-SAT AddNoOverlap（强制）** |
| ε·C_congestion 与HPWL反向耦合 | **删除，改用 CP-SAT AddCumulative（强制）** |
| ζ·C_ground 量纲不一致 | **删除，改用 CP-SAT AddCumulative（强制）** |

---

## v3 架构

```
Placement SA（极简化能量函数）：
E = α·HPWL + γ·C_boundary + δ·C_thermal

Routing CP-SAT（强制约束）：
· AddCircuit（连通性）
· AddLinearExpression（目标长度）
· AddNoOverlap（无交叉）← 删除SA的C_cross
· AddCumulative（过孔密度）← 删除SA的C_congestion/ζ
· occupied_cells（预布线障碍）
```

详见 [ALGORITHM-OVERVIEW.md](ALGORITHM-OVERVIEW.md)

---

## YAML Schema（拓扑分裂对齐）

```yaml
nodes:
  node_shunt_tap_001:
    type: "component_pad_junction"   # RLC吸附点
    parent_edge: "tl_main"
    position_along_parent: 0.4

edges:
  tl_main:
    type: "microstrip_parent"       # 父边（逻辑分组）
    children: [part1, part2, part3]

  part1: { type: "microstrip", parent: "tl_main", ... }
  part2: { type: "microstrip", parent: "tl_main", ... }

  c_shunt_001:
    type: "lumped_capacitor"         # RLC shunt
    connections: [node_shunt_tap_001, GND_REF]
    shunt_tap_of: "tl_main"          # 吸附到父边
```

---

## 文件结构

```
├── ALGORITHM-OVERVIEW.md          # 总体架构 v3（推荐先读）
├── concepts/
│   ├── placement-problem-formulation.md
│   ├── routing-algorithm-comparison.md
│   └── microstrip-topology-matching.md
└── README.md
```
