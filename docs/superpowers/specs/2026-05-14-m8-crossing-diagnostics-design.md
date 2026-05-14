# M8 设计规格 — 走线交叉诊断 + 管脚渲染

**日期**：2026-05-14  
**状态**：草案（待评审）  
**前置**：M7（语义 lint + 蛇形走线 + DRC 几何升级）已合入主干  
**关联 PR**：#10（M7）

---

## 1. 背景与目标

### 1.1 现状（M7 后基线）
- PA `pcb_solve --bend --meander` 端到端退出 0
- DRC critical=0 / warning=28
- **`overlap_pairs=66`**，其中 52 对重叠 > 1mm（视觉上严重交叉）
- `no_overlap_pass=False`，`astar.routed_edges=[]`（A* 在 PA 案例 0 触发）
- SVG 输出**不渲染封装管脚**，视觉上看不到组件

### 1.2 根因（已分析清楚）
1. **CP-SAT 不强制 NoOverlap**：注释明确写着 PA 案例 "provably-infeasible hard pairwise NoOverlap"，因为所有 RF 锁长边都是单段直线，没有几何避让自由度
2. **A* 只对 `flexible_path` 边生效**：PA 所有边都是 `rf_constrained_locked`，A* 不参与
3. **后处理仅做弯角/蛇形，不避障**：bend 与 meander 都不修改路径形状以避免交叉
4. **SVG 渲染层缺管脚**：`output/svg_full.py` 只画 board + routes + DRC overlay

### 1.3 M8 范围（明确不动算法）
1. **诊断报告**：自动分析 66 对重叠的根因，输出 JSON + Markdown，含算法 GAP 分析与修复建议
2. **管脚渲染**：SVG 增加 footprint pad 层

**不在范围**：求解器算法改造（留给 M9，由本轮报告驱动）

---

## 2. 总体架构

```
                    ┌────────────────────────┐
   pcb_solve ──→    │ overlap_pairs (audit)  │ ──→ JSON only (现状)
                    └────────────────────────┘
                                │
                                ▼   (M8 新增)
                    ┌────────────────────────┐
                    │ crossing_analysis      │
                    │  - per-pair facts      │
                    │  - root cause class    │
                    │  - algorithmic GAPs    │
                    └────────────────────────┘
                                │
                ┌───────────────┼────────────────┐
                ▼               ▼                ▼
            JSON            Markdown        SVG (with pads)
        (machine)         (human)         (visual)
```

---

## 3. 模块设计

### 3.1 `src/postproc/crossing_analysis.py`（新）

**职责**：纯计算。输入几何 IR + audit overlap，输出结构化报告。

**核心 dataclass**：
```python
@dataclass(frozen=True)
class PairFact:
    edge_a: str
    edge_b: str
    overlap_um: int          # 来自 audit
    overlap_area_um2: int    # 计算得到（重叠区面积）
    nearest_distance_um: int # 段-段最小距离（已有 _seg_seg_min_dist）
    intersection_angle_deg: float  # 0–90
    edge_a_class: str        # routing_class
    edge_b_class: str
    shared_endpoint: str | None    # 共享端点 ID（如 IC1.PIN_1）
    same_net: bool                  # 拓扑同网
    root_cause: str                 # R1–R5 标签

@dataclass(frozen=True)
class AlgorithmicGap:
    gap_id: str
    gap_title: str
    affected_pair_count: int
    affected_fraction: float
    evidence: str                   # 自动生成的中文证据描述
    root_cause_layer: str           # placement / cpsat_routing / astar / postproc / data
    recommended_fix: str
    estimated_pairs_eliminated: int
    complexity: str                 # S / M / L
    priority_score: float           # = eliminated / complexity_weight

@dataclass(frozen=True)
class CrossingReport:
    project: str
    total_pairs: int
    critical_pair_count: int        # overlap > 1mm
    pair_facts: tuple[PairFact, ...]
    root_cause_counts: dict[str, int]  # {R1: ..., R2: ...}
    region_hotspots: tuple[Hotspot, ...]   # 热力区聚类
    gaps: tuple[AlgorithmicGap, ...]       # 按 priority 排序

def analyze_crossings(
    geom: GeometryIR,
    solver_ir: SolverIR,
    overlap_pairs: list[dict],       # 来自 audit
) -> CrossingReport: ...
```

