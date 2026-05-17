# PCB Phase Viewer — Design Specification

**Date:** 2026-05-17  
**Status:** Approved  
**Scope:** Interactive per-phase PCB layout viewer (static HTML + PixiJS)

---

## 1. Problem Statement

The `pcb_solve` pipeline already emits per-phase SVG snapshots, but SVGs are static: no zoom/pan, no
element picking, no property inspection, and no phase switching in a single view. Engineers need to
interactively explore each phase (preA / phaseA / phaseB / phaseC) to diagnose routing failures,
length errors, and component placement quality.

---

## 2. Goals & Non-Goals

**Goals**
- Single static HTML file, zero server dependency, opens directly in any browser
- Load one `{project}.viewer.json` bundle covering all four phases
- Multi-phase tab switching (preA → phaseA → phaseB → phaseC) with persistent zoom/pan
- Pan/zoom canvas (mouse wheel + drag)
- Layer filter tree (show/hide routes, UV components, floating components, failed edges)
- Hover tooltip (net name, width)
- Click-to-select: highlights selected element + all associated routes; dims others to 30%
- Right-side property inspector with per-element detail
- Render: board outline, component rectangles, routes, pads, failed edges

**Non-Goals**
- Multi-project comparison
- Export / screenshot
- DRC violation overlay
- Offline PixiJS bundle (CDN load is acceptable)

---

## 3. Architecture

```
pcb_solve rf_layout.yaml
  └─ _persist_phase_artefacts()
       └─ _emit_viewer_bundle()          ← new
            → out/{project}.viewer.json  ← single file to load

viewer/viewer.html                        ← new static HTML
  ├─ PixiJS 8.x (CDN)
  ├─ PhaseTabBar    — tab switching drives SceneRenderer
  ├─ DataLoader     — parses viewer.json into scene model
  ├─ SceneRenderer  — PixiJS Graphics draws all primitives
  ├─ PanZoom        — wheel zoom (cursor-centred) + drag pan
  ├─ InteractionMgr — hover tooltip + click picking
  ├─ FilterPanel    — left checkbox tree → layer visibility
  └─ PropertyInspector — right DOM panel, populated on click
```

---

## 4. `viewer.json` Schema

```jsonc
{
  "project": "PA_Module_Simplified",
  "board": { "width_mm": 40.0, "height_mm": 100.0 },

  "components": [
    {
      "ref": "IC1",
      "x_mm": 10.0, "y_mm": 20.0,
      "w_mm": 4.0,  "h_mm": 6.0,
      "rotation_deg": 0.0,
      "kind": "fixed",       // "fixed" | "uv" | "floating" | "unknown" (parametric_uv → uv)
      "pads": [
        { "pin": "PIN_1", "x_mm": 10.0, "y_mm": 18.5 }
      ]
    }
  ],

  "phases": {
    "preA": {
      "edges": [
        {
          "edge_id": "IC1_pin1_seg1",
          "routing_class": "rf_constrained_uv",
          "width_mm": 0.3,
          "target_length_mm": 6.0,
          "connections": ["IC1.PIN_1", "IC1_pin1_seg1_universal_node"],
          "endpoint_positions_mm": {
            "IC1.PIN_1": { "x": 10.0, "y": 18.5 }
          }
        }
      ]
    },
    "phaseA": {
      "routes": [
        {
          "edge_id": "IC1_pin1_seg1",
          "polyline_mm": [[9.2, 30.0], [9.2, 24.2]],
          "width_mm": 0.3,
          "success": true,
          "length_mm": 5.8,
          "target_mm": 6.0,
          "length_err_pct": -3.4,
          "failure_reason": null,
          "rip_up_round": 0
        }
      ]
    },
    "phaseB": {
      "routes": [],           // phaseA routes + any retry successes
      "uv_placements": [
        {
          "ref": "C1",
          "anchor_x_mm": 12.0, "anchor_y_mm": 22.0,
          "rotation_deg": 0.0,
          "pads": [{ "pin": "1", "x_mm": 11.5, "y_mm": 22.0 }]
        }
      ]
    },
    "phaseC": {
      "routes": [],           // full final route set
      "uv_placements": [],
      "flex_routes": [
        {
          "edge_id": "flex_ubias_rpull",
          "polyline_mm": [[5.0, 10.0], [8.0, 10.0]],
          "width_mm": 0.1,
          "success": true
        }
      ]
    }
  },

  "summary": { /* verbatim from summary.json */ }
}
```

**Design rules:**
- All coordinates in mm (µm → mm conversion done in the backend emitter)
- `phaseB.routes` and `phaseC.routes` include the full accumulated route set (not deltas);
  tab switching replaces the entire route list, no client-side diff required
- Components with unknown footprint dimensions get `w_mm: 1.0, h_mm: 1.0` fallback

---

## 5. Backend Changes

### 5.1 `_emit_viewer_bundle(result, out_dir, layout)` (new)

Location: `src/tools/pcb_solve_v2.py`

Called at the end of `_persist_phase_artefacts()`.  
Also called from the `--stop-after` early-exit paths (missing phases emit `[]` / `{}` stubs).

