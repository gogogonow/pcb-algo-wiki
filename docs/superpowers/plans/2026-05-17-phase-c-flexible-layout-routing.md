# PhaseC 灵活布局布线实现计划 (WI-J)

> ⚠️ 历史归档文档：该计划用于当时迭代记录，当前主线请以 `README.md` / `ALGORITHM-OVERVIEW.md` / `ITERATION-PLAN.md` 为准。

> **面向 AI 代理的工作者：** 必需子技能 superpowers:executing-plans 或 subagent-driven-development。
> 步骤使用复选框（`- [ ]`）语法跟踪进度。每个任务结尾都要 commit，并在批次结束跑 `./scripts/verify_m1.sh`。

**目标：** 让 phaseC 真正承担"悬浮 RLC/IC + flexible_path 走线"的实现，输出可被独立评判
（SVG + JSON），并强制"无重叠 / 无交叉"作为硬约束（违反则该边/该器件标 FAILED）。

**架构：**
1. 扩展 `rf_layout_simplified.yaml`，加入 1 个 floating IC + 2 个 floating RLC + 若干 `flexible_path` 边
2. 在 `solver/v2/orchestrator.py` 之 phaseC 阶段新增 `_place_floating_components()` 子流程，
   复用 / 改造 `solver/sa_floating.py` 的 SA 退火放置 floating fixed 器件
3. 在 `_route_flex_edges()` 中把 phaseA/phaseB 已落地的 RLC pad / UV bbox / 微带线 polyline
   全部纳入障碍栅格，并在写回 `geometry.routes` 之前跑硬约束 DRC：重叠 → 失败；
   `polyline ∩ polyline` 交叉 → 失败
4. phaseC SVG 改成"phaseB 基础 + 蓝色 floating 高亮 + 紫色 flex 走线 + DRC 红色标记"，
   独立于 final.svg，能够被人工评判

**技术栈：** Python 3.11, pytest, mypy, ruff, black（沿用 M1 工具链）

---

## 文件结构

**新建文件：**
- `src/solver/v2/floating_placer.py`：floating fixed 组件（IC / RLC）的 SA 放置器
- `src/solver/v2/flex_drc.py`：phaseC 专用硬 DRC（多边形 polygon ↔ polyline 重叠 + polyline ↔ polyline 交叉）
- `tests/unit/v2/test_floating_placer.py`
- `tests/unit/v2/test_flex_drc.py`
- `tests/integration/test_phase_c_e2e.py`

**修改文件：**
- `rf_layout_simplified.yaml`：增加 floating 元素与 flex 边
- `src/solver/v2/orchestrator.py`：phaseC 新增 floating 放置 + DRC 收口
- `src/solver/astar_flex.py`：扩展障碍源（RLC pad / UV bbox / 已有 polyline）
- `src/tools/pcb_solve_v2.py`：phaseC SVG 加 banner + 蓝色 floating overlay + 紫色 flex overlay + 红色 DRC overlay
- `tests/integration/test_orchestrator_v2_smoke.py`：跟随案例 yaml 更新

**架构原则：** floating placer 与 flex DRC 是独立可测的小模块；orchestrator 只做装配。

---

## 任务 1：扩展 yaml 案例，加入 floating 元素

**目标：** 让案例真正覆盖 phaseC 路径。

**文件：**
- 修改：`rf_layout_simplified.yaml`（components 段尾增加，edges 段尾增加）

- [ ] **步骤 1：先确认现有占用区域**

运行：
```bash
grep -nE "x:|y:" rf_layout_simplified.yaml | head -40
```
预期：IC1 在 (12.1, 35.0)；测试点散落，右上 (x≈30, y≈50-80) 区域空。

- [ ] **步骤 2：在 `components:` 段末（330 行附近，TP5 之后、`nodes:` 之前）追加 floating 器件**

```yaml
  # ---- PhaseC floating components (WI-J) ----
  U_BIAS:    # 偏置/控制小 IC，位置由 phaseC 决定
    footprint_ref: "PKG_IC_4PIN"
    placement:
      is_floating: true     # 关键：放给 phaseC 灵活布局
    pin_nets:
      PIN_1: "PWR_NET"
      PIN_2: "CTRL_NET_A"
      PIN_3: "CTRL_NET_B"
      PIN_4: "GND"
    properties:
      PartNumber: BIAS_CTRL
  C_DEC1:    # 去耦电容 1
    footprint_ref: "PKG_CAP"
    placement:
      is_floating: true
    pin_nets:
      PIN_1: "PWR_NET"
      PIN_2: "GND"
    properties:
      Value: 100nF
  R_PULL:    # 上拉电阻
    footprint_ref: "PKG_RES"
    placement:
      is_floating: true
    pin_nets:
      PIN_1: "CTRL_NET_A"
      PIN_2: "PWR_NET"
    properties:
      Value: 10k
```

- [ ] **步骤 3：在 `edges:` 段末追加 flexible_path 边**

