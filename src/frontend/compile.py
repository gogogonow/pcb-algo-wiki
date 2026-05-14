"""M2 Frontend Compiler orchestration."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from schema.v33 import V33Layout

from .expand_components import expand_components
from .lint import lint_layout, lint_semantic
from .models import ExpandedPad, FrontendArtifact, LintReport
from .normalize_nodes import normalize_nodes
from .obstacles import build_obstacles
from .triage import triage_edges


def compile_layout(path: str | Path) -> FrontendArtifact:
    """Run the full M2 pipeline on a v3.3 YAML file and return the artifact."""

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    repaired, lint_report = lint_layout(raw)
    layout = V33Layout.model_validate(repaired)

    components = expand_components(layout)
    obstacles = build_obstacles(layout, components)

    fixed_terminals: dict[str, ExpandedPad] = {}
    uv_components: dict[str, Any] = {}
    for name, exp in components.items():
        if exp.placement_kind == "fixed":
            for pad in exp.pads:
                fixed_terminals[f"{name}.{pad.pin}"] = pad
        elif exp.placement_kind == "parametric_uv":
            uv_components[name] = exp

    edges = triage_edges(layout)
    nodes = normalize_nodes(layout)

    # M7: semantic cross-checks (pin_multi_net, length_infeasible_short, meander_required)
    sem_errors, sem_warnings = lint_semantic(layout, fixed_terminals)
    if sem_errors or sem_warnings:
        lint_report = LintReport(
            repairs=lint_report.repairs,
            warnings=lint_report.warnings + tuple(sem_warnings),
            errors=lint_report.errors + tuple(sem_errors),
        )

    board = layout.global_constraints.board_outline
    board_dict: dict[str, Any] = {}
    if board is not None:
        board_dict = {
            "type": board.type,
            "origin": (
                {"x": board.origin.x, "y": board.origin.y}
                if board.origin is not None
                else None
            ),
            "width": board.width,
            "height": board.height,
        }

    return FrontendArtifact(
        project_name=layout.metadata.project_name,
        board=board_dict,
        lint_report=lint_report,
        components=components,
        fixed_terminals=fixed_terminals,
        uv_components=uv_components,
        obstacles=obstacles,
        edges=edges,
        nodes=nodes,
    )


def artifact_to_dict(artifact: FrontendArtifact) -> dict[str, Any]:
    """Serialize a FrontendArtifact (and its dataclass children) to a JSON-safe dict."""

    return _to_jsonable(asdict(artifact))


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


__all__ = ["artifact_to_dict", "compile_layout"]
