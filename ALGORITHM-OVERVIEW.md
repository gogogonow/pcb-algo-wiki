# 功放PCB单层自动布局布线——总体算法方案

> 覆盖完整流程：布局（Placement）→ 布线（Routing）→ 后处理
>
> 场景：固定尺寸微带线 + 灵活走线 | 高密度 RLC shunt | 无交叉硬约束

---

## 1. 核心问题：布局与布线必须协同

### 1.1 顺序 Pipeline 的根本缺陷

```
Placement (SA) ──→ Routing (CP-SAT) ──→ Post-processing
   ❌ HPWL 误导       ❌ 拥塞不可知      ❌ 全局次优
   ❌ 锚定效应       ❌ infeasible 无解
```

| 缺陷 | 具体表现 | 影响 |
|------|---------|------|
| HPWL 误导 | Placement 用 HPWL 估算线长，但实际路由是 HPWL 的 1.5~3 倍（绕路、对齐网格） | Placement 优化的"最优"其实是错误假设下次优 |
| Infeasible 无解 | Placement 不知道 CP-SAT 的约束强度，输出导致 Routing 无可行解 | 强制 Ripup，从头重来 |
| 锚定效应 | 固定微带线的锚点由工程师经验设定，Placement 无法调整 | 全局次优 |
| 热拥塞脱节 | SA 热惩罚只考虑器件距离，不考虑走线密度 | 热点区域走线密集，直接拥塞 |

### 1.2 联合 CP-SAT 为什么不可行

联合优化（Placement + Routing 用一个 CP-SAT 模型）的变量规模：

```
Placement: N_devices × 2 坐标变量
Routing:   N_edges × N_grid × 2 方向变量
总计:      ~10^6 变量（可行）

但约束耦合是非线性的：
  器件坐标 (x_i, y_i) → 边起点/终点位置 → 网格距离计算
  → 绕路长度 → NoOverlap 约束有效性

这形成高度耦合的非线性约束图，CP-SAT 的 SAT 求解技术
在处理这种耦合时效率急剧下降。
```

**结论**：联合 CP-SAT 理论上可探索，但工程实现成本极高，不推荐。

---

## 2. 最优方案：迭代协同（Iterative Co-design）

### 2.1 算法流程

```
┌─────────────────────────────────────────────────────────┐
│                      迭代循环                            │
│                                                         │
│  ┌──────────────┐     ┌────────────────┐               │
│  │ Placement SA │────→│ Routing CP-SAT │               │
│  │  (热启动)     │     │                │               │
│  └──────┬───────┘     └───────┬────────┘               │
│         │                      │                        │
│         │              ┌───────┴────────┐               │
│         │              │  结果评估       │               │
│         │              └───┬────────┬───┘               │
│         │                  │        │                   │
│         │           成功    │        │  失败（infeasible）│
│         │                  ▼        ▼                   │
│         │              收敛判断    提取冲突约束            │
│         │                  │        │                   │
│         │                  │   反哺到能量函数              │
│         │                  │        │                   │
│         └──────────────────┴────────┘                   │
│                         │                                │
│                    收敛? ──→ 输出结果                    │
└─────────────────────────────────────────────────────────┘
```

### 2.2 关键机制

**热启动（Hot Start）**：
- 每次迭代的 Placement 从上次结果继续，不需要从头随机
- 冲突信息作为额外的惩罚项加入能量函数，加速收敛

**拥塞反哺（ Congestion Feedback）**：
- CP-SAT 求解失败时，用 `server.SufficientAssumptionForProblemContradiction()` 提取最小冲突集
- 冲突区域在 Placement 能量函数里加入惩罚项（拥塞惩罚 = 该区域器件数量 × 权重）

**收敛判断**：
- 连续 3 次迭代，Routing 成功率 > 95%
- 或迭代次数达到上限（默认 5 次，防止无限循环）

### 2.3 能量函数（Placement）

```
E = α·HPWL + β·C_cross + γ·C_boundary + δ·C_thermal + ε·C_congestion
```

| 项 | 含义 |
|----|------|
| HPWL | 半周长线长（电气性能） |
| C_cross | 预估交叉惩罚 |
| C_boundary | 出界惩罚 |
| C_thermal | 热感知（大功率器件相互远离） |
| C_congestion | **拥塞反哺惩罚**（来自上次 Routing 失败信息）⭐ |

---

## 3. Stage 详解

### 3.1 Stage 1：布局（Placement）— 模拟退火 + 热启动

```python
def placement_with_congestion_feedback(
    netlist: dict,
    initial_placement: dict = None,
    congestion_map: dict = None,
) -> dict:
    """
    热启动 SA 布局，支持拥塞反哺
    """
    placement = initial_placement or random_valid_placement(netlist)
    energy = compute_energy(placement, congestion_map)

    T = init_temp
    while T > 1.0 and not converged:
        new_placement = placement.copy()
        device = random.choice(flexible_devices)
        new_placement[device] = random_neighbor_position(device)

        if not is_valid(new_placement):
            T *= cool_rate
            continue

        new_energy = compute_energy(new_placement, congestion_map)
        delta_E = new_energy - energy

        if delta_E < 0 or random.random() < exp(-delta_E / T):
            placement = new_placement
            energy = new_energy

        T *= cool_rate

    return placement
```