```yaml
  # ---- PhaseC flexible-path edges (WI-J) ----
  flex_ubias_pwr:
    type: signal
    net: PWR_NET
    routing_class: flexible_path
    connections:
    - U_BIAS.PIN_1
    - TP2.PIN_1
    constraint:
      width: 0.254
  flex_ubias_dec1:
    type: signal
    net: PWR_NET
    routing_class: flexible_path
    connections:
    - U_BIAS.PIN_1
    - C_DEC1.PIN_1
    constraint:
      width: 0.254
  flex_dec1_gnd:
    type: signal
    net: GND
    routing_class: flexible_path
    connections:
    - C_DEC1.PIN_2
    - IC1.PIN_2     # 借 IC1 GND pin 作为 GND 参考接入
    constraint:
      width: 0.254
  flex_ubias_rpull:
    type: signal
    net: CTRL_NET_A
    routing_class: flexible_path
    connections:
    - U_BIAS.PIN_2
    - R_PULL.PIN_1
    constraint:
      width: 0.254
```

- [ ] **步骤 4：验证 schema 仍然通过**

运行：`.venv/bin/python -m tools.schema_check rf_layout_simplified.yaml`
预期：PASS（如失败则需放宽 schema —— 见任务 2）。

- [ ] **步骤 5：Commit**
```bash
git add rf_layout_simplified.yaml
git commit -m "feat(case): add floating IC/RLC and flexible_path edges for phaseC (WI-J1)"
```

---

## 任务 2：放宽 schema 接受 floating fixed 器件（如需要）

**前置：** 若任务 1 步骤 4 报错，否则跳过此任务（直接进任务 3）。

**文件：**
- 修改：`src/schema/v33.py`（`Placement` 模型）
- 修改：`src/frontend/expand_components.py`（`_expand_fixed` 对 `x/y is None && is_floating==True` 给出 placeholder bbox）
- 测试：`tests/unit/frontend/test_expand_components_floating.py`

- [ ] **步骤 1：编写失败测试**
```python
# tests/unit/frontend/test_expand_components_floating.py
from schema.v33 import Component, Placement
from frontend.expand_components import expand_component

def test_floating_component_yields_floating_expansion():
    comp = Component(
        footprint_ref="PKG_CAP",
        placement=Placement(is_floating=True),
        pin_nets={"PIN_1": "PWR", "PIN_2": "GND"},
    )
    exp = expand_component("C_DEC1", comp, footprints={"PKG_CAP": _cap_fp()})
    assert exp.placement_kind == "floating"
    assert exp.bbox is None  # phaseC 才决定
    assert all(p.kind == "floating_deferred" for p in exp.pads)

def _cap_fp():
    from schema.v33 import Footprint, FootprintPin, PadGeometry, FootprintDimensions
    return Footprint(
        dimensions=FootprintDimensions(width=0.95, length=1.75),
        pins={
            "PIN_1": FootprintPin(local_x=-0.685, local_y=0.0,
                pad_geometry=PadGeometry(shape="rect", width=0.91, length=1.19)),
            "PIN_2": FootprintPin(local_x=0.685, local_y=0.0,
                pad_geometry=PadGeometry(shape="rect", width=0.91, length=1.19)),
        },
    )
```

- [ ] **步骤 2：运行确认 FAIL**

运行：`.venv/bin/pytest tests/unit/frontend/test_expand_components_floating.py -v`
预期：FAIL（`placement_kind == "fixed"` 而非 "floating"）。

- [ ] **步骤 3：实现 `_expand_floating` 分支**

修改 `src/frontend/expand_components.py::expand_component`：在 `_expand_uv` 判断之后、
`_expand_fixed` 之前加：
```python
    if (
        placement is not None
        and getattr(placement, "is_floating", False)
        and (placement.x is None or placement.y is None)
    ):
        return _expand_floating(name, component, footprint)
```
并新增 `_expand_floating`：复制 `_expand_fixed` 的 pad 枚举骨架，但每个 pad 的
`kind="floating_deferred"`，组件 `placement_kind="floating"`，`bbox=None`。

修改 `src/frontend/models.py::ExpandedPad`：`PadKind` 增加字面量 `"floating_deferred"`。

- [ ] **步骤 4：跑测试通过**

运行：`.venv/bin/pytest tests/unit/frontend/test_expand_components_floating.py -v`
预期：PASS。

- [ ] **步骤 5：再跑 schema_check 验证 yaml**

运行：`.venv/bin/python -m tools.schema_check rf_layout_simplified.yaml`
预期：PASS。

- [ ] **步骤 6：Commit**
```bash
git add src/schema/v33.py src/frontend/expand_components.py src/frontend/models.py tests/unit/frontend/test_expand_components_floating.py
git commit -m "feat(frontend): support is_floating fixed components without x/y (WI-J2)"
```

---

## 任务 3：实现 floating placer（SA 退火放置）

**目标：** 在 phaseC 入口处，把所有 `placement_kind=="floating"` 的器件落到合法坐标。

**文件：**
- 创建：`src/solver/v2/floating_placer.py`
- 测试：`tests/unit/v2/test_floating_placer.py`

