# 功放 PCB 自动布局布线引擎 —— 迭代计划 v1

> 基础规范：[`射频微波版图结构化数据规范 (v3.3).md`](./射频微波版图结构化数据规范%20(v3.3).md)
> 算法基线：[`ALGORITHM-OVERVIEW.md`](./ALGORITHM-OVERVIEW.md) (v4 hybrid_rf_graph)
> 目标：把 v3.3 输入规范作为外部数据接口，落地 v4 三求解器算法，端到端跑通 Doherty 功放案例。

---

## 0. 文档定位

v3.3 是**外部数据交换规范**（面向 EDA 上游 / 设计师），v4 是**求解器内部 schema**（面向算法）。两者抽象层级不同，必须显式建立"翻译层"，否则算法在落地时会出现概念混乱。本计划：

1. 盘点 v3.3 ↔ v4 的差距（§1）；
2. 描述需要落地的算法增量（§2）；
3. 给出 6 期迭代计划与每期的退出标准（§3）；
4. 登记风险与技术债（§4）。

---

## 1. v3.3 输入规范 ↔ v4 算法 差距盘点

| 维度 | v3.3 输入侧 | v4 算法侧 | 差距 / 必要的桥接 |
|---|---|---|---|
| 元器件实体 | `components` + `footprints`（含 `pin_nets`、`pad_geometry`） | 仅 `terminals`（裸焊盘锚点） | **Frontend 编译**：把 component × footprint 的每个 pin 展开成 `terminal`，把 `pad_geometry` 转成障碍矩形 |
| 绝对放置 | `placement.{x,y,rotation}, is_floating: false` | `terminals.{x,y}` | 1:1 映射，外加 rotation 几何变换 |
| UV 吸附 | `parametric_uv: {anchor_pin, reference_net, offset_u/v=null/auto}` | 无对应概念 | **核心新增**：把 UV 器件解析为"在 reference_net 的某条 microstrip 上插入 `component_pad_junction` 节点，把宿主 edge 拓扑分裂为两段"，`offset_u` 即子段长度（CP-SAT 变量），`offset_v` 即横向偏移（左/右 BoolVar） |
| 板框 / 禁布区 | `global_constraints.board_outline / keepout_zones` | 隐含支持 | Phase 0 显式生成 obstacle map + 限制 CP-SAT 节点变量域 |
| 节点类型名 | `impedance_step / t_junction / universal_node` | `t_junction / component_pad_junction / pad_junction / stepped_impedance / floating_shunt_tap` | normalizer：`impedance_step ≡ stepped_impedance`，`universal_node ≡ pad_junction` |
| 边类型 | `microstrip / logical_net / multipoint_net / trace` | `microstrip / trace / lumped_*` | **`logical_net` 缺失** → 新增 LVS Verifier；**`multipoint_net` 缺失** → 用 RSMT 分解为多条 2 端边 |
| 路权类 | `rf_constrained / externally_constrained / unrouted_bus` | `rf_constrained / flexible_path` | `externally_constrained` → 跳过寻路、只走 LVS；`unrouted_bus` → 等价 `flexible_path`（但要支持多点） |
| 集总 RLC | 在 `components` 里，通过 `pin_nets` 接入网络 | `lumped_*` edge | **建模不一致**：Frontend 识别 → ①一脚触地 → 并联 shunt edge；②两脚非地 → 串联 series edge；③ UV 吸附 → 还要拓扑分裂宿主 microstrip |
| 拐弯几何 | `geometry.bend_style` | 同左 | 一致 |
| 阻抗阶跃偏移 | v3.3 节点 `impedance_step` 暂无 `custom_offset` 字段 | 节点支持 `connections_rule.custom_offset` | 默认 `centerline` 对齐；建议向上游补字段 `connections_rule.offset_from_center` |

---

## 2. 算法增量（v5 = v4 + v3.3 适配层）

整体管线：

```
v3.3 YAML
    │
    │ ① Frontend Compiler （新增）
    │   · pydantic 校验 schema
    │   · components × footprints 展开为 pads (rotation/translate)
    │   · obstacle map = board_outline ∪ keepout_zones ∪ fixed-component footprints
    │   · UV 解析：parametric_uv → 在宿主 microstrip 上插入 component_pad_junction，拓扑分裂
    │   · 节点 / 边类型 normalize（v3.3 名 → v4 名）
    │   · multipoint_net 分解：FLUTE-style RSMT → 2 端子边 + Steiner 节点
    │   · 集总 RLC 改写为 lumped_* edges（依据 pin_nets 是否触地判 串/并）
    │   · logical_net 注册到 LVS 校验表（不进求解器）
    ▼
v4 内部表示 {terminals, nodes, rf_edges, flex_edges, series_components, shunt_branches}
    │
    │ ② Phase 1 ─ Floating Placement SA  (v4 原算法，引力场)
    │ ③ Phase 2 ─ Rigid RF Kinematic + CP-SAT  (v4 原算法 + UV 子段变量)
    │ ④ Phase 3 ─ Flexible A*  (v4 原算法 + multipoint 顺序贪心)
    ▼
    │ ⑤ LVS Verifier （新增）
    │   · 对每条 logical_net.connections，校验等价连通分量
    │   · 校验无 "幽灵 net"
    ▼
    │ ⑥ DRC + 输出 GDS / Gerber / SVG
    ▼
最终几何
```

