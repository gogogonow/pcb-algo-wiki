"""Unit tests for ``solver.sa_floating`` (M5 Phase 1)."""

from __future__ import annotations

from pathlib import Path

from frontend.compile import compile_layout
from frontend.solver_ir import compile_solver_ir
from solver.sa_floating import (
    SaConfig,
    SaPlacement,
    compute_energy,
    hints_from_sa_result,
    run_sa,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PA_YAML = REPO_ROOT / "rf_layout_simplified.yaml"


def _load_pa() -> tuple:
    artifact = compile_layout(PA_YAML)
    ir = compile_solver_ir(PA_YAML)
    return ir, artifact


def test_sa_returns_placement_for_each_uv_component() -> None:
    ir, artifact = _load_pa()
    uv_count = len(ir.uv_resolutions)
    cfg = SaConfig(iterations=300, seed=42)
    result = run_sa(ir, artifact, cfg)
    assert len(result.placements) == uv_count
    for p in result.placements.values():
        assert p.side in (-1, 1)
        # Anchor must lie inside board envelope (µm).
        assert 0 <= p.anchor_x_um <= int(ir.board.width * 1000)
        assert 0 <= p.anchor_y_um <= int(ir.board.height * 1000)


def test_sa_energy_is_non_increasing_relative_to_initial() -> None:
    ir, artifact = _load_pa()
    cfg = SaConfig(iterations=500, seed=7)
    result = run_sa(ir, artifact, cfg)
    # Best energy returned must not be worse than the initial energy.
    assert result.final_energy <= result.initial_energy + 1e-6


def test_hints_from_sa_result_shape() -> None:
    ir, artifact = _load_pa()
    result = run_sa(ir, artifact, SaConfig(iterations=100, seed=1))
    hints = hints_from_sa_result(result)
    for comp_id, h in hints.items():
        assert set(h) == {"anchor_x_um", "anchor_y_um", "side"}
        assert isinstance(h["anchor_x_um"], int)
        assert isinstance(h["anchor_y_um"], int)
        assert h["side"] in (-1, 1)
        assert comp_id in result.placements


def test_compute_energy_decreases_when_anchor_moves_inside_board() -> None:
    ir, artifact = _load_pa()
    cfg = SaConfig(iterations=1, seed=0)
    res = run_sa(ir, artifact, cfg)
    if not res.placements:
        return
    # Move all anchors far outside the board → boundary penalty should rise.
    far = {
        comp: type(p)(
            anchor_x_um=p.anchor_x_um + 10_000_000,
            anchor_y_um=p.anchor_y_um + 10_000_000,
            side=p.side,
        )
        for comp, p in res.placements.items()
    }
    e_in = compute_energy(ir, artifact, res.placements, cfg)
    e_out = compute_energy(ir, artifact, far, cfg)
    assert e_out > e_in


def test_dispersion_energy_increases_when_close() -> None:
    """Dispersion energy is higher when anchors cluster than when spread out."""
    from solver.sa_floating import _dispersion_penalty

    ir, _ = _load_pa()
    close = {
        "a": SaPlacement(anchor_x_um=0, anchor_y_um=0, side=1),
        "b": SaPlacement(anchor_x_um=500, anchor_y_um=0, side=1),  # 0.5 mm apart
    }
    far = {
        "a": SaPlacement(anchor_x_um=0, anchor_y_um=0, side=1),
        "b": SaPlacement(anchor_x_um=100_000, anchor_y_um=0, side=1),  # 100 mm apart
    }
    e_close = _dispersion_penalty(ir, close, weight=1.0)
    e_far = _dispersion_penalty(ir, far, weight=1.0)
    assert e_close > e_far, f"expected {e_close} > {e_far}"


def test_crossing_weight_default_is_5000() -> None:
    assert SaConfig().crossing_weight == 5_000.0


def test_dispersion_weight_default_exists() -> None:
    cfg = SaConfig()
    assert hasattr(cfg, "dispersion_weight")
    assert cfg.dispersion_weight > 0.0
