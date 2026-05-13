from pathlib import Path

import pytest
from pydantic import ValidationError

from schema import (
    Board,
    Point,
    RoutingClass,
    V6Edge,
    V6IR,
    V33Layout,
    load_v33_layout,
)
from schema.v33 import Footprint, Metadata

REAL_CASE_PATH = Path(__file__).resolve().parents[2] / "rf_layout_simplified.yaml"


def test_schema_package_exports_v33_loader_and_model() -> None:
    assert V33Layout.__name__ == "V33Layout"
    assert callable(load_v33_layout)


def test_load_v33_layout_reads_yaml_as_utf8(
    monkeypatch,
) -> None:
    original_read_text = Path.read_text
    read_kwargs: dict[str, object] = {}

    def fake_read_text(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        read_kwargs["encoding"] = encoding
        return original_read_text(self, encoding="utf-8", errors=errors)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    load_v33_layout(REAL_CASE_PATH)

    assert read_kwargs["encoding"] == "utf-8"


def test_load_v33_layout_parses_real_case_with_expected_counts() -> None:
    layout = load_v33_layout(REAL_CASE_PATH)

    assert isinstance(layout.metadata, Metadata)
    assert layout.metadata.project_name == "PA_Module_Simplified"
    assert len(layout.components) == 15
    assert len(layout.footprints) == 5
    assert len(layout.nodes) == 8
    assert len(layout.terminals) == 10
    assert len(layout.edges) == 22


def test_load_v33_layout_preserves_unknown_fields_on_nested_models() -> None:
    layout = load_v33_layout(REAL_CASE_PATH)

    footprint = layout.footprints["PKG_TEST_POINT"]
    assert isinstance(footprint, Footprint)
    assert footprint.dimensions.extra_fields["redius"] == 2.0

    pin = footprint.pins["PIN_1"]
    assert pin.pad_geometry is not None
    assert pin.pad_geometry.extra_fields["redius"] == 2.0


def test_v33_layout_requires_documented_root_sections() -> None:
    with pytest.raises(ValidationError, match="components"):
        V33Layout.model_validate(
            {
                "metadata": {},
                "global_constraints": {},
                "footprints": {},
                "terminals": {},
                "nodes": {},
                "edges": {},
            }
        )


def test_v33_layout_rejects_null_global_constraints() -> None:
    with pytest.raises(ValidationError, match="global_constraints"):
        V33Layout.model_validate(
            {
                "metadata": {},
                "global_constraints": None,
                "footprints": {},
                "components": {},
                "terminals": {},
                "nodes": {},
                "edges": {},
            }
        )


def test_terminal_exposes_typed_v33_fields() -> None:
    layout = V33Layout.model_validate(
        {
            "metadata": {},
            "global_constraints": {},
            "footprints": {},
            "components": {},
            "terminals": {
                "RF_OUT": {
                    "type": "terminal",
                    "associated_component": "U1",
                    "pin": "PIN_1",
                    "net": "RF_OUT",
                    "tyop_field": "still-preserved",
                }
            },
            "nodes": {},
            "edges": {},
        }
    )

    terminal = layout.terminals["RF_OUT"]
    assert terminal.associated_component == "U1"
    assert terminal.pin == "PIN_1"
    assert terminal.net == "RF_OUT"
    assert terminal.extra_fields["tyop_field"] == "still-preserved"


def test_v6_ir_accepts_minimal_strict_graph() -> None:
    ir = V6IR.model_validate(
        {
            "board": {"origin": {"x": 0.0, "y": 0.0}, "width": 10.0, "height": 5.0},
            "terminals": {"J1": {"point": {"x": 0.0, "y": 0.0}}},
            "nodes": {"N1": {"point": {"x": 5.0, "y": 2.5}}},
            "edges": {
                "E1": {
                    "endpoints": ("J1", "N1"),
                    "routing_class": RoutingClass.FLEXIBLE_PATH,
                }
            },
        }
    )

    assert ir.edges["E1"].routing_class is RoutingClass.FLEXIBLE_PATH


def test_v6_ir_accepts_locked_edge_width_in_strict_graph() -> None:
    ir = V6IR.model_validate(
        {
            "board": {"origin": {"x": 0.0, "y": 0.0}, "width": 10.0, "height": 5.0},
            "terminals": {"J1": {"point": {"x": 0.0, "y": 0.0}}},
            "nodes": {"N1": {"point": {"x": 5.0, "y": 2.5}}},
            "edges": {
                "E1": {
                    "endpoints": ("J1", "N1"),
                    "routing_class": RoutingClass.RF_CONSTRAINED_LOCKED,
                    "target_length": 7.5,
                    "width": 1.2,
                }
            },
        }
    )

    assert ir.edges["E1"].width == 1.2


def test_v6_ir_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        V6IR.model_validate(
            {
                "board": {
                    "origin": {"x": 0.0, "y": 0.0},
                    "width": 10.0,
                    "height": 5.0,
                    "unexpected": True,
                },
                "terminals": {"J1": {"point": {"x": 0.0, "y": 0.0}}},
                "nodes": {"N1": {"point": {"x": 5.0, "y": 2.5}}},
                "edges": {
                    "E1": {
                        "endpoints": ("J1", "N1"),
                        "routing_class": RoutingClass.FLEXIBLE_PATH,
                    }
                },
            }
        )


def test_v6_locked_edge_requires_target_length() -> None:
    with pytest.raises(ValidationError, match="target_length"):
        V6IR.model_validate(
            {
                "board": {"origin": {"x": 0.0, "y": 0.0}, "width": 10.0, "height": 5.0},
                "terminals": {"J1": {"point": {"x": 0.0, "y": 0.0}}},
                "nodes": {"N1": {"point": {"x": 5.0, "y": 2.5}}},
                "edges": {
                    "E1": {
                        "endpoints": ("J1", "N1"),
                        "routing_class": RoutingClass.RF_CONSTRAINED_LOCKED,
                    }
                },
            }
        )


@pytest.mark.parametrize(
    "routing_class",
    [RoutingClass.RF_CONSTRAINED_FREE, RoutingClass.FLEXIBLE_PATH],
)
def test_v6_free_and_flexible_edges_can_omit_target_length(
    routing_class: RoutingClass,
) -> None:
    ir = V6IR.model_validate(
        {
            "board": {"origin": {"x": 0.0, "y": 0.0}, "width": 10.0, "height": 5.0},
            "terminals": {"J1": {"point": {"x": 0.0, "y": 0.0}}},
            "nodes": {"N1": {"point": {"x": 5.0, "y": 2.5}}},
            "edges": {
                "E1": {
                    "endpoints": ("J1", "N1"),
                    "routing_class": routing_class,
                }
            },
        }
    )

    assert ir.edges["E1"].target_length is None


@pytest.mark.parametrize(
    ("model", "payload", "match"),
    [
        (
            Board,
            {"origin": {"x": 0.0, "y": 0.0}, "width": "10.0", "height": 5.0},
            "width",
        ),
        (Point, {"x": "1.0", "y": 2.0}, "x"),
        (
            V6Edge,
            {
                "endpoints": ("J1", "N1"),
                "routing_class": RoutingClass.RF_CONSTRAINED_LOCKED,
                "target_length": "42.0",
            },
            "target_length",
        ),
        (
            V6Edge,
            {
                "endpoints": ("J1", "N1"),
                "routing_class": RoutingClass.RF_CONSTRAINED_LOCKED,
                "target_length": 42.0,
                "width": "1.2",
            },
            "width",
        ),
    ],
)
def test_v6_models_reject_numeric_strings(
    model: type[Board] | type[Point] | type[V6Edge],
    payload: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        model.model_validate(payload)


def test_point_rejects_int_for_float_field() -> None:
    with pytest.raises(ValidationError, match="x"):
        Point.model_validate({"x": 1, "y": 2.0})


def test_board_rejects_int_for_float_field() -> None:
    with pytest.raises(ValidationError, match="width"):
        Board.model_validate(
            {"origin": {"x": 0.0, "y": 0.0}, "width": 10, "height": 5.0}
        )


def test_v6_edge_rejects_int_for_width_field() -> None:
    with pytest.raises(ValidationError, match="width"):
        V6Edge.model_validate(
            {
                "endpoints": ("J1", "N1"),
                "routing_class": RoutingClass.RF_CONSTRAINED_LOCKED,
                "target_length": 42.0,
                "width": 1,
            }
        )


def test_point_rejects_bool_for_float_field() -> None:
    with pytest.raises(ValidationError, match="x"):
        Point.model_validate({"x": True, "y": 2.0})


def test_v6_edge_rejects_list_endpoints() -> None:
    with pytest.raises(ValidationError, match="endpoints"):
        V6Edge.model_validate(
            {
                "endpoints": ["A", "B"],
                "routing_class": "rf_constrained_free",
            }
        )


def test_v6_edge_accepts_tuple_endpoints() -> None:
    edge = V6Edge.model_validate(
        {
            "endpoints": ("A", "B"),
            "routing_class": "rf_constrained_free",
        }
    )

    assert edge.endpoints == ("A", "B")


@pytest.mark.parametrize(
    "routing_class",
    [RoutingClass.RF_CONSTRAINED_FREE, RoutingClass.FLEXIBLE_PATH],
)
def test_v6_free_and_flexible_edges_reject_target_length(
    routing_class: RoutingClass,
) -> None:
    with pytest.raises(ValidationError, match="target_length"):
        V6IR.model_validate(
            {
                "board": {"origin": {"x": 0.0, "y": 0.0}, "width": 10.0, "height": 5.0},
                "terminals": {"J1": {"point": {"x": 0.0, "y": 0.0}}},
                "nodes": {"N1": {"point": {"x": 5.0, "y": 2.5}}},
                "edges": {
                    "E1": {
                        "endpoints": ("J1", "N1"),
                        "routing_class": routing_class,
                        "target_length": 1.5,
                    }
                },
            }
        )


@pytest.mark.parametrize(
    "endpoints",
    [("MISSING", "N1"), ("J1", "MISSING")],
)
def test_v6_ir_rejects_edges_with_missing_endpoint_references(
    endpoints: tuple[str, str],
) -> None:
    with pytest.raises(ValidationError, match="endpoints"):
        V6IR.model_validate(
            {
                "board": {"origin": {"x": 0.0, "y": 0.0}, "width": 10.0, "height": 5.0},
                "terminals": {"J1": {"point": {"x": 0.0, "y": 0.0}}},
                "nodes": {"N1": {"point": {"x": 5.0, "y": 2.5}}},
                "edges": {
                    "E1": {
                        "endpoints": endpoints,
                        "routing_class": RoutingClass.FLEXIBLE_PATH,
                    }
                },
            }
        )
