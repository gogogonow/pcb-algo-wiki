# PCB 单层无交叉布线——CP-SAT 实现指南

> 场景：固定微带线 + 灵活走线 | 高密度 RLC shunt | 无交叉硬约束
>
> 目标：从输入 YAML 到可布线路径的完整算法流程

---

## 1. 输入格式

```yaml
# 节点定义
nodes:
  P1:     { type: "pad",         x: 0.0,  y: 0.0  }
  J1:     { type: "junction",    x: 10.0, y: 0.0  }
  SHUNT:  { type: "component_pad", x: 20.0, y: 0.0 }
  GND1:   { type: "ground",      x: 20.0, y: 5.0  }
  P2:     { type: "pad",         x: 30.0, y: 0.0  }

# 边定义
edges:
  main_line_1:
    connections: [P1, J1]
    type: "microstrip"
    width: 2.0          # mm
    target_length: 10.0 # mm
    fixed: true         # 预布线，不参与求解

  shunt_branch:
    connections: [J1, SHUNT]
    type: "microstrip"
    width: 1.0
    target_length: 8.0
    fixed: true

  output_line:
    connections: [SHUNT, P2]
    type: "microstrip"
    width: 1.5
    target_length: 12.0
    fixed: false       # 需CP-SAT求解
```

---

## 2. 几何建模：连续坐标 vs 栅格

**结论：连续坐标 + 网格搜索 = 最实用**

```python
class EdgeSegment:
    """灵活边的几何表示"""
    def __init__(self, edge_id, start, end, width):
        self.edge_id = edge_id
        self.start = start      # (x, y) 连续坐标
        self.end = end
        self.width = width

        # 包围盒（用于 NoOverlap 检测）
        self.bbox = self._compute_bbox()

    def _compute_bbox(self):
        xs = [self.start[0], self.end[0]]
        ys = [self.start[1], self.end[1]]
        return BoundingBox(
            min_x=min(xs), max_x=max(xs),
            min_y=min(ys), max_y=max(ys)
        )

    def to_rectangle(self):
        """转为 OR-Tools NoOverlap 矩形"""
        # 矩形表示：(start_x, start_y, width, height)
        return Rectangle(
            self.bbox.min_x, self.bbox.min_y,
            self.bbox.max_x - self.bbox.min_x + self.width,
            self.bbox.max_y - self.bbox.min_y + self.width
        )
```

**为什么不用纯栅格？**
- 栅格化引入量化误差（路径长度不精确）
- 目标长度约束要求精确的连续坐标
- 混合方案：先在连续空间建模，再离散化到足够细的网格（1um精度）

---

## 3. CP-SAT 建模：逐约束

### 3.1 变量定义

```python
from ortools.sat.python import cp_model

model = cp_model.CpModel()

# 网格分辨率：1 micrometer
GRID = 1e-3  # mm

# 节点离散化坐标
node_x = {n: round(pos["x"] / GRID) for n, pos in nodes.items()}
node_y = {n: round(pos["y"] / GRID) for n, pos in nodes.items()}

# 边变量：edge_id → 路径上的网格边集合
# path_vars[edge_id] = { (gx, gy, dir): bool_var }
path_vars = {}

for edge in edges.values():
    if edge["fixed"]:
        # 预布线：计算最短直线路径，固定为真
        fixed_path = straight_path(edge, node_x, node_y, GRID)
        for gx, gy, direction in fixed_path:
            var = model.NewBoolVar(f"fixed_{edge['name']}_{gx}_{gy}")
            var.SetValue(1)
        continue

    # 灵活边：为每条可能的网格边创建变量
    for gx in range(grid_width):
        for gy in range(grid_height):
            for dir in ["H", "V"]:  # 水平 or 垂直
                path_vars[(gx, gy, dir, edge["name"])] = \
                    model.NewBoolVar(f"p_{edge['name']}_{gx}_{gy}_{dir}")
```

### 3.2 无交叉约束（核心）

