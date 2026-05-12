# 功放 PCB 单层自动布局布线 —— 总体算法方案 v6

> **基于真实案例 `rf_layout_simplified.yaml` (PA_Module_Simplified) 验证后的最终方案**
>
> 三阶段求解器：SA 浮动 + CP-SAT 主求解 + A* 灵活
> 输入规范：v3.3（外部 EDA 接口）· 内部 IR：v6 · 上一基线：v4 hybrid_rf_graph

---

## 0. 版本谱系与本次刷新背景

| 版本 | 角色 | 状态 |
|---|---|---|
| v3 | 单求解器 CP-SAT，变量爆炸 | 废弃 |
| v4 | 内部 IR 基线：terminals/nodes/edges + 三求解器分发 + 拓扑分裂 + 浮动器件引力场 | 内部参考保留 |
| v5 | 在 v4 上接入 v3.3，**未经真实案例验证** | 草案废弃 |
| **v6** | **以 `rf_layout_simplified.yaml` 锚定回归用例** | **本文档** |

v4 → v6 的变化全部由真实案例驱动，归并为 5 个结构性决策（D1–D5），见 §1。

---

## 1. v4 → v6 的 5 个结构性决策

| 决策 | v4 / v5 | v6 | 触发依据 |
|---|---|---|---|
| **D1** RLC 建模 | 翻译为 `lumped_*` edge，串/并由 connections 是否触地推断 | RLC = component-with-pads；pin 直接进 `terminals`；`lumped_*` 翻译层在 v3.3 数据流中作废 | 案例 9 个 RLC 全是 `parametric_uv` + pin 直接出现在 edge 端点 |
| **D2** UV 解析 | "中段插入 `component_pad_junction` 拓扑分裂" | **anchor_pin 沿宿主 microstrip 端点滑动**；不破坏原 edge 拓扑 | 案例 anchor_pin 总是 host edge 端点 |
| **D3** routing_class | 二分法：rf_constrained / flexible_path | 三档：`rf_constrained_locked` / `rf_constrained_free` / `flexible_path` | 案例 8/22 RF 段无 `target_length` |
| **D4** 节点类型 | 5 种 | 新增 **`universal_junction`** 多分支 manifold 模板 + `t_combiner_junction` 别名 | 案例两个核心功分点都用 universal_junction，含显式 `branches[]` |
| **D5** Schema 容错 | 严格 pydantic + LVS 强阻塞 | Lint 容错（typo 自动修复 + 未知字段透传告警）+ LVS 软警告 | 案例有 `redius`/`cicle` typo + 未知 `bend_style: curved` + 无 `logical_net` |

> v4 文档原 §0（v3 → v4 关键改动）依然成立；v6 在其之上叠加上表 5 行修正。

---

## 2. v6 顶层架构

```
                ┌──────────────────────────────┐
                │  v3.3 YAML (外部 EDA 接口)   │
                │  components / footprints /   │
                │  nodes / edges / terminals   │
                └─────────────┬────────────────┘
                              │
                              ▼
        ┌───────────────────────────────────────────────┐
        │  ① Frontend Compiler                          │
        │   1. Schema Lint                              │
        │   2. expand_components (footprint → pad 坐标) │
        │   3. UV Resolve (anchor → host_edge 端点)     │
        │   4. universal_junction 模板展开              │
        │   5. routing_class triage (3 档)              │
        │   6. 拓扑健全性检查 (warning-only)            │
        └─────────────┬─────────────────────────────────┘
                      │ 内部 IR (v6 = v4 + D1..D5 修正)
        ┌─────────────┼─────────────┬───────────────────┐
        ▼                           ▼                   ▼
┌──────────────────┐    ┌────────────────────────┐  ┌──────────────────┐
│ ② Phase 1 SA     │    │ ③ Phase 2 CP-SAT       │  │ ④ Phase 3 A*     │
│ UV 粗放置         │    │ ★ 主求解器              │  │ flexible_path    │
│ 引力场 + HPWL     │ →  │ 长度锁 + NoOverlap +   │→ │ 迷宫绕障          │
│ + 5 次重试        │    │ universal_junction 几何 │  │ (本案例为空)     │
└──────────────────┘    └────────────────────────┘  └──────────────────┘
                              │
                              ▼
                ┌──────────────────────────────┐
                │ ⑤ Postproc                   │
                │  bend 渲染 / 软 LVS / DRC     │
                │  / SVG / Gerber stub          │
                └──────────────────────────────┘
```

**调度顺序**：

