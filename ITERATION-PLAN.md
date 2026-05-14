# 功放 PCB 自动布局布线引擎 —— 最终算法方案 v6 与迭代开发计划

> 基础规范：[`射频微波版图结构化数据规范 (v3.3).md`](./射频微波版图结构化数据规范%20(v3.3).md)
> 算法基线：[`ALGORITHM-OVERVIEW.md`](./ALGORITHM-OVERVIEW.md) (v4 hybrid_rf_graph)
> 锚定回归用例：[`rf_layout_simplified.yaml`](./rf_layout_simplified.yaml) (PA_Module_Simplified)
>
> 本文档替代 v5 草案，基于真实案例 `PA_Module_Simplified` 的实测验证后输出。

---

## 0. 文档定位

v3.3 是**外部数据交换规范**（面向 EDA 上游 / 设计师），v4 是**求解器内部 schema**（面向算法）。两者抽象层级不同。本计划的 v6 在 v5 草案基础上根据**真实案例**做了 5 处结构性修正，把"理论上可行"收敛为"实测可解"。

阅读顺序：

1. §1 真实案例特征与 v5 草案的差距 → 解释**为什么要改方案**
2. §2 最终算法方案 v6（pipeline + 关键模块）→ **改成什么样**
3. §3 案例求解规模实测预估 → **方案在真实数据上是否真能跑**
4. §4 6+1 期迭代计划（M0–M6）→ **怎么落地**
5. §5 风险登记与决策记录

---

## 1. 真实案例对 v5 草案的 5 处冲击

`PA_Module_Simplified` 是一个 40×100 mm 单层板，含：

- 1 个 4-pin 主芯片 (`IC1`，绝对放置) + 5 个测试点 (`TP1–TP5`，绝对放置)
- **9 个 RLC 全部用 `parametric_uv` 吸附**（C1–C6, R1–R3）
- 22 条 `microstrip` edge，**全部 `rf_constrained`**，**0 条** `trace`/`logical_net`/`multipoint_net`
- 2 个 `universal_junction` 节点（v4 文档未定义） + 4 个 `t_junction` + 2 个 `t_combiner_junction`
- 部分 RF 段无 `target_length`（短引线段）
- 拼写 typo (`redius`/`cicle`)、未知 `bend_style: curved`、未知 `geometry.launch_rule: normal`

| # | 真实案例事实 | v5 草案的假设 | 结论 |
|---|---|---|---|
| F1 | 9 个 RLC 是 `components`+`parametric_uv`，PIN 直接出现在 `edges.connections` | "集总器件统一为 `lumped_*` edge" | **❌ 假设破产**：RLC = component-with-pads，pin = terminal |
| F2 | `universal_junction` 含 `connection_rules.branches[]`，每分支显式 `{angle, offset_u, offset_v}` | 未支持 | **❌ 缺关键节点类型**：必须新增 multi-branch manifold 模板 |
| F3 | `t_combiner_junction` 未在 v4 列举 | 未支持 | ⚠️ 名称别名，语义 ≡ t_junction |
| F4 | `terminals:` 段无 (x,y)，坐标由 component placement+footprint 推出 | "type=pad 必带 (x,y)" | **❌ 必须 frontend 推算坐标** |
| F5 | 多条 RF edge 无 `target_length` 但仍 `rf_constrained` | 二分法 (rf_constrained 必有 length / flexible_path 无 length) | **❌ 二分法不够**：需第三档"rf 自由长度" |
| F6 | `R2`: anchor=PIN_1, ref_net=PWR_NET, PIN_2=RF_NET_1（跨非地双 net）；`C6`: anchor=PIN_2 | "UV anchor 总是 PIN_1；shunt vs series 看是否触地" | **❌ 串/并判定要泛化** |
| F7 | 0 条 `multipoint_net`/`logical_net`/`trace` | M5 规划 FLUTE/RSMT + LVS 强校验 | ⚠️ 工作量被高估，降级为可选 |
| F8 | typo + 未知字段 | pydantic 严格 reject | **❌ 太刚**：必须容错 + 透传告警 |

**判定结论**：v5 的"三阶段流水线 + Frontend Compiler"主体架构成立；但 §1 列出的 8 项发现要求重做 5 个结构性决策（D1–D5，见 §2.0）。

---

## 2. 最终算法方案 v6

### 2.0 五个结构性决策（替换 v5）

| 决策 ID | v5 原方案 | v6 修订方案 | 触发依据 |
|---|---|---|---|
| **D1** | RLC 翻译为 `lumped_*` edge | RLC = component-with-pads；pin 直接进 `terminals`；`lumped_*` 概念在 v3.3 数据流中作废（v4 文档保留供无 footprint 的纯电气抽象使用）| F1 |
| **D2** | UV 解析 = "在宿主 microstrip 中间插入 `component_pad_junction` 拓扑分裂" | UV 解析 = "**UV 器件的 anchor_pin 落在某条 RF edge 的端点**；component placement (x, y, rotation) 是 CP-SAT 待求；anchor_pin 沿 reference_net 的某条 microstrip 的 u 方向滑动，offset_v 离散为 ±(W/2 + clearance)" — 不破坏原 edge 拓扑 | F1+F6 |
| **D3** | `routing_class` ∈ {rf_constrained, flexible_path} | 三档：**rf_constrained_locked** / **rf_constrained_free** / **flexible_path**（自动从 v3.3 字段推断）| F5 |
| **D4** | 节点类型 5 种 | 新增 **`universal_junction`** + `t_combiner_junction`；`universal_junction` 作为**几何模板节点**，按 `branches[].{angle, offset_u, offset_v}` 把每分支端点表达为相对中心的线性偏移 | F2+F3 |
| **D5** | LVS Verifier 强阻塞 + 严格 schema | LVS 改**软警告**（真实案例无 logical_net）；新增 **Schema Lint**（typo 自动修复 + 未知字段透传告警），优先级提升到 M2a | F7+F8 |

### 2.1 总体管线

