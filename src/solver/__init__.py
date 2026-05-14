"""M4 + M5 solver package public API."""

from .astar_flex import AstarConfig, AstarReport, route_flexible_paths
from .astar_octilinear import OctilinearConfig, OctilinearResult
from .audit import (
    AuditReport,
    LengthCheck,
    OverlapPair,
    audit_geometry,
    polyline_length,
)
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
from .obstacle_map import (
    DEFAULT_GRID_STEP_UM,
    DEFAULT_PAD_HALO_UM,
    GridBounds,
    ObstacleMap,
    build_obstacle_map,
)
from .orchestrator import OrchestratorOptions, OrchestratorResult, solve_layout
from .route_orchestrator import (
    RouteOrchestratorConfig,
    RouteOrchestratorReport,
    route_all,
)
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
    "DEFAULT_GRID_STEP_UM",
    "DEFAULT_NUM_WORKERS",
    "DEFAULT_PAD_HALO_UM",
    "DEFAULT_TIME_LIMIT_S",
    "EndpointHandle",
    "GridBounds",
    "LENGTH_TOLERANCE_FRACTION",
    "LengthCheck",
    "MM_TO_UM",
    "ObstacleMap",
    "OctilinearConfig",
    "OctilinearResult",
    "OrchestratorOptions",
    "OrchestratorResult",
    "OverlapPair",
    "RouteOrchestratorConfig",
    "RouteOrchestratorReport",
    "SaConfig",
    "SaPlacement",
    "SaResult",
    "SolveResult",
    "audit_geometry",
    "build_model",
    "build_obstacle_map",
    "compute_energy",
    "extract_geometry",
    "hints_from_sa_result",
    "length_tolerance_um",
    "mm_to_um",
    "polyline_length",
    "route_all",
    "route_flexible_paths",
    "run_sa",
    "solve_layout",
    "solve_model",
    "um_to_mm",
]
