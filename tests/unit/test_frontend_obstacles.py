from frontend.models import BBox, ComponentExpansion
from frontend.obstacles import build_obstacles
from schema.v33 import (
    BoardOutline,
    GlobalConstraints,
    Metadata,
    Point,
    V33Layout,
)


def _layout(keepouts: list[object] | None = None) -> V33Layout:
    return V33Layout.model_validate(
        {
            "metadata": Metadata(project_name="X").model_dump(),
            "global_constraints": GlobalConstraints(
                board_outline=BoardOutline(
                    type="bounding_box",
                    origin=Point(x=0.0, y=0.0),
                    width=40.0,
                    height=100.0,
                ),
                keepout_zones=keepouts or [],
            ).model_dump(),
            "footprints": {},
            "components": {},
            "nodes": {},
            "terminals": {},
            "edges": {},
        }
    )


def test_board_outline_creates_polygon() -> None:
    layout = _layout()
    obstacles = build_obstacles(layout, {})
    kinds = [o.kind for o in obstacles]
    assert kinds.count("board_outline") == 1
    board = next(o for o in obstacles if o.kind == "board_outline")
    assert board.polygon == ((0.0, 0.0), (40.0, 0.0), (40.0, 100.0), (0.0, 100.0))


def test_keepout_polygon_passthrough() -> None:
    layout = _layout(
        keepouts=[
            {"name": "kz1", "polygon": [{"x": 1.0, "y": 1.0}, {"x": 5.0, "y": 1.0}]}
        ]
    )
    obstacles = build_obstacles(layout, {})
    keepouts = [o for o in obstacles if o.kind == "keepout"]
    assert len(keepouts) == 1
    assert keepouts[0].label == "kz1"
    assert keepouts[0].polygon == ((1.0, 1.0), (5.0, 1.0))


def test_fixed_component_bbox_appended() -> None:
    layout = _layout()
    expansion = ComponentExpansion(
        name="IC1",
        footprint_ref="FP",
        placement_kind="fixed",
        pads=(),
        bbox=BBox(0.0, 0.0, 5.0, 10.0),
    )
    uv_only = ComponentExpansion(
        name="C1", footprint_ref="FP", placement_kind="parametric_uv", pads=()
    )
    obstacles = build_obstacles(layout, {"IC1": expansion, "C1": uv_only})
    bboxes = [o for o in obstacles if o.kind == "footprint_bbox"]
    assert len(bboxes) == 1
    assert bboxes[0].label == "IC1"
