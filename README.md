# 功放PCB单层自动布局布线算法

> 固定尺寸微带线 + 灵活走线 | 高密度 RLC shunt | 无交叉硬约束

---

## 总体架构

**迭代协同方案**（推荐）：

```
Placement (SA + 拥塞反哺) ⇄ Routing (CP-SAT)
         ↑ 反哺冲突约束    ↓ 报告拥塞
```

详见 [ALGORITHM-OVERVIEW.md](ALGORITHM-OVERVIEW.md)

---

## 为什么顺序Pipeline不行

| 缺陷 | 具体表现 |
|------|---------|
| HPWL 误导 | Placement 用 HPWL 估算，实际路由是 1.5~3 倍 |
| Infeasible | Placement 不知道 CP-SAT 约束强度，输出无解 |
| 锚定效应 | 固定微带线锚点无法调整 |
| 热拥塞脱节 | SA 热惩罚不考虑走线密度 |

**联合 CP-SAT**：约束耦合是非线性的，工程实现成本极高，不可行。

---

## 最优方案：迭代协同

```
┌─────────────────────────────────┐
│  迭代循环（最多5次）              │
│                                 │
│  Placement SA ──→ CP-SAT        │
│       ↑              ↓          │
│   热启动      成功 ──→ 输出      │
│       ↑              ↓          │
│   反哺惩罚   失败 ──→ 提取冲突   │
│                      ↓          │
│                  反哺 Placement  │
└─────────────────────────────────┘
```

**收敛条件**：连续 3 次 Routing 成功率 > 95%

---

## 能量函数

```
E = α·HPWL + β·C_cross + γ·C_boundary + δ·C_thermal + ε·C_congestion
                                           ↑
                                     新增：拥塞惩罚项
```

---

## 核心算法

### Stage 1: 布局（SA + 热启动）

- 模拟退火优化器件坐标
- **拥塞惩罚项**：来自上次 Routing 失败信息
- 热启动：每次从上次结果继续，收敛更快

### Stage 2: 布线（CP-SAT）

| 约束 | CP-SAT 原语 |
|------|------------|
| 无交叉 | `AddNoOverlap` + `AddBoolOr` |
| 路径连通 | `AddCircuit` |
| 目标长度 | `AddLinearExpression` |
| 过孔密度 | `AddCumulative` |
| 固定微带线 | `Var.SetValue(1)` |

### Stage 3: 后处理

- mitered 弯角补偿
- 泪滴（Teardrop）过渡
- DRC 检查

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
