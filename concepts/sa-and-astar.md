# M5 — SA 预放置 + A* 灵活段路由 + 三阶段流水线

> ⚠️ 文档状态：**历史归档（M5）**。当前默认流程请参考 `pcb_solve_v2` 与 `concepts/skeleton-first-router.md`。
>
> 版本：M5（2024-Q4）
> 配套代码：`src/solver/sa_floating.py`、`src/solver/astar_flex.py`、`src/solver/orchestrator.py`、`src/tools/pcb_solve.py`

## 1. 三阶段流水线

```
v3.3 YAML
   │
   ▼  frontend.compile_layout            (M2)
FrontendArtifact
   │
   ▼  frontend.solver_ir.compile_solver_ir  (M3)
SolverIR
   │
   ▼  solver.run_sa  (Phase 1，M5)        ──► hints: dict[uv_id, anchor]
SaResult
   │
   ▼  solver.build_model(seed_hints=…)    (M4 + M5 hint 通道)
   ▼  solver.solve_model                 (CP-SAT 主求解，M4)
   ▼  solver.extract_geometry             (M4)
GeometryIR (含初步走线)
   │
   ▼  solver.route_flexible_paths  (Phase 3，M5)
GeometryIR (flexible_path 边已被 A* polyline 替换)
   │
   ▼  solver.audit_geometry               (M4)
   ▼  postproc.render_geometry_svg        (M4)
SVG + JSON report
```

入口函数：`solver.solve_layout(yaml_path, OrchestratorOptions) -> OrchestratorResult`。
CLI 入口：`pcb_solve <layout.yaml> [--no-sa] [--no-astar] [--max-retries N] [--time-limit S] [--workers W]`。

## 2. Phase 1 — SA 预放置（`sa_floating.py`）

### 2.1 能量函数（µm²，归一化到 mm² 量级）

总能量 `E = HPWL + boundary + attract + repulse`：

| 项 | 含义 | 实现 |
| --- | --- | --- |
| `HPWL` | 每个 UV 器件的 anchor 到其所有边对端点的曼哈顿半周长 | `_hpwl_for_uv` |
| `boundary` | anchor 落在板框外侧时按 `boundary_weight × dist²` 罚 | `_boundary_penalty` |
| `attract` | 拉向 host_edge 中点（M3 host_edge 的天然锚点） | `_attract_to_host_midpoint` |
| `repulse` | 软成本：UV 之间的 inflated bbox 重叠（`width + 2·clearance`），按 `repulse_weight × overlap²` 罚 | `_pairwise_repulse` |

所有二次项除以 `1_000_000` 把 µm² 折回 mm² 量级，使初温 `T_init=5000`、`T_min=1` 在 PA 用例上自然收敛。

### 2.2 初始放置

两遍：

1. 把所有 UV anchor 放到板心；
2. 凡是边的两端均为「具体坐标」（fixed 终端 / 已放置 UV）的，把对应 UV anchor 吸附到 host_edge 中点。

这是因为 UV 之间互相通过边引用，需要先有"种子坐标"才能算 host_edge 中点。

### 2.3 Metropolis 退火

```
for it in 1..iterations:
    pick random UV
    perturb anchor by U(-step_um, step_um)         # 默认 step=1.5mm
    occasionally flip side (1% probability)
    ΔE = compute_energy(new) - compute_energy(old)
    if ΔE ≤ 0 or random() < exp(-ΔE / T):
        accept
    else:
        revert
    T ← max(T_min, T × cooling)                    # 默认 cooling=0.995
```

终止：达到 `iterations` 上限，或 `T ≤ T_min` 且超过半数迭代（提前终止）。

### 2.4 Hint 通道

`hints_from_sa_result(result)` 把 `SaResult` 折叠成：

```python
{
    "C1": {"anchor_x_um": 18204, "anchor_y_um": 45004, "side": -1},
    ...
}
```

`solver.cpsat.build_model(ir, artifact, *, seed_hints=hints)` 在变量构建后调用 `model.AddHint(component_anchor[c][0], anchor_x_um)` 等三处把种子喂给 CP-SAT。这是软启发，CP-SAT 仍可自由跳出；所以 SA 退化（initial_energy ≤ final_energy）时 CLI 直接忽略 hint，回退 cold-start。

## 3. Phase 3 — A* 灵活段路由（`astar_flex.py`）

### 3.1 适用范围

