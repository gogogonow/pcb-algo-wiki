# 布线算法选型深度分析报告（v2 — 迭代优化架构版）

> 场景：固定微带线 + 灵活走线混合 | 高密度 RLC shunt | 无交叉硬约束
>
> 核心升级：引入 Ripup-Reroute 迭代优化机制，解决 ILP 规模不可控问题

---

## 1. 场景重新定义

### 1.1 输入格式

```yaml
nodes:
  node_A: { type: "junction" }
  node_shunt_tap:
    type: "component_pad_junction"
edges:
  tl_segment_part1:  # 固定微带线段
    type: "microstrip"
    connections: [ "node_A", "node_shunt_tap" ]
    constraint: { width: 1.0, target_length: 4.0, fixed: true }

  tl_segment_part2:  # 灵活走线段
    type: "microstrip"
    connections: [ "node_shunt_tap", "node_B" ]
    constraint: { width: 1.0, target_length: 6.0, fixed: false }

  c_shunt_match:  # 高密度RLC shunt
    type: "lumped_capacitor"
    connections: [ "node_shunt_tap", "GND_REF" ]
    parameters: { value_pF: 5.6, package: "0402" }
```

### 1.2 场景三要素

| 要素 | 说明 |
|------|------|
| **混合线网** | 固定微带线（fixed=true）+ 灵活走线（fixed=false） |
| **高密度 RLC** | 大量 shunt 器件，拓扑分裂频繁 |
| **无交叉硬约束** | 交叉 = 0 是唯一可接受的结果 |

### 1.3 问题性质

```
之前（简单场景）：局部路径优化
     → 单条线找最优路径，能量函数驱动

现在（复杂场景）：全局拥塞避免 + 拓扑感知的多层协调
     → 固定线是动态障碍
     → RLC密度高 → 拓扑分裂频繁
     → 无交叉 = 全局硬约束，不能后处理修复
```

---

## 2. 当前方案的架构缺陷

### 2.1 当前方案的问题

```
区域分解 → Net-Ordering → ILP（一次性全局求解） → 几何求解器
```

| 问题 | 说明 |
|------|------|
| **ILP 规模不可控** | 全局 N 条线网联合求解，变量数指数增长 |
| **拓扑分裂处理僵硬** | 一次性分裂处理，RLC 密集插入时需重新全部求解 |
| **计算时间不可预测** | 可能指数爆炸，无法给工程进度提供保证 |
| **无法渐进式改进** | 没有迭代反馈，发现问题时只能从头重来 |

### 2.2 ILP 的正确用法

ILP 的强项是**局部子问题的精确求解**，不是全局大规模问题。

| ILP 适合 | ILP 不适合 |
|---------|-----------|
| 区域内（<20节点）子问题 | 全局 N 条线网联合求解 |
| 有明确约束的精确优化 | 拓扑结构动态变化的场景 |
| 需要硬约束保证 | 大规模（>50节点）问题 |

---

## 3. 关键发现：Ripup-Reroute 迭代优化

### 3.1 什么是 Ripup-Reroute？

**经典 IC 领域的成熟方法**，在详细布线中广泛使用：

```
Phase 1: 初始布线（快速贪心/A*）
     ↓
Phase 2: 迭代优化
  while (存在冲突):
    - 识别拥塞/交叉区域
    - 移除该区域所有线路（ripup）
    - 在更大搜索空间内重新布线（reroute）
  until (收敛 或 超时)
     ↓
Phase 3: 验证输出
```

### 3.2 为什么 Ripup-Reroute 适合本场景？

| 特性 | 本场景需求 | Ripup-Reroute 匹配度 |
|------|----------|-------------------|
| 固定线保护 | 固定微带线不能动 | ✅ 固定线不参与 ripup |
| 拓扑分裂 | RLC 密集插入时重优化 | ✅ 局部 ripup 自然处理分裂 |
| 计算时间控制 | 工程进度需要可预测 | ✅ 可设置最大迭代次数 |
| 无交叉保证 | 硬性要求 | ✅ 迭代验证 + ILP 局部求解 |
| 渐进式改进 | 随时给出可行解 | ✅ 每次迭代都是有效改进 |

### 3.3 Ripup-Reroute vs ILP 一次性求解

