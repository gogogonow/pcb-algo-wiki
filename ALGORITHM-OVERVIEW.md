# 功放PCB单层自动布局布线——总体算法方案

> 覆盖完整流程：布局（Placement）→ 布线（Routing）→ 后处理
>
> 场景：固定尺寸微带线 + 灵活走线 | 高密度 RLC shunt | 无交叉硬约束

---

## 1. 系统架构

```
输入：电路网表（YAML）
        │
        ▼
┌─────────────────────────────────────────┐
│          Stage 1: 布局（Placement）      │
│                                         │
│   模拟退火 + HPWL + 禁入区域约束          │
│   输出：每个器件的 (x, y) 坐标           │
└────────────────┬────────────────────────┘
                 │ 器件坐标
                 ▼
┌─────────────────────────────────────────┐
│          Stage 2: 布线（Routing）        │
│                                         │
│   CP-SAT 单一模型 + AddNoOverlap         │
│   输出：每条边的 (x,y) 路径坐标           │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│       Stage 3: 后处理（Post-Processing） │
│                                         │
│   弯角mitered补偿 + 泪滴过渡 + DRC检查    │
└─────────────────────────────────────────┘
```

**为什么分两阶段而不是联合优化？**

联合优化（placement + routing 联合求解）规模太大，目前没有求解器能实时完成。业界标准做法是解耦：先放置器件，再布线。Placement 决定"在哪"，Routing 决定"怎么走"。

---

## 2. Stage 1：布局（Placement）

### 2.1 问题定义

已知：
- 功率管位置（**固定**）
- 匹配网络器件初始位置（**可移动**，但宽高已知）
- 板框尺寸（x_max, y_max）
- 禁入区域（散热器、装配孔、过孔禁止区）

求解：每个可移动器件的 (x, y) 坐标，使总成本最小。

### 2.2 成本函数

```
E = α·HPWL + β·C_cross + γ·C_boundary + δ·C_thermal
```

| 项 | 含义 |
|----|------|
| HPWL | 半周长线长（电气性能指标） |
| C_cross | 预估交叉惩罚（布局阶段的近似） |
| C_boundary | 器件出界惩罚 |
| C_thermal | 热感知惩罚（大功率器件相互远离） |

### 2.3 模拟退火算法

```python
def simulated_annealing_placement(netlist, init_temp=10000, cool_rate=0.9995):
    # 初始布局：随机放置（避开固定器件和禁入区）
    placement = random_valid_placement(netlist)
    energy = compute_energy(placement)

    T = init_temp
    while T > 1.0:
        # 随机扰动：一个器件移动 Δx, Δy
        new_placement = placement.copy()
        device = random.choice(flexible_devices)
        new_placement[device] = random_neighbor_position(device)

        # 检查硬约束（边界、禁入区、最小间距）
        if not is_valid(new_placement):
            T *= cool_rate
            continue

        new_energy = compute_energy(new_placement)
        delta_E = new_energy - energy

        # Metropolis 准则
        if delta_E < 0 or random.random() < exp(-delta_E / T):
            placement = new_placement
            energy = new_energy

        T *= cool_rate

    return placement
```

### 2.4 关键约束

| 约束 | 处理方式 |
|------|---------|
| 固定器件位置 | 不可移动，参与碰撞检测 |
| 板框边界 | 硬约束，超出则拒绝 |
| 禁入区域 | 硬约束，不可覆盖 |
| 器件最小间距 | 硬约束（电气安全距离） |
| 热感知 | 软惩罚项，加入能量函数 |

### 2.5 布局输出

```yaml
placement:
  Q1:      { x: 10.0, y: 15.0, type: "power_transistor", fixed: true }
  L_match: { x: 25.0, y: 20.0, type: "inductor" }
  C_shunt: { x: 30.0, y: 18.0, type: "capacitor" }
  R_bias:  { x: 35.0, y: 25.0, type: "resistor" }
  ...
```

---

## 3. Stage 2：布线（Routing）— CP-SAT 单一模型

> 这是整个系统的核心创新：不需要分阶段，一个 CP-SAT 模型涵盖所有约束。

### 3.1 输入

