import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "package_name",
    ["schema", "frontend", "solver", "postproc", "tools"],
)
def test_future_packages_are_importable_and_export_nothing(package_name: str) -> None:
    module = importlib.import_module(package_name)

    assert module.__all__ == []


def test_tools_package_resolves_to_src_package() -> None:
    module = importlib.import_module("tools")

    assert (
        Path(module.__file__).resolve() == REPO_ROOT / "src" / "tools" / "__init__.py"
    )
    assert module.__all__ == []
