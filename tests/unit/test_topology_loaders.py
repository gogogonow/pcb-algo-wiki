from pathlib import Path

import pytest

from schema.v33 import load_v33_layout
from topology.loaders import extract_topology_graph, load_topology_graph

REAL_CASE_PATH = Path(__file__).resolve().parents[2] / "rf_layout_simplified.yaml"


def test_real_case_extracts_expected_entity_counts() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)
    layout = load_v33_layout(REAL_CASE_PATH)

    composite_endpoints = {
        endpoint
        for edge in layout.edges.values()
        for endpoint in edge.connections
        if isinstance(endpoint, str) and "," in endpoint
    }

    assert len(graph.components) == len(layout.components)
    assert len(graph.fixed_components) + len(graph.parametric_uv_components) == len(
        layout.components
    )
    assert len(graph.parametric_uv_components) >= 1
    assert len(graph.nodes) == len(layout.nodes) + len(composite_endpoints)
    assert len(graph.terminals) == 9
    assert len(graph.edges) == len(layout.edges)


def test_real_case_preserves_named_entities_and_classification() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)

    assert graph.get_component("IC1").placement_kind == "fixed"
    assert graph.get_component("C1").placement_kind == "parametric_uv"
    assert graph.get_component("R3").placement_kind == "parametric_uv"

    assert graph.get_node("IC1_pin1_seg1_universal_node").kind == "universal_junction"
    assert graph.get_node("IC1_pin2_seg1_universal_node").kind == "universal_junction"
    assert all(
        node.kind
        in {
            "universal_junction",
            "t_junction",
            "t_combiner_junction",
            "composite_endpoint",
            "node",
        }
        for node in graph.nodes
    )

    assert graph.get_edge("IC1_pin1_seg1").kind == "microstrip"


def test_real_case_preserves_t_junction_classification() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)
    layout = load_v33_layout(REAL_CASE_PATH)

    expected = {
        name
        for name, node in layout.nodes.items()
        if (node.type or "").strip() == "t_junction"
    }
    actual = {node.id for node in graph.nodes if node.kind == "t_junction"}
    assert actual == expected


def test_real_case_preserves_microstrip_connection_names() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)

    edge = graph.get_edge("IC1_pin1_seg1")
    assert edge.source == "IC1.PIN_1"
    assert edge.target == "IC1_pin1_seg1_universal_node"

    # IC1_pin1_seg3_to_C5 removed; IC1_pin1_seg2 now terminates at C7.PIN_1 directly
    branch_edge = graph.get_edge("IC1_pin1_seg2")
    assert branch_edge.source == "IC1_pin1_seg1_universal_node"
    assert branch_edge.target == "C7.PIN_1"


def test_real_case_resolves_every_edge_endpoint() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)

    resolved = {
        endpoint: graph.resolve_endpoint(endpoint)
        for edge in graph.edges
        for endpoint in (edge.source, edge.target)
    }

    assert resolved["C2.PIN_1"].entity_kind == "component"
    assert resolved["C2.PIN_1"].entity_id == "C2"
    assert resolved["C2.PIN_1"].pin_id == "PIN_1"

    assert resolved["IC1.PIN_1"].entity_kind == "terminal"
    assert resolved["IC1.PIN_1"].entity_id == "IC1.PIN_1"
    assert resolved["IC1.PIN_1"].pin_id is None

    assert resolved["IC1_pin1_seg1_universal_node"].entity_kind == "node"
    assert (
        resolved["IC1_pin1_seg1_universal_node"].entity_id
        == "IC1_pin1_seg1_universal_node"
    )
    assert resolved["IC1_pin1_seg1_universal_node"].pin_id is None


def test_resolve_endpoint_rejects_unknown_component_pin() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)

    with pytest.raises(KeyError, match="C2.PIN_999"):
        graph.resolve_endpoint("C2.PIN_999")


def test_extract_topology_graph_rejects_non_mapping_root() -> None:
    with pytest.raises(ValueError, match="mapping"):
        extract_topology_graph(["not", "a", "mapping"])


@pytest.mark.parametrize(
    ("connections", "match"),
    [
        (
            ["UNKNOWN_ENDPOINT", "junction"],
            "edge 'broken_edge' references unresolved endpoint 'UNKNOWN_ENDPOINT'",
        ),
        (
            ["ghost_end_split_pad", "junction"],
            "edge 'broken_edge' references unresolved endpoint 'ghost_end_split_pad'",
        ),
        (
            ["junction", "R2.PIN_999"],
            "edge 'broken_edge' references unresolved endpoint 'R2\\.PIN_999'",
        ),
    ],
)
def test_extract_topology_graph_rejects_edges_with_unresolved_endpoints(
    connections: list[str],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        extract_topology_graph(
            {
                "components": {
                    "R2": {
                        "placement": {"type": "fixed"},
                        "pin_nets": {"PIN_1": "net_a", "PIN_2": "net_b"},
                    }
                },
                "nodes": {"junction": {"type": "node"}},
                "terminals": {"RF_OUT": {"type": "terminal"}},
                "edges": {
                    "broken_edge": {
                        "type": "microstrip",
                        "connections": connections,
                    }
                },
            }
        )


def test_load_topology_graph_reads_yaml_as_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read_text = Path.read_text
    read_kwargs: dict[str, object] = {}

    def fake_read_text(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        read_kwargs["encoding"] = encoding
        return original_read_text(self, encoding="utf-8", errors=errors)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    load_topology_graph(REAL_CASE_PATH)

    assert read_kwargs["encoding"] == "utf-8"


@pytest.mark.parametrize("section_name", ["components", "nodes", "terminals", "edges"])
def test_extract_topology_graph_rejects_non_mapping_sections(section_name: str) -> None:
    with pytest.raises(
        ValueError, match=rf"section {section_name!r} must be a mapping"
    ):
        extract_topology_graph({section_name: ["not", "a", "mapping"]})


@pytest.mark.parametrize("section_name", ["components", "nodes", "terminals", "edges"])
def test_extract_topology_graph_rejects_non_mapping_entities(section_name: str) -> None:
    with pytest.raises(
        ValueError,
        match=rf"{section_name[:-1]} 'broken' must be a mapping",
    ):
        extract_topology_graph({section_name: {"broken": "not-a-mapping"}})


def test_extract_topology_graph_rejects_non_mapping_component_placement() -> None:
    with pytest.raises(
        ValueError, match=r"component 'broken' placement must be a mapping"
    ):
        extract_topology_graph(
            {
                "components": {
                    "broken": {
                        "placement": "fixed",
                        "pin_nets": {"PIN_1": "net_a"},
                    }
                }
            }
        )


def test_extract_topology_graph_rejects_non_mapping_component_pin_nets() -> None:
    with pytest.raises(
        ValueError, match=r"component 'broken' pin_nets must be a mapping"
    ):
        extract_topology_graph(
            {
                "components": {
                    "broken": {
                        "placement": {"type": "fixed"},
                        "pin_nets": ["PIN_1"],
                    }
                }
            }
        )