```python
def add_no_cross_constraints(model, flexible_edges, path_vars):
    """
    核心约束：所有灵活边的路径矩形不能重叠
    """

    # 方案A：矩形包围盒互斥（宽松但快速）
    for i, e_a in enumerate(flexible_edges):
        for e_b in flexible_edges[i+1:]:
            # 两边的包围盒必须不相交
            bbox_a = compute_bbox(e_a)
            bbox_b = compute_bbox(e_b)

            # 方法：若相交，至少一个方向错开
            # disjunction: (x区间不相交) OR (y区间不相交)
            x_overlap = model.NewBoolVar("x_overlap")
            y_overlap = model.NewBoolVar("y_overlap")

            # x 区间重叠检测
            model.Add(
                bbox_a.max_x + e_a.width/2 < bbox_b.min_x - e_b.width/2
            ).OnlyEnforceIf(x_overlap.Not())
            model.Add(
                bbox_b.max_x + e_b.width/2 < bbox_a.min_x - e_a.width/2
            ).OnlyEnforceIf(x_overlap.Not())

            # y 区间重叠检测
            model.Add(
                bbox_a.max_y + e_a.width/2 < bbox_b.min_y - e_b.width/2
            ).OnlyEnforceIf(y_overlap.Not())
            model.Add(
                bbox_b.max_y + e_b.width/2 < bbox_a.min_y - e_a.height/2
            ).OnlyEnforceIf(y_overlap.Not())

            # 不相交 = x不相交 OR y不相交
            model.AddBoolOr([x_overlap.Not(), y_overlap.Not()])


def compute_bbox(edge):
    """计算灵活边的包围盒（动态，由求解变量决定）"""
    # 包围盒端点也是变量（由起点终点决定）
    min_x = model.NewIntVar(0, grid_width, f"bbox_{edge}_min_x")
    max_x = model.NewIntVar(0, grid_width, f"bbox_{edge}_max_x")
    min_y = model.NewIntVar(0, grid_height, f"bbox_{edge}_min_y")
    max_y = model.NewIntVar(0, grid_height, f"bbox_{edge}_max_y")

    # 端点约束：从路径变量提取端点
    # ...（从 path_vars 汇总）
    return (min_x, max_x, min_y, max_y)
```

### 3.3 路径连通性（AddCircuit）

```python
def add_circuit_constraints(model, edge, grid, path_vars):
    """
    每条灵活走线必须是从起点到终点的连通路径
    AddCircuit 要求恰好形成一个环，我们把终点→起点加一条虚拟边即可
    """
    arcs = []

    for gx in range(grid.width):
        for gy in range(grid.height):
            for dir in ["H", "V"]:
                var = path_vars.get((gx, gy, dir, edge))
                if var is None:
                    continue

                # 水平边：(gx,gy) → (gx+1, gy)
                # 垂直边：(gx,gy) → (gx, gy+1)
                tail = (gx, gy)
                head = (gx+1, gy) if dir == "H" else (gx, gy+1)
                arcs.append((tail, head, var))

    # 添加虚拟边：终点 → 起点（使电路闭合）
    end_to_start = model.NewBoolVar("virtual_end_start")
    arcs.append((edge["end_node"], edge["start_node"], end_to_start))

    model.AddCircuit(arcs)
```

### 3.4 目标长度约束

```python
def add_length_constraints(model, edge, path_vars, target_length):
    """
    路径总长度 = target_length（允许 ±5% 松弛）
    """
    length = 0
    for (gx, gy, dir, e_name), var in path_vars.items():
        if e_name != edge["name"]:
            continue
        # 水平边长=GRID，垂直边长=GRID，对角线=GRID*sqrt(2)
        seg_len = GRID if dir in ["H", "V"] else GRID * 1.414
        length += seg_len * var

    tolerance = target_length * 0.05
    model.Add(length >= target_length - tolerance)
    model.Add(length <= target_length + tolerance)
```

### 3.5 过孔密度约束

```python
def add_via_density_constraints(model, nodes, grid_zones, max_vias_per_zone):
    """
    每个网格区域的过孔数量不超过容量
    """
    for zone in grid_zones:
        # 该区域内的所有潜在过孔位置
        via_vars = []
        for gx in range(zone.x_min, zone.x_max):
            for gy in range(zone.y_min, zone.y_max):
                for n in nodes_in_zone(zone, nodes):
                    var = model.NewBoolVar(f"via_{gx}_{gy}_{n}")
                    via_vars.append(var)

        # 过孔数量 = 经过该节点的灵活边数量
        # AddCumulative 约束总高度不超过区域容量
        model.AddCumulative(
            [var for var in via_vars],
            [1] * len(via_vars),
            max_vias_per_zone
        )
```

---

## 4. 完整求解流程

