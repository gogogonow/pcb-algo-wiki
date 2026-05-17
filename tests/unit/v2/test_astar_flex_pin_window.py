"""PhaseC quality fix — endpoint components must remain HARD obstacles.

Verifies the new "pin-direction escape window" behavior in
``solver.astar_flex._build_grid``:

* The footprint bbox of an endpoint component is **still** in the obstacle
  set — paths can no longer traverse the device body.
* Only a small rectangle outwards from each endpoint pin (in the pin's
  natural outward direction) is carved out of the obstacle grid so the
  path can exit/enter the pad.

Test fixture: 2 fixed components A and B, each a 4×4 mm square footprint
with one pin on the OUTSIDE-FACING edge (right edge of A, left edge of B).
A flex edge ``flex1`` connects A.P → B.P.  We assert the resulting A*
polyline avoids the centre of each component bbox.
"""

from __future__ import annotations

from frontend.models import (
    BBox,
    ComponentExpansion,
    ExpandedPad,
    FrontendArtifact,
    LintReport,
)
from schema.geometry_ir import (
    ComponentPlacement,
    GeometryIR,
    PinPlacement,
    RoutePolyline,
)
from schema.solver_ir import RoutingClass as SolverRoutingClass
from schema.solver_ir import SolverEdge, SolverIR
from schema.v6_ir import Board, Point, RoutingClass, V6Terminal
from solver.astar_flex import AstarConfig, route_flexible_paths


def _make_square_comp(
    name: str, cx: float, cy: float, pin_local_dx: float
) -> tuple[ComponentExpansion, ComponentPlacement]:
    """Build a 4mm-square component with a single pin offset to ``pin_local_dx``
    along the local +X axis (so the pin sits on the right edge when
    ``pin_local_dx > 0`` or on the left edge when ``pin_local_dx < 0``).
    """
    pad = ExpandedPad(
        component=name,
        pin="P",
        abs_x=cx + pin_local_dx,
        abs_y=cy,
        orientation=0.0,
        kind="fixed",
        local_x=pin_local_dx,
        local_y=0.0,
        pad_width=0.5,
        pad_length=0.5,
    )
    bbox = BBox(min_x=cx - 2.0, min_y=cy - 2.0, max_x=cx + 2.0, max_y=cy + 2.0)
    comp = ComponentExpansion(
        name=name,
        footprint_ref=None,
        placement_kind="fixed",
        pads=(pad,),
        bbox=bbox,
    )
    placement = ComponentPlacement(
        component=name,
        anchor=Point(x=cx, y=cy),
        rotation_deg=0.0,
        pads=(PinPlacement(pin="P", point=Point(x=cx + pin_local_dx, y=cy)),),
    )
    return comp, placement


def test_endpoint_component_body_blocks_traversal() -> None:
    """Body of an endpoint component remains an obstacle; only the pin
    direction is opened for escape.

    Setup: A is at (10, 10) with pin on the LEFT edge (local -X, abs x=8).
    B is at (10, 25) with pin on the LEFT edge too (local -X, abs x=8).
    Direct flight A.P (8, 10) → B.P (8, 25) goes straight up along x=8 —
    which is on the OUTSIDE of both bodies. To make the routing meaningful
    we offset the components so a straight A.P → B.P line clips through
    them.

    Real setup: A pin on RIGHT edge (abs x=12), B pin on LEFT edge (abs x=8),
    but B is placed to the LEFT of A's pin. So direct line goes LEFT through
    A's body. With pin-window: A pin can ONLY escape RIGHT, so path must
    detour around A entirely.
    """
    # A at (10, 10), pin on RIGHT edge → abs pin = (12, 10), pin can only
    # escape rightward.
    a_comp, a_place = _make_square_comp("A", 10.0, 10.0, +2.0)
    # B at (5, 15), pin on LEFT edge → abs pin = (3, 15), pin can only
    # escape leftward.  Direct line (12,10)→(3,15) would pass through A
    # if body weren't an obstacle, AND would need to enter A from the LEFT
    # which is forbidden by the pin window.
    b_comp, b_place = _make_square_comp("B", 5.0, 15.0, -2.0)

    artifact = FrontendArtifact(
        project_name="pinwin",
        board={"width": 30.0, "height": 25.0},
        lint_report=LintReport(),
        components={"A": a_comp, "B": b_comp},
        fixed_terminals={"A.P": a_comp.pads[0], "B.P": b_comp.pads[0]},
        edges={},
    )

    ir = SolverIR(
        project="pinwin",
        board=Board(origin=Point(x=0.0, y=0.0), width=30.0, height=25.0),
        clearance=0.3,
        terminals={
            "A.P": V6Terminal(point=Point(x=12.0, y=10.0)),
            "B.P": V6Terminal(point=Point(x=3.0, y=15.0)),
        },
        edges={
            "flex1": SolverEdge(
                endpoints=("A.P", "B.P"),
                routing_class=SolverRoutingClass.FLEXIBLE_PATH,
                width=0.3,
            )
        },
    )

    seed = RoutePolyline(
        edge_id="flex1",
        routing_class=RoutingClass.FLEXIBLE_PATH,
        width=0.3,
        points=(Point(x=12.0, y=10.0), Point(x=3.0, y=15.0)),
    )
    geom = GeometryIR(
        project="pinwin",
        board=ir.board,
        placements={"A": a_place, "B": b_place},
        routes={"flex1": seed},
        solve_status="FEASIBLE",
        solve_wall_seconds=0.0,
    )

    new_geom, report = route_flexible_paths(
        ir=ir,
        artifact=artifact,
        geom=geom,
        config=AstarConfig(grid_step_um=200, rf_inflate_um=300),
    )

    assert "flex1" in report.routed_edges, "flex1 should still be routable"
    route = new_geom.routes["flex1"]
    pts = [(float(p.x), float(p.y)) for p in route.points]
    assert pts[0] == (12.0, 10.0)
    assert pts[-1] == (3.0, 15.0)

    # No intermediate (non-endpoint) point may fall *inside* A or B body.
    eps = 0.05
    for x, y in pts[1:-1]:
        for bb in (a_comp.bbox, b_comp.bbox):
            assert bb is not None
            inside = (
                bb.min_x + eps < x < bb.max_x - eps
                and bb.min_y + eps < y < bb.max_y - eps
            )
            assert not inside, (
                f"intermediate point ({x},{y}) falls inside endpoint "
                f"component bbox {bb}"
            )

    # Also: check that no SEGMENT of the polyline traverses the interior of
    # either body bbox (an intermediate vertex might land outside but the
    # segment crosses through).
    a_bb = a_comp.bbox
    b_bb = b_comp.bbox
    assert a_bb is not None and b_bb is not None
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        # Sample the segment at fine intervals; require strict interior
        # presence (eps margin) to rule out "skim along the edge".
        for t_step in range(1, 20):  # 1..19 excludes endpoints
            t = t_step / 20.0
            sx = x1 + t * (x2 - x1)
            sy = y1 + t * (y2 - y1)
            for bb in (a_bb, b_bb):
                inside = (
                    bb.min_x + eps < sx < bb.max_x - eps
                    and bb.min_y + eps < sy < bb.max_y - eps
                )
                assert not inside, (
                    f"segment ({x1},{y1})→({x2},{y2}) interior crosses "
                    f"endpoint component body {bb}"
                )
