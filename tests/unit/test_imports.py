from topology import TopologyEdge, TopologyGraph, TopologyNode, TopologyPoint


def test_topology_package_exports_basic_types() -> None:
    graph = TopologyGraph()
    node = TopologyNode(id="n1", kind="junction", position=TopologyPoint(1.0, 2.0))
    edge = TopologyEdge(id="e1", source="n1", target="n2")

    graph.nodes.append(node)
    graph.edges.append(edge)

    assert graph.nodes[0].id == "n1"
    assert graph.edges[0].target == "n2"