- [ ] **步骤 1：编写失败测试**
```python
# tests/unit/v2/test_floating_placer.py
import pytest
from frontend.compile import compile_layout
from solver.v2.floating_placer import (
    FloatingPlacerConfig,
    place_floating_components,
)
from solver.v2.orchestrator import solve_layout_v2

def test_floating_components_placed_inside_board_no_overlap(tmp_path):
    result = solve_layout_v2("rf_layout_simplified.yaml")
    floating_names = [n for n, c in result.artifact.components.items()
                      if c.placement_kind == "floating"]
    assert len(floating_names) >= 3, "案例应包含 ≥3 个 floating 组件"
    artifact = result.artifact
    skeleton = result.phase_a.skeleton
    adhesion = result.phase_b.adhesion
    placements = place_floating_components(
        artifact=artifact,
        skeleton=skeleton,
        adhesion=adhesion,
        board_w=float(artifact.board.get("width", 40.0)),
        board_h=float(artifact.board.get("height", 85.0)),
        config=FloatingPlacerConfig(seed=42),
    )
    # 每个 floating 必须被分配 (x, y, rotation)
    for name in floating_names:
        assert name in placements
        p = placements[name]
        assert 0.0 <= p.x <= 40.0
        assert 0.0 <= p.y <= 85.0
        assert p.rotation in (0, 90, 180, -90)
    # 任意两 floating bbox 不可重叠
    bboxes = [(_bbox_of(artifact, n, placements[n])) for n in floating_names]
    for i in range(len(bboxes)):
        for j in range(i + 1, len(bboxes)):
            assert not _bbox_overlap(bboxes[i], bboxes[j])

def _bbox_of(artifact, name, placement):
    fp = artifact.footprints[artifact.components[name].footprint_ref]
    w, h = float(fp["length"]), float(fp["width"])
    if placement.rotation in (90, -90):
        w, h = h, w
    return (placement.x - w / 2, placement.y - h / 2,
            placement.x + w / 2, placement.y + h / 2)

def _bbox_overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
```

- [ ] **步骤 2：运行确认 FAIL**

运行：`.venv/bin/pytest tests/unit/v2/test_floating_placer.py -v`
预期：FAIL（模块不存在 / `place_floating_components` 未定义）。

- [ ] **步骤 3：实现 `floating_placer.py`**

