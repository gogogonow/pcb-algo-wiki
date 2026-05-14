"""M4 + M5 solver package public API."""

from .astar_flex import AstarConfig, AstarReport, route_flexible_paths
from .audit import AuditReport, LengthCheck, OverlapPair, audit_geometry
from .cpsat import (
    DEFAULT_NUM_WORKERS,
    DEFAULT_TIME_LIMIT_S,
    CpsatModel,
    EndpointHandle,
    SolveResult,
    build_model,
    solve_model,
)
from .extract import extract_geometry
from .orchestrator import OrchestratorOptions, OrchestratorResult, solve_layout
from .sa_floating import (
    SaConfig,
    SaPlacement,
    SaResult,
    compute_energy,
    hints_from_sa_result,
    run_sa,
)
from .units import (
    LENGTH_TOLERANCE_FRACTION,
    MM_TO_UM,
    length_tolerance_um,
    mm_to_um,
    um_to_mm,
)

__all__ = [
    "AstarConfig",
    "AstarReport",
    "AuditReport",
    "CpsatModel",
    "DEFAULT_NUM_WORKERS",
    "DEFAULT_TIME_LIMIT_S",
    "EndpointHandle",
    "LENGTH_TOLERANCE_FRACTION",
    "LengthCheck",
    "MM_TO_UM",
    "OrchestratorOptions",
    "OrchestratorResult",
    "OverlapPair",
    "SaConfig",
    "SaPlacement",
    "SaResult",
    "SolveResult",
    "audit_geometry",
    "build_model",
    "compute_energy",
    "extract_geometry",
    "hints_from_sa_result",
    "length_tolerance_um",
    "mm_to_um",
    "route_flexible_paths",
    "run_sa",
    "solve_layout",
    "solve_model",
    "um_to_mm",
]
