# 微带线拓扑与匹配网络自动化综合（v6）

> ⚠️ 文档状态：**历史归档（v6）**。当前默认执行路径以 v2 Skeleton-First 为准。
>
> **v6 更新（基于真实案例 `rf_layout_simplified.yaml` 校准）**
>
> 本文档原 v4 内容（微带宽度计算、bend_style、stepped_impedance）**完全保留并继续适用**。v6 新增以下两处补充，请配合阅读：
>
> 1. **新增 `universal_junction`** 作为 v3.3 + v6 的"多分支 manifold 模板节点"。它在拓扑层是一个公共主干 + N 个带朝向和偏移的支路；在几何层等价于 N 个 stepped_impedance 节点的合成。每分支由 `{angle, origin: {offset_u, offset_v}}` 描述，求解器把它转化为线性几何约束注入 CP-SAT。详见 `ALGORITHM-OVERVIEW.md` §5。
> 2. **`bend_style` 容错**：v3.3 真实数据出现 v4 文档未列举的值（如 `curved`、`launch_rule: normal`）。v6 在 Postproc 阶段增加 fallback 表：未知 `bend_style` → `mitered_45` + warning 透传到 lint_report；`curved` 渲染为半径 ≥ 3W 的圆弧。
>
> ### v6 边几何模板（自 v3.3 universal_junction 推导）
>
> 给定 universal_junction 节点 N、其中心 `(cx, cy)` 与某条分支 b：
>
> $$\begin{aligned}
> branch\_end_x &= cx + \cos\theta \cdot du - \sin\theta \cdot dv \\
> branch\_end_y &= cy + \sin\theta \cdot du + \cos\theta \cdot dv
> \end{aligned}$$
>
> 其中 $\theta$ = `b.angle`（度）；$du$ = `b.origin.offset_u`；$dv$ = `signed_v(b.origin.offset_v)`：
>
> | `offset_v` 值 | $dv$ |
> |---|---|
> | `edge_left`    | $+W/2 + \text{clearance}$ |
> | `edge_right`   | $-W/2 - \text{clearance}$ |
> | `align_center` | $0$ |
>
> 真实案例的 `IC1_pin1_seg1_universal_node` 含 4 个分支，分别按上式生成 4 对 (x, y) 等式约束。
>
> ---

## 微带线宽度计算

给定基片参数（介电常数 $\varepsilon_r$、厚度 $h$）和工作频率 $f$，微带线特性阻抗：

$$Z_0 = \frac{60}{\sqrt{\varepsilon_e}} \ln\!\Big(\frac{8h}{W} + \frac{W}{4h}\Big),\quad W/h > 1$$

$$Z_0 = \frac{120\pi}{\sqrt{\varepsilon_e} \big[\frac{W}{h} + 1.393 + 0.667\ln\!\big(\frac{W}{h} + 1.444\big)\big]},\quad W/h \le 1$$

宽度 $W$ 由目标阻抗反算给出，本项目中宽度（YAML `constraint.width`）预先给定，不再优化。

---

## 微带拓扑类型

| 类型 | 适用场景 | 描述 |
|---|---|---|
| 直线型 | 短连接 | 最简单，插损最小 |
| U 形 | 需要延长线长 | 两端平行，弯角需 mitered 补偿 |
| L 形 | 90° 转向 | 占用面积小 |
| T 形 | 分支网络 | YAML 中通过 `t_junction` 节点表达 |
| 阶跃阻抗 | 宽—窄过渡 | YAML 中通过 `stepped_impedance` 节点表达，可带 `custom_offset` |

---

## 边几何：`bend_style`（v4 schema 新增）

每条 RF 边在 `geometry.bend_style` 字段声明拐角风格，由布线后处理器实施：

```yaml
edges:
  tl_rf_main:
    type: "microstrip"
    geometry: { bend_style: "mitered_45" }
```

| `bend_style` | 几何处理 |
|---|---|
| `square` | 直角，不补偿（仅低频或非射频路径用）|
| `mitered_45` | 45° 切角，按下式补偿 |
| `arc` | 圆弧过渡，半径 $R \ge 3W$ |

### Mitered 补偿公式

弯角切除宽度：

$$d = W \cdot \text{mitered\_factor} \cdot \sin(\theta/2)$$

其中 $\text{mitered\_factor}$ 通常取 $0.5 \sim 1.0$，$\theta$ 为转弯角度。补偿后传输线反射系数显著下降。

---

## 阶跃阻抗（`stepped_impedance` 节点）

用于宽窄微带过渡。YAML 节点声明：

```yaml
nodes:
  node_rf_step:
    type: "stepped_impedance"
    connections_rule:
      alignment_type: "custom_offset"
      offset_from_center: 0.25     # 偏移量（mm）或归一化比例
```

### 对齐模式

| `alignment_type` | 几何含义 |
|---|---|
| `centerline` | 宽窄段中心线对齐（默认对称跳变）|
| `edge` | 一侧边缘对齐（避免一侧应力集中）|
| `custom_offset` | 通过 `offset_from_center` 指定相对中心线的物理偏移 |

求解器在解 `stepped_impedance` 节点坐标时，会把偏移量作为线性约束注入 CP-SAT，使两侧子段的中心线按声明偏移对齐。

---

## 匹配网络自动化综合

### 传输线变换器

对于低损耗匹配，利用传输线段实现阻抗变换：

$$Z_{in} = Z_0 \frac{Z_L + jZ_0 \tan(\beta l)}{Z_0 + jZ_L \tan(\beta l)},\quad \beta = 2\pi/\lambda_g$$

### 拓扑分裂下的综合

v4 中"匹配电容 + 微带线段"组合通过**共享节点**自动表达：

```yaml
edges:
  tl_rf_up_p1: { connections: [..., node_rf_shunt_tap], target_length: 6.0 }
  c_rf_match:  { connections: [node_rf_shunt_tap, GND_REF] }   # 自动接地分支
  tl_rf_up_p2: { connections: [node_rf_shunt_tap, ...], target_length: 12.0 }
```

综合算法只需输出 `target_length` 与电容 `value_pF`，**位置由两段长度联立解出**——无需再写 `position_along_parent: 0.4` 这类与长度互相打架的字段。

### 遗传算法优化

1. 初始化随机拓扑和线长种群
2. 计算 S 参数（ADS 仿真或解析模型）
3. 适应度 $= 1 / (1 + \lvert S_{11}\rvert^2 + \lvert S_{22}\rvert^2)$
4. 选择、交叉、变异
5. 迭代至收敛

### 目标

自动化综合给定输入阻抗 $Z_{in}$ 到 $50\,\Omega$ 的匹配网络拓扑、微带线尺寸（`width` / `target_length`）以及阶跃阻抗的 `custom_offset`。
