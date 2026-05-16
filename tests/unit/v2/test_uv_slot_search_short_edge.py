"""WI-F3: ensure _sample_polyline produces ≥1 interior sample on short edges
so slot search does not silently fall back to legacy anchor placement."""
from src.solver.v2.uv_slot_search import _sample_polyline


def test_short_edge_yields_at_least_one_interior_sample():
    poly = [(0.0, 0.0), (1.3, 0.0)]
    samples = _sample_polyline(poly, step=0.8)
    # endpoints + at least one interior point
    assert len(samples) >= 3, f"expected >=3 samples, got {len(samples)}"
    interior_xs = [s[0][0] for s in samples[1:-1]]
    assert all(0.0 < x < 1.3 for x in interior_xs)


def test_long_edge_still_uses_step():
    poly = [(0.0, 0.0), (10.0, 0.0)]
    samples = _sample_polyline(poly, step=1.0)
    # ~10 segments → 11 samples
    assert len(samples) >= 10


def test_tiny_edge_min_two_segments():
    # Even a sub-step polyline now produces an interior sample (length 0.5,
    # step 0.8 → previously 1 segment / 2 samples / 0 interior).
    poly = [(0.0, 0.0), (0.5, 0.0)]
    samples = _sample_polyline(poly, step=0.8)
    assert len(samples) >= 3
