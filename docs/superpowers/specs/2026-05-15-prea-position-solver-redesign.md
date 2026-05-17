# PreA 位置求解器重构设计规范

> ⚠️ 历史归档文档：该规格用于当时迭代记录，当前主线请以 `README.md` / `ALGORITHM-OVERVIEW.md` / `ITERATION-PLAN.md` 为准。

**日期：** 2026-05-15  
**状态：** 待审查  
**文件：** `src/tools/pcb_solve_v2.py`（重点：`_solve_pre_a_positions` 和 `_render_pre_phase_svg`）

---

## 1. 背景与目标

### 1.1 PreA 的核心职责

PreA 阶段是射频版图可视化流水线的第一个可评判产物，其职责是：

1. **结构化输出固定端点**：所有固定器件（IC 管脚、测试点、GND 平面）以其绝对坐标精确呈现
2. **精确传播微带线段**：从 IC 管脚出发，沿管脚方向，以精确的约束长度逐段传播
3. **合法放置 RLC/UV 器件**：两管脚分别连接到正确的微带线端点，遵守封装 pitch，不短路
4. **减少斜向连接**：器件主轴应水平或垂直，贴近微带线走线方向
5. **完整渲染 GND 侧管脚**：即使一端为 GND，也要在组件体积内合法显示
6. **可评判性**：每个器件、每段微带线都能独立核查正确性

### 1.2 当前问题清单

| 编号 | 问题 | 影响 |
|------|------|------|
| P1 | `_solve_pre_a_positions` 单体 830 行，无清晰阶段边界 | 难以维护、调试困难 |
| P2 | 弹簧松弛迭代 180 次，可收敛到错误位置 | 微带线端点不在正确位置 |
| P3 | IC 前缀硬编码 `startswith("IC")` | 非 IC 前缀的固定器件逻辑异常 |
| P4 | 串联 RLC 方向错误：基于第一管脚的上游方向，而非两端微带线端点间向量 | C1/R1 等组件横跨微带线或距离太远 |
| P5 | 多次 pitch re-lock 逻辑（3 次执行相同操作）说明设计不干净 | 代码脆弱，改动易引入回退 |
| P6 | 并联 RLC 轴向吸附不彻底，某些情况仍出现斜线 | C3/C5/C6 等斜向放置 |
| P7 | 复合端点（如 `C1.PIN_1,R1.PIN_1`）在多处 ad-hoc 处理 | 复合端点同步不可靠 |

---

## 2. 数据案例变更（rf_layout_simplified.yaml）

用户明确指出 TP1 和 R2 这两个数据对案例无意义，需删除：

**要删除的内容：**
- `components.R2`（UV 器件）
- `components.TP1`（固定测试点）
- `terminals.TP1.PIN_1`
- `edges.R2_to_TP1`（约束微带线，R2.PIN_1 → TP1.PIN_1）
- `edges.IC1_pin1_seg2_to_R2`（自由微带线，IC1_pin1_seg2_end_split_pad → R2.PIN_2）
- `nodes.IC1_pin1_seg2_end_split_pad`（t_junction 节点，失去所有连接）
- `edges.IC1_pin1_seg2`（微带线段，只有 seg2_end_split_pad 作为末端，失去意义）
- `nodes.IC1_pin1_seg1_universal_node.connection_rules.branches[0]`（seg2 分支条目）

**删除后的效果：** PIN_1 微带线树简化为：
```
IC1.PIN_1 → seg1 → universal_node
  ├─ seg3 → seg3_end_split_pad → C5.PIN_1  (shunt cap, GND)
  └─ seg4 → seg4_end_split_pad → C1.PIN_1,R1.PIN_1 → combiner → seg5 → C2.PIN_1 → seg6 → TP4
```

---

## 3. 位置求解器重构方案

### 3.1 整体架构：5 个顺序纯函数

当前的 `_solve_pre_a_positions` 替换为以下调用链：

```
_seed_fixed_positions(artifact)
  → _propagate_microstrip_tree(artifact, junction_templates, branch_offset_u_tokens)
  → _place_series_rlc(artifact)
  → _place_shunt_rlc(artifact)
  → _sync_composite_endpoints(artifact)
```

每个函数接收前一个的 `positions: dict[str, tuple[float, float]]`，并返回更新后的版本，同时更新 `constrained: set[str]`。

### 3.2 Phase 1：种子固定端点（`_seed_fixed_positions`）

**输入：** `artifact.fixed_terminals`  
**规则：** 任何 `abs_x, abs_y` 不为 None 的 terminal，直接使用绝对坐标。其余使用 `plan_xy` 种子或板框中心。  
**判断标准：** 不再使用 `startswith("IC")`，改为检查 `abs_x is not None and abs_y is not None`。

### 3.3 Phase 2：微带线树 BFS 传播（`_propagate_microstrip_tree`）

**目标：** 从所有固定管脚出发，沿约束长度传播，放置所有微带线端点。

**算法：**
```
1. 初始化队列 Q = [所有 fixed terminals]
2. 初始化 constrained = {所有 fixed terminals}
3. BFS loop:
   a. 从 Q 取出 src_id
   b. 遍历与 src_id 相连的 microstrip edges
   c. 若对端 dst_id 已在 constrained，跳过
   d. 若 edge 有 target_length：
      - 若 src_id 有 orientation（管脚方向）: dx/dy = cos/sin(orientation) * length
      - 否则: dx/dy = normalize(dst_seed - src_pos) * length
      - positions[dst_id] = src_pos + (dx, dy)（经 clamp）
   e. 若 dst_id 是 junction_node，应用 junction_template 放置所有 branch endpoints
   f. 将 dst_id 加入 constrained 和 Q
4. 自由边（无 target_length）: 保留种子坐标，后续连接
```

