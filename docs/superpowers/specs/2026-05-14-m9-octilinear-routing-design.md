# M9 — 走线交叉硬约束化 + Octilinear A* 路径搜索

**日期**：2026-05-14
**状态**：设计批准 → 实施
**作者**：Copilot（自动驾驶模式）

---

## 1. 背景与问题

M8 端到端运行 PA 板（22 边、8 器件）报告 66 对走线重叠 / 52 critical（>1mm）/
1 个含 30 对的热点。M8 诊断把 priority 最高的 GAP 标记为
`GAP-PLACEMENT-DENSITY`，但用户审视后判定根因更深：

> *"应该把走线、器件、焊盘、微带线交叉作为布局布线过程硬要求，而不是靠后面迭代。"*
> *"微带线只是宽度、长度不能变，但是也可以绕线，使用 45 度切角绕线。"*

代码考古确认这是**架构性缺陷**：

| 现状 | 缺陷 |
|------|------|
| `cpsat.py` 为每条 edge 建模 "端点 A + 端点 B + Manhattan 距离 = target_length" | 求解器中没有 trace path 变量；走线视为两点直线 |
| NoOverlap 仅作用于器件 BBox | 走线/走线、走线/焊盘、走线/器件 几何冲突对求解器不可见 |
| `astar_flex.py` 仅对 `flexible_path` 类运行 | PA 板 22 条边全是 `rf_constrained_*`，A* 实际 routed=0 |
| `rf_constrained_locked` 长度锁 = 端点 Manhattan = target | 与微带"宽度长度锁、路径可绕（含 45° 切角）"物理实际不符 |
| `bend.mitered_45` 仅对已有角点切角 | 不是绕线 |
| `meander.py` 仅在单段插发夹环 | 不会避让其它线 |

## 2. 设计目标

1. **交叉硬约束化**：走线/走线、走线/焊盘、走线/器件 在求解过程中保证零交叉
   （硬障碍），而不是后处理诊断。
2. **微带线物理一致**：宽度/长度锁；路径自由（含 45° 切角绕线）；
   长度匹配优先靠绕路实现，meander 仅作为兜底。
3. **保留 CP-SAT 在 placement 层**（业界标准三层解耦：placement → routing
   → detailed），但解除其"端点距离=length"等式过度约束。
4. **失败时 rip-up-and-reroute**（KiCad / freerouting / TritonRoute 标准做法）。
5. **板尺寸不变**，placement + routing 协同避免拥挤（SA cost 加交叉惩罚）。

## 3. 算法架构

```
YAML
 │
 ▼
frontend_compile        (M2，不变)
 │
 ▼
solver_ir compile       (M3，不变)
 │
 ▼
SA seed (run_sa)        (M5，**新增 crossing_penalty 项**)
 │
 ▼
CP-SAT placement        (M4，**修改：长度等式 → 不等式**)
 │  · 决定 component xy + UV 端点滑动 + offset_v_side
 │  · `rf_constrained_locked`：Manhattan ≤ target_length + tol（>则 infeasible）
 │  · 不再约束 trace 几何
 ▼
extract_geometry        (M5，不变 — 仍输出端点 polyline 占位)
 │
 ▼
route_orchestrator      (**M9 新增**)
 │  按优先级布线 + rip-up-and-reroute
 │   1. 排序：locked > free > flex；同优先级 target_length 降序
 │   2. for each edge：
 │       octilinear A* on obstacle map (含已布走线)
 │       若失败 → 把"占据搜索区"的最近 N 条边加入 ripup 队列
 │   3. 最多 K=10 轮，仍失败则报 unroutable
 ▼
postproc bend           (M6，不变 — 渲染 mitered_45 等)
 ▼
postproc meander 兜底   (M7，仅当 A* 长度未达成才调用)
 ▼
DRC + crossing report   (M6/M8，预期 critical=0)
 ▼
SVG (含 pads)           (M8，不变)
```