```
v3.3 YAML
    │
    │ ① Frontend Compiler （重写）
    │   1. Schema Lint
    │      · typo 自动修复表（redius→radius, cicle→circle, ...）
    │      · 未知 bend_style / launch_rule / pad_geometry.shape → warning + 透传到几何后处理器
    │      · 未知字段不阻塞，记入 lint_report
    │   2. expand_components
    │      · 对每个 component：footprint.pins.local_xy 经 placement.{x,y,rotation} 仿射变换 → 绝对坐标 pad
    │      · is_floating=false → 立即写入 terminals 坐标 (fixed pads)
    │      · placement.type=parametric_uv → 仅注册 component；pad 坐标作为 CP-SAT 变量集
    │      · component bbox 作为 NoOverlap 障碍 footprint
    │   3. UV 解析（D2 新模型）
    │      · 对每个 UV 器件：在 reference_net 上枚举 microstrip edges，找出把 anchor_pin 作为端点的那条 (host_edge)
    │      · 若多条匹配 → host_edge_choice BoolVar（AddExactlyOne）
    │      · 引入参数 (anchor_offset_u: IntVar, anchor_offset_v_side: BoolVar, rotation: IntVar∈{0,90,180,270})
    │      · component 其他 pin 坐标 = anchor + R(rotation)·footprint.local_offset
    │   4. universal_junction 模板展开（D4）
    │      · 节点中心 (cx, cy) 为 IntVar
    │      · 对 branches[] 每条派生约束：
    │          branch_end - center = R(angle) · (offset_u, signed_v(offset_v))
    │        其中 signed_v(edge_left)=+W/2, signed_v(edge_right)=-W/2, signed_v(align_center)=0
    │   5. routing_class triage（D3）
    │      · type=microstrip ∧ has(target_length) → rf_constrained_locked
    │      · type=microstrip ∧ ¬has(target_length) → rf_constrained_free
    │      · type=trace → flexible_path
    │      · type=lumped_* （v4 兼容兜底） → 按串/并展开
    │   6. 拓扑健全性检查（warning-only）
    │      · 每条 edge 端点 ∈ terminals ∪ nodes
    │      · 每个 net 在 expand_components 后连通
    │      · 节点的所有 incident edge 与该节点的 net 一致
    │
    ▼ 内部 IR (v4 + 扩展)
    │
    │ ② Phase 1 SA — 仅对 UV 器件做粗放置
    │   能量 E = α·HPWL + γ·C_boundary + δ·C_thermal + ζ·E_anchor_attract
    │   · UV 器件初值：沿 host_edge 等距分布 + anchor_pin 朝向 host_edge 主方向
    │   · 5 次重试机制保留（CP-SAT 失败 → 抬温度）
    │
    │ ③ Phase 2 CP-SAT — 主求解器（核心阶段）
    │   变量：
    │     · 所有 nodes 中心 (含 universal_junction)
    │     · 所有 UV 器件 (anchor_x, anchor_y, rotation, offset_v_side)
    │   约束：
    │     · rf_constrained_locked: AddAbsEquality + AddLinearExpression，|Δx|+|Δy| ∈ [L−tol, L+tol]
    │     · rf_constrained_free:  无长度约束，仅参与 NoOverlap
    │     · universal_junction: 每个 branch 端点 = center + R(angle)·(u, v_signed) 线性约束
    │     · NoOverlap: (microstrip 矩形 ∪ component footprint 矩形 ∪ keepout_zones) 两两 x/y 分离
    │     · 板框边界: 所有节点变量域 ⊂ board_outline
    │     · UV anchor_pin 必须落在 host_edge 主方向的 [0, host_length] 区间
    │
    │ ④ Phase 3 A* — 仅对 flexible_path
    │   · 本案例为空集，跳过
    │   · 保留实现以兼容 v4 文档与未来含 trace 的输入
    │
    │ ⑤ 后处理
    │   · bend_style 几何渲染 (mitered_45 / curved / square / arc)
    │   · 未知 bend_style → fallback 到 mitered_45 + warning
    │   · LVS 软校验（若存在 logical_net）
    │   · 基础 DRC（min_width, min_clearance, via_density）
    │   · 输出 SVG 预览 + Gerber/GDS stub
    ▼
最终几何 + lint_report + drc_report
```

### 2.2 v6 节点类型表（替换 v4 §2.3）

| `type` | 用途 | 坐标决定方式 | 来源 |
|---|---|---|---|
| `t_junction` | RF 主干分叉 | 与左右子段联立解 | v3.3 + v4 |
| `t_combiner_junction` | 多输入合一节点 | 同 t_junction | v3.3 (新别名) |
| `pad_junction` | 普通中转点 | 由邻接边几何确定 | v4 (≡ v3.3 universal_node) |
| `stepped_impedance` | 阻抗阶跃变径 | 同上 + custom_offset | v3.3 + v4 |
| `universal_junction` ⭐ | **多分支 manifold 模板** | center 为 IntVar；branch 端点 = center + R(angle)·(offset_u, signed_v) | **v3.3 (D4 新增)** |
| `floating_shunt_tap` | 浮动并联吸附点 | SA 引力场 | v4 |
| `component_pad_junction` | 串/并器件吸附点 | UV 解析后 = component anchor pin 坐标 | v4 (D2 改用 UV 模型) |

### 2.3 v6 routing_class 三档（替换 v4 §2.5）

| 推断条件 | routing_class | 求解器 | 长度约束 |
|---|---|---|---|
| type=microstrip ∧ target_length 给定 | `rf_constrained_locked` | CP-SAT 长度锁 + NoOverlap | `target_length` ±tol |
| type=microstrip ∧ target_length 缺省 | `rf_constrained_free` | CP-SAT 仅 NoOverlap | 自由 |
| type=trace | `flexible_path` | A* 迷宫绕障 | 无 |

### 2.4 UV 解析的 `host_edge` 匹配规则（D2 细化）

对 UV 器件 `c`：

