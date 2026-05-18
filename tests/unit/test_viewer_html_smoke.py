"""Smoke tests for viewer/viewer.html structural integrity."""

from __future__ import annotations

from pathlib import Path

VIEWER_HTML = Path(__file__).resolve().parents[2] / "viewer" / "viewer.html"


def test_viewer_html_exists() -> None:
    assert VIEWER_HTML.exists(), f"viewer.html not found at {VIEWER_HTML}"


def test_viewer_html_loads_pixijs() -> None:
    content = VIEWER_HTML.read_text()
    assert "cdn.jsdelivr.net" in content, "Missing jsdelivr CDN reference"
    assert "pixi.js" in content, "Missing pixi.js reference"


def test_viewer_html_has_phase_tabs() -> None:
    content = VIEWER_HTML.read_text()
    for tab in ("preA", "phaseA", "phaseB", "phaseC"):
        assert tab in content, f"Missing phase tab identifier: {tab}"


def test_viewer_html_has_canvas_container() -> None:
    content = VIEWER_HTML.read_text()
    assert 'id="canvas-container"' in content, "Missing canvas-container element"


def test_viewer_html_has_filter_panel() -> None:
    content = VIEWER_HTML.read_text()
    assert 'id="filter-panel"' in content, "Missing filter-panel element"


def test_viewer_html_has_prop_panel() -> None:
    content = VIEWER_HTML.read_text()
    assert 'id="prop-panel"' in content, "Missing prop-panel element"


def test_viewer_html_has_css_grid() -> None:
    content = VIEWER_HTML.read_text()
    assert "grid-template-columns" in content, "Missing CSS Grid layout"


def test_viewer_html_has_key_js_classes() -> None:
    content = VIEWER_HTML.read_text()
    for cls in (
        "SceneRenderer",
        "PanZoom",
        "FilterPanel",
        "PropertyInspector",
        "PhaseTabBar",
    ):
        assert cls in content, f"Missing JS class: {cls}"


def test_viewer_html_prea_scene_colors() -> None:
    content = VIEWER_HTML.read_text()
    for key in ("EDGE_SCENE1", "EDGE_SCENE2", "EDGE_SCENE3"):
        assert key in content, f"Missing scene color constant: {key}"
    assert "scene_1_fixed_tree" in content
    assert "scene_2_shunt_uv" in content
    assert "scene_3_floating" in content


def test_viewer_html_prea_list_panel() -> None:
    content = VIEWER_HTML.read_text()
    # HTML placeholder
    assert 'id="prea-list"' in content, "Missing #prea-list element"
    # CSS classes
    for cls in (".prea-item", ".prea-group", ".prea-group-hdr", ".prea-dot"):
        assert cls in content, f"Missing CSS class: {cls}"
    # JS class
    assert "PreAListPanel" in content, "Missing PreAListPanel class"
    assert "syncSelect" in content, "Missing syncSelect method"


def test_viewer_html_prea_reasoning() -> None:
    content = VIEWER_HTML.read_text()
    assert "prop-reasoning" in content, "Missing .prop-reasoning class"
    assert "_edgeReasoning" in content, "Missing _edgeReasoning method"
    assert "推理说明" in content, "Missing reasoning section label"
