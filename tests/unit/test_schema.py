from pathlib import Path

import pytest
from pydantic import ValidationError

from schema import V33Layout, load_v33_layout
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
