"""Generalized tests for uv_slot_search — no PA-specific ids."""

from solver.v2.uv_slot_search import search_slot


def test_single_edge_picks_side_with_more_clearance():
    # Host trace runs along y=10 from x=0 to x=40, width 1mm.
    # An obstacle blocks the upper side (y>10) around x=10..30.
    # RLC footprint 2x3mm. Expect placement on lower side (y<10).
    poly = [(0.0, 10.0), (40.0, 10.0)]
    result = search_slot(
        footprint_size=(2.0, 0.6),
        anchor_pin_local=(-1.0, 0.0),
        host_polylines={"E1": poly},
        host_widths={"E1": 1.0},
        board=(40.0, 20.0),
        obstacles=[(5.0, 11.0, 35.0, 18.0)],  # upper obstacle
        step_mm=1.0,
    )
    assert result is not None
    # Body center y should be below the trace (y < 10).
    assert result.anchor_xy[1] < 10.0


def test_multi_edge_same_net_picks_higher_clearance_edge():
    # Two edges. E1 has dense obstacles nearby; E2 is wide open.
    e1 = [(0.0, 5.0), (40.0, 5.0)]
    e2 = [(0.0, 50.0), (40.0, 50.0)]
    obstacles = [
        (5.0, 6.0, 35.0, 8.0),
        (5.0, 2.0, 35.0, 4.0),
    ]
    result = search_slot(
        footprint_size=(2.0, 0.6),
        anchor_pin_local=(-1.0, 0.0),
        host_polylines={"E1": e1, "E2": e2},
        host_widths={"E1": 1.0, "E2": 1.0},
        board=(40.0, 80.0),
        obstacles=obstacles,
        step_mm=2.0,
    )
    assert result is not None
    assert result.edge_id == "E2"


def test_returns_none_when_no_feasible_slot():
    poly = [(0.0, 5.0), (10.0, 5.0)]
    # Tight obstacles on both sides leave no room for the footprint.
    obstacles = [
        (0.0, 0.0, 10.0, 4.7),
        (0.0, 5.3, 10.0, 10.0),
    ]
    result = search_slot(
        footprint_size=(2.0, 1.5),
        anchor_pin_local=(-1.0, 0.0),
        host_polylines={"E1": poly},
        host_widths={"E1": 1.0},
        board=(10.0, 10.0),
        obstacles=obstacles,
        step_mm=1.0,
    )
    assert result is None


def test_perpendicular_orientation():
    # Horizontal trace → expect rotation ±90 so body long axis (local x)
    # aligns with world y (perpendicular).
    poly = [(0.0, 5.0), (20.0, 5.0)]
    result = search_slot(
        footprint_size=(2.0, 0.6),
        anchor_pin_local=(-1.0, 0.0),
        host_polylines={"E1": poly},
        host_widths={"E1": 0.5},
        board=(20.0, 20.0),
        obstacles=[],
        step_mm=1.0,
    )
    assert result is not None
    assert int(round(abs(result.rotation_deg))) == 90
