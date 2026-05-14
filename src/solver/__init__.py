"""M4 solver package public API."""

from .audit import AuditReport, LengthCheck, OverlapPair, audit_geometry
from .cpsat import (
    CpsatModel,
    DEFAULT_NUM_WORKERS,
    DEFAULT_TIME_LIMIT_S,
    EndpointHandle,
    SolveResult,
    build_model,
    solve_model,
)
from .extract import extract_geometry
from .units import (
    LENGTH_TOLERANCE_FRACTION,
    MM_TO_UM,
    length_tolerance_um,
    mm_to_um,
    um_to_mm,
)

__all__ = [
    "AuditReport",
    "CpsatModel",
    "DEFAULT_NUM_WORKERS",
    "DEFAULT_TIME_LIMIT_S",
    "EndpointHandle",
    "LENGTH_TOLERANCE_FRACTION",
    "LengthCheck",
    "MM_TO_UM",
    "OverlapPair",
    "SolveResult",
    "audit_geometry",
    "build_model",
    "extract_geometry",
    "length_tolerance_um",
    "mm_to_um",
    "solve_model",
    "um_to_mm",
]
