# Octilinear Router (M9)

> 8 方向 A* + Rip-up & Reroute，把走线交叉转为硬约束。

## 1. 背景

M5–M8 的求解链路把 `rf_constrained_*` 微带线视为**端点直线**，长度通过 CP-SAT
`Manhattan(A, B) = target_length` 等式锁死，重叠和交叉只能在 M8 后处理时
诊断。这与微带线"宽度/长度不变、路径可绕（含 45° 切角）"的物理实际不符
（详见 `docs/superpowers/specs/2026-05-14-m9-octilinear-routing-design.md`
§1）。M9 在保留 CP-SAT 做 placement 的前提下，把 routing 移交给一个
基于网格的 A* 搜索，并通过 rip-up 处理冲突。

## 2. 求解链路位置

```
YAML → frontend → solver_ir → CP-SAT placement (length-lock 上界)
       → octilinear A* (本模块)
       → bend / meander 后处理 → DRC / LVS / SVG / report
```

CP-SAT 在 `relax_length_lock=True` 时只保留 `Manhattan ≤ target + tol`
作为上界——下界由 A* 找到的最短折线决定，不足部分由 M7 meander 兜底。
`--no-octilinear` 可恢复 M5–M8 的等式锁行为，便于回归。

## 3. 算法

### 3.1 障碍图 `obstacle_map.py`

- **网格**：µm 整数网格，默认步长 `DEFAULT_GRID_STEP_UM = 100µm`
  （PA 板 40×100mm → 400×1000 cells）。
- **三类障碍**：
  1. 元件 BBox（按 `clearance` 膨胀）；
  2. 固定焊盘的方形 halo（默认半边长 `DEFAULT_PAD_HALO_UM = 300µm`）；
  3. 已布走线的逐段轴对齐 BBox（按 `half_width + clearance` 膨胀）。
- 起/终点格周围 `endpoint_halo_cells`（默认 2）会在搜索时强制设为可通行，
  以保证从焊盘上下"出/入针"。

### 3.2 八方向 A*  `astar_octilinear.py`

| 方向 | 步进代价 |
|------|---------|
| 上下左右 | 1.0 |
| 4 个对角 | √2 |

转弯额外代价：
- 0–1° 视为同向，0；
- ≤ 46°（45° 切角），`turn_cost_45 = 0.3`；
- 否则（90° 直角），`turn_cost_90 = 1.0`。

启发式使用 octile 距离 `dx + dy + (√2 − 2)·min(dx, dy)`，admissible 且
consistent。`max_expansions = 1_000_000` 兜底防止病态实例。

返回 `OctilinearResult`，包含压缩后的折线（删除共线中间点）、Euclidean
长度、节点扩展次数，以及在给定 `target_length_mm + length_tol_um` 时的
`length_overshoot` 标志（仅在最短路径已超长时为 True；偏短由 meander 兜底）。

### 3.3 路由编排  `route_orchestrator.py`

按下表优先级排序，长度长者优先：

| 类别 | 优先级 |
|------|--------|
| `rf_constrained_locked` | 0 |
| `rf_constrained_free`   | 1 |
| `flexible_path`         | 2 |

每一轮：

1. 取队首边，构造障碍图（已布的边作为障碍），跑 A*。
2. **成功**：记录到 `routed`，写回新的 `RoutePolyline`。
3. **失败**：调用 `_find_blockers` 找 BBox 与本边重叠的已布边，按
   `MAX_RIPUP_PER_EDGE = 3` 上限 rip 掉它们，把本边和被 rip 的边重新
   入队下一轮。
4. 当一轮内无任何进展或达到 `max_rounds`（默认 10），退出。

未能完成的边记入 `RouteOrchestratorReport.unrouted`，供 audit / DoD 检查。

## 4. SA 协同

`solver/sa_floating.py` 的能量函数中加入 `crossing_weight = 100.0`
作为软项（segment-segment 距离平方），让 placement 阶段倾向于把走线
"自然"分散开，缓解后续 routing 的拥挤。

## 5. CLI

```
python -m tools.pcb_solve <yaml>           \
    --octilinear / --no-octilinear         \
    --astar-step-um 100                    \
    --ripup-rounds 10                      \
    --bend --meander                       \
    --report-out out/report.json
```

`--octilinear` 默认启用；`--no-octilinear` 切回 M8 行为。报告 JSON 中新增
`octilinear` 段，记录 `routed_edges / unrouted_edges / rounds_used /
ripup_count / overshoot_edges / expansions_total`。

## 6. 兼容与限制

- 已布段使用轴对齐 BBox 近似，对 45° 段是过保守的（不会漏判碰撞但可能
  过早判定阻塞），交由 rip-up 解决。
- 未实现 length-band 多维 state（设计 §7 R1）：用"最短路径 + meander"
  的简化路径换取小状态空间。
- via 切层、多层走线超出 M9 范围。

## 7. 相关文件

- 设计：`docs/superpowers/specs/2026-05-14-m9-octilinear-routing-design.md`
- 代码：`src/solver/{obstacle_map,astar_octilinear,route_orchestrator}.py`
- 测试：`tests/unit/test_solver_{obstacle_map,astar_octilinear,route_orchestrator}.py`
- 验证：`scripts/verify_m9.sh`
