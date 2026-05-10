# 功放PCB单层自动布局布线算法设计报告

> 本仓库为功放PCB单层自动布局布线算法研究的核心文档，涵盖算法调研、方案设计和技术规范。

---

## 一、研究历程与版本演进

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

## 二、最终推荐方案：五阶段迭代架构（v3）

```
┌─────────────────────────────────────────────────────────────────┐
│ Phase 1: 全局拓扑规划（FLUTE）                                    │
│   粗粒度路由骨架，估算全局拥塞                                     │
│   O(n log n) Steiner 树构建                                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Phase 2: 区域分解 + 动态 Net-Ordering                             │
│                                                                │
│   固定微带线 → 动态障碍区域（永不参与 Ripup）                     │
│   RLC shunt → 按主干分组（每个区域独立求解）                      │
│                                                                │
│   Net-Ordering（动态规则）：                                      │
│     1. fixed=true → 最先布                                       │
│     2. RF 主干线（宽、微波频段）→ 次布                           │
│     3. 粗短分支 → 再次                                           │
│     4. 细长灵活走线 → 最后                                       │
│     5. RLC shunt 接地支路 → 按分组依序布                         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Phase 3: 区域内详细布线（CP-SAT）⭐                              │
│                                                                │
│   约束建模：                                                     │
│     - AddNoOverlap: 无交叉硬约束                                 │
│     - AddCumulative: 过孔密度约束                               │
│     - AddCircuit: 路径连续性约束                                │
│                                                                │
│   求解策略：                                                     │
│     - 规模 < 20 节点：CP-SAT 直接求解                           │
│     - 规模 > 20 节点：分区 + CP-SAT 求解                        │
│     - 超时：触发 Ripup-Reroute                                  │
│                                                                │
│   求解器：Google OR-Tools（完全开源，Apache 2.0）                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Phase 4: Ripup-Reroute 迭代优化                                 │
│                                                                │
│   while (存在拥塞 或 交叉) and (迭代 < max_iterations):         │
│     1. 拥塞评估：识别 >70% 利用率区域                           │
│     2. Ripup：移除该区域灵活走线（fixed=true 保留）             │
│     3. Reroute：CP-SAT 重新求解                                 │
│     4. 收敛检查：无新增冲突则结束                                │
│                                                                │
│   关键优势：                                                     │
│     - 固定线永远保护                                             │
│     - 计算时间可控（可中断）                                     │
│     - 拓扑分裂自然处理                                           │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Phase 5: 几何精化 + 后处理                                       │
│                                                                │
│   梯度下降路径松弛（连续空间精化）                               │
│   target_length 滑动优化                                         │
│   弧形弯角（Mitered / Circular）                                │
│   DRC 检查（间距、宽度、拥塞率）                                 │
│   输出：Gerber 文件                                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、核心约束建模（CP-SAT）

### 3.1 无交叉约束

```python
from ortools.sat.python import cp_model

model = cp_model.CpModel()

# 每条边最多被一条线网占用
for edge in edges:
    occupying = [edge_vars[(net.id, edge.id)] for net in nets]
    model.AddNoOverlap(occupying)  # CP-SAT 原生支持
```

### 3.2 路径连续性约束

```python
# 每条线网必须是连续路径
for net in nets:
    for node in nodes:
        incoming = sum(incoming_vars)
        outgoing = sum(outgoing_vars)
        model.Add(incoming == outgoing)
    model.Add(start_node.incoming == 1)
    model.Add(end_node.outgoing == 1)
```

### 3.3 过孔密度约束（Cumulative）

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

### 3.4 固定线保护约束

```python
# 固定线：被唯一固定线网独占，其他线网禁止占用
for fixed_edge in zone.fixed_edges:
    owner_net = fixed_edge.owner_net
    model.Add(edge_vars[(owner_net, fixed_edge.id)] == 1)
    for net in nets:
        if net.id != owner_net:
            model.Add(edge_vars[(net.id, fixed_edge.id)] == 0)