**根因分类规则（R1–R5）**（按优先级顺序判定，落入第一个匹配类）：

| 标签 | 名称 | 触发条件 |
|------|------|---------|
| R1 | 同源管脚扇出 | `shared_endpoint != None`（两条边共享物理端点） |
| R2 | 同网络汇聚 | `same_net == True` 且非 R1（在同一 net 拓扑下） |
| R3 | 异网络穿越 | `same_net == False` 且 `intersection_angle > 30°` |
| R4 | 长边穿短边 | 长度比 `max/min > 3` 且 `intersection_angle > 30°` |
| R5 | 锁长冲突 | 两条都是 `rf_constrained_locked` 且未匹配 R1–R4 |

R1/R2 通常是**合理交叉**（同源/同网拓扑允许），R3/R4/R5 是**真实问题**。

**算法 GAP 检测器（M8 内置 6 条规则）**：每条规则是 `def detect(report_in_progress) -> Optional[AlgorithmicGap]`，按声明顺序执行，独立可测。

| GAP ID | 触发条件 | 推荐修复 | 复杂度 |
|--------|---------|---------|-------|
| `GAP-CPSAT-NO-GEOM-FREEDOM` | R5 类对数 ≥ 30% 总对数 | M9：Negotiated A* router for RF locked edges + meander 兜底补长 | L |
| `GAP-PLACEMENT-DENSITY` | overlap 几何中心聚集在 < 30% 板面 bbox | SA cost 加分散项 / 增大 board 尺寸 | M |
| `GAP-FANOUT-NOT-DISPERSED` | R1 类对数 ≥ 20% 总对数 | 管脚扇出策略：星形/树状，短引线优先布外圈 | M |
| `GAP-FLEX-NO-OBSTACLE` | flexible_path 边参与 R3/R4 重叠 ≥ 1 对 | A* obstacle map 补充已布 RF 边 | S |
| `GAP-MEANDER-COLLISION` | 蛇形边参与 R3/R4 重叠 ≥ 1 对 | meander 选段时跳过拥挤区，或 meander 后再做避障 pass | M |
| `GAP-DATA-OVERSPEC` | semantic lint 报 `meander_required` 或 `length_infeasible_short` ≥ 1 | YAML 数据复核 / 约束放松 | S |

**`estimated_pairs_eliminated` 计算口径**（透明、保守）：
- `GAP-CPSAT-NO-GEOM-FREEDOM`：R5 类对数（修复理论上消除全部 R5）
- `GAP-FANOUT-NOT-DISPERSED`：R1 类对数 × 0.6（扇出策略可消除 60%）
- `GAP-PLACEMENT-DENSITY`：热点区域内的对数 × 0.4
- 其余按各自规则；具体系数是经验保守值，写在常量表里以便后续调校

**热点区域定义**：把每对重叠对的几何中心（两条边 polyline 的中点连线中点）作为 2D 点，用简单 grid bucket（板面 5×5 网格，单元 ~8×20mm）做密度统计。**热点格子** = 包含 ≥ 总对数 20% 的格子；多个相邻热点格子合并为一个 `Hotspot { bbox_mm, contained_pair_count }`。算法故意简单（无聚类库依赖）。

**`priority_score` 计算**：
```
complexity_weight = {S: 1, M: 3, L: 8}
priority_score = estimated_pairs_eliminated / complexity_weight[complexity]
```

### 3.2 `tools/crossing_report.py`（新 CLI）

**职责**：编排器。运行完整 pcb_solve 流水线 → 提取 overlap_pairs → 调用 crossing_analysis → 输出 JSON + Markdown。