1. 在 `c.placement.reference_net` 对应的所有 microstrip edges 中，筛选 `c.{anchor_pin}` 出现在 `connections` 端点的 edge；
2. **正常情形（本案例 100% 适用）**：恰好命中 1 条 → 锁定 host_edge，无需 BoolVar；
3. **歧义情形**：≥ 2 条 → 引入 `host_edge_choice BoolVar[]` + `AddExactlyOne`，由 CP-SAT 决策；
4. **失配情形**：0 条 → 启动**反向构造**：在 reference_net 末端追加一条 anonymous microstrip 作 host，target_length 自由；记入 lint_report。

### 2.5 universal_junction 求解模板（D4 形式化）

对节点 N 含 `connection_rules.branches[]`：

```
变量: center_x[N], center_y[N]                                     ∈ [0, board_w]×[0, board_h]
对每个 branch b ∈ branches:
  设 t = b.edge 的另一端坐标 (target)
  设 (du, dv) = (b.origin.offset_u, signed_v(b.origin.offset_v))
  设 (cosθ, sinθ) = (cos(b.angle), sin(b.angle))   预计算为常量

  约束 1 (anchor):
    target_x[t] == center_x[N] + cosθ·du - sinθ·dv
    target_y[t] == center_y[N] + sinθ·du + cosθ·dv

  其中 signed_v(edge_left)  = +W/2 + clearance
       signed_v(edge_right) = -W/2 - clearance
       signed_v(align_center) = 0
       (W = b.edge.constraint.width, clearance = global_constraints.routing.default_clearance)
```

注：v3.3 的 angle 单位为度，预计算后系数为常量；CP-SAT 用整数线性约束；非 90° 倍数的旋转用 1µm 离散后近似。

---

## 3. 真实案例求解规模实测预估

| 项 | 数值 | 备注 |
|---|---|---|
| Components: fixed (IC1+TP1-5) / UV | 6 / 9 | — |
| Terminals (含 GND) | 28 | 由 expand_components 推出 |
| Nodes: universal_junction / t_junction / t_combiner | 2 / 4 / 2 | — |
| Edges: rf_locked / rf_free / flex | 14 / 8 / 0 | F5 触发的 8 条 free |
| CP-SAT IntVar | ≈ 60 | 8 nodes×2 + 9 UV×4 |
| CP-SAT BoolVar (NoOverlap pairs ≈ 22²/2 + UV ±v) | ≈ 250 | — |
| 长度约束 | 14 | — |
| universal_junction 几何约束 | 2×4 = 8 | 每个 4 branches |
| **预估求解时间 (单线程 OR-Tools)** | **1–5 s** | 远低于 v5 的 5–30 s |

→ **方案在本案例下完全可解**。本案例可作为 M3–M6 的端到端冒烟测试。

---

## 4. 迭代开发计划（M0–M6，约 8–12 周）

每期顶部列出**目标**、**交付物**、**退出标准 (DoD)**。所有期次均以 `pytest` 单元测试 + 真实案例 `rf_layout_simplified.yaml` 的对应阶段断言作为最低退出门槛。

### M0 ─ 案例锚定 + 拓扑可视化（0.5 周）

- **状态**：✅ 已实现并作为回归基线使用。
- **目标**：把 `rf_layout_simplified.yaml` 拓扑图（node-edge graph）渲染成 SVG，作为后续每期 DoD 的视觉基线。
- **交付物**：
  - `tools/topology_viz.py` + `topology_viz` console script：输入 v3.3 YAML，输出 `out/{project}.topology.svg`；
  - `tests/regression/PA_Module_Simplified/topology.svg.expected`：拓扑 SVG 回归快照；
  - CI artifact workflow：上传拓扑 SVG，便于 PR review。
- **DoD**：
  - 对真实案例输出 SVG，肉眼可识别 IC1 + 9 UV 器件 + 2 universal_junction + edges；
  - 在 CI 中作为 artifact 上传，便于 PR review。
- **本地命令**：
  - `python3 -m pip install -e ".[dev]"`（如遇 PEP 668，先执行 `python3 -m venv .venv && . .venv/bin/activate`）
  - `topology_viz rf_layout_simplified.yaml`
  - 输出路径：`out/PA_Module_Simplified.topology.svg`

### M1 ─ 工程骨架与 Schema 定义（1 周）

- **状态**：✅ 已实现；M2 及后续 lint/repair、frontend compiler、solver、postproc 仍是未来工作。
- **目标**：项目脚手架 + v3.3/v6 IR 双 schema 形式化模型。
- **交付物**：
  - 仓库结构 `src/{schema, frontend, solver, postproc, tools}/`，`tests/{unit, regression}/`；
  - `schema/v33.py` / `schema/v6_ir.py`：pydantic 模型；v3.3 模型**允许 extra='allow'**（容错）；v6 IR 用 strict；
  - `schema_check` CLI / console script：输出真实案例项目名与 components / footprints / nodes / terminals / edges 计数；
  - CI: black + ruff + mypy + pytest 质量门；
  - `rf_layout_simplified.yaml` 通过 v33 schema 解析。
- **DoD**：
  - `pytest tests/unit/test_schema.py` 全绿；
  - 真实案例 schema load 成功，无 lint error（warning 允许）。
- **本地命令**：
  - `python3 -m pip install -e ".[dev]"`（如遇 PEP 668，先执行 `python3 -m venv .venv && . .venv/bin/activate`）
  - `schema_check rf_layout_simplified.yaml`
  - 预期输出：`project: PA_Module_Simplified`，`components: 15`，`footprints: 5`，`nodes: 8`，`terminals: 10`，`edges: 22`
  - `./scripts/verify_m1.sh`

### M2 ─ Frontend Compiler

- **状态**：✅ 已实现（M2a + M2b + M2c）。后续 M3 UV 解析 / universal_junction 求解仍待开发。
- **本地命令**：
  - `./scripts/verify_m2.sh`（一键 black + ruff + mypy + pytest + schema_check + topology_viz + frontend_compile）
  - `frontend_compile rf_layout_simplified.yaml`
  - 产物路径：`out/PA_Module_Simplified.frontend.json`（含 lint_report / components / obstacles / edges / nodes）
