# 功放PCB单层自动布局布线——总体算法方案 v4

> **混合射频图路由（hybrid_rf_graph）**
>
> 三类求解器分发：
> - **刚性 RF 微带网**（长度锁定的运动学求解 + CP-SAT 无交叉）
> - **灵活偏置网络**（A* 迷宫绕障，`target_length: null`）
> - **浮动器件布局**（引力场 / `placement_objective`）

---

## 0. v4 相对 v3 的关键改动

| v3 问题 | v4 解决方案 |
|---|---|
| `nodes` 段混用了"物理管脚"与"逻辑分叉点" | 新增 **`terminals`** 段（绝对坐标锚点），`nodes` 只放算法推导坐标的节点 |
| 所有边走同一条 CP-SAT 流水线，灵活偏置线被迫栅格化 → 变量爆炸 | 顶层 `routing_type: hybrid_rf_graph` + 每边 `routing_class` 三类分发 |
| `microstrip_parent.children` + `parent_edge` + `position_along_parent` 三重冗余，且 `target_length` 与 `position_along_parent` 互相覆盖 | 删除 `microstrip_parent`：**共享节点即拓扑分裂**，节点位置由两侧子段的 `target_length` 联立推导 |
| `lumped_*` 默认是并联接地分支（`shunt_tap_of`），无法表达**串联**集总器件（如磁珠 `BLM18`）| 集总器件统一为 **edge**，串/并由 `connections` 是否触地决定，无需 `shunt_tap_of` |
| 没有"浮动器件"概念，所有 shunt 必须有显式 `position_along_parent` | 新增 `floating_shunt_tap` 节点 + `placement_objective`（`space_available` / `attract_to_target`）|
| 无 `bend_style`、无 `stepped_impedance` 偏移 | 边支持 `geometry.bend_style`，节点支持 `stepped_impedance` + `connections_rule.custom_offset` |
| `target_length` 的"无约束"语义不明确 | 显式 `target_length: null` |
| Placement SA 的能量函数还残留 v2 的约束惩罚项 | SA 仍保持极简，新增**引力场项 `ζ·E_attract`** 仅对 `floating_shunt_tap` 生效 |

---

## 1. 顶层架构：三类求解器协同

```
                ┌──────────────────────────┐
                │  YAML (hybrid_rf_graph)  │
                │  terminals / nodes /edges│
                └─────────────┬────────────┘
                              │ routing_class triage
       ┌──────────────────────┼──────────────────────────┐
       ▼                      ▼                          ▼
┌──────────────┐    ┌──────────────────┐       ┌────────────────────┐
│ rf_constrained│    │ flexible_path     │       │ floating component │
│ 刚性 RF 微带  │    │ 灵活 DC/Bias 走线 │       │ placement_objective│
├──────────────┤    ├──────────────────┤       ├────────────────────┤
│ Kinematic     │    │ A* 迷宫寻路      │       │ 引力场 SA           │
│ length-lock   │    │ obstacles =      │       │ space_available    │
│ + CP-SAT      │    │  rigid + comps   │       │ attract_to_target  │
│ NoOverlap     │    │ target_length    │       │ weight × distance  │
│               │    │  = null          │       │                    │
└──────┬────────┘    └────────┬─────────┘       └─────────┬──────────┘
       │                      │                            │
       └──────────────────────┴───────────────────────────┘
                              ▼
                ┌──────────────────────────┐
                │   全局 DRC + 输出几何     │
                └──────────────────────────┘
```

**调度顺序**：
1. **Floating placement**（引力场 SA）→ 推算 `floating_shunt_tap` 节点的预选坐标。
2. **Rigid RF kinematic**（运动学求解 + CP-SAT NoOverlap）→ 锁死 `rf_constrained` 边的几何。
3. **Flexible A***（A* 迷宫）→ 把 1+2 的产物视作障碍物，绕障寻路。
4. **DRC + 回滚**：A* 失败 ⇒ 反馈到 SA 抬温度，回到第 1 步（最多 5 次）。

