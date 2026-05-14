"""Heuristic node-position planner (lightweight stand-in for the §3.2 LP).

Resolves an initial position for every endpoint that is not already a
fixed pad, so Phase A can route concrete (start, goal) pairs:

* **Universal/T/T-combiner junctions** → centroid of fixed endpoints in the
  same net, biased by branch directions when available.
* **UV-component anchor pins** → midpoint between the host_edge's other
  endpoint and the centroid of the reference_net.
* **UV non-anchor pins** → derived from anchor + footprint local offset
  (rotation defaults to 0; refined by Phase B).

This is intentionally simple — the goal is only to give A* a sensible
seed; rip-up & reroute (M10c) recovers from poor initial positions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from frontend.models import (
    ComponentExpansion,
    ExpandedPad,
    FrontendArtifact,
    TriagedEdge,
)


@dataclass
class NodePlan:
    """Resolved coordinates for every endpoint string referenced by edges."""

    endpoint_xy: dict[str, tuple[float, float]] = field(default_factory=dict)
    """Map ``"NodeId"`` or ``"Component.PIN_x"`` → (x_mm, y_mm)."""

    uv_anchor_seed: dict[str, tuple[float, float]] = field(default_factory=dict)
    """Per-UV-component initial anchor pin position (mm)."""

    uv_rotation_seed: dict[str, float] = field(default_factory=dict)
    """Per-UV initial rotation (degrees)."""


def plan_node_positions(
    artifact: FrontendArtifact,
    *,
    board_width_mm: float,
    board_height_mm: float,
) -> NodePlan:
    plan = NodePlan()

    # 1. Fixed pads: copy directly.
    for key, pad in artifact.fixed_terminals.items():
        if pad.abs_x is not None and pad.abs_y is not None:
            plan.endpoint_xy[key] = (pad.abs_x, pad.abs_y)

    # 2. Compute per-net centroid of fixed pads (helps junctions & UVs).
    net_to_pads = _net_to_endpoints(artifact)
    net_centroid: dict[str, tuple[float, float]] = {}
    board_center = (board_width_mm / 2.0, board_height_mm / 2.0)
    for net, endpoints in net_to_pads.items():
        coords = [plan.endpoint_xy[e] for e in endpoints if e in plan.endpoint_xy]
        if coords:
            cx = sum(c[0] for c in coords) / len(coords)
            cy = sum(c[1] for c in coords) / len(coords)
            net_centroid[net] = (cx, cy)
        else:
            net_centroid[net] = board_center

    # 3. UV anchor seeds: spread around net centroid in distinct directions to
    #    avoid collapsing every UV pin onto a single coordinate.
    for net, endpoints in net_to_pads.items():
        uvs_in_net = [
            (uv_name, uv)
            for uv_name, uv in artifact.uv_components.items()
            if (uv.uv_meta.reference_net if uv.uv_meta else None) == net
        ]
        if not uvs_in_net:
            continue
        cx, cy = net_centroid.get(net, board_center)
        # Seed along a circle of radius 3mm around the centroid, one direction
        # per UV. This guarantees distinct seeds so A* targets don't collide.
        radius = 3.0
        n = len(uvs_in_net)
        for idx, (uv_name, uv) in enumerate(uvs_in_net):
            angle = 2.0 * math.pi * idx / max(n, 1)
            sx = _clamp(cx + radius * math.cos(angle), 1.0, board_width_mm - 1.0)
            sy = _clamp(cy + radius * math.sin(angle), 1.0, board_height_mm - 1.0)
            plan.uv_anchor_seed[uv_name] = (sx, sy)
            plan.uv_rotation_seed[uv_name] = 0.0
            for pad in uv.pads:
                # Spread the non-anchor pads along +x by 1.4mm so they don't
                # coincide with the anchor pad in the seed.
                if uv.uv_meta and pad.pin == uv.uv_meta.anchor_pin:
                    plan.endpoint_xy[f"{uv_name}.{pad.pin}"] = (sx, sy)
                else:
                    plan.endpoint_xy[f"{uv_name}.{pad.pin}"] = (sx + 1.4, sy)

    # Catch any UV components that didn't get a net match.
    for uv_name, uv in artifact.uv_components.items():
        if uv_name in plan.uv_anchor_seed:
            continue
        plan.uv_anchor_seed[uv_name] = board_center
        plan.uv_rotation_seed[uv_name] = 0.0
        for pad in uv.pads:
            plan.endpoint_xy[f"{uv_name}.{pad.pin}"] = board_center

    # 4. Junction nodes: centroid of incident edges' "other endpoint".
    edge_endpoints = _edge_endpoints_by_node(artifact)
    for node_name, node in artifact.nodes.items():
        candidates: list[tuple[float, float]] = []
        for edge in edge_endpoints.get(node_name, ()):
            other = next((ep for ep in edge.connections if ep != node_name), None)
            if other and other in plan.endpoint_xy:
                candidates.append(plan.endpoint_xy[other])
        if candidates:
            cx = sum(c[0] for c in candidates) / len(candidates)
            cy = sum(c[1] for c in candidates) / len(candidates)
        else:
            cx, cy = board_center
        plan.endpoint_xy[node_name] = (
            _clamp(cx, 1.0, board_width_mm - 1.0),
            _clamp(cy, 1.0, board_height_mm - 1.0),
        )
        # Light pull along universal_junction's reference_edge if available.
        _ = node  # no-op; kept for future biasing

    # 5. One relaxation pass: re-centre nodes once UVs got a seed.
    for node_name in artifact.nodes:
        candidates = []
        for edge in edge_endpoints.get(node_name, ()):
            other = next((ep for ep in edge.connections if ep != node_name), None)
            if other and other in plan.endpoint_xy:
                candidates.append(plan.endpoint_xy[other])
        if candidates:
            cx = sum(c[0] for c in candidates) / len(candidates)
            cy = sum(c[1] for c in candidates) / len(candidates)
            plan.endpoint_xy[node_name] = (
                _clamp(cx, 1.0, board_width_mm - 1.0),
                _clamp(cy, 1.0, board_height_mm - 1.0),
            )

    return plan


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _net_to_endpoints(artifact: FrontendArtifact) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for edge in artifact.edges.values():
        if edge.routing_class.startswith("rf_constrained"):
            net = _edge_net(artifact, edge)
            for ep in edge.connections:
                out.setdefault(net or "", []).append(ep)
    return out


def _edge_net(artifact: FrontendArtifact, edge: TriagedEdge) -> str | None:
    # TriagedEdge does not currently carry .net; we rely on V6IR/SolverIR for
    # that. As a fallback heuristic, infer net by lookup in component.pin_nets.
    for ep in edge.connections:
        if "." not in ep:
            continue
        comp_name, pin = ep.split(".", 1)
        comp = _component_by_name(artifact, comp_name)
        if comp is None or comp.uv_meta is None:
            continue
        meta = comp.uv_meta
        if pin == meta.anchor_pin:
            return meta.reference_net
    return None


def _component_by_name(
    artifact: FrontendArtifact, name: str
) -> ComponentExpansion | None:
    return artifact.components.get(name)


def _edge_endpoints_by_node(
    artifact: FrontendArtifact,
) -> dict[str, list[TriagedEdge]]:
    out: dict[str, list[TriagedEdge]] = {}
    nodes_set = set(artifact.nodes.keys())
    for edge in artifact.edges.values():
        for ep in edge.connections:
            if ep in nodes_set:
                out.setdefault(ep, []).append(edge)
    return out


def _pad_xy(pad: ExpandedPad) -> tuple[float, float] | None:
    if pad.abs_x is None or pad.abs_y is None:
        return None
    return pad.abs_x, pad.abs_y


def _direction_to(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


__all__ = ["NodePlan", "plan_node_positions"]
