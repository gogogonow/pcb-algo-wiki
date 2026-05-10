# 布局问题数学建模

## 基本定义

给定电路网表 $N = (V, E)$，其中：
- $V$：器件集合（包含功率管 $V_{fix}$ 和匹配网络器件 $V_{flex}$）
- $E$：连接关系（微带线宽度 $w_e$ 预先给定）

布局目标：将每个器件 $v_i \in V_{flex}$ 放置在二维坐标系中，最小化总成本。

## 目标函数

$$F_{placement} = \alpha \cdot HPWL + \beta \cdot C_{cross} + \gamma \cdot C_{boundary}$$

其中：

- **HPWL（半周长线长）**：$\sum_{e \in E} (|x_{u} - x_{v}| + |y_{u} - y_{v}|)$
- **交叉惩罚** $C_{cross}$：微带线交叉次数
- **边界惩罚** $C_{boundary}$：器件超出板框的程度

## 约束条件

### 硬约束

| 约束 | 描述 |
|------|------|
| 边界约束 | 所有器件中心必须在板框内 |
| 固定器件 | $v \in V_{fix}$ 位置不可移动 |
| 禁止区域 | 指定坐标区域不可放置器件 |

### 软约束

| 约束 | 描述 |
|------|------|
| 最小间距 | 相邻器件间保持最小电气间距 |
| 热感知 | 大功率器件分散布局 |

## 能量函数（模拟退火）

$$E = \alpha \cdot HPWL + \beta \cdot \sum_{e_1, e_2 \in E, e_1 \neq e_2} \mathbb{1}_{cross(e_1, e_2)} + \gamma \cdot \sum_{v \in V} penalty_{boundary}(v)$$

模拟退火通过随机扰动器件位置，评估能量变化，按 Metropolis 准则接受/拒绝新解。
