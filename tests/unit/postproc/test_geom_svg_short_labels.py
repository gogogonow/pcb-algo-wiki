"""WI-F1b: base geom_svg renderer must use short_id for node + edge labels."""

from src.postproc.geom_svg import render_geometry_svg
from src.schema.geometry_ir import GeometryIR, RoutePolyline
from src.schema.v6_ir import Board, Point


def _empty_board(w: float = 10.0, h: float = 10.0) -> Board:
    return Board(origin=Point(x=0.0, y=0.0), width=w, height=h)


def test_node_labels_use_short_id():
    """WI-I7: node labels removed, but node circles still rendered."""
    ir = GeometryIR(
        project="t",
        board=_empty_board(),
        placements={},
        routes={},
        nodes={"IC1_pin2_seg2_end_split_pad": Point(x=1.0, y=1.0)},
        solve_status="OPTIMAL",
        solve_wall_seconds=0.0,
    )
    svg = render_geometry_svg(ir)
    # Node circle still rendered (radius 1.5)
    assert '<circle cx=' in svg and 'r="1.5"' in svg
    # Node label no longer rendered
    assert ">p2s2_sp<" not in svg
    assert "IC1_pin2_seg2_end_split_pad" not in svg


def test_edge_labels_use_short_id():
    ir = GeometryIR(
        project="t",
        board=_empty_board(),
        placements={},
        routes={
            "IC1_pin1_seg5": RoutePolyline(
                edge_id="IC1_pin1_seg5",
                routing_class="rf_constrained_locked",
                width=0.5,
                points=(Point(x=0.0, y=0.0), Point(x=5.0, y=5.0)),
            )
        },
        nodes={},
        solve_status="OPTIMAL",
        solve_wall_seconds=0.0,
    )
    svg = render_geometry_svg(ir)
    assert ">p1s5<" in svg
    assert ">IC1_pin1_seg5<" not in svg