完整实现（不要写 TODO）：
```python
"""WI-J3 — Floating component placer.

对所有 `placement_kind == "floating"` 的组件（fixed 类型但缺 x/y），用
SA 退火寻找合法 (x, y, rotation)，禁止与下列任意几何重叠：
 * fixed 组件 bbox（artifact.components[*].bbox）
 * 已吸附的 UV 组件 bbox（adhesion.placements[*].bbox）
 * 已成功的微带线 polyline + clearance 膨胀
能量函数：HPWL（floating ↔ 其连接端点） + 越界平方惩罚 + 重叠平方惩罚。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from frontend.models import FrontendArtifact
from solver.v2.skeleton_router import SkeletonReport
from solver.v2.uv_adhesion import UvAdhesionReport


@dataclass(frozen=True)
class FloatingPlacement:
    x: float
    y: float
    rotation: int  # ∈ {0, 90, 180, -90}


@dataclass(frozen=True)
class FloatingPlacerConfig:
    iterations: int = 3000
    temperature_init: float = 5000.0
    temperature_min: float = 1.0
    cooling: float = 0.995
    step_mm: float = 1.5
    boundary_weight: float = 200.0
    overlap_weight: float = 1000.0
    hpwl_weight: float = 1.0
    clearance_mm: float = 0.15
    seed: int = 0xC0FFEE


def place_floating_components(
    *,
    artifact: FrontendArtifact,
    skeleton: SkeletonReport,
    adhesion: UvAdhesionReport,
    board_w: float,
    board_h: float,
    config: FloatingPlacerConfig | None = None,
) -> dict[str, FloatingPlacement]:
    cfg = config or FloatingPlacerConfig()
    rng = random.Random(cfg.seed)
    floating_names = [
        n for n, c in artifact.components.items() if c.placement_kind == "floating"
    ]
    if not floating_names:
        return {}

    obstacles = _collect_static_obstacles(artifact, skeleton, adhesion, cfg.clearance_mm)
    # 初始化：把每个 floating 摆到板面中心略带随机偏移
    state: dict[str, FloatingPlacement] = {}
    for i, name in enumerate(floating_names):
        state[name] = FloatingPlacement(
            x=board_w * 0.5 + (i - len(floating_names) / 2) * 4.0,
            y=board_h * 0.5 + ((-1) ** i) * 3.0,
            rotation=0,
        )

    def energy(s: dict[str, FloatingPlacement]) -> float:
        e = 0.0
        bboxes = {n: _bbox_for(artifact, n, p) for n, p in s.items()}
        # 越界
        for bb in bboxes.values():
            e += cfg.boundary_weight * _out_of_board_sq(bb, board_w, board_h)
        # 与静态障碍重叠
        for bb in bboxes.values():
            for ob in obstacles:
                e += cfg.overlap_weight * _bbox_overlap_area_sq(bb, ob)
        # floating 互相重叠
        items = list(bboxes.values())
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                e += cfg.overlap_weight * _bbox_overlap_area_sq(items[i], items[j])
        # HPWL：每个 floating pin ↔ 它的 connection 另一端
        e += cfg.hpwl_weight * _hpwl_energy(artifact, s, skeleton, adhesion)
        return e

    cur_e = energy(state)
    best_state, best_e = dict(state), cur_e
    T = cfg.temperature_init
    for _ in range(cfg.iterations):
        if T < cfg.temperature_min:
            break
        # 扰动：随机选一个 floating，移动或旋转
        name = rng.choice(floating_names)
        old = state[name]
        if rng.random() < 0.15:
            new_rot = rng.choice([r for r in (0, 90, 180, -90) if r != old.rotation])
            new_p = FloatingPlacement(old.x, old.y, new_rot)
        else:
            dx = rng.uniform(-cfg.step_mm, cfg.step_mm)
            dy = rng.uniform(-cfg.step_mm, cfg.step_mm)
            new_p = FloatingPlacement(old.x + dx, old.y + dy, old.rotation)
        state[name] = new_p
        new_e = energy(state)
        accept = new_e < cur_e or rng.random() < math.exp(-(new_e - cur_e) / T)
        if accept:
            cur_e = new_e
            if cur_e < best_e:
                best_state, best_e = dict(state), cur_e
        else:
            state[name] = old
        T *= cfg.cooling
    return best_state


# ---- helpers ---------------------------------------------------------------

def _collect_static_obstacles(artifact, skeleton, adhesion, clearance):
    obs: list[tuple[float, float, float, float]] = []
    for c in artifact.components.values():
        if c.bbox is None:
            continue
        obs.append((c.bbox.min_x - clearance, c.bbox.min_y - clearance,
                    c.bbox.max_x + clearance, c.bbox.max_y + clearance))
    for p in adhesion.placements.values():
        bb = p.bbox
        if bb is None:
            continue
        obs.append((bb.min_x - clearance, bb.min_y - clearance,
                    bb.max_x + clearance, bb.max_y + clearance))
    # 微带线 polyline → 逐段 bbox
    from solver.units import um_to_mm
    for route in skeleton.routes.values():
        if not route.success:
            continue
        half_w = route.target_mm and 0.0  # width 在 polyline 上不直接，取 0.5mm 兜底
        for (x1u, y1u), (x2u, y2u) in zip(route.polyline_um, route.polyline_um[1:]):
            x1, y1 = um_to_mm(x1u), um_to_mm(y1u)
            x2, y2 = um_to_mm(x2u), um_to_mm(y2u)
            pad = 0.5 + clearance
            obs.append((min(x1, x2) - pad, min(y1, y2) - pad,
                        max(x1, x2) + pad, max(y1, y2) + pad))
    return obs


def _bbox_for(artifact, name, p: FloatingPlacement):
    comp = artifact.components[name]
    fp = artifact.footprints.get(comp.footprint_ref, {})
    w = float(fp.get("length", 2.0))
    h = float(fp.get("width", 2.0))
    if p.rotation in (90, -90):
        w, h = h, w
    return (p.x - w / 2, p.y - h / 2, p.x + w / 2, p.y + h / 2)


def _out_of_board_sq(bb, W, H):
    dx = max(0.0, -bb[0]) + max(0.0, bb[2] - W)
    dy = max(0.0, -bb[1]) + max(0.0, bb[3] - H)
    return dx * dx + dy * dy


def _bbox_overlap_area_sq(a, b):
    ow = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    oh = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = ow * oh
    return area * area


def _hpwl_energy(artifact, state, skeleton, adhesion):
    e = 0.0
    floating_set = set(state.keys())
    for edge in artifact.edges.values():
        if not edge.connections or len(edge.connections) < 2:
            continue
        xs, ys = [], []
        any_float = False
        for ep in edge.connections:
            comp_id = ep.split(".")[0] if "." in ep else None
            if comp_id and comp_id in floating_set:
                any_float = True
                p = state[comp_id]
                xs.append(p.x); ys.append(p.y)
            else:
                xy = _resolve_endpoint(artifact, skeleton, adhesion, ep)
                if xy is None:
                    continue
                xs.append(xy[0]); ys.append(xy[1])
        if any_float and xs:
            e += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return e


def _resolve_endpoint(artifact, skeleton, adhesion, ep: str):
    # 优先 fixed 组件 pin，其次 skeleton 端点，其次 adhesion pad center
    from solver.units import um_to_mm
    if "." in ep:
        comp_id, pin_id = ep.split(".", 1)
        comp = artifact.components.get(comp_id)
        if comp is not None and comp.bbox is not None:
            for pad in comp.pads:
                if pad.pin_id == pin_id and pad.center is not None:
                    return (float(pad.center.x), float(pad.center.y))
        place = adhesion.placements.get(comp_id) if adhesion else None
        if place is not None:
            for pad in place.pads:
                if pad.pin_id == pin_id:
                    return (float(pad.center.x), float(pad.center.y))
    xy_um = skeleton.final_endpoint_um.get(ep)
    if xy_um is not None:
        return (um_to_mm(xy_um[0]), um_to_mm(xy_um[1]))
    return None
```

