# 功放 PCB 自动布局布线引擎 v6

> **基于真实案例 `rf_layout_simplified.yaml` 验证后的最终算法方案**
>
> 输入规范：v3.3（外部 EDA 接口） · 内部 IR：v6 · 求解器三阶段：SA + CP-SAT + A*

---

## 0. 文档定位与版本

本仓库是一个 **设计文档 wiki**（暂无实现代码）。它描述：

1. 上游 EDA 工具产出的 v3.3 YAML 数据规范；
2. 后端布局布线引擎应当采用的算法方案；
3. 落地的 6+1 期迭代开发计划。

| 版本 | 状态 | 说明 |
|---|---|---|
| v3 | 已废弃 | 单求解器 CP-SAT，变量爆炸 |
| v4 | 内部 IR 基线 | 引入三求解器 + 拓扑分裂 + 浮动器件引力场 |
| v5 | 草案，已废弃 | 在 v4 基础上接入 v3.3，**未经真实案例验证** |
| **v6** | **当前最终方案** | 基于 `rf_layout_simplified.yaml` 真实案例修正 5 个结构性决策 |

---

## 1. v5 → v6 的 5 个结构性修正

把真实案例 `rf_layout_simplified.yaml` (PA_Module_Simplified) 与 v5 草案 1:1 对照后，发现 8 项假设破产/新发现，归并为 5 个必须落地的修正：

| ID | v5 假设 | v6 决策 | 触发依据 |
|---|---|---|---|
| **D1** | RLC 翻译为 `lumped_*` edge | RLC = component-with-pads，pin 直接进 `terminals`；`lumped_*` 翻译层作废 | 案例 9 个 RLC 全是 `parametric_uv` |
| **D2** | UV = "中段插入分裂" | UV = **anchor_pin 沿宿主 microstrip 端点滑动** | 案例 anchor_pin 总是 edge 端点 |
| **D3** | `routing_class` 二分法 | 三档：`rf_constrained_locked` / `rf_constrained_free` / `flexible_path` | 案例 8/22 RF 段无 `target_length` |
| **D4** | 节点 5 种 | 新增 **`universal_junction`** 多分支几何模板节点 | 案例两个核心功分点都用此类型 |
| **D5** | Schema 严格 + LVS 强阻塞 | Schema Lint 容错 + LVS 软警告 | 案例有 typo / 未知字段 / 无 logical_net |

详细推导见 [`ITERATION-PLAN.md`](./ITERATION-PLAN.md) §1–§2。

---

## 2. v6 总体管线

```
v3.3 YAML  (rf_layout_simplified.yaml)
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  ① Frontend Compiler                                    │
│   · Schema Lint        (typo 修复 + 未知字段透传)       │
│   · expand_components  (footprint × placement → pad 坐标)│
│   · UV Resolve         (anchor_pin 锚定到宿主 edge 端点) │
│   · universal_junction (branches[] → 线性几何约束)      │
│   · routing_class triage (三档分类)                     │
└─────────────────────────────────────────────────────────┘
    │ 内部 IR (v6)
    ▼
┌────────────────────┐  ┌──────────────────┐  ┌─────────────────┐
│ ② Phase 1 SA       │→ │ ③ Phase 2 CP-SAT │→ │ ④ Phase 3 A*    │
│ UV 器件粗放置      │  │ 主求解器         │  │ flexible_path   │
│ + 引力场 + HPWL    │  │ 长度锁 + NoOverlap│  │ 迷宫绕障         │
│ + 5 次重试抬温度   │  │ + manifold 几何  │  │ (本案例为空)    │
└────────────────────┘  └──────────────────┘  └─────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  ⑤ Postproc                                             │
│   · bend_style 渲染 (mitered_45 / curved / square / arc) │
│   · 软 LVS (有 logical_net 才生效)                      │
│   · DRC (min_width / clearance / via_density)           │
│   · 输出 SVG + Gerber/GDS stub                          │
└─────────────────────────────────────────────────────────┘
```

---

## 3. v6 节点类型表

| `type` | 用途 | 坐标决定方式 | 来源 |
|---|---|---|---|
| `t_junction` | RF 主干分叉 | 与左右子段联立解 | v3.3 + v4 |
| `t_combiner_junction` | 多输入合一节点 | 同 t_junction | v3.3（新别名）|
| `pad_junction` | 普通中转点 | 由邻接边几何确定 | v4 |
| `stepped_impedance` | 阻抗阶跃变径 | 同上 + custom_offset | v3.3 + v4 |
| **`universal_junction`** ⭐ | **多分支 manifold 模板** | center 为 IntVar；branch 端点 = center + R(angle)·(offset_u, signed_v) | **v3.3（D4 新增）**|
| `floating_shunt_tap` | 浮动并联吸附点 | SA 引力场 | v4 |
| `component_pad_junction` | 串/并器件吸附点 | UV 解析后 = component anchor pin 坐标 | v4（D2 改用 UV 模型）|

---

## 4. v6 routing_class 三档

| 推断条件 | routing_class | 求解器 | 长度约束 |
|---|---|---|---|
| `microstrip` ∧ 有 `target_length` | `rf_constrained_locked` | CP-SAT 长度锁 + NoOverlap | `target_length` ±tol |
| `microstrip` ∧ 无 `target_length` | `rf_constrained_free` | CP-SAT 仅 NoOverlap | 自由 |
| `trace` | `flexible_path` | A* 迷宫 | 无 |

