# PCB 单层无交叉布线——双求解器实现指南（v4）

> 场景：刚性微带 + 灵活偏置 + 浮动器件 | 高密度 RLC shunt | 无交叉硬约束
>
> 目标：从输入 YAML 到可布线路径的完整算法流程（CP-SAT for RF + A* for flex）

---

## 1. 输入格式（v4 schema）

```yaml
version: "2.0.0"
routing_type: "hybrid_rf_graph"

# ── Terminals: 物理锚点（绝对坐标，不可移动） ─────────────
terminals:
  P1:      { type: "pad",          component: "U_DRV", pad: "OUT", x: 0.0,  y: 0.0 }
  P2:      { type: "pad",          component: "U_PA",  pad: "G1",  x: 30.0, y: 0.0 }
  GND_REF: { type: "ground_plane" }

# ── Nodes: 逻辑节点（坐标算法推导） ──────────────────────
nodes:
  J1:    { type: "t_junction" }
  SHUNT: { type: "component_pad_junction" }

# ── Edges: 微带 / 灵活线 / 集总器件 ──────────────────────
edges:
  main_line_1:
    type: "microstrip"
    routing_class: "rf_constrained"           # 走 CP-SAT
    connections: [P1, J1]
    constraint: { width: 2.0, target_length: 10.0 }
    geometry:   { bend_style: "mitered_45" }

  shunt_branch:
    type: "microstrip"
    routing_class: "rf_constrained"
    connections: [J1, SHUNT]
    constraint: { width: 1.0, target_length: 8.0 }

  c_shunt:
    type: "lumped_capacitor"                  # 一端为 GND_REF → 自动并联
    connections: [SHUNT, GND_REF]
    parameters: { value_pF: 5.6, package: "0402" }

  output_line:
    type: "trace"
    routing_class: "flexible_path"            # 走 A*
    connections: [SHUNT, P2]
    constraint: { width: 1.5, target_length: null }
```

**与 v3 的关键差异**：

| v3 | v4 |
|---|---|
| `nodes` 既放 pad 也放 junction | `terminals` 放绝对坐标，`nodes` 只放推导坐标 |
| `fixed: true/false` 决定是否参与求解 | `routing_class` 决定走哪个求解器 |
| `lumped_*` 必须配 `shunt_tap_of` 字段 | `connections` 含 `GND_REF` 即并联，无需额外字段 |
| `target_length` 缺失就当作"无约束" | 显式 `target_length: null` |

---

## 2. 几何建模：连续坐标 + 1µm 网格

**结论：连续坐标用于约束建模 + 1µm 网格用于求解器离散化。**

```python
class EdgeSegment:
    """RF 边的几何表示（CP-SAT 求解的最小单元）"""
    def __init__(self, edge_id, start, end, width):
        self.edge_id = edge_id
        self.start   = start        # (x, y) 连续坐标 mm
        self.end     = end
        self.width   = width
        self.bbox    = self._compute_bbox()

    def _compute_bbox(self):
        xs = [self.start[0], self.end[0]]
        ys = [self.start[1], self.end[1]]
        return BoundingBox(min_x=min(xs), max_x=max(xs),
                           min_y=min(ys), max_y=max(ys))

    def to_rectangle(self):
        """转为 OR-Tools NoOverlap 矩形（含线宽 outset）"""
        return Rectangle(
            self.bbox.min_x - self.width / 2,
            self.bbox.min_y - self.width / 2,
            self.bbox.max_x - self.bbox.min_x + self.width,
            self.bbox.max_y - self.bbox.min_y + self.width,
        )
```

**为什么不用纯栅格？**

- 栅格化引入量化误差 → 路径长度不精确。
- RF 段 `target_length` 要求精确到亚毫米。
- 1µm 离散精度对实际 PCB 制造完全够用。

---

## 3. CP-SAT 建模：仅作用于 `rf_constrained` 边

> ⚠️ **v4 重要变化**：CP-SAT **不再处理灵活线**（v3 把 flexible 也丢进 CP-SAT 导致 10⁶ 变量爆炸）。灵活线见 §4 A*。

### 3.1 变量定义

