# 功放 PCB 自动布局布线引擎 v6

> **基于真实案例 `rf_layout_simplified.yaml` 验证后的最终算法方案**
>
> 输入规范：v3.3（外部 EDA 接口） · 内部 IR：v6 · 求解器三阶段：SA + CP-SAT + A*

---

## 0. 文档定位与版本

本仓库目前包含 **设计文档 + M0/M1/M2/M3 基础实现**。它描述并提供：

1. 上游 EDA 工具产出的 v3.3 YAML 数据规范；
2. 后端布局布线引擎应当采用的算法方案；
3. 已落地的 M0/M1/M2/M3 命令行工具与质量门；
4. 后续 6+1 期迭代开发计划。

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
  IC1_pin1_seg2:
    type: microstrip
    net: RF_NET_1
    routing_class: rf_constrained
    # 新语义：微带线与器件端点直连，不引入冗余 *_split_pad 节点
    connections: [IC1_pin1_seg1_universal_node, C7.PIN_1]
    geometry: { bend_style: mitered_45 }
```

---

## 7. 文件结构

```
.
├── README.md                                       # 本文档（v6 入口 + M0/M1/M2/M3/M4/M5 运行说明）
├── ALGORITHM-OVERVIEW.md                           # v6 算法总览
├── ITERATION-PLAN.md                               # v6 算法方案 + 6+1 期开发计划
├── pyproject.toml                                  # 可编辑安装 + console scripts
├── rf_layout_simplified.yaml                       # 真实案例 (PA_Module_Simplified)
├── scripts/verify_m1.sh                            # M1 一键验证
├── scripts/verify_m2.sh                            # M2 一键验证（含 frontend_compile 烟测）
├── scripts/verify_m3.sh                            # M3 一键验证（含 solver_ir 烟测）
├── scripts/verify_m4.sh                            # M4 一键验证（含 cpsat_solve 烟测）
├── scripts/verify_m5.sh                            # M5 一键验证（含 pcb_solve 三阶段流水线烟测）
├── tools/
│   ├── topology_viz.py                             # 仓库根包装器
│   ├── schema_check.py                             # 仓库根包装器
│   ├── frontend_compile.py / solver_ir.py          # M2 / M3 CLI 包装
│   ├── cpsat_solve.py                              # M4 CP-SAT 单段 CLI
│   └── pcb_solve.py                                # M5 三阶段一键 CLI（SA + CP-SAT + A*）
├── src/
│   ├── schema/                                     # v33 / v6_ir schema
│   ├── tools/                                      # CLI 实现
│   └── topology/                                   # M0 拓扑渲染
├── tests/
│   ├── unit/                                       # schema / CLI / 渲染测试
│   └── regression/PA_Module_Simplified/            # 拓扑 SVG 基线快照
├── concepts/
│   ├── placement-problem-formulation.md            # SA + 引力场（v6 适配）
│   ├── routing-algorithm-comparison.md             # CP-SAT + A* 双求解器（v6 适配）
│   └── microstrip-topology-matching.md             # 微带 / bend_style / universal_junction 几何
└── 射频微波版图结构化数据规范 (v3.3).md            # 输入数据规范（外部 EDA 接口）
```

阅读路径：
- **新读者**：本 README → `ALGORITHM-OVERVIEW.md` → `ITERATION-PLAN.md`
- **算法实现者**：`ITERATION-PLAN.md` §2–§3 → 三个 concept 文档
- **数据生产者**：`射频微波版图结构化数据规范 (v3.3).md` + `rf_layout_simplified.yaml`

---

## 8. 本地运行（M0 / M1 / M2 / M3 / M4 / M5 / M6）

推荐先创建虚拟环境（部分 Linux 发行版对系统 Python 启用了 PEP 668）：

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e ".[dev]"
```

> 注意：本仓库要求 Python ≥ 3.11。Ubuntu 22.04 默认仅有 3.10，可执行
> `sudo apt-get install -y python3.11 python3.11-venv` 后再创建 `.venv`。

若你的环境允许直接安装，也可直接执行：

```bash
python3 -m pip install -e ".[dev]"
```

常用命令：

