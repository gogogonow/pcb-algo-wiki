# M7 GAP修复设计文档

> ⚠️ 历史归档文档：该规格用于当时迭代记录，当前主线请以 `README.md` / `ALGORITHM-OVERVIEW.md` / `ITERATION-PLAN.md` 为准。

**日期**: 2026-05-14  
**迭代**: M7  
**触发**: 基于 rf_layout_simplified.yaml 端到端运行结果的GAP分析  
**范围**: 前端Lint加固 + 蛇形走线 + DRC几何升级 + YAML数据修复  

---

## 1. 问题背景

M6完成后首次对真实PA参考板运行全流程，发现4类关键GAP：

| ID | 现象 | 根因 |
|----|------|------|
| G1 | `RF_INPUT_to_IC1` 长度约束被静默跳过（target=30mm < Manhattan=70.78mm）| 前端未检测不可行约束；目标长度错误 |
| G2 | `IC1.PIN_3` 同时出现在 RF_INPUT 和 PWR_VDD 两个 net 中，前端无报错 | Lint缺少多网引脚冲突规则 |
| G3 | 50个重叠对，DRC使用BBox近似，大量误报（对角线段bbox扩展导致误判）| DRC clearance检测基于矩形包围盒而非实际线段 |
| G4 | 蛇形走线能力缺失；当target > 直线路径时无法自动插入U型弯以补足长度 | postproc层未实现meander模块 |

---

## 2. 架构设计

### 2.1 前端Lint增强（G1+G2）

**文件**: `src/frontend/lint.py`

新增两个lint函数，在 `compile_layout()` 后执行：

#### Rule: `pin_multi_net`（Error级）

```
对于每条边 e 的每个端点 "comp.PIN_N"：
  找到该端点在所有边中出现的 net 集合
  若 |nets| > 1 → LintError(code="pin_multi_net", ...)
```

**修复YAML**: 将 `IC1.pin_nets.PIN_3: PWR_VDD` 改为 `RF_INPUT`，  
将 `PWR_VDD_bus` 改为连接 `TP2.PIN_1 → IC1.PIN_4`（power bus到测试点）

#### Rule: `length_infeasible_short`（Error级）

```
对于有 target_length 的边：
  若 两个端点都是 fixed 组件 → 计算Manhattan距离
  若 target_length < Manhattan - tolerance（0.5mm容差）:
    → LintError(code="length_infeasible_short", ...)
```

#### Rule: `meander_required`（Warning级）

```
对于有 target_length 的边：
  若 target_length > Manhattan + tolerance:
    → LintWarning(code="meander_required", 
                  message="target exceeds direct path; meander will be applied")
```

**修复YAML**: `RF_INPUT_to_IC1` target_length: 30.0 → 80.0（> 70.78mm Manhattan，触发meander）  
**修复YAML**: `PWR_VDD_bus` target_length: 5.0 → 6.5（> 5.76mm Manhattan）

### 2.2 蛇形走线模块（G4）

**文件**: `src/postproc/meander.py`

```python
def apply_meanders(
    geom: GeometryIR,
    ir: V6IR,
    *,
    min_loop_spacing: float = 0.5,   # mm between adjacent loops
) -> tuple[GeometryIR, MeanderReport]:
    ...
```

**算法（U形蛇形）**:

```
对于每条2点路径（start, end），若 route的 routing_class 含 target_length：
  actual_len = Euclidean(start, end)
  excess = target_length - actual_len
  若 excess <= tol: 跳过（route已足够长）
  
  base_dir = normalize(end - start)
  perp_dir = base_dir.perpendicular()  # 90度旋转
  
  # 步骤1：估算单个U环的初始高度（用路径长度的1/5，至少2×线宽）
  loop_height_est = max(actual_len / 5.0, route.width * 2 + min_loop_spacing)
  # 步骤2：计算所需环数
  n_loops = ceil(excess / (2 * loop_height_est))
  # 步骤3：反算精确loop_height，使 n_loops × 2H = excess（精确补偿）
  loop_height = excess / (2 * n_loops)
  
  # 均匀分布在路径中段，避开两端pin区域（留20%空间）
  生成折线点序列：直行→转弯→U型往返×n→直行
  返回修改后的 RoutePolyline（多点）
```