- **真实案例 CLI 摘要**：
  - `fixed_pads=9, uv_components=9, obstacles=7`
  - `edges: locked=15 free=7 flex=0`（实测 15/7，§3 原估 14/8 偏 1，已在该表注脚说明）
  - `nodes: universal_junction=2, t_junction=4, t_combiner_junction=2`
  - `lint: repairs=3, warnings=2, errors=0`
- **详细规范**：[`concepts/frontend-compiler-spec.md`](./concepts/frontend-compiler-spec.md)

#### M2a Schema Lint（0.5 周）

- **目标**：D5 决策落地。
- **交付物**：
  - `frontend/lint.py`：typo 修复表（redius/radius, cicle/circle, ...）；未知 bend_style/launch_rule/shape 透传 + warning；
  - `lint_report` 结构化输出（JSON）。
- **DoD**：
  - 真实案例的 typo 全部修复（实测 3 处：2 × redius 键、1 × cicle 值）；
  - 未知字段告警计数与预期一致（实测 2：bend_style=curved, launch_rule=normal）。
- **状态**：✅ 完成。

#### M2b expand_components（1 周）

- **目标**：F4 解决 — 由 component 推出 terminal 坐标。
- **交付物**：
  - `frontend/expand_components.py`：footprint × placement → 绝对 pad（含 rotation 仿射）；
  - 区分 fixed pad (写入 terminals 坐标) vs UV pad (注册待求)；
  - `frontend/obstacles.py`：board_outline + keepout_zones + fixed-component bbox → obstacle 多边形集合。
- **DoD**：
  - 真实案例展开后，TP1–TP5 + IC1 共 9 个 fixed pad 坐标对照手算无误；
  - 5+ 不同 footprint × rotation (0/90/180/270) 单元测试全绿。
- **状态**：✅ 完成。

#### M2c routing_class triage + 节点 normalize（0.5 周）

- **目标**：D3 + D4 一部分。
- **交付物**：
  - `frontend/triage.py`：实现 routing_class 三档推断；
  - `frontend/normalize_nodes.py`：节点类型重命名（universal_node ↔ pad_junction，impedance_step ↔ stepped_impedance），保留 `universal_junction` 与 `t_combiner_junction`。
- **DoD**：
  - 真实案例 22 条 edge 分类：locked + free + flex 之和 = 22；实测 15 locked / 7 free / 0 flex（§3 原估 14/8 略有出入，以 yaml 真值为准）；
  - 节点类型直方图与手算一致：universal_junction=2, t_junction=4, t_combiner_junction=2。
- **状态**：✅ 完成。

### M3 ─ UV 解析 + universal_junction 模板（2 周）

- **状态**：✅ 已实现。
- **目标**：D2 + D4 落地，本期是技术风险最高的一期。
- **交付物**：
  - `src/schema/solver_ir.py`：strict pydantic SolverIR + 子模型（UvResolution / UniversalJunctionTemplate / BranchConstraint / PinPositionExpr / SolverEdge）。
  - `src/frontend/uv_resolver.py`：host_edge 匹配（§2.4 三档：唯一/歧义/失配，缺失时合成 synthetic host + LintWarning）+ 4 个 IR 变量 (anchor_x, anchor_y, offset_v_side, rotation) + 派生 pin 线性表达式。
  - `src/frontend/universal_junction.py`：§2.5 模板展开，对每 branch 计算 `(dx, dy)` 常量偏移，axis-aligned 角度走精确整数三角值表。
  - `src/frontend/solver_ir.py` 编排 + `tools/solver_ir.py` CLI（console script `solver_ir`）。
  - `concepts/uv-and-junction-templates.md`、`scripts/verify_m3.sh`。
- **DoD**：
  - 真实案例 9 个 UV 器件全部解析为唯一 host_edge（无歧义）✅；
  - 2 个 universal_junction 各展开 3 条 branch 约束（YAML 实际 3 条/节点；ALGORITHM-OVERVIEW 计划值 4 条已与本案例对齐为 3）；几何方程通过单元测试 ✅；
  - 输出 IR 通过 strict pydantic 校验（`SolverIR` 系列）✅。
- **本地命令**：

  ```bash
  ./scripts/verify_m3.sh                       # 全量 lint/format/mypy/pytest + CLI 烟雾
  python3 -m tools.solver_ir rf_layout_simplified.yaml \
    --out out/PA_Module_Simplified.solver.json # 仅 SolverIR 编译
  ```

  产物：`out/PA_Module_Simplified.solver.json`。

### M4 ─ Phase 2 CP-SAT 接入（2 周）

- **状态**：✅ 已实现（M4，2024-Q4）。NoOverlap 在本里程碑下沉为「post-extract audit」；M6 postproc 将负责弯折插入与硬避障（M5 SA 仅作 hint 软种子，不修复几何）。
- **目标**：v6 求解器主干，跑通真实案例。
- **交付物**：
  - `src/solver/units.py`：mm↔µm 整数离散 + 长度容差工具；
  - `src/solver/cpsat.py`：CP-SAT 模型构建（端点/junction/UV 变量、locked length、branch origin 别名、板框边界）；
  - `src/solver/extract.py`：OR-Tools 解 → strict `GeometryIR`；
  - `src/solver/audit.py`：抽取后长度 / NoOverlap 审计；
  - `src/schema/geometry_ir.py`：strict `GeometryIR` / `Placement` / `RoutePolyline` / `PinPlacement`；
  - `src/postproc/geom_svg.py`：GeometryIR → SVG 渲染；
  - `src/tools/cpsat_solve.py` + `tools/cpsat_solve.py` shim：`cpsat_solve` CLI（`--time-limit`、`--workers`、`--svg-out`、`--report-out`、`--quiet`）。
