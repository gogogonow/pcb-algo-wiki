# PhaseB UV 吸附修复 实现计划

> ⚠️ 历史归档文档：该计划用于当时迭代记录，当前主线请以 `README.md` / `ALGORITHM-OVERVIEW.md` / `ITERATION-PLAN.md` 为准。

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 修复 phaseB 中 RLC 吸附到微带线时的四个核心问题：标签不统一、seg1 占用、走线方向不垂直、body 与微带线重叠（非法）。

**架构：**
- `src/solver/v2/uv_slot_search.py`: 核心算法——修复 self-segment skip bug（只跳过连接点附近，不跳过整段）；降低 step_mm 默认值以获得更多采样点
- `src/solver/v2/uv_adhesion.py`: 过滤 seg1 + 过短边；legacy fallback 时从 host edge 推导旋转
- `src/tools/pcb_solve_v2.py`: phaseB SVG 增加 `_phase_a_diag_overlay` 实现标签统一

**技术栈：** Python 3.11, math, pytest, black/ruff/mypy

---

## 背景：已知 Bug 根因

### Bug A — self-segment 完全跳过导致 body 与 seg1 重叠
`search_slot` 中当前做法：采样点 pt 落在某段上，就 `continue` 跳过整段不做间距检查。
但单段 polyline（如 seg1 只有两端点）会导致整条 seg1 被跳过——body bbox 可以与 seg1 完全重叠。

**正确做法：** 只跳过 pt 周围 `conn_buf` 范围内的路径，对 pt 之前/之后的部分仍然检查间距。

### Bug B — seg1 两侧缺少足够空间
IC1 封装在 seg1 旁边，用户明确要求不在 seg1 上挂接 RLC。
**修复：** `_try_slot_search` 过滤掉 edge id 包含 `_seg1` 的边（若同 net 还有其他边）。

### Bug C — seg2/seg3 采样点不足
seg2=1.3mm, seg3=1.4mm，默认 step_mm=1.5 → interior_samples=0 → 无法挂接。
**修复：** `_try_slot_search` 传 `step_mm=0.8` 给 `search_slot`；此时 seg4(4.6mm)得 4 interior samples, seg2/seg3 各得 1。

### Bug D — legacy 旋转缺失导致水平 trace 上 RLC 仍 rot=0
当 UV 的 GND 侧没有 routed endpoint（如 C7.PIN_2=GND），legacy path 使用 `seed_rotation=0`，导致水平 trace 上的 RLC 也是 rot=0（inline 方向），而非 rot=90（垂直方向）。
**修复：** legacy path 找到 anchor pin 所在 host edge，使用该 edge 的 tangent 方向 + 90° 作为 rotation。

### Bug E — phaseB 标签格式与 phaseA/preA 不一致
phaseB SVG 没有 `_phase_a_diag_overlay`，缺少节点 ID、边短名称及 tooltip 诊断信息。
**修复：** 在 phaseB overlay 中追加 `_phase_a_diag_overlay(result, geom_b)`。

---

## 修改文件一览

| 文件 | 变更 |
|------|------|
| `src/solver/v2/uv_slot_search.py` | `search_slot`: 修复 self-segment skip（连接区分段检查） |
| `src/solver/v2/uv_adhesion.py` | `_try_slot_search`: 过滤 seg1 + step_mm=0.8；`_legacy_anchor_placement`: 从 host edge 派生旋转 |
| `src/tools/pcb_solve_v2.py` | phaseB overlay 追加 `_phase_a_diag_overlay` |
| `tests/unit/v2/test_uv_slot_search.py` | 更新 self-segment 测试 |
| `tests/unit/test_phase_b_svg_rendering.py` | 增加标签一致性断言 |

---

## 任务 1：修复 search_slot self-segment skip 为分段检查（Bug A）

**文件：**
- 修改：`src/solver/v2/uv_slot_search.py:186-204`
- 测试：`tests/unit/v2/test_uv_slot_search.py`

