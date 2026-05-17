# 骨架优先三阶段路由器（Skeleton-First Router, v7）

> ✅ 文档状态：当前主线算法文档（默认入口 `pcb_solve` / `pcb_solve_v2`）。
>
> **状态**：M10 系列已交付。替代 v6（CP-SAT 联合 placement+geometry）。
> **入口**：`pcb_solve`（已切换至 v2）/ `pcb_solve_v2`；旧入口保留为 `pcb_solve_v1`。
> **回归用例**：`rf_layout_simplified.yaml`。

## 设计动机

v6 把"器件位置"与"走线几何"放在同一组 CP-SAT 变量里联合求解，PA 案例 22 条 RF 边只能成功 5 条，剩余高度交叉。M9 复盘定位 5 类根因，但本质上都源于一个架构缺陷：

> 解空间 = O(器件位置 × 几何点) 太大，NoOverlap 弱，路由可达性不感知。

v7 反转思路：**先布"看得见的金属"（微带线骨架），器件位置由金属位置反推**。

## 三阶段流水线

```
v3.3 YAML
   │
   │ ① Frontend Compiler (沿用 M2/M3：lint / expand / triage / UV / junction)
   ▼
SolverIR
   │
   │ ② Phase A — Skeleton Routing
   │    · 节点 LP 预解 (node center + UV anchor)
   │    · 100 µm 八角栅格 A* 通道路由
   │    · 优先级：width × target_length 降序
   │    · 长度补偿：多段 hairpin meander
   │    · Rip-up & Reroute（≤ 50 轮）
   ▼
SkeletonGeometryIR
   │
   │ ③ Phase B — UV 就近吸附
   │    · 拖挂 UV：直接由 microstrip 端点反推
   │    · 浮动 UV：free-space 评分采样
   │    · A↔B 反馈（≤ 3 轮）
   ▼
PlacedGeometryIR
   │
   │ ④ Phase C — Flex 余下
   │    · routing_class == flexible_path 的边
   │    · M5 `solver.astar_flex.route_flexible_paths` 在 Phase B 障碍图上路由
   ▼
FinalGeometryIR  →  Postproc (bend / DRC / LVS / crossing) → SVG / 报告
```

每一阶段都强制持久化 `out/{project}.{phaseA,phaseB,phaseC,final}.{svg,json}`，便于独立验收与回放调试。

## 关键模块（`src/solver/v2/`）

| 模块 | 职责 |
|---|---|
| `node_planner.py` | 节点 LP + UV anchor 候选 |
| `channel_grid.py` | 100 µm 八角栅格 + 障碍图 |
| `skeleton_router.py` | 单边 A* + 长度上界裁剪 |
| `length_meander.py` | 多段蛇形长度补偿 |
| `rip_up.py` | 失败回滚 + 优先级提升 |
| `uv_adhesion.py` | Phase B 拖挂回填 + 浮动评分 |
| `freespace.py` | 200 µm free-space 二值 mask |
| `orchestrator.py` | A→B→C 编排 + 阶段持久化 |

## 评判矩阵

| 指标 | Phase A 后 | Phase B 后 | Final |
|---|---|---|---|
| 微带线交叉数（M8 critical） | = 0 | = 0 | = 0 |
| max length error | ≤ 1% | ≤ 1% | ≤ 1% |
| UV 落位率 | 拖挂部分已落 | 9/9 | 9/9 |
| DRC critical | ≤ 5（允许 UV 未落位告警） | = 0 | = 0 |
| Wall time（PA） | ≤ 30 s | ≤ +10 s | ≤ +5 s |

## 与 v6 的关系

- **保留**：`src/schema/`、`src/frontend/`、`src/postproc/`、`src/output/svg_full.py`、`src/solver/astar_flex.py`（被 Phase C 复用）。
- **废弃**：v6 的 `cpsat / sa_floating / octilinear_router` 求解链；其测试已在 M10f 中移除。源代码模块暂留作向后兼容（`pcb_solve_v1` 别名仍可启动）。

## 参考

- `ITERATION-PLAN.md` §M10
- `ALGORITHM-OVERVIEW.md`（v7）
- 复盘：`docs/m9-postmortem.md`（v6 失效根因分析）
