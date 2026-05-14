"""M5 — Phase 1 SA floating placement.

Provides a coarse pre-placement for ``universal`` (UV) components by minimising
a simple energy on their anchor positions. The output is a hint dictionary
that :func:`solver.cpsat.build_model` can feed to OR-Tools as a search seed
via ``CpModel.AddHint`` (see :func:`solver.cpsat.build_model`'s ``seed_hints``
parameter introduced in M5).

Energy components (all µm units):

* **HPWL** — half-perimeter wirelength over edges that connect each UV
  component's pads to its peer endpoints (other UV anchors, fixed terminals
  or junction centres approximated by the host_edge midpoint).
* **Boundary penalty** — quadratic for anchors outside the board envelope.
* **UV anchor attract** — pulls the anchor pin towards the host_edge midpoint
  (the M3 host_edge anchor is the natural starting point on the microstrip).
* **Soft pairwise repulsion** — quadratic on overlap of inflated UV bboxes
  (width = host_width + 2·clearance) so the seed avoids stacked anchors.

Metropolis–Hastings annealing with geometric cooling. Defaults are tuned
for the PA case (≤9 UV components) to converge in <50 ms; callers can pass
``temperature_init`` / ``cooling`` / ``iterations`` to scale up.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from frontend.models import FrontendArtifact
from schema.solver_ir import SolverIR

from .units import mm_to_um


@dataclass(frozen=True)
class SaPlacement:
    anchor_x_um: int
    anchor_y_um: int
    side: int  # ∈ {-1, +1}


@dataclass
class SaResult:
    placements: dict[str, SaPlacement] = field(default_factory=dict)
    final_energy: float = 0.0
    initial_energy: float = 0.0
    accepted: int = 0
    rejected: int = 0
    iterations: int = 0
    converged: bool = False


@dataclass(frozen=True)
class SaConfig:
    iterations: int = 2000
    temperature_init: float = 5_000.0
    temperature_min: float = 1.0
    cooling: float = 0.995
    step_um: int = 1_500  # 1.5mm
    boundary_weight: float = 100.0
    attract_weight: float = 1.0
    repulse_weight: float = 50.0
    seed: int | None = 0xC0FFEE


def _uv_neighbours(ir: SolverIR, comp_id: str) -> set[str]:
    """Return the set of endpoint names that connect to any pin of ``comp_id``.

    A neighbour is the *other* endpoint of every edge whose endpoints reference
    a pin of ``comp_id`` (in the form ``"<comp_id>.<pin>"``). The result is a
    flat set so HPWL can simply min/max across endpoint coordinates.
    """
    prefix = f"{comp_id}."
    neighbours: set[str] = set()
    for edge in ir.edges.values():
        ep_a, ep_b = edge.endpoints
        a_is_uv = ep_a.startswith(prefix)
        b_is_uv = ep_b.startswith(prefix)
        if a_is_uv and not b_is_uv:
            neighbours.add(ep_b)
        elif b_is_uv and not a_is_uv:
            neighbours.add(ep_a)
    return neighbours


def _endpoint_xy_um(
    ir: SolverIR,
    artifact: FrontendArtifact,
    placements: dict[str, SaPlacement],
    endpoint_name: str,
) -> tuple[int, int] | None:
    """Best-effort µm coordinate for a SolverIR endpoint during SA.

    * Fixed terminals → exact coordinate.
    * UV pins → current SA anchor (approximate; pin offset ignored — HPWL
      gradient is dominated by anchor placement).
    * Junction centres / free t-junction nodes → host_edge midpoint of the
      junction's reference edge if available; else None (skipped).
    """
    if endpoint_name in ir.terminals:
        t = ir.terminals[endpoint_name]
        return mm_to_um(float(t.point.x)), mm_to_um(float(t.point.y))

    if "." in endpoint_name:
        comp_id = endpoint_name.split(".", 1)[0]
        if comp_id in placements:
            p = placements[comp_id]
            return p.anchor_x_um, p.anchor_y_um

    # Junction or free node: approximate by midpoint of a connected edge whose
    # other endpoint is concrete (fixed terminal or UV anchor).
    for edge in ir.edges.values():
        ep_a, ep_b = edge.endpoints
        if endpoint_name not in (ep_a, ep_b):
            continue
        other = ep_b if ep_a == endpoint_name else ep_a
        if other == endpoint_name:
            continue
        oxy = _endpoint_xy_um_no_recurse(ir, placements, other)
        if oxy is not None:
            return oxy
    del artifact
    return None


def _endpoint_xy_um_no_recurse(
    ir: SolverIR,
    placements: dict[str, SaPlacement],
    endpoint_name: str,
) -> tuple[int, int] | None:
    """Concrete-only resolver used to avoid infinite recursion."""
    if endpoint_name in ir.terminals:
        t = ir.terminals[endpoint_name]
        return mm_to_um(float(t.point.x)), mm_to_um(float(t.point.y))
    if "." in endpoint_name:
        comp_id = endpoint_name.split(".", 1)[0]
        if comp_id in placements:
            p = placements[comp_id]
            return p.anchor_x_um, p.anchor_y_um
    return None


def _hpwl_for_uv(
    ir: SolverIR,
    artifact: FrontendArtifact,
    placements: dict[str, SaPlacement],
    comp_id: str,
) -> float:
    """HPWL over all endpoints connected to ``comp_id`` (incl. its anchor)."""
    p = placements[comp_id]
    xs = [p.anchor_x_um]
    ys = [p.anchor_y_um]
    for nb in _uv_neighbours(ir, comp_id):
        xy = _endpoint_xy_um(ir, artifact, placements, nb)
        if xy is None:
            continue
        xs.append(xy[0])
        ys.append(xy[1])
    if len(xs) < 2:
        return 0.0
    return float((max(xs) - min(xs)) + (max(ys) - min(ys)))


def _boundary_penalty(ir: SolverIR, p: SaPlacement, weight: float) -> float:
    bx0 = mm_to_um(float(ir.board.origin.x))
    by0 = mm_to_um(float(ir.board.origin.y))
    bx1 = bx0 + mm_to_um(float(ir.board.width))
    by1 = by0 + mm_to_um(float(ir.board.height))
    over = 0.0
    if p.anchor_x_um < bx0:
        over += (bx0 - p.anchor_x_um) ** 2
    if p.anchor_x_um > bx1:
        over += (p.anchor_x_um - bx1) ** 2
    if p.anchor_y_um < by0:
        over += (by0 - p.anchor_y_um) ** 2
    if p.anchor_y_um > by1:
        over += (p.anchor_y_um - by1) ** 2
    return weight * over / 1_000_000.0


def _attract_to_host_midpoint(
    ir: SolverIR,
    artifact: FrontendArtifact,
    placements: dict[str, SaPlacement],
    comp_id: str,
    weight: float,
) -> float:
    """Quadratic pull towards the host_edge midpoint."""
    uv = ir.uv_resolutions[comp_id]
    host_edge = ir.edges.get(uv.host_edge_id)
    if host_edge is None:
        return 0.0
    ep_a, ep_b = host_edge.endpoints
    a_xy = _endpoint_xy_um_no_recurse(ir, placements, ep_a)
    b_xy = _endpoint_xy_um_no_recurse(ir, placements, ep_b)
    if a_xy is None or b_xy is None:
        del artifact
        return 0.0
    mx = (a_xy[0] + b_xy[0]) / 2.0
    my = (a_xy[1] + b_xy[1]) / 2.0
    p = placements[comp_id]
    dx = p.anchor_x_um - mx
    dy = p.anchor_y_um - my
    return weight * (dx * dx + dy * dy) / 1_000_000.0


def _pairwise_repulse(
    ir: SolverIR,
    placements: dict[str, SaPlacement],
    weight: float,
) -> float:
    """Quadratic repulsion when two UV anchors get within (host_width+clearance)."""
    items = list(placements.items())
    energy = 0.0
    for i in range(len(items)):
        ai, pa = items[i]
        ra = mm_to_um(
            float(ir.uv_resolutions[ai].host_width) / 2.0
            + float(ir.uv_resolutions[ai].clearance)
        )
        for j in range(i + 1, len(items)):
            bj, pb = items[j]
            rb = mm_to_um(
                float(ir.uv_resolutions[bj].host_width) / 2.0
                + float(ir.uv_resolutions[bj].clearance)
            )
            min_dist = ra + rb
            dx = pa.anchor_x_um - pb.anchor_x_um
            dy = pa.anchor_y_um - pb.anchor_y_um
            d = math.hypot(dx, dy)
            if d < min_dist:
                energy += (min_dist - d) ** 2
    return weight * energy / 1_000_000.0


def compute_energy(
    ir: SolverIR,
    artifact: FrontendArtifact,
    placements: dict[str, SaPlacement],
    cfg: SaConfig,
) -> float:
    """Sum of HPWL + boundary + attract + repulse over all UV components."""
    e = 0.0
    for comp_id, p in placements.items():
        e += _hpwl_for_uv(ir, artifact, placements, comp_id)
        e += _boundary_penalty(ir, p, cfg.boundary_weight)
        e += _attract_to_host_midpoint(
            ir, artifact, placements, comp_id, cfg.attract_weight
        )
    e += _pairwise_repulse(ir, placements, cfg.repulse_weight)
    return e


def _initial_placements(
    ir: SolverIR, artifact: FrontendArtifact
) -> dict[str, SaPlacement]:
    """Seed each UV anchor at its host_edge midpoint (or board centre)."""
    out: dict[str, SaPlacement] = {}
    bx0 = mm_to_um(float(ir.board.origin.x))
    by0 = mm_to_um(float(ir.board.origin.y))
    bx1 = bx0 + mm_to_um(float(ir.board.width))
    by1 = by0 + mm_to_um(float(ir.board.height))
    cx = (bx0 + bx1) // 2
    cy = (by0 + by1) // 2

    # First pass: place all UV at board centre so we can resolve neighbours.
    for comp_id, uv in ir.uv_resolutions.items():
        out[comp_id] = SaPlacement(cx, cy, side=int(uv.offset_v_sides[0]))

    # Second pass: snap to host_edge midpoint when both endpoints are concrete.
    for comp_id, uv in ir.uv_resolutions.items():
        host_edge = ir.edges.get(uv.host_edge_id)
        if host_edge is None:
            continue
        ep_a, ep_b = host_edge.endpoints
        a_xy = _endpoint_xy_um_no_recurse(ir, out, ep_a)
        b_xy = _endpoint_xy_um_no_recurse(ir, out, ep_b)
        if a_xy is None or b_xy is None:
            continue
        out[comp_id] = SaPlacement(
            anchor_x_um=(a_xy[0] + b_xy[0]) // 2,
            anchor_y_um=(a_xy[1] + b_xy[1]) // 2,
            side=int(uv.offset_v_sides[0]),
        )
    del artifact
    return out


def run_sa(
    ir: SolverIR,
    artifact: FrontendArtifact,
    cfg: SaConfig | None = None,
) -> SaResult:
    """Run Metropolis–Hastings annealing on UV anchor positions.

    Returns an :class:`SaResult` whose ``placements`` is suitable for passing
    as ``seed_hints`` to :func:`solver.cpsat.build_model`. When the IR has no
    UV components the result is empty (with both energies = 0).
    """
    cfg = cfg or SaConfig()
    rng = random.Random(cfg.seed)
    placements = _initial_placements(ir, artifact)
    if not placements:
        return SaResult()

    current_e = compute_energy(ir, artifact, placements, cfg)
    initial_e = current_e
    best_e = current_e
    best_placements = dict(placements)
    accepted = 0
    rejected = 0
    temperature = cfg.temperature_init
    bx0 = mm_to_um(float(ir.board.origin.x))
    by0 = mm_to_um(float(ir.board.origin.y))
    bx1 = bx0 + mm_to_um(float(ir.board.width))
    by1 = by0 + mm_to_um(float(ir.board.height))

    keys = list(placements.keys())
    for it in range(cfg.iterations):
        comp_id = rng.choice(keys)
        old = placements[comp_id]
        dx = rng.randint(-cfg.step_um, cfg.step_um)
        dy = rng.randint(-cfg.step_um, cfg.step_um)
        new_x = max(bx0, min(bx1, old.anchor_x_um + dx))
        new_y = max(by0, min(by1, old.anchor_y_um + dy))
        # Occasional side flip (1% of moves).
        new_side = -old.side if rng.random() < 0.01 else old.side
        candidate = SaPlacement(new_x, new_y, new_side)

        placements[comp_id] = candidate
        new_e = compute_energy(ir, artifact, placements, cfg)
        delta = new_e - current_e
        if delta <= 0 or rng.random() < math.exp(-delta / max(temperature, 1e-9)):
            current_e = new_e
            accepted += 1
            if new_e < best_e:
                best_e = new_e
                best_placements = dict(placements)
        else:
            placements[comp_id] = old
            rejected += 1

        temperature = max(cfg.temperature_min, temperature * cfg.cooling)
        if temperature <= cfg.temperature_min and it > cfg.iterations // 2:
            break

    return SaResult(
        placements=best_placements,
        final_energy=best_e,
        initial_energy=initial_e,
        accepted=accepted,
        rejected=rejected,
        iterations=accepted + rejected,
        converged=best_e < initial_e,
    )


def hints_from_sa_result(result: SaResult) -> dict[str, dict[str, int]]:
    """Pack :class:`SaResult` into the dict shape ``build_model`` consumes."""
    return {
        comp_id: {
            "anchor_x_um": p.anchor_x_um,
            "anchor_y_um": p.anchor_y_um,
            "side": int(p.side),
        }
        for comp_id, p in result.placements.items()
    }


__all__ = [
    "SaConfig",
    "SaPlacement",
    "SaResult",
    "compute_energy",
    "hints_from_sa_result",
    "run_sa",
]
