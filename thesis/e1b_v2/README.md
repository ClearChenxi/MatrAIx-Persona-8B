# e1b_v2 — Persona 增量价值与机制（v2 重做版）

**状态**：骨架已建立。**所有 5 个 LLM 方法（L0 / L1 / L2 / P1 / P2 / P3）和 1 个第二模型复核（P4）尚未实现**——必须用真 PersonaJsonSurvey workflow 接入。

**绝对禁止**：用裸 `urllib.request` + 手写 prompt 假装 PersonaJsonSurvey（这是 archive/method_dev 的根本问题）。

## 角色清单（E1b_v2）

| 方法 ID | 唯一有意差异 | 后端要求 |
|---|---|---|
| **L0** | 只接收活动 S；不含 X；不使用 Agent 工作流 | 真 PersonaJsonSurvey（system prompt 仅 S） |
| **L1** | 完整 X/S 的中性结构化事实 | 真 PersonaJsonSurvey（结构化文本） |
| **L2** | 与 P1 完全相同事实；Persona 文本；**不使用** Agent 工作流 | 真 PersonaJsonSurvey（Persona 文本渲染，但用 L2 特定的简化 envelope） |
| **P1** | 与 L2 相同事实；完整 Persona Agent | **真 PersonaJsonSurvey + HostEnvironment** |
| **P2** | 同活动另一家庭的整套 X（按 X 形成群体、按 seed 置换） | 真 PersonaJsonSurvey |
| **P3** | 只保留 R/F/M + S | 真 PersonaJsonSurvey |
| **P4** | L1/L2/P1/P2/P3 同输入换第二模型 | 待定（gemma 等候选，待冻结） |

## 任务书关键约束（[实验执行手册 §17](experiment-blueprint/实验执行手册.md)）

> 若实际渲染后 L2 与 P1 的模型消息完全相同，应合并两者并明确无法识别 Agent 工作流，而不是保留两个名称制造差异。

也就是说：v2 实现必须确保 L2 与 P1 的 `system` message 在 PersonaJsonSurvey 渲染后**真实不同**（区别在于 PersonaJsonSurvey 的 survey envelope 模板与 HostEnvironment 的多步工具调用）；否则合并后只能写"无法识别 Agent 工作流"。

## 调用规则（同 e1a_v2）

```bash
# 从仓库根运行；每个方法独立 <role>.py
.venv/bin/python thesis/e1b_v2/methods/run_l0.py
.venv/bin/python thesis/e1b_v2/methods/run_l1.py
.venv/bin/python thesis/e1b_v2/methods/run_l2.py
.venv/bin/python thesis/e1b_v2/methods/run_p1.py
.venv/bin/python thesis/e1b_v2/methods/run_p2.py
.venv/bin/python thesis/e1b_v2/methods/run_p3.py
# P4 待冻结第二模型后再写 run_p4_<model>.py
```

## 占位文件骨架

```
thesis/e1b_v2/
├── README.md                         # 本文件
├── .gitignore                        # 排除 runs/ + private/
├── ISOLATION.md                      # 引用 e1a_v2/ISOLATION.md
├── methods/
│   ├── __init__.py                   # 占位
│   ├── run_l0.py                     # ⏳ 待实现
│   ├── run_l1.py                     # ⏳ 待实现
│   ├── run_l2.py                     # ⏳ 待实现
│   ├── run_p1.py                     # ⏳ 待实现
│   ├── run_p2.py                     # ⏳ 待实现
│   ├── run_p3.py                     # ⏳ 待实现
│   └── workflow_common.py            # ⏳ PersonaJsonSurvey + HostEnvironment 共享调用逻辑
└── runs/                             # UTC-timestamped
```

## 上下游

- 上游：`thesis/e1a_v2/`（P1 共用 P1 的预测；L0/L1/L2 的差异化输入由 `run_*.py` 各自构造）
- 下游：`thesis/e2_v2/`（E2-2 加入冻结 P1 分数）