```bash
python -m tools.crossing_report rf_layout_simplified.yaml \
  --json out/crossing.json \
  --md out/crossing.md \
  [--bend] [--meander]    # 与 pcb_solve 共用算法选项
```

**Markdown 模板**（节选）：
```markdown
# Crossing Analysis Report — {project}

> Generated: {ISO timestamp}  
> Pipeline options: bend={bool} meander={bool}

## Summary
- Total overlap pairs: {N}
- Critical (>1mm): {M}
- Algorithmic GAPs detected: {K}

## Algorithmic GAPs (priority-ordered)

### #1 {gap_id} ({HIGH/MED/LOW based on priority percentile})
- **Affected**: {N}/{total} pairs ({pct}%)
- **Root cause layer**: {layer}
- **Evidence**: {evidence}
- **Recommended fix**: {fix}
- **Estimated elimination**: ~{N} pairs
- **Complexity**: {S/M/L}

...

## Root Cause Distribution

| Class | Description       | Count | % |
|-------|-------------------|-------|---|
| R1    | 同源管脚扇出      | ...   | ... |
...

## Top 20 Critical Pairs

| # | Edge A → Edge B | Overlap (mm) | Class | Suggested action |
|---|-----------------|--------------|-------|------------------|
...
```

### 3.3 `src/output/svg_full.py`（增强）

**新增 `_render_pads(geom, layout)`**（在 board → footprint outline 之后、routes 之前插入）：

```python
def _render_pads(layout: V33Layout, x, y) -> list[str]:
    out = []
    for comp_id, comp in layout.components.items():
        fp = layout.footprints[comp.footprint_ref]
        cx, cy = comp.placement.x, comp.placement.y
        comp_rot = comp.placement.orientation
        for pin_id, pin in fp.pins.items():
            # 旋转 + 平移
            px, py = _rotate(pin.local_x, pin.local_y, comp_rot)
            ax, ay = cx + px, cy + py
            pad = pin.pad_geometry
            # SVG <rect> with rotation transform
            out.append(_pad_rect_svg(ax, ay, pad, pin.local_orientation + comp_rot, x, y))
    return out
```

**渲染顺序（自下而上）**：
1. board
2. footprint outline（黄色填充）
3. **pads（金色 `#fcd34d` 填充 + 棕色 `#92400e` 描边）**（新）
4. routes（按 routing_class 配色）
5. DRC overlay（红/橙虚框）
6. endpoints + labels

**接口变更**：`render_full_layout(geom, ...)` 增加可选参数 `layout: V33Layout | None = None`；为 None 时不渲染 pads（向后兼容）。

**形状支持**：M8 仅支持 `shape: rect`（PA 案例所有 pad 都是 rect）；其它 shape 透传 warning 后用 bbox 矩形渲染（不阻塞）。

### 3.4 `src/tools/pcb_solve.py`（增强）

新增 CLI 选项（默认值在括号）：
- `--crossing-report-json PATH`（None；不输出）
- `--crossing-report-md PATH`（None；不输出）
- `--show-pads / --no-show-pads`（默认 `True`）

**接线**：在现有 `--final-svg` 输出前后调用 crossing_analysis 与 pad 渲染。

---

## 4. 数据流补全

### 4.1 V33Layout 已有 `footprints + components`，无需修改 schema
（已验证：`PKG_IC_4PIN.pins.PIN_1.{local_x, local_y, local_orientation, pad_geometry: {shape, length, width}}`）

### 4.2 GeometryIR **不**扩展 footprint 字段
管脚渲染由 `svg_full.py` 直接读 `V33Layout`（通过 `pcb_solve` 在调用渲染时一并传入），保持 `GeometryIR` 几何聚焦。

### 4.3 SolverIR / overlap_pairs 已有所需信息
- `audit.overlap_pairs` 已含 `edge_a, edge_b, overlap_um`
- `solver_ir.edges` 含 routing_class、connections（用于判定 shared_endpoint / same_net）
- `geom.routes` 含 polyline（用于面积、夹角、最近距离计算）