**Component footprint extraction:**  
`FrontendArtifact.components` is a `dict[str, ComponentExpansion]`.  
Each `ComponentExpansion` has:
- `bbox: BBox | None` — absolute-coordinate bounding box (`min_x/y`, `max_x/y` in mm); use this
  for component position and size: `x = (min_x+max_x)/2`, `y = (min_y+max_y)/2`,
  `w = max_x - min_x`, `h = max_y - min_y`. Falls back to `1.0 × 1.0` if `bbox` is `None`.
- `placement_kind: Literal["fixed", "parametric_uv", "floating", "unknown"]` — maps to viewer
  `kind` as: `parametric_uv → "uv"`, others pass through verbatim.
- `pads: tuple[ExpandedPad, ...]` — each with `abs_x`, `abs_y` (mm), `pin` name.

### 5.2 `_make_viewer_components(artifact)` (new helper)

Builds the `"components"` list from `FrontendArtifact.components` (a `dict[str, ComponentExpansion]`).
For each component:
- Position/size from `bbox` (absolute mm); fallback `1.0 × 1.0` if `None`
- `kind` mapped from `placement_kind`: `"parametric_uv" → "uv"`, others pass through
- `pads` from `ExpandedPad.abs_x / abs_y` (only pads where `abs_x` is not `None`)

### 5.3 `_collect_phase_routes(skeleton)` (new helper)

Converts `SkeletonReport.routes` to the `viewer.json` route list, applying `/ 1000` unit
conversion (`polyline_um → polyline_mm`) and rounding to 4 decimal places.

---

## 6. Frontend — `viewer/viewer.html`

Single file, ~600 lines. No build step. PixiJS 8.x loaded from `https://cdn.jsdelivr.net/npm/pixi.js@8/dist/pixi.min.js`.

### 6.1 Layout (CSS Grid)

```
┌─────────────────────────────────────────────────────────┐
│ Tab bar: [preA] [phaseA] [phaseB] [phaseC]  [Load ▶]   │  48px header
├──────────┬──────────────────────────────┬───────────────┤
│ Filter   │       PixiJS canvas          │  Property     │
│ Tree     │   (flex: 1 1 auto)           │  Inspector    │
│ 200px    │                              │  260px        │
└──────────┴──────────────────────────────┴───────────────┘
```

### 6.2 Rendering Layers (PixiJS Container stack, bottom to top)

1. `boardLayer` — board outline rectangle (grey stroke)
2. `componentLayer` — component rectangles (fill by kind: fixed=dark-grey, uv=green, floating=blue) + ref label
3. `routeLayer` — route polylines (colour by routing_class: rf_constrained=steelblue, flex=orange, failed=red dashed)
4. `padLayer` — pad circles (white fill, dark stroke, radius 0.3mm)
5. `highlightLayer` — yellow outline overlay for selected element (drawn on click)

### 6.3 Interaction

| Action | Behaviour |
|--------|-----------|
| Mouse wheel | Zoom in/out centred on cursor |
| Left-drag | Pan |
| Hover route | Tooltip: `Net: <edge_id> \| W: 0.15mm` |
| Hover component | Tooltip: `<ref> (<kind>) \| 4×6mm` |
| Click route | Highlight route (yellow), dim all others to α=0.25; open Property Inspector |
| Click component | Highlight component + all its connected routes; open Property Inspector |
| Click empty | Clear selection, restore α=1.0 for all |
| Filter checkbox | Toggle container visibility for that layer |
| Tab switch | Call `SceneRenderer.loadPhase(phaseData)`; canvas clears + redraws; pan/zoom preserved |

### 6.4 Hit Testing

PixiJS EventSystem handles `pointerover` / `pointerdown` events on each Graphics object.
Each drawn polyline segment registers as an interactive `Graphics` with `eventMode = 'static'`
and a thickened hit area (`lineWidth + 4px` in screen space) using `hitArea`.

### 6.5 Property Inspector

Pure DOM (no framework). On click, `PropertyInspector.show(item)` sets `innerHTML` of the
right panel `<div>`. On deselect, `PropertyInspector.hide()` collapses it.

---

## 7. Rendering Colour Scheme

| Element | Colour |
|---------|--------|
| Board outline | `#444` stroke, `#1a1a1a` fill |
| Fixed component | `#333` fill, `#888` stroke |
| UV component | `#1a4d2e` fill, `#4caf50` stroke |
| Floating component | `#0d2b4d` fill, `#2196f3` stroke |
| rf_constrained route | `#4a90d9` |
| flex route | `#f5a623` |
| Failed route | `#e74c3c` (dashed) |
| Pad | `#fff` fill, `#555` stroke |
| Selected highlight | `#f0c040` outline |
| Dimmed elements | α = 0.25 |
| Background | `#111` |

---

## 8. Out-of-Scope Items

- Offline PixiJS bundle (CDN only)
- DRC violation overlay
- Multi-project / split-screen comparison
- Export to PNG / PDF
- Undo/redo history

---

## 9. Deliverables

| # | Deliverable | Location |
|---|-------------|----------|
| 1 | `_emit_viewer_bundle()` + helpers | `src/tools/pcb_solve_v2.py` |
| 2 | Unit tests for bundle emitter | `tests/unit/v2/test_viewer_bundle.py` |
| 3 | `viewer.html` | `viewer/viewer.html` |
| 4 | Smoke test: load bundle, check key counts | `tests/unit/test_viewer_html_smoke.py` |