```python
from ortools.sat.python import cp_model

model = cp_model.CpModel()
GRID  = 1e-3                 # 1 µm 离散精度（mm）

# Terminal 坐标固定，转为整数
term_x = {t: round(pos["x"] / GRID) for t, pos in terminals.items()}
term_y = {t: round(pos["y"] / GRID) for t, pos in terminals.items()}

# 推导节点坐标 = 整数变量（边界 = 板框）
node_x = {n: model.NewIntVar(0, board_w_int, f"x_{n}") for n in nodes}
node_y = {n: model.NewIntVar(0, board_h_int, f"y_{n}") for n in nodes}

# RF 边端点查表：terminal → 常量；node → 变量
def coord(name, axis):
    if name in terminals: return term_x[name] if axis == "x" else term_y[name]
    return node_x[name]   if axis == "x" else node_y[name]
```

### 3.2 长度锁定（运动学）

每条 `rf_constrained` 边强制满足 $|x_u - x_v| + |y_u - y_v| \approx L$（曼哈顿近似），或对纯水平/竖直段直接相等：

```python
def add_length_constraint(model, edge):
    u, v = edge["connections"]
    L    = round(edge["constraint"]["target_length"] / GRID)
    tol  = round(L * 0.005)            # ±0.5%

    dx = model.NewIntVar(0, L, f"dx_{edge['name']}")
    dy = model.NewIntVar(0, L, f"dy_{edge['name']}")
    model.AddAbsEquality(dx, coord(u, "x") - coord(v, "x"))
    model.AddAbsEquality(dy, coord(u, "y") - coord(v, "y"))

    model.Add(dx + dy >= L - tol)
    model.Add(dx + dy <= L + tol)
```

> 共享节点的两条 RF 边（如 `tl_rf_up_p1` 与 `tl_rf_up_p2` 共享 `node_rf_shunt_tap`）会同时约束同一个 `node_x[node_rf_shunt_tap]`、`node_y[node_rf_shunt_tap]`——这就是"拓扑分裂"在求解器层面的实现：**自动联立**。

### 3.3 无交叉约束（核心）

```python
def add_pairwise_no_overlap(model, e_a, e_b):
    """
    两条 RF 边之间：x 方向分离 OR y 方向分离（强制约束）

    谓词方向修正（v3 文档此处反向，已在 v4 统一）：
      x_sep == True  ⇔  a 完全在 b 左侧 OR b 完全在 a 左侧
    """
    bbox_a = get_path_bbox(e_a)
    bbox_b = get_path_bbox(e_b)

    x_sep = model.NewBoolVar(f"xsep_{e_a['name']}_{e_b['name']}")
    y_sep = model.NewBoolVar(f"ysep_{e_a['name']}_{e_b['name']}")

    # x_sep 为真时，bbox 必须 x 方向不重叠
    model.Add(bbox_a.max_x + e_a["width"] / 2 <= bbox_b.min_x).OnlyEnforceIf(x_sep)
    model.Add(bbox_b.max_x + e_b["width"] / 2 <= bbox_a.min_x).OnlyEnforceIf(x_sep)

    # y_sep 同理
    model.Add(bbox_a.max_y + e_a["width"] / 2 <= bbox_b.min_y).OnlyEnforceIf(y_sep)
    model.Add(bbox_b.max_y + e_b["width"] / 2 <= bbox_a.min_y).OnlyEnforceIf(y_sep)

    model.AddBoolOr([x_sep, y_sep])     # 至少一方向必须分离
```

> 与 v3 文档的差别：v3 的 `OnlyEnforceIf(x_overlap.Not())` 谓词方向反了——把"必须不重叠"绑到了"重叠为假"上，逻辑上是双重否定且需要反向 BoolOr，容易写错。v4 改用直接的 `x_sep`/`y_sep`，与 `ALGORITHM-OVERVIEW.md §6` 保持一致。

### 3.4 阶跃阻抗 `custom_offset`

```python
def add_stepped_impedance(model, node, edge_in, edge_out):
    """对齐两侧子段的中心线，按 offset_from_center 偏移"""
    rule   = node["connections_rule"]
    offset = round(rule["offset_from_center"] / GRID)

    # 下游边的中心线 y 坐标 = 上游边中心线 y 坐标 + offset
    model.Add(coord(edge_out["connections"][1], "y") ==
              coord(edge_in["connections"][0], "y") + offset)
```

### 3.5 过孔密度

```python
def add_via_density(model, shunt_branches, zones, max_per_zone):
    """每个区域接地过孔数 ≤ 容量（AddCumulative）"""
    for zone in zones:
        in_zone = [b for b in shunt_branches if b["node"] in zone]
        if not in_zone: continue
        model.AddCumulative(
            intervals=[zone_interval(b) for b in in_zone],
            demands  =[1] * len(in_zone),
            capacity =max_per_zone,
        )
```