## 4. 关键算法

### 4.1 CP-SAT 长度约束改造（src/solver/cpsat.py）

**改动位置**：第 ~227 行 `# 6. Length-lock for rf_constrained_locked edges`

**Before**：
```python
# Manhattan 距离 == target_length（强 equality）
model.Add(dx + dy == target_um)  # 通过辅助变量等价
```

**After**：
```python
# Manhattan 距离 ≤ target_length + tol（必要条件）
# 大于该值则物理不可达 → 让 CP-SAT infeasible
model.Add(dx + dy <= target_um + tol_um)
```

**审计逻辑同步更新**（`audit.py`）：locked 边的"长度合规"判定从
"|Manhattan − target| < tol" 改为 "trace_path_length 在 ±tol 内"
（trace path 由 A* 输出，audit 读 polyline 实际长度）。

### 4.2 Octilinear A*（src/solver/astar_octilinear.py 新增）

**网格**：与 `astar_flex` 一致使用 µm 整数网格，可配步长（默认 100µm = 0.1mm）。

**邻居 8 方向**：
```
(±1, 0), (0, ±1)              # 轴向，cost = step
(±1, ±1)                      # 对角（45°），cost = step × √2
```

**节点 cost = 累计走线长度（µm）**

**边 cost 加项**：
- **拐角代价**：
  - 直行 → +0
  - 45° 转向 → +turn_cost_45 (默认 0.3 × step)
  - 90° 转向 → +turn_cost_90 (默认 1.0 × step)
- **远离起/终点 host_edge 法向**：起点首段沿微带"出脚"方向有 bonus（保留物理几何）

**长度软目标 / locked 硬约束**：
- 对 `rf_constrained_locked` 边：终点接受条件 = `target − tol ≤ g_score(goal) ≤ target + tol`；
  若达到 goal 时长度不在容差内，**不接受**该路径，继续探索（用扩展 state 包含路径长度）。
- 对 `rf_constrained_free`：goal 接受任意长度；cost 中含 `λ_len × |g − target|`
  软目标项（若有 target）。
- 对 `flexible_path`：goal 接受任意长度，cost 中无长度项。

**State**：`(cell, last_dir, length_band)` — 把 `g_score` 离散到 `target_length / step`
个 band 以保持有限状态空间。无 target 时退化为 `(cell, last_dir)`。

**Heuristic**：`(|dx| + |dy| − min(|dx|,|dy|)) × step + min(|dx|,|dy|) × step × √2`
（admissible 八方向距离）。

**障碍图（obstacle_map.py）**：
- 器件 BBox + clearance；
- 焊盘多边形 inflate（取 footprint pad geometry）+ clearance；
- 已布走线 polyline 沿宽度方向 inflate (route_width/2 + clearance)；
- 起/终点 3×3 邻域强制 free（让走线从 pad 内"出脚"）。

### 4.3 Rip-up-and-Reroute（src/solver/route_orchestrator.py 新增）

**排序**：
```python
def sort_key(edge):
    return (
        priority_of_class[edge.routing_class],   # locked=0, free=1, flex=2
        -float(edge.target_length or 0),          # 长边先布
        edge.edge_id,                             # 稳定排序
    )
```

**算法**：
```
queue = sorted(edges)
routed = []
ripup_count = defaultdict(int)
for round in range(K):  # K = 10
    for e in queue:
        path = octilinear_astar(e, obstacles_from(routed))
        if path is None:
            # 找出"占据 e 起终点 BBox + 最短 manhattan 矩形"内的已布边
            blockers = find_blockers(e, routed)
            ripup_count[e.id] += 1
            for b in blockers:
                routed.remove(b)
                queue.insert(0, b)
            queue.insert(0, e)  # 让 e 优先重试
            break  # 重新跑本轮
        else:
            routed.append((e, path))
    else:  # for-else: 全部成功
        return routed
return UnroutableError(remaining=queue)
```

