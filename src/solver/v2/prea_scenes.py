"""PreA 场景分类器（M-PreA-Scene-Split 重构第一步）.

按业务场景把每条边分到三类，便于后续 PreA 流水线按场景拆分处理：

* :data:`SCENE_1_FIXED_TREE` —— 微带线起源于固定 pin（IC 等），整条线树
  几何完全由 pin 方向、segment 长度/宽度和 universal_junction 模板决定。
  典型：``IC1.PIN_1 → seg1 → universal_node → seg3 → C5.PIN_1``。
* :data:`SCENE_2_SHUNT_UV` —— 与微带线并联的 shunt UV（RLC），其一脚接
  微带线 split_pad，另一脚为 GND 或浮空。PreA 阶段不求解其位置，留给
  PhaseB UV 吸附处理。典型：C5 这类对地电容。
* :data:`SCENE_3_FLOATING` —— 两端都不是固定 pin、也不在场景 1 的微带线
  树内（典型：两个浮动器件之间的微带线）。PreA 用极简预布局摆出来。

* :data:`SCENE_NOT_APPLICABLE` —— 非微带线边（``flexible_path``、``trace``
  等）。PreA 不直接处理其几何。

该模块当前为只读分析器，不修改 ``positions``；后续 PreA 流水线会消费分类
结果以选择执行路径。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

SCENE_1_FIXED_TREE = "scene_1_fixed_tree"
SCENE_2_SHUNT_UV = "scene_2_shunt_uv"
SCENE_3_FLOATING = "scene_3_floating"
SCENE_NOT_APPLICABLE = "not_applicable"

PreaScene = str  # one of the SCENE_* constants above


@dataclass(frozen=True)
class SceneClassification:
    """单条边的场景分类结果."""

    edge_name: str
    scene: PreaScene
    reason: str = ""


def _split_endpoint_tokens(endpoint_id: str) -> tuple[str, ...]:
    """复合端点（``"A.PIN_1,B.PIN_1"``）拆成单端点元组."""
    if "," not in endpoint_id:
        return (endpoint_id,)
    return tuple(part.strip() for part in endpoint_id.split(",") if part.strip())


def _endpoint_components(endpoint_id: str) -> tuple[str, ...]:
    """从端点 id 提取所有涉及的器件名（去重保序）."""
    comps: list[str] = []
    for token in _split_endpoint_tokens(endpoint_id):
        if "." in token:
            comp = token.split(".", 1)[0]
            if comp not in comps:
                comps.append(comp)
    return tuple(comps)


def _is_microstrip(edge: Any) -> bool:
    return getattr(edge, "edge_type", "") == "microstrip"


def _build_fixed_tree_closure(artifact: Any) -> set[str]:
    """从所有 fixed_terminals 出发，沿 microstrip 边/junction 节点做 BFS，
    返回所有"属于固定微带线树"的端点 id 集合（含 junction 节点 id）.

    复合端点：只要其任一拆分子端点已在闭包中，整个复合端点都纳入闭包。
    串联 UV RLC（两个 pin 都连到 microstrip）会"传导"闭包：任一 pin 进入
    闭包，另一 pin 也加入（用于覆盖 C1/R1 → C2 这类串联链路场景 1）。
    """
    closure: set[str] = set(getattr(artifact, "fixed_terminals", {}).keys())
    # 邻接表：endpoint -> list[(other_endpoint, edge_name)]
    adj: dict[str, list[tuple[str, str]]] = {}
    for name, edge in getattr(artifact, "edges", {}).items():
        if not _is_microstrip(edge) or len(edge.connections) != 2:
            continue
        a, b = edge.connections
        adj.setdefault(a, []).append((b, name))
        adj.setdefault(b, []).append((a, name))
        # 复合端点：把每个 token 也作为入口
        for token in _split_endpoint_tokens(a):
            if token != a:
                adj.setdefault(token, []).append((b, name))
        for token in _split_endpoint_tokens(b):
            if token != b:
                adj.setdefault(token, []).append((a, name))

    # 串联 UV RLC 桥：器件名 -> 该器件出现在 microstrip 中的所有端点 id 集合
    series_bridge: dict[str, set[str]] = {}
    for comp_name in getattr(artifact, "uv_components", {}):
        endpoints_with_this_comp: set[str] = set()
        for edge in getattr(artifact, "edges", {}).values():
            if not _is_microstrip(edge):
                continue
            for ep in edge.connections:
                for token in _split_endpoint_tokens(ep):
                    if token.startswith(f"{comp_name}."):
                        endpoints_with_this_comp.add(ep)
                        break
        pins_seen = set()
        for ep in endpoints_with_this_comp:
            for token in _split_endpoint_tokens(ep):
                if token.startswith(f"{comp_name}."):
                    pins_seen.add(token.split(".", 1)[1])
        if len(pins_seen) >= 2:  # 串联：两个或更多 pin 都接 microstrip
            series_bridge[comp_name] = endpoints_with_this_comp

    queue: deque[str] = deque(closure)
    while queue:
        current = queue.popleft()
        # 邻接 microstrip 端点扩散
        for neighbor, _edge_name in adj.get(current, []):
            tokens = _split_endpoint_tokens(neighbor)
            new_added = False
            if neighbor not in closure:
                closure.add(neighbor)
                new_added = True
            for token in tokens:
                if token not in closure:
                    closure.add(token)
                    new_added = True
            if new_added:
                queue.append(neighbor)
        # 串联 UV 桥扩散：current 涉及的器件若为串联 RLC，加入其全部 pin 端点
        for comp in _endpoint_components(current):
            bridge_eps = series_bridge.get(comp)
            if not bridge_eps:
                continue
            for ep in bridge_eps:
                if ep in closure:
                    continue
                closure.add(ep)
                for token in _split_endpoint_tokens(ep):
                    closure.add(token)
                queue.append(ep)
    return closure


def _is_uv_rlc_component(artifact: Any, comp_name: str) -> bool:
    return comp_name in getattr(artifact, "uv_components", {})


def _component_microstrip_pins(artifact: Any, comp_name: str) -> dict[str, list[str]]:
    """返回该器件每个 pin 参与的 microstrip 边名列表。"""
    out: dict[str, list[str]] = {}
    for name, edge in getattr(artifact, "edges", {}).items():
        if not _is_microstrip(edge):
            continue
        for ep in edge.connections:
            for token in _split_endpoint_tokens(ep):
                if "." not in token:
                    continue
                comp, pin = token.split(".", 1)
                if comp != comp_name:
                    continue
                out.setdefault(pin, []).append(name)
    return out


def classify_edges(artifact: Any) -> dict[str, SceneClassification]:
    """对 artifact 中的每条边给出场景分类.

    Parameters
    ----------
    artifact:
        :class:`~frontend.models.FrontendArtifact` 或同形对象，须暴露
        ``edges``、``fixed_terminals``、``uv_components``、``nodes``.

    Returns
    -------
    dict[str, SceneClassification]
        以 edge 名为 key 的分类结果.
    """
    closure = _build_fixed_tree_closure(artifact)
    results: dict[str, SceneClassification] = {}

    for name, edge in getattr(artifact, "edges", {}).items():
        if not _is_microstrip(edge):
            results[name] = SceneClassification(
                edge_name=name,
                scene=SCENE_NOT_APPLICABLE,
                reason=f"edge_type={getattr(edge, 'edge_type', '?')}",
            )
            continue
        if len(edge.connections) != 2:
            results[name] = SceneClassification(
                edge_name=name,
                scene=SCENE_NOT_APPLICABLE,
                reason="microstrip with !=2 connections",
            )
            continue

        a, b = edge.connections

        # 场景 2 优先检测：任一端涉及"shunt UV RLC"（该 RLC 只有一个 pin
        # 连到 microstrip，另一 pin 接 GND/浮空），整条边交给 PhaseB 处理.
        shunt_comp = _detect_shunt_uv_rlc(artifact, a, b)
        if shunt_comp is not None:
            results[name] = SceneClassification(
                edge_name=name,
                scene=SCENE_2_SHUNT_UV,
                reason=f"shunt UV RLC {shunt_comp}: only one pin on microstrip",
            )
            continue

        a_in = a in closure or any(t in closure for t in _split_endpoint_tokens(a))
        b_in = b in closure or any(t in closure for t in _split_endpoint_tokens(b))

        if a_in and b_in:
            results[name] = SceneClassification(
                edge_name=name,
                scene=SCENE_1_FIXED_TREE,
                reason="both endpoints in fixed-pin microstrip tree",
            )
            continue

        results[name] = SceneClassification(
            edge_name=name,
            scene=SCENE_3_FLOATING,
            reason="neither endpoint in fixed-pin microstrip tree",
        )

    return results


def _detect_shunt_uv_rlc(artifact: Any, ep_a: str, ep_b: str) -> str | None:
    """若任一端属于 shunt UV RLC（只有一个 pin 接 microstrip），返回器件名."""
    for ep in (ep_a, ep_b):
        for comp in _endpoint_components(ep):
            if not _is_uv_rlc_component(artifact, comp):
                continue
            pins = _component_microstrip_pins(artifact, comp)
            # 只有一个 pin 出现在 microstrip 中 → shunt
            if len(pins) <= 1:
                return comp
    return None


def scene_summary(
    classifications: dict[str, SceneClassification],
) -> dict[str, int]:
    """按场景统计数量，便于诊断输出."""
    out: dict[str, int] = {}
    for c in classifications.values():
        out[c.scene] = out.get(c.scene, 0) + 1
    return out


__all__ = [
    "PreaScene",
    "SCENE_1_FIXED_TREE",
    "SCENE_2_SHUNT_UV",
    "SCENE_3_FLOATING",
    "SCENE_NOT_APPLICABLE",
    "SceneClassification",
    "classify_edges",
    "scene_summary",
]