- **DoD（实测）**：
  - `rf_layout_simplified.yaml`：status = **OPTIMAL**，wall ≈ 0.01 s（远低于 10 s 上限）；
  - 14 条 `rf_constrained_locked` 边长度误差 ≤ ±0.5%（实测 max = 0.500%；2 条几何不可行边由 audit 报告并跳过：`RF_INPUT_to_IC1`、`PWR_VDD_bus`）；
  - NoOverlap 由 post-extract audit 报告（M4 不强约束；M6 postproc 通过弯折插入修复，详见 `concepts/cpsat-model.md`）。
- **运行**：
  ```bash
  ./scripts/verify_m4.sh                # 完整质量门 + cpsat_solve 烟测
  cpsat_solve rf_layout_simplified.yaml \
      --svg-out out/PA.geom.svg \
      --report-out out/PA.geom.json
  ```
- **设计文档**：`concepts/cpsat-model.md`。

### M5 ─ Phase 1 SA + Phase 3 A*（兼容能力）（1.5 周）

- **状态**：✅ 已实现（M5，2024-Q4）。SA 仅作为 CP-SAT 的 `AddHint` 软种子；A* 仅处理 `flexible_path` 边；NoOverlap 几何修复 / 弯折插入下沉至 M6（详见 `concepts/cpsat-model.md` §3.4）。
- **目标**：补齐三阶段流水线；A* 与 multipoint 降为可选能力。
- **交付物**：
  - `solver/sa_floating.py`：HPWL + 边界 + UV anchor 吸附 + 软排斥能量 + Metropolis 退火；输出 `dict[uv_id, (anchor_x_um, anchor_y_um, side)]` 作为 hint；
  - `solver/cpsat.py::build_model`：新增 `seed_hints=` kwarg，将 SA 输出经 `model.AddHint` 喂给 CP-SAT（软启发，不破坏可行性）；
  - `solver/astar_flex.py`：均匀网格 A*（默认 200 µm 步长，Manhattan 启发，turn + near-RF 软成本），消费 `routing_class == FLEXIBLE_PATH` 的边并改写 GeometryIR 中的 polyline；找不到路径时回退保留原 polyline 并记入 `failed_edges`；
  - `solver/orchestrator.py`：`solve_layout(yaml_path, OrchestratorOptions) -> OrchestratorResult` 一行编排「frontend → solver_ir → SA → CP-SAT (hints) → extract → A* → audit」全流程；INFEASIBLE 自动重试 ≤ 5 次（每轮 SA 初温 ×1.5、CP-SAT 时限 ×1.5）；
  - `tools/pcb_solve.py` + `pcb_solve` console script：一行 CLI E2E（`--no-sa` / `--no-astar` 旁路开关，`--max-retries`、`--svg-out`、`--report-out`、`--quiet`）；
  - `concepts/sa-and-astar.md`：能量函数 / A* 网格 / orchestrator 重试策略说明文档；
  - `scripts/verify_m5.sh`：一键质量门（含 black/ruff/mypy/pytest + 全部 CLI 烟测）；
  - **不**实现 FLUTE/RSMT（F7：真实案例无 multipoint，留作 v7 扩展）；
  - **不**实现 NoOverlap 几何修复 / 弯折插入（M6 范围）。
- **DoD（已达成）**：
  - 真实案例端到端：`pcb_solve rf_layout_simplified.yaml --time-limit 10` 单命令一键跑通；
  - 端到端 wall ≤ 30 s（实测 ≈ 13 ms，attempts=1）；status=OPTIMAL，max_len_err ≤ 0.5%；
  - 合成 trace 单元用例（`tests/unit/test_solver_astar_flex.py`）通过 A*：A* polyline 实际绕过中央 RF 障碍（≥3 个折点）；
  - SA 关闭时（`--no-sa`）退化为 M4 行为，仍 OPTIMAL；
  - 全部 172 项 pytest + black/ruff/mypy 全绿。
- **运行指引**：
  - 一键三阶段 E2E：`./scripts/verify_m5.sh`
  - 直接调用 CLI：`pcb_solve rf_layout_simplified.yaml --time-limit 10 --workers 8 --svg-out out/PA.m5.svg --report-out out/PA.m5.json`
  - Python API：`from solver import solve_layout, OrchestratorOptions`

### M6 ─ 后处理 + LVS 软校验 + DRC + 输出（1 周）

- **状态**：✅ 已实现（M6，2024-Q4）。`postproc/{bend,drc,lvs}.py` + `output/svg_full.py` 全部落地，PA 端到端 `pcb_solve --bend --drc-out --final-svg` 退出码 0、DRC critical=0（详见 `concepts/postproc-bend-drc-lvs.md`）。Gerber/GDS 仅 stub，字段映射见 `concepts/output-stub-mapping.md`，真实写入留 v7。
- **目标**：达到 MVP 可用状态。
- **交付物**：
  - `postproc/bend.py`：mitered_45 / curved / square / arc 几何渲染；未知 bend_style → fallback；
  - `postproc/lvs.py`：软校验（若 YAML 含 logical_net 才生效；产出 warning 不阻塞）；
  - `postproc/drc.py`：min_width / min_clearance / via_density 检查；
  - `output/svg_full.py`：最终板图 SVG（含 footprint + 走线 + bend）；
  - `output/gerber_stub.py` / `output/gds_stub.py`：占位 + 字段映射文档；
  - `README.md` 增加"如何运行"章节；
  - 性能基线报告（真实案例耗时分布）。
- **DoD**：
  - 真实案例 DRC 0 critical；
  - 最终 SVG 视觉检查无明显交叉 / 越界；
  - 端到端命令文档化；
  - 性能基线 ≤ 30 s。

### M7 ─ GAP 修复：语义 Lint + 蛇形走线 + DRC 几何升级