```bash
# M0: 生成拓扑 SVG 基线
topology_viz rf_layout_simplified.yaml
# 输出: out/PA_Module_Simplified.topology.svg

# M1: 运行 v3.3 schema 检查
schema_check rf_layout_simplified.yaml
# 预期:
# project: PA_Module_Simplified
# components: 18
# footprints: 5
# nodes: 8
# terminals: 9
# edges: 23

# 一键验证 M1 质量门
./scripts/verify_m1.sh

# M2: 前端编译（Lint + expand + triage + normalize）
frontend_compile rf_layout_simplified.yaml
# 输出: out/PA_Module_Simplified.frontend.json
# 预期 stdout:
# fixed_pads: 8
# uv_components: 9
# obstacles: 6
# edges: locked=14 free=3 flex=6 other=0
# nodes: t_combiner_junction=2 t_junction=4 universal_junction=2
# lint: repairs=3 warnings=2 errors=0

# 一键验证 M2 质量门（含 verify_m1 全部步骤 + frontend_compile 烟测）
./scripts/verify_m2.sh

# M3: SolverIR 编译（UV 解析 + universal_junction 模板）
solver_ir rf_layout_simplified.yaml
# 输出: out/PA_Module_Simplified.solver.json
# 预期 stdout:
# project: PA_Module_Simplified
# board: 40.0 x 85.0
# clearance: 0.15
# terminals: 9
# edges: 23
# uv_resolutions: unique=7 ambiguous=0 missing=2
# junction_templates: nodes=2 branches=6

# 一键验证 M3 质量门（含 verify_m2 全部步骤 + solver_ir 烟测）
./scripts/verify_m3.sh

# M4: CP-SAT 主求解器（端点 / junction / UV 变量 + locked length 约束）
cpsat_solve rf_layout_simplified.yaml \
    --svg-out out/PA_Module_Simplified.geom.svg \
    --report-out out/PA_Module_Simplified.geom.json
# 输出:
#   out/PA_Module_Simplified.geom.svg  几何 SVG（板框 + footprint + 走线）
#   out/PA_Module_Simplified.geom.json audit 报告（长度误差 + NoOverlap 列表）
# 预期 stdout（典型）:
# status=OPTIMAL wall=0.011s locked_within_tol=True no_overlap_pass=False max_len_err=0.500%

# 一键验证 M4 质量门（含 verify_m3 全部步骤 + cpsat_solve 烟测）
./scripts/verify_m4.sh

# M5: 三阶段一键流水线（SA hint → CP-SAT → A* flexible-path → audit）
pcb_solve rf_layout_simplified.yaml \
    --time-limit 10 --workers 8 --max-retries 5 \
    --svg-out out/PA_Module_Simplified.m5.svg \
    --report-out out/PA_Module_Simplified.m5.json
# 旁路开关:
#   --no-sa     仅 cold-start CP-SAT（等价 M4 行为）
#   --no-astar  保留直连 polyline，不跑 A*
# 预期 stdout（典型）:
# status=OPTIMAL attempts=1 wall=0.013s locked_within_tol=True no_overlap_pass=False max_len_err=0.500% sa(E:190369.4->146336.9, acc=1202/1700) astar(routed=0,failed=0)

# 一键验证 M5 质量门（含 verify_m4 全部步骤 + pcb_solve 三阶段烟测）
./scripts/verify_m5.sh

# M6: MVP 收官 — 弯折几何 + DRC + 软 LVS + 最终 SVG（+ Gerber/GDS 占位）
pcb_solve rf_layout_simplified.yaml \
    --time-limit 10 --workers 8 --max-retries 5 --quiet \
    --bend \
    --drc-out  out/PA_Module_Simplified.m6.drc.json \
    --lvs-out  out/PA_Module_Simplified.m6.lvs.json \
    --final-svg out/PA_Module_Simplified.m6.final.svg \
    --report-out out/PA_Module_Simplified.m6.json
# 旁路开关:
#   --no-bend   保留 M5 直线段输出（不渲染弯折几何）
# 退出码: 0=ok / 3=求解失败 / 4=锁定长度超容 / 5=DRC critical>0
# 预期 stdout（典型）:
# bend: bended=0 skip=22 warn=0
# drc: critical=0 warning=50
# lvs: skipped (no logical_net assignment provided (PA single-layer default))

# 一键验证 M6 质量门（含 verify_m5 全部步骤 + pcb_solve M6 + DRC critical 断言）
./scripts/verify_m6.sh

# ---- M7：语义 lint + 蛇形走线 + DRC 几何升级 ----
pcb_solve rf_layout_simplified.yaml \
    --time-limit 10 --workers 8 --bend --meander \
    --drc-out out/PA_Module_Simplified.m7.drc.json \
    --final-svg out/PA_Module_Simplified.m7.final.svg
./scripts/verify_m7.sh

# ---- M8：走线交叉诊断 + Footprint Pad 渲染 ----
# 端到端跑求解 + 输出 3 层交叉诊断（JSON + Markdown），并在 SVG 中渲染 pad 多边形
pcb_solve rf_layout_simplified.yaml \
    --time-limit 10 --workers 8 --bend --meander --show-pads \
    --crossing-report-json out/PA_Module_Simplified.crossing.json \
    --crossing-report-md   out/PA_Module_Simplified.crossing.md \
    --final-svg out/PA_Module_Simplified.m8.final.svg

# 仅生成交叉诊断报告（独立 CLI，不需要其它输出）
python -m tools.crossing_report rf_layout_simplified.yaml \
    --time-limit 5 --workers 4 \
    --json out/crossing.json --md out/crossing.md
# 报告含 4 节：根因分布 / 区域热点 / 算法 GAP（按 priority 排序）/ 重叠对明细

# 一键验证 M8 质量门（断言：critical=0、≥1 GAP、≥20 个 pad polygon、244 测试通过）
./scripts/verify_m8.sh

# ---- M10：骨架优先三阶段路由器（v7-alt，已成为 pcb_solve 默认实现）----
# Phase A 微带骨架（八角栅格 A* + 蛇形长度补偿 + Rip-up）→
# Phase B UV 就近吸附 → Phase C flexible_path A*。
pcb_solve rf_layout_simplified.yaml --out-dir out/
# 等价显式入口（保留 v6 旧实现用 pcb_solve_v1）：
#   pcb_solve_v2 rf_layout_simplified.yaml --out-dir out/
# 每阶段持久化：out/{project}.{phaseA,phaseB,phaseC,final}.{svg,json}
# 设计说明详见 concepts/skeleton-first-router.md
```

