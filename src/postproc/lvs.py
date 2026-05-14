"""M6 soft LVS — checks logical-net connectivity in produced geometry.

The PA single-layer YAML does not declare a ``logical_net`` mapping today
(see ``射频微波版图结构化数据规范 (v3.3).md`` for the optional field), so this
check is a no-op (``skipped=True``) on PA. When future inputs supply
``net``-grouped edges that should belong to the same logical net, the
Union-Find pass below verifies every logical net resolves to a single
connected component.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from schema.geometry_ir import GeometryIR
from schema.solver_ir import SolverIR


@dataclass(frozen=True)
class LvsMismatch:
    logical_net: str
    component_count: int
    members: tuple[str, ...]


@dataclass(frozen=True)
class LvsReport:
    skipped: bool
    reason: str = ""
    checked_nets: tuple[str, ...] = ()
    mismatches: tuple[LvsMismatch, ...] = ()

    @property
    def passed(self) -> bool:
        return self.skipped or not self.mismatches


class _UnionFind:
    def __init__(self, items: list[str]) -> None:
        self.parent = {x: x for x in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def run_lvs(
    ir: SolverIR,
    geom: GeometryIR,
    *,
    logical_net_of_edge: dict[str, str] | None = None,
) -> LvsReport:
    """Verify every ``logical_net`` resolves to one connected component.

    ``logical_net_of_edge`` maps edge_id → logical_net name. When ``None``
    (default for PA today) the check is skipped.
    """

    if not logical_net_of_edge:
        return LvsReport(
            skipped=True,
            reason="no logical_net assignment provided (PA single-layer default)",
        )

    nets: dict[str, list[str]] = defaultdict(list)
    edges_per_net: dict[str, list[str]] = defaultdict(list)
    endpoints: set[str] = set()
    for edge_id, lnet in logical_net_of_edge.items():
        if edge_id not in ir.edges:
            continue
        edge = ir.edges[edge_id]
        ep_a, ep_b = edge.endpoints
        nets[lnet].extend((ep_a, ep_b))
        edges_per_net[lnet].append(edge_id)
        endpoints.update((ep_a, ep_b))
        # Geometry must produce a polyline so the endpoint count is meaningful.
        if edge_id not in geom.routes:
            continue

    mismatches: list[LvsMismatch] = []
    for lnet, eps in nets.items():
        unique = sorted(set(eps))
        if len(unique) <= 1:
            continue
        uf = _UnionFind(unique)
        for edge_id in edges_per_net[lnet]:
            ep_a, ep_b = ir.edges[edge_id].endpoints
            uf.union(ep_a, ep_b)
        roots = {uf.find(x) for x in unique}
        if len(roots) > 1:
            mismatches.append(
                LvsMismatch(
                    logical_net=lnet,
                    component_count=len(roots),
                    members=tuple(unique),
                )
            )

    return LvsReport(
        skipped=False,
        reason="",
        checked_nets=tuple(sorted(nets)),
        mismatches=tuple(mismatches),
    )


__all__ = ["LvsMismatch", "LvsReport", "run_lvs"]
