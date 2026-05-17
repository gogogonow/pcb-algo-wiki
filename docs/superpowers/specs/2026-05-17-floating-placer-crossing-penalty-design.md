# Floating Placer 交叉惩罚升级设计

## 背景

phaseC flex 路由当前 3/6 通过。剩余 3 条失败 (`flex_ubias_rpull`, `flex_ubias_ctrl_b`,
`flex_dec2_gnd`) 的根因不是 A* / DRC，而是 SA placer 把 floating 组件放到了
与目标隔着已锁定 RF 总线（如 `PWR_VDD_bus`）的另一侧。任何直连飞线都被迫
跨越 locked RF 走线，物理上不可达。

`src/solver/v2/floating_placer.py` 当前的 SA 代价函数包含：
HPWL / overlap / boundary / routability（基于路径采样） / escape_alignment，
**缺少"飞线-RF 走线相交"与"飞线-飞线相交"这两项几何代价**，导致 SA
无法把这种"跨 bus"摆位识别为差解。

## 目标

在 SA 代价函数中加入两项纯几何的交叉惩罚，让 placer 输出近平面布局，
直接消除当前 3 个 flex 失败。

- **成功标准**：rf_layout_simplified.yaml 跑出 `phase_c.flex_routed ≥ 5/6`
- **不妥协**：新增 4 个 pad-granular DRC 测试与所有 v2 既有测试仍然全绿
- **性能预算**：SA 总耗时增长 < 1.0s（当前约 1.8s）

## 方案

### 几何代价 1：rf_bus_cross_penalty

对每条至少一端为 floating 的 flex edge，把当前 SA 候选状态下的两端点连成
理想"飞线"线段，遍历所有已 locked 的 RF route segment，使用标准 CCW
线段相交判定计数：

```
cost += rf_bus_cross_weight × Σ intersections(airwire_i, rf_seg_j)
```

权重默认 `50.0`，比 HPWL（默认 1.0）大两个量级，让 SA 几乎绝不接受跨 bus
的状态。共享端点不算相交。

### 几何代价 2：airwire_cross_penalty

所有 floating-involved flex edge 两两组合，计数飞线相交：

```
cost += airwire_cross_weight × Σ_{i<j} intersections(airwire_i, airwire_j)
```

权重默认 `5.0`，介于 HPWL 与 RF bus 之间，让 SA 在等长方案中倾向无交叉。

### 边界处理

- 共享端点（pin 重合）不算相交
- 一条线段完全在另一条上（共线重叠）算 1 次相交
- 飞线退化为点（同一组件内）跳过

## 文件改动

| 文件 | 改动 |
|------|------|
| `src/solver/v2/floating_placer.py` | 加 `_segments_intersect()` / `_rf_bus_cross_cost()` / `_airwire_cross_cost()`；`PlacerConfig` 加 `rf_bus_cross_weight=50.0`, `airwire_cross_weight=5.0`；`energy()` 加两项 |
| `tests/unit/v2/test_floating_placer_rf_bus_cross.py` (新) | mini case：候选位置 A（不跨 bus）vs B（跨 bus），断言 energy(B) > energy(A) 至少 40 |
| `tests/unit/v2/test_floating_placer_airwire_cross.py` (新) | X 形 vs 平行飞线，断言 energy(X) > energy(平行) 至少 4 |
| `tests/regression/test_real_case_flex_routed.py` (新或扩) | 跑 rf_layout_simplified.yaml，断言 `phase_c.flex_routed ≥ 5` |

## 测试策略

TDD：先写 3 个失败的测试，再实现两项代价让它们绿。

## Fallback

如果加这两项后只到 4/6 或 5/6，按优先级尝试：
1. 提高 `rf_bus_cross_weight` 到 100
2. SA `iterations` 翻倍
3. 把 `airwire_cross_weight` 提到 20

如果仍不到 5/6，留作下一阶段引入拓扑布线引擎（RBS + Delaunay）。

## 验证

- `./scripts/verify_m1.sh` 全绿（baseline 8 失败不变）
- 手跑 CLI 检查 phaseC summary 的 `flex_routed` 与 `flex_failed`
- 比对 phaseC SVG，确认 floating 组件不再跨越 RF bus