**failure handling**：
- 某条边 `ripup_count > MAX_RIPUP_PER_EDGE`（默认 3）→ 标记为 unroutable，跳过；
- K 轮后仍有 unroutable → 返回部分结果 + warning，exit_code=6。

### 4.4 SA crossing_penalty（src/solver/sa_floating.py 修改）

**新加 cost 项**：`crossing_penalty(λ_cross)`

**估算方法**（不跑 A*，用解析估算保持 SA 快速）：
- 对每对 edge (a, b)：构造 a 端点连线段 + b 端点连线段；
- 计算两段相交（如交叉）或最近距离（如平行接近）；
- 累加 `max(0, clearance_required − dist) ^ 2` 项；
- λ_cross 默认 100（与 boundary_weight 同量级）。

### 4.5 集成到 pcb_solve（src/tools/pcb_solve.py）

新 flag：
```
--octilinear / --no-octilinear      # 启用新 A*（默认 on）
--ripup-rounds INT                  # rip-up 重试次数上限（默认 10）
--astar-step-um INT                 # 网格步长（默认 100）
```

旧 `astar_flex` 仍可用（`--no-octilinear`），M10 删除。

## 5. DoD

| 指标 | 目标 | 验证方式 |
|------|------|----------|
| PA crossing critical | **= 0**（M9 主目标） | M8 报告 + verify_m9.sh 断言 |
| PA DRC critical | = 0 | drc.json |
| locked 边长度误差 | < 0.5% | audit |
| 端到端 wall | < 30s | pcb_solve --quiet |
| 测试通过 | ≥ 280（含 ≥ 35 新增） | pytest |
| pad polygon 渲染 | ≥ 20 | M8 SVG |

## 6. 备选方案（已拒绝）

| 方案 | 拒绝理由 |
|------|----------|
| B 纯 SA + 路径搜索 | 放弃 CP-SAT placement 最优性证明，重写量过大 |
| C 后处理 ripup | 交叉仍非硬约束，与用户诉求矛盾 |
| D CP-SAT + 走线段 NoOverlap | 变量规模 100×，求解器无法收敛 |

## 7. 风险

- **R1 A* 长度匹配收敛慢**：`length_band` state 维度可能让搜索空间膨胀。
  缓解：分层搜索（先无 length 找路径，再 ε-步长扩长度）+ 节点 cap 1e6。
- **R2 rip-up 震荡**：缓解：`MAX_RIPUP_PER_EDGE=3` + 失败次数衰减权重。
- **R3 兼容性**：`--no-octilinear` 保留旧行为；旧测试不受影响。

## 8. 文件清单

新增：
- `src/solver/obstacle_map.py`
- `src/solver/astar_octilinear.py`
- `src/solver/route_orchestrator.py`
- `tests/unit/test_astar_octilinear.py`
- `tests/unit/test_route_orchestrator.py`
- `tests/unit/test_obstacle_map.py`
- `scripts/verify_m9.sh`
- `concepts/octilinear-router.md`

修改：
- `src/solver/cpsat.py`：长度约束 = → ≤
- `src/solver/audit.py`：长度合规读 polyline 实长
- `src/solver/sa_floating.py`：加 crossing_penalty
- `src/solver/orchestrator.py`：A* 阶段切到新 orchestrator
- `src/tools/pcb_solve.py`：新 flag
- `ITERATION-PLAN.md`：§M9
- `README.md`：M9 运行指引

## 9. 落地后预期效果

- M8 报告对 M9 输出：critical_pair_count = 0、gaps = []；
- SVG 视觉上微带线大量出现 45° 切角绕线（替代当前直线穿越）；
- locked 边长度通过路径绕路达成，不再依赖 meander；
- M9 完成即可宣告"PA 单层 PCB 自动布局布线 v1 真正闭环"。
