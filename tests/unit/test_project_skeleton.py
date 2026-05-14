import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "package_name",
    ["solver", "postproc", "tools"],
)
def test_future_packages_are_importable_and_export_nothing(package_name: str) -> None:
    module = importlib.import_module(package_name)

    assert module.__all__ == []


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
        "Point",
        "RoutingClass",
        "V6Edge",
        "V6IR",
        "V6Node",
        "V6Terminal",
        "V33Layout",
        "load_v33_layout",
    ]


def test_tools_package_resolves_to_src_package() -> None:
    module = importlib.import_module("tools")

    assert (
        Path(module.__file__).resolve() == REPO_ROOT / "src" / "tools" / "__init__.py"
    )
    assert module.__all__ == []
