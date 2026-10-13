# archive/method_dev — 已被冻结的方法开发目录（2026-10-13）

> 本目录已经从 `thesis/method_dev/` 整体迁移而来。**论文主实验不再引用本目录的任何方法实现**。
> 留作历史审计，不应被任何 e1a_v2 / e1b_v2 / e2_v2 脚本调用。

## 冻结日期 / 触发原因

- **冻结日期**：2026-10-13
- **决策发起人**：研究者主动撤回
- **触发原因**：

  1. `thesis/method_dev/methods/run_e1_T_predictions.py` 中 E1a 的 P1 与 E1b 的 L0 / L1 / L2 / P2 / P3 全部用 `urllib.request` 直接 POST 到 `http://127.0.0.1:11434/api/chat` + 一行手写 `PROMPT_V2` / `PROMPT_L0_CAMPAIGN` / `PROMPT_L1_STRUCTURED` / `PROMPT_L2_PERSONA` / `PROMPT_P2_MISALIGNED` / `PROMPT_P3_MINIMAL`。
  2. 整个 `archive/method_dev/methods/` 目录下 `grep -rn 'PersonaJsonSurvey\|render_persona_template\|matraix.agents\|HostEnvironment'` **零匹配**——意味着完全未走官方 PersonaJsonSurvey 渲染管线。
  3. `thesis/e1a_freeze_manifest.md`（已迁入 `thesis/e1a_v2/freeze-manifest.md`）第 105-115 行 P1 声明 `Agent 实现：官方未修改的 PersonaJsonSurvey 1.0.0 + HostEnvironment，论文适配器` 与实际实现严重不一致。
  4. `archive/method_dev/methods/p1_calibration_summary.json` 的 OOF isotonic 校准使用了 drand 30 分钟短窗解封后的 T 标签（`n_total=1386, n_positives=121, positive_rate=0.0873`），违反"封存前不能读 T"硬约束。
  5. 任务书规定 drand 双阶段至少各 7 天保守审计基线，但 `timelock-20261013.md` 用的是 30 分钟短窗（adaption 02:30Z / final 03:00Z）。

## 不可反向引用的具体边界

以下文件 / 制品**不得**被 `thesis/e1a_v2/`、`thesis/e1b_v2/`、`thesis/e2_v2/` 引用或重制：

| 制品 | 不引用原因 |
|---|---|
| `archive/method_dev/methods/run_e1_T_predictions.py` | 全部方法走裸 `urllib`，不 import PersonaJsonSurvey |
| `archive/method_dev/methods/run_e1a_dev.py` | 含手写 prompt 选择逻辑（P1 v2 是按此选出，但实际跑 P1 时仍走裸 LLM） |
| `archive/method_dev/methods/run_r_v2_pilot.py` | R 区分度 check 用手写 prompt；不是 PersonaJsonSurvey 渲染 |
| `archive/method_dev/methods/run_d_prompt_compare.py` | 同上 |
| `archive/method_dev/methods/run_e2_limited_label.py` | 接收已污染的预测 jsonl |
| `archive/method_dev/methods/evaluate_e1a_e1b.py` | 输入 jsonl 已被污染 |
| `archive/method_dev/methods/evaluate_p1_cal.py` | OOF 校准读取解封后 T 标签 |
| `archive/method_dev/methods/launch_e1_T_detached.py`、`launch_r_v2_detached.py` | 启动上述方法，必须废弃 |
| `archive/method_dev/methods/seal_e1_predictions.py` | 封存包内容是裸 LLM 预测 |
| `archive/method_dev/methods/register_prediction_contract.py` | 早期合同生成器；新合同基于新接口 |
| `archive/method_dev/runs/e1a-e1b-T-20261012/private/*.jsonl` | 11 个 LLM 预测制品全部作废 |
| `archive/method_dev/runs/e1-evaluation-20261013/*` | 解封后评价产物全部作废 |
| `archive/method_dev/runs/d-prompt-compare-20261012/` | 手写 prompt v2 选 v1/v3 的对比结果，仅作审计保留 |
| `archive/method_dev/runs/dr-pilot-20261012/` | 160 次 D/R 技术试跑，技术门槛检查仍有效但**不**等同于 E1a 真 workflow |
| `archive/method_dev/methods/r_v2_pilot_results.json` | 同上 |

## 允许引用的部分

仅以下四个文件已搬家到 `thesis/e1a_v2/methods/`：

| 归档位置 | e1a_v2 当前位置 | 用途 |
|---|---|---|
| `archive/method_dev/methods/run_baselines.py` | `thesis/e1a_v2/methods/baselines.py` | N0 / N1 / B1 算法 |
| `archive/method_dev/methods/run_logistic.py` | `thesis/e1a_v2/methods/baseline_logistic.py` | B2 算法 |
| `archive/method_dev/methods/run_catboost.py` | `thesis/e1a_v2/methods/baseline_catboost.py` | B3 算法 |
| `archive/method_dev/methods/io_utils.py` | `thesis/e1a_v2/methods/io_utils.py` | `load_split` / `E1_RUN` 工具（路径已改相对仓库根） |

## 30 分钟短窗 timelock 的具体后果

`archive/method_dev/...` 中所有依赖 T 解封的制品（evaluate_*.py、p1_calibration_summary.json、e1a_e1b_summary.json）都属于"封存前读了 T 后生产"的产物，不能进论文主结果。

任务书（`thesis/experiment-blueprint/实验执行手册.md`）§22 + `thesis/e0b/公开时间锁封存规范.md` 规定：

- 第一轮次（解封适配 Y1）= 当前 drand 最新轮次 + **至少 7 天**
- 第二轮次（解封最终评价 Y1）= 第一轮次 + **至少 7 天**

正确重做必须重新走 drand 登记流程。详见 `thesis/timelock_runbook.md`。

## 审计方法

任何人执行以下命令应得到零结果（不允许新代码反向引用 archive/method_dev/）：

```bash
cd /Users/jiangchenxi/科研内容/代码仓库/MatrAIx-Persona-8B
grep -rn "archive/method_dev\|thesis/method_dev" \
    thesis/e1a_v2 thesis/e1b_v2 thesis/e2_v2 \
    --include="*.py" --include="*.md" \
    | grep -v "DEPRECATED" | grep -v "隔离"
# 期望：无
```

如果非空，意味着有人误引用了已冻结的方法实现，按"立即删除 / 撤回对应 commit"处理。

## 历史与命名空间

`archive/method_dev/` 仍是历史事实。如果后续论文要把"method_dev 阶段"作为附录复盘 PR，可以从 git 历史中通过 `git log --all --follow archive/method_dev/` 检索；当前 commit 起 `archive/method_dev/` 不再变动。

如果未来需要在 `archive/method_dev/` 内做事（例如一份新附录讨论 P1 的退化现象），必须在 archive 内部独立 git checkout / 分支，不污染主分支。