---

## 2. v4 完整 YAML Schema

### 2.1 顶层字段

```yaml
version: "2.0.0"
network_name: "PA_Complete_Module_Net"
routing_type: "hybrid_rf_graph"      # 必须；激活 v4 三类分发
description: "..."
```

### 2.2 `terminals` — 物理锚点（绝对坐标，不可移动）

```yaml
terminals:
  PIN_RF_IN:    { type: "pad",          component: "U_DRV", pad: "OUT", x: 0.0,   y: 50.0 }
  PIN_PA_GATE_1:{ type: "pad",          component: "U_PA",  pad: "G1",  x: 100.0, y: 70.0 }
  PIN_DC_IN:    { type: "pad",          component: "J_PWR", pad: "1",   x: 0.0,   y: 90.0 }
  GND_REF:      { type: "ground_plane", description: "全局地" }
```

| 字段 | 说明 |
|---|---|
| `type: pad` | 普通器件管脚，必带 `(x, y)` |
| `type: ground_plane` | 全局接地参考，统一处理所有并联分支 |
| `component` / `pad` | 反查器件—管脚的元信息（用于 component-aware DRC）|

### 2.3 `nodes` — 逻辑节点（坐标由算法推导）

```yaml
nodes:
  # ---- 普通分叉/吸附 ----
  node_rf_splitter:        { type: "t_junction",              description: "功分点" }
  node_rf_shunt_tap:       { type: "component_pad_junction",  description: "吸附匹配电容" }

  # ---- 串联器件两端 ----
  node_bead_in:            { type: "pad_junction" }
  node_bead_out:           { type: "pad_junction" }

  # ---- 阶跃阻抗（带物理偏移）----
  node_rf_step:
    type: "stepped_impedance"
    connections_rule:
      alignment_type: "custom_offset"
      offset_from_center: 0.25       # 下半支路阶跃阻抗的物理偏移（mm 或归一化比例）

  # ---- 浮动器件（引力场布局）----
  node_cap_bulk:
    type: "floating_shunt_tap"
    placement_objective:
      strategy: "space_available"    # 在空白处寻找位置

  node_cap_bypass:
    type: "floating_shunt_tap"
    placement_objective:
      strategy: "attract_to_target"
      target_terminal: "PIN_PA_VDD"
      weight: 100.0                  # 极高权重 → 紧贴目标管脚
```

**节点类型表**：

| `type` | 用途 | 坐标决定方式 |
|---|---|---|
| `t_junction` | RF 主干分叉 | 与左右子段联立解（运动学）|
| `component_pad_junction` | 串/并器件吸附点（位置必须确定）| 由两侧子段 `target_length` 推算 |
| `pad_junction` | 串联器件两端的过渡点 | 由邻接边几何确定 |
| `stepped_impedance` | 阶跃阻抗变径点 | 同上 + `custom_offset` |
| `floating_shunt_tap` | 浮动并联器件吸附点 | 由 `placement_objective` 引力场 SA 决定 |

> ⚠️ **v3→v4 重要变化**：取消 `parent_edge` + `position_along_parent` 字段。节点位置完全由"它两侧的边的 `target_length` + 起止端点坐标"在求解器里联立解出，避免冗余/冲突。

### 2.4 `edges` — 广义边（微带线 / 普通走线 / 集总器件）

