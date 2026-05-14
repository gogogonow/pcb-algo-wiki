"""M3 — UV component host_edge resolution + pin position expressions.

For each `parametric_uv` component, derives:

- which microstrip edge hosts the anchor pin (unique / ambiguous / missing);
- linear expressions that translate the four IR variables
  (anchor_x, anchor_y, rotation, offset_v_side) into per-pin (x, y) coordinates
  ready for OR-Tools.

The expressions are stored symbolically as `(coefficient, variable_name)` term
tuples plus a constant offset. M4 will resolve each `variable_name` to an
actual `cp_model.IntVar` at model-build time.

Per `ITERATION-PLAN.md` §M3 / §5.1 R2:
- `unique`: locked to the single matching edge, no host_edge BoolVar;
- `ambiguous`: candidate list emitted for M4 to introduce an ExactlyOne BoolVar;
- `missing`: synthetic anonymous host edge id assigned, lint warning recorded.
"""

from __future__ import annotations

from typing import Iterable

from frontend.models import ComponentExpansion, LintWarning
from schema.solver_ir import (
    ExpressionTerm,
    PinPositionExpr,
    PinPositionKind,
    RotationDomain,
    UvHostMatch,
    UvResolution,
)
from schema.v33 import Component, Edge, Footprint, V33Layout

DEFAULT_ROTATION_DOMAIN: tuple[int, ...] = (0, 90, 180, 270)
DEFAULT_OFFSET_V_SIDES: tuple[int, ...] = (-1, 1)


def _normal_to_edge(layout: V33Layout, edge_id: str) -> tuple[float, float]:
    """Approximate the unit normal to a host edge.

    Without the solved geometry we cannot know the true normal; M3 emits a
    placeholder (0.0, 1.0) for non-axis-aligned cases. M4 will substitute the
    OR-Tools-derived normal at model-build time. This keeps the symbolic
    expression structure correct: the offset term is `offset_v_side · (W/2 +
    clearance) · n̂`, so storing n̂ as a constant pair is sufficient for the
    expression spec.
    """

    del layout, edge_id
    # Symbolic placeholder: M4 substitutes the real normal once host edge
    # geometry is resolved during CP-SAT modelling.
    return (0.0, 1.0)


def _resolve_clearance(layout: V33Layout) -> float:
    routing = layout.global_constraints.routing
    if routing is not None and routing.default_clearance is not None:
        return float(routing.default_clearance)
    return 0.2


def _candidate_edges(
    layout: V33Layout, reference_net: str, anchor_endpoint: str
) -> list[str]:
    matches: list[str] = []
    for edge_id, edge in layout.edges.items():
        if edge.type != "microstrip":
            continue
        if edge.net != reference_net:
            continue
        if anchor_endpoint in edge.connections:
            matches.append(edge_id)
    return matches


def _host_width(edge: Edge | None, layout: V33Layout) -> float:
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


def _footprint_pin_locals(
    footprint: Footprint, anchor_pin: str
) -> tuple[dict[str, tuple[float, float]], tuple[float, float]]:
    locals_xy: dict[str, tuple[float, float]] = {}
    for pin_name, pin in footprint.pins.items():
        lx = float(pin.local_x or 0.0)
        ly = float(pin.local_y or 0.0)
        locals_xy[pin_name] = (lx, ly)
    if anchor_pin not in locals_xy:
        locals_xy[anchor_pin] = (0.0, 0.0)
    return locals_xy, locals_xy[anchor_pin]


def _anchor_pin_expr(
    component_id: str,
    anchor_pin: str,
    host_width: float,
    clearance: float,
    normal: tuple[float, float],
) -> PinPositionExpr:
    nx, ny = normal
    side_offset = host_width / 2.0 + clearance
    return PinPositionExpr(
        component=component_id,
        pin=anchor_pin,
        kind=PinPositionKind.ANCHOR,
        const_x=0.0,
        const_y=0.0,
        terms_x=(
            ExpressionTerm(coefficient=1.0, variable=f"{component_id}.anchor_x"),
            ExpressionTerm(
                coefficient=side_offset * nx,
                variable=f"{component_id}.offset_v_side",
            ),
        ),
        terms_y=(
            ExpressionTerm(coefficient=1.0, variable=f"{component_id}.anchor_y"),
            ExpressionTerm(
                coefficient=side_offset * ny,
                variable=f"{component_id}.offset_v_side",
            ),
        ),
    )


def _derived_pin_expr(
    component_id: str,
    pin_name: str,
    local_offset: tuple[float, float],
    anchor_local: tuple[float, float],
) -> PinPositionExpr:
    """Encodes pin_k_pos = anchor_pin_pos + R(rotation) · (local_k - local_anchor).

    rotation is an IntVar in {0, 90, 180, 270}; M3 emits a symbolic
    `R(rotation)` placeholder by storing the local offset as a constant and
    naming the rotation variable. M4 expands the rotation case-split into
    boolean-gated linear terms (one branch per allowed rotation value).
    """

    dx = local_offset[0] - anchor_local[0]
    dy = local_offset[1] - anchor_local[1]
    return PinPositionExpr(
        component=component_id,
        pin=pin_name,
        kind=PinPositionKind.DERIVED,
        const_x=dx,
        const_y=dy,
        terms_x=(
            ExpressionTerm(coefficient=1.0, variable=f"{component_id}.anchor_pin_x"),
            ExpressionTerm(
                coefficient=1.0, variable=f"{component_id}.rot_dx_{pin_name}"
            ),
        ),
        terms_y=(
            ExpressionTerm(coefficient=1.0, variable=f"{component_id}.anchor_pin_y"),
            ExpressionTerm(
                coefficient=1.0, variable=f"{component_id}.rot_dy_{pin_name}"
            ),
        ),
    )


