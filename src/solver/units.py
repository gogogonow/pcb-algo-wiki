"""Unit conversion helpers for the M4 CP-SAT model.

CP-SAT only handles integer variables; we discretise every coordinate at the
1 µm grid (per ALGORITHM-OVERVIEW §5). Public IR (``v6_ir``, ``geometry_ir``)
keeps floating-point millimetres so downstream tools (SVG, regression
assertions) stay easy to read; this module is the single conversion seam.
"""

from __future__ import annotations

import math

MM_TO_UM = 1000
"""Number of µm per mm — the discretisation grid step."""

LENGTH_TOLERANCE_FRACTION = 0.005
"""±0.5 % length tolerance for ``rf_constrained_locked`` edges (M4 DoD)."""


def mm_to_um(value: float) -> int:
    """Convert millimetres → micrometres, rounding to the nearest integer.

    Uses banker's rounding via :func:`round` to keep symmetric error around
    half-µm; M4 fixtures are at most 4 decimals so the rounding never bites.
    """

    if not math.isfinite(value):
        raise ValueError(f"mm_to_um requires a finite value, got {value!r}")
    return int(round(value * MM_TO_UM))


def um_to_mm(value: int) -> float:
    """Convert micrometres → millimetres."""

    return value / MM_TO_UM


def length_tolerance_um(target_length_mm: float) -> int:
    """Return the half-width of the locked-edge length tolerance in µm.

    The CP-SAT length constraint emits ``target - tol ≤ Σ|Δ| ≤ target + tol``;
    we always allow at least 1 µm slack to absorb the µm rounding of two
    endpoints (each rounded once).
    """

    if target_length_mm <= 0.0:
        raise ValueError(f"target_length must be positive, got {target_length_mm!r}")
    target_um = target_length_mm * MM_TO_UM
    return max(1, int(target_um * LENGTH_TOLERANCE_FRACTION))


__all__ = [
    "LENGTH_TOLERANCE_FRACTION",
    "MM_TO_UM",
    "length_tolerance_um",
    "mm_to_um",
    "um_to_mm",
]
