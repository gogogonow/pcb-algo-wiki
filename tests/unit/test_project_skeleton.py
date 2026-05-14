import importlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_postproc_package_exports_m4_renderer() -> None:
    module = importlib.import_module("postproc")

    assert "render_geometry_svg" in module.__all__


def test_frontend_package_exports_m2_compiler() -> None:
    module = importlib.import_module("frontend")

    for name in (
        "compile_layout",
        "lint_layout",
        "expand_components",
        "build_obstacles",
        "triage_edges",
        "normalize_nodes",
        "FrontendArtifact",
    ):
        assert name in module.__all__


def test_schema_package_exports_schema_models() -> None:
    module = importlib.import_module("schema")

    assert module.__all__ == [
        "Board",
        "BranchConstraint",
        "ComponentPlacement",
        "ExpressionTerm",
        "GeometryIR",
        "PinPlacement",
        "PinPositionExpr",
        "PinPositionKind",
        "Point",
        "RotationDomain",
        "RoutePolyline",
        "RoutingClass",
        "SignedVKind",
        "SolverEdge",
        "SolverIR",
        "UniversalJunctionTemplate",
        "UvHostMatch",
        "UvResolution",
        "V33Layout",
        "V6Edge",
        "V6IR",
        "V6Node",
        "V6Terminal",
        "load_v33_layout",
    ]


def test_tools_package_resolves_to_src_package() -> None:
    module = importlib.import_module("tools")

    assert (
        Path(module.__file__).resolve() == REPO_ROOT / "src" / "tools" / "__init__.py"
    )
    # tools/__init__.py historically exported nothing; M4 added new CLI scripts
    # imported directly via "tools.cpsat_solve" but the package __all__ stays
    # empty by convention.
    assert module.__all__ == []


def test_solver_package_exports_m4_api() -> None:
    module = importlib.import_module("solver")

    for name in (
        "AuditReport",
        "CpsatModel",
        "audit_geometry",
        "build_model",
        "extract_geometry",
        "solve_model",
    ):
        assert name in module.__all__
