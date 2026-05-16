"""WI-F4: UV placements must not overlap each other's rendered footprint bbox.

The previous logic computed obstacles from raw pad min/max which is
degenerate for 0402-style RLCs (both pads share y → bbox height ≈ 0 →
adjacent UVs on the same trace overlap visually after rendering).
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _bbox_for(anchor_x, anchor_y, rot_deg, footprint):
    fw, fh = footprint
    rot_q = int(round(float(rot_deg or 0.0))) % 180
    bw, bh = (fw, fh) if rot_q == 0 else (fh, fw)
    return (
        anchor_x - bw / 2.0,
        anchor_y - bh / 2.0,
        anchor_x + bw / 2.0,
        anchor_y + bh / 2.0,
    )


def _intersect(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def test_no_pairwise_uv_footprint_overlap_in_simplified(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "src.tools.pcb_solve_v2",
            str(REPO / "rf_layout_simplified.yaml"),
            "--out-dir",
            str(out_dir),
        ],
        check=True,
        cwd=str(REPO),
    )

    import json

    phaseB = json.loads((out_dir / "PA_Module_Simplified.phaseB.json").read_text())

    # All RLCs in this case use PKG_CAP (local_x ±0.685, local_y 0).
    # _footprint_size → (max(1.37, 1.0)+0.6, max(0, 1.0)+0.6) = (1.97, 1.6)
    footprint = (1.97, 1.6)

    bboxes = []
    for name, p in phaseB["placements"].items():
        # only RLCs (single-letter prefix C/R/L) are UVs in this case
        if not name or name[0] not in ("C", "R", "L"):
            continue
        bboxes.append(
            (
                name,
                _bbox_for(p["anchor_x"], p["anchor_y"], p["rotation_deg"], footprint),
            )
        )

    overlaps = []
    for i in range(len(bboxes)):
        for j in range(i + 1, len(bboxes)):
            ni, bi = bboxes[i]
            nj, bj = bboxes[j]
            if _intersect(bi, bj):
                overlaps.append((ni, bi, nj, bj))
    assert not overlaps, "UV footprint overlaps detected: " + "; ".join(
        f"{a}={ba} vs {b}={bb}" for a, ba, b, bb in overlaps
    )