```yaml
edges:
  # ===== A. 刚性射频网络区 =====
  tl_rf_main:
    type: "microstrip"
    routing_class: "rf_constrained"
    connections: [ "PIN_RF_IN", "node_rf_splitter" ]
    constraint: { width: 1.20, target_length: 15.00 }
    geometry:   { bend_style: "mitered_45" }       # 拐角风格

  # 拓扑分裂：tl_rf_up_p1 与 tl_rf_up_p2 共享 node_rf_shunt_tap
  tl_rf_up_p1:
    type: "microstrip"
    routing_class: "rf_constrained"
    connections: [ "node_rf_splitter", "node_rf_shunt_tap" ]
    constraint: { width: 0.50, target_length: 6.00 }

  c_rf_match:
    type: "lumped_capacitor"                       # 并联（一端为 GND_REF）
    connections: [ "node_rf_shunt_tap", "GND_REF" ]
    parameters: { value_pF: 1.5, package: "0402" }

  tl_rf_up_p2:
    type: "microstrip"
    routing_class: "rf_constrained"
    connections: [ "node_rf_shunt_tap", "PIN_PA_GATE_1" ]
    constraint: { width: 0.50, target_length: 12.00 }

  # 阶跃阻抗
  tl_rf_down_narrow:
    type: "microstrip"
    routing_class: "rf_constrained"
    connections: [ "node_rf_splitter", "node_rf_step" ]
    constraint: { width: 0.50, target_length: 8.00 }

  tl_rf_down_wide:
    type: "microstrip"
    routing_class: "rf_constrained"
    connections: [ "node_rf_step", "PIN_PA_GATE_2" ]
    constraint: { width: 1.50, target_length: 10.00 }

  # ===== B. 灵活偏置网络区 =====
  trace_dc_1:
    type: "trace"
    routing_class: "flexible_path"
    connections: [ "PIN_DC_IN", "node_cap_bulk" ]
    constraint: { width: 0.80, target_length: null }   # 无长度约束 → A*

  C_bulk:
    type: "lumped_capacitor"                            # 并联
    connections: [ "node_cap_bulk", "GND_REF" ]
    parameters: { value: "10uF", package: "0805" }

  trace_dc_2:
    type: "trace"
    routing_class: "flexible_path"
    connections: [ "node_cap_bulk", "node_bead_in" ]
    constraint: { width: 0.80, target_length: null }

  L_bead:
    type: "lumped_inductor"                             # 串联（两端均非 GND）
    connections: [ "node_bead_in", "node_bead_out" ]
    parameters: { package: "0603", part: "BLM18" }
```

### 2.5 边类型 × `routing_class` 矩阵

| `type` | `routing_class` | 求解器 | 长度约束 |
|---|---|---|---|
| `microstrip` | `rf_constrained` | 运动学 + CP-SAT NoOverlap | `target_length` 硬锁（±tol）|
| `trace` | `flexible_path` | A* 迷宫 | `null`（忽略）|
| `lumped_capacitor` / `lumped_inductor` | （由 connections 推断）| 不直接参与寻路；作为节点的物理 footprint | — |

**集总器件的串/并自动判定**：
- `connections` 中包含 `GND_REF` → **并联接地分支**（自动生成接地过孔）。
- `connections` 两端都是普通节点 → **串联器件**（footprint 占位 + 两端连续走线对齐 pad）。

---

## 3. 拓扑分裂展开（v4）

```python
def expand_topology(netlist: dict) -> dict:
    """
    v4 展开规则：
    - 不再有 microstrip_parent；共享节点即拓扑分裂。
    - lumped_* 中含 GND_REF → 并联（生成 ground_branch + via）。
    - lumped_* 两端都是普通节点 → 串联（生成 series_component footprint）。
    - trace + routing_class=flexible_path → 进 A* 队列。
    - microstrip + routing_class=rf_constrained → 进运动学 + CP-SAT 队列。
    """
    out_nodes = dict(netlist["terminals"])    # terminals 直接进入坐标已知集
    out_nodes.update(netlist.get("nodes", {}))

    rf_edges, flex_edges, series_comps, shunt_branches = [], [], [], []

    for name, edge in netlist["edges"].items():
        etype = edge.get("type", "")
        rclass = edge.get("routing_class")

        if etype.startswith("lumped_"):
            ends = edge["connections"]
            if "GND_REF" in ends:
                shunt_branches.append({**edge, "name": name,
                                       "via_diameter": 0.3})
            else:
                series_comps.append({**edge, "name": name})
            continue

        if rclass == "rf_constrained":
            rf_edges.append({**edge, "name": name})
        elif rclass == "flexible_path":
            flex_edges.append({**edge, "name": name})
        else:
            raise ValueError(f"edge {name} missing routing_class")

    return {
        "nodes": out_nodes,
        "rf_edges": rf_edges,
        "flex_edges": flex_edges,
        "series_components": series_comps,
        "shunt_branches": shunt_branches,
    }
```

