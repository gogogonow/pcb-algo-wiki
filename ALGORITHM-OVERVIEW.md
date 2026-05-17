# 算法总览（当前主线：v2 Skeleton-First）

> 本文档描述当前默认实现：`pcb_solve` / `pcb_solve_v2`。  
> 历史 CP-SAT 主求解路线不再是默认执行路径。

---

## 1. 总体流程

```text
v3.3 YAML
  -> Frontend 编译
  -> PreA 位置求解
  -> Phase A 骨架路由（A* + rip-up + 长度补偿）
  -> Phase B UV 吸附
  -> Phase C 浮动布局 + flexible_path 路由 + DRC
  -> 分阶段 SVG/JSON + final SVG + summary
```

---

## 2. 各阶段职责

### 2.1 Frontend 编译

- 输入校验与容错（lint repair + warning）
- footprint/pad 展开到可求解结构
- edge 分类（locked/free/flexible）
- node 归一化

输出：`FrontendArtifact`（供后续阶段统一使用）。

### 2.2 PreA 位置求解

- 结合目标长度与节点连接关系，生成初始端点位置；
- 为 Phase A 提供可路由的几何种子。

输出：`*.preA.json` + `*.preA.svg`。

### 2.3 Phase A 骨架路由

- 在通道栅格上进行微带主干路由；
- 失败边执行 rip-up 重试；
- 对长度受约束边做补偿（含 meander/chamfer 处理链）。

输出：`*.phaseA.json` + `*.phaseA.svg`。

### 2.4 Phase B UV 吸附

- 将 UV 器件吸附到 Phase A 骨架结果；
- 输出器件姿态与 pin 位置，减少后续绕线代价。

输出：`*.phaseB.json` + `*.phaseB.svg`。

### 2.5 Phase C 浮动布局布线

- 对 floating 器件执行 SA 放置；
- 对 `flexible_path` 执行 A* 路由；
- 对重叠/交叉执行 DRC 检查与统计。

输出：`*.phaseC.svg` + `summary.json`。

---

## 3. 关键设计原则

1. **骨架优先**：先保证关键微带主干，再处理附属吸附与灵活走线。
2. **阶段解耦**：每阶段可单独观察/回归，便于定位问题。
3. **数据驱动**：对 YAML 变更尽量通过 frontend 编译层吸收，不把规则硬编码到后续阶段。
4. **结果可评判**：每阶段都有可视化产物与 summary 指标。

---

## 4. 当前与历史路线

### 当前默认

- `pcb_solve` → `pcb_solve_v2`
- `pcb_solve_v2`（Skeleton-First）

### 历史对照（非默认）

- `pcb_solve_v1`
- `cpsat_solve`

这些工具保留用于回归比对，不作为主线方案。

---

## 5. 产物说明

典型产物（`out/PA_Module_Simplified.*`）：

- `preA.{json,svg}`
- `phaseA.{json,svg}`
- `phaseB.{json,svg}`
- `phaseC.svg`
- `final.svg`
- `summary.json`

`summary.json` 重点包含：

- `status`
- `phase_a.routed/total`
- `phase_b.uv_placed/uv_total`
- `phase_c.flex_routed/flex_failed`
- `wall_total_s`