def _build_pin_expressions(
    component_id: str,
    component: Component,
    footprint: Footprint,
    host_width: float,
    clearance: float,
    normal: tuple[float, float],
) -> tuple[PinPositionExpr, ...]:
    placement = component.placement
    if placement is None or placement.anchor_pin is None:
        raise ValueError(f"component {component_id} missing placement.anchor_pin")
    anchor_pin = placement.anchor_pin
    locals_xy, anchor_local = _footprint_pin_locals(footprint, anchor_pin)
    exprs: list[PinPositionExpr] = [
        _anchor_pin_expr(component_id, anchor_pin, host_width, clearance, normal)
    ]
    for pin_name in sorted(locals_xy):
        if pin_name == anchor_pin:
            continue
        exprs.append(
            _derived_pin_expr(
                component_id,
                pin_name,
                locals_xy[pin_name],
                anchor_local,
            )
        )
    return tuple(exprs)


def resolve_uv_components(
    layout: V33Layout,
    expansions: dict[str, ComponentExpansion],
    *,
    warnings: list[LintWarning] | None = None,
) -> dict[str, UvResolution]:
    """Resolve every `parametric_uv` component to a UvResolution.

    `warnings` (optional) is appended in-place when missing host edges trigger
    synthetic-host fallback. Pass `LintReport.warnings` (after copying to a
    list) to capture them in the SolverIR caller.
    """

    clearance = _resolve_clearance(layout)
    resolutions: dict[str, UvResolution] = {}

    for comp_id, expansion in sorted(expansions.items()):
        if expansion.placement_kind != "parametric_uv":
            continue
        component = layout.components.get(comp_id)
        if component is None or component.placement is None:
            continue
        placement = component.placement
        anchor_pin = placement.anchor_pin or "PIN_1"
        reference_net = placement.reference_net or ""
        anchor_endpoint = f"{comp_id}.{anchor_pin}"
        candidates = _candidate_edges(layout, reference_net, anchor_endpoint)

        synthetic = False
        if len(candidates) == 1:
            status = UvHostMatch.UNIQUE
            host_edge_id = candidates[0]
        elif len(candidates) >= 2:
            status = UvHostMatch.AMBIGUOUS
            host_edge_id = candidates[0]
        else:
            status = UvHostMatch.MISSING
            host_edge_id = f"synthetic_host__{comp_id}"
            synthetic = True
            if warnings is not None:
                warnings.append(
                    LintWarning(
                        field_path=f"components.{comp_id}.placement.reference_net",
                        code="uv_host_missing",
                        message=(
                            f"no microstrip edge on net {reference_net!r} contains "
                            f"endpoint {anchor_endpoint!r}; synthesizing host "
                            f"{host_edge_id!r}"
                        ),
                    )
                )

        host_edge_obj: Edge | None = None
        if not synthetic:
            host_edge_obj = layout.edges.get(host_edge_id)
        host_width = _host_width(host_edge_obj, layout)
        normal = _normal_to_edge(layout, host_edge_id)

        footprint_ref = component.footprint_ref or ""
        footprint = layout.footprints.get(footprint_ref)
        if footprint is None:
            footprint = Footprint()

        pin_exprs = _build_pin_expressions(
            comp_id,
            component,
            footprint,
            host_width=host_width,
            clearance=clearance,
            normal=normal,
        )

        resolutions[comp_id] = UvResolution(
            component=comp_id,
            anchor_pin=anchor_pin,
            reference_net=reference_net,
            host_match_status=status,
            host_edge_id=host_edge_id,
            host_candidates=tuple(candidates) if candidates else (host_edge_id,),
            rotation_domain=RotationDomain(values=DEFAULT_ROTATION_DOMAIN),
            offset_v_sides=DEFAULT_OFFSET_V_SIDES,
            host_width=host_width,
            clearance=clearance,
            pin_position_exprs=pin_exprs,
            synthetic_host=synthetic,
        )

    return resolutions


def host_match_histogram(
    resolutions: Iterable[UvResolution],
) -> dict[str, int]:
    hist: dict[str, int] = {member.value: 0 for member in UvHostMatch}
    for res in resolutions:
        hist[res.host_match_status.value] += 1
    return hist


# math import is reserved for future bend-style projections.


__all__ = [
    "DEFAULT_OFFSET_V_SIDES",
    "DEFAULT_ROTATION_DOMAIN",
    "host_match_histogram",
    "resolve_uv_components",
]