| 维度 | ILP 一次性求解 | Ripup-Reroute 迭代优化 |
|------|---------------|---------------------|
| **计算时间** | 不可预测（可能指数） | 可控制（固定迭代次数） |
| **内存占用** | 全局变量，可能爆炸 | 局部问题，空间可控 |
| **拓扑分裂** | 需完全重求解 | 自然重新优化 |
| **固定线处理** | 区域分解隐式保护 | 显式保护（永不 ripup） |
| **全局最优** | 理论保证 | 不保证，但可接近 |
| **工程友好度** | 低（不知道何时完成） | 高（可随时中断） |

---

## 4. 推荐方案：五阶段迭代架构

### 4.1 完整流水线

```
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 1: 全局拓扑规划（Global Routing）                          │
│                                                                │
│ 输入：Graph-Topology YAML + 板框 DXF                            │
│ 方法：FLUTE（RC1/RC2 树）或 A* 粗略搜索                         │
│ 输出：每个线网的粗略路由骨架（区域级路径）                        │
│ 目标：建立初始可行解，估算全局拥塞                               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 2: 区域分解 + Net-Ordering（预处理）                       │
│                                                                │
│ 区域分解：                                                       │
│   固定微带线 → 动态障碍区域                                       │
│   RLC shunt → 按主干分组（每个主干+shunt 为一个区域）            │
│   灵活走线 → 在剩余自由区域布线                                  │
│                                                                │
│ Net-Ordering（静态排序）：                                       │
│   1. fixed=true → 最先布（不可移动）                            │
│   2. RF 主干线（宽、微波频段）→ 次布（阻抗敏感）                 │
│   3. 粗短分支 → 再次（电阻低优先）                               │
│   4. 细长灵活走线 → 最后（有最多绕行空间）                       │
│   5. RLC shunt 接地支路 → 按分组依序布                          │
│                                                                │
│ 输出：分区图 + 布线顺序                                          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 3: 区域内详细布线（Detail Routing）                        │
│                                                                │
│ 对每个区域内的线网：                                              │
│   if 区域规模 < 阈值(20节点):                                    │
│       ILP（硬约束精确求解）                                       │
│   else:                                                         │
│       A* + 拥塞代价（效率优先）                                  │
│                                                                │
│ 固定线被标记为障碍，不参与优化                                    │
│ 输出：每个区域的精确几何路径                                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 4: Ripup-Reroute 迭代优化 ⭐（核心改进）                  │
│                                                                │
│ while (存在拥塞 或 交叉) and (迭代 < max_iterations):            │
│                                                                │
│   1. 拥塞评估：                                                  │
│      - 构建全局拥塞图                                            │
│      - 识别高冲突区域（>70% 利用率）                             │
│                                                                │
│   2. Ripup 阶段：                                               │
│      - 标记冲突区域内的所有灵活走线（fixed=true 保留）           │
│      - 从拥塞区域移除这些线路                                    │
│                                                                │
│   3. Reroute 阶段：                                             │
│      - 在更大搜索空间内重新求解（调用 Phase 3）                  │
│      - 优先保护高优先级线网                                      │
│                                                                │
│   4. 收敛检查：                                                  │
│      - 如果无新增冲突 → 收敛                                     │
│      - 否则 → 继续迭代                                           │
│                                                                │
│ 输出：冲突-free 的几何路径                                       │
│ 关键优势：固定线永远保护，不参与 ripup                           │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 5: 几何精化 + 后处理                                       │
│                                                                │
│ 梯度下降路径松弛（连续空间精化）                                   │
│ target_length 滑动优化（减小残余误差）                            │
│ 弧形弯角（Mitered 90° → 45° 斜切 或 Circular）                  │
│ GND_REF 规则引擎（自动生成接地过孔）                             │
│ DRC 检查（间距、宽度、间距 vs 宽度）                             │
│ 输出：Gerber 文件                                                │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 架构决策树

```
输入判断：
├── 电路规模 < 20节点（小型）
│   └── Phase 1-2-3 直接求解（无需 Phase 4 Ripup-Reroute）
│
├── 电路规模 20-100节点（中大型）
│   ├── 固定线 + 高密度 RLC + 无交叉硬约束
│   │   └── 全五阶段：FLUTE + Zone ILP + Ripup-Reroute + Geometric
│   └── 低密度 + 软约束
│       └── Phase 1-2-3 简化版：Zone Decomp + A*（无需 ILP）
│
└── 电路规模 > 100节点（大型）
    └── 分层迭代（hierarchical Ripup-Reroute）
        ├── 上层：粗粒度全局拓扑（FLUTE）
        └── 下层：区域级 Ripup-Reroute