只处理 `routing_class == FLEXIBLE_PATH` 的 SolverEdge（即 v3.3 中的 `trace`）。
PA 案例 0 条 flexible_path → A* 是 no-op；由 `tests/unit/test_solver_astar_flex.py` 用合成 1×1 trace 用例验证。

### 3.2 网格构建

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `grid_step_um` | 200 (0.2 mm) | PA 板 40×100 mm → 200×500 = 100k cells 上限 |
| `turn_cost` | 0.5 | 每次方向变化加成本 |
| `near_rf_cost` | 0.3 | 落入「靠近 RF 走线」环上的软成本 |
| `rf_inflate_um` | 100 | 在 `width/2 + clearance` 之外再额外膨胀的安全距离 |

障碍来源：

1. 所有 footprint bbox（`FrontendArtifact.components.*.bbox`）；
2. 所有非 flexible_path 路由的 inflated bbox（`width/2 + clearance + rf_inflate_um`）。

为防止起终点被自身的 footprint bbox 围住而无法找到出口，A* 在搜索前会把起点和终点各自的 3×3 邻域从障碍集中扣除。

### 3.3 A* 搜索

* 邻域：4-connected（仅水平/竖直，符合制造规则）；
* 启发：Manhattan；
* 成本：`step + (turn_cost if 转向) + (near_rf_cost if cell ∈ near_rf)`；
* 失败回退：保留原 polyline 不动，把 edge_id 记入 `AstarReport.failed_edges`（不阻塞 CLI 退出码）。

输出折线经 `_compress_polyline` 去除共线中间点，再把首尾点 snap 回精确端点坐标，避免半个网格步的漂移。

## 4. Orchestrator 重试策略（`orchestrator.py`）

伪代码：

```python
for attempt in 0..max_retries:
    sa = run_sa(ir, artifact, SaConfig(temperature_init = T0 * 1.5**attempt))
    cpsat = build_model(ir, artifact, seed_hints=hints_from_sa_result(sa))
    res = solve_model(cpsat, time_limit_s = T_limit * 1.5**attempt)
    if res.status_name in ("OPTIMAL", "FEASIBLE"):
        break
geom = extract_geometry(...)
geom, astar_report = route_flexible_paths(...)
audit = audit_geometry(...)
```

只有 status ∈ {INFEASIBLE, MODEL_INVALID} 时才重试；length-tolerance 违例视为数据问题，不重试（直接退出码 4）。

## 5. CLI

```bash
pcb_solve <layout.yaml> \
    [--time-limit 10] [--workers 8] [--max-retries 5] \
    [--no-sa] [--no-astar] \
    [--svg-out out/<name>.geom.svg] \
    [--report-out out/<name>.geom.json] \
    [--quiet]
```

退出码：

| 码 | 含义 |
| --- | --- |
| 0  | 求解成功 |
| 2  | layout 文件不存在 |
| 3  | status ∈ {INFEASIBLE, MODEL_INVALID, UNKNOWN}（已重试到 max_retries 仍失败） |
| 4  | locked-edge 长度超容差（0.5%） |

JSON 报告比 M4 `cpsat_solve` 多 `attempts`、`sa.{initial_energy, final_energy, accepted, rejected, ...}`、`astar.{routed_edges, failed_edges}` 三组字段。

## 6. 何时关闭 SA / A*

| 场景 | 推荐开关 |
| --- | --- |
| PA 案例（CP-SAT 11 ms 即得 OPTIMAL） | `--no-sa --no-astar`（与 M4 行为完全一致，最快） |
| UV 数量 ≥ 30 / CP-SAT 时常 INFEASIBLE | 默认开 SA（hint 显著缩短搜索时间） |
| YAML 中含 `routing_class == trace` 边 | 必开 A*（否则 polyline 直连可能穿过 RF 障碍） |

## 7. 已知限制

* SA 为单线程实现；UV ≥ 50 时建议把 `iterations` 调到 5000+；
* A* 网格步长固定 200 µm，未实现自适应/层级网格（v7 计划）；
* A* 仅 4-connected，不支持 45° 折线（M6 弯折渲染会处理 mitered_45 等几何样式，但路径本身仍走 90°）；
* 不支持 multipoint / Steiner（FLUTE/RSMT 留作 v7 扩展，见 ITERATION-PLAN ADR-6）。

## 8. 一键验证

```bash
./scripts/verify_m5.sh
```

依次跑：black / ruff / mypy / pytest（172 项） / topology_viz / frontend_compile / solver_ir / cpsat_solve / **pcb_solve**，全绿即 M5 通过。
