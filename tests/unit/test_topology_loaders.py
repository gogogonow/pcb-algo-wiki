from pathlib import Path

import pytest

from topology.loaders import extract_topology_graph, load_topology_graph

REAL_CASE_PATH = Path(__file__).resolve().parents[2] / "rf_layout_simplified.yaml"


def test_real_case_extracts_expected_entity_counts() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)

    assert len(graph.fixed_components) == 6
    assert len(graph.parametric_uv_components) == 9
    assert len(graph.nodes) == 11
    assert len(graph.terminals) == 10
    assert len(graph.edges) == 22


def test_real_case_preserves_named_entities_and_classification() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)

    assert graph.get_component("IC1").placement_kind == "fixed"
    assert graph.get_component("C1").placement_kind == "parametric_uv"
    assert graph.get_component("R2").placement_kind == "parametric_uv"
    assert graph.get_component("TP1").placement_kind == "fixed"

    assert graph.get_node("IC1_pin1_seg1_universal_node").kind == "universal_junction"
    assert graph.get_node("IC1_pin1_seg2_end_split_pad").kind == "t_junction"
    assert graph.get_node("IC1_pin1_seg5_start_combiner").kind == "t_combiner_junction"

    assert graph.get_edge("IC1_pin1_seg1").kind == "microstrip"


def test_real_case_preserves_t_junction_classification() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)

    assert {node.id for node in graph.nodes if node.kind == "t_junction"} == {
        "IC1_pin1_seg2_end_split_pad",
        "IC1_pin1_seg3_end_split_pad",
        "IC1_pin1_seg4_end_split_pad",
        "IC1_pin2_seg2_end_split_pad",
        "IC1_pin2_seg3_end_split_pad",
    }


def test_real_case_preserves_microstrip_connection_names() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)

    edge = graph.get_edge("IC1_pin1_seg1")
    assert edge.source == "IC1.PIN_1"
    assert edge.target == "IC1_pin1_seg1_universal_node"

    branch_edge = graph.get_edge("IC1_pin1_seg2_to_R2")
    assert branch_edge.source == "IC1_pin1_seg2_end_split_pad"
    assert branch_edge.target == "R2.PIN_2"


def test_real_case_resolves_every_edge_endpoint() -> None:
    graph = load_topology_graph(REAL_CASE_PATH)

    resolved = {
        endpoint: graph.resolve_endpoint(endpoint)
        for edge in graph.edges
        for endpoint in (edge.source, edge.target)
    }

    assert resolved["R2.PIN_2"].entity_kind == "component"
    assert resolved["R2.PIN_2"].entity_id == "R2"
    assert resolved["R2.PIN_2"].pin_id == "PIN_2"

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

    with pytest.raises(KeyError, match="R2.PIN_999"):
        graph.resolve_endpoint("R2.PIN_999")


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
