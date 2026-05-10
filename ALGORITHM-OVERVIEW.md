# 功放PCB单层自动布局布线——总体算法方案 v3

> 覆盖完整流程：布局（Placement）→ 布线（Routing）→ 后处理
>
> 场景：
> - 固定尺寸微带线 + **部分灵活走线**（拓扑分裂结构）
> - **大量并联RLC shunt接地**
> - **固定路由边（预布线）**
> - 高密度 RLC | 无交叉硬约束

---

## 0. 核心改进：约束分离原则

**v2 的问题**：能量函数包含 6 个相互耦合的参数（HPWL权重/交叉惩罚/拥塞惩罚/接地拥塞惩罚/热惩罚/边界惩罚），参数调优是指数级难度。

**v3 的根因**：硬约束（无交叉、拥塞限制）不应该放在 SA 能量函数中作为软惩罚项。

**v3 的解决方案**：

```
Placement SA（只优化物理性能）：
E = α·HPWL + γ·C_boundary + δ·C_thermal
    ↓ （删除 β·C_cross + ε·C_congestion + ζ·C_ground_congestion）
CP-SAT（强制满足所有硬约束）：
- AddNoOverlap（无交叉）← 删除SA的C_cross
- AddCumulative（过孔密度）← 删除SA的C_congestion
- AddCircuit + AddLinearExpression（连通+长度）
```

**为什么这样可行？** CP-SAT 的 `AddNoOverlap` 是强制约束，不是惩罚项。只要 CP-SAT 有解，输出一定 100% 无交叉。这比用 SA 软优化 β·C_cross 可靠一百倍。

---

## 1. 完整 YAML Schema（拓扑分裂完全对齐）

### 1.1 节点类型

```yaml
nodes:
  # 射频端口/器件引脚
  rf_port_1:
    type: "pad"
    x: 0.0
    y: 0.0

  # 微带线 junctions
  junction_001:
    type: "junction"
    x: 10.0
    y: 0.0

  # RLC shunt 吸附点（核心新增）⭐
  node_shunt_tap_001:
    type: "component_pad_junction"   # 专用于RLC吸附点的节点类型
    parent_edge: "tl_main"            # 属于哪条父微带线
    position_along_parent: 0.4       # 在父边上的位置比例（0.0~1.0）
    physical_pad: { width: 1.0, height: 0.5, package: "0402" }

  node_shunt_tap_002:
    type: "component_pad_junction"
    parent_edge: "tl_main"
    position_along_parent: 0.7

  # 全局接地参考点
  GND_REF:
    type: "ground_reference"

  # 器件 pad
  inductor_pad_001:
    type: "component_pad"
    x: 25.0
    y: 20.0

  capacitor_pad_001:
    type: "component_pad"
    x: 30.0
    y: 18.0
```

### 1.2 边类型详解

```yaml
edges:
  # ============================================================
  # 1. 微带线父边（逻辑分组，不直接参与CP-SAT求解）
  # ============================================================
  tl_main:
    type: "microstrip_parent"       # 父边类型
    connections: [rf_port_1, output_junction]
    constraint: { width: 1.0 }
    children: [tl_main_part1, tl_main_part2, tl_main_part3]
    # 总长度 = sum(子段target_length)

  # ============================================================
  # 2. 微带线子段（CP-SAT求解的最小单元）
  # ============================================================
  tl_main_part1:
    type: "microstrip"
    connections: [rf_port_1, node_shunt_tap_001]
    constraint: { width: 1.0, target_length: 4.0 }
    parent: "tl_main"              # 显式声明父关系

  tl_main_part2:
    type: "microstrip"
    connections: [node_shunt_tap_001, node_shunt_tap_002]
    constraint: { width: 1.0, target_length: 3.0 }
    parent: "tl_main"

  tl_main_part3:
    type: "microstrip"
    connections: [node_shunt_tap_002, output_junction]
    constraint: { width: 1.0, target_length: 3.0 }
    parent: "tl_main"

  # ============================================================
  # 3. RLC shunt 接地分支（通过 shunt_tap_of 吸附到父边上）
  # ============================================================
  c_shunt_001:
    type: "lumped_capacitor"
    connections: [node_shunt_tap_001, GND_REF]
    parameters: { value_pF: 5.6, package: "0402" }
    shunt_tap_of: "tl_main"        # 吸附在哪条父边上
    via: { diameter: 0.3 }         # 自动生成接地过孔

  l_bias_001:
    type: "lumped_inductor"
    connections: [node_shunt_tap_001, bias_node]
    parameters: { value_nH: 12, package: "0603" }
    shunt_tap_of: "tl_main"
    via: { diameter: 0.3 }

  c_shunt_002:
    type: "lumped_capacitor"
    connections: [node_shunt_tap_002, GND_REF]
    parameters: { value_pF: 2.2, package: "0402" }
    shunt_tap_of: "tl_main"
    via: { diameter: 0.3 }

  # ============================================================
  # 4. 预布线（固定路由，路径完全确定，作为障碍物）
  # ============================================================
  bias_feed:
    type: "fixed_route"
    connections: [power_bus, node_shunt_tap_001]
    width: 0.8
    path_coords: [[0, 10], [0, 20], [5, 20]]  # 完全固定路径

  # ============================================================
  # 5. 灵活走线（需要CP-SAT求解）
  # ============================================================
  output_trace:
    type: "flexible"
    connections: [output_junction, rf_port_2]
    constraint: { width: 1.5, target_length: 12.0 }
    fixed: false
```