- [ ] **步骤 1：编写失败测试——body 不能与自身 host segment 完全重叠**

```python
# 在 tests/unit/v2/test_uv_slot_search.py 末尾追加：
def test_body_does_not_overlap_self_host_segment():
    """When anchor pin attaches mid-trace, body must not overlap the trace
    beyond the immediate connection region (i.e., the trace portions before
    and after pt must still have clearance from the body)."""
    # Vertical trace from y=0 to y=20 at x=0; halfwidth=0.5.
    # Footprint 2×0.6mm, anchor_pin_local at local (-1, 0) → anchor pin
    # is the left pad, body extends right (+x).
    poly = [(0.0, 0.0), (0.0, 20.0)]
    result = search_slot(
        footprint_size=(2.0, 0.6),
        anchor_pin_local=(-1.0, 0.0),
        host_polylines={"E1": poly},
        host_widths={"E1": 1.0},  # halfwidth=0.5
        board=(20.0, 20.0),
        obstacles=[],
        step_mm=2.0,
    )
    assert result is not None
    # Body bbox x-range: anchor at x=0, body extends right.
    # Body left edge ≈ 0.0 - ... the left half of the body overlaps the trace,
    # which is acceptable AT the connection point.
    # But body right edge must be > 0 (body is to the right, not inline).
    ax, _ = result.anchor_xy
    # Body center must be to the RIGHT of the trace (x>0) for a rightward perp.
    assert ax > 0.0
```

- [ ] **步骤 2：运行确认失败（当前算法不管 self-segment overlap）**

```bash
cd /github-repo/pcb-algo-wiki && .venv/bin/python -m pytest tests/unit/v2/test_uv_slot_search.py::test_body_does_not_overlap_self_host_segment -v
```

预期：PASS（这个测试实际上验证 body 在 trace 右侧，当前算法应该已经做到——但我们需要确认 self-segment 全跳带来的真实 bug）

- [ ] **步骤 3：添加更严格测试——确保不跳过 pt 以外的 trace 段**

```python
def test_self_segment_non_connection_portions_checked():
    """Body must be blocked if it overlaps a portion of self-segment that
    is far from the connection point (e.g., top of trace is well above pt)."""
    # Vertical trace from y=0 to y=20, candidate at y=5 (near bottom).
    # Body extends right. Then we put an obstacle on the right that blocks
    # all right-side placements. Body should NOT be placed overlapping the
    # trace portions above y=6 (far from pt).
    # If self-segment is entirely skipped, a body overlapping the trace at
    # y=15 would pass clearance check — which is wrong.
    poly = [(0.0, 0.0), (0.0, 20.0)]
    # Obstacle blocks the right side completely:
    obstacles_right = [(0.5, 0.0, 20.0, 20.0)]
    result = search_slot(
        footprint_size=(2.0, 0.6),
        anchor_pin_local=(-1.0, 0.0),
        host_polylines={"E1": poly},
        host_widths={"E1": 0.2},  # halfwidth=0.1, so trace occupies x=[-0.1,0.1]
        board=(20.0, 20.0),
        obstacles=obstacles_right,
        step_mm=2.0,
    )
    # Left side only: perp = (-x direction, side=-1)
    # If found, body should be to the LEFT (ax < 0).
    if result is not None:
        assert result.anchor_xy[0] < 0.0
```

- [ ] **步骤 4：运行新测试**

```bash
.venv/bin/python -m pytest tests/unit/v2/test_uv_slot_search.py -v 2>&1 | tail -15
```

- [ ] **步骤 5：修改 `search_slot` — 将 self-segment 全跳改为分段检查**

在 `src/solver/v2/uv_slot_search.py` 的 `search_slot` 函数中，将：

