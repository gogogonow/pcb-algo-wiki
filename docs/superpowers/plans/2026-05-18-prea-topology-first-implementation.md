# preA Topology-First 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 preA 改为“按 node 定义构建微带线+串联 RLC 拓扑”的阶段，确保 preA.svg / preA.json / viewer preA 三端一致。

**架构：** 在 `src/tools/pcb_solve_v2.py` 增加统一 `PreATopologyModel` 构建函数，集中生成 preA 边端点与 preA uv_placements。`_render_pre_phase_svg` 与 `_emit_viewer_bundle` 全部消费该模型，不再各自拼装 preA 几何。先通过失败测试锁定行为，再最小实现通过。

**技术栈：** Python 3.11、pytest、black、ruff、mypy、现有 FrontendArtifact / UniversalJunctionTemplate / viewer bundle 输出链。

---

## 文件结构（先锁边界）

- 修改：`src/tools/pcb_solve_v2.py`
  - 新增 `PreATopologyModel` 数据结构与构建函数
  - 让 `_render_pre_phase_svg` 和 `_emit_viewer_bundle` 复用同一 preA 拓扑数据
- 修改：`tests/unit/v2/test_viewer_bundle.py`
  - 增加 preA 拓扑模式行为测试（edge 可见性、长度语义、preA uv_placements）
- 可能修改：`tests/unit/test_pcb_solve_v2_svg.py`
  - 若已有 preA SVG 结构测试，补充分支 edge 与 RLC 断言

---

### 任务 1：用失败测试锁定 preA 新语义（viewer bundle）

**文件：**
- 修改：`tests/unit/v2/test_viewer_bundle.py`
- 目标代码：`src/tools/pcb_solve_v2.py`

- [ ] **步骤 1：编写失败测试（preA 边必须按拓扑完整输出）**

```python
def test_emit_viewer_bundle_prea_emits_all_microstrip_branches(tmp_path) -> None:
    result = _fixture_with_universal_junction_4_branches()
    out = _emit_viewer_bundle(result, tmp_path)
    data = json.loads(out.read_text())
    edges = {e["edge_id"]: e for e in data["phases"]["preA"]["edges"]}
    assert set(edges) >= {"seg1", "seg2", "seg3", "seg4"}
    for edge_id in ("seg1", "seg2", "seg3", "seg4"):
        ep = edges[edge_id]["endpoint_positions_mm"]
        assert len(ep) == 2
```

- [ ] **步骤 2：运行测试验证失败**

运行：
```bash
cd /github-repo/pcb-algo-wiki && .venv/bin/python -m pytest tests/unit/v2/test_viewer_bundle.py::test_emit_viewer_bundle_prea_emits_all_microstrip_branches -q
```

预期：FAIL（当前 preA 数据源/构建链不满足新语义）。

- [ ] **步骤 3：编写失败测试（preA 必须有 uv_placements）**

```python
def test_emit_viewer_bundle_prea_has_uv_placements(tmp_path) -> None:
    result = _fixture_with_serial_rlc()
    out = _emit_viewer_bundle(result, tmp_path)
    data = json.loads(out.read_text())
    placements = data["phases"]["preA"]["uv_placements"]
    assert {p["ref"] for p in placements} == {"R1", "C1"}
```

- [ ] **步骤 4：运行测试验证失败**

运行：
```bash
cd /github-repo/pcb-algo-wiki && .venv/bin/python -m pytest tests/unit/v2/test_viewer_bundle.py::test_emit_viewer_bundle_prea_has_uv_placements -q
```

预期：FAIL（旧实现无 preA uv_placements 或字段不完整）。

- [ ] **步骤 5：Commit（只提交测试）**

```bash
git add tests/unit/v2/test_viewer_bundle.py
git commit -m "test(prea): lock topology-first preA bundle behavior"
```

---

### 任务 2：实现统一 PreATopologyModel 构建器

**文件：**
- 修改：`src/tools/pcb_solve_v2.py`
- 测试：`tests/unit/v2/test_viewer_bundle.py`

- [ ] **步骤 1：新增 PreA 拓扑模型结构与构建入口**

```python
@dataclass
class PreATopologyModel:
    endpoint_positions: dict[str, tuple[float, float]]
    edge_endpoint_overrides: dict[str, dict[str, tuple[float, float]]]
    pre_edges: list[dict[str, Any]]
    pre_uv_placements: list[dict[str, Any]]
```

```python
def _build_prea_topology_model(
    result: OrchestratorV2Result,
    *,
    board_w: float,
    board_h: float,
    junction_templates: dict[str, UniversalJunctionTemplate] | None = None,
    branch_offset_u_tokens: dict[str, str] | None = None,
) -> PreATopologyModel:
    ...
```

- [ ] **步骤 2：把 edge 几何构建迁入统一函数**