---

## 5. 测试策略

### 5.1 单元测试

**`tests/unit/test_crossing_analysis.py`** (≥ 12 测试)：
- 5 类根因分类（R1-R5 各 1+ 测试 case）
- 6 个 GAP 检测器（每个独立测）
- `estimated_pairs_eliminated` 计算口径（边界值）
- `priority_score` 排序
- 空 `overlap_pairs` → 空报告（边界）

**`tests/unit/test_svg_pads.py`** (≥ 6 测试)：
- 单组件 + 单 rect pad 渲染：SVG 含 `<rect>`、坐标正确
- 旋转 90°：pad 局部坐标正确变换
- 多组件并存：每个组件 pad 数 = footprint.pins 数
- pad 在 footprint outline 之上、routes 之下（图层顺序）
- 未知 shape：warning + bbox 兜底
- `--no-show-pads`：SVG 不含 pad

**`tests/unit/test_crossing_report_cli.py`** (≥ 4 测试)：
- 真实 PA YAML → JSON 含 `gaps`/`pair_facts`/`root_cause_counts`
- Markdown 含所有必要 section（标题、Summary、GAPs、表格）
- `--json` 文件存在 + 可解析
- 退出码 0

### 5.2 回归测试
- PA 报告：`total_pairs == 66`（M7 基线值），各根因分类合计 = 66
- PA SVG：含 ≥ 20 个 `<rect class="pad">` 元素

---

## 6. DoD（Definition of Done）

- `./scripts/verify_m8.sh` 全绿（black + ruff + mypy + pytest + 端到端 PA + DoD 断言）
- `pcb_solve --crossing-report-md out/crossing.md` 退出 0
- 报告 Markdown 含所有必要 section，至少检测出 1 个 GAP
- PA SVG 含 ≥ 20 个 pad 元素，视觉检查能看清器件管脚位置
- 218 + 新增 ≥ 22 测试全通过（不引入新失败）
- `ITERATION-PLAN.md` §M8 章节更新

---

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| GAP 检测规则启发性强，可能误判 | 规则透明、独立可测；evidence 字段记录原始数据；用户可基于报告手动判断 |
| `estimated_pairs_eliminated` 系数偏差 | 标注为"保守估计"；常量集中于一处便于后续校准 |
| pad 旋转矩阵实现错误 | 单元测试覆盖 0°/90°/180°/270° + 任意角度 |
| V33Layout 未传入时 SVG 不显示 pad | 接口可选参数 + 警告日志，向后兼容 |
| 复杂 footprint shape (round/oblong) 渲染不准 | M8 仅承诺 rect 精确；其它 shape bbox 兜底 + warning |

---

## 8. 后续路线（M9 候选，由本报告驱动）

M8 报告会生成可执行的 GAP 列表。预期 PA 案例下 `GAP-CPSAT-NO-GEOM-FREEDOM` 会成为最高优先级 GAP，复杂度 L，对应**M9 主题：Negotiated A* router for RF edges**（由 M8 报告作为输入论证）。

---

## 9. 文件变更清单

**新增**：
- `src/postproc/crossing_analysis.py`
- `tools/crossing_report.py`
- `tests/unit/test_crossing_analysis.py`
- `tests/unit/test_svg_pads.py`
- `tests/unit/test_crossing_report_cli.py`
- `scripts/verify_m8.sh`

**修改**：
- `src/output/svg_full.py`（+ `_render_pads`）
- `src/tools/pcb_solve.py`（+ 3 CLI flags + crossing_analysis 调用 + pad 渲染传参）
- `src/postproc/__init__.py`（导出 crossing_analysis）
- `ITERATION-PLAN.md`（§M8）

**不变**：
- `src/schema/*`（不扩 schema）
- `src/solver/*`（不动算法）
- `src/frontend/*`
- `rf_layout_simplified.yaml`
