from pathlib import Path
from xml.etree import ElementTree as ET

import topology
from topology.loaders import load_topology_graph


REAL_CASE_PATH = Path(__file__).resolve().parents[2] / "rf_layout_simplified.yaml"


def test_real_case_render_includes_expected_labels() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)

    svg = topology.render_topology_svg(graph)
    labels = _text_labels(svg)

    assert {"IC1", "C1", "R2", "TP1", "IC1_pin1_seg1_universal_node"} <= labels


def test_render_adds_distinct_groups_and_classes() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)

    root = ET.fromstring(topology.render_topology_svg(graph))

    assert _classes(_find_by_id(root, "topology-edges")) == {"topology-edges"}
    assert _classes(_find_by_id(root, "topology-fixed-components")) == {
        "topology-fixed-components",
    }
    assert _classes(_find_by_id(root, "topology-uv-components")) == {"topology-uv-components"}
    assert _classes(_find_by_id(root, "topology-nodes")) == {"topology-nodes"}
    assert _classes(_find_by_id(root, "topology-terminals")) == {"topology-terminals"}

    assert len(_elements_with_class(root, "topology-fixed-component")) == len(graph.fixed_components)
    assert len(_elements_with_class(root, "topology-uv-component")) == len(
        graph.parametric_uv_components
    )
    assert len(_elements_with_class(root, "topology-node")) == len(graph.nodes)
    assert len(_elements_with_class(root, "topology-terminal")) == len(graph.terminals)
    assert len(_elements_with_class(root, "topology-edge")) == len(graph.edges)


def test_real_case_render_includes_all_edges_in_deterministic_order() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)

    first_svg = topology.render_topology_svg(graph)
    second_svg = topology.render_topology_svg(graph)

    assert first_svg == second_svg

    root = ET.fromstring(first_svg)
    edge_elements = _elements_with_class(root, "topology-edge")

    assert len(edge_elements) == 22
    assert [element.attrib["data-edge-id"] for element in edge_elements] == sorted(
        edge.id for edge in graph.edges
    )


def test_real_case_renders_distinct_component_pin_anchors_for_multi_pin_passives() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)
    root = ET.fromstring(topology.render_topology_svg(graph))

    c1_pin_1_anchor = _edge_endpoint_coordinates(root, graph, "IC1_pin1_seg4", "C1.PIN_1")
    c1_pin_2_anchor = _edge_endpoint_coordinates(root, graph, "C1_to_R1", "C1.PIN_2")

    assert c1_pin_1_anchor != c1_pin_2_anchor


def test_real_case_single_pin_component_terminals_anchor_to_component_pin() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)
    root = ET.fromstring(topology.render_topology_svg(graph))

    assert _edge_endpoint_coordinates(root, graph, "R2_to_TP1", "TP1.PIN_1") == ("142.0", "80.0")
    assert _edge_endpoint_coordinates(root, graph, "RF_INPUT_to_IC1", "TP3.PIN_1") == ("422.0", "80.0")
    assert _edge_endpoint_coordinates(root, graph, "IC1_pin1_seg6", "TP4.PIN_1") == ("562.0", "80.0")
    assert _edge_endpoint_coordinates(root, graph, "IC1_pin2_seg6", "TP5.PIN_1") == ("562.0", "580.0")


def _find_by_id(root: ET.Element, element_id: str) -> ET.Element:
    for element in root.iter():
        if element.attrib.get("id") == element_id:
            return element
    raise AssertionError(f"missing element {element_id!r}")


def _elements_with_class(root: ET.Element, class_name: str) -> list[ET.Element]:
    return [element for element in root.iter() if class_name in _classes(element)]


def _classes(element: ET.Element) -> set[str]:
    return set(element.attrib.get("class", "").split())


def _edge_endpoint_coordinates(
    root: ET.Element,
    graph: topology.TopologyGraph,
    edge_id: str,
    endpoint_id: str,
) -> tuple[str, str]:
    edge = graph.get_edge(edge_id)
    element = _find_edge(root, edge_id)
    if edge.source == endpoint_id:
        return element.attrib["x1"], element.attrib["y1"]
    if edge.target == endpoint_id:
        return element.attrib["x2"], element.attrib["y2"]
    raise AssertionError(f"edge {edge_id!r} does not reference endpoint {endpoint_id!r}")


def _find_edge(root: ET.Element, edge_id: str) -> ET.Element:
    for element in _elements_with_class(root, "topology-edge"):
        if element.attrib.get("data-edge-id") == edge_id:
            return element
    raise AssertionError(f"missing edge {edge_id!r}")


def _text_labels(svg: str) -> set[str]:
    root = ET.fromstring(svg)
    return {
        "".join(element.itertext()).strip()
        for element in root.iter()
        if _local_name(element.tag) == "text" and "".join(element.itertext()).strip()
    }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