```

### 4.3 三种架构路线对比

| 路线 | 描述 | 适合场景 | 缺点 |
|------|------|---------|------|
| **路线A（简化版）** | Zone Decomp + 区域内 ILP + Geometric | 小规模（<20节点） | 无迭代优化 |
| **路线B（推荐）** | FLUTE 全局拓扑 + Zone ILP + Ripup-Reroute + Geometric | 中大规模（>20节点） | 迭代次数难预测 |
| **路线C（分层版）** | 分层 Ripup-Reroute + FLUTE 顶层规划 | 超大规模（>100节点） | 不保证全局最优 |

---

## 5. 算法对比（完整版）

### 5.1 核心对比表

| 算法 | 无交叉保证 | 拥塞感知 | 规模可控 | 拓扑分裂 | 固定线保护 | 计算时间 | 适合 Phase |
|------|-----------|---------|---------|---------|-----------|---------|-----------|
| Lee 迷宫 | ✅✅✅ | ❌ | ❌ | ❌ | ⚠️ | O(ng) | 不推荐 |
| A* + 拥塞 | ⚠️ | ✅ | ⚠️ | ❌ | ✅ | O(n²) | Phase 3（大规模区域） |
| NC-A* | ✅✅ | ✅ | ⚠️ | ⚠️ | ✅ | O(n² log n) | Phase 3 |
| **ILP** | ✅✅✅ | ✅✅ | ⚠️（需分解） | ✅✅ | ✅✅✅ | 指数级 | **Phase 3（区域级）** |
| SAT | ✅✅✅ | ⚠️ | ❌ | ✅ | ✅ | 指数级 | Phase 3（小区域） |
| 模拟退火 | ⚠️ | ✅✅ | ✅ | ✅✅ | ✅✅ | 多项式 | Phase 1（全局粗规划） |
| **Ripup-Reroute** | ✅✅✅ | ✅✅ | ✅✅✅ | ✅✅✅ | ✅✅✅ | **可控** | **Phase 4（核心）** |
| **Zone Decomp + ILP** | ✅✅✅ | ✅✅ | ✅✅✅ | ✅✅ | ✅✅✅ | 可控 | Phase 2-3 |
| FLUTE | ❌ | ❌ | ✅✅ | ⚠️ | ⚠️ | O(n log n) | Phase 1（拓扑规划） |

### 5.2 为什么有些算法被放弃？

| 算法 | 放弃原因 |
|------|---------|
| Lee 迷宫 | 高密度下状态空间爆炸，O(ng) 不可接受 |
| 纯 A* | 栅格化与 target_length 滑动冲突，无交叉保证弱 |
| 纯 SAT | 变量数指数增长，只适合 <20 节点 |
| 纯模拟退火 | 交叉是惩罚项，无法保证硬约束 |
| 纯几何求解器 | 全局拥塞协调弱，高密度场景会冲突 |

---

## 6. Net-Ordering 的深度优化

### 6.1 静态规则（当前方案）

```python
# 简单排序：约束最严的先布
order = sorted(nets, key=lambda n: (
    not n.fixed,            # fixed=true 优先（布在前面）
    -n.priority,            # 高优先级次之
    n.length                # 短线路先布
))
```

**问题**：静态规则无法捕捉线路间的复杂依赖关系。

### 6.2 动态规则（改进方案）

```python
class DynamicNetOrderer:
    """基于实时拥塞反馈的动态线网排序"""

    def __init__(self):
        self.routing_state = None

    def update_state(self, routing_state):
        """每次布线完成后更新状态"""
        self.routing_state = routing_state

    def select_next(self, remaining_nets):
        """
        选择下一条要布的线：冲突风险最低 + 优先级最高
        贪心选择：min(冲突风险 / 优先级)
        """
        scores = []
        for net in remaining_nets:
            conflict_risk = self.estimate_conflict_risk(net)
            priority_score = net.priority
            score = conflict_risk / priority_score if priority_score > 0 else conflict_risk
            scores.append((net, score))

        # 选择 score 最小的（冲突风险低 + 优先级高）
        return min(scores, key=lambda x: x[1])[0]

    def estimate_conflict_risk(self, net):
        """估算布线冲突风险"""
        # 1. 经过高拥塞区域的数量
        congestion_risk = sum(
            self.routing_state.congestion[p] * len(net.passes_through(p))
            for p in net.path_candidates
        )

        # 2. 与高优先级固定线的交汇数量
        fixed_crossing_risk = len([
            f for f in self.routing_state.fixed_nets
            if self.paths_intersect(net, f)
        ])

        # 3. 目标长度 vs 可用空间
        length_risk = net.target_length / self.routing_state.available_space(net.region)

        return congestion_risk + 2 * fixed_crossing_risk + length_risk