---

## 2. 拓扑分裂展开算法

```python
def expand_topology(netlist: dict) -> dict:
    """
    将拓扑分裂模型展开为CP-SAT可处理的扁平边列表

    规则：
    - microstrip_parent + children → 子段加入求解
    - lumped_* + shunt_tap_of → ground_branch 加入求解
    - fixed_route → occupied_cells 加入障碍
    - microstrip → 正常求解
    """
    expanded_nodes = dict(netlist["nodes"])
    expanded_edges = {}

    for name, edge in netlist["edges"].items():
        etype = edge.get("type", "")

        if etype == "microstrip_parent":
            # 父边不加入，把所有子段展开加入
            for child_name in edge.get("children", []):
                expanded_edges[child_name] = {
                    **netlist["edges"][child_name],
                    "parent": name
                }

        elif etype.startswith("lumped_"):
            # 集总器件 → ground_branch 类型
            expanded_edges[name] = {
                **edge,
                "type": "ground_branch",
                "via_diameter": edge.get("via", {}).get("diameter", 0.3)
            }

        elif etype == "fixed_route":
            # 预布线：加入障碍映射
            expanded_edges[name] = {**edge}

        elif etype == "microstrip":
            # 正常微带线段
            expanded_edges[name] = {**edge}

        elif etype == "flexible":
            expanded_edges[name] = {**edge}

    return {"nodes": expanded_nodes, "edges": expanded_edges}


def build_obstacle_map(fixed_routes: dict, grid: Grid) -> set:
    """
    将所有 fixed_route 边转为占用的网格单元集合
    """
    occupied = set()
    for name, edge in fixed_routes.items():
        for (x, y) in edge.get("path_coords", []):
            w = edge.get("width", 1.0)
            for dx in range(-int(w/2/grid.resolution), int(w/2/grid.resolution)+1):
                for dy in range(-int(w/2/grid.resolution), int(w/2/grid.resolution)+1):
                    gx = int((x + dx) / grid.resolution)
                    gy = int((y + dy) / grid.resolution)
                    occupied.add((gx, gy))
    return occupied
```

---

## 3. 算法流程 v3

```
┌─────────────────────────────────────────────────────────────┐
│                   v3 迭代协同（最多5次）                      │
│                                                             │
│  ┌────────────────┐                                         │
│  │ Placement SA   │  E = α·HPWL + γ·C_boundary            │
│  │ 极简化能量函数  │            + δ·C_thermal             │
│  │                │  ← 无任何约束惩罚项                      │
│  └───────┬────────┘                                        │
│          │                                                 │
│          ▼                                                 │
│  ┌────────────────┐   ┌──────────────────────────────┐   │
│  │ expand_topology│ → │ CP-SAT Routing（扁平边列表）   │   │
│  │ 拓扑展开        │   │                              │   │
│  └───────┬────────┘   │ · AddCircuit（连通性）        │   │
│          │             │ · AddLinearExpression（长度） │   │
│          │             │ · AddNoOverlap（无交叉）⭐    │   │
│          │             │ · AddCumulative（过孔密度）⭐ │   │
│          │             │ · occupied_cells（预布线）⭐   │   │
│          │             └───────┬──────────────────────┘   │
│          │                     │                         │
│          │             ┌───────┴────────┐               │
│          │             ↓                ↓                 │
│          │          成功             失败（infeasible）    │
│          │             ↓                ↓                 │
│          │         收敛判断        提取冲突约束              │
│          │             ↓                ↓                 │
│          │         输出结果        反哺SA温度（不是权重）     │
│          │                             ↓                 │
│          │                      回到Placement（热启动）       │
│          │                                                │
│          └──────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Placement SA（极简化能量函数）

```python
def compute_energy_v3(placement: dict, netlist: dict) -> float:
    """
    v3能量函数：只包含物理性能项，无任何约束惩罚项
    所有约束（交叉/拥塞/接地密度）全部由CP-SAT处理
    """
    hpwl = compute_hpwl(placement, netlist)
    boundary = compute_boundary_penalty(placement, board_bounds)
    thermal = compute_thermal_penalty(placement, power_devices)

    # 约束惩罚项：全部删除（β=ε=ζ=0）
    return α * hpwl + γ * boundary + δ * thermal


