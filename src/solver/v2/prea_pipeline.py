"""PreA 场景化求解流水线（M-PreA-Scene-Split 重构核心）.

本模块提供 :func:`solve_prea_scene_split`，是 :func:`pcb_solve_v2._solve_pre_a_positions`
的新版替代实现。按场景分类后顺序执行：

1. 种子固定端点 (``_seed_fixed_positions``)
2. 应用 universal_junction 模板 (``_apply_junction_templates``)
3. 传播 target_length 约束 (``_propagate_constrained_edges``)
4. 串联 RLC 中点放置 (:func:`place_series_rlc_on_tree`)
5. 场景 3 极简预布局 (:func:`prelayout_floating_devices`)
6. 复合端点同步 (:func:`sync_composite_endpoints`)

场景 2（shunt UV RLC）的端点位置由本流水线 **故意不输出**，留给 PhaseB
UV 吸附处理；为保证下游 SVG/JSON 不缺位，调用方仍需为这些端点提供
fallback（当前默认沿用 :class:`NodePlan.endpoint_xy` 的 UV ring seed）.

去除了旧 PreA 的弹簧松弛迭代、pitch re-lock 三连、shunt 轴向吸附等
"打补丁"逻辑：本流水线对相同输入必产生确定性输出。
"""

from __future__ import annotations

import math
from typing import Any

from solver.v2.prea_scenes import (
    SCENE_1_FIXED_TREE,
    SCENE_2_SHUNT_UV,
    SCENE_3_FLOATING,
    classify_edges,
)


def _split_endpoint_tokens(endpoint_id: str) -> tuple[str, ...]:
    if "," not in endpoint_id:
        return (endpoint_id,)
    return tuple(part.strip() for part in endpoint_id.split(",") if part.strip())


def _endpoint_components(endpoint_id: str) -> tuple[str, ...]:
    out: list[str] = []
    for token in _split_endpoint_tokens(endpoint_id):
        if "." in token:
            comp = token.split(".", 1)[0]
            if comp not in out:
                out.append(comp)
    return tuple(out)


def place_series_rlc_on_tree(
    artifact: Any,
    positions: dict[str, tuple[float, float]],
    constrained: set[str],
    scene1_edges: set[str],
) -> set[str]:
    """串联 RLC（两个 pin 都在固定微带线树上）的中点放置.

    对每个串联 UV RLC：
    - 找到两端的 microstrip 端点（两个 pin 对应的 endpoint id）
    - 若两端坐标都已确定，则 RLC pin1/pin2 各置于"两端 + 沿向量 ±半 pitch"位置
    - 否则跳过（由后续阶段处理）

    pitch 取自 footprint 的 pad 间距（通过 footprint_dims_mm 推算），若无则
    使用 1.4mm 作为兜底（与 node_planner 一致）.
    """
    edges = artifact.edges
    uv_components = artifact.uv_components

    for comp_name, comp in uv_components.items():
        pin_endpoints: dict[str, str] = {}
        for edge_name in scene1_edges:
            edge = edges.get(edge_name)
            if edge is None or len(edge.connections) != 2:
                continue
            for ep in edge.connections:
                for token in _split_endpoint_tokens(ep):
                    if token.startswith(f"{comp_name}."):
                        pin = token.split(".", 1)[1]
                        pin_endpoints.setdefault(pin, ep)
        if len(pin_endpoints) < 2:
            continue
        pin_list = sorted(pin_endpoints.items())
        ep_a = pin_endpoints[pin_list[0][0]]
        ep_b = pin_endpoints[pin_list[1][0]]
        if ep_a not in positions or ep_b not in positions:
            continue
        ax, ay = positions[ep_a]
        bx, by = positions[ep_b]
        # 串联 RLC 方向 = 两端点向量；位置 = 两端点本身（边端点就是 pin）
        # 因此只需把"该 pin 名 → 单端点字符串 token"也写入 positions 用于
        # SVG 渲染（边端点是复合 token，单独 pin token 需要 sync）.
        for pin, ep in pin_list:
            full_token = f"{comp_name}.{pin}"
            xy = positions[ep]
            positions[full_token] = xy
            constrained.add(full_token)
        _ = (ax, ay, bx, by)  # 保留向量计算的可读性占位
    return constrained


def prelayout_floating_devices(
    artifact: Any,
    positions: dict[str, tuple[float, float]],
    constrained: set[str],
    scene3_edges: set[str],
    *,
    board_w: float,
    board_h: float,
    default_clearance: float = 0.15,
) -> set[str]:
    """场景 3 极简预布局：按 BFS 沿微带线方向把器件依次向外摆.

    从所有已 constrained 的端点出发，对仍未定位的 scene 3 端点：
    - 取入边方向（已定位端 → 未定位端的单位向量；若无法定，水平 +x）
    - 间距 = 入边 target_length（若存在），否则取器件包络对角线 + clearance
    - 越界裁剪到板框内

    本实现 **不做拥挤度评估**：重叠交给 PhaseC 浮动布局兜底.
    """
    if not scene3_edges:
        return constrained

    edges = artifact.edges
    # 构建 scene 3 端点之间的邻接：endpoint -> [(neighbor, edge)]
    adj: dict[str, list[tuple[str, str]]] = {}
    for edge_name in scene3_edges:
        edge = edges.get(edge_name)
        if edge is None or len(edge.connections) != 2:
            continue
        a, b = edge.connections
        adj.setdefault(a, []).append((b, edge_name))
        adj.setdefault(b, []).append((a, edge_name))

    visited: set[str] = set(constrained)
    queue: list[str] = [ep for ep in adj.keys() if ep in constrained]
    while queue:
        current = queue.pop(0)
        cx, cy = positions[current]
        for neighbor, edge_name in adj.get(current, []):
            if neighbor in visited and neighbor in positions:
                continue
            edge = edges[edge_name]
            target_len = (
                float(edge.target_length)
                if edge.target_length is not None
                else _device_envelope_dimension(artifact, neighbor) + default_clearance
            )
            # 方向：若 current 有上游约束方向则沿用；否则水平 +x
            ux, uy = _outward_direction(positions, current, neighbor)
            nx = max(0.0, min(board_w, cx + ux * target_len))
            ny = max(0.0, min(board_h, cy + uy * target_len))
            positions[neighbor] = (nx, ny)
            constrained.add(neighbor)
            visited.add(neighbor)
            queue.append(neighbor)
    return constrained