```

### 6.3 强化学习 Net-Ordering（长期方向）

```python
# 状态：当前布线完成率、拥塞图、剩余线路特征
# 动作：选择下一条要布的线
# 奖励：最终无交叉率 + 布线完成率

class RLNetOrderer:
    """强化学习线网排序（长期研究方向）"""

    def __init__(self):
        self.q_network = load_trained_model("net_ordering_rl.pt")

    def select_next(self, state, remaining_nets):
        # Q(state, action) → 选择 Q 值最高的 action
        q_values = [self.q_network.forward(state, net) for net in remaining_nets]
        return remaining_nets[argmax(q_values)]

    def train(self, episodes):
        # 收集交互数据
        # 更新 Q 网络
        # 收敛后可用于实际排序
        pass
```

**为什么 RL > 静态规则？**
- 高密度 RLC 场景下，静态规则无法捕捉线路间的复杂依赖
- RL 可以学习"这条线先布会让另一条高优先级线无路可走"的反事实
- 类似下棋：高手看 10 步，静态规则只看 1 步

**注意**：RL 方案目前是研究方向，短期内以动态规则为主。

---

## 7. 拥塞图与冲突检测

### 7.1 拥塞图构建

```python
class CongestionMap:
    """二维拥塞图，评估全局布线密度"""

    def __init__(self, board_width, board_height, resolution=1.0):
        # resolution: 每个网格的物理尺寸（mm）
        self.width = int(board_width / resolution)
        self.height = int(board_height / resolution)
        self.grid = np.zeros((self.width, self.height))
        self.capacity = np.ones((self.width, self.height)) * max_capacity_per_cell

    def mark_fixed(self, fixed_lines):
        """标记固定线占据的区域（永久障碍）"""
        for line in fixed_lines:
            for segment in line.segments:
                cells = self.get_cells_covered(segment)
                self.grid[cells] = INF  # 永久占用

    def mark_estimated_route(self, net, path):
        """预估一条线网占用的区域（用于排序决策）"""
        for segment in path:
            cells = self.get_cells_covered(segment)
            self.grid[cells] += 1

    def get_congestion_ratio(self, region):
        """返回区域内拥塞率（0.0~1.0+）"""
        usage = self.grid[region].sum()
        capacity = self.capacity[region].sum()
        return usage / capacity if capacity > 0 else 0

    def find_hotspots(self, threshold=0.7):
        """找出拥塞率超过阈值的区域"""
        hotspots = []
        for x in range(self.width):
            for y in range(self.height):
                if self.grid[x, y] / self.capacity[x, y] > threshold:
                    hotspots.append((x, y))
        return hotspots
```

### 7.2 冲突检测算法

```python
def detect_crossings(all_routes):
    """检测所有线网之间的交叉点"""
    crossings = []
    for i, route_i in enumerate(all_routes):
        for route_j in all_routes[i+1:]:
            intersection = compute_intersection(route_i, route_j)
            if intersection is not None:
                crossings.append({
                    'net_a': route_i.net_id,
                    'net_b': route_j.net_id,
                    'point': intersection,
                    'type': 'crossing' if is_hard_constraint_violated(route_i, route_j) else 'proximity'
                })
    return crossings

def compute_intersection(route_a, route_b):
    """计算两条路线的交点（几何计算）"""
    # 对 route_a 和 route_b 的每对线段检测交叉
    for seg_a in route_a.segments:
        for seg_b in route_b.segments:
            if do_segments_intersect(seg_a, seg_b):
                return intersection_point(seg_a, seg_b)
    return None