**拥塞惩罚项的实现**：

```python
def compute_energy(placement: dict, congestion_map: dict = None) -> float:
    hpwl = compute_hpwl(placement, netlist)
    cross = estimate_crossings(placement)        # 布局阶段近似
    boundary = compute_boundary_penalty(placement)
    thermal = compute_thermal_penalty(placement)
    congestion = 0.0

    if congestion_map:
        # congestion_map: {(x_zone, y_zone): penalty}
        for device, pos in placement.items():
            zone = get_zone(pos, congestion_map.grid)
            congestion += congestion_map.get(zone, 0.0)

    return (α * hpwl + β * cross + γ * boundary
            + δ * thermal + ε * congestion)
```

### 3.2 Stage 2：布线（Routing）— CP-SAT 单一模型

> 与之前方案相同，此处不再重复完整代码。详见 `concepts/routing-algorithm-comparison.md`

**CP-SAT 关键约束**：

| 约束 | CP-SAT 原语 | 作用 |
|------|------------|------|
| 无交叉 | `AddNoOverlap` + `AddBoolOr` | 两边至少一维分离 |
| 路径连通 | `AddCircuit` | 起点→终点连通路径 |
| 目标长度 | `AddLinearExpression` | 路径总长 ≈ target_length |
| 过孔密度 | `AddCumulative` | 区域过孔数 ≤ 上限 |
| 固定微带线 | `Var.SetValue(1)` | 预布线固定 |

**拥塞信息提取**（CP-SAT 失败时）：

```python
def extract_congestion_info(model, solver) -> dict:
    """
    从失败的 CP-SAT 模型中提取冲突约束，生成拥塞地图
    """
    # 找出导致不可行的最小假设集
    assumptions = solver.SufficientAssumptionForProblemContradiction()
    # assumptions: 导致冲突的变量列表

    # 将冲突变量映射到网格区域
    congestion_map = {}
    for var in assumptions:
        zone = var_to_zone(var)  # 从变量名解析所属区域
        congestion_map[zone] = congestion_map.get(zone, 0) + 1

    return congestion_map
```

### 3.3 Stage 3：后处理

| 步骤 | 作用 |
|------|------|
| mitered 弯角补偿 | 90°/45° 转弯处切除一角，补偿不连续性 |
| 泪滴（Teardrop）过渡 | 焊盘与走线渐变过渡，减小应力集中 |
| DRC 检查 | 最小线宽 0.1mm / 最小间距 0.1mm / 最小过孔直径 0.3mm |

---

## 4. 完整数据流

```
电路网表（YAML）
       │
       ▼
┌──────────────────┐
│  迭代循环初始化    │  max_iterations = 5
└────────┬─────────┘
         │
         │  ┌──────────────────────────────────────┐
         │  │ 迭代 N (N ≤ 5)                        │
         │  │                                      │
         │  │  ┌────────────────┐                   │
         │  │  │ Placement SA  │ ← 热启动 +       │
         │  │  │ (拥塞惩罚项)   │   拥塞反哺       │
         │  │  └───────┬────────┘                   │
         │  │          │                            │
         │  │          ▼                            │
         │  │  ┌────────────────┐                   │
         │  │  │ CP-SAT Routing │                   │
         │  │  └───────┬────────┘                   │
         │  │          │                            │
         │  │    ┌─────┴─────┐                     │
         │  │    ↓           ↓                     │
         │  │  成功        失败（infeasible）        │
         │  │    │           │                     │
         │  │    │     提取冲突约束 ──→ 拥塞地图     │
         │  │    │           │                     │
         │  │    ↓           ↓                     │
         │  │  收敛判断    回到 Placement           │
         │  │    │           │                     │
         │  │    └───────────┘                     │
         │  │                                      │
         │  └──────────────────────────────────────┘
         │
         ▼
┌──────────────────┐
│  后处理           │  mitered + 泪滴 + DRC
└────────┬─────────┘
         │
         ▼
   Gerber/输出文件
```

---

## 5. 复杂度与收敛

| 场景 | 器件数 | 灵活边数 | 迭代次数（预估）| 总求解时间 |
|------|--------|---------|--------------|-----------|
| 简单 | < 10 | 3 | 1-2 | < 0.5s |
| 中等 | 10-50 | 10 | 2-3 | 2-5s |
| 复杂 | 50-200 | 30 | 3-5 | 30-120s |
| 超复杂 | > 200 | > 30 | — | 需区域分解 |

**为什么迭代次数可控？**
- 热启动：每次 Placement 从上次结果继续，初始温度更低
- 拥塞惩罚项：直接告诉 SA "这些区域不能放器件"
- 最坏情况：5 次迭代后强制输出（即便不是全局最优）

---

## 6. 文件索引

| 文件 | 内容 |
|------|------|
| `ALGORITHM-OVERVIEW.md` | **总体架构**（本文档）|
| `concepts/placement-problem-formulation.md` | 布局数学建模（SA、能量函数）|
| `concepts/routing-algorithm-comparison.md` | 布线 CP-SAT 详细实现 |
| `concepts/microstrip-topology-matching.md` | 微带线拓扑类型、mitered 补偿 |
