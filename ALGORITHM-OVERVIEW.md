# 功放PCB单层自动布局布线——总体算法方案 v2

> 覆盖完整流程：布局（Placement）→ 布线（Routing）→ 后处理
>
> 场景：
> - 固定尺寸微带线 + 灵活走线
> - **固定路由边（预布线）**
> - **大量并联RLC shunt接地**
> - 高密度 RLC | 无交叉硬约束

---

## 1. 扩展输入格式

```yaml
nodes:
  Q1:     { type: "pad",              x: 10.0, y: 15.0 }
  L_match: { type: "inductor_pad",    x: 25.0, y: 20.0 }
  C_shunt: { type: "capacitor_pad",   x: 30.0, y: 18.0 }
  GND_plane: { type: "ground_plane", x: 50.0, y: 0.0 }

edges:
  # 固定微带线（只定起点终点，路径在CP-SAT中求解）
  main_line:
    connections: [Q1, L_match]
    type: "fixed_microstrip"
    width: 2.5
    target_length: 15.0
    fixed: true

  # 固定路由边（路径完全预确定，作为障碍物）⭐
  bias_feed:
    connections: [power_bus, L_match]
    type: "fixed_route"          # ← 预布线，路径已知
    width: 1.0
    path_coords: [[0, 10], [0, 20], [5, 20]]  # 完全固定路径

  # 灵活走线（CP-SAT求解）
  output_trace:
    connections: [L_match, C_shunt]
    type: "flexible"
    width: 1.5
    target_length: 12.0
    fixed: false

  # RLC shunt接地分支 ⭐
  shunt_ground_1:
    connections: [C_shunt, GND1_via]
    type: "ground_branch"         # ← 接地分支
    width: 0.8
    target_length: 5.0
    fixed: false

  # 接地过孔 ⭐
  gnd_via_1:
    connections: [GND1_via, GND_plane]
    type: "via_to_ground"        # ← 接地过孔
    diameter: 0.5
    fixed: false
```

---

## 2. 核心问题：布局与布线必须协同

### 2.1 顺序 Pipeline 的根本缺陷

| 缺陷 | 具体表现 | 影响 |
|------|---------|------|
| HPWL 误导 | Placement 用 HPWL 估算线长，实际路由是 HPWL 的 1.5~3 倍 | Placement 基于错误假设优化 |
| Infeasible 无解 | Placement 不知道 CP-SAT 约束强度，输出导致 Routing 无可行解 | 强制 Ripup，从头重来 |
| 锚定效应 | 固定微带线锚点无法调整 | 全局次优 |
| 热拥塞脱节 | SA 热惩罚不考虑走线密度 | 热点区域走线密集，直接拥塞 |

### 2.2 联合 CP-SAT 为什么不可行

约束耦合是非线性的（器件坐标 → 边端点 → 网格距离 → NoOverlap），CP-SAT 处理效率急剧下降。

---

## 3. 最优方案：迭代协同 v2（增强版）

### 3.1 算法流程

```
┌─────────────────────────────────────────────────────────────┐
│                      迭代循环（最多5次）                      │
│                                                             │
│  ┌────────────────┐                                        │
│  │ Placement SA   │  E = α·HPWL + β·C_cross               │
│  │ 增强能量函数    │      + γ·C_boundary + δ·C_thermal    │
│  └───────┬────────┘      + ε·C_congestion                 │
│          │                + ζ·C_ground_congestion  ⭐    │
│          │                                              │
│          ▼                                              │
│  ┌────────────────┐   ┌─────────────────────────────┐    │
│  │ Routing CP-SAT │ → │ 1. AddCircuit（连通性）     │    │
│  │ 增强约束        │   │ 2. AddNoOverlap（无交叉）   │    │
│  └───────┬────────┘   │ 3. AddCumulative（过孔密度）│    │
│          │             │    含星型接地板 ⭐          │    │
│          │             │ 4. 固定路由边→障碍集合 ⭐   │    │
│          │             └─────────────────────────────┘    │
│          │                                              │
│    ┌─────┴─────┐                                        │
│    ↓           ↓                                         │
│  成功        失败（infeasible）                           │
│    │           │                                         │
│    │     提取冲突约束 → 反哺能量函数                        │
│    │           │                                         │
│    ↓           ↓                                         │
│  收敛判断    回到 Placement（热启动）                       │
│    │                                              │
│    └───────────────────────→ 输出结果                   │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 增强能量函数（Placement）

```
E = α·HPWL + β·C_cross + γ·C_boundary + δ·C_thermal + ε·C_congestion + ζ·C_ground_congestion
                                                                              ↑
                                                                        新增：接地拥塞
```

**新增项详解**：

```python
def compute_ground_congestion_penalty(placement, ground_branches, grid):
    """
    接地拥塞惩罚：统计每个接地区域的接地过孔密度
    密度越高，惩罚越大
    """
    zone_via_count = {}

    for gb in ground_branches:
        via_pos = get_via_position(gb.end)
        zone = get_zone(via_pos, grid)
        zone_via_count[zone] = zone_via_count.get(zone, 0) + 1

    penalty = 0.0
    for zone, count in zone_via_count.items():
        capacity = zone.via_capacity  # 区域容量上限
        if count > capacity:
            penalty += (count - capacity) * GROUND_CONGESTION_WEIGHT

    return penalty
