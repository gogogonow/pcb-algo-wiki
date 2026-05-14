"""GDSII export — **stub** for v7.

The current MVP (M6) does not emit GDSII files. This module locks the public
API so pipeline integrations can be written ahead of the binary writer.

See ``concepts/output-stub-mapping.md`` for the planned mapping (GeometryIR
→ GDS layers / boundaries / paths).
"""

from __future__ import annotations

from pathlib import Path

from schema.geometry_ir import GeometryIR


def export_gds_stub(geom: GeometryIR, out_path: Path) -> None:
    """Raise :class:`NotImplementedError` — see module docstring."""

    raise NotImplementedError(
        "GDSII export is scheduled for v7 (post-MVP). See "
        "concepts/output-stub-mapping.md for the planned mapping table. "
        f"Requested: project={geom.project!r} → {out_path}"
    )


__all__ = ["export_gds_stub"]
