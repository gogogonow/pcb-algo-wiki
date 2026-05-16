"""Placement-kind classifier for parametric UV components.

Used by the UV adhesion solver to choose the correct placement strategy:
- SERIES: both pins live on distinct signal nets (e.g. an inline R/L/C
  bridging two microstrip endpoints) → must be anchored at BOTH ends.
- SHUNT:  one pin connects to a power/ground rail (GND/VCC/VDD/...) →
  anchor one signal pin to the host trace, body extends perpendicular.
- PROBE:  single pin only (test points, single-pad pads).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

_RAIL_NET_PREFIXES: tuple[str, ...] = ("GND", "VCC", "VDD", "VSS", "VEE", "VBAT")


class UvKind(str, Enum):
    SERIES = "series"
    SHUNT = "shunt"
    PROBE = "probe"


def _is_rail(net: str | None) -> bool:
    if not net:
        return False
    upper = net.upper()
    return any(upper.startswith(p) for p in _RAIL_NET_PREFIXES)


def classify_uv(uv: Any) -> UvKind:
    """Classify a UV component based on its ``pin_nets`` mapping.

    ``uv`` is expected to expose a ``pin_nets`` attribute (dict-like). The
    function is duck-typed to accept frontend ``ComponentExpansion``,
    schema ``Component``, or test stubs.
    """
    pin_nets = dict(getattr(uv, "pin_nets", {}) or {})
    nets = [n for n in pin_nets.values() if n]
    if len(nets) < 2:
        return UvKind.PROBE
    rails = sum(1 for n in nets if _is_rail(n))
    signal = len(nets) - rails
    if rails == 0 and signal >= 2:
        return UvKind.SERIES
    return UvKind.SHUNT


__all__ = ["UvKind", "classify_uv"]