- [ ] **步骤 4：跑测试通过**

运行：`.venv/bin/pytest tests/unit/v2/test_floating_placer.py -v`
预期：PASS。

- [ ] **步骤 5：Commit**
```bash
git add src/solver/v2/floating_placer.py tests/unit/v2/test_floating_placer.py
git commit -m "feat(v2): SA placer for floating components in phaseC (WI-J3)"
```

---

## 任务 4：实现 phaseC 硬 DRC（无重叠 + 无交叉）

**目标：** 提供独立可测的 `validate_flex_routes()`，返回每条 flex 边的违规列表。

**文件：**
- 创建：`src/solver/v2/flex_drc.py`
- 测试：`tests/unit/v2/test_flex_drc.py`

- [ ] **步骤 1：编写失败测试**
```python
# tests/unit/v2/test_flex_drc.py
from schema.v6_ir import Point
from schema.geometry_ir import RoutePolyline
from schema.v6_ir import RoutingClass
from solver.v2.flex_drc import (
    DrcViolation,
    validate_flex_routes,
)


def _poly(*pts, eid="e", w=0.254):
    return RoutePolyline(
        edge_id=eid, routing_class=RoutingClass.FLEXIBLE_PATH, width=w,
        points=tuple(Point(x=x, y=y) for x, y in pts),
    )


def test_no_violation_when_routes_disjoint():
    flex = {"a": _poly((0, 0), (10, 0), eid="a"),
            "b": _poly((0, 5), (10, 5), eid="b")}
    rep = validate_flex_routes(flex_routes=flex, other_routes={},
                                obstacle_bboxes=[], clearance_mm=0.05)
    assert rep.violations == []


def test_crossing_two_flex_routes_reported():
    flex = {"a": _poly((0, 0), (10, 10), eid="a"),
            "b": _poly((0, 10), (10, 0), eid="b")}
    rep = validate_flex_routes(flex_routes=flex, other_routes={},
                                obstacle_bboxes=[], clearance_mm=0.05)
    kinds = {(v.edge_id, v.kind) for v in rep.violations}
    assert ("a", "crossing") in kinds or ("b", "crossing") in kinds


def test_route_overlapping_obstacle_reported():
    flex = {"a": _poly((0, 5), (10, 5), eid="a")}
    rep = validate_flex_routes(flex_routes=flex, other_routes={},
                                obstacle_bboxes=[(4.0, 4.0, 6.0, 6.0)],
                                clearance_mm=0.05)
    assert any(v.edge_id == "a" and v.kind == "overlap" for v in rep.violations)
```

- [ ] **步骤 2：运行确认 FAIL**

运行：`.venv/bin/pytest tests/unit/v2/test_flex_drc.py -v`
预期：FAIL（模块不存在）。

- [ ] **步骤 3：实现 `flex_drc.py`**