> 关键：**没有 `microstrip_parent` 段**；`tl_rf_up_p1` 和 `tl_rf_up_p2` 通过共享 `node_rf_shunt_tap` 自动构成"分裂"关系，运动学求解器在解节点坐标时会自然把它们对齐到同一条逻辑微带线上。

---

## 4. 算法流程 v4

```
┌─────────────────────────────────────────────────────────────────┐
│                    v4 三阶段协同（最多 5 轮）                    │
│                                                                 │
│  Phase 1: Floating Placement SA                                 │
│  ─────────────────────────────────                              │
│  E = α·HPWL + γ·C_boundary + δ·C_thermal + ζ·E_attract          │
│  目标：解出 floating_shunt_tap 节点的初始坐标                     │
│                                                                 │
│         ↓ 输出节点坐标                                           │
│                                                                 │
│  Phase 2: Rigid RF Kinematic + CP-SAT                           │
│  ─────────────────────────────────                              │
│  · 端点已知 + target_length 已知 → 联立解 t_junction 等节点坐标   │
│  · CP-SAT AddNoOverlap：所有刚性 RF 段两两不交叉                  │
│  · stepped_impedance 节点应用 custom_offset                     │
│  · bend_style 决定拐角几何                                       │
│                                                                 │
│         ↓ 输出 RF 几何（视作 obstacle）                          │
│                                                                 │
│  Phase 3: Flexible A* Routing                                  │
│  ─────────────────────────────────                              │
│  · obstacles = (Phase 2 RF 几何) ∪ (series_components 占位)      │
│                ∪ (shunt_branches via 占位) ∪ 板框                │
│  · 对每条 flexible_path 边跑 A*（无长度约束）                    │
│  · 失败 → 反馈到 Phase 1 抬温度，重启                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. Placement SA（v4 能量函数）

```python
def compute_energy_v4(placement, netlist):
    hpwl     = compute_hpwl(placement, netlist)
    boundary = compute_boundary_penalty(placement, board_bounds)
    thermal  = compute_thermal_penalty(placement, power_devices)
    attract  = compute_attract_field(placement, netlist)   # ⭐ v4 新增

    return α * hpwl + γ * boundary + δ * thermal + ζ * attract


def compute_attract_field(placement, netlist):
    """
    引力场项：仅对 floating_shunt_tap 节点生效
      - strategy=attract_to_target: weight × dist(node, target_terminal)²
      - strategy=space_available:   惩罚"靠近已占用区域"，鼓励落到空白
    """
    E = 0.0
    for nname, node in netlist["nodes"].items():
        if node.get("type") != "floating_shunt_tap":
            continue
        obj = node["placement_objective"]
        if obj["strategy"] == "attract_to_target":
            tgt = netlist["terminals"][obj["target_terminal"]]
            d2  = (placement[nname].x - tgt["x"]) ** 2 \
                + (placement[nname].y - tgt["y"]) ** 2
            E  += obj["weight"] * d2
        elif obj["strategy"] == "space_available":
            E  += local_density(placement, nname)   # 已占用密度作惩罚
    return E
