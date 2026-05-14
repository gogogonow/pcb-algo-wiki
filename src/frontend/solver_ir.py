"""M3 SolverIR orchestration.

Combines the M2 FrontendArtifact with M3 UV resolution and universal_junction
template expansion to produce a strict `SolverIR` ready for the CP-SAT model
builder (M4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from schema.solver_ir import (
    Board,
    SolverEdge,
    SolverIR,
    UvHostMatch,
)
from schema.v33 import V33Layout
from schema.v6_ir import Point, RoutingClass, V6Terminal

from .compile import compile_layout
from .lint import lint_layout
from .models import LintWarning
from .universal_junction import expand_universal_junctions
from .uv_resolver import resolve_uv_components

_DEFAULT_BOARD_WIDTH = 100.0
_DEFAULT_BOARD_HEIGHT = 100.0
_DEFAULT_CLEARANCE = 0.2


def _board_from_layout(layout: V33Layout) -> Board:
    outline = layout.global_constraints.board_outline
    width = float(
        outline.width if outline and outline.width is not None else _DEFAULT_BOARD_WIDTH
    )
    height = float(
        outline.height
        if outline and outline.height is not None
        else _DEFAULT_BOARD_HEIGHT
    )
    if (
        outline
        and outline.origin
        and outline.origin.x is not None
        and outline.origin.y is not None
    ):
        origin = Point(x=float(outline.origin.x), y=float(outline.origin.y))
    else:
        origin = Point(x=0.0, y=0.0)
    return Board(origin=origin, width=width, height=height)


def _clearance_from_layout(layout: V33Layout) -> float:
    routing = layout.global_constraints.routing
    if routing is not None and routing.default_clearance is not None:
        return float(routing.default_clearance)
    return _DEFAULT_CLEARANCE


def _routing_class_from_string(raw: str | None) -> RoutingClass:
    if raw is None:
        return RoutingClass.RF_CONSTRAINED_FREE
    if raw == "rf_constrained":
        return RoutingClass.RF_CONSTRAINED_LOCKED
    try:
        return RoutingClass(raw)
    except ValueError:
        return RoutingClass.RF_CONSTRAINED_FREE


def _solver_edges(layout: V33Layout) -> dict[str, SolverEdge]:
    out: dict[str, SolverEdge] = {}
    for edge_id, edge in layout.edges.items():
        endpoints = tuple(edge.connections[:2]) if len(edge.connections) >= 2 else None
        if endpoints is None:
            continue
        target_length = (
            float(edge.constraint.target_length)
            if edge.constraint and edge.constraint.target_length is not None
            else None
        )
        width = (
            float(edge.constraint.width)
            if edge.constraint and edge.constraint.width is not None
            else None
        )
        if edge.type == "microstrip":
            routing_cls = (
                RoutingClass.RF_CONSTRAINED_LOCKED
                if target_length is not None
                else RoutingClass.RF_CONSTRAINED_FREE
            )
        elif edge.type == "trace":
            routing_cls = RoutingClass.FLEXIBLE_PATH
            target_length = None
        else:
            routing_cls = _routing_class_from_string(edge.routing_class)
            if routing_cls is not RoutingClass.RF_CONSTRAINED_LOCKED:
                target_length = None
        out[edge_id] = SolverEdge(
            endpoints=(endpoints[0], endpoints[1]),
            routing_class=routing_cls,
            net=edge.net,
            target_length=target_length,
            width=width,
            bend_style=edge.geometry.bend_style if edge.geometry else None,
            launch_rule=edge.geometry.launch_rule if edge.geometry else None,
        )
    return out


def _fixed_terminals(
    layout: V33Layout, fixed_pads: dict[str, Any]
) -> dict[str, V6Terminal]:
    """Project FrontendArtifact.fixed_terminals into the strict V6Terminal map.

    M4 expects every fixed pad (testpoints + IC fixed-component pins) to be
    available with real coordinates. The v3.3 ``terminals`` section only lists
    net stubs (e.g. testpoints, GND), so we union those keys with every
    ``ExpandedPad`` from the M2 expansion that has a resolved ``abs_x/abs_y``.
    GND-style terminals without coordinates are skipped to keep the schema
    invariant (Point requires finite floats).
    """

    out: dict[str, V6Terminal] = {}
    for pad_id, pad in fixed_pads.items():
        x = getattr(pad, "abs_x", None)
        y = getattr(pad, "abs_y", None)
        if x is None or y is None:
            continue
        out[pad_id] = V6Terminal(point=Point(x=float(x), y=float(y)))
    # Preserve YAML-declared net-stub terminals without coordinates as a
    # zero-anchored entry only when the M2 expansion did not already supply
    # one. This keeps backwards-compatibility for callers that iterate the
    # ``terminals`` section verbatim (e.g. visualisation regression tests).
    for term_id in layout.terminals:
        if term_id in out:
            continue
        out[term_id] = V6Terminal(point=Point(x=0.0, y=0.0))
    return out


def compile_solver_ir(path: str | Path) -> SolverIR:
    """Run the full M2 + M3 pipeline on a v3.3 YAML file and return SolverIR."""

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    repaired, _ = lint_layout(raw)
    layout = V33Layout.model_validate(repaired)
    artifact = compile_layout(path)

    extra_warnings: list[LintWarning] = []
    uv_resolutions = resolve_uv_components(
        layout, artifact.components, warnings=extra_warnings
    )
    junction_templates = expand_universal_junctions(layout)

    edges = _solver_edges(layout)
    project = artifact.project_name or "unnamed"

    return SolverIR(
        project=project,
        board=_board_from_layout(layout),
        clearance=_clearance_from_layout(layout),
        terminals=_fixed_terminals(layout, artifact.fixed_terminals),
        edges=edges,
        uv_resolutions=uv_resolutions,
        junction_templates=junction_templates,
    )


def solver_ir_to_dict(ir: SolverIR) -> dict[str, Any]:
    """Serialize SolverIR to a JSON-safe nested dict."""

    return _model_to_dict(ir)


def _model_to_dict(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _model_to_dict(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(k): _model_to_dict(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_model_to_dict(v) for v in value]
    return value


def host_match_summary(ir: SolverIR) -> dict[str, int]:
    summary = {member.value: 0 for member in UvHostMatch}
    for res in ir.uv_resolutions.values():
        summary[res.host_match_status.value] += 1
    return summary


__all__ = [
    "compile_solver_ir",
    "host_match_summary",
    "solver_ir_to_dict",
]
