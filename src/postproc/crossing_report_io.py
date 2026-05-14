"""M8 crossing report serialisers — JSON + human-readable Markdown.

Pure formatting helpers around :class:`CrossingReport`; no I/O happens here.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from .crossing_analysis import (
    ROOT_CAUSE_DESCRIPTIONS_ZH,
    CrossingReport,
)


def to_json(report: CrossingReport, *, indent: int = 2) -> str:
    payload = {
        "project": report.project,
        "total_pairs": report.total_pairs,
        "critical_pair_count": report.critical_pair_count,
        "root_cause_counts": report.root_cause_counts,
        "region_hotspots": [
            {
                "bbox_mm": list(h.bbox_mm),
                "contained_pair_count": h.contained_pair_count,
            }
            for h in report.region_hotspots
        ],
        "gaps": [asdict(g) for g in report.gaps],
        "pair_facts": [asdict(f) for f in report.pair_facts],
    }
    return json.dumps(payload, indent=indent, ensure_ascii=False)


def to_markdown(report: CrossingReport) -> str:
    lines: list[str] = []
    lines.append(f"# 走线交叉诊断报告 — {report.project}")
    lines.append("")
    lines.append(
        f"- 总重叠对：**{report.total_pairs}**（critical >1mm: "
        f"**{report.critical_pair_count}**）"
    )
    lines.append("")

    # Root cause summary
    lines.append("## 根因分布")
    lines.append("")
    if report.root_cause_counts:
        lines.append("| 根因 | 中文描述 | 对数 | 占比 |")
        lines.append("|------|----------|------|------|")
        total = max(1, report.total_pairs)
        for code, count in sorted(
            report.root_cause_counts.items(), key=lambda kv: -kv[1]
        ):
            zh = ROOT_CAUSE_DESCRIPTIONS_ZH.get(code, "")
            lines.append(f"| {code} | {zh} | {count} | {count/total*100:.1f}% |")
    else:
        lines.append("_无重叠对_")
    lines.append("")

    # Hotspots
    lines.append("## 区域热点")
    lines.append("")
    if report.region_hotspots:
        lines.append("| # | bbox (mm) | 包含对数 |")
        lines.append("|---|-----------|----------|")
        for i, h in enumerate(report.region_hotspots, 1):
            x0, y0, x1, y1 = h.bbox_mm
            lines.append(
                f"| {i} | ({x0:.1f},{y0:.1f})–({x1:.1f},{y1:.1f}) | "
                f"{h.contained_pair_count} |"
            )
    else:
        lines.append("_无明显热点_")
    lines.append("")

    # GAPs
    lines.append("## 算法 GAP（按 priority 排序）")
    lines.append("")
    if report.gaps:
        for i, g in enumerate(report.gaps, 1):
            lines.append(f"### {i}. {g.gap_id} — {g.gap_title}")
            lines.append("")
            lines.append(
                f"- **影响**：{g.affected_pair_count} 对（{g.affected_fraction*100:.1f}%）"
            )
            lines.append(f"- **所在层**：`{g.root_cause_layer}`")
            lines.append(f"- **证据**：{g.evidence}")
            lines.append(f"- **建议修复**：{g.recommended_fix}")
            lines.append(
                f"- **预计消除**：{g.estimated_pairs_eliminated} 对 / "
                f"复杂度 **{g.complexity}** / priority_score "
                f"**{g.priority_score:.2f}**"
            )
            lines.append("")
    else:
        lines.append("_未触发任何 GAP 规则_")
    lines.append("")

    # Pair facts (top-N to keep MD readable)
    lines.append("## 重叠对明细（前 50 条）")
    lines.append("")
    if report.pair_facts:
        lines.append(
            "| edge_a | edge_b | overlap_µm | 角度° | a长 | b长 | 根因 | 共享端点 |"
        )
        lines.append(
            "|--------|--------|------------|-------|-----|-----|------|----------|"
        )
        for f in list(report.pair_facts)[:50]:
            lines.append(
                f"| {f.edge_a} | {f.edge_b} | {f.overlap_um} | "
                f"{f.intersection_angle_deg:.1f} | {f.edge_a_length_mm:.1f} | "
                f"{f.edge_b_length_mm:.1f} | {f.root_cause} | "
                f"{f.shared_endpoint or '-'} |"
            )
        if len(report.pair_facts) > 50:
            lines.append("")
            lines.append(f"_… 共 {len(report.pair_facts)} 条，其余见 JSON 报告_")
    else:
        lines.append("_无_")
    lines.append("")

    return "\n".join(lines)


__all__ = ["to_json", "to_markdown"]