### 2.1 UV 吸附求解（v3.3 核心新概念）

输入示例：`C731312` 以 `PIN_2` 吸附于 `$36N998423`，`offset_u: null`、`offset_v: auto`。

求解步骤：

1. 在 `reference_net` 中按拓扑顺序找到由 microstrips 组成的"骨架链" $E_1, E_2, \dots, E_k$；
2. 选择宿主 edge $E_i$（启发式见 §2.3）；
3. 新建节点 $N_{uv}$（type: `component_pad_junction`），把 $E_i$ 拆为 $E_i^{(1)}: A \to N_{uv}$ 与 $E_i^{(2)}: N_{uv} \to B$；
4. 原 `target_length: L_i` 被联立约束 $L_i^{(1)} + L_i^{(2)} = L_i$ 替代；$L_i^{(1)}$ 即 `offset_u`，是 CP-SAT 整数变量（$0 \le L_i^{(1)} \le L_i$）；
5. `offset_v: auto` 离散化为左/右 BoolVar，对应 ±(W/2 + clearance) 横向偏移；
6. anchor_pin 对应的 component 焊盘 footprint 进入 NoOverlap obstacle 集。

**优点**：UV 完全融入 v4 现有运动学求解，不引入新求解器，复杂度只增 O(N_uv) 个变量。

### 2.2 `multipoint_net` 多点 A*

- 用 **FLUTE**（开源 RSMT 库，BSD）做 Rectilinear Steiner Min Tree 分解 → (N−1) 条 2 端边 + Steiner 节点；
- Steiner 节点加入 `nodes`（type: `pad_junction`），子边逐条交给 v4 现有 A*；
- 顺序贪心：已布的子边视为障碍，与 v4 现有 `route_flexible_edges` 行为一致。

### 2.3 Frontend 宿主 edge 选择启发式

候选优先级：

1. anchor_pin 所属 component 已有绝对坐标 → 选与该坐标 HPWL 最近的 microstrip edge；
2. 否则按 net 内 microstrip edge 的 `target_length` 加权选最长者（最有"伸缩余量"）；
3. 多 UV 同 edge → 沿 u 方向均分初始猜测，所有 `offset_u` 联立解；
4. 退化策略：把 host_edge 改成"候选集 + AddExactlyOne"的 BoolVar，让 CP-SAT 自己选（代价：变量 +O(N_uv·k)，仅在启发失败时启用）。

### 2.4 LVS Verifier

对每条 `logical_net`：

- 构图 $G$：节点 = 该 net 出现的所有 component pin + nodes + GND_REF + UV junctions；边 = 同 net 的 microstrip / lumped_* / trace；
- 验证 1：`logical_net.connections` 中所有 pin 落在 $G$ 同一连通分量；
- 验证 2：物理 edges 中没有"幽灵 net"（出现在 edges 但任何 logical_net 都未声明）。

LVS 失败一律抛 build error，不触发求解器重试（这是建模错误，不是求解失败）。

### 2.5 板框 / 禁布区

- `board_outline` → CP-SAT 节点变量域 $[0, W_{int}] \times [0, H_{int}]$；A* 网格边界。
- `keepout_zones` → ①栅格化为 occupied cells 注入 A* obstacle map；②CP-SAT 端复用 `add_pairwise_no_overlap` 模式，把 keepout 作为"虚拟固定矩形"参与无交叉约束。

### 2.6 复杂度自检

| 阶段 | 规模上限 | 时间预算 |
|---|---|---|
| Frontend 编译 | components 200 / pins 2000 / edges 500 | < 200 ms |
| Phase 1 SA | floating + UV 节点 ≤ 50 | 1–5 s |
| Phase 2 CP-SAT | RF 段 ≤ 50 + NoOverlap O(n²) ≈ 1500 对 | 5–30 s |
| Phase 3 A* | flex 边 ≤ 30 + RSMT 子边 ≤ 50，单边 < 10 ms × 5 retry | < 3 s |
| LVS | logical_net ≤ 200 | < 100 ms |

**整盘 ≤ 1 min / Doherty 案例**，与 v4 估算一致。UV 与 multipoint 扩展不会显著抬量级，因为它们都被前置编译成 v4 已有的对象。