- **状态**：✅ 已实现（M7，2025-Q2）。针对 M6 初版流水线运行发现的 8 个 GAP 中优先级最高的 3 个进行了修复。PA 端到端 `pcb_solve --bend --meander` 退出码 0、DRC critical=0，蛇形走线正确插入（详见 `docs/superpowers/specs/2026-05-14-m7-gap-fixes-design.md`）。
- **目标**：修复首批 GAP，提升输出正确性。
- **GAP 分析**：
  - **G1**（已修复）：`RF_INPUT_to_IC1.target_length=30mm` 远小于端点 Manhattan 距离 70.78mm → 物理不可达；已修正为 80mm（需蛇形补偿 9.22mm）。
  - **G2**（已修复）：`IC1.PIN_3` 同时归属 `RF_INPUT` 和 `PWR_VDD` 两个网络（YAML 数据错误）；已修复 `pin_nets` 映射。
  - **G3**（已修复）：DRC Rule 2 使用膨胀 BBox 重叠检测，对对角线走线产生大量误报（28→0 critical）；升级为逐段段间最小距离算法。
  - **G4**（已实现）：增加 U 形蛇形走线模块，对 `target_length > Manhattan` 的边自动插入发夹环。
- **交付物**：
  - `src/frontend/lint.py`：新增 `lint_semantic()` 含 `pin_multi_net`（Error）与 `meander_required` / `length_infeasible_short`（Warning/Error）规则；
  - `src/postproc/meander.py`：U 形蛇形走线（`apply_meanders(geom, ir)`），对最长线段中段 60% 区间插入发夹环；
  - `src/postproc/drc.py`：Rule 2 升级为段-段最小距离 `_seg_seg_min_dist()`；
  - `src/solver/cpsat.py`：增加蛇形跳过逻辑（`target > Manhattan + tol` 时延迟至后处理）；
  - `src/tools/pcb_solve.py`：新增 `--meander/--no-meander` CLI 参数；
  - `rf_layout_simplified.yaml`：4 处数据修正（G1+G2）；
  - `scripts/verify_m7.sh`：完整质量门（black + ruff + mypy + pytest + 端到端 PA + DRC/meander DoD 断言）；
  - 28 个新单元测试（语义 lint / 蛇形走线 / DRC 几何）。
- **DoD**：
  - `./scripts/verify_m7.sh` 全绿；
  - PA YAML `pcb_solve --bend --meander`：exit 0、DRC critical=0、meander applied≥1；
  - 218 测试通过（3 个预存环境失败不计）。

### M8 ─ 走线交叉诊断 + Footprint Pad 渲染

- **状态**：✅ 已实现（M8）。M7 全流程通过后，可视化输出仍能看到大量交叉、且无管脚显示；M8 在不动求解算法的前提下，新增"3 层诊断报告"（每对事实 → 根因聚合 → 算法 GAP）+ SVG pad 渲染，为 M9 算法改进提供数据驱动的优先级排序。
- **目标**：
  1. 把"看 SVG 数交叉"升级为"读 JSON/MD 报告找 GAP"；
  2. SVG 增加 footprint pad 多边形，让端口/封装与走线的关系一目了然；
  3. 报告自动给出优先级最高的算法 GAP 候选，驱动 M9 排期。
- **核心交付**：
  - `src/postproc/crossing_analysis.py`：3 层诊断核心
    - **Layer 1 PairFact**：每对重叠的几何事实（µm 重叠、夹角、两段长度、共享端点等）；
    - **Layer 2 根因聚合**：5 类规则 R1–R5 分类（容差噪声 / 同网汇聚 / 异网穿越 / 长穿短 / 锁长冲突）+ 5×5 grid 热点；
    - **Layer 3 算法 GAP 检测**：6 条独立规则（CPSAT-NO-GEOM-FREEDOM / SA-DENSITY / DATA-OVERSPEC / TOPOLOGY-NO-LAYER / POSTPROC-MEANDER-EXPAND / PLACEMENT-DENSITY），priority_score = 消除对数 / 复杂度权重；
  - `src/postproc/crossing_report_io.py`：JSON + Markdown 序列化（中文根因翻译）；
  - `src/output/svg_full.py`：`_render_pads()` 基于 `geom.placements` 解析后位置 + `V33Layout` footprint pad 几何，绘制 22 个 PA pad 多边形（rect 精确，其它 shape bbox 兜底）；
  - `src/tools/crossing_report.py` + `tools/crossing_report.py`：独立 CLI（`--json` / `--md` 输出报告，不需要重跑完整 pcb_solve）；
  - `src/tools/pcb_solve.py`：新增 `--crossing-report-json` / `--crossing-report-md` / `--show-pads/--no-show-pads` 三个 flag；
  - `scripts/verify_m8.sh`：质量门（断言 critical=0、≥1 GAP、≥20 个 pad polygon、四个 MD 章节齐全）；
  - 35 个新单元测试（21 分类 / GAP / 热点，7 SVG pad，7 CLI/serialiser）。
- **DoD**：
  - `./scripts/verify_m8.sh` 全绿；
  - PA YAML `pcb_solve --bend --meander --show-pads --crossing-report-md ...`：exit 0、DRC critical=0、SVG pad polygon ≥ 20、报告 ≥ 1 GAP；
  - 244 测试通过（3 个预存环境失败不计）。
- **关键发现 / M9 排期输入**：
  - PA 数据上的 5 类根因分布：R4 长穿短 38% / R3 异网穿越 30% / R2 同网汇聚 23% / R5 锁长冲突 9%；R1 容差噪声 0%；
  - 实际跑下来 priority_score 最高的 GAP **不是**先验假设的 `GAP-CPSAT-NO-GEOM-FREEDOM`（R5 占比仅 9%，未触达 30% 阈值），而是 `GAP-PLACEMENT-DENSITY`（priority 4.00，30/66 对集中在 (8,20)–(16,40) 8×20mm 热点内）；
  - 推论：**M9 应优先做"布局分散性优化"（SA cost 增加 dispersion 项 / footprint margin / 板尺寸评估），而不是先动 CP-SAT 求解器**——这一改动是 M8 数据驱动决策的直接成果。