1. **Frontend Compiler**：把 v3.3 容错地编译为 v6 IR；fixed pad 坐标推算；UV 器件 + universal_junction 转化为 CP-SAT 变量与线性约束；routing_class 三档分流。
2. **Phase 1 SA**：仅对 UV 器件做粗放置（沿宿主 microstrip 等距初值 + Metropolis 微调），用引力场吸附 anchor_pin 邻位。
3. **Phase 2 CP-SAT**：主求解阶段，承担长度锁、NoOverlap、manifold 几何模板、UV anchor 滑动。**真实案例预估 1–5 s 解出**。
4. **Phase 3 A***：仅对 `flexible_path` 边求解。本案例为空，跳过；保留实现以兼容含 `trace` 的输入。
5. **Postproc**：bend_style 几何、DRC、软 LVS、SVG/Gerber 输出。
6. **回滚**：CP-SAT infeasible 或 A* 失败 → 反馈 SA 抬温度，最多重试 5 轮。

---

## 3. v6 内部 IR 与 v3.3 字段映射

### 3.1 核心段

| v3.3 段 | 经 Frontend Compiler 后 | 说明 |
|---|---|---|
| `components.{C, placement: {x,y,rotation}}` (`is_floating: false`) | 写入 `terminals` 段：`C.PIN_k → (abs_x, abs_y)` | 由 footprint.pins.local_xy 经仿射变换推出 |
| `components.{C, placement: {type: parametric_uv, anchor_pin, reference_net}}` | 注册 UV 器件；引入变量 `(anchor_x, anchor_y, rotation, offset_v_side)`；其他 pin = anchor + R(rotation)·local_offset | D2：端点滑动模型 |
| `nodes.{N, type: universal_junction, connection_rules.branches[]}` | 节点中心 `(cx, cy)` 作为 IntVar；每分支派生线性约束 `branch_end = center + R(angle)·(offset_u, signed_v)` | D4 |
| `nodes.{N, type: t_combiner_junction}` | 重命名为 `t_junction`（语义等价：多输入合一）| D4 别名 |
| `edges.{E, type: microstrip, target_length: ...}` | `routing_class: rf_constrained_locked` | D3 |
| `edges.{E, type: microstrip, no target_length}` | `routing_class: rf_constrained_free`（仅参与 NoOverlap）| D3 |
| `edges.{E, type: trace}` | `routing_class: flexible_path`（A* 处理）| 同 v4 |

### 3.2 v6 节点类型表（替换 v4 §2.3）

| `type` | 用途 | 坐标决定方式 | 来源 |
|---|---|---|---|
| `t_junction` | RF 主干分叉 | 与左右子段联立解 | v3.3 + v4 |
| `t_combiner_junction` | 多输入合一节点 | 同 t_junction（v6 视为别名）| v3.3 |
| `pad_junction` | 普通中转点 | 由邻接边几何确定 | v4 (≡ v3.3 universal_node) |
| `stepped_impedance` | 阻抗阶跃变径 | 同上 + custom_offset | v3.3 + v4 |
| **`universal_junction`** ⭐ | **多分支 manifold 模板** | center 为 IntVar；branch 端点 = center + R(angle)·(u, signed_v) | **v3.3（D4 新增）**|
| `floating_shunt_tap` | 浮动并联吸附点 | SA 引力场 | v4 |
| `component_pad_junction` | 串/并器件吸附点（v4 兼容路径）| UV 解析后 = component anchor pin 坐标 | v4（D2 改用 UV 模型）|

### 3.3 v6 routing_class 三档（替换 v4 §2.5）

| 推断条件 | routing_class | 求解器 | 长度约束 |
|---|---|---|---|
| `microstrip` ∧ 给定 `target_length` | `rf_constrained_locked` | CP-SAT 长度锁 + NoOverlap | `target_length` ±tol |
| `microstrip` ∧ 缺省 `target_length` | `rf_constrained_free` | CP-SAT 仅 NoOverlap | 自由 |
| `trace` | `flexible_path` | A* 迷宫绕障 | 无 |

---

## 4. UV 解析（D2 形式化）

对每个 `parametric_uv` 器件 `c`：

1. **host_edge 匹配**：在 `c.placement.reference_net` 对应的所有 microstrip edges 中，筛选 `c.{anchor_pin}` 出现在 `connections` 端点的 edge。
2. **三种情形**：
   - **正常**（本案例 100% 适用）：恰好命中 1 条 → 锁定为 `host_edge`，无需选择变量。
   - **歧义**：≥ 2 条 → 引入 `host_edge_choice BoolVar[]` + `AddExactlyOne`，由 CP-SAT 决策。
   - **失配**：0 条 → 反向构造 anonymous microstrip 作 host，target_length 自由；记入 lint_report。
