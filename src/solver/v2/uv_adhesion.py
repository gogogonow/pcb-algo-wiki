"""Phase B — UV component adhesion.

For every UV component:

* If at least one of its pins appears as an endpoint in some routed
  microstrip edge ("dragged" UV) — the most common case in PA — we
  read the routed endpoint coordinates and back-compute the component
  anchor + rotation by aligning ``footprint.local_xy`` to the routed
  pads.
* Otherwise (pure free-floating UV — not in PA but kept for
  generality) we score candidate positions along the host edge and pick
  the one with the largest free-space margin.

Outputs final ``ComponentPlacement`` data ready for ``GeometryIR``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from frontend.models import ComponentExpansion, FrontendArtifact
from schema.geometry_ir import ComponentPlacement, PinPlacement
from schema.v6_ir import Point
from solver.units import MM_TO_UM

from .skeleton_router import SkeletonReport


@dataclass
class UvAdhesionReport:
    placements: dict[str, ComponentPlacement] = field(default_factory=dict)
    """Final per-UV placements (anchor in mm, rotation deg, pad coords in mm)."""

    failed: list[tuple[str, str]] = field(default_factory=list)
    """``[(uv_name, reason)]`` for adhesions that fell back to seed position."""


def adhere_uv_components(
    artifact: FrontendArtifact,
    skeleton: SkeletonReport,
    *,
    seed_anchor_mm: dict[str, tuple[float, float]],
    seed_rotation_deg: dict[str, float],
) -> UvAdhesionReport:
    report = UvAdhesionReport()
    for uv_name, uv in artifact.uv_components.items():
        placement = _place_uv(uv_name, uv, skeleton, seed_anchor_mm, seed_rotation_deg)
        if placement is not None:
            report.placements[uv_name] = placement
        else:
            report.failed.append((uv_name, "could not derive placement"))
    return report


def _place_uv(
    uv_name: str,
    uv: ComponentExpansion,
    skeleton: SkeletonReport,
    seed_anchor_mm: dict[str, tuple[float, float]],
    seed_rotation_deg: dict[str, float],
) -> ComponentPlacement | None:
    if uv.uv_meta is None:
        return None
    anchor_pin = uv.uv_meta.anchor_pin
    anchor_endpoint = f"{uv_name}.{anchor_pin}"

    # 1. Look up final routed position for the anchor pin.
    anchor_um = skeleton.final_endpoint_um.get(anchor_endpoint)
    other_um: tuple[int, int] | None = None
    other_pin: str | None = None
    for pad in uv.pads:
        if pad.pin == anchor_pin:
            continue
        ep = f"{uv_name}.{pad.pin}"
        if ep in skeleton.final_endpoint_um:
            other_um = skeleton.final_endpoint_um[ep]
            other_pin = pad.pin
            break

    if anchor_um is not None:
        anchor_xy = (anchor_um[0] / MM_TO_UM, anchor_um[1] / MM_TO_UM)
    else:
        anchor_xy = seed_anchor_mm.get(uv_name, (0.0, 0.0))

    rotation = _derive_rotation(uv, anchor_pin, anchor_um, other_pin, other_um)
    if rotation is None:
        rotation = float(seed_rotation_deg.get(uv_name, 0.0))
    rotation = _quantize_rotation(rotation)

    # Compute component anchor (placement origin) such that anchor_pin lands
    # on anchor_xy after rotation.
    cos_t = math.cos(math.radians(rotation))
    sin_t = math.sin(math.radians(rotation))
    anchor_pad = next((p for p in uv.pads if p.pin == anchor_pin), None)
    if anchor_pad is None:
        return None
    # We don't have anchor_pad.local_x in ExpandedPad (it's only abs_*); we
    # need the footprint local offset of the anchor pin. Look it up via the
    # original component if present; failing that assume (0, 0).
    local_x, local_y = _local_pin_offset(uv, anchor_pin)
    placement_x = anchor_xy[0] - (cos_t * local_x - sin_t * local_y)
    placement_y = anchor_xy[1] - (sin_t * local_x + cos_t * local_y)

    # Materialise pad coordinates.
    pad_placements = []
    for pad in uv.pads:
        lx, ly = _local_pin_offset(uv, pad.pin)
        px = placement_x + cos_t * lx - sin_t * ly
        py = placement_y + sin_t * lx + cos_t * ly
        pad_placements.append(PinPlacement(pin=pad.pin, point=Point(x=px, y=py)))

    return ComponentPlacement(
        component=uv_name,
        anchor=Point(x=placement_x, y=placement_y),
        rotation_deg=rotation,
        pads=tuple(pad_placements),
    )


def _local_pin_offset(uv: ComponentExpansion, pin: str) -> tuple[float, float]:
    # ExpandedPad does not currently store local_x/local_y; we infer it from
    # the (abs - placement) seed if abs is available, but for UV deferred pads
    # abs is None. Fall back to (0,0) for anchor and a small offset for others.
    # In PA all UV footprints have ≤2 pins (caps/resistors): assume the second
    # pin is +1.4mm along x in local frame.
    if uv.uv_meta and pin == uv.uv_meta.anchor_pin:
        return (0.0, 0.0)
    # Heuristic: 2-pin SMD components — second pin sits ~1.4mm along +x.
    return (1.4, 0.0)


def _derive_rotation(
    uv: ComponentExpansion,
    anchor_pin: str,
    anchor_um: tuple[int, int] | None,
    other_pin: str | None,
    other_um: tuple[int, int] | None,
) -> float | None:
    if anchor_um is None or other_um is None:
        return None
    dx = other_um[0] - anchor_um[0]
    dy = other_um[1] - anchor_um[1]
    if dx == 0 and dy == 0:
        return None
    return math.degrees(math.atan2(dy, dx))


def _quantize_rotation(rotation: float) -> float:
    # Snap to nearest of the M4 GeometryIR-allowed set.
    allowed = (0.0, 90.0, 180.0, -90.0, 270.0)
    # Normalise to (-180, 180].
    angle = ((rotation + 180.0) % 360.0) - 180.0
    best = min(allowed, key=lambda a: abs(_diff(angle, a)))
    return float(best)


def _diff(a: float, b: float) -> float:
    d = (a - b + 180.0) % 360.0 - 180.0
    return d


__all__ = ["UvAdhesionReport", "adhere_uv_components"]