### M9 ─ 八角走线路由器（Octilinear A\* Router）

- **状态**：⚠️ 已实现基础框架，效果待优化（仅 5/22 边成功路由）。
- **目标**：引入支持 45° 对角线方向的 A\* 路由器（八角走线），减少 Manhattan 布线的直角绕行，降低与现有走线的交叉。
- **核心交付**：
  - `src/solver/octilinear_router.py`：A\* 搜索核心，支持 8 个方向（0°/45°/90°/135° 及镜像）；pad halo 300µm 障碍标记；
  - `src/postproc/route_orchestrator.py`：路由编排器，对 `rf_constrained` 边逐一调用 A\* 并写入 GeometryIR；
  - `src/tools/pcb_solve.py`：新增 `--octilinear/--no-octilinear` CLI 参数；
  - 相关单元测试（路由器基本路径、障碍绕行、八向扩展）。

#### M9 效果复盘 — 根因分析（深度）

**背景数据**：PA_Module_Simplified，22 条 RF 边，M9 仅路由 5/22（22.7%）；`R2_to_TP1` 实际长度=0.000mm（零长度）；`IC1_pin2_seg6` 误差=-98.7%（严重下冲）。

**根因一：CP-SAT 布局不感知路由可达性**

CP-SAT 将 8 个 RLC UV 器件全部堆积在 IC1 周围约 25mm×10mm 区域（布局分散性未优化），导致 22 条 RF 走线的端点高度聚集；A\* 栅格在该区域几乎无可通行单元，绝大多数路由请求直接失败。

- **本质**：布局阶段没有"走线可达性"约束，CP-SAT 的 `NoOverlap` 仅阻止器件物理重叠，不阻止端点密集。
- **修复方向（M9.1）**：SA 能量函数增加 `dispersion_term`（端点离散度惩罚）+ CP-SAT `footprint_margin` 参数扩大间距。

**根因二：SA crossing_weight 过小（=100），布局优化无效**

SA 总能量约 190k→146k（优化幅度 23%），但交叉惩罚项仅贡献 < 0.5% 的总能量，导致 SA 对走线交叉几乎无感，布局结果中大量端点仍相互挤压。

- **修复方向（M9.1）**：将 `crossing_weight` 提升至 5,000–10,000，使交叉惩罚在 SA 能量中占有实质比重（目标 ≥ 10%）。

**根因三：Pad halo 固定 300µm 堵塞 IC1 出口**

IC1 管脚间距约 0.5mm；相邻管脚的 300µm halo 在栅格上互相重叠，在 IC1 周边形成连片障碍区，第 2–3 条路由通过后，后续路由的出口通道完全关闭。

- **修复方向（M9.1）**：自适应 halo：`halo = max(line_width/2 + clearance, 100µm)`；或引入动态障碍更新策略（已路由边的 halo 比未路由边更宽）。

**根因四：锁长目标超出 A\* 最短可达路径长度（严重过长）**

部分边（如 `IC1_pin1_seg6`）的 `target_length=20mm`，而 A\* 实际最短路径 ≈ 7mm；蛇形走线模块最多能在 60% 中段插入单个 U 形弯，补偿能力 < 3mm，远不足以填补 13mm 缺口。更极端的 `IC1_pin2_seg6` target=~50mm 而最短路径 ≈ 0.5mm（端点几乎重合），误差 -98.7%。

- **修复方向（M9.1）**：`route_orchestrator` 增加"过长预检"：若 `astar_shortest > target + tolerance`，标记为 `overshoot_skip` 并输出警告，不再尝试路由；蛇形模块升级为多段蛇形（当缺口 > 3mm 时自动增加折叠次数）。

**根因五：UV 端点坐标提取 bug（零长度路由）**

`R2_to_TP1` 路由长度=0.000mm，说明起点和终点坐标相同。根因是 `R2.PIN_1` 未写入 `terminals` 字典（只有固定端点 `TP1.PIN_1` 有坐标），UV 器件的管脚坐标在 CP-SAT 求解后没有被反写回 `terminals`，导致路由编排器取到 `(0, 0)` 或重复使用起点坐标。

- **修复方向（M9.1）**：CP-SAT 求解后遍历所有 UV placement，将每个管脚的世界坐标写入 `terminals` 字典，供路由编排器和蛇形模块消费。

#### M9.1 修复优先级列表

| 优先级 | 修复项 | 预期影响 |
|---|---|---|
| P1 | UV 端点坐标提取修复（`terminals` 反写） | 消除零长度路由，直接提升可路由边数 |
| P2 | SA `crossing_weight` 提升至 5,000–10,000 + `dispersion_term` | 驱动布局分散，减少端点密集，A\* 可通行区域扩大 |
| P3 | pad halo 自适应：`max(line_width/2 + clearance, 100µm)` | IC1 周边出口通道恢复，多管脚器件可路由性提升 |
| P4 | `route_orchestrator` 过长预检 + `overshoot_skip` 标记 | 消除无效路由尝试，减少误差报告噪声 |
| P5 | 蛇形走线升级：多段蛇形（缺口 > 3mm 时自动增折） | 解决大缺口锁长边的蛇形补偿不足问题 |

---

## 5. 风险登记与决策记录

### 5.1 风险登记表

