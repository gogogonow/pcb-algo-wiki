# Gerber / GDS 输出字段映射（v7 留实现）

> M6 仅提供 `output/gerber_stub.py` 与 `output/gds_stub.py` 占位，
> 调用即抛 `NotImplementedError("v7")`。本文档锁定 GeometryIR → 二进制
> 输出格式的字段映射，便于 v7 直接落地。

## 1. GeometryIR → Gerber RS-274X

| GeometryIR 字段 | Gerber 概念 | 备注 |
|---|---|---|
| `board.{width,height}` | 文件 header 的 image size | mm，坐标系左下原点 |
| `placements[c].pads[*].point` | aperture flash (D03) | 用 ADD aperture C/R 定义 pad shape |
| `routes[e].points` | draw move (D02/D01) | 多段折线，linewidth=`route.width` |
| `routes[e].width` | aperture C 直径 | 每个不同 width 注册一个 D-code |
| `routes[e].routing_class` | layer 选择 | rf_* → top copper；power_* → 单独 power 层 |
| `nodes[*]` | (无对应) | junction 仅几何辅助，不出现在 Gerber 上 |

输出文件：
- `<project>.GTL`（top copper）
- `<project>.GTO`（top silkscreen — 来自 placements 的 component 名）
- `<project>.TXT`（drill — 来自 via，PA 单层无）

## 2. GeometryIR → GDSII

| GeometryIR 字段 | GDS 元素 | layer 约定 |
|---|---|---|
| `board.{width,height}` | top-level cell BOUNDARY | layer=255, datatype=0 |
| `placements[c]` | SREF 引用 component cell | layer=10 |
| `placements[c].pads[*]` | BOUNDARY (rect) | layer=11 |
| `routes[e].points` | PATH（width=`route.width*1000`，单位 nm） | rf_* → layer=20；power_* → layer=21 |
| 文字标签 | TEXT | layer=63 |

dbu = 1nm（与 ExactFloat mm 相乘 1e6 取整）。

## 3. 实施清单（v7 任务拆分）

1. `output/gerber.py`：替换 stub，依赖 `pygerber` 或自研 D-code writer
2. `output/gds.py`：替换 stub，依赖 `gdspy` 或 `gdstk`
3. `tests/integration/test_gerber_roundtrip.py`：用 `pygerber` 解析回读校验
4. `tests/integration/test_gds_roundtrip.py`：用 `gdstk` 读回校验 polygon 数量
5. `pcb_solve` 增 `--gerber-out DIR`、`--gds-out FILE`
