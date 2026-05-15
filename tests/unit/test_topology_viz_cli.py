from pathlib import Path

import pytest

from tools.topology_viz import main

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CASE_PATH = REPO_ROOT / "rf_layout_simplified.yaml"


def test_main_writes_svg_to_explicit_output_path_and_creates_parent_dir(
    scratch_dir: Path,
) -> None:
    output_path = scratch_dir / "nested" / "explicit.svg"

    exit_code = main([str(REAL_CASE_PATH), str(output_path)])

    assert exit_code == 0
    assert output_path.exists()
    assert output_path.parent.exists()

    svg = output_path.read_text(encoding="utf-8")
    assert "<svg" in svg
    assert "IC1" in svg
    assert "R3" in svg


def test_main_defaults_real_case_output_path_from_project_name(
    scratch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(scratch_dir)

    exit_code = main([str(REAL_CASE_PATH)])

    output_path = scratch_dir / "out" / "PA_Module_Simplified.topology.svg"

    assert exit_code == 0
    assert output_path.exists()

    svg = output_path.read_text(encoding="utf-8")
    assert "<svg" in svg
    assert "IC1" in svg
    assert "C1" in svg


@pytest.mark.parametrize(
    "project_name,expected_name",
    [
        ("../../escape", "escape.topology.svg"),
        ("../nested/unsafe name", "nested_unsafe_name.topology.svg"),
    ],
)
def test_main_sanitizes_unsafe_project_name_for_default_output_path(
    scratch_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    project_name: str,
    expected_name: str,
) -> None:
    monkeypatch.chdir(scratch_dir)
    input_path = scratch_dir / "unsafe.yaml"
    input_path.write_text(
        f"metadata:\n  project_name: {project_name!r}\n",
        encoding="utf-8",
    )

    exit_code = main([str(input_path)])

    output_path = scratch_dir / "out" / expected_name

    assert exit_code == 0
    assert output_path.exists()
    assert output_path.is_relative_to(scratch_dir / "out")