```yaml
nodes:
  Q1_d:    { type: "pad",         x: 10.0, y: 15.0 }  # 功率管漏极
  L1_pad:  { type: "pad",         x: 25.0, y: 20.0 }  # 电感位置
  C1_pad:  { type: "pad",         x: 30.0, y: 18.0 }  # shunt电容

edges:
  tl_main:              # 固定微带线（预布线）
    connections: [Q1_d, L1_pad]
    type: "microstrip"
    width: 2.5           # mm（特性阻抗50Ω决定）
    target_length: 15.0
    fixed: true          # 布局阶段确定，不参与求解

  shunt_trace:           # 灵活走线（需CP-SAT求解）
    connections: [L1_pad, C1_pad]
    type: "microstrip"
    width: 1.2
    target_length: 8.0
    fixed: false
```

### 3.2 网格离散化

```python
GRID_RESOLUTION = 0.001  # 1 μm，亚微米精度

class Grid:
    def __init__(self, x_min, y_min, x_max, y_max, resolution=0.001):
        self.x_min = x_min
        self.y_min = y_min
        self.width  = int((x_max - x_min) / resolution)
        self.height = int((y_max - y_min) / resolution)

    def is_inside(self, x, y):
        gx = int((x - self.x_min) / self.resolution)
        gy = int((y - self.y_min) / self.resolution)
        return 0 <= gx < self.width and 0 <= gy < self.height
```

### 3.3 CP-SAT 建模（完整伪代码）

```python
from ortools.sat.python import cp_model

def solve_routing(topology: dict) -> dict:
    model = cp_model.CpModel()

    # ── 变量 ──────────────────────────────────────────────
    # x[u,v,k] = 1 表示边 k 使用了网格边 (u→v)
    # 维度：grid_nodes × grid_nodes × n_edges（稀疏建模）
    x = {}
    for edge in flexible_edges:
        for gx in range(grid.width):
            for gy in range(grid.height):
                for dir in ["H", "V"]:          # H=水平, V=垂直
                    x[gx, gy, dir, edge.name] = model.NewBoolVar("")

    # ── 约束1: 连通性（每条灵活边是连通路径） ───────────
    # AddCircuit: 恰好形成一个环（含虚拟终点→起点边）
    for edge in flexible_edges:
        arcs = []
        for gx, gy, dir, name in x.keys():
            if name != edge.name:
                continue
            tail = (gx, gy)
            head = (gx+1, gy) if dir == "H" else (gx, gy+1)
            arcs.append((tail, head, x[gx, gy, dir, name]))

        # 虚拟边：终点→起点（闭合电路）
        virtual = model.NewBoolVar(f"virt_{edge.name}")
        arcs.append((edge.end_node, edge.start_node, virtual))
        model.AddCircuit(arcs)

    # ── 约束2: 目标长度 ─────────────────────────────────
    # sum(length_of_edge × x) ≈ target_length (±5%)
    for edge in flexible_edges:
        total_len = sum(
            GRID_RESOLUTION * x[gx, gy, dir, edge.name]
            for gx, gy, dir, name in x.keys()
            if name == edge.name
        )
        tol = edge.target_length * 0.05
        model.Add(total_len >= edge.target_length - tol)
        model.Add(total_len <= edge.target_length + tol)

    # ── 约束3: 无交叉（核心） ───────────────────────────
    # AddNoOverlap: 所有灵活边的路径矩形两两不重叠
    for i, e_a in enumerate(flexible_edges):
        for e_b in flexible_edges[i+1:]:
            # 获取两条边的路径包围盒（动态变量）
            bbox_a = get_path_bbox(e_a, x, grid)
            bbox_b = get_path_bbox(e_b, x, grid)

            # disjunction: x不相交 OR y不相交
            x_sep = model.NewBoolVar("")
            y_sep = model.NewBoolVar("")

            # x 方向分离
            model.Add(bbox_a.max_x + e_a.width/2 <= bbox_b.min_x - e_b.width/2).OnlyEnforceIf(x_sep)
            model.Add(bbox_b.max_x + e_b.width/2 <= bbox_a.min_x - e_a.width/2).OnlyEnforceIf(x_sep)

            # y 方向分离
            model.Add(bbox_a.max_y + e_a.width/2 <= bbox_b.min_y - e_b.width/2).OnlyEnforceIf(y_sep)
            model.Add(bbox_b.max_y + e_b.width/2 <= bbox_a.min_y - e_a.height/2).OnlyEnforceIf(y_sep)

            # 强制至少一个方向分离
            model.AddBoolOr([x_sep, y_sep])

    # ── 约束4: 过孔密度 ─────────────────────────────────
    # AddCumulative: 每个网格区域的过孔总数 ≤ 容量上限
    zones = partition_grid(grid, n_zones=4)  # 4×4 = 16 区域
    for zone in zones:
        via_vars = collect_via_vars_in_zone(zone, x, nodes)
        model.AddCumulative(via_vars, [1]*len(via_vars), max_vias_per_zone=8)

    # ── 约束5: 固定微带线（预布线） ────────────────────
    for edge in fixed_edges:
        # 直接设真值，不参与求解
        path = compute_straight_path(edge, nodes, grid)
        for gx, gy, dir in path:
            x[gx, gy, dir, edge.name].SetValue(1)

    # ── 求解 ────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = os.cpu_count()   # 多核并行
    solver.parameters.log_progression = True

    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return extract_routes(solver, x, topology, grid)
    else:
        return {"status": "infeasible", "conflicts": solver.NumConflicts()}
```