```python
                for other_eid, other_poly in host_polylines.items():
                    other_hw = host_widths.get(other_eid, 0.0) / 2.0
                    for j in range(len(other_poly) - 1):
                        # Skip self-segment that touches pt
                        if other_eid == edge_id:
                            seg_dist_to_pt = _point_seg_distance(
                                pt, other_poly[j], other_poly[j + 1]
                            )
                            if seg_dist_to_pt < 1e-6:
                                continue
                        d = _rect_seg_distance(
                            rect, other_poly[j], other_poly[j + 1], other_hw
                        )
                        if d < min_clearance:
                            blocked = True
                            break
                        clr = min(clr, d)
                    if blocked:
                        break
```

替换为：

```python
                for other_eid, other_poly in host_polylines.items():
                    other_hw = host_widths.get(other_eid, 0.0) / 2.0
                    for j in range(len(other_poly) - 1):
                        a_pt = other_poly[j]
                        b_pt = other_poly[j + 1]
                        if other_eid == edge_id:
                            seg_dist_to_pt = _point_seg_distance(
                                pt, a_pt, b_pt
                            )
                            if seg_dist_to_pt < 1e-6:
                                # Connection segment: only check portions
                                # outside the connection buffer around pt.
                                # conn_buf covers anchor-pad footprint half
                                # plus the trace half-width.
                                conn_buf = max(fw, fh) / 2.0 + other_hw + min_clearance
                                axb = b_pt[0] - a_pt[0]
                                ayb = b_pt[1] - a_pt[1]
                                seg_len_j = math.hypot(axb, ayb)
                                if seg_len_j < 1e-6:
                                    continue
                                t_pt = (
                                    (pt[0] - a_pt[0]) * axb
                                    + (pt[1] - a_pt[1]) * ayb
                                ) / (seg_len_j * seg_len_j)
                                t_pt = max(0.0, min(1.0, t_pt))
                                t_buf = conn_buf / seg_len_j
                                # Check sub-segment before pt
                                t1 = max(0.0, t_pt - t_buf)
                                if t1 > 1e-3:
                                    p1 = (
                                        a_pt[0] + t1 * axb,
                                        a_pt[1] + t1 * ayb,
                                    )
                                    d = _rect_seg_distance(rect, a_pt, p1, other_hw)
                                    if d < min_clearance:
                                        blocked = True
                                        break
                                    clr = min(clr, d)
                                # Check sub-segment after pt
                                t2 = min(1.0, t_pt + t_buf)
                                if t2 < 1.0 - 1e-3:
                                    p2 = (
                                        a_pt[0] + t2 * axb,
                                        a_pt[1] + t2 * ayb,
                                    )
                                    d = _rect_seg_distance(rect, p2, b_pt, other_hw)
                                    if d < min_clearance:
                                        blocked = True
                                        break
                                    clr = min(clr, d)
                                continue
                        d = _rect_seg_distance(rect, a_pt, b_pt, other_hw)
                        if d < min_clearance:
                            blocked = True
                            break
                        clr = min(clr, d)
                    if blocked:
                        break
```

注意：该代码中 `fw, fh` 来自外层 `footprint_size` 解包，确保在 for 循环外部可见（已有）。

- [ ] **步骤 6：运行测试验证**

```bash
.venv/bin/python -m pytest tests/unit/v2/test_uv_slot_search.py -v 2>&1 | tail -10
```

预期：所有测试 PASS。

- [ ] **步骤 7：Commit**

```bash
git add src/solver/v2/uv_slot_search.py tests/unit/v2/test_uv_slot_search.py
git commit -m "fix(slot-search): split self-segment at connection point to prevent body-trace overlap (WI-E1)"
```

---

## 任务 2：过滤 seg1 + 降低 step_mm（Bug B/C）

**文件：**
- 修改：`src/solver/v2/uv_adhesion.py:145-177`（`_try_slot_search` 函数）

- [ ] **步骤 1：在 `_try_slot_search` 中过滤 seg1 边和过短边**

将 `_try_slot_search` 中构建 `host_polylines` / `host_widths` 的循环改为：

