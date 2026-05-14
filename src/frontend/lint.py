"""Schema lint pass (M2a).

Walk the raw v3.3 dict, repair known typos, emit structured warnings for
unknown enum values / unknown top-level fields, and return both the repaired
dict and a ``LintReport`` snapshot.

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
from typing import Any

from .models import LintReport, LintRepair, LintWarning

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


__all__ = [
    "KNOWN_BEND_STYLES",
    "KNOWN_LAUNCH_RULES",
    "KNOWN_PAD_SHAPES",
    "TYPO_FIX_TABLE",
    "lint_layout",
]
