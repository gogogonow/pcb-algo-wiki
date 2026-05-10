# 功放PCB单层自动布局布线算法 v4

> **混合射频图路由（hybrid_rf_graph）** | 三求解器分发 | 拓扑分裂结构 | 浮动器件引力场 | 串/并集总器件统一建模

---

## 核心改进（v3 → v4）

**多目标路由分发**：根据每条边的 `routing_class` 把任务分派给不同求解器。

| v3 问题 | v4 解决方案 |
|---|---|
| `nodes` 混用物理管脚（绝对坐标）和逻辑节点（推导坐标） | 拆分出 **`terminals`** 段，专门承载不可移动的物理锚点 |
| 灵活偏置线被强行塞进 CP-SAT，10⁶ 变量爆炸 | 灵活线改走 **A* 迷宫绕障**（`target_length: null` 显式表达）|
| 没有"浮动器件"概念 | 新增 `floating_shunt_tap` + `placement_objective`（`space_available` / `attract_to_target`）**引力场模型** |
| `lumped_*` 仅支持并联（`shunt_tap_of`），无法表达串联磁珠 | 集总器件统一为 edge；**串/并由 `connections` 是否触地自动判定** |
| `microstrip_parent.children` + `parent_edge` + `position_along_parent` 三重冗余且与 `target_length` 互相覆盖 | 删除父边/比例字段；**共享节点即拓扑分裂**，节点位置由两侧 `target_length` 联立解 |
| 边 schema 缺 `bend_style`、节点缺 `stepped_impedance` 偏移 | 边支持 `geometry.bend_style`（如 `mitered_45`）；节点支持 `stepped_impedance` + `connections_rule.custom_offset` |

---

## v4 架构

```
YAML(hybrid_rf_graph) → 按 routing_class 分发到三个求解器：

┌──────────────────┐  ┌─────────────────┐  ┌──────────────────────┐
│ rf_constrained   │  │ flexible_path   │  │ floating placement   │
│ 运动学 + CP-SAT  │  │ A* 迷宫绕障    │  │ 引力场 SA            │
│ (长度锁定/无交叉)│  │ (target_length= │  │ (space_available /   │
│                  │  │  null)          │  │  attract_to_target)  │
└──────────────────┘  └─────────────────┘  └──────────────────────┘
```

详见 [ALGORITHM-OVERVIEW.md](ALGORITHM-OVERVIEW.md)。

---

## YAML Schema 速览（共享节点 = 拓扑分裂）

```yaml
version: "2.0.0"
routing_type: "hybrid_rf_graph"

terminals:                # 物理锚点（绝对坐标）
  PIN_RF_IN: { type: "pad", component: "U_DRV", pad: "OUT", x: 0.0, y: 50.0 }
  GND_REF:   { type: "ground_plane" }

nodes:                    # 逻辑节点（坐标算法推导）
  node_rf_shunt_tap: { type: "component_pad_junction" }
  node_cap_bypass:
    type: "floating_shunt_tap"
    placement_objective:
      strategy: "attract_to_target"
      target_terminal: "PIN_PA_VDD"
      weight: 100.0

edges:
  # 共享 node_rf_shunt_tap 的两段微带 = 拓扑分裂；电容自然吸附其上
  tl_rf_up_p1:
    type: "microstrip"
    routing_class: "rf_constrained"
    connections: [PIN_RF_IN, node_rf_shunt_tap]
    constraint: { width: 0.5, target_length: 6.0 }
    geometry:   { bend_style: "mitered_45" }

  c_rf_match:
    type: "lumped_capacitor"           # 一端是 GND_REF → 自动判定为并联
    connections: [node_rf_shunt_tap, GND_REF]

  trace_dc_1:
    type: "trace"
    routing_class: "flexible_path"     # 走 A*
    connections: [PIN_DC_IN, node_cap_bypass]
    constraint: { width: 0.8, target_length: null }
```

---

## 文件结构

```
├── ALGORITHM-OVERVIEW.md          # 总体架构 v4（推荐先读）
├── concepts/
│   ├── placement-problem-formulation.md   # SA 能量函数 + 引力场模型
│   ├── routing-algorithm-comparison.md    # 刚性 CP-SAT + 灵活 A* 双求解器
│   └── microstrip-topology-matching.md    # 微带类型 / bend_style / 阶跃阻抗
└── README.md
```
