"""Gerber RS-274X export — **stub** for v7.

The current MVP (M6) does not emit Gerber files. This module exists to lock
the public API surface so downstream callers can wire pipeline plumbing
without waiting for the binary writer.

See ``concepts/output-stub-mapping.md`` for the planned field mapping
(GeometryIR → Gerber apertures / D-codes / regions).
"""

from __future__ import annotations

from pathlib import Path

from schema.geometry_ir import GeometryIR


def export_gerber_stub(geom: GeometryIR, out_dir: Path) -> None:
    """Raise :class:`NotImplementedError` — see module docstring."""

    raise NotImplementedError(
        "Gerber RS-274X export is scheduled for v7 (post-MVP). See "
        "concepts/output-stub-mapping.md for the planned mapping table. "
        f"Requested: project={geom.project!r} → {out_dir}"
    )


__all__ = ["export_gerber_stub"]
