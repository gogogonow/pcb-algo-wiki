"""Task 1 — segment intersection primitive used by floating placer crossing costs."""

from solver.v2.floating_placer import _segments_intersect


def test_x_cross_returns_true() -> None:
    assert _segments_intersect((0, 0), (2, 2), (0, 2), (2, 0)) is True


def test_parallel_returns_false() -> None:
    assert _segments_intersect((0, 0), (4, 0), (0, 1), (4, 1)) is False


def test_shared_endpoint_does_not_count() -> None:
    # Two segments meeting at (1, 0) — legal pin sharing, not a crossing.
    assert (
        _segments_intersect(
            (0, 0), (1, 0), (1, 0), (2, 1), ignore_shared_endpoints=True
        )
        is False
    )


def test_collinear_overlap_counts() -> None:
    # Segments lying on the same line with a real overlap region.
    assert _segments_intersect((0, 0), (3, 0), (1, 0), (4, 0)) is True


def test_t_junction_counts() -> None:
    # B endpoint touches interior of A — counts as cross (not a shared endpoint).
    assert _segments_intersect((0, 0), (4, 0), (2, 0), (2, 3)) is True


def test_disjoint_segments() -> None:
    assert _segments_intersect((0, 0), (1, 0), (3, 3), (4, 4)) is False
