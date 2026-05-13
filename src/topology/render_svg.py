"""Deterministic SVG renderer for topology review."""

from __future__ import annotations

from collections.abc import Iterable
import math
from xml.etree import ElementTree as ET

from .model import TopologyComponent, TopologyGraph, TopologyPoint, TopologyTerminal

SVG_WIDTH = 900
SVG_HEIGHT = 760


def render_topology_svg(graph: TopologyGraph) -> str:
    """Render a minimal deterministic SVG for a topology graph."""

    positions = _build_positions(graph)

    root = ET.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "width": str(SVG_WIDTH),
            "height": str(SVG_HEIGHT),
            "viewBox": f"0 0 {SVG_WIDTH} {SVG_HEIGHT}",
            "role": "img",
            "aria-label": "topology graph",
        },
    )

    style = ET.SubElement(root, "style")
    style.text = """
.topology-edge { stroke: #667085; stroke-width: 2; fill: none; }
.topology-fixed-component-shape { fill: #dbeafe; stroke: #1d4ed8; stroke-width: 2; }
.topology-uv-component-shape { fill: #fef3c7; stroke: #b45309; stroke-width: 2; }
.topology-node-shape { fill: #dcfce7; stroke: #15803d; stroke-width: 2; }
.topology-terminal-shape { fill: #f3e8ff; stroke: #7e22ce; stroke-width: 2; }
.topology-label { fill: #111827; font-family: Arial, sans-serif; font-size: 11px; }
""".strip()

    edge_layer = ET.SubElement(
        root, "g", {"id": "topology-edges", "class": "topology-edges"}
    )
    fixed_layer = ET.SubElement(
        root,
        "g",
        {"id": "topology-fixed-components", "class": "topology-fixed-components"},
    )
    uv_layer = ET.SubElement(
        root,
        "g",
        {"id": "topology-uv-components", "class": "topology-uv-components"},
    )
    node_layer = ET.SubElement(
        root, "g", {"id": "topology-nodes", "class": "topology-nodes"}
    )
    terminal_layer = ET.SubElement(
        root,
        "g",
        {"id": "topology-terminals", "class": "topology-terminals"},
    )

    for edge in sorted(graph.edges, key=lambda item: item.id):
        source_ref = graph.resolve_endpoint(edge.source)
        target_ref = graph.resolve_endpoint(edge.target)
        source = _endpoint_position(source_ref, positions)
        target = _endpoint_position(target_ref, positions)
        ET.SubElement(
            edge_layer,
            "line",
            {
                "class": "topology-edge",
                "data-edge-id": edge.id,
                "data-kind": edge.kind,
                "x1": _fmt(source.x),
                "y1": _fmt(source.y),
                "x2": _fmt(target.x),
                "y2": _fmt(target.y),
            },
        )

    for component in sorted(graph.fixed_components, key=lambda item: item.id):
        _append_fixed_component(fixed_layer, component, positions[component.id])

    for component in sorted(graph.parametric_uv_components, key=lambda item: item.id):
        _append_uv_component(uv_layer, component, positions[component.id])

    for node in sorted(graph.nodes, key=lambda item: item.id):
        _append_node(node_layer, node.id, node.label or node.id, positions[node.id])

    for terminal in sorted(graph.terminals, key=lambda item: item.id):
        _append_terminal(terminal_layer, terminal, positions[terminal.id])

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode")


def _build_positions(graph: TopologyGraph) -> dict[str, TopologyPoint]:
    positions: dict[str, TopologyPoint] = {}

    fixed_defaults = _iter_grid_points(
        x_values=(140.0, 280.0, 420.0), y_start=120.0, y_step=90.0
    )
    for component in sorted(graph.fixed_components, key=lambda item: item.id):
        positions[component.id] = _scale_component_position(component.position) or next(
            fixed_defaults
        )

    uv_points = _iter_grid_points(x_values=(170.0, 710.0), y_start=140.0, y_step=78.0)
    for component in sorted(graph.parametric_uv_components, key=lambda item: item.id):
        positions[component.id] = next(uv_points)

    node_points = _iter_grid_points(x_values=(450.0,), y_start=120.0, y_step=72.0)
    for node in sorted(graph.nodes, key=lambda item: item.id):
        positions[node.id] = next(node_points)

    terminal_points = _iter_grid_points(x_values=(790.0,), y_start=120.0, y_step=54.0)
    for terminal in sorted(graph.terminals, key=lambda item: item.id):
        positions[terminal.id] = _terminal_position(terminal, positions, graph) or next(
            terminal_points
        )

    return positions


def _endpoint_position(
    endpoint_ref,
    positions: dict[str, TopologyPoint],
) -> TopologyPoint:
    if endpoint_ref.pin_id is None:
        return positions[endpoint_ref.id]
    return _component_pin_position(
        endpoint_ref.entity,
        endpoint_ref.pin_id,
        positions[endpoint_ref.entity_id],
    )


def _scale_component_position(position: TopologyPoint | None) -> TopologyPoint | None:
    if position is None:
        return None
    return TopologyPoint(x=100.0 + (position.x * 14.0), y=80.0 + (position.y * 5.0))


