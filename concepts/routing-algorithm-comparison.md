# 布线算法选型深度分析报告（v3 — CP-SAT 五阶段迭代架构版）

> 场景：固定微带线 + 灵活走线混合 | 高密度 RLC shunt | 无交叉硬约束
>
> 核心升级：用 CP-SAT 替换 ILP，配合 Ripup-Reroute 五阶段迭代架构

---

## 1. 研究历程与版本演进

### v1（初始版）
- **场景**：固定微带线 + 低密度RLC + 软约束
- **方案**：几何求解器 + FLUTE + 电弧后处理
- **问题**：高密度RLC场景下无交叉无法保证

### v2（复杂场景版）
- **场景**：固定微带线 + 灵活走线 + 高密度RLC + 无交叉硬约束
- **方案**：Zone ILP + Ripup-Reroute 迭代优化
- **改进**：硬约束保证，规模可控
- **问题**：ILP 求解速度慢，交叉约束编码困难

### v3（当前版）⭐
- **核心改变**：用 CP-SAT 替换 ILP 作为区域求解器
- **原因**：CP-SAT 的 AddNoOverlap/AddCumulative/AddCircuit 原生支持本场景所有约束，且速度快 5-100 倍

---

## 2. 场景定义

### 2.1 输入格式

```yaml
nodes:
  node_A: { type: "junction" }
  node_shunt_tap: { type: "component_pad_junction" }
  node_B: { type: "junction" }
  GND_REF: { type: "ground_reference" }

edges:
  tl_segment_part1:  # 固定微带线（RF主干）
    type: "microstrip"
    connections: [ "node_A", "node_shunt_tap" ]
    constraint:
      width: 1.0
      target_length: 4.0
      fixed: true  # 永不参与Ripup

  tl_segment_part2:  # 灵活走线
    type: "microstrip"
    connections: [ "node_shunt_tap", "node_B" ]
    constraint:
      width: 1.0
      target_length: 6.0
      fixed: false

  c_shunt_match:  # 高密度RLC shunt
    type: "lumped_capacitor"
    connections: [ "node_shunt_tap", "GND_REF" ]
    parameters:
      value_pF: 5.6
      package: "0402"

constraints:
  max_via_count: 50
  via_density_threshold: 2/mm²
  min_spacing: 0.15
  min_width: 0.3
```

### 2.2 场景三要素

| 要素 | 说明 |
|------|------|
| **混合线网** | 固定微带线（fixed=true）+ 灵活走线（fixed=false） |
| **高密度 RLC** | 大量 shunt 器件，拓扑分裂频繁 |
| **无交叉硬约束** | 交叉 = 0 是唯一可接受的结果 |

---

## 3. 推荐方案：五阶段迭代架构

```
┌─────────────────────────────────────────────────────────────────┐
│ Phase 1: 全局拓扑规划（FLUTE）                                    │
│   粗粒度路由骨架，估算全局拥塞                                     │
│   O(n log n) Steiner 树构建                                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Phase 2: 区域分解 + 动态 Net-Ordering                             │
│   固定微带线 → 动态障碍区域（永不参与 Ripup）                     │
│   RLC shunt → 按主干分组（每个区域独立求解）                      │
│   Net-Ordering：约束最严的线先布                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Phase 3: 区域内详细布线（CP-SAT）⭐                              │
│   AddNoOverlap: 无交叉硬约束                                     │
│   AddCumulative: 过孔密度约束                                    │
│   AddCircuit: 路径连续性约束                                     │
│   求解器：Google OR-Tools（完全开源，Apache 2.0）                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Phase 4: Ripup-Reroute 迭代优化                                 │
│   识别拥塞区域 → Ripup → CP-SAT重新求解                         │
│   固定线永远保护，不参与 ripup                                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Phase 5: 几何精化 + 后处理                                       │
│   梯度下降 + target_length滑动 + 弧形弯角 + DRC                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. CP-SAT 约束建模（核心）

### 4.1 为什么用 CP-SAT 替代 ILP？

| 维度 | ILP | CP-SAT |
|------|-----|--------|
| 交叉约束编码 | 需要大量二元变量，约束爆炸 | `AddNoOverlap` 原生支持 |
| 过孔密度约束 | 线性约束 + 辅助变量 | `AddCumulative` 原生支持 |
| 路径连续性 | 手动编码入度=出度 | `AddCircuit` 原生支持 |
| 求解速度 | 指数级（分支定界） | 快5-100倍 |
| 并行化 | 商业版才有 | 原生多核 |
| 开源 | SCIP学术免费/商业版付费 | **完全免费** |

### 4.2 无交叉约束

```python
from ortools.sat.python import cp_model

