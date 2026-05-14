"""Schema lint pass (M2a) + semantic lint (M7).

Walk the raw v3.3 dict, repair known typos, emit structured warnings for
unknown enum values / unknown top-level fields, and return both the repaired
dict and a ``LintReport`` snapshot.

M7 adds ``lint_semantic()``:
* ``pin_multi_net`` (Error): same terminal appears in edges with conflicting nets.
* ``length_infeasible_short`` (Error): target_length < Manhattan(fixed endpoints).
* ``meander_required`` (Warning): target_length > Manhattan(fixed endpoints).

Design notes:

* The lint pass runs **before** pydantic parsing so typo keys are corrected
  in-place; the v3.3 schema itself stays lenient (``extra="allow"``).
* Unknown enum values for ``bend_style`` / ``launch_rule`` /
  ``pad_geometry.shape`` are not blocking — they are passed through verbatim
  so M6 post-processing can decide how to render them.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
from typing import TYPE_CHECKING, Any

from .models import LintError, LintReport, LintRepair, LintWarning

if TYPE_CHECKING:
    from schema.v33 import V33Layout

    from .models import ExpandedPad

TYPO_FIX_TABLE: dict[str, str] = {
    "redius": "radius",
    "cicle": "circle",
    "lenght": "length",
    "widht": "width",
    "hieght": "height",
}

KNOWN_BEND_STYLES: frozenset[str] = frozenset({"mitered_45", "square", "arc", "none"})
KNOWN_LAUNCH_RULES: frozenset[str] = frozenset({"taper", "stub", "default"})
KNOWN_PAD_SHAPES: frozenset[str] = frozenset({"rect", "circle", "oval", "polygon"})

KNOWN_TOP_LEVEL_FIELDS: frozenset[str] = frozenset(
    {
        "metadata",
        "global_constraints",
        "footprints",
        "components",
        "nodes",
        "terminals",
        "edges",
    }
)


def lint_layout(raw: Any) -> tuple[Any, LintReport]:
    """Return ``(repaired_data, lint_report)`` for a parsed YAML mapping."""

    repairs: list[LintRepair] = []
    warnings: list[LintWarning] = []

    if not isinstance(raw, Mapping):
        return raw, LintReport()

    repaired = copy.deepcopy(dict(raw))

    for key in list(repaired.keys()):
        if key not in KNOWN_TOP_LEVEL_FIELDS:
            warnings.append(
                LintWarning(
                    field_path=key,
                    code="unknown_top_level_field",
                    message=f"unknown top-level field {key!r} (passed through)",
                )
            )

    _walk(repaired, "", repairs, warnings)

    return repaired, LintReport(
        repairs=tuple(repairs),
        warnings=tuple(warnings),
        errors=(),
    )


def _walk(
    obj: Any,
    path: str,
    repairs: list[LintRepair],
    warnings: list[LintWarning],
) -> None:
    if isinstance(obj, dict):
        # Repair typo keys first so we then walk the corrected structure.
        for key in list(obj.keys()):
            if key in TYPO_FIX_TABLE:
                fixed_key = TYPO_FIX_TABLE[key]
                obj[fixed_key] = obj.pop(key)
                repairs.append(
                    LintRepair(
                        field_path=_join(path, key),
                        before=key,
                        after=fixed_key,
                        kind="typo",
                    )
                )
        # Repair typo string values, then re-check enum knowledge with the
        # corrected value so we don't double-report a "fixed" typo as unknown.
        for key in list(obj.keys()):
            value = obj[key]
            if isinstance(value, str) and value in TYPO_FIX_TABLE:
                fixed_value = TYPO_FIX_TABLE[value]
                obj[key] = fixed_value
                repairs.append(
                    LintRepair(
                        field_path=_join(path, str(key)),
                        before=value,
                        after=fixed_value,
                        kind="typo",
                    )
                )
        for key, value in obj.items():
            child_path = _join(path, str(key))
            _check_known_enum(child_path, key, value, warnings)
            _walk(value, child_path, repairs, warnings)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _walk(value, f"{path}[{index}]", repairs, warnings)


def _check_known_enum(
    field_path: str,
    key: Any,
    value: Any,
    warnings: list[LintWarning],
) -> None:
    if not isinstance(key, str) or not isinstance(value, str):
        return
    if key == "bend_style" and value not in KNOWN_BEND_STYLES:
        warnings.append(
            LintWarning(
                field_path=field_path,
                code="unknown_bend_style",
                message=f"unknown bend_style {value!r} (passed through)",
            )
        )
    elif key == "launch_rule" and value not in KNOWN_LAUNCH_RULES:
        warnings.append(
            LintWarning(
                field_path=field_path,
                code="unknown_launch_rule",
                message=f"unknown launch_rule {value!r} (passed through)",
            )
        )
    elif key == "shape" and value not in KNOWN_PAD_SHAPES:
        warnings.append(
            LintWarning(
                field_path=field_path,
                code="unknown_pad_shape",
                message=f"unknown pad shape {value!r} (passed through)",
            )
        )


def _join(parent: str, child: str) -> str:
    if not parent:
        return child
    return f"{parent}.{child}"


# ---------------------------------------------------------------------------
# M7: Semantic lint (runs on parsed V33Layout + expanded fixed terminals)
# ---------------------------------------------------------------------------

_LENGTH_TOL_MM = 0.5  # allowable error before flagging infeasible/meander


def lint_semantic(
    layout: "V33Layout",
    fixed_terminals: "dict[str, ExpandedPad]",
) -> tuple[list[LintError], list[LintWarning]]:
    """Return (errors, warnings) from semantic cross-checks.

    Rules:
    - pin_multi_net (Error): same ``comp.PIN`` terminal in edges with different nets.
    - length_infeasible_short (Error): target_length < Manhattan distance between
      fixed endpoints by more than ``_LENGTH_TOL_MM``.
    - meander_required (Warning): target_length > Manhattan distance (will need
      serpentine routing to add extra length).
    """
    errors: list[LintError] = []
    warnings: list[LintWarning] = []

    comp_ids: set[str] = set(layout.components.keys())

    # ------------------------------------------------------------------
    # Rule: pin_multi_net
    # ------------------------------------------------------------------
    terminal_nets: dict[str, set[str]] = {}
    terminal_edges: dict[str, list[str]] = {}
    for edge_name, edge in layout.edges.items():
        net = edge.net or ""
        if not net:
            continue
        for conn in edge.connections:
            if "." not in conn:
                continue
            comp_id, _pin = conn.split(".", 1)
            if comp_id not in comp_ids:
                continue  # node reference, not a component terminal
            terminal_nets.setdefault(conn, set()).add(net)
            terminal_edges.setdefault(conn, []).append(edge_name)

    for terminal, nets in sorted(terminal_nets.items()):
        if len(nets) > 1:
            edges_involved = terminal_edges.get(terminal, [])
            errors.append(
                LintError(
                    field_path=f"edges.*.connections[{terminal}]",
                    code="pin_multi_net",
                    message=(
                        f"terminal {terminal!r} appears in {len(nets)} conflicting "
                        f"nets {sorted(nets)!r} across edges {edges_involved!r}"
                    ),
                )
            )

    # ------------------------------------------------------------------
    # Rule: length_infeasible_short / meander_required
    # ------------------------------------------------------------------
    for edge_name, edge in layout.edges.items():
        if edge.constraint is None or edge.constraint.target_length is None:
            continue
        target = float(edge.constraint.target_length)

        # Collect only connections that resolve to fixed pads with coordinates.
        fixed_pads = []
        for conn in edge.connections:
            pad = fixed_terminals.get(conn)
            if pad is None or pad.abs_x is None or pad.abs_y is None:
                break  # non-fixed endpoint → skip this edge
            fixed_pads.append(pad)
        else:
            # All connections are fixed pads; at least two needed.
            if len(fixed_pads) < 2:
                continue
            # Use first ↔ last endpoint Manhattan distance.
            p1, p2 = fixed_pads[0], fixed_pads[-1]
            assert (
                p1.abs_x is not None
                and p1.abs_y is not None
                and p2.abs_x is not None
                and p2.abs_y is not None
            )
            manhattan = abs(p1.abs_x - p2.abs_x) + abs(p1.abs_y - p2.abs_y)

            field = f"edges.{edge_name}.constraint.target_length"
            if target < manhattan - _LENGTH_TOL_MM:
                errors.append(
                    LintError(
                        field_path=field,
                        code="length_infeasible_short",
                        message=(
                            f"target_length={target}mm < Manhattan={manhattan:.2f}mm "
                            f"for fixed endpoints {edge.connections[0]!r} → "
                            f"{edge.connections[-1]!r}; routing cannot achieve this length"
                        ),
                    )
                )
            elif target > manhattan + _LENGTH_TOL_MM:
                warnings.append(
                    LintWarning(
                        field_path=field,
                        code="meander_required",
                        message=(
                            f"target_length={target}mm > Manhattan={manhattan:.2f}mm; "
                            f"meander will add {target - manhattan:.2f}mm via U-loops"
                        ),
                    )
                )

    return errors, warnings


__all__ = [
    "KNOWN_BEND_STYLES",
    "KNOWN_LAUNCH_RULES",
    "KNOWN_PAD_SHAPES",
    "TYPO_FIX_TABLE",
    "lint_layout",
    "lint_semantic",
]
