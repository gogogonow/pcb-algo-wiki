import importlib

import pytest


@pytest.mark.parametrize(
    "package_name",
    ["schema", "frontend", "solver", "postproc", "tools"],
)
def test_future_packages_are_importable_and_export_nothing(package_name: str) -> None:
    module = importlib.import_module(package_name)

    assert module.__all__ == []
