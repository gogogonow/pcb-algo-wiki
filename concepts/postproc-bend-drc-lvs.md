# M6 后处理：bend / DRC / 软 LVS

> ⚠️ 文档状态：**历史归档（M6）**。当前主线仍复用其中部分后处理思想，但实现入口已迁移到 v2 编排流程。
>
> 模块路径：`src/postproc/{bend,drc,lvs}.py` + `src/output/svg_full.py`
> CLI：`pcb_solve --bend / --drc-out / --lvs-out / --final-svg`

## 1. bend.py

将 M4/M5 输出的「直线段折点 polyline」按每条 `SolverEdge.bend_style`
渲染为弯折几何，仍然以 `RoutePolyline`（多点折线）形式输出，保持 SVG
渲染契约不变。

| bend_style | 处理 |
|---|---|
| `square` / 未设置 | 折点保持原样（90°） |
| `mitered_45` | 每个内角拆为两个折点（45° 倒角，倒角长度 `min(seg_a/3, seg_b/3, max(2·width, 1.5·width))`） |
| `rounded` / `curved` | 6 段折线近似 1/4 圆弧（半径同上 clamp） |
| 未知 | fallback 到 `square` + `BendReport.warnings` 记一条 |

倒角 / 半径都受相邻段长度的 1/3 上界约束，保证不"吃穿"短引出线
（例如 PA 的 0.5mm 短脚）。共线（cross product < 1e-9）的折点不视为内角。

## 2. drc.py

膨胀 BBox 两两相交检测（沿用 `solver/audit.py` 的整数 µm 量化思路）。

| 规则 | 默认 | 严重度 |
|---|---|---|
| `min_width` | `min(geom.routes[*].width)` | critical |
| `min_clearance` | `ir.clearance`（PA：0.15mm） | critical |
| `via_density` | 单层无 via，预留接口 | — |

**Waiver 机制**：CLI 把 M5 audit 输出的 `overlap_pairs`（已知的
CP-SAT BBox-NoOverlap 对 45° 斜段的近似缺陷，参见 ITERATION-PLAN
风险表 R4）作为 `known_overlap_pairs` 传入，这些 pair 被降级为
`severity="warning"` 并附 `(audit-waivered)` 标记。**M6 只把"后处理
新引入"的违规判为 critical。**

排除规则：
1. 共享端点的边对自动跳过（实际是连接关系）；
2. `net` 字段相同的边对自动跳过（同电气网络上的相邻段允许触碰）。

## 3. lvs.py

软网络一致性检查。PA YAML 不含 `logical_net` 字段，因此 `run_lvs`
返回 `LvsReport(skipped=True)`，CLI 退出码不受影响。

未来 v3.3+ 输入若提供 `logical_net_of_edge: dict[edge_id, logical_net]`
映射，就用 Union-Find 合并同 logical_net 的所有 endpoint，验证每个
逻辑网络是否单连通分量；不连通的列入 `mismatches` 并产出 warning。

## 4. svg_full.py

在 `postproc.geom_svg.render_geometry_svg` 基础上叠加：
- DRC critical 违规：红色虚线 BBox（dash 4-3）
- DRC warning 违规：橙色虚线 BBox
- 底部状态栏：`bends=N/skip=M/warn=K | DRC crit=X/warn=Y | LVS=skipped|mismatch=Z`

## 5. CLI 退出码（pcb_solve）

| code | 含义 |
|---|---|
| 0 | 全绿 |
| 2 | 参数错误（layout 文件不存在） |
| 3 | 求解失败（INFEASIBLE / UNKNOWN） |
| 4 | locked 长度超出 tolerance |
| **5** | **DRC critical > 0**（M6 新增） |

## 6. PA 基线表现（参考）

```
status=OPTIMAL attempts=1 wall=0.010s
bend: bended=0 skip=22 warn=0   # PA 全为 2 点直线，无内角可弯折
drc:  critical=0 warning=50      # 50 条 audit-waivered 已知 BBox 重叠
lvs:  skipped (no logical_net)
exit=0
```