---

## 3. 6 期迭代计划

每期顶部列出**目标**、**交付物**、**退出标准**（DoD）。所有期次均以 `pytest` 单元测试 + 1 个回归 YAML 用例作为最低退出门槛。

### M1 ─ 工程骨架与 Schema 定义

- **目标**：建立项目脚手架，定义 v3.3 与 v4 内部 schema 的形式化模型。
- **交付物**：
  - 仓库结构：`src/{schema, frontend, solver, lvs, output}/`；`tests/{unit, regression}/`；
  - `schema/v33.py`、`schema/v4.py`：pydantic 模型，覆盖 v3.3 全 7 个 root key 与 v4 hybrid_rf_graph；
  - CI（GitHub Actions）：lint (ruff) + format (black) + type check (mypy) + pytest；
  - 一个最小 v3.3 示例 YAML（README 示例），能被 pydantic 解析。
- **DoD**：
  - `pytest tests/unit/test_schema.py` 全绿；
  - `mypy src/` 零 error；
  - 示例 YAML round-trip：load → dump → load 不丢字段。

### M2 ─ Frontend Compiler

- **目标**：完成除 UV 之外的 v3.3 → v4 翻译，包括 components 展开、节点 / 边类型 normalize、集总器件串/并判定、obstacle map 生成。
- **交付物**：
  - `frontend/expand_components.py`：footprint × placement → 物理 pad terminals（含 rotation 矩阵）；
  - `frontend/normalize_nodes.py`：v3.3 节点类型名 → v4；
  - `frontend/lumped_classifier.py`：`pin_nets` 触地判 shunt / series；
  - `frontend/obstacles.py`：board_outline + keepout_zones + fixed-component footprint → obstacle 多边形集合；
  - **暂不**支持 `parametric_uv`、`multipoint_net`（M3、M5）。
- **DoD**：
  - 把 v3.3 文档示例（不含 UV / multipoint）编译为 v4 内部表示，通过 schema 校验；
  - 单元测试：5+ 不同 footprint × rotation 组合的展开结果正确；
  - 集总器件分类测试：≥ 3 个串联 + 3 个并联用例。

### M3 ─ UV 解析 + 拓扑分裂

- **目标**：实现 §2.1 的 UV 吸附解析。
- **交付物**：
  - `frontend/uv_resolver.py`：实现 §2.3 启发式宿主选择 + 拓扑分裂；
  - `frontend/uv_resolver.py:host_edge_candidate_set()`：退化策略（CP-SAT 选宿主）；
  - 1 个真实射频匹配网络回归用例（≥ 2 个 UV 电容挂在一条 microstrip 上）。
- **DoD**：
  - UV 解析后，新生成的 component_pad_junction + 子段在 v4 schema 校验通过；
  - 联立约束 $\sum L^{(j)} = L_{original}$ 在 CP-SAT 构造时正确生成（M4 验证求解通过）。

### M4 ─ Phase 2 CP-SAT 接入（求解器主干）

- **目标**：把 v4 的 CP-SAT 伪代码实化（OR-Tools），跑通 §2 routing-algorithm-comparison §1 的 4 边示例。
- **交付物**：
  - `solver/cpsat_rf.py`：长度约束、无交叉约束、阶跃阻抗 offset、UV 子段联立、过孔密度约束；
  - `solver/cpsat_rf.py:no_overlap_with_obstacles()`：把 M2 的 obstacle 多边形纳入 NoOverlap；
  - 4 边示例的 RF 几何输出 SVG 可视化。
- **DoD**：
  - routing-algorithm-comparison §1 示例求解 status ∈ {OPTIMAL, FEASIBLE}；
  - 求解结果中 RF 段长度误差 ≤ ±0.5%；
  - 任意两条 RF 边 bbox 不相交（自动断言）。

### M5 ─ Phase 1 SA + Phase 3 A* + multipoint RSMT

- **目标**：闭环三阶段，端到端跑通 PA 模块。
- **交付物**：
  - `solver/sa_floating.py`：v4 引力场能量函数 + Metropolis 退火；
  - `solver/astar_flex.py`：v4 A* + 拐弯惩罚 + 远离 RF 边惩罚；
  - `frontend/multipoint_steiner.py`：FLUTE 集成或简化 RSMT 算法，把 multipoint_net 分解为 2 端子边 + Steiner 节点；
  - `solver/orchestrator.py`：三阶段流水线 + 5 次重试 + 抬温度反馈；
  - 1 个完整 Doherty 功放回归用例（v3.3 YAML，包含 UV、multipoint、keepout）。
- **DoD**：
  - Doherty 用例端到端求解成功；
  - 求解时间 < 60 s（CI 机器，单线程）；
  - 输出 SVG 视觉检查无明显交叉 / 越界。