model = cp_model.CpModel()

# 决策变量：边是否被线网占用
edge_vars = {}
for net in nets:
    for edge in all_edges:
        edge_vars[(net.id, edge.id)] = model.NewBoolVar(f'e_{net.id}_{edge.id}')

# 约束：每条边最多被一条线网占用（无交叉）
for edge in all_edges:
    occupying = [edge_vars[(net.id, edge.id)] for net in nets]
    model.AddNoOverlap(occupying)
```

### 4.3 路径连续性约束

```python
# 每条线网必须是连续路径
for net in nets:
    for node in nodes:
        incoming = [edge_vars[(net.id, e.id)] for e in node.incoming]
        outgoing = [edge_vars[(net.id, e.id)] for e in node.outgoing]
        model.Add(sum(incoming) == sum(outgoing))
    
    # 起点和终点
    model.Add(sum(incoming) == 1)  # 源节点
    model.Add(sum(outgoing) == 1)  # 汇节点
```

### 4.4 过孔密度约束（Cumulative）

```python
# 每区域过孔总数 ≤ 容量
for zone in zones:
    via_usage = []
    for net in nets:
        for via_edge in zone.via_edges:
            via_usage.append(
                edge_vars[(net.id, via_edge.id)] * via_edge.width
            )
    model.AddCumulative(
        via_usage,
        capacity=zone.max_vias,
        name=f"via_density_zone_{zone.id}"
    )
```

### 4.5 固定线保护约束

```python
# 固定线：被唯一固定线网独占，其他线网禁止占用
for fixed_edge in zone.fixed_edges:
    owner_net = fixed_edge.owner_net
    model.Add(edge_vars[(owner_net, fixed_edge.id)] == 1)
    for net in nets:
        if net.id != owner_net:
            model.Add(edge_vars[(net.id, fixed_edge.id)] == 0)
```

### 4.6 求解

```python
# 目标函数：最小化总长度
total_length = sum(
    edge_vars[(net.id, edge.id)] * edge.length
    for net in nets
    for edge in all_edges
)
model.Minimize(total_length)

# 求解
solver = cp_model.CpSolver()
solver.parameters.num_workers = 8  # 并行
solver.parameters.max_time_in_seconds = 30  # 超时保护
status = solver.Solve(model)

if status == cp_model.OPTIMAL or cp_model.FEASIBLE:
    routes = extract_routes(solver, edge_vars)
```

---

## 5. Ripup-Reroute 迭代优化

### 5.1 为什么需要 Ripup-Reroute？

CP-SAT 比 ILP 快，但：
1. **拓扑分裂时仍需重新求解**
2. **固定线保护需要显式机制**
3. **迭代渐进改进让工程进度可控**

### 5.2 核心算法

```python
class RipupRerouteOptimizer:
    def __init__(self, solver_timeout=30):
        self.solver_timeout = solver_timeout

    def solve(self, zones, nets):
        max_iterations = 5

        for iteration in range(max_iterations):
            # 1. CP-SAT 求解
            solution = self.cpsat_solve(zones, nets, timeout=self.solver_timeout)

            # 2. 检测冲突
            conflicts = self.detect_conflicts(solution)
            if not conflicts:
                return solution  # 收敛

            # 3. Ripup：移除拥塞区域的灵活走线
            self.ripup_flexible_nets(zones, conflicts)

            # 4. 更新 Net-Ordering
            self.update_net_ordering(nets)

        return solution  # 返回最后解（可能次优）

    def detect_conflicts(self, solution):
        """检测拥塞和交叉"""
        hotspots = []
        for zone in zones:
            congestion = zone.get_congestion()
            if congestion > 0.7:  # 70% 利用率阈值
                hotspots.append(zone)
        return hotspots

    def ripup_flexible_nets(self, zones, hotspots):
        """Ripup 拥塞区域的灵活走线（fixed=true 保留）"""
        for zone in hotspots:
            for net in zone.flexible_nets:  # fixed=false
                net.routed = False
                net.clear_route()