---

## 5. 真实案例求解规模预估

`PA_Module_Simplified` (40×100 mm 单层板)：

| 项 | 值 |
|---|---|
| Components: fixed (IC1+TP1-5) / UV | 6 / 9 |
| Terminals (含 GND) | 28 |
| Nodes: universal / t_junction / t_combiner | 2 / 4 / 2 |
| Edges: rf_locked / rf_free / flex | 14 / 8 / 0 |
| CP-SAT IntVar / BoolVar / 长度约束 | ≈60 / ≈250 / 14 |
| **预估求解时间（单线程 OR-Tools）** | **1–5 s** |

→ **方案在本案例下完全可解**，远优于 v5 估算（5–30 s）。本案例自始至终作为各期 DoD 的回归基线。

---

## 6. 真实案例 v3.3 YAML 速览

```yaml
# 主芯片：绝对坐标
components:
  IC1:
    footprint_ref: "PKG_IC_4PIN"
    placement: { x: 12.1, y: 50.0, rotation: 0, is_floating: false }
    pin_nets: { PIN_1: RF_NET_1, PIN_2: RF_NET_2, PIN_3: PWR_VDD, PIN_4: PWR_VDD }

# 匹配电容：parametric_uv（吸附在 RF_NET_1 上）
  C1:
    footprint_ref: "PKG_CAP"
    placement:
      type: parametric_uv
      anchor_pin: PIN_1
      reference_net: "RF_NET_1"
      origin: { offset_u: null, offset_v: auto }
    pin_nets: { PIN_1: RF_NET_1, PIN_2: RF_NET_3 }

# universal_junction：多分支 manifold 模板（v6 D4 触发点）
nodes:
  IC1_pin1_seg1_universal_node:
    type: universal_junction
    semantic_intent: [stepped_impedance, manifold_junction]
    connection_rules:
      reference_edge: IC1_pin1_seg1
      branches:
        - edge: IC1_pin1_seg2
          angle: 90
          origin: { offset_u: -3.94, offset_v: edge_left }
        - edge: IC1_pin1_seg3
          angle: 90
          origin: { offset_u: -1.6,  offset_v: edge_left }
        - edge: IC1_pin1_seg4
          angle: 0
          origin: { offset_u: 0,     offset_v: align_center }

# 长度锁定 RF 段（rf_constrained_locked）
edges:
  IC1_pin1_seg1:
    type: microstrip
    net: RF_NET_1
    routing_class: rf_constrained
    connections: [IC1.PIN_1, IC1_pin1_seg1_universal_node]
    constraint: { width: 3.6, target_length: 5.75 }
    geometry:   { bend_style: mitered_45 }

# 自由长度 RF 段（rf_constrained_free，v6 D3 触发点）
  IC1_pin1_seg2_to_R2:
    type: microstrip
    net: RF_NET_1
    routing_class: rf_constrained
    connections: [IC1_pin1_seg2_end_split_pad, R2.PIN_2]
    geometry: { bend_style: mitered_45 }
```

---

## 7. 文件结构

```
.
├── README.md                                       # 本文档（v6 入口）
├── ALGORITHM-OVERVIEW.md                           # v6 算法总览
├── ITERATION-PLAN.md                               # v6 算法方案 + 6+1 期开发计划
├── 射频微波版图结构化数据规范 (v3.3).md            # 输入数据规范（外部 EDA 接口）
├── rf_layout_simplified.yaml                       # 真实案例 (PA_Module_Simplified)
└── concepts/
    ├── placement-problem-formulation.md            # SA + 引力场（v6 适配）
    ├── routing-algorithm-comparison.md             # CP-SAT + A* 双求解器（v6 适配）
    └── microstrip-topology-matching.md             # 微带 / bend_style / universal_junction 几何
```

阅读路径：
- **新读者**：本 README → `ALGORITHM-OVERVIEW.md` → `ITERATION-PLAN.md`
- **算法实现者**：`ITERATION-PLAN.md` §2–§3 → 三个 concept 文档
- **数据生产者**：`射频微波版图结构化数据规范 (v3.3).md` + `rf_layout_simplified.yaml`

---

## 8. 6+1 期迭代计划速览

| 期 | 周 | 目标 | 关键交付 |
|---|---|---|---|
| **M0** | 0.5 | 拓扑可视化基线 | `rf_layout_simplified.yaml` → SVG |
| **M1** | 1   | 脚手架 + 双 schema | v33 (lenient) / v6 IR (strict) |
| **M2** | 2   | Frontend Compiler | Lint + expand_components + triage |
| **M3** | 2   | UV + universal_junction | 端点滑动模型 + 几何模板（最高风险）|
| **M4** | 2   | CP-SAT 主求解器 | 真实案例端到端 ≤ 10 s |
| **M5** | 1.5 | SA + A* 兼容能力 | 三阶段流水线 + 5 次重试 |
| **M6** | 1   | 后处理 + 输出 | bend / DRC / 软 LVS / SVG / Gerber stub |

详见 [`ITERATION-PLAN.md`](./ITERATION-PLAN.md) §4。
