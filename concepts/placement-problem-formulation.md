# 布局问题数学建模（v6）

> ⚠️ 文档状态：**历史归档（v6）**。当前主线布局放置实现以 v2 阶段编排与现网参数为准。
>
> **v6 更新（基于真实案例 `rf_layout_simplified.yaml` 校准）**
>
> 本文档原 v4 内容描述"`floating_shunt_tap` 节点 + 引力场"模型，仍作为内部 IR 兼容路径保留。在 v3.3 真实数据流下，主要的"待放置变量"由两类**新对象**承担，请把下文的 $V_{float}$ 概念一并扩展为下表中的 v6 三类：
>
> | v6 类别 | 来源 | SA 处理 | CP-SAT 处理 |
> |---|---|---|---|
> | **UV 器件** (`components.placement.type = parametric_uv`) | v3.3 D2 决策 | 沿宿主 microstrip 等距初值 + Metropolis 微调 anchor | (anchor_x, anchor_y, rotation, offset_v_side) 进入 IntVar/BoolVar |
> | **`universal_junction` 中心** | v3.3 D4 决策 | SA 不直接放置（由 CP-SAT 几何模板决定）| (cx, cy) IntVar，按 branches[] 派生线性约束 |
> | **`floating_shunt_tap` 节点**（v4 兼容路径）| v4 文档 | 引力场 SA（本文 §"引力场模型"）| 不直接进 CP-SAT 几何变量 |
>
> 真实案例 `PA_Module_Simplified` 不触发 `floating_shunt_tap`；触发 9 个 UV 器件 + 2 个 universal_junction。
>
> ---
>
> **v6 SA 能量函数（替代下文 §"v4 能量函数"）**
>
> $$E_{v6} = \alpha \cdot HPWL + \gamma \cdot C_{boundary} + \delta \cdot C_{thermal} + \zeta \cdot E_{uv\_anchor} + \zeta' \cdot E_{attract}^{v4}$$
>
> 其中 $E_{uv\_anchor}$ 把每个 UV 器件的 anchor_pin 拉向其 host_edge 主方向并对超出 $[0, host\_length]$ 的位置做铰链惩罚；$E_{attract}^{v4}$ 仅当输入仍含 `floating_shunt_tap` 节点时生效（向后兼容）。
>
> ---

## 基本定义

给定电路网表 $N = (T, V, E)$，其中：

- $T$：**Terminals** — 物理锚点（绝对坐标已知，不可移动）
- $V = V_{flex} \cup V_{float}$：**逻辑节点**
  - $V_{flex}$：普通分叉/吸附节点，坐标由邻接 RF 边的 `target_length` 联立解出
  - $V_{float}$：`floating_shunt_tap` 节点，坐标由**引力场 SA** 优化
- $E$：广义边（微带线 / 普通走线 / 集总器件），宽度 $w_e$ 预先给定

**布局目标**：为每个 $v \in V_{float}$ 求一个二维坐标，使总能量最小，同时不破坏所有硬约束。

---

## v4 能量函数

$$
E_{v4} \;=\; \alpha \cdot HPWL \;+\; \gamma \cdot C_{boundary} \;+\; \delta \cdot C_{thermal} \;+\; \zeta \cdot E_{attract}
$$

| 项 | 含义 |
|---|---|
| $HPWL = \sum_{e \in E}\big(\lvert x_u - x_v\rvert + \lvert y_u - y_v\rvert\big)$ | 半周长线长 |
| $C_{boundary}$ | 器件超出板框的程度（铰链函数）|
| $C_{thermal}$ | 大功率器件聚集惩罚（散热感知）|
| $\bm{E_{attract}}$（**v4 新增**）| 浮动器件引力场能量项 |

> **与 v3 的差别**：v3 已经把无交叉 / 拥塞 / 接地拥塞等硬约束移交 CP-SAT，能量函数仅含 $\alpha\,HPWL + \gamma\,C_{boundary} + \delta\,C_{thermal}$。v4 在此基础上**增加 $\zeta \cdot E_{attract}$**，专门处理浮动器件的位置偏好。

---

## 引力场模型（$E_{attract}$）

$E_{attract}$ 仅对节点类型为 `floating_shunt_tap` 的节点生效，根据其 `placement_objective.strategy` 取不同形式：

### 1. `attract_to_target` — 吸附到目标管脚

$$
E_{attract}^{(node)} = w \cdot \big[(x_{node} - x_{target})^2 + (y_{node} - y_{target})^2\big]
$$

- $w$ 即 YAML 中的 `weight`；`weight: 100.0` 这种极高权重会把器件**强力推挤到目标管脚邻位**（典型用于旁路电容紧贴 VDD pin）。
- 量纲：距离平方，与 HPWL 的线性距离不同——通过 $\zeta$ 平衡。

### 2. `space_available` — 寻找空白区域

$$
E_{attract}^{(node)} = \sum_{u \in \text{occupied}(r)} \frac{1}{\lVert p_{node} - p_u \rVert^2 + \epsilon}
$$

- 在以 $r$ 为半径的局部窗口内累加"已占用单元"的反比距离势能。
- 越靠近其它器件能量越高 → SA 自动把节点推向空地（典型用于体电容 $C_{bulk}$）。

---

## 约束条件

### 硬约束（由 CP-SAT / 几何求解器强制保证）

| 约束 | 描述 |
|---|---|
| 边界约束 | 所有节点中心必须在板框内 |
| Terminal 不可动 | $t \in T$ 坐标固定 |
| 禁止区域 | 指定坐标区域不可放置器件 |
| 无交叉 | 所有 RF 边两两不重叠（CP-SAT `AddNoOverlap`）|
| 过孔密度上限 | `AddCumulative`，限制单位面积接地过孔数 |

### 软约束（写入 SA 能量函数）

| 约束 | 体现 |
|---|---|
| 最小线长 | $\alpha \cdot HPWL$ |
| 板框紧凑 | $\gamma \cdot C_{boundary}$ |
| 散热分散 | $\delta \cdot C_{thermal}$ |
| 浮动器件位置偏好 | $\zeta \cdot E_{attract}$ ⭐ v4 新增 |

---

## SA 调度

模拟退火只对 $V_{float}$ 节点扰动，按 Metropolis 准则接受新解：

$$
P(\text{accept}) = \min\!\Big\{1,\; \exp\big(-\Delta E_{v4} / T\big)\Big\}
$$

CP-SAT 求解失败的轮次会**抬高初始温度**（不是调权重 $\alpha,\gamma,\delta,\zeta$）：

```python
def adaptive_temperature(routing_success_rate, T):
    if routing_success_rate < 0.5: return T * 1.2
    if routing_success_rate > 0.9: return T * 0.95
    return T
```

> **为什么调温度而不是调权重**：权重决定"想要什么样的解"，温度决定"愿意接受多差的中间解"。求解失败说明陷入局部最优，应该重新搜索（升温）而不是改变目标。