```python
def solve_routing(topology: dict) -> dict:
    model = cp_model.CpModel()

    # ── Step 1: 读取输入 ─────────────────────────────────
    nodes   = topology["nodes"]
    edges   = topology["edges"]
    fixed   = [e for e in edges.values() if e.get("fixed")]
    flex    = [e for e in edges.values() if not e.get("fixed")]

    # ── Step 2: 构建网格 ─────────────────────────────────
    # 覆盖所有节点的 bounding box + 10% margin
    grid = build_grid(nodes, margin=0.1)

    # ── Step 3: 变量创建 ─────────────────────────────────
    path_vars = {}
    for edge in flex:
        for gx in range(grid.width):
            for gy in range(grid.height):
                for dir in ["H", "V"]:
                    path_vars[(gx, gy, dir, edge["name"])] = \
                        model.NewBoolVar(f"p_{edge['name']}_{gx}_{gy}_{dir}")

    # ── Step 4: 约束1 — 连通性（每条灵活走线是路径） ──────
    for edge in flex:
        add_circuit_constraints(model, edge, grid, path_vars)

    # ── Step 5: 约束2 — 目标长度 ─────────────────────────
    for edge in flex:
        add_length_constraints(model, edge, path_vars, edge["target_length"])

    # ── Step 6: 约束3 — 无交叉（核心） ───────────────────
    add_no_cross_constraints(model, flex, path_vars)

    # ── Step 7: 约束4 — 过孔密度 ─────────────────────────
    grid_zones = partition_grid(grid, n_zones=4)  # 4x4 区域分解
    add_via_density_constraints(model, nodes, grid_zones, max_vias_per_zone=8)

    # ── Step 8: 固定微带线（预布线） ────────────────────
    for edge in fixed:
        route_fixed_edge(model, edge, nodes, path_vars, grid)

    # ── Step 9: 求解 ────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.log_progression = True
    solver.parameters.num_workers = os.cpu_count()  # 多核并行

    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return extract_routes(solver, path_vars, flex, fixed, grid)
    else:
        return {"status": "infeasible", "reason": analyze_infeasibility(model)}
```

---

## 5. 关键数据结构的 Python 实现

```python
from dataclasses import dataclass
from enum import Enum

class NodeType(Enum):
    PAD = "pad"
    JUNCTION = "junction"
    COMPONENT_PAD = "component_pad"
    GROUND = "ground"
    GROUND_REFERENCE = "ground_reference"

@dataclass
class Node:
    name: str
    type: NodeType
    x: float  # mm
    y: float

@dataclass
class Edge:
    name: str
    connections: list[str]  # [node_a, node_b]
    type: str               # "microstrip" | "via" | "bend"
    width: float            # mm
    target_length: float    # mm
    fixed: bool = False

@dataclass
class GridEdge:
    """网格上的有向边"""
    gx: int
    gy: int
    direction: str  # "H" | "V"
    physical_length: float  # mm

@dataclass
class RoutingResult:
    """求解结果"""
    edge_name: str
    segments: list[dict]  # [{"x1":0,"y1":0,"x2":10,"y2":0,"type":"H"}, ...]
    total_length: float
    status: str  # "optimal" | "feasible" | "infeasible"
```

---

## 6. 网格构建策略

```python
def build_grid(nodes: dict, margin: float = 0.1, resolution: float = 1e-3):
    """
    构建覆盖所有节点的网格

    resolution = 1mm / 1000 = 1um（亚微米精度）
    """
    xs = [n.x for n in nodes.values()]
    ys = [n.y for n in nodes.values()]

    x_min = min(xs) * (1 - margin)
    x_max = max(xs) * (1 + margin)
    y_min = min(ys) * (1 - margin)
    y_max = max(ys) * (1 + margin)

    width  = int((x_max - x_min) / resolution)
    height = int((y_max - y_min) / resolution)

    return Grid(
        x_origin=x_min, y_origin=y_min,
        width=width, height=height,
        resolution=resolution
    )
```

---

## 7. 无解时的诊断

```python
def analyze_infeasibility(model):
    """CP-SAT 原生支持不可行基分析"""
    solver = cp_model.CpSolver()
    # ... 求解失败后 ...
    logging.info(solver.ResponseStats())
    # 打印冲突的约束数量
    return {
        "num_conflicts": solver.NumConflicts(),
        "num_branches":  solver.NumBranches(),
    }
```

---

## 8. 复杂度估算

| 场景 | 灵活边数 | 网格规模 | 变量数估算 | 求解时间 |
|------|---------|---------|-----------|---------|
| 简单（<10节点） | 3 | 100×100 | ~10⁴ | < 0.1s |
| 中等（20-50节点） | 10 | 200×200 | ~10⁵ | 0.5-2s |
| 复杂（>50节点） | 30 | 500×500 | ~10⁶ | 10-60s |

**当变量数 > 10⁵ 时**，建议加一层区域分解（Zone Decomposition）：

```
高密度场景 → 将板子划分为 NxN 区域
→ 每个区域独立求解 CP-SAT
→ 区域边界用接口变量协调
```

这就是"分阶段"的真正价值所在——**只在必要时分解**。

---

## 9. 总结

**完整算法 = 三个步骤**：

```
输入YAML → CP-SAT建模 → 求解器 → 路径坐标输出
```

```python
# 核心伪代码（20行）
model = cp_model.CpModel()
x = new_bool_var_matrix(grids, edges)          # 路径变量
add_circuit_constraints(model, flex_edges, x)  # 路径连通
add_no_overlap_constraints(model, flex_edges)  # 无交叉 ⭐
add_length_constraints(model, flex_edges, x)    # 目标长度
solver.solve(model)                             # 求解
```