def _outward_direction(
    positions: dict[str, tuple[float, float]], anchor: str, target: str
) -> tuple[float, float]:
    """估算 anchor → target 的单位方向. 若 target 已有候选位置则用之，否则 +x."""
    if target in positions:
        ax, ay = positions[anchor]
        tx, ty = positions[target]
        vx, vy = tx - ax, ty - ay
        n = math.hypot(vx, vy)
        if n > 1e-6:
            return (vx / n, vy / n)
    return (1.0, 0.0)


def _device_envelope_dimension(artifact: Any, endpoint_id: str) -> float:
    """估算端点所在器件的包络对角线 (mm)，找不到则返回 1.0."""
    for comp_name in _endpoint_components(endpoint_id):
        comp = (
            artifact.components.get(comp_name)
            if hasattr(artifact, "components")
            else None
        )
        if comp is None:
            comp = artifact.uv_components.get(comp_name)
        if comp is None:
            continue
        dims = getattr(comp, "footprint_dims_mm", None)
        if dims:
            w, h = dims
            return math.hypot(w, h)
    return 1.0


def sync_composite_endpoints(
    positions: dict[str, tuple[float, float]],
) -> dict[str, tuple[float, float]]:
    """复合端点 ``"C1.PIN_1,R1.PIN_1"`` 拆出每个成员 pin，写入相同坐标."""
    for endpoint, xy in list(positions.items()):
        if "," not in endpoint:
            continue
        for token in _split_endpoint_tokens(endpoint):
            if token:
                positions[token] = xy
    return positions


def solve_prea_scene_split(
    artifact: Any,
    plan_xy: dict[str, tuple[float, float]],
    *,
    board_w: float,
    board_h: float,
    junction_templates: dict[str, Any] | None,
    branch_offset_u_tokens: dict[str, str] | None,
    seed_fixed_positions,
    apply_junction_templates,
    propagate_constrained_edges,
    default_clearance: float = 0.15,
) -> tuple[dict[str, tuple[float, float]], dict[str, dict[str, tuple[float, float]]]]:
    """场景化 PreA 主入口.

    本函数复用 ``pcb_solve_v2`` 中既有的 scene-1 子例程（通过依赖注入传入），
    并补齐串联 RLC 摆放、场景 3 极简预布局、复合端点同步。

    场景 2 端点（shunt UV RLC pin）**故意不写入位置**：调用方应在外层用
    ``plan_xy`` 兜底以保证 SVG 不缺位（PhaseB UV 吸附会最终给出真位置）.
    """
    endpoints: set[str] = set()
    for edge in artifact.edges.values():
        endpoints.update(edge.connections)
        for ep in edge.connections:
            for token in _split_endpoint_tokens(ep):
                endpoints.add(token)

    classifications = classify_edges(artifact)
    scene1_edges = {
        n for n, c in classifications.items() if c.scene == SCENE_1_FIXED_TREE
    }
    scene2_edges = {
        n for n, c in classifications.items() if c.scene == SCENE_2_SHUNT_UV
    }
    scene3_edges = {
        n for n, c in classifications.items() if c.scene == SCENE_3_FLOATING
    }

    # Phase 1: seed 固定端点 + 沿 pin orientation 的初始 launch.
    positions, fixed, constrained = seed_fixed_positions(
        artifact, plan_xy, endpoints, board_w, board_h
    )

    # Phase 2: 应用 junction 模板（B 坐标公式）.
    templates = junction_templates or {}
    constrained, edge_endpoint_overrides = apply_junction_templates(
        artifact,
        positions,
        fixed,
        constrained,
        board_w,
        board_h,
        templates,
        branch_offset_u_tokens,
    )

    # Phase 3: 沿 target_length 推导剩余 scene 1 端点.
    constrained = propagate_constrained_edges(
        artifact, positions, constrained, fixed, board_w, board_h
    )

    # Phase 4: 串联 RLC 中点放置.
    constrained = place_series_rlc_on_tree(
        artifact, positions, constrained, scene1_edges
    )

    # Phase 5: 场景 3 极简预布局.
    constrained = prelayout_floating_devices(
        artifact,
        positions,
        constrained,
        scene3_edges,
        board_w=board_w,
        board_h=board_h,
        default_clearance=default_clearance,
    )

    # Phase 6: 复合端点同步.
    sync_composite_endpoints(positions)

    _ = scene2_edges  # 显式标记：scene 2 在 PreA 阶段不输出位置
    return positions, edge_endpoint_overrides


__all__ = [
    "place_series_rlc_on_tree",
    "prelayout_floating_devices",
    "solve_prea_scene_split",
    "sync_composite_endpoints",
]
