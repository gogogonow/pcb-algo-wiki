# preA Topology-First 构建设计

**Date:** 2026-05-18  
**Status:** Approved（interactive review）  
**Scope:** preA 阶段仅按 node 定义构建微带线 + 串联 RLC 拓扑几何，不做走线求解。

---

## 1. 目标

preA 的核心目标从“坐标求解+多轮修正”收敛为：

1. 把 microstrip 连接关系完整画出来；
2. 保持每条边 `width/target_length` 语义不变；
3. 串联 RLC 按连接方向和 footprint 几何（pin pitch、body 尺寸）体现出来；
4. 不做障碍绕行、冲突让位、越界裁剪。

---

## 2. 架构

新增统一的 preA 拓扑模型构建器（Topology-First）：

- 输入：`FrontendArtifact` + `universal_junction templates` + `NodePlan(seed)`
- 输出：`PreATopologyModel`
  - `edges`: 每条 microstrip 的两端绘制点（mm）+ width + target_length
  - `endpoints`: endpoint 坐标（用于诊断/标签）
  - `uv_placements`: preA 串联/UV 器件落位（anchor、rotation、pads）
  - `diagnostics`: 退化边、缺失模板、端点缺失等

所有 preA 输出统一消费这一个模型：

- `preA.svg`
- `preA.json`
- `viewer.json.phases.preA`

避免“SVG 一套逻辑、viewer 一套逻辑”的漂移。

---

## 3. 算法规则（严格版）

### 3.1 Edge 构建

仅处理：

- `edge_type == "microstrip"`
- `len(connections) == 2`

对每条边生成主段：

- 方向来源：
  1. 若边属于 `universal_junction` branch，按 branch 几何（`angle/offset_u/offset_v` + `reference_edge`）计算方向；
  2. 否则用 seed endpoint 方向（`NodePlan`）作为 fallback。
- 长度：
  - `target_length` 存在时，主段长度 **强制等于** `target_length`；
  - 否则使用 seed 端点距离（再兜底为最小长度常量）。
- 线宽：
  - 直接使用 edge.width（渲染时 mm→px）。

### 3.2 Node / Junction 规则

- `universal_junction` 严格按 template 分支构建；
- `reference_edge` 定义分支坐标系；
- 每个 branch 输出独立 edge 端点覆盖（edge-local endpoints）；
- 不做全局位置协调，不做额外松弛。

### 3.3 串联 RLC 规则（几何精度优先）

对 2-pin UV/RLC：

- 从 endpoint 坐标反推出 anchor 与方向；
- pin 间距严格等于 footprint pin pitch；
- body 使用 footprint dimensions（无 dimensions 时回退 pad span + margin）；
- 旋转角由两 pin 连线决定；
- 输出到 `preA.uv_placements`（viewer 可直接绘制）。

---

## 4. 输出数据约定

### 4.1 preA.json

- `edges[].endpoint_positions_mm`：来自 `PreATopologyModel.edges`
- `edges[].render_endpoint_positions_mm`：与绘制端点一致
- 新增/强化 `uv_placements`：preA 器件几何结果

### 4.2 viewer.json

- `phases.preA.edges` 使用同一套 preA 模型坐标
- `phases.preA.uv_placements` 与 `preA.json` 同源

---

## 5. 不做项（明确排除）

- 不做 A* / rip-up / 路径寻优
- 不做障碍冲突优化
- 不做越界裁剪
- 不做“画面美化优先”自动调位

---

## 6. 验证标准

1. `U731031_pin2_seg1/2/3/4` 在 preA 全部可见；
2. 每条可见边长度与 `target_length` 一致（允许渲染离散误差）；
3. 串联 RLC 在 preA 具备正确 pin pitch 和 body 几何；
4. `preA.svg`、`preA.json`、`viewer preA` 三者拓扑一致。

---

## 7. 风险与防护

- 风险：去掉坐标协调后，局部重叠会增加。  
  接受：这是该模式的有意取舍，preA 目标是“拓扑正确性优先”。
- 风险：无 template 的节点方向可能不稳定。  
  防护：记录 diagnostics，且 fallback 明确（seed 方向）。