```

---

## 4. Stage 1：布局（Placement）— 模拟退火 + 热启动

```python
def placement_v2(
    netlist: dict,
    initial_placement: dict = None,
    congestion_map: dict = None,
    ground_congestion_map: dict = None,
) -> dict:
    """
    增强版SA布局：支持拥塞反哺 + 接地拥塞反哺
    """
    placement = initial_placement or random_valid_placement(netlist)
    energy = compute_energy(
        placement, netlist,
        congestion_map=congestion_map,
        ground_congestion_map=ground_congestion_map,
    )

    T = init_temp
    while T > 1.0 and not converged:
        new_placement = placement.copy()
        device = random.choice(flexible_devices)
        new_placement[device] = random_neighbor_position(device)

        if not is_valid(new_placement):
            T *= cool_rate
            continue

        new_energy = compute_energy(
            new_placement, netlist,
            congestion_map=congestion_map,
            ground_congestion_map=ground_congestion_map,
        )
        delta_E = new_energy - energy

        if delta_E < 0 or random.random() < exp(-delta_E / T):
            placement = new_placement
            energy = new_energy

        T *= cool_rate

    return placement
```

---

## 5. Stage 2：布线（Routing）— CP-SAT 增强约束

### 5.1 边类型与处理方式

| 边类型 | CP-SAT 处理 |
|--------|------------|
| `fixed_microstrip` | `Var.SetValue(1)`，不参与求解 |
| `fixed_route` | 转为 `occupied_cells` 集合，灵活边禁止进入 |
| `flexible` | 正常 CP-SAT 求解 |
| `ground_branch` | 与灵活走线同等处理，**含 NoOverlap** |
| `via_to_ground` | 计入 `AddCumulative` 容量约束 |

### 5.2 固定路由边建模（预布线障碍）⭐

```python
def build_fixed_obstacle_map(fixed_routing_edges: list, grid: Grid) -> set:
    """
    将所有固定路由边转为占用的网格单元集合
    返回: {(gx, gy), ...} 被固定边占据的网格单元
    """
    occupied = set()
    for edge in fixed_routing_edges:
        path = compute_full_path(edge)  # 预确定路径
        for x, y in path:
            # 线有宽度，向两侧扩展
            for dx in range(-edge.width, edge.width + 1):
                for dy in range(-edge.height, edge.height + 1):
                    gx = int((x + dx) / grid.resolution)
                    gy = int((y + dy) / grid.resolution)
                    occupied.add((gx, gy))
    return occupied


def add_fixed_route_constraints(model, occupied: set, x: dict):
    """
    对每条灵活边，约束其不能使用被固定路由边占据的网格单元
    """
    for (gx, gy, dir, edge_name), var in x.items():
        if (gx, gy) in occupied:
            var.SetValue(0)  # 该网格边不可用，强制绕行
```

### 5.3 接地分支与信号线的无交叉约束 ⭐

```python
def add_no_cross_constraints_all(model, all_edges, x, grid):
    """
    统一处理所有边的无交叉约束：
    - 灵活边之间
    - 灵活边与接地分支之间
    - 灵活边与固定路由边之间
    - 接地分支与信号走线之间
    """
    # 分类边
    flex_edges = [e for e in all_edges if e.type == "flexible"]
    ground_branches = [e for e in all_edges if e.type == "ground_branch"]
    fixed_routes = [e for e in all_edges if e.type == "fixed_route"]

    # 所有需要无交叉约束的边对
    all_constrained = flex_edges + ground_branches

    for i, e_a in enumerate(all_constrained):
        for e_b in all_constrained[i+1:]:
            add_pairwise_no_overlap(model, e_a, e_b, x, grid)

    # 固定路由边与灵活边的无交叉（用包围盒）
    for flex in flex_edges:
        for fixed in fixed_routes:
            add_bbox_no_overlap_with_fixed(model, flex, fixed, x)


def add_pairwise_no_overlap(model, e_a, e_b, x, grid):
    """
    两边之间的无交叉：x方向分离 OR y方向分离
    """
    bbox_a = get_path_bbox(e_a, x)
    bbox_b = get_path_bbox(e_b, x)

    x_sep = model.NewBoolVar("")
    y_sep = model.NewBoolVar("")

    model.Add(bbox_a.max_x + e_a.width/2 <= bbox_b.min_x - e_b.width/2).OnlyEnforceIf(x_sep)
    model.Add(bbox_b.max_x + e_b.width/2 <= bbox_a.min_x - e_a.width/2).OnlyEnforceIf(x_sep)

    model.Add(bbox_a.max_y + e_a.width/2 <= bbox_b.min_y - e_b.width/2).OnlyEnforceIf(y_sep)
    model.Add(bbox_b.max_y + e_b.width/2 <= bbox_a.min_y - e_a.height/2).OnlyEnforceIf(y_sep)

    model.AddBoolOr([x_sep, y_sep])