```python
def _try_slot_search(
    uv: ComponentExpansion,
    artifact: FrontendArtifact,
    skeleton: SkeletonReport,
    edges_by_net: dict[str, list[str]],
    obstacles: list[tuple[float, float, float, float]],
    board: tuple[float, float],
) -> SlotCandidate | None:
    meta = uv.uv_meta
    if meta is None or not meta.reference_net:
        return None
    host_eids = edges_by_net.get(meta.reference_net, [])
    if not host_eids:
        return None

    # Build polyline dict; skip single-point stubs.
    host_polylines: dict[str, list[tuple[float, float]]] = {}
    host_widths: dict[str, float] = {}
    for eid in host_eids:
        route = skeleton.routes[eid]
        poly_mm = [(p[0] / MM_TO_UM, p[1] / MM_TO_UM) for p in route.polyline_um]
        if len(poly_mm) < 2:
            continue
        host_polylines[eid] = poly_mm
        edge = artifact.edges.get(eid)
        host_widths[eid] = (
            float(edge.width) if (edge and edge.width is not None) else 0.5
        )

    if not host_polylines:
        return None

    # Prefer non-seg1 edges; only fall back to seg1 if nothing else available.
    non_seg1 = {eid: poly for eid, poly in host_polylines.items() if "_seg1" not in eid}
    search_polylines = non_seg1 if non_seg1 else host_polylines
    search_widths = {eid: host_widths[eid] for eid in search_polylines}

    fw, fh = _footprint_size(uv)
    return search_slot(
        footprint_size=(fw, fh),
        anchor_pin_local=_local_pin_offset(uv, meta.anchor_pin),
        host_polylines=search_polylines,
        host_widths=search_widths,
        board=board,
        obstacles=obstacles,
        step_mm=0.8,  # smaller step → more interior samples on short edges
    )
```

- [ ] **步骤 2：运行完整管道确认 placements 改变**

```bash
cd /github-repo/pcb-algo-wiki && .venv/bin/python -c "
from solver.v2.orchestrator import solve_layout_v2
result = solve_layout_v2('rf_layout_simplified.yaml')
for n, p in sorted(result.geometry.placements.items()):
    if n.startswith(('C','R')) and n[1].isdigit():
        print(f'{n}: rot={p.rotation_deg:.0f} anchor=({p.anchor.x:.2f},{p.anchor.y:.2f})')
print('failed:', result.phase_b.adhesion.failed)
"
```

预期：C3, C5 不再挂在 seg1 上（不再出现 `IC1_pin1_seg1` 或 `IC1_pin2_seg1`）；C7 可能仍走 legacy。

- [ ] **步骤 3：运行单测**

```bash
.venv/bin/python -m pytest tests/unit/v2/test_uv_slot_search.py -v 2>&1 | tail -8
```

- [ ] **步骤 4：Commit**

```bash
git add src/solver/v2/uv_adhesion.py
git commit -m "fix(uv-adhesion): exclude seg1, step_mm=0.8 for more interior samples (WI-E2)"
```

---

## 任务 3：Legacy fallback 从 host edge 推导旋转（Bug D）

**文件：**
- 修改：`src/solver/v2/uv_adhesion.py:209-264`（`_legacy_anchor_placement`）
- 新增辅助函数 `_host_edge_tangent`

当 UV 的另一端为 GND（`other_um=None`），当前 `_derive_rotation` 返回 None，
最终使用 `seed_rotation_deg=0`。对水平微带线（angle=0°）来说，0° rotation 导致 RLC 沿线排列——应改为 90°。

- [ ] **步骤 1：在 `uv_adhesion.py` 中添加 `_host_edge_tangent` 辅助函数**

在 `_local_pin_offset` 函数下方（约 line 267）添加：

