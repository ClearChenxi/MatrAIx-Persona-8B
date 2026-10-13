# e1a_v2 — 预测效度与传统方法竞争力（v2 重做版）

**状态**：骨架已建立。基线 B1/B2/B3、N0/N1 已从 `archive/method_dev/methods/` 搬过来（旧 method_dev/ 整体已迁移到 archive/，从 git untracked，原因见 `archive/method_dev/DEPRECATED.md`）。P1 的真 PersonaJsonSurvey workflow 代码尚未实现——**严禁**用裸 `urllib.request` + 手写 prompt 实现 P1。

**目录规则**：本目录所有脚本从仓库根用 `thesis/e1a_v2/xxx.py` 调用；不跨 eX 共享脚本；命名严格遵循 E0 规范（`<role>.py` / `<日期>.md` / `runs/<UTC>/`）。

## 角色清单（E1a_v2）

| 方法 ID | 实现状态 | 后端 |
|---|---|---|
| **E1a-N0** | ✅ 已搬入 `methods/baselines.py` | 冻结 D 总体兑换率常数 |
| **E1a-N1** | ✅ 已搬入 `methods/baselines.py` | S-only Logistic（活动级） |
| **E1a-B1** | ✅ 已搬入 `methods/baselines.py` | RFM 业务规则排序 |
| **E1a-B2** | ✅ 已搬入 `methods/baseline_logistic.py` | sklearn Logistic C=0.1 |
| **E1a-B3** | ✅ 已搬入 `methods/baseline_catboost.py` | catboost depth=5, lr=0.1 |
| **E1a-P1** | ⏳ 占位，待 `methods/persona_workflow.py` 实现 | **真 PersonaJsonSurvey + HostEnvironment** |

## 占位文件骨架（待 P1 真 workflow 实现）

```
thesis/e1a_v2/
├── README.md                         # 本文件
├── .gitignore                        # 排除 runs/ + private/
├── freeze-manifest.md                # 旧 thesis/e1a_freeze_manifest.md 已迁入；待改写以对齐真 workflow
├── ISOLATION.md                      # 与 method_dev/ 的隔离规则（详细）
├── methods/
│   ├── __init__.py
│   ├── baselines.py                  # N0/N1/B1（已搬入）
│   ├── baseline_logistic.py          # B2（已搬入）
│   ├── baseline_catboost.py          # B3（已搬入）
│   ├── io_utils.py                   # load_split / E1_RUN（已搬入，路径已改相对仓库根）
│   └── persona_workflow.py           # ⏳ 待实现
└── runs/                             # UTC-timestamped，本地不入库
```

## 调用规则

```bash
# 从仓库根运行
.venv/bin/python thesis/e1a_v2/methods/baseline_catboost.py
.venv/bin/python thesis/e1a_v2/methods/baseline_logistic.py
.venv/bin/python thesis/e1a_v2/methods/baselines.py

# P1（待实现）必须用真 PersonaJsonSurvey ——
# 实现参考：thesis/e0a/run.py 的 run_trials() + preflight() 流程
# 严禁：import urllib.request / 手写 prompt / 裸 LLM 调用
```

## 上下游依赖

- 上游：`thesis/e1_data/runs/e1-data-20261011T042328.304949Z/`（已冻结，不变）
- 下游：`thesis/e1b_v2/`（同工作流）+ `thesis/e2_v2/`（适配器在 E1a/E1b 封存后跑）

## 隔离声明

本目录与 `archive/method_dev/` 之间的隔离规则见 [`ISOLATION.md`](ISOLATION.md)；不在 `archive/method_dev/` 的 run_d_prompt_compare.py / run_e1_T_predictions.py / run_e1a_dev.py 中寻找代码；不再用裸 LLM 调用伪装 PersonaJsonSurvey workflow。
