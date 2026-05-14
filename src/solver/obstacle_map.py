"""M9 — Octilinear router obstacle grid.

Builds a uniform µm-grid obstacle set used by :mod:`solver.astar_octilinear`.
Cells are represented as ``(ix, iy)`` integers where ``ix = x_um // step``
(see :func:`solver.astar_flex._cells_in_bbox` for the same convention).

Three obstacle sources are merged:

1. **Component footprint bboxes** — every component in
   :class:`frontend.models.FrontendArtifact.components` and ``uv_components``
   that exposes a ``bbox`` is rasterised after inflation by ``clearance + half_w``
   (``half_w`` is the largest microstrip half-width on the board, i.e. the
   worst-case route radius that may run alongside the device).

2. **Fixed pad halos** — every fixed terminal in :class:`schema.solver_ir.SolverIR`
   becomes a square obstacle of side ``2 × pad_clearance_um`` centred on its
   coordinate. Pads are not full polygons in the artifact, so we use a small
   safety square (default 0.6 mm side) as a stand-in. Start / goal cells of
   the active edge are exempted by :func:`solver.astar_octilinear`.

3. **Already-routed polylines** — every segment of a previously routed edge is
   inflated by ``half_route_width + clearance`` (axis-aligned bbox per segment;
   coarse but safe overapproximation for the M9 router).

The resulting :class:`ObstacleMap` is passed wholesale to A*; per-edge
exemptions (start/goal halo, ``skip_edge_id``) are handled at search time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from frontend.models import FrontendArtifact
from schema.geometry_ir import RoutePolyline
from schema.solver_ir import SolverIR

from .units import mm_to_um

DEFAULT_GRID_STEP_UM = 100
"""Default µm step (0.1 mm). PA board (40×100 mm) → 400×1000 cells."""

DEFAULT_PAD_HALO_UM = 300
"""Half-side of the square obstacle around each fixed pad (µm)."""


@dataclass(frozen=True)
class GridBounds:
    ix_min: int
    iy_min: int
    ix_max: int
    iy_max: int

    def contains(self, cell: tuple[int, int]) -> bool:
        return (
            self.ix_min <= cell[0] <= self.ix_max
            and self.iy_min <= cell[1] <= self.iy_max
        )


@dataclass
class ObstacleMap:
    step_um: int
    bounds: GridBounds
    obstacles: set[tuple[int, int]] = field(default_factory=set)

    def is_blocked(self, cell: tuple[int, int]) -> bool:
        return cell in self.obstacles

    def cell_count(self) -> int:
        return len(self.obstacles)


def _cells_in_bbox(
    bx0: int, by0: int, bx1: int, by1: int, step: int
) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    cx0, cy0 = bx0 // step, by0 // step
    cx1, cy1 = bx1 // step, by1 // step
    if cx1 < cx0:
        cx0, cx1 = cx1, cx0
    if cy1 < cy0:
        cy0, cy1 = cy1, cy0
    for ix in range(cx0, cx1 + 1):
        for iy in range(cy0, cy1 + 1):
            out.append((ix, iy))
    return out


def _board_grid(ir: SolverIR, step: int) -> GridBounds:
    bx0 = mm_to_um(float(ir.board.origin.x))
    by0 = mm_to_um(float(ir.board.origin.y))
    bx1 = bx0 + mm_to_um(float(ir.board.width))
    by1 = by0 + mm_to_um(float(ir.board.height))
    return GridBounds(
        ix_min=bx0 // step,
        iy_min=by0 // step,
        ix_max=bx1 // step,
        iy_max=by1 // step,
    )


def build_obstacle_map(
    *,
    ir: SolverIR,
    artifact: FrontendArtifact,
    routed: dict[str, RoutePolyline] | None = None,
    step_um: int = DEFAULT_GRID_STEP_UM,
    pad_halo_um: int = DEFAULT_PAD_HALO_UM,
    extra_inflate_um: int = 0,
    skip_terminals: frozenset[str] = frozenset(),
    skip_components: frozenset[str] = frozenset(),
) -> ObstacleMap:
    """Construct an :class:`ObstacleMap` from the inputs described above.

    ``routed`` is the dict of *already-placed* :class:`RoutePolyline` instances
    whose footprints become obstacles for the next edge. Pass ``None`` (the
    default) when bootstrapping the very first edge.

    ``extra_inflate_um`` is added to every obstacle source — useful for
    rip-up-and-reroute escalation passes that want to give the router more
    breathing room around hot spots.

    ``skip_terminals`` / ``skip_components`` exempt the active edge's own
    endpoints (and their host components) from the obstacle set so the trace
    can actually leave / enter the pad it owns.
    """

    bounds = _board_grid(ir, step_um)
    obstacles: set[tuple[int, int]] = set()
    clearance_um = mm_to_um(float(ir.clearance))

    # 1. Component bboxes.
    for source in (artifact.components, artifact.uv_components):
        for comp_name, comp in source.items():
            if comp.bbox is None or comp_name in skip_components:
                continue
            inflate = clearance_um + extra_inflate_um
            bx_lo = mm_to_um(float(comp.bbox.min_x)) - inflate
            by_lo = mm_to_um(float(comp.bbox.min_y)) - inflate
            bx_hi = mm_to_um(float(comp.bbox.max_x)) + inflate
            by_hi = mm_to_um(float(comp.bbox.max_y)) + inflate
            for cell in _cells_in_bbox(bx_lo, by_lo, bx_hi, by_hi, step_um):
                obstacles.add(cell)

    # 2. Fixed pad halos.
    for term_key, term in ir.terminals.items():
        if term_key in skip_terminals:
            continue
        cx = mm_to_um(float(term.point.x))
        cy = mm_to_um(float(term.point.y))
        halo = pad_halo_um + extra_inflate_um
        for cell in _cells_in_bbox(cx - halo, cy - halo, cx + halo, cy + halo, step_um):
            obstacles.add(cell)

    # 3. Already-routed polyline footprints.
    if routed:
        for route in routed.values():
            half_w = mm_to_um(float(route.width)) // 2
            inflate = half_w + clearance_um + extra_inflate_um
            pts = route.points
            for i in range(len(pts) - 1):
                ax = mm_to_um(float(pts[i].x))
                ay = mm_to_um(float(pts[i].y))
                bx = mm_to_um(float(pts[i + 1].x))
                by = mm_to_um(float(pts[i + 1].y))
                seg_lo_x = min(ax, bx) - inflate
                seg_lo_y = min(ay, by) - inflate
                seg_hi_x = max(ax, bx) + inflate
                seg_hi_y = max(ay, by) + inflate
                for cell in _cells_in_bbox(
                    seg_lo_x, seg_lo_y, seg_hi_x, seg_hi_y, step_um
                ):
                    obstacles.add(cell)

    return ObstacleMap(step_um=step_um, bounds=bounds, obstacles=obstacles)


__all__ = [
    "DEFAULT_GRID_STEP_UM",
    "DEFAULT_PAD_HALO_UM",
    "GridBounds",
    "ObstacleMap",
    "build_obstacle_map",
]