3. **变量与约束**：
   ```
   anchor_x, anchor_y                    ∈ host_edge 主方向 [0, host_length]
   rotation                              ∈ {0, 90, 180, 270}
   offset_v_side                         ∈ {+1, -1}    BoolVar
   anchor_pin_pos = (anchor_x, anchor_y) + offset_v_side · (W_host/2 + clearance) · n̂_host
   pin_k_pos = anchor_pin_pos + R(rotation) · (footprint.local[k] - footprint.local[anchor])
   ```
4. **NoOverlap 障碍**：`c` 的 footprint bbox 在 anchor + rotation 下进入 NoOverlap 集合。

---

## 5. universal_junction 求解模板（D4 形式化）

对节点 `N`，其 `connection_rules.branches[]` 给出：

```
变量: center_x[N], center_y[N]   ∈ [0, board_w] × [0, board_h]

对每个 branch b ∈ branches:
  设 t = b.edge 的另一端坐标 (target)
  设 (du, dv) = (b.origin.offset_u, signed_v(b.origin.offset_v))
  设 (cosθ, sinθ) = (cos(b.angle), sin(b.angle))    # 预计算为常量

  约束 (anchor):
    target_x[t] == center_x[N] + cosθ·du - sinθ·dv
    target_y[t] == center_y[N] + sinθ·du + cosθ·dv

  其中 signed_v(edge_left)    = +W/2 + clearance
       signed_v(edge_right)   = -W/2 - clearance
       signed_v(align_center) = 0
       (W = b.edge.constraint.width, clearance = global_constraints.routing.default_clearance)
```

**注**：v3.3 angle 单位为度；预计算后系数为常量；CP-SAT 用整数线性约束；非 90° 倍数旋转用 1µm 离散后近似。`semantic_intent` 字段（如 `[stepped_impedance, manifold_junction]`）当前版本仅作为后处理 EM 仿真标记，不参与求解。

---

## 6. CP-SAT 约束矩阵（v6）

| 约束 | OR-Tools 原语 | 适用范围 |
|---|---|---|
| RF 段长度（locked） | `AddAbsEquality` + `AddLinearExpression`（±tol） | `rf_constrained_locked` |
| RF 段长度（free） | — | `rf_constrained_free` 不施加长度 |
| universal_junction 几何 | `model.Add(...)` 线性方程 | 每分支 1 对 (x, y) 等式 |
| UV anchor 滑动 | IntVar 域 + 派生 pin 坐标线性表达 | 每个 UV 器件 |
| 无交叉 | `AddNoOverlap` + `AddBoolOr(x_sep, y_sep)` | 所有 RF 边矩形 + UV/fixed component footprint + keepout |
| 阶跃阻抗偏移 | 节点 `custom_offset` → 线性约束 | `stepped_impedance` |
| 板框边界 | 变量域裁剪 | 所有节点变量 |
| 过孔密度 | `AddCumulative` | shunt via |

无交叉实现（v4 沿用，与 `concepts/routing-algorithm-comparison.md` §3.2 谓词方向一致）：

```python
def add_pairwise_no_overlap(model, e_a, e_b):
    bbox_a, bbox_b = get_path_bbox(e_a), get_path_bbox(e_b)
    x_sep = model.NewBoolVar(f"xsep_{e_a.name}_{e_b.name}")
    y_sep = model.NewBoolVar(f"ysep_{e_a.name}_{e_b.name}")
    model.Add(bbox_a.max_x + e_a.width/2 <= bbox_b.min_x).OnlyEnforceIf(x_sep)
    model.Add(bbox_b.max_x + e_b.width/2 <= bbox_a.min_x).OnlyEnforceIf(x_sep)
    model.Add(bbox_a.max_y + e_a.width/2 <= bbox_b.min_y).OnlyEnforceIf(y_sep)
    model.Add(bbox_b.max_y + e_b.width/2 <= bbox_a.min_y).OnlyEnforceIf(y_sep)
    model.AddBoolOr([x_sep, y_sep])
```

---

## 7. Phase 1 SA（v6 能量函数）