```

---

## 8. 约束验证方法

### 8.1 硬约束验证（100% 必须通过）

| 约束类型 | 验证方法 | 通过标准 |
|---------|---------|---------|
| **无交叉** | 几何交叉检测（所有线段两两检测） | crossing_count = 0 |
| **固定线保护** | 检查固定线是否被移动或修改 | fixed_line_count = original |
| **边界约束** | 所有线段端点必须在板框内 | 0 violations |
| **最小间距** | 同一层所有相邻线段距离 | ≥ design_rule_min |
| **最小宽度** | 所有线段宽度 | ≥ min_width |
| **过孔数量** | 统计全局过孔数 | ≤ max_via_count |

### 8.2 软约束评估（优化目标）

| 约束类型 | 评估方法 | 目标 |
|---------|---------|------|
| **总长度** | 累加所有线段长度 | 最小化 |
| **目标长度偏差** | Σ |target_length - actual_length| | 最小化 |
| **弧形弯角比例** | 弧形弯角数 / 总弯角数 | 越高越好（高频场景） |
| **拥塞率** | 全局拥塞图平均值 | < 0.6 |

---

## 9. 实现路线图

### 9.1 短期（1-2月）

- [ ] 实现拥塞图（CongestionMap）
- [ ] 实现 Ripup-Reroute 主循环
- [ ] 实现固定线保护机制
- [ ] Phase 1-4 联调测试
- [ ] 小规模（<20节点）端到端验证

### 9.2 中期（3-4月）

- [ ] 实现几何求解器（梯度下降路径松弛）
- [ ] 实现 Net-Ordering 动态规则
- [ ] Phase 5 后处理集成
- [ ] 中规模（20-100节点）性能测试
- [ ] DRC 规则引擎

### 9.3 长期（6月+）

- [ ] 强化学习 Net-Ordering 研究
- [ ] GPU 加速 Ripup-Reroute 并发
- [ ] 与 EM 仿真集成
- [ ] 超大规模（>100节点）分层方案

---

## 10. 关键结论

### 10.1 推荐方案总结

> **最优方案 = FLUTE 全局拓扑 + Zone ILP + Ripup-Reroute 迭代优化 + 几何求解器**

```
Phase 1: FLUTE 全局拓扑规划（粗粒度）
Phase 2: Zone Decomp + 静态/动态 Net-Ordering（预处理）
Phase 3: Zone ILP 区域内详细布线（硬约束求解）
Phase 4: Ripup-Reroute 迭代优化（核心改进，可控时间）
Phase 5: 几何精化 + 后处理（输出 Gerber）
```

### 10.2 为什么这个架构更好？

| 改进点 | 之前方案 | 新方案 | 收益 |
|--------|---------|--------|------|
| ILP 规模 | 全局联合求解 | 区域内子问题 | **规模可控** |
| 拓扑分裂 | 一次性处理 | 迭代自然重优化 | **灵活应对** |
| 计算时间 | 不可预测 | 可中断控制 | **工程友好** |
| 固定线保护 | 区域分解隐式 | Ripup 显式保护 | **更可靠** |
| 冲突解决 | 预防为主 | 预防 + 检测 + 修复 | **更鲁棒** |

### 10.3 核心决策依据

1. **无交叉硬约束** → 必须用 ILP（唯一硬约束保证方案）
2. **ILP 规模可控** → Zone Decomp（区域分解降复杂度）
3. **ILP 时间可控** → Ripup-Reroute（迭代优化，可中断）
4. **固定线保护** → Ripup 永不涉及 fixed=true 的线
5. **全局最优 vs 工程实用** → 选择工程实用（可接受的次优解 > 不收敛的最优解）

---

## 参考资料

1. **Ripup-Reroute**: "A Detailed Router for VLSI Chips" — S. 以上的经典算法
2. **ILP Routing**: "Integer Programming for VLSI Layout" — Lengauer et al.
3. **Zone Decomposition**: "A Practical Approach to Global Routing" — Cheng et al.
4. **FLUTE**: "FLUTE: Fast Lookup Table Based Rectilinear Steiner Tree Algorithm" — Chu
5. **Net-Ordering**: "Routability-driven Routing: Net Reordering" — Pan et al.
6. **Congestion Estimation**: "Global Routing with Crosstalk Constraints" — Ho et al.