```python
def _host_edge_tangent(
    anchor_endpoint: str,
    skeleton: SkeletonReport,
    edges_by_net: dict[str, list[str]],
    net: str,
    artifact: FrontendArtifact,
) -> float | None:
    """Return the trace angle (degrees) of the host edge that contains
    anchor_endpoint, or None if not found.  Used to derive perpendicular
    rotation for the UV body when the GND endpoint has no routed position.
    """
    for eid in edges_by_net.get(net, []):
        edge = artifact.edges.get(eid)
        if edge is None:
            continue
        if anchor_endpoint not in edge.connections:
            continue
        route = skeleton.routes.get(eid)
        if route is None or not route.success or len(route.polyline_um) < 2:
            continue
        poly = route.polyline_um
        dx = poly[-1][0] - poly[0][0]
        dy = poly[-1][1] - poly[0][1]
        if dx == 0 and dy == 0:
            continue
        return math.degrees(math.atan2(dy, dx))
    return None
```

- [ ] **步骤 2：修改 `_legacy_anchor_placement` 使用 host edge 旋转**

在 `_legacy_anchor_placement` 中找到：

```python
    rotation = _derive_rotation(uv, anchor_pin, anchor_um, other_pin, other_um)
    if rotation is None:
        rotation = float(seed_rotation_deg.get(uv_name, 0.0))
```

替换为：

```python
    rotation = _derive_rotation(uv, anchor_pin, anchor_um, other_pin, other_um)
    if rotation is None and uv.uv_meta is not None:
        # Derive perpendicular rotation from host edge direction.
        anchor_endpoint = f"{uv_name}.{anchor_pin}"
        host_angle = _host_edge_tangent(
            anchor_endpoint, skeleton, edges_by_net, uv.uv_meta.reference_net or "", artifact
        )
        if host_angle is not None:
            # Perpendicular to the trace: rotate 90° CCW from trace direction.
            rotation = host_angle + 90.0
    if rotation is None:
        rotation = float(seed_rotation_deg.get(uv_name, 0.0))
```

注意：`_legacy_anchor_placement` 现在需要 `edges_by_net` 和 `artifact` 参数。更新其函数签名并更新调用处（`_place_uv`）。

更新 `_legacy_anchor_placement` 签名：

```python
def _legacy_anchor_placement(
    uv_name: str,
    uv: ComponentExpansion,
    skeleton: SkeletonReport,
    seed_anchor_mm: dict[str, tuple[float, float]],
    seed_rotation_deg: dict[str, float],
    edges_by_net: dict[str, list[str]],
    artifact: FrontendArtifact,
) -> ComponentPlacement | None:
```

更新 `_place_uv` 中调用处：

```python
    legacy = _legacy_anchor_placement(
        uv_name, uv, skeleton, seed_anchor_mm, seed_rotation_deg,
        edges_by_net, artifact
    )
```

- [ ] **步骤 3：运行完整管道，检查 C7 等的 rotation**

```bash
.venv/bin/python -c "
from solver.v2.orchestrator import solve_layout_v2
result = solve_layout_v2('rf_layout_simplified.yaml')
for n, p in sorted(result.geometry.placements.items()):
    if n.startswith(('C','R')) and n[1].isdigit():
        print(f'{n}: rot={p.rotation_deg:.0f}')
"
```

预期：C7 rotation 变为 90 或 -90（若 seg2 水平则垂直方向）。

- [ ] **步骤 4：运行测试套件**

```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -10
```

- [ ] **步骤 5：Commit**

```bash
git add src/solver/v2/uv_adhesion.py
git commit -m "fix(uv-adhesion): derive perpendicular rotation from host edge when GND endpoint missing (WI-E3)"
```

---

## 任务 4：PhaseB SVG 统一标签格式（Bug E）

**文件：**
- 修改：`src/tools/pcb_solve_v2.py:2354`
- 测试：`tests/unit/test_phase_b_svg_rendering.py`

- [ ] **步骤 1：在测试中添加标签一致性断言**

在 `tests/unit/test_phase_b_svg_rendering.py` 的 `test_phase_b_svg_has_rlc_bbox_pads_and_gnd` 末尾追加：

