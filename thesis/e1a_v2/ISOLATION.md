# e1a_v2 / e1b_v2 / e2_v2 与 archive/method_dev/ 的隔离规则

日期：2026-10-13。建立日期对应 `archive/method_dev/DEPRECATED.md` 的同日归档动作。

## 背景

2026-10-10 至 2026-10-13，仓库内 `thesis/method_dev/` 被用作：
- **E1a/E1b 主实验的 LLM 方法（P1 / L0 / L1 / L2 / P2 / P3）实施位置**——但实际实现是裸 `urllib.request` + 手写 prompt，**没有 import PersonaJsonSurvey / render_persona_template / HostEnvironment**。
- **E1a/E1b freeze manifest 与实现严重不一致**——`thesis/e1a_freeze_manifest.md` 第 105-115 行声称 P1 = "官方未修改的 PersonaJsonSurvey + HostEnvironment"，但 method_dev/ 内的所有 LLM 调用都绕过官方 Agent。
- **drand 时间锁 30 分钟短窗封存**（在 `timelock-20261013.md`）让 T 标签在封存当日暴露；method_dev/ 的 `p1_calibration_summary.json` 在解封后用 T 标签做了 OOF isotonic 校准，违反"封存前不能读 T"硬约束。

结论：`thesis/method_dev/` 的全部 LLM 相关制品不能进入论文主结果。

## 隔离规则

**新实验目录（e1a_v2 / e1b_v2 / e2_v2）从不引用 archive/method_dev/ 的方法实现**：

| 不允许从 e1a_v2 / e1b_v2 / e2_v2 做 | 原因 |
|---|---|
| `from archive.method_dev...` 或相对路径 | 已 untracked，不在 sys.path |
| 复制 `archive/method_dev/methods/run_e1_T_predictions.py` | 那是裸 LLM 调用，必须丢弃 |
| 复制 `archive/method_dev/methods/evaluate_e1a_e1b.py` | 输入的 jsonl 已被污染，必须废弃 |
| 复制 `archive/method_dev/methods/run_d_prompt_compare.py` | 手写 prompt；新实现走 PersonaJsonSurvey 渲染 |
| 复制 `archive/method_dev/methods/seal_e1_predictions.py` | 封存的是裸 LLM 预测 |
| 复用 `p1_calibration_summary.json` 或 `e1a_e1b_summary.json` | T 标签已暴露 |
| 把"30 分钟短窗 timelock"作为合规承诺 | 任务书规定至少 7 天双阶段 |

**允许引用**（基线与数据加载）：

| 来源 | 用于 |
|---|---|
| `archive/method_dev/methods/run_baselines.py` | E1a-N0/N1/B1 算法 — 内容已搬入 `thesis/e1a_v2/methods/baselines.py` |
| `archive/method_dev/methods/run_logistic.py` | E1a-B2 算法 — 已搬入 `thesis/e1a_v2/methods/baseline_logistic.py` |
| `archive/method_dev/methods/run_catboost.py` | E1a-B3 算法 — 已搬入 `thesis/e1a_v2/methods/baseline_catboost.py` |
| `archive/method_dev/methods/io_utils.py` | E1_RUN / load_split 工具 — 已搬入 `thesis/e1a_v2/methods/io_utils.py`，路径已改为相对仓库根 |

## 路径统一

新目录的 Python 脚本导入 `io_utils` 必须是相对仓库根的相对路径或包内导入：

```python
from thesis.e1a_v2.methods.io_utils import load_split, E1_RUN
```

而不是：

```python
sys.path.insert(0, "/Users/jiangchenxi/.../thesis/method_dev/methods")
from io_utils import load_split  # 拒绝
```

搬过来的 `baselines.py` / `baseline_logistic.py` / `baseline_catboost.py` / `io_utils.py` 顶部都加上了 `# MOVED FROM` 头部；`methods/__init__.py` 显式声明只导出基线方法学。

## 审计

任何人执行以下命令应得到零结果（不允许新代码反向引用 archive/method_dev/）：

```bash
cd /Users/jiangchenxi/科研内容/代码仓库/MatrAIx-Persona-8B
grep -rn "method_dev" thesis/e1a_v2 thesis/e1b_v2 thesis/e2_v2 \
    --include="*.py" --include="*.md" \
    | grep -v "^Binary" \
    | grep -v "MOVED FROM" \
    | grep -v "隔离" \
    | grep -v "ISOLATION" \
    | grep -v "DEPRECATED"
# 期望输出：无
```

如果非空，说明有未授权引用，按"立即删除 / 撤回对应 commit"处理。
