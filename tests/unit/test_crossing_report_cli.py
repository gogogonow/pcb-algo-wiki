"""M8 CLI tests for crossing_report + pcb_solve crossing flags + serialisers."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from postproc.crossing_analysis import (
    AlgorithmicGap,
    CrossingReport,
    Hotspot,
    PairFact,
    R5,
)
from postproc.crossing_report_io import to_json, to_markdown


def _sample_report() -> CrossingReport:
    pf = PairFact(
        edge_a="EA",
        edge_b="EB",
        overlap_um=2500,
        overlap_area_um2=1234,
        nearest_distance_um=0,
        intersection_angle_deg=89.9,
        edge_a_class="rf_constrained_locked",
        edge_b_class="rf_constrained_locked",
        edge_a_length_mm=20.0,
        edge_b_length_mm=10.0,
        shared_endpoint=None,
        same_net=False,
        root_cause=R5,
        centre_x_mm=12.0,
        centre_y_mm=8.0,
    )
    gap = AlgorithmicGap(
        gap_id="GAP-CPSAT-NO-GEOM-FREEDOM",
        gap_title="CP-SAT 缺少 RF 锁长边的几何避让自由度",
        affected_pair_count=1,
        affected_fraction=1.0,
        evidence="R5 dominant",
        root_cause_layer="cpsat_routing",
        recommended_fix="Negotiated A*",
        estimated_pairs_eliminated=1,
        complexity="L",
        priority_score=0.125,
    )
    hot = Hotspot(bbox_mm=(0.0, 0.0, 20.0, 20.0), contained_pair_count=1)
    return CrossingReport(
        project="DemoProj",
        total_pairs=1,
        critical_pair_count=1,
        pair_facts=(pf,),
        root_cause_counts={R5: 1},
        region_hotspots=(hot,),
        gaps=(gap,),
    )


def test_to_json_roundtrip() -> None:
    rep = _sample_report()
    out = to_json(rep)
    parsed = json.loads(out)
    assert parsed["project"] == "DemoProj"
    assert parsed["total_pairs"] == 1
    assert parsed["critical_pair_count"] == 1
    assert parsed["root_cause_counts"]["R5_locked_locked_collision"] == 1
    assert parsed["gaps"][0]["gap_id"] == "GAP-CPSAT-NO-GEOM-FREEDOM"
    assert parsed["pair_facts"][0]["edge_a"] == "EA"
    assert parsed["region_hotspots"][0]["contained_pair_count"] == 1


def test_to_markdown_contains_sections() -> None:
    rep = _sample_report()
    md = to_markdown(rep)
    assert "走线交叉诊断报告 — DemoProj" in md
    assert "## 根因分布" in md
    assert "## 区域热点" in md
    assert "## 算法 GAP" in md
    assert "## 重叠对明细" in md
    assert "GAP-CPSAT-NO-GEOM-FREEDOM" in md
    assert "锁长冲突" in md  # zh translation present
    assert "EA" in md and "EB" in md


def test_to_markdown_empty_report_safe() -> None:
    empty = CrossingReport(
        project="Empty",
        total_pairs=0,
        critical_pair_count=0,
        pair_facts=(),
        root_cause_counts={},
        region_hotspots=(),
        gaps=(),
    )
    md = to_markdown(empty)
    assert "无重叠对" in md
    assert "无明显热点" in md
    assert "未触发任何 GAP 规则" in md


def test_crossing_report_cli_help() -> None:
    from tools.crossing_report import main as cli_main

    rc = -1
    try:
        cli_main(["--help"])
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 0
    assert rc == 0


def test_crossing_report_cli_missing_layout(tmp_path: Path, capsys) -> None:
    from tools.crossing_report import main as cli_main

    rc = cli_main([str(tmp_path / "nope.yaml"), "--json", str(tmp_path / "x.json")])
    assert rc == 2


def test_crossing_report_cli_no_outputs(tmp_path: Path) -> None:
    from tools.crossing_report import main as cli_main

    fake = tmp_path / "f.yaml"
    fake.write_text("metadata: {}\n", encoding="utf-8")
    rc = cli_main([str(fake)])
    assert rc == 2


def test_pairfact_dataclass_is_serialisable() -> None:
    rep = _sample_report()
    # asdict must not raise (no unbound types)
    asdict(rep.pair_facts[0])
    asdict(rep.gaps[0])
    asdict(rep.region_hotspots[0])
