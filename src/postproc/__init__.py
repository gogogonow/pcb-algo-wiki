"""Post-processing package — M4 SVG + M6 bend / DRC / LVS + M7 meander."""

from .bend import BendReport, apply_bends
from .crossing_analysis import (
    AlgorithmicGap,
    CrossingReport,
    Hotspot,
    PairFact,
    analyze_crossings,
)
from .drc import DrcReport, DrcViolation, run_drc
from .geom_svg import render_geometry_svg
from .lvs import LvsMismatch, LvsReport, run_lvs
from .meander import MeanderEdgeResult, MeanderReport, apply_meanders

__all__ = [
    "AlgorithmicGap",
    "BendReport",
    "CrossingReport",
    "DrcReport",
    "DrcViolation",
    "Hotspot",
    "LvsMismatch",
    "LvsReport",
    "MeanderEdgeResult",
    "MeanderReport",
    "PairFact",
    "analyze_crossings",
    "apply_bends",
    "apply_meanders",
    "render_geometry_svg",
    "run_drc",
    "run_lvs",
]
