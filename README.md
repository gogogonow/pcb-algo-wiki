# 功放PCB单层自动布局布线算法 v2

> 固定尺寸微带线 + **固定路由边** + **大量并联RLC接地** | 高密度 RLC | 无交叉硬约束

---

## 总体架构

**迭代协同 v2（增强版）**：

```
Placement SA（增强能量函数） ⇄ Routing CP-SAT（增强约束）
         ↑ 反哺冲突约束            ↓ 报告拥塞
```

详见 [ALGORITHM-OVERVIEW.md](ALGORITHM-OVERVIEW.md)

---

## 场景扩展

| 边类型 | CP-SAT 处理 |
|--------|------------|
| `fixed_microstrip` | `Var.SetValue(1)`，不参与求解 |
| `fixed_route` | 转为 `occupied_cells` 集合，灵活边禁止进入 |
| `flexible` | 正常 CP-SAT 求解 |
| `ground_branch` | 与灵活走线同等处理，**含 NoOverlap** |
| `via_to_ground` | 计入 `AddCumulative` 容量约束 |

---

## 能量函数（Placement SA）

```
E = α·HPWL + β·C_cross + γ·C_boundary + δ·C_thermal + ε·C_congestion + ζ·C_ground_congestion
                                                                                ↑
                                                                          新增：接地拥塞
```

---

## 迭代协同 v2 关键增强

| 增强项 | 实现方式 |
|--------|---------|
| 固定路由边（预布线） | `occupied_cells` 集合，灵活边 `SetValue(0)` 禁止进入 |
| 接地分支 | 与灵活走线同等处理，`AddNoOverlap` 含信号走线 |
| 星型接地板 | 独立zone，`AddCumulative(via_vars, capacity=N)` |
| 接地拥塞反哺 | `C_ground_congestion` 加入能量函数 |
| 自适应区域分解 | 探测拥塞热点 → 自动细分该区域 |

---

## 典型Doherty功放量化

| 参数 | 典型值 |
|------|--------|
| RLC shunt总数 | 23-42 个 |
| 接地过孔 | ~40 个汇聚到 GND 区域 |
| 每子区域平均接地孔（4×4分解） | ~2.5 个 |
| 变量规模 | ~10⁶ |
| CP-SAT求解时间 | 30s内 |

---

## 文件结构

```
├── ALGORITHM-OVERVIEW.md          # 总体架构 v2（推荐先读）
├── concepts/
│   ├── placement-problem-formulation.md   # 布局建模
│   ├── routing-algorithm-comparison.md    # 布线 CP-SAT 实现
│   └── microstrip-topology-matching.md   # 微带线拓扑 + mitered
└── README.md
```
