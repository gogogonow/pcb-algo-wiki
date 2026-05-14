"""M3 — universal_junction template expansion.

For each `universal_junction` node we emit one BranchConstraint per branch:
the (dx, dy) constants that, added to the node's center variable pair,
yield the absolute (x, y) coordinates of the branch's far endpoint.

Per `ALGORITHM-OVERVIEW.md` §5:

```
signed_v = +(W/2 + clearance)   if offset_v == "edge_left"
         = -(W/2 + clearance)   if offset_v == "edge_right"
         = 0                    if offset_v == "align_center"
cosθ, sinθ = cos(angle°), sin(angle°)
dx = cosθ · offset_u  −  sinθ · signed_v
dy = sinθ · offset_u  +  cosθ · signed_v
```
"""

from __future__ import annotations

import math

from schema.solver_ir import (
    BranchConstraint,
    SignedVKind,
    UniversalJunctionTemplate,
)
from schema.v33 import Edge, Node, V33Layout

_ANGLE_TRIG_EXACT: dict[float, tuple[float, float]] = {
    0.0: (1.0, 0.0),
    90.0: (0.0, 1.0),
    -90.0: (0.0, -1.0),
    180.0: (-1.0, 0.0),
    -180.0: (-1.0, 0.0),
    270.0: (0.0, -1.0),
    -270.0: (0.0, 1.0),
}


def _trig(angle_deg: float) -> tuple[float, float]:
    """Return (cosθ, sinθ) using exact integer values for axis-aligned angles."""

    normalized = float(angle_deg)
    if normalized in _ANGLE_TRIG_EXACT:
        return _ANGLE_TRIG_EXACT[normalized]
    radians = math.radians(normalized)
    return (math.cos(radians), math.sin(radians))


def _signed_v_kind(raw: object) -> SignedVKind:
    if isinstance(raw, str):
        try:
            return SignedVKind(raw)
        except ValueError as exc:
            raise ValueError(
                f"unknown universal_junction offset_v value: {raw!r}"
            ) from exc
    raise ValueError(
        "universal_junction branches must declare offset_v as a string "
        "(edge_left | edge_right | align_center)"
    )


def _signed_v_value(kind: SignedVKind, width: float, clearance: float) -> float:
    if kind is SignedVKind.ALIGN_CENTER:
        return 0.0
    side = width / 2.0 + clearance
    return side if kind is SignedVKind.EDGE_LEFT else -side


def _resolve_clearance(layout: V33Layout) -> float:
    routing = layout.global_constraints.routing
    if routing is not None and routing.default_clearance is not None:
        return float(routing.default_clearance)
    return 0.2


def _branch_target_endpoint(node_id: str, edge: Edge) -> str:
    """Return the endpoint of `edge` that is *not* `node_id`.

    universal_junction branches connect the junction node to some far endpoint
    (a t_junction, terminal, etc.). The far endpoint is the one that is not the
    junction node itself.
    """

    candidates = [endpoint for endpoint in edge.connections if endpoint != node_id]
    if not candidates:
        raise ValueError(
            f"branch edge {edge!r} has no endpoint distinct from node {node_id!r}"
        )
    return candidates[0]


def _branch_edge_width(edge: Edge | None, layout: V33Layout) -> float:
    if (
        edge is not None
        and edge.constraint is not None
        and edge.constraint.width is not None
    ):
        return float(edge.constraint.width)
    routing = layout.global_constraints.routing
    if routing is not None and routing.default_trace_width is not None:
        return float(routing.default_trace_width)
    return 0.5


def _expand_branches(
    node_id: str,
    node: Node,
    layout: V33Layout,
    clearance: float,
) -> tuple[BranchConstraint, ...]:
    rules = node.connection_rules
    if rules is None or not rules.branches:
        return ()
    out: list[BranchConstraint] = []
    for index, branch in enumerate(rules.branches):
        if branch.edge is None:
            raise ValueError(f"node {node_id!r} branch[{index}] missing edge id")
        edge = layout.edges.get(branch.edge)
        if edge is None:
            raise ValueError(
                f"node {node_id!r} branch[{index}] references unknown edge "
                f"{branch.edge!r}"
            )
        if branch.angle is None:
            raise ValueError(f"node {node_id!r} branch[{index}] missing angle")
        if branch.origin is None:
            raise ValueError(f"node {node_id!r} branch[{index}] missing origin")
        offset_u = float(branch.origin.offset_u or 0.0)
        kind = _signed_v_kind(branch.origin.offset_v)
        width = _branch_edge_width(edge, layout)
        signed_v = _signed_v_value(kind, width, clearance)
        cos_t, sin_t = _trig(float(branch.angle))
        dx = cos_t * offset_u - sin_t * signed_v
        dy = sin_t * offset_u + cos_t * signed_v
        target = _branch_target_endpoint(node_id, edge)
        out.append(
            BranchConstraint(
                branch_index=index,
                edge_id=branch.edge,
                target_endpoint=target,
                angle_deg=float(branch.angle),
                offset_u=offset_u,
                signed_v_kind=kind,
                signed_v=signed_v,
                dx=dx,
                dy=dy,
            )
        )
    return tuple(out)


def expand_universal_junctions(
    layout: V33Layout,
) -> dict[str, UniversalJunctionTemplate]:
    """Build BranchConstraint templates for every `universal_junction` node."""

    clearance = _resolve_clearance(layout)
    out: dict[str, UniversalJunctionTemplate] = {}
    for node_id, node in sorted(layout.nodes.items()):
        if node.type != "universal_junction":
            continue
        if (
            node.connection_rules is None
            or node.connection_rules.reference_edge is None
        ):
            raise ValueError(
                f"universal_junction node {node_id!r} missing connection_rules.reference_edge"
            )
        branches = _expand_branches(node_id, node, layout, clearance)
        out[node_id] = UniversalJunctionTemplate(
            node_id=node_id,
            reference_edge=node.connection_rules.reference_edge,
            branches=branches,
        )
    return out


__all__ = [
    "expand_universal_junctions",
]
