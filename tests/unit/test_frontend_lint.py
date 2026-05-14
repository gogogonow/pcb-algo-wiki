from frontend.lint import TYPO_FIX_TABLE, lint_layout


def test_typo_fix_table_repairs_known_keys() -> None:
    raw = {
        "footprints": {
            "PKG_TP": {
                "dimensions": {"redius": 2.0},
                "pins": {"PIN_1": {"pad_geometry": {"shape": "cicle", "redius": 2.0}}},
            }
        }
    }
    fixed, report = lint_layout(raw)

    assert fixed["footprints"]["PKG_TP"]["dimensions"]["radius"] == 2.0
    assert (
        fixed["footprints"]["PKG_TP"]["pins"]["PIN_1"]["pad_geometry"]["shape"]
        == "circle"
    )
    assert (
        fixed["footprints"]["PKG_TP"]["pins"]["PIN_1"]["pad_geometry"]["radius"] == 2.0
    )
    typo_repairs = [r for r in report.repairs if r.kind == "typo"]
    befores = sorted(r.before for r in typo_repairs)
    assert befores == ["cicle", "redius", "redius"]


def test_unknown_bend_style_emits_warning() -> None:
    raw = {
        "edges": {
            "E1": {
                "type": "microstrip",
                "geometry": {
                    "bend_style": "curved",
                    "launch_rule": "normal",
                },
            }
        }
    }
    _, report = lint_layout(raw)
    codes = {w.code for w in report.warnings}
    assert "unknown_bend_style" in codes
    assert "unknown_launch_rule" in codes


def test_unknown_top_level_field_warns_and_passes_through() -> None:
    raw = {"metadata": {}, "experimental": {"foo": 1}}
    fixed, report = lint_layout(raw)
    assert fixed["experimental"] == {"foo": 1}
    codes = {w.code for w in report.warnings}
    assert "unknown_top_level_field" in codes


def test_typo_table_covers_known_entries() -> None:
    assert TYPO_FIX_TABLE["redius"] == "radius"
    assert TYPO_FIX_TABLE["cicle"] == "circle"


def test_non_mapping_input_is_passed_through() -> None:
    fixed, report = lint_layout([1, 2, 3])
    assert fixed == [1, 2, 3]
    assert report.repairs == ()
    assert report.warnings == ()