M2 产物 `out/PA_Module_Simplified.frontend.json` 的 schema 与字段含义见
[`concepts/frontend-compiler-spec.md`](./concepts/frontend-compiler-spec.md)。

M3 产物 `out/PA_Module_Simplified.solver.json` 的 schema、UV/junction 表达式语义见
[`concepts/uv-and-junction-templates.md`](./concepts/uv-and-junction-templates.md)。

M4 CP-SAT 模型（变量 / 约束 / NoOverlap 取舍 / CLI 退出码）详见
[`concepts/cpsat-model.md`](./concepts/cpsat-model.md)。

M5 三阶段流水线（SA 能量函数 / `AddHint` 通道 / A* 网格 / orchestrator 重试策略）详见
[`concepts/sa-and-astar.md`](./concepts/sa-and-astar.md)。

M6 后处理（弯折几何 / DRC waiver 机制 / 软 LVS / 最终 SVG）详见
[`concepts/postproc-bend-drc-lvs.md`](./concepts/postproc-bend-drc-lvs.md)；
Gerber/GDS 字段映射（v7 留实现）详见
[`concepts/output-stub-mapping.md`](./concepts/output-stub-mapping.md)。

### 8.1 性能基线（PA 单层参考板）

| 阶段 | 耗时（典型） | 备注 |
|---|---|---|
| frontend_compile | < 0.05 s | 22 边 / 9 UV |
| solver_ir         | < 0.05 s | 含 universal_junction 模板展开 |
| SA seed (200 iter) | ~0.05 s | UV 软种子 |
| CP-SAT 主求解     | ~0.01 s | OPTIMAL，1 attempt |
| A* flexible_path  | 0 s | PA 无 flexible 边 |
| audit + bend + DRC + LVS | < 0.05 s | M6 后处理 |
| **端到端 wall**   | **< 0.2 s** | DoD ≤ 30s ✅ |

### 8.2 MVP 完成声明（M6）

至 M6 合入主干，本仓库形成最小可发布闭环：

- ✅ YAML → SolverIR → SA → CP-SAT → A* → audit → bend → DRC → LVS → 最终 SVG 一键贯通
- ✅ PA 参考板：DRC critical=0、locked length 在 0.5% 容差内、端到端 < 0.2s
- ✅ 完整测试套件 + `scripts/verify_m{1..6}.sh` 阶梯化质量门
- ⚠️ **已知限制**（v7 任务）：
  - CP-SAT NoOverlap 仅做轴对齐 BBox 近似，PA 上有 50 条 audit-waivered 重叠（详见 ITERATION-PLAN R4）
  - Gerber / GDSII 仅 stub，调用即抛 `NotImplementedError`
  - LVS 仅在输入提供 `logical_net_of_edge` 时生效（PA 默认 skip）
  - 多层 / via stitching / 阻抗匹配 DRC 未支持

---

## 9. 6+1 期迭代计划速览

| 期 | 周 | 目标 | 关键交付 |
|---|---|---|---|
| **M0** | 0.5 | 拓扑可视化基线 | `rf_layout_simplified.yaml` → SVG |
| **M1** | 1   | 脚手架 + 双 schema | v33 (lenient) / v6 IR (strict) |
| **M2** | 2   | Frontend Compiler | Lint + expand_components + triage |
| **M3** | 2   | UV + universal_junction | 端点滑动模型 + 几何模板（最高风险）|
| **M4** | 2   | CP-SAT 主求解器 | 真实案例端到端 ≤ 10 s ✅ |
| **M5** | 1.5 | SA hint + A* flexible-path + orchestrator | `pcb_solve` 一键 E2E + 5 次重试 ✅ |
| **M6** | 1   | 后处理 + 输出 | bend / DRC / 软 LVS / 最终 SVG / Gerber·GDS stub ✅ MVP |

详见 [`ITERATION-PLAN.md`](./ITERATION-PLAN.md) §4。