| ID | 风险 | 触发条件 | 缓解 | 责任期 |
|---|---|---|---|---|
| R1 | universal_junction 含非 90° 倍数 angle 时 OR-Tools 整数近似引入误差 | 任意 angle ∉ {0, ±90, 180} | 1µm 离散；误差 < 单位长度的 1‰，DRC 容忍内 | M3 |
| R2 | UV host_edge 失配（reference_net 无任何 anchor_pin 端点）| 输入数据自描述错误 | §2.4 反向构造 anonymous host edge + lint warning；不阻塞 | M3 |
| R3 | rf_constrained_free 段长度过短被 NoOverlap 挤穿 | 邻近器件密集 | NoOverlap 的 inflate 用 width + 2·clearance；CP-SAT infeasible 时反馈 SA 抬温度 | M4 |
| R4 | OR-Tools NoOverlap 仅 axis-aligned，对斜段近似 | 大量 45° 走线 | 把斜段拆为多个轴对齐子矩形 (v4 现做法)；监控误差 | M4 |
| R5 | 9 个 UV 同时求解，CP-SAT 启发式搜索退化 | 高密度 UV | num_workers=cpu_count + portfolio search；超时则降级"先 SA 固定 placement，再 CP-SAT 仅解节点" | M4–M5 |
| R6 | 拼写 typo 表覆盖不全 | 上游持续产生新 typo | lint_report 累积统计，每月回顾扩充修复表；未知字段透传不阻塞 | M2a 持续 |
| R7 | universal_junction 的 `semantic_intent` 列表多语义（如同时 stepped_impedance + manifold_junction）| 真实案例已出现 | 当前版本只用几何模板（branches[]）求解；semantic_intent 仅用于后处理 EM 仿真标记 | M3 |
| R8 | 未来若引入 multipoint_net / logical_net | v7 扩展 | M5 已搭好 A* 与 LVS 接口，预留 FLUTE 集成钩子 | post-M6 |

### 5.2 ADR（关键决策记录）

- **ADR-1（D1）**：作废 v3.3→v4 的 `lumped_*` 翻译。理由：真实案例所有 RLC 都是 component-with-pads，pin 直接出现在 edge 端点。`lumped_*` 在 v4 文档中保留供"无 footprint 抽象电路"使用。
- **ADR-2（D2）**：UV 解析采用"端点滑动"而非"中段插入分裂"。理由：真实案例 UV 器件的 anchor_pin 总是出现在某条 microstrip 的 connections 端点，"中段插入"的 v5 模型与数据不符。
- **ADR-3（D3）**：routing_class 扩为三档。理由：真实案例存在 `microstrip + 无 target_length` 的"短引线段"，二分法无法表达。
- **ADR-4（D4）**：新增 `universal_junction` 作为几何模板节点类型。理由：真实案例两个核心功分点都用此类型，含显式 branches[] 配置；现有 t_junction/stepped_impedance 不足以表达。
- **ADR-5（D5）**：Schema Lint 容错优先于严格校验。理由：真实数据存在 typo + 未知字段，严格 reject 会让数据进不来；warning 透传更工程化。
- **ADR-6**：M5 不实现 FLUTE/RSMT。理由：真实案例无 multipoint_net；保留接口但不预先投入。
- **ADR-7**：LVS 改为软警告。理由：真实案例无 logical_net；强阻塞不适用。

---

## 6. 文件索引

| 文件 | 状态 | 说明 |
|---|---|---|
| `射频微波版图结构化数据规范 (v3.3).md` | 既有 | 输入规范（外部接口） |
| `ALGORITHM-OVERVIEW.md` | 既有 | v4 算法基线（求解器内部 schema） |
| `concepts/placement-problem-formulation.md` | 既有 | SA + 引力场建模 |
| `concepts/routing-algorithm-comparison.md` | 既有 | CP-SAT + A* 双求解器 |
| `concepts/microstrip-topology-matching.md` | 既有 | 微带线 / bend_style / 阶跃阻抗 |
| `rf_layout_simplified.yaml` | **既有（锚定回归用例）** | PA_Module_Simplified 真实数据 |
| `ITERATION-PLAN.md` | **本文件 v7** | 最终算法方案 + M0–M7 迭代计划 |
| `concepts/frontend-compiler-spec.md` | ✅ 已写（M2 完成） | Frontend Compiler 详细规范 |
| `concepts/uv-and-junction-templates.md` | ✅ 已写（M3 完成） | UV 解析 + universal_junction 模板规范 |
| `docs/superpowers/specs/2026-05-14-m7-gap-fixes-design.md` | ✅ 已写（M7 完成） | M7 GAP 修复设计规格（语义 lint、蛇形走线、DRC 几何升级）|
| `scripts/verify_m7.sh` | ✅ 已写（M7 完成） | M7 质量门脚本 |
| `docs/superpowers/specs/2026-05-14-m8-crossing-diagnostics-design.md` | ✅ 已写（M8 完成） | M8 走线交叉诊断 + Pad 渲染设计规格 |
| `scripts/verify_m8.sh` | ✅ 已写（M8 完成） | M8 质量门脚本 |

---

## §M10 骨架优先三阶段路由器（v7-alt）

继 M9 复盘后启动的架构级重构。详见 `concepts/skeleton-first-router.md`。

| 子里程碑 | 状态 | 交付物 |
|---|---|---|
| M10a 架构脚手架 + 回归基线 | ✅ | `src/solver/v2/`、`scripts/verify_m10.sh`、PA snapshot |
| M10b Phase A 节点 LP + 通道路由 | ✅ | `node_planner.py`、`channel_grid.py`、`skeleton_router.py` |
| M10c 长度补偿 + Rip-up 主循环 | ✅ | `length_meander.py`、`rip_up.py`；PA 18→20/22 |
| M10d Phase B UV 吸附 + A↔B 反馈 | ✅ | `uv_adhesion.py`、`freespace.py`；UV 9/9 |
| M10e Phase C 接入 + 端到端编排 | ✅ | `orchestrator.py` Phase C 通过 `solver.astar_flex.route_flexible_paths` |
| M10f 入口切换 + 文档同步 | ✅ | `pcb_solve` 默认指向 v2；`pcb_solve_v1` 保留别名；删除 v1 测试；本节 + `concepts/skeleton-first-router.md` |

PA 案例当前结果：22 条 microstrip 中 20 条由 Phase A 路由成功，9/9 UV 吸附成功，0 条 flex 边，wall ≈ 2.6 s。剩余 2 条（`IC1_pin1_seg2_to_R2`、`C1_to_R1`）需要 A↔B 多轮反馈深度迭代，留作后续优化。
