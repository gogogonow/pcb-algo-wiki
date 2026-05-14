"""Extract solved geometry from a :class:`CpsatModel` + solver pair.

Bridges the µm-discretised CP-SAT solution back to floating-point mm in the
:class:`GeometryIR` schema.
"""

from __future__ import annotations

from frontend.models import FrontendArtifact
from schema.geometry_ir import (
    ComponentPlacement,
    GeometryIR,
    PinPlacement,
    RoutePolyline,
)
from schema.solver_ir import SolverIR
from schema.v6_ir import Point

from .cpsat import CpsatModel, SolveResult
from .units import um_to_mm


def _value(solver_value: int | object) -> int:
    if isinstance(solver_value, int):
        return solver_value
    return int(solver_value)  # pragma: no cover - defensive


def _resolve_xy(
    cpsat: CpsatModel, endpoint_name: str, solver_get
) -> tuple[float, float]:
    handle = cpsat.endpoints[endpoint_name]
    x = handle.x_expr()
    y = handle.y_expr()
    xv = x if isinstance(x, int) else solver_get(x)
    yv = y if isinstance(y, int) else solver_get(y)
    return um_to_mm(int(xv)), um_to_mm(int(yv))


def extract_geometry(
    *,
    ir: SolverIR,
    artifact: FrontendArtifact,
    cpsat: CpsatModel,
    result: SolveResult,
) -> GeometryIR:
    """Build the strict :class:`GeometryIR` from solver results.

    For ``INFEASIBLE`` / ``UNKNOWN`` results we still return a valid IR so the
    caller can persist the failure metadata; placements/routes are populated
    on a best-effort basis (empty when the solver has no values).
    """

    placements: dict[str, ComponentPlacement] = {}
    routes: dict[str, RoutePolyline] = {}
    nodes: dict[str, Point] = {}

    has_solution = result.status_name in ("OPTIMAL", "FEASIBLE")
    solver_get = result.solver.Value if has_solution else (lambda v: 0)

    if has_solution:
        # Component placements (UV components only — fixed components are
        # already known from the artifact).
        for comp_id, comp_exp in artifact.components.items():
            if comp_exp.placement_kind == "fixed":
                pads = []
                for pad in comp_exp.pads:
                    if pad.abs_x is None or pad.abs_y is None:
                        continue
                    pads.append(
                        PinPlacement(
                            pin=pad.pin,
                            point=Point(x=float(pad.abs_x), y=float(pad.abs_y)),
                        )
                    )
                if not pads:
                    continue
                anchor = pads[0].point
                placements[comp_id] = ComponentPlacement(
                    component=comp_id,
                    anchor=anchor,
                    rotation_deg=float(comp_exp.pads[0].orientation or 0.0),
                    pads=tuple(pads),
                )

        for comp_id, uv_exp in artifact.uv_components.items():
            anchor_var = cpsat.component_anchor.get(comp_id)
            if anchor_var is None:
                continue
            ax = um_to_mm(int(solver_get(anchor_var[0])))
            ay = um_to_mm(int(solver_get(anchor_var[1])))
            pin_pads: list[PinPlacement] = []
            for pad in uv_exp.pads:
                ep_name = f"{comp_id}.{pad.pin}"
                if ep_name not in cpsat.endpoints:
                    continue
                px, py = _resolve_xy(cpsat, ep_name, solver_get)
                pin_pads.append(PinPlacement(pin=pad.pin, point=Point(x=px, y=py)))
            placements[comp_id] = ComponentPlacement(
                component=comp_id,
                anchor=Point(x=ax, y=ay),
                rotation_deg=0.0,
                pads=tuple(pin_pads),
            )

        # Route polylines: M4 emits straight (start, end) — bend modelling is M6.
        for edge_id, edge in ir.edges.items():
            ep_a, ep_b = cpsat.edge_endpoint_resolved.get(edge_id, edge.endpoints)
            try:
                ax, ay = _resolve_xy(cpsat, ep_a, solver_get)
                bx, by = _resolve_xy(cpsat, ep_b, solver_get)
            except KeyError as exc:
                import warnings

                warnings.warn(
                    f"extract_geometry: endpoint {exc} not in cpsat.endpoints for "
                    f"edge {edge_id!r}; using (0,0) placeholder — UV resolution "
                    "may be incomplete.",
                    stacklevel=2,
                )
                ax = ay = bx = by = 0.0
            width_mm = (
                float(edge.width)
                if edge.width is not None
                else um_to_mm(cpsat.width_um_by_edge[edge_id])
            )
            routes[edge_id] = RoutePolyline(
                edge_id=edge_id,
                routing_class=edge.routing_class,
                width=width_mm,
                points=(Point(x=ax, y=ay), Point(x=bx, y=by)),
            )

        # Junction node centres + free t-junction nodes.
        for node_id, vars_xy in cpsat.junction_centres.items():
            cx = um_to_mm(int(solver_get(vars_xy[0])))
            cy = um_to_mm(int(solver_get(vars_xy[1])))
            nodes[node_id] = Point(x=cx, y=cy)

        # Also expose every endpoint that isn't already a placement pad / fixed
        # terminal so SVG / regression can plot t-junction nodes.
        seen = set(nodes) | set(ir.terminals)
        for ep_name, handle in cpsat.endpoints.items():
            if ep_name in seen or "." in ep_name:
                continue
            ex, ey = _resolve_xy(cpsat, ep_name, solver_get)
            nodes[ep_name] = Point(x=ex, y=ey)

    return GeometryIR(
        project=ir.project,
        board=ir.board,
        placements=placements,
        routes=routes,
        nodes=nodes,
        solve_status=result.status_name,
        solve_wall_seconds=float(result.wall_seconds),
        objective_value=result.objective,
    )


__all__ = ["extract_geometry"]
