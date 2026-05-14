# CP-SAT 主求解器模型（M4）

> 输入：M3 `SolverIR` + M2 `FrontendArtifact`
> 输出：strict `GeometryIR`（端点 / 折线 / pin 位）+ SVG
> 实现：`src/solver/cpsat.py`、`src/solver/extract.py`、`src/solver/audit.py`

## 1. 离散与单位

- 内部全部使用 **微米 (µm)** 整数变量，遵循 v6 §5 约定（`MM_TO_UM = 1000`）。
- 公开 IR / SVG 仍为浮点 mm，仅在 build/extract 边界换算。
- 长度容差：`length_tolerance_um = max(1, int(target_um * 0.005))`。
  - 用 `int()`（向下取整）而非 `ceil`，确保任何被约束边的最终误差严格 ≤ 0.5%。

## 2. 变量

| 角色 | 变量 | 域 |
|---|---|---|
| 自由端点 | `x_µm`, `y_µm` IntVar | `[0, board_width_µm] × [0, board_height_µm]` |
| 固定 pad 端点 | 常量 | 来自 `FrontendArtifact.fixed_terminals` |
| `t_junction`/`combiner` 节点 | `x, y` IntVar | 板内 |
| `universal_junction` | `cx, cy` IntVar；branch 端点 = `(cx + dx_µm, cy + dy_µm)`（线性等式） |
| UV 组件 | `anchor_x, anchor_y` IntVar；`rotation ∈ {0°, 180°}` BoolVar；`offset_v_side` BoolVar | `AddExactlyOne` 约束 |

端点 ID 全局复用：`endpoint_id → (x_var, y_var)` lazy 注册，保证 junction 子边和 UV pin 共享同一变量。

## 3. 约束

### 3.1 长度（`rf_constrained_locked`）

```
sum_dx_abs + sum_dy_abs == target_um   ± tol_um
```
通过 `AddAbsEquality` 拆 `dx_abs / dy_abs`。

**Fixed-fixed skip**：当两端均为常量且 `Manhattan(p_a, p_b) > target + tol` 时，
认为输入数据自身不一致（PA 案例的 `RF_INPUT_to_IC1`、`PWR_VDD_bus`），
跳过 length 约束并写入 `cpsat.skipped_locked_edges`，由 audit 后报告。

### 3.2 Branch origin 别名

M3 `BranchConstraint(target_endpoint, dx, dy)`：
- `target_endpoint` 是 branch 的**远端**符号端点；
- `(dx, dy)` 是 branch 在父 host edge 上**起点**的偏移。

M4 在求解时新建合成端点 `<node>__br<i>_origin_<edge>`，绑定到 `(cx + dx_µm, cy + dy_µm)`，
并将原父边的端点替换为该原点。映射保存在 `cpsat.edge_endpoint_resolved`，供 extract / audit 使用。

### 3.3 板框边界

变量域裁剪即可，无需额外约束。

### 3.4 NoOverlap：M4 下沉为 audit-only

| 选项 | 是否可行 | 备注 |
|---|---|---|
| 硬 `AddNoOverlap2D` 含所有边 | ❌ | 共享端点的两条边 bbox 必相交，立即 INFEASIBLE |
| 配对 `BoolOr` 拒绝 + 邻接对豁免 | ❌（PA 案例） | IC1 fanout 4 条 trace（pin pitch 5.76mm，宽 3.6/3.9mm）单段 Manhattan 物理不可行 |
| **post-extract audit**（M4 选定） | ✅ | 求解后用 `audit_geometry()` 报告所有几何重叠对，CLI 默认不阻塞退出 |

audit 实现位于 `src/solver/audit.py::audit_geometry`，参数：
- `skip_length_edges`：跳过 length-skip 边的长度断言；
- `resolved_endpoints`：把同一 junction 上的多条 sub-edge 共点视为合法连接，避免误报。

**M5 SA 责任**：通过弯折点插入 + 排斥能量将 audit 报告的重叠对修复为真实可制造布线。

### 3.5 UV / rotation（M4 收敛）

PA 案例所有 UV 都对齐网格，M4 仅启用 `{0°, 180°}` 两档旋转 + `offset_v_side ∈ {+, −}` 两档 BoolVar，
分别用 `OnlyEnforceIf` 切两个 pin offset 分支。`±90°` 留给 M5（pin 数 ≤ 2 时可证拓扑等价）。

## 4. 求解参数

```python
solver.parameters.max_time_in_seconds = 10  # CLI 可覆盖
solver.parameters.num_search_workers  = 8
```

PA 案例实测：status=OPTIMAL，wall=0.011s。

## 5. 抽取 → GeometryIR

`src/schema/geometry_ir.py` 定义 strict pydantic 模型：
- `GeometryIR(project, board, placements, routes)`；
- `ComponentPlacement(name, position, rotation, pads: list[PinPlacement])`；
- `RoutePolyline(edge_id, polyline: list[Point], width)`。

抽取后立即调用 audit，结果写入 `--report-out` JSON，并由 SVG 渲染器（`src/postproc/geom_svg.py`）落盘。

## 6. CLI

```bash
cpsat_solve <layout.yaml> \
    [--time-limit 10] [--workers 8] \
    [--svg-out out/<name>.geom.svg] \
    [--report-out out/<name>.geom.json] \
    [--quiet]
```

退出码：
- `0` 求解成功（即使 NoOverlap audit 失败，M4 不阻塞）；
- `2` 缺 `layout` 段；
- `3` status ∈ {INFEASIBLE, MODEL_INVALID, UNKNOWN}；
- `4` locked-edge 长度容差超过 0.5%。

## 7. 已知限制（M4 范围外）

- NoOverlap 不是硬约束，依赖 M5 几何修复；
- 旋转限定 `{0°, 180°}`；
- bend_style 几何渲染推迟到 M6（M4 输出折线 + width 即可）；
- 非 90° 倍数旋转的 footprint 不解（PA 案例不出现）。
