"""M8 SVG pad-rendering tests for ``output.render_full_layout``."""

from __future__ import annotations

from output import render_full_layout
from schema.geometry_ir import (
    ComponentPlacement as GeomComponentPlacement,
    GeometryIR,
    PinPlacement,
    RoutePolyline,
)
from schema.solver_ir import RoutingClass
from schema.v33 import (
    Component,
    ComponentPlacement,
    Footprint,
    FootprintPin,
    GlobalConstraints,
    Metadata,
    PadGeometry,
    RoutingConstraints,
    Stackup,
    V33Layout,
)
from schema.v6_ir import Board, Point


def _v33(footprints, components):
    return V33Layout(
        metadata=Metadata(),
        global_constraints=GlobalConstraints(
            routing=RoutingConstraints(),
            stackup=Stackup(),
        ),
        footprints=footprints,
        components=components,
        nodes={},
        terminals={},
        edges={},
    )


def _geom_with_placements(*placements: GeomComponentPlacement) -> GeometryIR:
    return GeometryIR(
        project="t",
        board=Board(origin=Point(x=0.0, y=0.0), width=50.0, height=50.0),
        routes={
            "e1": RoutePolyline(
                edge_id="e1",
                routing_class=RoutingClass.FLEXIBLE_PATH,
                width=0.254,
                points=(Point(x=0.0, y=5.0), Point(x=20.0, y=5.0)),
            )
        },
        placements={p.component: p for p in placements},
        solve_status="OPTIMAL",
        solve_wall_seconds=0.01,
    )


def _layout_with_pads(component_count: int = 2, pin_count: int = 4):
    pins = {}
    for i in range(pin_count):
        pins[f"P{i+1}"] = FootprintPin(
            local_x=float(i),
            local_y=0.0,
            local_orientation=0.0,
            pad_geometry=PadGeometry(shape="rect", length=1.0, width=0.5),
        )
    fp = Footprint(pins=pins)
    components = {}
    placements = []
    for c in range(component_count):
        comp_id = f"C{c+1}"
        components[comp_id] = Component(
            footprint_ref="FP1",
            placement=ComponentPlacement(x=10.0 + c * 5.0, y=20.0, rotation=0.0),
        )
        pads = tuple(
            PinPlacement(
                pin=f"P{i+1}",
                point=Point(x=10.0 + c * 5.0 + i, y=20.0),
            )
            for i in range(pin_count)
        )
        placements.append(
            GeomComponentPlacement(
                component=comp_id,
                anchor=Point(x=10.0 + c * 5.0, y=20.0),
                rotation_deg=0.0,
                pads=pads,
            )
        )
    return _v33(footprints={"FP1": fp}, components=components), placements


def test_render_pads_emits_polygon_per_pin() -> None:
    layout, placements = _layout_with_pads(component_count=2, pin_count=4)
    geom = _geom_with_placements(*placements)
    svg = render_full_layout(geom, layout=layout, show_pads=True)
    assert svg.count("<polygon ") == 8
    assert "pads=8" in svg


def test_render_pads_disabled_emits_none() -> None:
    layout, placements = _layout_with_pads()
    geom = _geom_with_placements(*placements)
    svg = render_full_layout(geom, layout=layout, show_pads=False)
    assert "<polygon " not in svg


def test_render_pads_no_layout_backwards_compat() -> None:
    geom = _geom_with_placements()
    svg = render_full_layout(geom)
    assert "<polygon " not in svg
    assert svg.startswith("<svg")


def test_render_pads_skips_components_not_in_geom() -> None:
    layout = _v33(
        footprints={
            "FP1": Footprint(
                pins={
                    "P1": FootprintPin(
                        local_x=0.0,
                        local_y=0.0,
                        pad_geometry=PadGeometry(shape="rect", length=1.0, width=0.5),
                    )
                }
            )
        },
        components={"C1": Component(footprint_ref="FP1", placement=None)},
    )
    geom = _geom_with_placements()  # no GeometryIR placement for C1
    svg = render_full_layout(geom, layout=layout)
    assert "<polygon " not in svg


def test_render_pads_rotation_changes_orientation() -> None:
    pin = FootprintPin(
        local_x=5.0,
        local_y=0.0,
        local_orientation=0.0,
        pad_geometry=PadGeometry(shape="rect", length=2.0, width=1.0),
    )
    fp = Footprint(pins={"P1": pin})
    layout = _v33(
        footprints={"FP1": fp},
        components={
            "C1": Component(
                footprint_ref="FP1",
                placement=ComponentPlacement(x=10.0, y=20.0, rotation=0.0),
            )
        },
    )
    placement_0 = GeomComponentPlacement(
        component="C1",
        anchor=Point(x=10.0, y=20.0),
        rotation_deg=0.0,
        pads=(PinPlacement(pin="P1", point=Point(x=15.0, y=20.0)),),
    )
    placement_90 = GeomComponentPlacement(
        component="C1",
        anchor=Point(x=10.0, y=20.0),
        rotation_deg=90.0,
        pads=(PinPlacement(pin="P1", point=Point(x=10.0, y=25.0)),),
    )
    svg_0 = render_full_layout(_geom_with_placements(placement_0), layout=layout)
    svg_90 = render_full_layout(_geom_with_placements(placement_90), layout=layout)
    assert svg_0.count("<polygon ") == 1
    assert svg_90.count("<polygon ") == 1
    assert svg_0 != svg_90


def test_render_pads_includes_pin_label_in_title() -> None:
    layout, placements = _layout_with_pads(component_count=1, pin_count=1)
    geom = _geom_with_placements(*placements)
    svg = render_full_layout(geom, layout=layout)
    assert "<title>C1.P1</title>" in svg


def test_render_pads_skips_pin_without_geometry() -> None:
    fp = Footprint(
        pins={
            "P1": FootprintPin(local_x=0.0, local_y=0.0, pad_geometry=None),
            "P2": FootprintPin(
                local_x=1.0,
                local_y=0.0,
                pad_geometry=PadGeometry(shape="rect", length=1.0, width=0.5),
            ),
        }
    )
    layout = _v33(
        footprints={"FP1": fp},
        components={
            "C1": Component(
                footprint_ref="FP1",
                placement=ComponentPlacement(x=10.0, y=20.0, rotation=0.0),
            )
        },
    )
    placement = GeomComponentPlacement(
        component="C1",
        anchor=Point(x=10.0, y=20.0),
        rotation_deg=0.0,
        pads=(
            PinPlacement(pin="P1", point=Point(x=10.0, y=20.0)),
            PinPlacement(pin="P2", point=Point(x=11.0, y=20.0)),
        ),
    )
    svg = render_full_layout(_geom_with_placements(placement), layout=layout)
    assert svg.count("<polygon ") == 1
