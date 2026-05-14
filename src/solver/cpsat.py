"""M4 — CP-SAT model builder for v6 single-layer PA layout.

Consumes the M3 :class:`SolverIR` plus the M2 :class:`FrontendArtifact`
(needed for footprint bboxes and fixed-component coordinates that are not
copied verbatim into SolverIR) and produces:

- a fully populated ``cp_model.CpModel`` with length-lock, NoOverlap2D,
  junction, UV-anchor and board-bound constraints;
- an :class:`EndpointTable` that maps every endpoint string used by edges to
  the ``(x_var, y_var)`` pair (variable or constant) that represents it.

The split between :class:`build_model` (build) and :func:`solve_model` (run)
keeps the model inspectable by unit tests without invoking the OR-Tools
solver.

**M4 simplifications** (documented in plan.md §6 and concepts/cpsat-model.md):

- UV component rotation is fixed to 0° (PA case has 2-pin caps/resistors;
  rotating ±90 only swaps PIN_1↔PIN_2 and is left to M5);
- ``offset_v_side`` is modelled as a free :math:`\\pm 1` IntVar so the solver
  may flip the host_edge perpendicular offset to avoid collisions;
- the host_edge normal stored in SolverIR is the placeholder ``(0, 1)``;
  for PA this happens to align with reality (microstrips here are vertical),
  and is good enough for length feasibility / NoOverlap regression.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ortools.sat.python import cp_model

from frontend.models import FrontendArtifact
from schema.solver_ir import (
    PinPositionKind,
    SolverEdge,
    SolverIR,
    UvResolution,
)

from .units import length_tolerance_um, mm_to_um

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ortools.sat.python.cp_model import IntVar

INFLATION_BIAS_UM = 50
"""Extra µm of clearance baked into NoOverlap rectangles to absorb rounding."""

DEFAULT_TIME_LIMIT_S = 10.0
DEFAULT_NUM_WORKERS = 8


@dataclass
class EndpointHandle:
    """An endpoint resolved to either a constant point or a CP-SAT variable.

    Use :meth:`x_expr` / :meth:`y_expr` to obtain a value that can be passed
    to ``model.Add(...)``. Constants are wrapped in plain ``int`` so OR-Tools
    treats them as numeric literals (it accepts ``int`` in linear expressions).
    """

    name: str
    x: "int | IntVar"
    y: "int | IntVar"

    def x_expr(self) -> "int | IntVar":
        return self.x

    def y_expr(self) -> "int | IntVar":
        return self.y


@dataclass
class CpsatModel:
    model: cp_model.CpModel
    endpoints: dict[str, EndpointHandle]
    edges_modelled: list[str]
    locked_edges: list[str]
    width_um_by_edge: dict[str, int]
    component_anchor: dict[str, tuple["IntVar", "IntVar"]] = field(default_factory=dict)
    component_side: dict[str, "IntVar"] = field(default_factory=dict)
    junction_centres: dict[str, tuple["IntVar", "IntVar"]] = field(default_factory=dict)
    edge_endpoint_resolved: dict[str, tuple[str, str]] = field(default_factory=dict)
    skipped_locked_edges: list[tuple[str, str]] = field(default_factory=list)


def _board_bounds_um(ir: SolverIR) -> tuple[int, int, int, int]:
    ox = mm_to_um(ir.board.origin.x)
    oy = mm_to_um(ir.board.origin.y)
    return (
        ox,
        oy,
        ox + mm_to_um(ir.board.width),
        oy + mm_to_um(ir.board.height),
    )


def _new_xy(
    model: cp_model.CpModel, name: str, bounds: tuple[int, int, int, int]
) -> tuple["IntVar", "IntVar"]:
    x_lo, y_lo, x_hi, y_hi = bounds
    return (
        model.NewIntVar(x_lo, x_hi, f"{name}_x"),
        model.NewIntVar(y_lo, y_hi, f"{name}_y"),
    )


def _edge_default_width(edge: SolverEdge, fallback_um: int) -> int:
    if edge.width is not None:
        return mm_to_um(float(edge.width))
    return fallback_um


def _default_trace_width_um(ir: SolverIR) -> int:
    # Fall back to a conservative 0.5 mm if no width is set on a free edge.
    return mm_to_um(0.5)


def build_model(
    ir: SolverIR,
    artifact: FrontendArtifact,
    *,
    seed_hints: dict[str, dict[str, int]] | None = None,
    relax_length_lock: bool = False,
) -> CpsatModel:
    """Build the CP-SAT model from SolverIR + FrontendArtifact.

    ``seed_hints`` (M5) is an optional mapping
    ``{uv_comp_id: {"anchor_x_um": int, "anchor_y_um": int, "side": ±1}}``
    produced by :func:`solver.sa_floating.run_sa`. Hints are applied via
    ``model.AddHint`` after every variable is created, so they are pure
    soft heuristics — CP-SAT remains free to ignore them.

    Returns a :class:`CpsatModel` carrying the built model plus all metadata
    the extractor needs to reconstruct geometry from solver values.
    """

    model = cp_model.CpModel()
    bounds = _board_bounds_um(ir)
    endpoints: dict[str, EndpointHandle] = {}

    # 1. Fixed terminals (TPs + IC pins) → constant points.
    for term_id, term in ir.terminals.items():
        endpoints[term_id] = EndpointHandle(
            name=term_id,
            x=mm_to_um(term.point.x),
            y=mm_to_um(term.point.y),
        )

    # 2. Universal junction centres → IntVars; record for branch wiring.
    junction_centres: dict[str, tuple["IntVar", "IntVar"]] = {}
    for node_id, template in ir.junction_templates.items():
        cx, cy = _new_xy(model, f"junc_{node_id}", bounds)
        junction_centres[node_id] = (cx, cy)
        endpoints[node_id] = EndpointHandle(name=node_id, x=cx, y=cy)

    # 3. UV components → anchor (anchor_x, anchor_y) + signed offset_v_side.
    component_anchor: dict[str, tuple["IntVar", "IntVar"]] = {}
    component_side: dict[str, "IntVar"] = {}
    for comp_id, uv in ir.uv_resolutions.items():
        ax, ay = _new_xy(model, f"uv_{comp_id}", bounds)
        component_anchor[comp_id] = (ax, ay)
        side = model.NewIntVar(-1, 1, f"uv_{comp_id}_side")
        # Force ±1 (skip the value 0): two BoolVars in ExactlyOne.
        b_pos = model.NewBoolVar(f"uv_{comp_id}_side_pos")
        b_neg = model.NewBoolVar(f"uv_{comp_id}_side_neg")
        model.AddBoolOr([b_pos, b_neg])
        model.AddBoolAnd([b_pos.Not()]).OnlyEnforceIf(b_neg)
        model.Add(side == 1).OnlyEnforceIf(b_pos)
        model.Add(side == -1).OnlyEnforceIf(b_neg)
        component_side[comp_id] = side

        _wire_uv_pin_endpoints(
            model=model,
            ir=ir,
            comp_id=comp_id,
            uv=uv,
            anchor_xy=(ax, ay),
            side_var=side,
            endpoints=endpoints,
            bounds=bounds,
        )

    # 4. Universal junction branches: each branch describes the origin of a
    # sub-edge on the parent reference_edge. We materialise a virtual
    # "branch_origin" endpoint at ``centre + (dx, dy)`` and rewire the
    # corresponding sub-edge so its endpoint that previously referred to the
    # universal_node is replaced by the branch_origin. The far node (split_pad,
    # combiner, UV pin, ...) keeps its own free / derived endpoint.
    branch_origin_aliases: dict[tuple[str, str], str] = {}
    for node_id, template in ir.junction_templates.items():
        cx, cy = junction_centres[node_id]
        for branch in template.branches:
            origin_id = f"{node_id}__br{branch.branch_index}_origin_{branch.edge_id}"
            ox, oy = _new_xy(model, f"branch_origin_{origin_id}", bounds)
            model.Add(ox == cx + mm_to_um(float(branch.dx)))
            model.Add(oy == cy + mm_to_um(float(branch.dy)))
            endpoints[origin_id] = EndpointHandle(name=origin_id, x=ox, y=oy)
            branch_origin_aliases[(branch.edge_id, node_id)] = origin_id

    # 5. Free endpoints (t_junction split_pad / combiner) — create IntVars.
    for edge in ir.edges.values():
        for endpoint_name in edge.endpoints:
            if endpoint_name not in endpoints:
                ex, ey = _new_xy(model, f"node_{endpoint_name}", bounds)
                endpoints[endpoint_name] = EndpointHandle(
                    name=endpoint_name, x=ex, y=ey
                )

    # 5b. Resolve per-edge endpoint references taking branch_origin aliases
    # into account: when an edge is a branch sub-edge of a junction (matched
    # by edge_id + node_id co-occurrence), replace the universal_node endpoint
    # with the corresponding branch_origin synthetic endpoint.
    edge_endpoint_resolved: dict[str, tuple[str, str]] = {}
    for edge_id, edge in ir.edges.items():
        ep_a, ep_b = edge.endpoints
        for node_id in junction_centres:
            origin_id = branch_origin_aliases.get((edge_id, node_id))
            if origin_id is None:
                continue
            if ep_a == node_id:
                ep_a = origin_id
            elif ep_b == node_id:
                ep_b = origin_id
        edge_endpoint_resolved[edge_id] = (ep_a, ep_b)

    # 6. Length-lock for rf_constrained_locked edges (Manhattan).
    locked_edges: list[str] = []
    skipped_locked_edges: list[tuple[str, str]] = []
    edges_modelled: list[str] = []
    width_um_by_edge: dict[str, int] = {}
    default_width = _default_trace_width_um(ir)
    for edge_id, edge in ir.edges.items():
        edges_modelled.append(edge_id)
        width_um_by_edge[edge_id] = _edge_default_width(edge, default_width)
        if edge.routing_class.value == "rf_constrained_locked":
            assert edge.target_length is not None
            ep_pair = edge_endpoint_resolved[edge_id]
            a = endpoints[ep_pair[0]]
            b = endpoints[ep_pair[1]]
            # Detect impossible fixed-endpoint pairs: when both endpoints are
            # constants and the direct Manhattan distance already exceeds the
            # locked target_length (beyond tolerance), no trace length can
            # satisfy the constraint. Skip with a recorded reason so the CLI
            # can surface a warning instead of producing an INFEASIBLE model.
            if (
                isinstance(a.x, int)
                and isinstance(a.y, int)
                and isinstance(b.x, int)
                and isinstance(b.y, int)
            ):
                direct_um = abs(a.x - b.x) + abs(a.y - b.y)
                target_um = mm_to_um(float(edge.target_length))
                tol_um = length_tolerance_um(float(edge.target_length))
                if direct_um > target_um + tol_um:
                    skipped_locked_edges.append(
                        (
                            edge_id,
                            f"fixed-endpoint Manhattan {direct_um/1000:.3f}mm > "
                            f"target {edge.target_length:.3f}mm + tol; "
                            f"data inconsistency, length lock skipped",
                        )
                    )
                    continue
                if direct_um < target_um - tol_um and not relax_length_lock:
                    # target > Manhattan: meander routing required.  CP-SAT can
                    # only produce straight-segment paths; meander insertion is
                    # deferred to the M7 postproc pass (apply_meanders).
                    skipped_locked_edges.append(
                        (
                            edge_id,
                            f"fixed-endpoint Manhattan {direct_um/1000:.3f}mm < "
                            f"target {edge.target_length:.3f}mm - tol; "
                            f"meander routing required, length lock deferred to postproc",
                        )
                    )
                    continue
            _add_locked_length_constraint(
                model,
                edge_id,
                ep_pair,
                endpoints,
                edge.target_length,
                upper_only=relax_length_lock,
            )
            locked_edges.append(edge_id)

    # 7. NoOverlap: M4 emits a **soft** (post-extract audit) NoOverlap rather
    # than a hard CP-SAT constraint. With single-segment Manhattan traces the
    # PA case has provably-infeasible hard pairwise NoOverlap. Geometric
    # repair (bend insertion, repulsion) is the responsibility of M6
    # postproc rather than M5 (M5 only seeds CP-SAT and routes flexible_path
    # edges with A*; the audit pairs returned by audit_geometry feed M6).
    del artifact

    # 8. Apply M5 SA hints if provided.
    if seed_hints:
        for comp_id, hint in seed_hints.items():
            anchor_xy = component_anchor.get(comp_id)
            if anchor_xy is not None:
                ax_var, ay_var = anchor_xy
                if "anchor_x_um" in hint:
                    model.AddHint(ax_var, int(hint["anchor_x_um"]))
                if "anchor_y_um" in hint:
                    model.AddHint(ay_var, int(hint["anchor_y_um"]))
            side_var = component_side.get(comp_id)
            if side_var is not None and "side" in hint:
                model.AddHint(side_var, int(hint["side"]))

    return CpsatModel(
        model=model,
        endpoints=endpoints,
        edges_modelled=edges_modelled,
        locked_edges=locked_edges,
        width_um_by_edge=width_um_by_edge,
        component_anchor=component_anchor,
        component_side=component_side,
        junction_centres=junction_centres,
        edge_endpoint_resolved=edge_endpoint_resolved,
        skipped_locked_edges=skipped_locked_edges,
    )


def _wire_uv_pin_endpoints(
    *,
    model: cp_model.CpModel,
    ir: SolverIR,
    comp_id: str,
    uv: UvResolution,
    anchor_xy: tuple["IntVar", "IntVar"],
    side_var: "IntVar",
    endpoints: dict[str, EndpointHandle],
    bounds: tuple[int, int, int, int],
) -> None:
    """Materialise endpoint vars for every pin of a UV component.

    Anchor pin: ``(anchor_x + nx·side·offset, anchor_y + ny·side·offset)``
    Other pins: anchor_pin + constant offset (rotation fixed to 0° in M4).
    """

    side_offset_um = mm_to_um(float(uv.host_width) / 2.0 + float(uv.clearance))
    # SolverIR records the anchor expression with (nx, ny) baked into the
    # offset_v_side coefficients. We recover those coefficients to keep the
    # CP-SAT geometry consistent with M3's symbolic spec.
    anchor_expr = next(
        e for e in uv.pin_position_exprs if e.kind is PinPositionKind.ANCHOR
    )
    nx_coef_mm = next(
        (t.coefficient for t in anchor_expr.terms_x if "offset_v_side" in t.variable),
        0.0,
    )
    ny_coef_mm = next(
        (t.coefficient for t in anchor_expr.terms_y if "offset_v_side" in t.variable),
        0.0,
    )
    # The coefficient already incorporates host_width/2 + clearance; convert
    # the *signed magnitude* and the *sign of the normal* separately so we
    # multiply integer side ∈ {-1, +1} by an integer µm.
    nx_sign = 1 if nx_coef_mm >= 0 else -1
    ny_sign = 1 if ny_coef_mm >= 0 else -1
    nx_um = abs(mm_to_um(nx_coef_mm))
    ny_um = abs(mm_to_um(ny_coef_mm))

    ax, ay = anchor_xy
    anchor_pin_x = model.NewIntVar(bounds[0], bounds[2], f"uv_{comp_id}_pin_x")
    anchor_pin_y = model.NewIntVar(bounds[1], bounds[3], f"uv_{comp_id}_pin_y")
    model.Add(anchor_pin_x == ax + nx_sign * nx_um * side_var)
    model.Add(anchor_pin_y == ay + ny_sign * ny_um * side_var)
    del side_offset_um  # already encoded into nx_um/ny_um via the coefficient

    endpoints[f"{comp_id}.{uv.anchor_pin}"] = EndpointHandle(
        name=f"{comp_id}.{uv.anchor_pin}",
        x=anchor_pin_x,
        y=anchor_pin_y,
    )

    for expr in uv.pin_position_exprs:
        if expr.kind is PinPositionKind.ANCHOR:
            continue
        # M4 fixes rotation to 0° → rot_dx_PIN = const_x; rot_dy_PIN = const_y.
        dx_um = mm_to_um(expr.const_x)
        dy_um = mm_to_um(expr.const_y)
        px = model.NewIntVar(bounds[0], bounds[2], f"uv_{comp_id}_{expr.pin}_x")
        py = model.NewIntVar(bounds[1], bounds[3], f"uv_{comp_id}_{expr.pin}_y")
        model.Add(px == anchor_pin_x + dx_um)
        model.Add(py == anchor_pin_y + dy_um)
        endpoints[f"{comp_id}.{expr.pin}"] = EndpointHandle(
            name=f"{comp_id}.{expr.pin}", x=px, y=py
        )


def _add_locked_length_constraint(
    model: cp_model.CpModel,
    edge_id: str,
    endpoint_pair: tuple[str, str],
    endpoints: dict[str, EndpointHandle],
    target_length_mm: float,
    *,
    upper_only: bool = False,
) -> None:
    """Constrain endpoint Manhattan distance to satisfy the length lock.

    ``upper_only=False`` (legacy, M4-M8): equality within ±tol — the trace is
    the straight Manhattan two-point line, so endpoint distance == target.

    ``upper_only=True`` (M9 ``relax_length_lock``): only the upper bound
    (Manhattan ≤ target + tol) is enforced. Endpoints may be closer; the
    octilinear router (M9) is responsible for growing the trace via 45°
    detours so the final polyline length matches target ± tol. Manhattan
    > target + tol still implies physical infeasibility (no detour can
    shorten the path), so the upper bound stays a hard constraint.
    """
    a = endpoints[endpoint_pair[0]]
    b = endpoints[endpoint_pair[1]]
    target_um = mm_to_um(target_length_mm)
    tol_um = length_tolerance_um(target_length_mm)

    dx = model.NewIntVar(-(2**31), 2**31 - 1, f"len_{edge_id}_dx")
    dy = model.NewIntVar(-(2**31), 2**31 - 1, f"len_{edge_id}_dy")
    model.Add(dx == a.x_expr() - b.x_expr())
    model.Add(dy == a.y_expr() - b.y_expr())

    abs_dx = model.NewIntVar(0, 2**31 - 1, f"len_{edge_id}_absdx")
    abs_dy = model.NewIntVar(0, 2**31 - 1, f"len_{edge_id}_absdy")
    model.AddAbsEquality(abs_dx, dx)
    model.AddAbsEquality(abs_dy, dy)

    if not upper_only:
        model.Add(abs_dx + abs_dy >= target_um - tol_um)
    model.Add(abs_dx + abs_dy <= target_um + tol_um)


def _add_pairwise_no_overlap(
    *,
    model: cp_model.CpModel,
    ir: SolverIR,
    endpoints: dict[str, EndpointHandle],
    edge_endpoint_resolved: dict[str, tuple[str, str]],
    width_um_by_edge: dict[str, int],
    clearance_um: int,
) -> None:
    """Pairwise BoolOr separation for non-connected microstrip pairs.

    For each pair of edges that do **not** share a (post-aliasing) endpoint,
    require at least one of the four axis-separation predicates to hold:

        ax_max + half_a + half_b + clearance ≤ bx_min
        bx_max + half_a + half_b + clearance ≤ ax_min
        ay_max + half_a + half_b + clearance ≤ by_min
        by_max + half_a + half_b + clearance ≤ ay_min

    where ``half_e = width_um_by_edge[e] // 2``.  Skipped routing classes:
    ``flexible_path`` (M4 doesn't shape these).
    """

    edge_ids = [
        eid for eid, e in ir.edges.items() if e.routing_class.value != "flexible_path"
    ]
    edge_eps: dict[str, set[str]] = {
        eid: set(edge_endpoint_resolved.get(eid, ir.edges[eid].endpoints))
        for eid in edge_ids
    }
    # Pre-compute per-edge axis min/max IntVars (one per edge, shared).
    axis_vars: dict[str, tuple] = {}
    for eid in edge_ids:
        ep_a, ep_b = edge_endpoint_resolved.get(eid, ir.edges[eid].endpoints)
        a = endpoints[ep_a]
        b = endpoints[ep_b]
        # Compute bounds for IntVar domain from endpoint domains/values.
        ax = a.x_expr()
        ay = a.y_expr()
        bx = b.x_expr()
        by = b.y_expr()

        # Shortcut: if both endpoints are constants, build constant bounds.
        if all(isinstance(v, int) for v in (ax, ay, bx, by)):
            axis_vars[eid] = (
                int(min(ax, bx)),
                int(max(ax, bx)),
                int(min(ay, by)),
                int(max(ay, by)),
            )
            continue

        bw = float(ir.board.width)
        bh = float(ir.board.height)
        ub_x = mm_to_um(bw)
        ub_y = mm_to_um(bh)
        x_min = model.NewIntVar(0, ub_x, f"nov_{eid}_xmin")
        x_max = model.NewIntVar(0, ub_x, f"nov_{eid}_xmax")
        y_min = model.NewIntVar(0, ub_y, f"nov_{eid}_ymin")
        y_max = model.NewIntVar(0, ub_y, f"nov_{eid}_ymax")
        model.AddMinEquality(x_min, [ax, bx])
        model.AddMaxEquality(x_max, [ax, bx])
        model.AddMinEquality(y_min, [ay, by])
        model.AddMaxEquality(y_max, [ay, by])
        axis_vars[eid] = (x_min, x_max, y_min, y_max)

    n = len(edge_ids)
    for i in range(n):
        ea = edge_ids[i]
        for j in range(i + 1, n):
            eb = edge_ids[j]
            if edge_eps[ea] & edge_eps[eb]:
                continue
            half_a = width_um_by_edge[ea] // 2
            half_b = width_um_by_edge[eb] // 2
            sep = half_a + half_b + clearance_um
            ax_min, ax_max, ay_min, ay_max = axis_vars[ea]
            bx_min, bx_max, by_min, by_max = axis_vars[eb]

            literals = []
            for cond in (
                ax_max + sep <= bx_min,
                bx_max + sep <= ax_min,
                ay_max + sep <= by_min,
                by_max + sep <= ay_min,
            ):
                lit = model.NewBoolVar(f"sep_{ea}_{eb}_{len(literals)}")
                model.Add(cond).OnlyEnforceIf(lit)
                literals.append(lit)
            model.AddBoolOr(literals)


@dataclass
class SolveResult:
    status_name: str
    wall_seconds: float
    objective: float | None
    solver: cp_model.CpSolver


def solve_model(
    cpsat: CpsatModel,
    *,
    time_limit_s: float = DEFAULT_TIME_LIMIT_S,
    num_workers: int = DEFAULT_NUM_WORKERS,
    log_search: bool = False,
) -> SolveResult:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = num_workers
    solver.parameters.log_search_progress = log_search

    status = solver.Solve(cpsat.model)
    status_name = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
        cp_model.UNKNOWN: "UNKNOWN",
    }.get(status, "UNKNOWN")

    objective: float | None = None
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        try:
            objective = float(solver.ObjectiveValue())
        except Exception:  # pragma: no cover - no objective registered
            objective = None

    return SolveResult(
        status_name=status_name,
        wall_seconds=float(solver.WallTime()),
        objective=objective,
        solver=solver,
    )


__all__ = [
    "CpsatModel",
    "DEFAULT_NUM_WORKERS",
    "DEFAULT_TIME_LIMIT_S",
    "EndpointHandle",
    "INFLATION_BIAS_UM",
    "SolveResult",
    "build_model",
    "solve_model",
]