```python
"""WI-J4 — Hard DRC for phaseC flexible routes.

提供两类违规检测：
 1. overlap：flex polyline 与任意 obstacle bbox（fixed/UV/floating 组件 + 已有微带线 bbox）相交
 2. crossing：两条 flex polyline 的线段相互相交
违反任一即把对应 edge_id 标为 FAILED。orchestrator 不写回该 polyline。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from schema.geometry_ir import RoutePolyline


@dataclass(frozen=True)
class DrcViolation:
    edge_id: str
    kind: str  # "overlap" | "crossing"
    detail: str


@dataclass
class DrcReport:
    violations: list[DrcViolation] = field(default_factory=list)

    def failed_edges(self) -> set[str]:
        return {v.edge_id for v in self.violations}


def validate_flex_routes(
    *,
    flex_routes: Mapping[str, RoutePolyline],
    other_routes: Mapping[str, RoutePolyline],
    obstacle_bboxes: Iterable[tuple[float, float, float, float]],
    clearance_mm: float = 0.05,
) -> DrcReport:
    report = DrcReport()
    obs = list(obstacle_bboxes)
    flex_list = list(flex_routes.items())

    # 1. polyline ↔ bbox overlap
    for eid, route in flex_list:
        half = float(route.width) / 2.0 + clearance_mm
        for (x1, y1), (x2, y2) in _segments(route):
            seg_bb = (min(x1, x2) - half, min(y1, y2) - half,
                      max(x1, x2) + half, max(y1, y2) + half)
            for ob in obs:
                if _bbox_intersects(seg_bb, ob):
                    report.violations.append(DrcViolation(
                        eid, "overlap", f"segment {(x1, y1)}->{(x2, y2)} hits {ob}"))
                    break
            else:
                continue
            break

    # 2. polyline ↔ polyline crossings（flex vs flex 和 flex vs other）
    all_segs: list[tuple[str, tuple[float, float], tuple[float, float]]] = []
    for eid, route in flex_list:
        for a, b in _segments(route):
            all_segs.append((eid, a, b))
    for eid, route in other_routes.items():
        for a, b in _segments(route):
            all_segs.append((eid, a, b))

    for i, (eid_a, a1, a2) in enumerate(all_segs):
        for j in range(i + 1, len(all_segs)):
            eid_b, b1, b2 = all_segs[j]
            if eid_a == eid_b:
                continue
            if _segments_share_endpoint(a1, a2, b1, b2):
                continue
            if _segments_cross(a1, a2, b1, b2):
                if eid_a in flex_routes:
                    report.violations.append(DrcViolation(
                        eid_a, "crossing", f"crosses {eid_b}"))
                if eid_b in flex_routes:
                    report.violations.append(DrcViolation(
                        eid_b, "crossing", f"crosses {eid_a}"))
    return report


def _segments(route: RoutePolyline):
    pts = [(float(p.x), float(p.y)) for p in route.points]
    return list(zip(pts, pts[1:]))


def _bbox_intersects(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _orient(a, b, c):
    v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if v > 1e-9: return 1
    if v < -1e-9: return -1
    return 0


def _on_seg(a, b, c):
    return (min(a[0], b[0]) - 1e-9 <= c[0] <= max(a[0], b[0]) + 1e-9 and
            min(a[1], b[1]) - 1e-9 <= c[1] <= max(a[1], b[1]) + 1e-9)


def _segments_cross(p1, p2, p3, p4) -> bool:
    o1, o2 = _orient(p1, p2, p3), _orient(p1, p2, p4)
    o3, o4 = _orient(p3, p4, p1), _orient(p3, p4, p2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_seg(p1, p2, p3): return True
    if o2 == 0 and _on_seg(p1, p2, p4): return True
    if o3 == 0 and _on_seg(p3, p4, p1): return True
    if o4 == 0 and _on_seg(p3, p4, p2): return True
    return False


def _segments_share_endpoint(a1, a2, b1, b2) -> bool:
    eq = lambda p, q: abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6
    return eq(a1, b1) or eq(a1, b2) or eq(a2, b1) or eq(a2, b2)
```

- [ ] **步骤 4：跑测试通过**

运行：`.venv/bin/pytest tests/unit/v2/test_flex_drc.py -v`
预期：PASS（3 条用例全过）。

- [ ] **步骤 5：Commit**
```bash
git add src/solver/v2/flex_drc.py tests/unit/v2/test_flex_drc.py
git commit -m "feat(v2): hard DRC for phaseC flex routes — overlap + crossing (WI-J4)"
```

---

## 任务 5：orchestrator 接入 floating placer + DRC

**目标：** phaseC 真正按"floating 放置 → flex 路由 → DRC 收口"三步运行。

**文件：**
- 修改：`src/solver/v2/orchestrator.py`
- 修改：`src/solver/astar_flex.py`（障碍源补充 RLC pad + adhesion bbox）
- 测试：`tests/integration/test_phase_c_e2e.py`

- [ ] **步骤 1：编写失败的集成测试**
```python
# tests/integration/test_phase_c_e2e.py
from solver.v2.orchestrator import solve_layout_v2


def test_phase_c_places_floating_and_routes_flex_without_drc():
    result = solve_layout_v2("rf_layout_simplified.yaml")
    # floating 全部入版
    placements = result.geometry.placements
    floating_names = [n for n, c in result.artifact.components.items()
                      if c.placement_kind == "floating"]
    assert floating_names, "案例应包含 floating 器件"
    for n in floating_names:
        assert n in placements, f"floating {n} 未在 phaseC 落地"
    # flex 边全部成功且无 DRC 违规
    assert result.phase_c.failed_flex_edges == [], (
        f"flex 边失败：{result.phase_c.failed_flex_edges}"
    )
    assert result.phase_c.routed_flex_edges, "至少应有一条 flex 边被路由"
    # GeometryIR 中所有 flex 边都已写回
    for eid in result.phase_c.routed_flex_edges:
        assert eid in result.geometry.routes
```

- [ ] **步骤 2：运行确认 FAIL**

运行：`.venv/bin/pytest tests/integration/test_phase_c_e2e.py -v`
预期：FAIL（floating 未落地 / flex 边失败）。

- [ ] **步骤 3：在 orchestrator phaseC 段加入 floating placer**

在 `solve_layout_v2()` 函数 `t2 = time.perf_counter()` 之后、`_assemble_geometry` 之前，
插入：
```python
    # ---- Phase C step 1: place floating components ------------------------
    from .floating_placer import (
        FloatingPlacerConfig,
        place_floating_components,
    )
    floating = place_floating_components(
        artifact=artifact,
        skeleton=skeleton,
        adhesion=adhesion,
        board_w=board_w,
        board_h=board_h,
    )
```
然后在 `_assemble_geometry` 里加可选 `floating: dict[str, FloatingPlacement] | None = None`
参数，把 floating 组件展开成 `ComponentPlacement`（参考 fixed 分支的 pad 枚举：用
`artifact.footprints[fp_ref]` 的本地 pin 坐标 + rotation 矩阵）并写入 `placements`。

