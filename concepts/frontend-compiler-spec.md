# Frontend Compiler 规范（M2）

> ✅ 文档状态：当前有效。适用于默认 v2 主线的输入编译阶段。
>
> 落地于 `src/frontend/`，对应 [`ITERATION-PLAN.md`](../ITERATION-PLAN.md) §M2 与 [`ALGORITHM-OVERVIEW.md`](../ALGORITHM-OVERVIEW.md) §2.1 ① Frontend Compiler。

Frontend Compiler 把上游 v3.3 YAML（外部 EDA 接口）容错地编译为 v6 求解器可消费的中间产物 `FrontendArtifact`。本期（M2）不涉及 UV 数学求解、universal_junction 几何方程展开或 CP-SAT 接入 —— 那些由 M3/M4 承担；M2 只交付**结构化前置数据**与**容错 lint**。

## 1. 管线

```
v3.3 YAML
   │
   ▼
┌──────────────┐  ┌────────────────────┐  ┌──────────────────┐
│ M2a Lint     │→ │ pydantic v33 parse │→ │ M2b expand        │
│ typo / enum  │  │ (lenient)          │  │ components +      │
│ / 顶层字段    │  │                    │  │ obstacles         │
└──────────────┘  └────────────────────┘  └──────────────────┘
                                              │
                                              ▼
                                  ┌──────────────────────────┐
                                  │ M2c triage + normalize   │
                                  │ routing_class 三档       │
                                  │ 节点别名归一              │
                                  └──────────────────────────┘
                                              │
                                              ▼
                                       FrontendArtifact
```

## 2. 模块契约

### 2.1 `lint.py` — `lint_layout(raw) -> (repaired_dict, LintReport)`

| 行为 | 处理 |
|---|---|
| 已知 typo（`redius`/`cicle`/`lenght`/`widht`/`hieght`） | 自动改写键名**或**字符串值；记 `LintRepair(kind="typo")` |
| 未知 `bend_style` | warning code `unknown_bend_style`，原值透传 |
| 未知 `launch_rule` | warning code `unknown_launch_rule`，原值透传 |
| 未知 `pad_geometry.shape` | warning code `unknown_pad_shape`，原值透传 |
| 未知顶层字段 | warning code `unknown_top_level_field`，整段透传 |
| 任意 `errors`（当前未启用） | 阻塞 CLI 退出码 = 1 |

LintReport 是不可变 dataclass，便于 JSON 序列化。

### 2.2 `expand_components.py` — `expand_components(layout) -> dict[str, ComponentExpansion]`

- `placement.is_floating == False` 且 `(x, y)` 完整 → `placement_kind == "fixed"`，每个 footprint pin 经
  `abs = (x, y) + R(rotation) · (local_x, local_y)` 给出 `ExpandedPad(kind="fixed")`。
- `placement.type == "parametric_uv"` → `placement_kind == "parametric_uv"`，每个 pin 写 `kind="uv_deferred"`（坐标为 None），并附 `UvMeta(anchor_pin, reference_net, rotation_domain=(0,90,180,270))` —— 实际 (anchor_x, anchor_y, rotation, offset_v_side) 求解延迟到 M3。
- 其他/缺失放置 → `placement_kind == "unknown"`，pads 为空。

固定器件的 `bbox` 由 `footprint.dimensions.{width,length}` 经 `placement.rotation` 旋转后取最小外接轴对齐矩形。

### 2.3 `obstacles.py` — `build_obstacles(layout, expansions)`

返回 `tuple[Obstacle, ...]`，包含：

- 1 个 `kind="board_outline"`（板框矩形）；
- 0+ 个 `kind="keepout"`（v3.3 `keepout_zones` 透传）；
- 0+ 个 `kind="footprint_bbox"`（每个固定器件 1 个）。

UV 器件的 footprint bbox **不**进入 obstacles —— 等 M3 求解后才能给出绝对坐标。

### 2.4 `triage.py` — `triage_edges(layout) -> dict[str, TriagedEdge]`

D3 决策的纯函数实现：

| 触发条件 | `routing_class` |
|---|---|
| `type=microstrip` ∧ `constraint.target_length is not None` | `rf_constrained_locked` |
| `type=microstrip` ∧ `constraint.target_length is None`     | `rf_constrained_free`   |
| `type=trace`                                              | `flexible_path`         |
| `type=lumped_*` 等 v4 兜底                                | 沿用原 `routing_class`（默认 `unknown`） |

`bend_style` / `launch_rule` 透传到 `TriagedEdge`，由 M6 后处理消费。

### 2.5 `normalize_nodes.py` — `normalize_nodes(layout) -> dict[str, NormalizedNode]`

别名表：

| 原 type | 归一为 |
|---|---|
| `universal_node` | `pad_junction` |
| `impedance_step` | `stepped_impedance` |

保留：`universal_junction`（D4 关键模板节点）、`t_combiner_junction`、`t_junction`、`pad_junction`、`stepped_impedance`、`floating_shunt_tap`、`component_pad_junction`。

未知 type 透传（`normalized_type == original_type`），不阻塞。

### 2.6 `compile.py` — `compile_layout(path) -> FrontendArtifact`

编排入口：lint → pydantic 解析 → expand → obstacles → triage → normalize → 装配 `FrontendArtifact`，并提供 `artifact_to_dict` 用于 JSON 序列化（递归 `dataclasses.asdict`）。

## 3. 真实案例 (`PA_Module_Simplified`) 编译产物快照

```
fixed_pads: 9             # IC1×4 + TP1..TP5
uv_components: 9          # C1..C6 + R1..R3
obstacles: 7              # board + 6 fixed footprint bbox
edges: locked=15 free=7 flex=0 other=0
nodes: universal_junction=2 t_junction=4 t_combiner_junction=2
lint: repairs=3           # 2 × redius + 1 × cicle
       warnings=2          # bend_style: curved, launch_rule: normal
       errors=0
```

> ITERATION-PLAN §3 原估 14 locked / 8 free，实测为 15 locked / 7 free（一条 RF 段在数据中携带了 `target_length`，原估算遗漏）。功能正确性不受影响。

## 4. 下游消费约定

- M3 `frontend/uv_resolver.py` 读取 `artifact.uv_components` + `artifact.edges`，推导 host_edge 与变量域。
- M3 `frontend/universal_junction.py` 读取 `artifact.nodes` 中 `normalized_type == "universal_junction"` 的节点 `branches[]`，生成 CP-SAT 线性约束模板。
- M4 CP-SAT 读取 `artifact.obstacles` 与 `artifact.edges` 构建 NoOverlap。
- M6 后处理消费 `TriagedEdge.bend_style` / `launch_rule` 与 `LintReport.warnings`，决定渲染或回退。

## 5. CLI

```bash
frontend_compile rf_layout_simplified.yaml \
  [--out out/<project>.frontend.json] [--lint-only]
```

退出码：`lint_report.errors == 0` → 0，否则 1。