```

---

## 6. 算法对比

### 6.1 求解器对比

| 求解器 | 无交叉保证 | 过孔密度 | 求解速度 | 开源 | 推荐度 |
|--------|-----------|---------|---------|------|--------|
| 模拟退火 | ❌ | ❌ | 快 | ✅ | ⭐ |
| 几何求解器 | ❌ | ❌ | 快 | ✅ | ⭐ |
| A* | ⚠️ | ❌ | 中 | ✅ | ⭐⭐ |
| ILP | ✅✅✅ | ✅ | 慢 | SCIP免费 | ⭐⭐⭐ |
| **CP-SAT** | ✅✅✅ | ✅✅ | **快** | ✅免费 | **⭐⭐⭐⭐⭐** |
| SAT | ✅✅✅ | ⚠️ | 慢 | ✅ | ⭐⭐ |

### 6.2 架构路线对比

| 路线 | 描述 | 适合场景 | 无交叉保证 |
|------|------|---------|-----------|
| 路线A | Zone + A* | <20节点，软约束 | ❌ |
| **路线B（推荐）** | **FLUTE + Zone CP-SAT + Ripup** | **20-100节点，硬约束** | **✅✅✅** |
| 路线C | 分层 Ripup | >100节点 | ✅✅✅ |

### 6.3 决策树

```
输入判断：
│
├── 电路规模 < 20节点（小型）
│   └── CP-SAT 直接求解（无需 Ripup-Reroute）
│
├── 电路规模 20-100节点（中大型）
│   ├── 高密度RLC + 无交叉硬约束
│   │   └── 全五阶段（推荐）
│   └── 低密度 + 软约束
│       └── Zone + A*（无需 CP-SAT）
│
└── 电路规模 > 100节点（大型）
    └── 分层 Ripup-Reroute
```

---

## 7. 约束验证

### 7.1 硬约束（100% 必须通过）

| 约束 | 验证方法 | 通过标准 |
|------|---------|---------|
| **无交叉** | 两两线段几何交叉检测 | crossing_count = 0 |
| **固定线保护** | 检查 fixed=true 的边是否被修改 | 0 violations |
| **边界约束** | 所有线段端点在板框内 | 0 violations |
| **最小间距** | 相邻线段距离 | ≥ min_spacing |
| **最小宽度** | 所有线段宽度 | ≥ min_width |
| **过孔数量** | 统计全局过孔数 | ≤ max_via_count |

### 7.2 软约束（优化目标）

| 约束 | 评估方法 | 目标 |
|------|---------|------|
| 总长度 | Σ 所有线段长度 | 最小化 |
| 目标长度偏差 | Σ |target - actual| | 最小化 |
| 拥塞率 | 全局拥塞图平均值 | < 0.6 |

---

## 8. 实现路线图

### 短期（1-2月）
- [ ] 实现 CP-SAT 约束建模
- [ ] 实现 Zone Decomp 区域分解
- [ ] 实现动态 Net-Ordering
- [ ] Phase 1-3 联调测试
- [ ] 小规模（<20节点）端到端验证

### 中期（3-4月）
- [ ] 实现 Ripup-Reroute 主循环
- [ ] 实现拥塞图与冲突检测
- [ ] Phase 4 集成
- [ ] 中规模（20-100节点）性能测试
- [ ] Phase 5 后处理

### 长期（6月+）
- [ ] 强化学习 Net-Ordering
- [ ] GPU 加速 CP-SAT
- [ ] EM 仿真集成

---

## 9. 参考资料

1. **Google OR-Tools CP-SAT**: https://developers.google.com/optimization
2. **FLUTE**: "FLUTE: Fast Lookup Table Based Rectilinear Steiner Tree Algorithm" — Chu
3. **Ripup-Reroute**: "A Detailed Router for VLSI Chips" — S.
4. **CP-SAT Routing**: "Using Constraint Programming for VLSI Routing" — Held et al.
5. **Zone Decomposition**: "A Practical Approach to Global Routing" — Cheng et al.