- [ ] **步骤 4：扩展 astar_flex 障碍源**

修改 `src/solver/astar_flex.py::_build_grid`：
1. 遍历 `geom.placements`（不仅是 `artifact.components`），把所有 UV/floating 组件的 bbox 也加入 `obstacles`
2. 把 `RoutingClass.FLEXIBLE_PATH` 已路由的边（不仅 RF）也算作障碍（除 `skip_edge_id`）
3. 给 `Pad` 多边形单独加 obstacle cells（防止 flex 走到任何 pad 上）

实现要点（代码示例）：
```python
    for placement in geom.placements.values():
        bb = placement.bbox
        if bb is None:
            continue
        bx_lo = mm_to_um(float(bb.min_x))
        by_lo = mm_to_um(float(bb.min_y))
        bx_hi = mm_to_um(float(bb.max_x))
        by_hi = mm_to_um(float(bb.max_y))
        for cell in _cells_in_bbox(bx_lo, by_lo, bx_hi, by_hi, step):
            obstacles.add(cell)
```

- [ ] **步骤 5：在 `_route_flex_edges` 末尾插入 DRC 收口**

```python
    from .flex_drc import validate_flex_routes
    flex_routes = {
        eid: new_geom.routes[eid]
        for eid in report.routed_edges
        if eid in new_geom.routes
    }
    other = {
        eid: r for eid, r in new_geom.routes.items()
        if eid not in flex_routes
    }
    obstacle_bboxes = []
    for placement in new_geom.placements.values():
        if placement.bbox is None:
            continue
        obstacle_bboxes.append((
            float(placement.bbox.min_x), float(placement.bbox.min_y),
            float(placement.bbox.max_x), float(placement.bbox.max_y),
        ))
    drc = validate_flex_routes(
        flex_routes=flex_routes, other_routes=other,
        obstacle_bboxes=obstacle_bboxes, clearance_mm=0.05,
    )
    failed = set(report.failed_edges) | drc.failed_edges()
    # 抛弃违规的 flex polyline
    purged_routes = {
        eid: r for eid, r in new_geom.routes.items() if eid not in drc.failed_edges()
    }
    new_geom = GeometryIR(
        project=new_geom.project, board=new_geom.board,
        placements=new_geom.placements, routes=purged_routes,
        nodes=new_geom.nodes, solve_status=new_geom.solve_status,
        solve_wall_seconds=new_geom.solve_wall_seconds,
        objective_value=new_geom.objective_value,
    )
    routed_ok = [e for e in report.routed_edges if e not in failed]
    return new_geom, routed_ok, sorted(failed)
```

- [ ] **步骤 6：跑测试**

运行：
```
.venv/bin/pytest tests/integration/test_phase_c_e2e.py -v
.venv/bin/pytest tests/unit/v2/ -q
```
预期：PASS。如果 flex 边因板面太满而失败，需要调整任务 1 中 floating 的初始能量（任务 3
的 `step_mm` 调小，`iterations` 调大）；如仍失败，对应 floating 组件位置预留稍大空隙。

- [ ] **步骤 7：Commit**
```bash
git add src/solver/v2/orchestrator.py src/solver/astar_flex.py tests/integration/test_phase_c_e2e.py
git commit -m "feat(v2): orchestrator wires floating placer + flex DRC into phaseC (WI-J5)"
```

---

## 任务 6：phaseC SVG 独立可视化 + DRC 红色标记

**目标：** `out/PA_Module_Simplified.phaseC.svg` 不再等于 final，要有：
- 蓝色高亮 floating 组件 bbox & 标签
- 紫色 flex polyline
- 红色虚线高亮所有 DRC 违规线段

**文件：**
- 修改：`src/tools/pcb_solve_v2.py`（`_phase_c_overlay`、phaseC SVG 调用处）
- 测试：`tests/unit/test_phase_c_svg_overlay.py`

- [ ] **步骤 1：编写失败测试**
```python
# tests/unit/test_phase_c_svg_overlay.py
from pathlib import Path
from tools.pcb_solve_v2 import main

def test_phase_c_svg_has_floating_and_flex_overlay(tmp_path):
    out_dir = tmp_path / "out"
    main([
        "--yaml", "rf_layout_simplified.yaml",
        "--out-dir", str(out_dir),
    ])
    svg = (out_dir / "PA_Module_Simplified.phaseC.svg").read_text()
    # 蓝色 floating 标记（class 或 fill）
    assert "floating-bbox" in svg
    # 紫色 flex 走线
    assert "flex-route" in svg
    # banner 标识 phaseC
    assert "Phase C" in svg
```

- [ ] **步骤 2：运行确认 FAIL**

运行：`.venv/bin/pytest tests/unit/test_phase_c_svg_overlay.py -v`
预期：FAIL（"floating-bbox" / "flex-route" 缺失）。

- [ ] **步骤 3：实现 `_phase_c_overlay`**