**关键变化：** 无弹簧松弛，无 180 次迭代，约束直接传播。

### 3.4 Phase 3：串联 RLC 放置（`_place_series_rlc`）

**定义：** 串联 RLC = 2-pin UV 器件，两个管脚各有至少一条微带线边。  
**例：** R3（PIN_1 连接到 seg4 末端，PIN_2 连接到 R3_to_combiner）

**算法：**
```
对每个 2-pin UV 器件 comp:
  anchor_ep = comp.anchor_pin
  other_ep = comp.other_pin
  
  若两个 pin 都有 edge 连接:
    ax, ay = positions[anchor_ep]
    bx, by = 从 other_ep 的相邻边的另一端的位置
    
    # 计算轴向量化方向
    raw_u = normalize(bx - ax, by - ay)
    axis_u = snap_to_axis(raw_u)  # 最接近水平或垂直方向
    
    # 按 pitch 放置
    positions[other_ep] = (ax + axis_u[0] * pitch, ay + axis_u[1] * pitch)
    
    # 更新相邻自由边另一端
    free_neighbor = 从 other_ep 的自由边找到的非 UV 端点
    if free_neighbor:
      positions[free_neighbor] = positions[other_ep]
```

**关键变化：** 方向基于「两端微带线端点间向量」，不再是下游 sink 方向猜测。

### 3.5 Phase 4：并联 RLC 放置（`_place_shunt_rlc`）

**定义：** 并联 RLC = 2-pin UV 器件，只有一个管脚有显式微带线边，另一端为 GND（或无连接）。  
**例：** C3（PIN_1 连接到 seg2 末端，PIN_2 = GND）

**算法：**
```
对每个 2-pin UV 器件 comp:
  若只有一个 pin 有 edge 连接（known_ep）:
    ref_seg = known_ep 相连的约束微带线段方向（snap 到轴）
    
    # 三个候选方向：前（沿微带线）、左（垂直左）、右（垂直右）
    candidates = [front, left, right]
    
    # 用净间距评分（避开其他微带线段）
    best = max(candidates, key=clearance_to_constrained_segments)
    positions[missing_ep] = known_pos + best * pitch
```

**关键变化：** `ref_seg` 强制 snap 到轴，确保候选方向始终水平/垂直。

### 3.6 Phase 5：复合端点同步（`_sync_composite_endpoints`）

对形如 `C1.PIN_1,R1.PIN_1` 的复合端点：
- 从其成员 pin 位置求均值，更新复合端点坐标
- 反向同步：所有成员 pin 设为该均值（保证一致）

---

## 4. 通用规则（非 IC 特定）

以下规则适用于任何案例，不依赖器件命名：

| 规则 | 实现 |
|------|------|
| 固定器件识别 | `abs_x is not None and abs_y is not None`，不检查名称前缀 |
| 管脚方向识别 | `terminal.orientation is not None` |
| 串联/并联 RLC 判别 | 统计每个 pin 的 edge 连接数（>0 = 有连接，=0 = 无约束）|
| 方向量化 | `snap_to_axis(u)`: `|ux| >= |uy|` → (±1, 0)，否则 (0, ±1) |
| 净间距评分 | 对所有约束微带线段计算端点到线段最短距离，取最小值 |

---

## 5. 渲染层（`_render_pre_phase_svg`）变更

位置求解器重构完成后，渲染层只需要：
1. 输入正确的 `positions` 字典
2. 无需改动渲染代码（复用现有逻辑）

若出现可视化 overlap（RLC 焊盘跨越非连接微带线），这是求解器问题，在求解器层修复，不在渲染层规避。

---

## 6. 测试策略（TDD）

每个修复都先写失败测试，再实现：

| 测试 | 断言 |
|------|------|
| TP1/R2 删除后案例加载正常 | yaml 解析无报错，artifact 中无 R2/TP1 相关 edge |
| 串联 RLC 方向正确（R3/C4） | `R3.PIN_1` 和 `R3.PIN_2` 的 x 或 y 坐标只差 pitch，无斜向 |
| 串联 RLC 不与微带线重叠（R3） | R3 两管脚坐标不在 seg4 上 |
| 并联 RLC 两管脚都可见（C3/C5/C6） | `C3.PIN_1` 与 `C3.PIN_2` 坐标不相同，distance > 0.3 |
| 固定器件识别不依赖前缀 | TP2（非 IC）的坐标正确固定 |
| 通用位置求解函数签名测试 | `_seed_fixed_positions` 等 5 个函数可独立调用、返回预期类型 |

---

## 7. 工作项

### WI-1：yaml 案例清理（独立，无依赖）
删除 TP1、R2 及其关联 edges/nodes，更新回归基线。

### WI-2：串联 RLC 方向修复（依赖 WI-1）
针对 R3/C4/C1/R1 的方向错误，先写失败测试，再修复 `_pitch_direction` 或提取新函数。

### WI-3：并联 RLC 轴向吸附加固（依赖 WI-1）
针对 C3/C5/C6，确认 `_one_hop_constrained_u` 的 snap 分支覆盖所有情况。

### WI-4：重构 `_solve_pre_a_positions` 为 5 个子函数（依赖 WI-2, WI-3）
实际代码结构化拆分。测试通过后，删除弹簧松弛迭代。

### WI-5：移除 IC 前缀硬编码（依赖 WI-4）
替换 `startswith("IC")` 为 `abs_x is not None` 判断，补相应测试。

---

## 8. 不包含在本次范围内

- 渲染层（SVG 样式、颜色、标签）不做结构性变更
- PhaseA/B/C 路由器不受影响
- 其他测试文件不做无关修改