```

### 5.4 星型接地板约束（接地孔密度）⭐

```python
def add_star_ground_plane_constraints(model, ground_branches, grid_zones):
    """
    星型接地板：所有接地分支汇聚到同一个接地板区域
    需要单独建模其过孔密度约束
    """
    # 找到星型接地中心所在的zone
    star_center_zone = find_star_center_zone(ground_branches)

    # 该zone的接地过孔变量
    via_vars = []
    for gb in ground_branches:
        via_pos = get_via_position(gb.end)
        if star_center_zone.contains(via_pos):
            var = model.NewBoolVar(f"via_{gb.name}")
            via_vars.append(var)

    # 星型接地板的容量 = 该区域可用过孔数量（物理限制）
    max_vias_in_star_zone = star_center_zone.via_capacity

    model.AddCumulative(via_vars, [1]*len(via_vars), max_vias_in_star_zone)
```

### 5.5 典型Doherty功放的量化分析

| 参数 | 典型值 |
|------|--------|
| RLC shunt总数 | 23-42 个 |
| 接地过孔直径 | 0.5 mm（内径0.3mm） |
| 过孔含clearance占面积 | ~1.6 mm² |
| 40个接地孔总占面积 | 64 mm² |
| 接地区域可用面积 | 400 mm²（20×20mm区域） |
| **占可用面积比例** | **16%** |
| 4×4区域分解，每子区域 | 100 mm² |
| 每子区域平均接地孔 | ~2.5 个 |

**结论**：典型Doherty场景的接地孔密度完全在CP-SAT可控范围内。

---

## 6. Stage 3：后处理

| 步骤 | 作用 |
|------|------|
| mitered 弯角补偿 | 90°/45° 转弯处切除一角，补偿不连续性 |
| 泪滴（Teardrop）过渡 | 焊盘与走线渐变过渡，减小应力集中 |
| DRC 检查 | 最小线宽 0.1mm / 最小间距 0.1mm / 最小过孔直径 0.3mm |

---

## 7. 完整数据流

```
电路网表（YAML，含fixed_route/ground_branch/via_to_ground）
       │
       ▼
┌──────────────────────────────────────────────┐
│  迭代循环初始化    max_iterations = 5         │
└──────────────┬───────────────────────────────┘
               │
               │  ┌────────────────────────────────┐
               │  │ 迭代 N (N ≤ 5)                  │
               │  │                                │
               │  │  ┌──────────────────┐          │
               │  │  │ Placement SA      │          │
               │  │  │ 增强能量函数      │          │
               │  │  │ C_ground_congestion ← NEW │  │
               │  │  └───────┬──────────┘          │
               │  │          │                     │
               │  │          ▼                     │
               │  │  ┌──────────────────┐          │
               │  │  │ CP-SAT Routing   │          │
               │  │  │ 增强约束          │          │
               │  │  │ ·固定路由边→障碍 │          │
               │  │  │ ·接地分支NoOverlap│          │
               │  │  │ ·星型接地板Cumul.│          │
               │  │  └───────┬──────────┘          │
               │  │          │                     │
               │  │    ┌─────┴─────┐             │
               │  │    ↓           ↓             │
               │  │  成功        失败              │
               │  │    │           │             │
               │  │    │     提取冲突约束         │
               │  │    │       ↓                │
               │  │    │  ground_congestion_map │
               │  │    │       ↓                │
               │  │    │  回到 Placement         │
               │  │    ↓                       │
               │  │  收敛判断                   │
               │  │    │                       │
               │  │    └───────────┘           │
               │  │                                │
               │  └────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│  后处理    mitered + 泪滴 + DRC              │
└──────────────────────────────────────────────┘
               │
               ▼
         Gerber/输出文件
```

---

## 8. 复杂度与收敛

| 场景 | 器件数 | 灵活边数 | 接地分支 | 迭代次数 | 总求解时间 |
|------|--------|---------|---------|---------|---------|
| 简单 | <10 | 3 | 5 | 1-2 | <0.5s |
| 中等 | 10-50 | 10 | 20 | 2-3 | 2-5s |
| **复杂（Doherty）** | 50-200 | 30 | **40** | 3-5 | 30-120s |
| 超复杂 | >200 | >30 | >40 | — | 需区域分解 |

**典型Doherty功放（23-42个接地分支）**：变量规模约10⁶，CP-SAT可在30s内求解，增强方案完全可行。

---

## 9. 文件索引

| 文件 | 内容 |
|------|------|
| `ALGORITHM-OVERVIEW.md` | **总体架构 v2**（本文档）|
| `concepts/placement-problem-formulation.md` | 布局数学建模（SA、能量函数）|
| `concepts/routing-algorithm-comparison.md` | 布线 CP-SAT 详细实现 |
| `concepts/microstrip-topology-matching.md` | 微带线拓扑类型、mitered 补偿 |