def _iter_grid_points(
    *,
    x_values: tuple[float, ...],
    y_start: float,
    y_step: float,
) -> Iterable[TopologyPoint]:
    row = 0
    while True:
        for x in x_values:
            yield TopologyPoint(x=x, y=y_start + (row * y_step))
        row += 1


def _terminal_position(
    terminal: TopologyTerminal,
    positions: dict[str, TopologyPoint],
    graph: TopologyGraph,
) -> TopologyPoint | None:
    owner_id, separator, pin_id = terminal.id.partition(".")
    if not separator:
        return None

    owner_position = positions.get(owner_id)
    if owner_position is None:
        return None

    try:
        owner = graph.get_component(owner_id)
    except KeyError:
        owner = None
    if owner is not None and len(owner.pin_ids) == 1 and pin_id in owner.pin_ids:
        return _component_pin_position(owner, pin_id, owner_position)

    offset_map = {
        "PIN_1": TopologyPoint(-55.0, -28.0),
        "PIN_2": TopologyPoint(-55.0, 28.0),
        "PIN_3": TopologyPoint(55.0, -28.0),
        "PIN_4": TopologyPoint(55.0, 28.0),
    }
    offset = offset_map.get(pin_id, TopologyPoint(0.0, -40.0))
    return TopologyPoint(x=owner_position.x + offset.x, y=owner_position.y + offset.y)


def _component_pin_position(
    component: TopologyComponent,
    pin_id: str,
    component_position: TopologyPoint,
) -> TopologyPoint:
    pin_order = component.pin_order or tuple(sorted(component.pin_ids))
    pin_index = pin_order.index(pin_id)
    angle = math.pi + ((2 * math.pi * pin_index) / len(pin_order))

    if component.placement_kind == "fixed":
        radius_x = 28.0
        radius_y = 18.0
    else:
        radius_x = 30.0
        radius_y = 18.0

    return TopologyPoint(
        x=component_position.x + (radius_x * math.cos(angle)),
        y=component_position.y + (radius_y * math.sin(angle)),
    )


def _append_fixed_component(
    layer: ET.Element, component: TopologyComponent, position: TopologyPoint
) -> None:
    group = ET.SubElement(
        layer,
        "g",
        {
            "class": "topology-component topology-fixed-component",
            "data-component-id": component.id,
        },
    )
    ET.SubElement(
        group,
        "rect",
        {
            "class": "topology-fixed-component-shape",
            "x": _fmt(position.x - 28.0),
            "y": _fmt(position.y - 18.0),
            "width": "56",
            "height": "36",
            "rx": "6",
        },
    )
    _append_label(group, component.label or component.id, position, dy=34.0)


def _append_uv_component(
    layer: ET.Element, component: TopologyComponent, position: TopologyPoint
) -> None:
    group = ET.SubElement(
        layer,
        "g",
        {
            "class": "topology-component topology-uv-component",
            "data-component-id": component.id,
        },
    )
    ET.SubElement(
        group,
        "ellipse",
        {
            "class": "topology-uv-component-shape",
            "cx": _fmt(position.x),
            "cy": _fmt(position.y),
            "rx": "30",
            "ry": "18",
        },
    )
    _append_label(group, component.label or component.id, position, dy=34.0)


def _append_node(
    layer: ET.Element, node_id: str, label: str, position: TopologyPoint
) -> None:
    group = ET.SubElement(
        layer,
        "g",
        {"class": "topology-node", "data-node-id": node_id},
    )
    ET.SubElement(
        group,
        "circle",
        {
            "class": "topology-node-shape",
            "cx": _fmt(position.x),
            "cy": _fmt(position.y),
            "r": "12",
        },
    )
    _append_label(group, label, position, dx=20.0, dy=4.0, anchor="start")


def _append_terminal(
    layer: ET.Element, terminal: TopologyTerminal, position: TopologyPoint
) -> None:
    group = ET.SubElement(
        layer,
        "g",
        {"class": "topology-terminal", "data-terminal-id": terminal.id},
    )
    points = [
        (position.x, position.y - 11.0),
        (position.x + 11.0, position.y),
        (position.x, position.y + 11.0),
        (position.x - 11.0, position.y),
    ]
    ET.SubElement(
        group,
        "polygon",
        {
            "class": "topology-terminal-shape",
            "points": " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in points),
        },
    )
    _append_label(
        group, terminal.label or terminal.id, position, dx=16.0, dy=4.0, anchor="start"
    )


def _append_label(
    group: ET.Element,
    label: str,
    position: TopologyPoint,
    *,
    dx: float = 0.0,
    dy: float = 0.0,
    anchor: str = "middle",
) -> None:
    text = ET.SubElement(
        group,
        "text",
        {
            "class": "topology-label",
            "x": _fmt(position.x + dx),
            "y": _fmt(position.y + dy),
            "text-anchor": anchor,
        },
    )
    text.text = label


def _fmt(value: float) -> str:
    return f"{value:.1f}"
