# 迭代计划与状态（2026-05 刷新版）

> 本文档只保留当前有效路线与近期任务。  
> 历史 M0–M10 长篇记录已下沉到 `docs/superpowers/{plans,specs}` 与各 `concepts/*` 文档中。

---

## 1. 当前基线

- 默认入口：`pcb_solve` / `pcb_solve_v2`
- 主线架构：PreA + Phase A/B/C（Skeleton-First）
- 回归案例：`rf_layout_simplified.yaml`
- 质量门：`./scripts/verify_m1.sh`

---

## 2. 已完成能力（主线）

1. v3.3 YAML 输入链路（schema_check + frontend_compile）
2. 拓扑可视化基线（topology_viz）
3. Skeleton-First 三阶段框架（PreA/PhaseA/PhaseB/PhaseC）
4. 浮动器件布局与 flexible_path 路由联动
5. 分阶段 SVG/JSON 输出与 summary 聚合
6. 直连端点语义适配（删除冗余 `*_split_pad/*_combiner` 依赖）

---

## 3. 近期优先级

### P0：Phase C 质量提升

- 目标：减少 `flex_failed`，降低交叉与重叠；
- 手段：持续优化 floating placer 代价函数与 pin-side 偏好。

### P1：文档与对接稳定性

- 保持 README / ALGORITHM / RUNBOOK 与默认实现一致；
- 所有 CLI 示例与输出统计按当前案例实时更新。

### P2：历史链路治理

- 将 CP-SAT 路线彻底标记为历史对照；
- 保留最小可运行入口以支撑回归比对。

---

## 4. 验收口径

每次主干合入需满足：

1. `./scripts/verify_m1.sh` 通过；
2. `rf_layout_simplified.yaml` 可由 `pcb_solve_v2` 端到端运行；
3. 文档中的主命令、主流程、主输出与当前实现一致。

---

## 5. 运维建议

- 把 `out/PA_Module_Simplified.summary.json` 作为阶段结果的首要检查点；
- 发现“文档与实现不一致”时，优先修文档并在同次提交中完成；
- 对历史实验链路（v1/cpsat）只做必要维护，不扩展新功能。

