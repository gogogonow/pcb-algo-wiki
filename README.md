# 功放PCB单层自动布局布线算法

> 固定尺寸微带线 + 灵活走线 | 高密度 RLC shunt | 无交叉硬约束

---

## 总体架构

**三阶段流水线**：

```
布局 (模拟退火) → 布线 (CP-SAT) → 后处理 (mitered + DRC)
```

详见 [ALGORITHM-OVERVIEW.md](ALGORITHM-OVERVIEW.md)

---

## 核心算法

### Stage 1: 布局（Placement）

**模拟退火**优化器件坐标：

```
E = α·HPWL + β·C_cross + γ·C_boundary + δ·C_thermal
```

- 固定器件：功率管位置（不可移动）
- 灵活器件：匹配网络器件（电感、电容、电阻）
- 约束：边界、禁入区、最小间距

### Stage 2: 布线（Routing）

**CP-SAT 单一模型**，涵盖所有约束：

| 约束 | CP-SAT 原语 |
|------|------------|
| 无交叉 | `AddNoOverlap` + `AddBoolOr` |
| 路径连通 | `AddCircuit` |
| 目标长度 | `AddLinearExpression` |
| 过孔密度 | `AddCumulative` |
| 固定微带线 | `Var.SetValue(1)` 预布线 |

### Stage 3: 后处理

- 弯角 mitered 补偿
- 泪滴（Teardrop）过渡
- DRC 检查（最小线宽/间距/过孔）

---

## 文件结构

```
├── ALGORITHM-OVERVIEW.md          # 总体架构（推荐先读）
├── concepts/
│   ├── placement-problem-formulation.md   # 布局建模
│   ├── routing-algorithm-comparison.md    # 布线 CP-SAT 实现
│   └── microstrip-topology-matching.md   # 微带线拓扑 + mitered
└── README.md
```

---

## 复杂度

| 场景 | 器件数 | 灵活边数 | 求解时间 |
|------|--------|---------|---------|
| 简单 | < 10 | 3 | < 0.1s |
| 中等 | 10-50 | 10 | 0.5-2s |
| 复杂 | 50-200 | 30 | 10-60s |
| 超复杂 | > 200 | > 30 | 需区域分解 |