### 3.4 关键 CP-SAT 原语

| 约束 | CP-SAT 原语 | 作用 |
|------|------------|------|
| 路径连通 | `AddCircuit(arcs)` | 保证起点→终点形成连通路径 |
| 无交叉 | `AddNoOverlap` + `AddBoolOr` | 两条边至少一个方向分离 |
| 目标长度 | `AddLinearExpression` | 路径总长 ≈ target_length |
| 过孔密度 | `AddCumulative` | 区域过孔数 ≤ 上限 |
| 固定微带 | `Var.SetValue(1)` | 预布线直接固定 |

---

## 4. Stage 3：后处理

### 4.1 弯角 mitered 补偿

微带线转弯处（90°/45°）需要切除一角以补偿不连续性：

```
     │         →
     │    →→→→
─────×    →→→→→→→
     │
  切除区域: d = W × mitered_factor × sin(θ/2)
  mitered_factor ∈ [0.5, 1.0]
```

### 4.2 泪滴（Teardrop）过渡

焊盘与走线连接处渐变过渡，减小应力集中：

```
     ╱╲
   ╱────╲        渐变宽度: W_pad → W_trace
  ╱──────╲
```

### 4.3 DRC 检查

| 检查项 | 阈值 |
|--------|------|
| 最小线宽 | 0.1 mm |
| 最小间距 | 0.1 mm |
| 最小过孔 | 直径 0.3 mm |
| 最小弯角半径 | 1× 线宽 |

---

## 5. 完整数据流

```
电路网表
    │
    ▼
┌──────────────┐
│ YAML 解析     │ → 节点列表、边列表、固定微带线
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 布局 (SA)    │ → 器件 (x,y) 坐标
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 网格构建     │ → 离散化网格 (1μm 分辨率)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ CP-SAT 建模  │ → 变量 + 约束
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 求解 (<1s)   │ → 路径坐标
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 后处理       │ → mitered + 泪滴 + DRC
└──────────────┘
       │
       ▼
Gerber/输出文件
```

---

## 6. 复杂度与规模

| 场景 | 器件数 | 灵活边数 | 网格规模 | 求解时间 |
|------|--------|---------|---------|---------|
| 简单 | < 10 | 3 | 100×100 | < 0.1s |
| 中等 | 10-50 | 10 | 200×200 | 0.5-2s |
| 复杂 | 50-200 | 30 | 500×500 | 10-60s |
| 超复杂 | > 200 | > 30 | — | 需区域分解 |

**区域分解**（只在超复杂场景启用）：
```
板子划分为 NxN 区域 → 每区域独立 CP-SAT → 接口变量协调
```

---

## 7. 文件索引

| 文件 | 内容 |
|------|------|
| `ALGORITHM-OVERVIEW.md` | **总体架构**（本文档）|
| `concepts/placement-problem-formulation.md` | 布局数学建模（SA、能量函数、HPWL）|
| `concepts/routing-algorithm-comparison.md` | 布线 CP-SAT 详细实现 |
| `concepts/microstrip-topology-matching.md` | 微带线拓扑类型、mitered 补偿 |
