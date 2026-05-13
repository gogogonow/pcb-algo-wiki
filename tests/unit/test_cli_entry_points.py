from importlib import import_module


def test_cli_entry_point_targets_are_importable() -> None:
    schema_check = import_module("tools.schema_check")
    topology_viz = import_module("tools.topology_viz")

    assert callable(schema_check.main)
    assert callable(topology_viz.main)
