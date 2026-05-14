"""Tests for ``solver.units`` mm/µm conversions and length tolerance."""

from __future__ import annotations

import pytest

from solver.units import (
    LENGTH_TOLERANCE_FRACTION,
    MM_TO_UM,
    length_tolerance_um,
    mm_to_um,
    um_to_mm,
)


def test_mm_to_um_round_trips_for_grid_aligned_values() -> None:
    for v in (0.0, 1.0, 12.345, 100.0, -3.142):
        u = mm_to_um(v)
        assert isinstance(u, int)
        assert um_to_mm(u) == pytest.approx(v, abs=1e-6)


def test_mm_to_um_uses_round_half_to_even_at_µm_grid() -> None:
    # 0.0005 mm == 0.5µm — Python round() bankers rounding → 0
    assert mm_to_um(0.0005) == 0
    assert mm_to_um(0.0015) == 2  # 1.5 → 2
    assert mm_to_um(0.001) == 1


def test_length_tolerance_uses_floor_so_error_stays_within_limit() -> None:
    # 1.29 mm * 0.005 = 6.45 µm → floor → 6 µm
    tol = length_tolerance_um(1.29)
    assert tol == 6
    target_um = mm_to_um(1.29)
    assert tol / target_um <= LENGTH_TOLERANCE_FRACTION + 1e-9


def test_length_tolerance_minimum_one_um() -> None:
    # Tiny target: 0.001mm = 1µm; 1 * 0.005 = 0.005 → max(1, 0) = 1.
    assert length_tolerance_um(0.001) == 1


def test_mm_to_um_constants() -> None:
    assert MM_TO_UM == 1000
    assert LENGTH_TOLERANCE_FRACTION == 0.005