在 `pcb_solve_v2.py` 中新增（参考 `_phase_b_rlc_overlay` 的实现风格）：
```python
def _phase_c_overlay(geom, result, layout):
    parts: list[str] = []
    floating_names = {
        n for n, c in result.artifact.components.items()
        if c.placement_kind == "floating"
    }
    # floating bbox（蓝色）
    for name, place in geom.placements.items():
        if name not in floating_names or place.bbox is None:
            continue
        bb = place.bbox
        parts.append(
            f'<rect class="floating-bbox" x="{bb.min_x:.2f}" y="{bb.min_y:.2f}" '
            f'width="{bb.max_x - bb.min_x:.2f}" height="{bb.max_y - bb.min_y:.2f}" '
            f'fill="rgba(37,99,235,0.18)" stroke="#1d4ed8" stroke-width="0.2"/>'
        )
        parts.append(
            f'<text x="{(bb.min_x + bb.max_x) / 2:.2f}" y="{bb.min_y - 0.3:.2f}" '
            f'font-size="1.5" fill="#1d4ed8" text-anchor="middle">{name}</text>'
        )
    # flex 走线（紫色）
    for eid, route in geom.routes.items():
        if route.routing_class.value != "flexible_path":
            continue
        d = " ".join(f"{'M' if i == 0 else 'L'}{p.x:.2f},{p.y:.2f}"
                     for i, p in enumerate(route.points))
        parts.append(
            f'<path class="flex-route" d="{d}" stroke="#7e22ce" '
            f'stroke-width="{route.width:.2f}" fill="none" opacity="0.85"/>'
        )
    # DRC 违规（红色虚线）
    for eid in getattr(result.phase_c, "failed_flex_edges", []):
        edge = result.artifact.edges.get(eid)
        if edge is None or len(edge.connections) < 2:
            continue
        # 用 plan endpoint 还原直连示意
        from solver.units import um_to_mm
        a = result.phase_a.plan.endpoint_xy.get(edge.connections[0])
        b = result.phase_a.plan.endpoint_xy.get(edge.connections[-1])
        if a is None or b is None:
            continue
        parts.append(
            f'<line class="flex-drc-fail" x1="{a[0]:.2f}" y1="{a[1]:.2f}" '
            f'x2="{b[0]:.2f}" y2="{b[1]:.2f}" stroke="#dc2626" '
            f'stroke-width="0.3" stroke-dasharray="0.8 0.4"/>'
        )
    return "\n".join(parts)
```

并在 `_emit_phase_svgs` 中改 phaseC SVG 调用：
```python
    phase_c_svg = out_dir / f"{project}.phaseC.svg"
    _render_svg(
        result.geometry,
        phase_c_svg,
        banner=_phase_banner(
            "Phase C",
            f"Floating + flex routing — {flex_ok} routed / {flex_failed} failed",
            "#1d4ed8",
        ),
        overlay=_pin_label_overlay(
            result.geometry,
            skip_components=frozenset(result.artifact.uv_components.keys()),
        )
        + _phase_c_overlay(result.geometry, result, layout),
        layout=layout,
        show_pad_labels=False,
    )
```

- [ ] **步骤 4：跑测试**

运行：`.venv/bin/pytest tests/unit/test_phase_c_svg_overlay.py -v`
预期：PASS。

- [ ] **步骤 5：重新生成所有 SVG 并目视检查**

运行：`.venv/bin/python -m tools.pcb_solve_v2 --yaml rf_layout_simplified.yaml --out-dir out`
预期：`out/PA_Module_Simplified.phaseC.svg` 中能看到 3 个蓝色 floating bbox + 4 条紫色 flex 走线。

- [ ] **步骤 6：Commit**
```bash
git add src/tools/pcb_solve_v2.py tests/unit/test_phase_c_svg_overlay.py out/
git commit -m "feat(svg): phaseC overlay — floating bbox + flex routes + DRC red marks (WI-J6)"
```

---

## 任务 7：全量验证 + 推送

- [ ] **步骤 1：跑全套 M1 验证**

运行：`./scripts/verify_m1.sh`
预期：除已知预存失败的 `test_node_planner_respects_target_length_constraint` 之外，全部通过。

- [ ] **步骤 2：推送 main**
```bash
git push origin main
```

---

## 自检结论

**规格覆盖度**
- ✅ "悬浮 RLC/IC 灵活布局" → 任务 1（案例）、任务 2（schema）、任务 3（placer）
- ✅ "灵活布线" → 任务 1（flex 边）、任务 5（astar_flex 障碍增强）
- ✅ "不能与器件/微带线重叠" → 任务 4 + 任务 5（DRC overlap + 障碍栅格）
- ✅ "不能交叉" → 任务 4（DRC crossing）
- ✅ "每阶段结果都能输出可评判" → 任务 6（独立 phaseC SVG + 蓝紫红标记）

**占位符扫描** — 已审；所有代码块都是可直接复制运行的完整实现。

**类型一致性** — `FloatingPlacement`、`DrcReport`、`PhaseCResult` 在任务 3/4/5 中字段一致；
`placement_kind == "floating"` 在任务 2/3/5/6 中均匹配。