def adaptive_temperature(routing_success_rate: float, current_T: float) -> float:
    """
    CP-SAT成功率 → SA初始温度调整（而非权重调整）
    成功率低 → 提高温度，鼓励跳出局部最优
    成功率高 → 降低温度，微调即可
    """
    if routing_success_rate < 0.5:
        return current_T * 1.2   # 提高温度
    elif routing_success_rate > 0.9:
        return current_T * 0.95  # 降低温度
    else:
        return current_T          # 保持不变
```

### 为什么删除约束项是对的？

| 删除的项 | 原本在SA中 | 应该在CP-SAT中 |
|---------|-----------|--------------|
| C_cross | 软惩罚 | `AddNoOverlap`（强制） |
| C_congestion | 软惩罚 | `AddCumulative`（强制） |
| C_ground_congestion | 软惩罚 | `AddCumulative`（强制） |

SA 软优化约束 → 约束可能被违反（infeasible）。
CP-SAT 强制约束 → 只要有解，约束 100% 满足。

---

## 5. CP-SAT 完整约束（v3）

### 5.1 约束汇总

| 约束 | CP-SAT 原语 | 来自 |
|------|------------|------|
| 路径连通性 | `AddCircuit` | 所有灵活边/子段 |
| 目标长度 | `AddLinearExpression` | 所有子段 |
| 无交叉 | `AddNoOverlap` + `AddBoolOr` | **所有边**（含接地分支） |
| 过孔密度 | `AddCumulative` | 接地过孔 + 信号过孔 |
| 星型接地板 | 独立zone + `AddCumulative` | 接地分支汇聚点 |
| 预布线障碍 | `SetValue(0)` | 固定路由边 |
| 固定微带线 | `SetValue(1)` | `fixed: true` |

### 5.2 关键实现：无交叉约束（所有边类型统一）

```python
def add_no_cross_constraints_all(model, all_edges, x, grid):
    """
    统一处理所有边的无交叉约束
    包括：微带线子段 + 接地分支 + 预布线（预布线用包围盒）
    """
    # 需要NoOverlap的两两类：所有灵活子段 + 接地分支
    constrained_edges = [
        e for e in all_edges
        if e["type"] in ("microstrip", "ground_branch", "flexible")
    ]

    for i, e_a in enumerate(constrained_edges):
        for e_b in constrained_edges[i+1:]:
            add_pairwise_no_overlap(model, e_a, e_b, x)


def add_pairwise_no_overlap(model, e_a, e_b, x):
    """
    两边之间的无交叉：x方向分离 OR y方向分离
    """
    bbox_a = get_path_bbox(e_a, x)
    bbox_b = get_path_bbox(e_b, x)

    x_sep = model.NewBoolVar("")
    y_sep = model.NewBoolVar("")

    model.Add(
        bbox_a.max_x + e_a["constraint"]["width"]/2 <= bbox_b.min_x
    ).OnlyEnforceIf(x_sep)
    model.Add(
        bbox_b.max_x + e_b["constraint"]["width"]/2 <= bbox_a.min_x
    ).OnlyEnforceIf(x_sep)

    model.Add(
        bbox_a.max_y + e_a["constraint"]["width"]/2 <= bbox_b.min_y
    ).OnlyEnforceIf(y_sep)
    model.Add(
        bbox_b.max_y + e_b["constraint"]["width"]/2 <= bbox_a.min_y
    ).OnlyEnforceIf(y_sep)

    model.AddBoolOr([x_sep, y_sep])
```

---

## 6. 典型Doherty功放量化

| 参数 | 典型值 |
|------|--------|
| 微带线父边数 | 5-8 条 |
| 微带线子段数（含shunt tap分裂） | 15-30 段 |
| RLC shunt 总数 | 23-42 个 |
| 接地分支数 | 23-42 条 |
| 预布线（bias等） | 5-10 条 |
| **CP-SAT变量规模** | ~10⁵-10⁶ |
| **求解时间（典型Doherty）** | **30-120s** |

---

## 7. 文件索引

| 文件 | 内容 |
|------|------|
| `ALGORITHM-OVERVIEW.md` | **总体架构 v3**（本文档）|
| `concepts/placement-problem-formulation.md` | 布局数学建模（SA、能量函数）|
| `concepts/routing-algorithm-comparison.md` | 布线 CP-SAT 详细实现 |
| `concepts/microstrip-topology-matching.md` | 微带线拓扑类型、mitered 补偿 |