---

## 4. A* 求解器：仅作用于 `flexible_path` 边

```python
def route_flexible_edges(flex_edges, rigid_geometry, board_grid):
    """
    把 CP-SAT 求出的 RF 几何 + 串联器件 footprint + shunt via 都视作 obstacle，
    然后对每条 flexible_path 边做 A* 寻路。
    """
    obstacles = build_obstacle_map(rigid_geometry, board_grid)
    results   = {}

    for e in flex_edges:
        start = board_grid.snap(coord_of(e["connections"][0]))
        goal  = board_grid.snap(coord_of(e["connections"][1]))

        path = astar(
            start, goal,
            passable=lambda c: c not in obstacles,
            cost=lambda c, prev: (
                1                                  # 步长
                + 0.5 * is_turn(prev, c)           # 拐弯惩罚
                + 2.0 * coupling_penalty(c, rigid_geometry)  # 远离 RF 边
            ),
        )
        if path is None:
            return None      # 触发回到 SA 抬温度
        # 走线宽度 outset 后回写 obstacles，避免后续灵活线和它重叠
        obstacles |= inflate(path, e["constraint"]["width"], board_grid)
        results[e["name"]] = path
    return results
```

**为什么 A* 而不是 CP-SAT？**

| 方面 | CP-SAT（v3 做法） | A*（v4 做法） |
|---|---|---|
| 变量规模 | 单边 500×500×2 ≈ 5×10⁵ 布尔变量 | 0（直接搜路径）|
| 长度约束 | 必须有 `target_length` | 灵活线本来就 `null` |
| 单边耗时 | 数秒 ~ 数十秒 | 通常 < 10 ms |
| 多边交互 | 全局求解 | 顺序 A*，已布的边作障碍（贪心，但偏置网络一般足够）|

---

## 5. 完整求解流程（v4 三阶段）

```python
def solve_v4(topology: dict) -> dict:
    # ── Phase 0: 拓扑展开 ──────────────────────────────
    expanded = expand_topology(topology)
    # → terminals/nodes/rf_edges/flex_edges/series_components/shunt_branches

    for retry in range(5):
        # ── Phase 1: Floating placement (引力场 SA) ────
        floating_coords = sa_place_floating_nodes(
            expanded["nodes"], expanded["series_components"],
            energy_fn=compute_energy_v4,                # 见 placement 文档
        )

        # ── Phase 2: Rigid RF (CP-SAT) ────────────────
        model = cp_model.CpModel()
        for e in expanded["rf_edges"]:
            add_length_constraint(model, e)
        for n in stepped_impedance_nodes(expanded):
            add_stepped_impedance(model, n, *adjacent_edges(n))
        for i, a in enumerate(expanded["rf_edges"]):
            for b in expanded["rf_edges"][i+1:]:
                add_pairwise_no_overlap(model, a, b)
        add_via_density(model, expanded["shunt_branches"], zones, 8)

        solver = cp_model.CpSolver()
        solver.parameters.num_workers = os.cpu_count()
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            adaptive_temperature(0.0, sa_T)             # 抬温度重试
            continue

        rigid_geom = extract_rf_geometry(solver, expanded["rf_edges"])

        # ── Phase 3: Flexible A* ─────────────────────
        flex_geom = route_flexible_edges(
            expanded["flex_edges"], rigid_geom, board_grid,
        )
        if flex_geom is None:
            adaptive_temperature(0.5, sa_T)             # 抬温度重试
            continue

        return assemble_result(rigid_geom, flex_geom, expanded["shunt_branches"])

    return {"status": "infeasible", "reason": "max retries exceeded"}
```

---

## 6. 关键数据结构