```python
def compute_energy_v6(placement, ir):
    return (alpha   * compute_hpwl(placement, ir)
          + gamma   * compute_boundary_penalty(placement, ir.board_outline)
          + delta   * compute_thermal_penalty(placement, ir.power_devices)
          + zeta    * compute_uv_anchor_attract(placement, ir))   # v6: UV 取代 floating_shunt_tap

def compute_uv_anchor_attract(placement, ir):
    """对每个 UV 器件，把 anchor_pin 拉向其 host_edge 主方向，并对超出 [0, host_length] 的位置惩罚。"""
    E = 0.0
    for uv in ir.uv_components:
        host = ir.edges[uv.host_edge]
        u_along = project_onto(placement[uv.name].anchor, host)
        if u_along < 0 or u_along > host.length:
            E += zeta_oob * (clamp(u_along, 0, host.length) - u_along) ** 2
    return E
```

> **v4 → v6 差异**：`floating_shunt_tap` 引力场（`attract_to_target` / `space_available`）被 UV 器件的 anchor 吸附逻辑取代；前者作为 v4 兼容路径保留，但真实案例不触发。

详见 [`concepts/placement-problem-formulation.md`](./concepts/placement-problem-formulation.md)。

---

## 8. Phase 3 A*（保留兼容）

灵活偏置线（`type: trace`）走 A* 迷宫绕障，cost = 路径段数 + λ·拐弯惩罚 + μ·靠近 RF 边惩罚。
本案例无 `trace` 边，本阶段跳过；实现保留以兼容未来含 trace 输入。

详见 [`concepts/routing-algorithm-comparison.md`](./concepts/routing-algorithm-comparison.md)。

---

## 9. 真实案例求解规模实测预估

`PA_Module_Simplified` (40 × 100 mm 单层板) 经 Frontend Compiler 展开后：

| 项 | 数值 |
|---|---|
| Components: fixed (IC1+TP1-5) / UV | 6 / 9 |
| Terminals (含 GND) | 28 |
| Nodes: universal / t_junction / t_combiner | 2 / 4 / 2 |
| Edges: rf_locked / rf_free / flex | 14 / 8 / 0 |
| CP-SAT IntVar | ≈ 60 |
| CP-SAT BoolVar | ≈ 250 |
| 长度约束 | 14 |
| universal_junction 几何约束 | 2 × 4 = 8 |
| **预估求解时间（单线程 OR-Tools）** | **1–5 s** |

→ **方案在本案例下完全可解**；远低于 v5 的 5–30 s 估算。规模可作为 M3–M6 的端到端冒烟基线。

---

## 10. 风险登记摘要

| ID | 风险 | 缓解 |
|---|---|---|
| R1 | universal_junction 含非 90° angle 时整数近似误差 | 1µm 离散；误差 < 1‰ 在 DRC 容忍内 |
| R2 | UV host_edge 失配 | 反向构造 anonymous host edge + lint warning，不阻塞 |
| R3 | rf_constrained_free 短段被挤穿 | NoOverlap inflate 用 width + 2·clearance；infeasible 反馈 SA 抬温度 |
| R4 | NoOverlap 仅轴对齐，对斜段近似 | 斜段拆为多个轴对齐子矩形（v4 现做法）|
| R5 | 高密度 UV 时 CP-SAT 启发退化 | num_workers + portfolio search；超时降级"先 SA 固定 placement，再 CP-SAT 仅解节点" |
| R6 | typo 修复表覆盖不全 | lint_report 累积统计 + 月度回顾；未知字段透传不阻塞 |
| R7 | universal_junction 多语义 `semantic_intent` | 当前版本仅按几何模板求解；语义留给后处理 EM 仿真 |
| R8 | 未来引入 multipoint_net / logical_net | M5 已搭好 A* 与 LVS 接口，预留 FLUTE 集成钩子 |

完整 ADR 见 [`ITERATION-PLAN.md`](./ITERATION-PLAN.md) §5。

---

## 11. 文件索引

| 文件 | 内容 |
|---|---|
| `README.md` | v6 入口与速览 |
| `ALGORITHM-OVERVIEW.md` | **总体架构 v6**（本文档）|
| `ITERATION-PLAN.md` | v6 算法方案 + 6+1 期开发计划 + 风险登记 + ADR |
| `射频微波版图结构化数据规范 (v3.3).md` | 输入数据规范（外部 EDA 接口）|
| `rf_layout_simplified.yaml` | 真实案例 (PA_Module_Simplified)，所有期次的回归基线 |
| `concepts/placement-problem-formulation.md` | SA + 引力场 + UV anchor 吸附（v6 适配）|
| `concepts/routing-algorithm-comparison.md` | CP-SAT + A* 双求解器实现（v6 适配）|
| `concepts/microstrip-topology-matching.md` | 微带类型 / bend_style / 阶跃阻抗 / universal_junction 几何（v6 适配）|