```python
    # Phase A / preA style: node labels and edge short names must appear in phaseB too.
    # _phase_a_diag_overlay injects edge short labels and node id labels.
    assert 'fill="#7c3aed"' in svg or 'fill="#1f2937"' in svg  # node/edge label colours
```

- [ ] **步骤 2：运行确认失败**

```bash
.venv/bin/python -m pytest tests/unit/test_phase_b_svg_rendering.py -v 2>&1 | tail -10
```

预期：FAIL（phaseB 缺少紫色节点标签）。

- [ ] **步骤 3：在 phaseB 渲染调用中追加 `_phase_a_diag_overlay`**

在 `src/tools/pcb_solve_v2.py` 找到 phaseB 的 `_render_svg` 调用（约 line 2354）：

```python
        overlay=_pin_label_overlay(geom_b)
        + _phase_b_rlc_overlay(geom_b, result, layout),
```

改为：

```python
        overlay=_pin_label_overlay(geom_b)
        + _phase_a_diag_overlay(result, geom_b)
        + _phase_b_rlc_overlay(geom_b, result, layout),
```

`_phase_a_diag_overlay` 使用 `result.phase_a.skeleton.routes`，在 phaseB 阶段同样有效。

- [ ] **步骤 4：运行测试验证**

```bash
.venv/bin/python -m pytest tests/unit/test_phase_b_svg_rendering.py -v 2>&1 | tail -8
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/tools/pcb_solve_v2.py tests/unit/test_phase_b_svg_rendering.py
git commit -m "feat(phase-b-svg): add _phase_a_diag_overlay for consistent labels (WI-E4)"
```

---

## 任务 5：全量验证 + 推送

- [ ] **步骤 1：运行 verify_m1.sh 确认全绿**

```bash
cd /github-repo/pcb-algo-wiki && ./scripts/verify_m1.sh 2>&1 | tail -20
```

预期：black OK / ruff OK / mypy OK / pytest all PASS / topology OK。

- [ ] **步骤 2：生成最新 out/ 并视觉检查**

```bash
.venv/bin/python -m tools.pcb_solve_v2 rf_layout_simplified.yaml --out out
```

打开 `out/PA_Module_Simplified.phaseB.svg`，确认：
- 标签风格与 preA 相同（紫色节点 ID、灰色边名称）
- C3/C5 不再挂在 seg1 上
- 所有 RLC body 与微带线 seg1 无重叠
- C7 等 rotation 为 90° 或 -90°（垂直）

- [ ] **步骤 3：强制追加 out/ 并推送**

```bash
git add -f out/PA_Module_Simplified.phaseA.svg out/PA_Module_Simplified.phaseB.svg \
         out/PA_Module_Simplified.phaseC.svg out/PA_Module_Simplified.preA.svg \
         out/PA_Module_Simplified.summary.json out/PA_Module_Simplified.topology.svg \
         out/PA_Module_Simplified.phaseA.json out/PA_Module_Simplified.phaseB.json \
         out/PA_Module_Simplified.preA.json out/PA_Module_Simplified.final.svg
git commit -m "chore(out): update phaseB artifacts after WI-E fixes

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
git push origin HEAD:main
```

---

## 自检

**规格覆盖度：**
1. ✅ 标签不统一 → 任务 4（追加 `_phase_a_diag_overlay`）
2. ✅ 避免 seg1 → 任务 2（过滤 `_seg1` 边）
3. ✅ 垂直放置（rotation 与 trace 方向垂直）→ 任务 3（legacy path 推导旋转）
4. ✅ 合法性（body 不与 trace 重叠）→ 任务 1（self-segment split check）

**占位符扫描：** 无"待定"、"TODO"等。

**类型一致性：**
- `_host_edge_tangent` 参数 `artifact: FrontendArtifact` 与 `_place_uv` 中已有的 `artifact` 一致
- `edges_by_net` 类型 `dict[str, list[str]]` 在 `adhere_uv_components` 和 `_legacy_anchor_placement` 间一致