```python
from dataclasses import dataclass
from enum import Enum

class NodeType(Enum):
    # Terminal 类
    PAD               = "pad"
    GROUND_PLANE      = "ground_plane"
    # Logical node 类
    T_JUNCTION        = "t_junction"
    COMPONENT_PAD_JCT = "component_pad_junction"
    PAD_JUNCTION      = "pad_junction"
    STEPPED_IMPEDANCE = "stepped_impedance"
    FLOATING_SHUNT    = "floating_shunt_tap"

class RoutingClass(Enum):
    RF_CONSTRAINED = "rf_constrained"
    FLEXIBLE_PATH  = "flexible_path"

@dataclass
class Terminal:
    name: str
    type: NodeType
    x: float | None      # ground_plane 无坐标
    y: float | None
    component: str | None = None
    pad: str | None       = None

@dataclass
class Edge:
    name: str
    type: str                                # microstrip/trace/lumped_*
    routing_class: RoutingClass | None       # lumped_* 无 routing_class
    connections: list[str]
    constraint: dict                         # {width, target_length(可为None)}
    geometry: dict | None = None             # {bend_style: ...}

@dataclass
class RoutingResult:
    edge_name: str
    segments: list[dict]                     # [{x1,y1,x2,y2,type:"H"|"V"}]
    total_length: float
    status: str                              # optimal/feasible/infeasible
```

---

## 7. 网格构建

```python
def build_grid(terminals, nodes, margin=0.1, resolution=1e-3):
    """覆盖所有 terminal 的 bounding box + margin；亚微米精度"""
    xs = [t["x"] for t in terminals.values() if "x" in t]
    ys = [t["y"] for t in terminals.values() if "y" in t]

    x_min, x_max = min(xs) - margin, max(xs) + margin
    y_min, y_max = min(ys) - margin, max(ys) + margin

    return Grid(
        x_origin=x_min, y_origin=y_min,
        width =int((x_max - x_min) / resolution),
        height=int((y_max - y_min) / resolution),
        resolution=resolution,
    )
```

---

## 8. 无解时的诊断

```python
def analyze_infeasibility(solver, model):
    """CP-SAT 原生支持冲突基分析"""
    logging.info(solver.ResponseStats())
    return {
        "num_conflicts": solver.NumConflicts(),
        "num_branches":  solver.NumBranches(),
        # 对 v4，进一步区分是 RF 阶段还是 A* 阶段失败
        "phase":         "cp_sat_rf" if model is not None else "astar_flex",
    }
```

A* 失败的常见原因（按优先级排查）：

1. RF 几何把板面切成了不连通区域 → 反馈到 SA 调整 floating node 位置。
2. shunt via 占据了灵活线必经之路 → 调整 `floating_shunt_tap` 的 `weight`。
3. 板框过小 → 增大 `margin` 或检查 terminal 坐标。

---

## 9. 复杂度估算（v4）

| 场景 | RF 段数 | flex 边数 | floating 节点 | CP-SAT 变量 | 总求解时间 |
|---|---|---|---|---|---|
| 简单（< 10 节点） | 5 | 3 | 0-2 | ~10³ | < 0.5 s |
| 中等（20-50 节点） | 15 | 10 | 5-10 | ~10⁴ | 2-8 s |
| 复杂（典型 Doherty） | 25-30 | 15-20 | 10-15 | ~10⁴ | 5-30 s |

> 对比 v3：复杂场景 v3 估算 30-120s，且因灵活线变量爆炸经常 timeout。v4 把灵活线挪出 CP-SAT 后规模直接降一个量级。

---

## 10. 总结

```
v4 完整算法 = 三阶段流水线

输入 YAML(hybrid_rf_graph)
    ↓ expand_topology
{terminals, nodes, rf_edges, flex_edges, series_comps, shunt_branches}
    ↓ Phase 1: 引力场 SA
floating_shunt_tap 节点坐标
    ↓ Phase 2: CP-SAT (rigid RF)
RF 几何 + stepped_impedance 偏移 + 无交叉
    ↓ Phase 3: A* (flexible)
灵活偏置网络绕障路径
    ↓
最终路径坐标输出
```

```python
# 核心伪代码（约 25 行）
expanded = expand_topology(yaml_input)
for retry in range(5):
    coords  = sa_place_floating(expanded["nodes"], energy_fn=compute_energy_v4)
    model   = cp_model.CpModel()
    add_length_constraints(model, expanded["rf_edges"])
    add_no_overlap_all(model, expanded["rf_edges"])
    add_stepped_impedance_offsets(model, expanded["nodes"])
    add_via_density(model, expanded["shunt_branches"])
    if solver.Solve(model) not in OK: continue
    rigid = extract_rf_geometry(solver)
    flex  = astar_route_all(expanded["flex_edges"], obstacles=rigid)
    if flex is None: continue
    return assemble(rigid, flex, expanded["shunt_branches"])
```