**与pcb_solve集成**:
- 新增 `--meander/--no-meander`（default: ON，当bend=ON时）
- 在 `apply_bends()` 之后调用 `apply_meanders()`
- `MeanderReport` 包含 `meandered_edges, skipped_edges, warnings`

### 2.3 DRC几何升级（G3）

**文件**: `src/postproc/drc.py`  

替换 `_inflate_bbox` + bbox重叠检测，改用 **线段-线段最短距离**：

```python
def _seg_seg_min_dist(
    p1: Point, p2: Point,   # segment A
    p3: Point, p4: Point,   # segment B
) -> float:
    """最短线段间距（含端点情形），单位mm。"""
    # 采用参数化投影 + 端点检测的标准算法
    ...
```

**新的clearance检测逻辑**:
```
对于路径 ra（多点折线）的每个线段 seg_a：
  对于路径 rb 的每个线段 seg_b：
    dist = _seg_seg_min_dist(seg_a, seg_b)
    half_width_sum = (ra.width + rb.width) / 2 + clearance
    若 dist < half_width_sum:
      → 违规（不再是bbox误报）
```

**预期效果**: PA板的50个bbox-waivered违规中，对角线段的误报大量减少，真实间距不足的才报警。

### 2.4 YAML数据修复

| 字段 | 原值 | 修改值 | 原因 |
|------|------|--------|------|
| `IC1.pin_nets.PIN_3` | `PWR_VDD` | `RF_INPUT` | 与 RF_INPUT_to_IC1 边的 net 对齐 |
| `PWR_VDD_bus.connections` | `[IC1.PIN_3, IC1.PIN_4]` | `[TP2.PIN_1, IC1.PIN_4]` | 电源总线应从测试点到IC1电源引脚 |
| `RF_INPUT_to_IC1.constraint.target_length` | `30.0` | `80.0` | 使约束可行（>70.78mm）；触发meander演示 |
| `PWR_VDD_bus.constraint.target_length` | `5.0` | `6.5` | 使约束可行（>5.76mm） |

---

## 3. 接口定义

### MeanderReport
```python
@dataclass
class MeanderReport:
    meandered_edges: list[str]  # 实际插入meander的边
    skipped_edges: list[str]    # actual >= target，跳过
    warnings: list[str]         # 空间不足等警告
    
    @property
    def applied_count(self) -> int: ...
```

### 新LintWarning codes
- `pin_multi_net`: 同一pin出现在多个不同net的边中
- `length_infeasible_short`: target_length < 固定端点间Manhattan距离
- `meander_required`: target_length > Manhattan距离，将触发蛇形走线

---

## 4. 测试计划

| 测试文件 | 测试内容 |
|----------|----------|
| `tests/unit/test_lint_feasibility.py` | pin_multi_net / length_infeasible_short / meander_required |
| `tests/unit/test_meander.py` | apply_meanders：excess=0跳过、U型长度精度±1%、边界案例（极短excess、多圈） |
| `tests/unit/test_drc_seg_dist.py` | _seg_seg_min_dist：平行/垂直/对角/端点重合；regression vs bbox |

---

## 5. 交付物

1. `src/frontend/lint.py` — 新增3条lint规则
2. `src/postproc/meander.py` — 新模块
3. `src/postproc/drc.py` — clearance检测升级为线段几何
4. `src/tools/pcb_solve.py` — 新增 `--meander/--no-meander`
5. `rf_layout_simplified.yaml` — 数据修复
6. `tests/unit/test_lint_feasibility.py`
7. `tests/unit/test_meander.py`
8. `tests/unit/test_drc_seg_dist.py`
9. `scripts/verify_m7.sh`
10. `ITERATION-PLAN.md §M7`

---

## 6. DoD（完成定义）

- [ ] `rf_layout_simplified.yaml` 跑通后lint报告：0 error，meander_required warning出现
- [ ] `apply_meanders` 使 RF_INPUT_to_IC1 实际走线长度达到 80mm ± 1%
- [ ] DRC 使用真实线段几何后，50条warning中的对角线误报显著减少（目标 < 20条）
- [ ] 所有新单元测试通过
- [ ] `./scripts/verify_m7.sh` 全绿
- [ ] `no_overlap_pass` 或 DRC warning数下降可见