### M6 ─ LVS Verifier + DRC + 输出

- **目标**：补齐验证与导出，达到 MVP 可用状态。
- **交付物**：
  - `lvs/verifier.py`：实现 §2.4 两条验证规则；
  - `lvs/drc.py`：基础 DRC（最小线宽、最小间距、过孔密度）；
  - `output/gerber_stub.py`、`output/gds_stub.py`：先输出占位（明确字段映射），完整渲染留作 v6；
  - `output/svg_preview.py`：可视化预览；
  - 性能基线报告（Doherty 用例耗时分布）；
  - `README.md` 增加"如何运行"章节。
- **DoD**：
  - LVS 对 Doherty 用例报告 0 error；
  - DRC 对 Doherty 用例报告 0 critical；
  - 性能基线 ≤ 60 s 在 CI 机器复现。

---

## 4. 风险与技术债登记

| ID | 风险 / 技术债 | 触发条件 | 缓解 / 处理 | 责任期 |
|---|---|---|---|---|
| R1 | 宿主 edge 启发式选错 → CP-SAT infeasible | UV 数量多 / 宿主 net 短 | 退化为 host_edge BoolVar 候选集 + AddExactlyOne | M3 |
| R2 | RSMT 引入 Steiner 节点与 RF 几何冲突 | 多端 net 横跨 RF 区 | A* 阶段把 Steiner 视为可移动；最终位置由路径决定，不传 CP-SAT | M5 |
| R3 | v3.3 缺 `custom_offset` 字段 | 阶跃阻抗有非中心对齐需求 | 默认 `centerline`；向上游提交 spec 补丁建议 | M2 |
| R4 | OR-Tools NoOverlap 矩形仅 axis-aligned，对斜段近似差 | 大量 45° 斜走线 | 把斜段拆为多个轴对齐子矩形（v4 现做法），监控误差 | M4 |
| R5 | `is_floating: false` 的 component 与 UV 子段冲突 | UV 宿主 edge 穿越 fixed component | Frontend 编译期 fail-fast 检测 | M3 |
| R6 | FLUTE 引入 BSD 依赖（C 扩展） | M5 集成 | 准备 fallback：纯 Python 的简化 Steiner（精度略降） | M5 |
| R7 | A* 顺序贪心可能给后到 flex 边留死路 | 多条 flex 边交叉布线 | 触发 Phase 1 抬温度重试；上限 5 次后报告 infeasible | M5 |
| R8 | 共享节点联立联立解可能多解 / 无解 | RF 拓扑闭环 / 长度互斥 | CP-SAT 不可行时输出冲突基（已有 `analyze_infeasibility`），落到日志 | M4 |
| TD1 | v4 文档当前不含 `host_edge_candidates`、`uv_subsegment` 等字段 | M3 完成后 | M6 后回写更新 v4 文档 | M6+ |
| TD2 | GDS / Gerber 输出仅占位 | M6 | 留待 v6 单独迭代 | post-M6 |

---

## 5. 关键决策记录（ADR 摘要）

- **ADR-1**：UV 吸附通过"拓扑分裂 + offset_u 变量"融入 v4 而不引入新求解器。理由：复用现有 CP-SAT 联立机制，避免双层优化。
- **ADR-2**：multipoint_net 用 RSMT 预分解而不扩展 A* 为多源寻路。理由：业界成熟方案，且不影响 v4 现有 A* 单源逻辑。
- **ADR-3**：LVS 失败不触发重试。理由：LVS 是建模错误（输入数据矛盾），重试无意义；区别于 CP-SAT/A* 的求解失败（可通过抬温度重试）。
- **ADR-4**：连续坐标 + 1 µm 离散化（沿用 v4）。理由：制造精度足够，且能让 CP-SAT 用整数变量保持高效。

---

## 6. 文件索引

| 文件 | 状态 | 说明 |
|---|---|---|
| `射频微波版图结构化数据规范 (v3.3).md` | 既有 | 输入规范（外部接口） |
| `ALGORITHM-OVERVIEW.md` | 既有 | v4 算法基线（求解器内部 schema） |
| `concepts/placement-problem-formulation.md` | 既有 | SA + 引力场建模 |
| `concepts/routing-algorithm-comparison.md` | 既有 | CP-SAT + A* 双求解器实现 |
| `concepts/microstrip-topology-matching.md` | 既有 | 微带线拓扑、bend_style、阶跃阻抗 |
| `ITERATION-PLAN.md` | **本文件** | 迭代计划（v3.3 ↔ v4 桥接 + 6 期里程碑） |
| `concepts/frontend-compiler-spec.md` | 待写（M2） | Frontend Compiler 详细规范 |
| `concepts/lvs-verifier.md` | 待写（M6） | LVS 校验器规范 |
