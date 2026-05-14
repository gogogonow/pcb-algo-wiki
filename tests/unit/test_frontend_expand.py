import math

import pytest

from frontend.expand_components import expand_component
from schema.v33 import (
    Component,
    ComponentPlacement,
    Footprint,
    FootprintDimensions,
    FootprintPin,
)


def _footprint(
    width: float, length: float, pins: dict[str, tuple[float, float]]
) -> Footprint:
    return Footprint(
        dimensions=FootprintDimensions(width=width, length=length),
        pins={
            name: FootprintPin(local_x=lx, local_y=ly, local_orientation=0)
            for name, (lx, ly) in pins.items()
        },
    )


def test_fixed_component_no_rotation() -> None:
    fp = _footprint(2.0, 4.0, {"PIN_1": (1.0, 0.0), "PIN_2": (-1.0, 0.0)})
    component = Component(
        footprint_ref="FP",
        placement=ComponentPlacement(x=10.0, y=20.0, rotation=0.0, is_floating=False),
    )
    exp = expand_component("U1", component, {"FP": fp})
    assert exp.placement_kind == "fixed"
    pads = {pad.pin: pad for pad in exp.pads}
    assert pads["PIN_1"].abs_x == pytest.approx(11.0)
    assert pads["PIN_1"].abs_y == pytest.approx(20.0)
    assert pads["PIN_2"].abs_x == pytest.approx(9.0)
    assert exp.bbox is not None
    assert exp.bbox.min_x == pytest.approx(9.0)
    assert exp.bbox.max_x == pytest.approx(11.0)


@pytest.mark.parametrize(
    "rotation, expected",
    [
        (0, (11.0, 20.0)),
        (90, (10.0, 21.0)),
        (180, (9.0, 20.0)),
        (270, (10.0, 19.0)),
    ],
)
def test_fixed_component_rotation_pin1(
    rotation: float, expected: tuple[float, float]
) -> None:
    fp = _footprint(2.0, 4.0, {"PIN_1": (1.0, 0.0)})
    component = Component(
        footprint_ref="FP",
        placement=ComponentPlacement(
            x=10.0, y=20.0, rotation=rotation, is_floating=False
        ),
    )
    exp = expand_component("U1", component, {"FP": fp})
    pad = exp.pads[0]
    assert pad.abs_x == pytest.approx(expected[0])
    assert pad.abs_y == pytest.approx(expected[1])


def test_uv_component_registers_meta_with_deferred_pads() -> None:
    fp = _footprint(1.0, 2.0, {"PIN_1": (-0.5, 0.0), "PIN_2": (0.5, 0.0)})
    component = Component(
        footprint_ref="FP",
        placement=ComponentPlacement(
            type="parametric_uv",
            anchor_pin="PIN_1",
            reference_net="RF_NET_1",
        ),
    )
    exp = expand_component("C1", component, {"FP": fp})
    assert exp.placement_kind == "parametric_uv"
    assert exp.uv_meta is not None
    assert exp.uv_meta.anchor_pin == "PIN_1"
    assert exp.uv_meta.reference_net == "RF_NET_1"
    for pad in exp.pads:
        assert pad.kind == "uv_deferred"
        assert pad.abs_x is None and pad.abs_y is None


def test_unknown_placement_returns_empty_pads() -> None:
    fp = _footprint(1.0, 2.0, {"PIN_1": (0.0, 0.0)})
    component = Component(footprint_ref="FP", placement=None)
    exp = expand_component("X", component, {"FP": fp})
    assert exp.placement_kind == "unknown"
    assert exp.pads == ()
    assert exp.bbox is None


def test_rotation_45_uses_affine_math() -> None:
    fp = _footprint(2.0, 2.0, {"PIN_1": (1.0, 0.0)})
    component = Component(
        footprint_ref="FP",
        placement=ComponentPlacement(x=0.0, y=0.0, rotation=45.0, is_floating=False),
    )
    exp = expand_component("X", component, {"FP": fp})
    pad = exp.pads[0]
    assert pad.abs_x == pytest.approx(math.cos(math.radians(45)))
    assert pad.abs_y == pytest.approx(math.sin(math.radians(45)))