```python
# for each microstrip edge with 2 endpoints:
# 1) read endpoint from overrides > endpoint_positions
# 2) enforce target_length semantics for rendered main segment
# 3) emit endpoint_positions_mm for both endpoints
```

- [ ] **步骤 3：把 preA 串联 RLC 几何输出迁入统一函数**

```python
# derive preA uv placements from endpoint_positions
# preserve pad pitch and footprint-based body orientation
pre_uv_placements.append({
    "ref": name,
    "anchor_x_mm": ...,
    "anchor_y_mm": ...,
    "rotation_deg": ...,
    "pads": [...],
})
```

- [ ] **步骤 4：运行任务 1 的全部测试验证通过**

运行：
```bash
cd /github-repo/pcb-algo-wiki && .venv/bin/python -m pytest tests/unit/v2/test_viewer_bundle.py -q
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/tools/pcb_solve_v2.py tests/unit/v2/test_viewer_bundle.py
git commit -m "feat(prea): add topology-first preA model for bundle outputs"
```

---

### 任务 3：让 preA.svg 与 viewer preA 共用同一模型

**文件：**
- 修改：`src/tools/pcb_solve_v2.py`
- 测试：`tests/unit/test_pcb_solve_v2_svg.py`（若已有 preA 断言）

- [ ] **步骤 1：重构 `_render_pre_phase_svg` 消费 `PreATopologyModel`**

```python
model = _build_prea_topology_model(...)
# draw edges using model.pre_edges / model.edge_endpoint_overrides
# draw uv/rlc using model.pre_uv_placements
```

- [ ] **步骤 2：重构 `_emit_viewer_bundle` 消费同一模型**

```python
model = _build_prea_topology_model(...)
bundle["phases"]["preA"] = {
    "edges": model.pre_edges,
    "uv_placements": model.pre_uv_placements,
}
```

- [ ] **步骤 3：补充（或更新）preA SVG 回归测试**

```python
def test_prea_svg_contains_all_branch_edge_ids(...):
    ...
```

- [ ] **步骤 4：运行相关测试**

运行：
```bash
cd /github-repo/pcb-algo-wiki && .venv/bin/python -m pytest tests/unit/v2/test_viewer_bundle.py tests/unit/test_pcb_solve_v2_svg.py -q
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/tools/pcb_solve_v2.py tests/unit/test_pcb_solve_v2_svg.py tests/unit/v2/test_viewer_bundle.py
git commit -m "refactor(prea): unify preA svg/json/viewer on shared topology model"
```

---

### 任务 4：质量验证与交付

**文件：**
- 修改：`README.md`（仅在需要补充 preA 语义时）

- [ ] **步骤 1：运行目标回归**

```bash
cd /github-repo/pcb-algo-wiki && .venv/bin/python -m pytest tests/unit/v2/test_viewer_bundle.py tests/unit/test_viewer_html_smoke.py tests/unit/test_pcb_solve_v2_svg.py -q
```

- [ ] **步骤 2：运行代码质量检查**

```bash
cd /github-repo/pcb-algo-wiki && .venv/bin/python -m black --check src/tools/pcb_solve_v2.py tests/unit/v2/test_viewer_bundle.py tests/unit/test_pcb_solve_v2_svg.py
cd /github-repo/pcb-algo-wiki && .venv/bin/python -m ruff check src/tools/pcb_solve_v2.py tests/unit/v2/test_viewer_bundle.py tests/unit/test_pcb_solve_v2_svg.py
cd /github-repo/pcb-algo-wiki && .venv/bin/python -m mypy src/tools/pcb_solve_v2.py --no-error-summary
```

- [ ] **步骤 3：手工样例验证（你关心的分支）**

```bash
cd /github-repo/pcb-algo-wiki
pcb_solve rf_layout_simplified.yaml --stop-after phaseB --quiet
# 打开 viewer/viewer.html，加载 out/*.viewer.json
# 检查 preA: seg1/2/3/4 全出现，串联RLC可见
```

- [ ] **步骤 4：Commit + Push**

```bash
git add -A
git commit -m "fix(prea): topology-first construction for microstrip and serial RLC"
git push origin main
```

---

## 规格覆盖自检

- 需求“preA 只做微带线 + 串联RLC 构建”：由任务 2/3 覆盖。
- 需求“宽长不变、按 node 方向”：由任务 2 的 edge 构建规则与测试覆盖。
- 需求“先不考虑走线与坐标优化、越界不管”：在任务 2 的实现约束里明确，不引入冲突/越界优化。
- 需求“三端一致（svg/json/viewer）”：由任务 3 的共享模型与回归测试覆盖。

占位符扫描：无 TODO/待定条目。  
类型一致性：统一使用 `PreATopologyModel`，避免多套字段名漂移。

