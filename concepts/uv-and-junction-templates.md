# UV 解析与 universal_junction 模板（M3 SolverIR）

> Status: M3 实现 ✅
> 输入：M2 `FrontendArtifact` + 原始 v3.3 YAML
> 输出：`SolverIR`（CP-SAT 模型构建器在 M4 直接消费）

## 1. 设计目标

把 v3.3 中两类"参数化"对象编译成纯线性表达式 + 常量偏移，使 M4 求解器无需再做任何几何推导：

| 对象 | 输入 | M3 输出 |
|---|---|---|
| `parametric_uv` 器件 | `anchor_pin / reference_net / 旋转域` | `UvResolution.pin_position_exprs[]` 线性表达式族 |
| `universal_junction` 节点 | `connection_rules.branches[]` | `UniversalJunctionTemplate.branches[]` 含常量 `(dx, dy)` |

## 2. UV 解析

### 2.1 host_edge 匹配

对每个 UV 器件 `c`（`anchor_pin=p`, `reference_net=n`）：

1. 列出所有 `type=microstrip` 且 `net=n` 的边；
2. 在 `connections` 中检查 `c.p` 端点是否出现；
3. 三种状态：

| 状态 | 候选数 | 行为 |
|---|---|---|
| `unique` | 1 | 锁定 `host_edge_id` |
| `ambiguous` | ≥2 | 取第一个，等待 M4 报警 |
| `missing` | 0 | 合成 `synthetic_host__{c}`，emit `LintWarning(uv_host_missing)` |

### 2.2 引脚坐标线性表达式

每个 UV 器件引入 4 个 IR 变量：

```
{c}.anchor_x, {c}.anchor_y       # host_edge 上的弧长投影坐标
{c}.offset_v_side ∈ {-1, +1}     # host_edge 法线方向选边
{c}.rot_dx_{pin}, {c}.rot_dy_{pin}  # 旋转占位（M4 旋转 case-split 展开）
```

锚点引脚位置：

```
anchor.x = {c}.anchor_x + offset_v_side · (W/2 + clearance) · n̂.x
anchor.y = {c}.anchor_y + offset_v_side · (W/2 + clearance) · n̂.y
```

派生引脚位置（`pin_k`）：

```
pin_k.x = anchor.x + {c}.rot_dx_{pin_k}
pin_k.y = anchor.y + {c}.rot_dy_{pin_k}
```

> ℹ️ M3 留下的占位：法向量 `n̂` 来自 `_normal_to_edge`，目前返回 `(0, 1)`。
> 真实法线方向依赖 host_edge 解算后的几何，由 M4 在模型构建时回填。

## 3. universal_junction 模板展开

每个 `universal_junction` 节点对应一对 IntVars `(center_x, center_y)`。每条 `branches[i]` 在 IR 中下沉为一个常量约束：

```
signed_v =  +(W/2 + clearance)   if offset_v == "edge_left"
         =  −(W/2 + clearance)   if offset_v == "edge_right"
         =   0                    if offset_v == "align_center"

cosθ, sinθ = cos(angle°), sin(angle°)        # 0/±90/±180 走精确整数表

dx = cosθ · offset_u  −  sinθ · signed_v
dy = sinθ · offset_u  +  cosθ · signed_v
```

`BranchConstraint(target_endpoint, dx, dy)` 直接驱动 M4 添加：

```
endpoint.x == center_x + dx
endpoint.y == center_y + dy
```

## 4. SolverIR Strict 校验

`schema/solver_ir.py` 中的 `SolverIR` 与 v6 IR 同一家族（`StrictFrozenModel`）。关键不变量：

- 所有宽度 / 间距 > 0；
- `offset_v_sides ⊆ {−1, +1}` 且非空；
- `host_match_status` 与 `host_candidates` / `synthetic_host` 一致：
  - `unique` ⇒ 候选数 = 1；
  - `ambiguous` ⇒ 候选数 ≥ 2；
  - `missing` ⇒ `synthetic_host=True`；
- `routing_class=rf_constrained_locked` ⇒ `target_length is not None`；
- `uv_resolutions[*].host_edge_id` 必须在 `edges` 中存在（`synthetic_host` 例外）。

## 5. CLI

```bash
python3 -m tools.solver_ir rf_layout_simplified.yaml \
  --out out/PA_Module_Simplified.solver.json
```

输出摘要示例：

```
project: PA_Module_Simplified
board: 40.0 x 100.0
clearance: 0.15
terminals: 10
edges: 22
uv_resolutions: unique=9 ambiguous=0 missing=0
junction_templates: nodes=2 branches=6
```

## 6. PA_Module 当前案例

- 9 个 UV 器件 100% `unique` 命中（含 R2→`R2_to_TP1`、C6 锚点 `PIN_2`/`RF_NET_2`）。
- 2 个 universal_junction × 3 branches = 6 条 BranchConstraint，全部走 `angle ∈ {0, ±90}` 精确整数三角值，规避 R1 量化误差。
- `target_length` 锁定边 15 条，自由 RF 边 7 条，flex 0 条；`SolverEdge` 全量校验通过。