```

---

## 四、算法对比总结

### 4.1 求解器对比

| 求解器 | 无交叉保证 | 过孔密度 | 路径连续性 | 求解速度 | 开源 | 推荐度 |
|--------|-----------|---------|-----------|---------|------|--------|
| 模拟退火 | ❌（惩罚项） | ❌ | ❌ | 快 | ✅ | ⭐ |
| 几何求解器 | ❌ | ❌ | ⚠️ | 快 | ✅ | ⭐ |
| A* | ⚠️ | ❌ | ✅ | 中 | ✅ | ⭐⭐ |
| ILP | ✅✅✅ | ✅ | ✅ | 慢 | SCIP免费 | ⭐⭐⭐ |
| **CP-SAT** | ✅✅✅ | ✅✅ | ✅✅ | **快** | ✅免费 | **⭐⭐⭐⭐⭐** |
| SAT | ✅✅✅ | ⚠️ | ✅ | 慢 | ✅ | ⭐⭐ |

### 4.2 架构路线对比

| 路线 | 描述 | 适合场景 | 无交叉保证 |
|------|------|---------|-----------|
| 路线A | Zone Decomp + A* | <20节点，软约束 | ❌ |
| **路线B（推荐）** | **FLUTE + Zone CP-SAT + Ripup-Reroute** | **20-100节点，硬约束** | **✅✅✅** |
| 路线C | 分层 Ripup-Reroute | >100节点 | ✅✅✅ |

### 4.3 为什么 CP-SAT > ILP？

| 维度 | ILP | CP-SAT |
|------|-----|--------|
| 约束编码 | 交叉约束需要大量二元变量 | AddNoOverlap 原生支持 |
| 过孔密度 | 线性约束 + 辅助变量 | AddCumulative 原生支持 |
| 路径连续 | 需要手动编码入度=出度 | AddCircuit 原生支持 |
| 求解速度 | 指数级（分支定界） | 快5-100倍（CP + SAT + 并行） |
| 并行化 | 商业版才有 | 原生多核 |
| 开源 | SCIP学术免费/商业版付费 | **完全免费** |

---

## 五、输入格式

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
  min_spacing: 0.15  # mm
  min_width: 0.3    # mm
```

---

## 六、架构决策树

```
输入判断：
│
├── 电路规模 < 20节点（小型）
│   └── CP-SAT 直接求解（无需 Ripup-Reroute）
│       Route: Phase 1 → Phase 2 → Phase 3 → Phase 5
│
├── 电路规模 20-100节点（中大型）
│   │
│   ├── 高密度RLC + 无交叉硬约束
│   │   └── 全五阶段（推荐）
│   │       Route: Phase 1 → 2 → 3 → 4 → 5
│   │
│   └── 低密度 + 软约束
│       └── 简化版（Zone Decomp + A*）
│           Route: Phase 1 → 2 → Phase 3(A*) → Phase 5
│
└── 电路规模 > 100节点（大型）
    └── 分层迭代（hierarchical）
        ├── 上层：粗粒度全局拓扑（FLUTE）
        └── 下层：区域级 CP-SAT + Ripup-Reroute
```

---

## 七、约束验证

### 7.1 硬约束（必须 100% 通过）

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
| 目标长度偏差 | Σ |target_length - actual| | 最小化 |
| 拥塞率 | 全局拥塞图平均值 | < 0.6 |

---

## 八、实现路线图

### 短期（1-2月）
- [ ] 实现 CP-SAT 约束建模（NoOverlap, Cumulative, Circuit）
- [ ] 实现 Zone Decomp 区域分解算法
- [ ] 实现动态 Net-Ordering
- [ ] Phase 1-3 联调测试
- [ ] 小规模（<20节点）端到端验证

### 中期（3-4月）
- [ ] 实现 Ripup-Reroute 主循环
- [ ] 实现拥塞图与冲突检测
- [ ] Phase 4 集成
- [ ] 中规模（20-100节点）性能测试
- [ ] Phase 5 后处理（弧形弯角 + DRC）

### 长期（6月+）
- [ ] 强化学习 Net-Ordering 研究
- [ ] GPU 加速 CP-SAT 并行
- [ ] 与 EM 仿真集成（ADS/CST）
- [ ] 超大规模分层方案（>100节点）

---

## 九、关键论文发现

| 论文/工具 | 年份 | 关键贡献 | 本项目适用性 |
|----------|------|----------|-------------|
| Apollo | 2024 | GPU加速波导单层路由 | ⭐⭐⭐ 参考架构 |
| FALCON | 2024 | ML框架+可微布局损失 | 长期扩展方向 |
| **Google OR-Tools** | 2019+ | CP-SAT 求解器 | ⭐⭐⭐⭐⭐ **核心工具** |
| FLUTE | 2008 | O(n log n) Steiner树 | ⭐⭐⭐ 全局拓扑规划 |
| Ripup-Reroute | 1980s | IC详细布线经典方法 | ⭐⭐⭐⭐ 迭代优化 |

---

## 十、核心设计约束

- **单层PCB**：禁止过孔，全平面接地
- **微带宽度预定**：根据功率/频率计算一次，后续固定
- **功率管固定**：大功率晶体管位置由热/机械设计决定
- **DXF输入**：支持导入板框和接口坐标
- **微带拓扑**：直线/U形/L形/T形/十字形，预定义模板
- **无交叉硬约束**：交叉数量必须为0

---

## 十一、局限性与未来扩展

### 局限性
- 不支持多层PCB
- 不优化微带宽度
- 无电磁仿真集成
- 拓扑需手动指定

### 未来扩展
- [ ] 多层过孔支持
- [ ] 宽度优化
- [ ] EM感知优化
- [ ] 强化学习Net-Ordering
- [ ] 与ADS/CST集成