```

> 注意：约束类项（无交叉、过孔密度）仍保留 v3 的"全部交给 CP-SAT 强制"原则——SA 不承担硬约束。

---

## 6. CP-SAT 约束（v4 仅作用于刚性 RF 区）

| 约束 | CP-SAT 原语 | 适用范围 |
|---|---|---|
| RF 段长度 | `AddLinearExpression`（±tol） | 所有 `rf_constrained` 边 |
| 节点坐标联立 | 几何方程（`AddAbsEquality` 等） | 共享节点的两侧子段 |
| 阶跃阻抗偏移 | 节点 `custom_offset` 转为线性约束 | `stepped_impedance` 节点 |
| 无交叉 | `AddNoOverlap` + `AddBoolOr` | 所有 RF 边 + series_component footprint |
| 过孔密度 | `AddCumulative` | shunt_branches via |
| 预布线障碍 | `occupied_cells` | 由 Phase 2 RF 几何生成，供 Phase 3 的 A* 使用 |

**关键实现：无交叉约束（与 §3.2 routing-algorithm-comparison.md 统一谓词方向）**

```python
def add_pairwise_no_overlap(model, e_a, e_b, x):
    """两边之间的无交叉：x 方向分离 OR y 方向分离（强制约束）"""
    bbox_a = get_path_bbox(e_a, x)
    bbox_b = get_path_bbox(e_b, x)

    x_sep = model.NewBoolVar(f"xsep_{e_a.name}_{e_b.name}")
    y_sep = model.NewBoolVar(f"ysep_{e_a.name}_{e_b.name}")

    # x_sep == True ⇔ a 在 b 左侧 或 b 在 a 左侧
    model.Add(bbox_a.max_x + e_a.width / 2 <= bbox_b.min_x).OnlyEnforceIf(x_sep)
    model.Add(bbox_b.max_x + e_b.width / 2 <= bbox_a.min_x).OnlyEnforceIf(x_sep)

    model.Add(bbox_a.max_y + e_a.width / 2 <= bbox_b.min_y).OnlyEnforceIf(y_sep)
    model.Add(bbox_b.max_y + e_b.width / 2 <= bbox_a.min_y).OnlyEnforceIf(y_sep)

    model.AddBoolOr([x_sep, y_sep])      # 至少一个方向必须分离
```

---

## 7. 灵活路径 A* 求解器（替代 v3 中对 flexible 边的 CP-SAT）

```python
def route_flexible(flex_edge, obstacles, grid):
    """
    A* 迷宫绕障；忽略 target_length（因为是 null）。
    Cost = 路径段数 + λ·拐弯惩罚 + μ·靠近 RF 边惩罚（避免耦合）
    """
    start = grid.snap(flex_edge.connections[0].xy)
    goal  = grid.snap(flex_edge.connections[1].xy)
    return astar(
        start, goal,
        passable=lambda c: c not in obstacles,
        cost=cell_cost(flex_edge, obstacles),
    )
```

**为什么从 CP-SAT 切到 A***：
- 灵活偏置线没有长度约束 ⇒ CP-SAT 的 `AddLinearExpression` 失去用武之地。
- 单条边在 500×500 网格里 CP-SAT 变量 ≈10⁵；A* 单边 < 10ms。
- 复杂度从 v3 估算的 10⁶ 量级下降到 RF 段数 × 节点联立 ≈ 10²~10³。

---

## 8. 典型 Doherty 功放量化（v4 重新评估）

| 参数 | v3 估算 | v4 估算 | 改善原因 |
|---|---|---|---|
| 微带线段数（含拓扑分裂） | 15-30 | 15-30 | — |
| 浮动器件 (`floating_shunt_tap`) | 不支持 | 5-15 | 新增能力 |
| 串联器件 (`L_bead` 类) | 不支持 | 2-5 | 新增能力 |
| RLC 并联 shunt | 23-42 | 23-42 | — |
| **CP-SAT 变量规模** | ~10⁵-10⁶ | **~10⁴**（仅 RF）| flex 不再走 CP-SAT |
| **求解时间** | 30-120 s | **5-30 s** | A* 替换灵活线 CP-SAT |

---

## 9. 文件索引

| 文件 | 内容 |
|---|---|
| `ALGORITHM-OVERVIEW.md` | **总体架构 v4**（本文档）|
| `concepts/placement-problem-formulation.md` | 布局 SA + 引力场模型 |
| `concepts/routing-algorithm-comparison.md` | 刚性 RF CP-SAT + 灵活 A* 双求解器实现 |
| `concepts/microstrip-topology-matching.md` | 微带线类型、`bend_style`、阶跃阻抗 `custom_offset` |
