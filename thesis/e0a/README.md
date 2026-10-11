# E0a 方法身份与接口检查

2026-10-10 当前状态：E0a兼容方法路径技术验收通过。三份交付物已完成；六次合成调用和活动29的20户×3次真实历史调用均通过核验。活动27不足样本的零调用失败记录保留。结论和限制见 [历史验收报告](历史验收报告-20261010.md)，执行经过见 [历史检查执行记录](历史检查执行记录-20261010.md)。本次不评价预测准确性，不替代E0b或正式E1冻结。

2026-10-11 协议衔接：当前[任务定义卡](任务定义卡.md)已将 E1 明确为父实验，E1a 检验预测效度与传统方法竞争力，E1b 检验 Persona 增量价值与机制；四个研究层次中的活动—人群匹配暂缓。此次更新不改变下述历史运行的身份、输入、调用次数或验收结论。下一步只能进行 E1a/E1b 的 D/R 方法开发和技术门槛检查，不能直接运行 T。

| 交付物 | 状态与入口 |
|---|---|
| 资料与版本索引 | [索引](资料与版本索引.md)、[18 份资料版本快照](materials.snapshot.json)已整理 |
| 方法身份记录 | [记录](方法身份记录.md)；本轮沿用审阅后的兼容方法名称 |
| 唯一主任务 | [任务定义卡](任务定义卡.md)已更新为 E1/E1a/E1b 层级；正式预测仍依赖 D/R 开发、机器合同改名和冻结 |
| 已有合成证据 | [验收报告](验收报告-20261010.md)；三户各两次，未使用真实标签 |
| 历史开发接口检查 | [历史验收报告](历史验收报告-20261010.md)、[方案](历史接口检查方案.md)、[配置](history-plan.json)；活动29、60次通过，1116文件独立核验 |
| 本轮准备验证 | [验证记录](准备验证记录-20261010.md)；零新增模型调用 |

在仓库根目录运行以下命令，只做合成测试、资料版本核验、方案展示和已有日志复核：

```sh
.venv/bin/python -m unittest discover -s thesis/e0a -p 'test_*.py' -v
.venv/bin/python thesis/e0a/inventory.py --check
.venv/bin/python thesis/e0a/history.py
.venv/bin/python thesis/e0a/run_history.py
.venv/bin/python thesis/e0a/verify.py thesis/e0a/runs/20261010T081210.118577Z
.venv/bin/python thesis/e0a/verify.py thesis/e0a/runs/history-20261010T092841.328585Z
```

历史方案为活动29、DAY197–280的投放前行为、固定哈希选20户、每户3次、已有本地Qwen后端，最多60次、首错停止、不重试、不读取兑换表或人口表。本轮确认已登记为 `execution_approved=true`；代码须提交并推送，再从干净分支启动。`--prepare-only` 会读取真实输入但不调用模型；`--execute` 会重新准备输入并执行该轮调用，不复用可修改的旧准备包。此次预算仅允许一轮60次；失败后保留证据并停止，不能为了通过而自动另起一轮。

已完成运行的复现入口（本轮60次预算已用尽；下列准备/生成命令不是再次调用授权，不自动重复运行）：

```sh
.venv/bin/python thesis/e0a/run_history.py --prepare-only
.venv/bin/python thesis/e0a/run_history.py --execute
.venv/bin/python thesis/e0a/verify.py thesis/e0a/runs/history-<timestamp>
```

活动27前轮已停止，不再执行其生成命令。其原配置已归档，只读复现失败原因：

```sh
.venv/bin/python thesis/e0a/diagnose_history.py --run thesis/e0a/runs/history-20261010T090938.875838Z --plan thesis/e0a/history-plan-campaign27-stopped.json
```

## 已完成合成检查与复现范围

Scope approved on 2026-10-10: three invented household behavior profiles, each repeated twice, using existing local Ollama `qwen3.5:9b`. No real records or labels, cloud calls, purchases or model downloads.

The unchanged official `PersonaJsonSurvey` 1.0.0 class and `HostEnvironment` run directly. The job scheduler and CLI reward verifier are not invoked. This establishes an open-source official-code execution path, not access to a hosted MatrAIx account/API or use of MatrAIx-Persona-8B model weights. The `openai/` prefix only selects the official OpenAI-compatible client; the actual backend is Ollama/Qwen.

The thesis adapter preserves exact persona/task messages, saves both API request formats and the raw native response, and maps the official client's temperature 0.7 to a fixed local temperature 0. This deliberate integration difference is recorded in the manifest. Thinking is disabled; seed, context (8192), output limit (512), top-k, top-p and presence penalty are explicit in config.json. The native Ollama model metadata and full digest are captured before and after execution. A fixed seed does not guarantee repeatability across hardware/runtime versions; observed repetition differences are reported rather than forced to zero.

The household profiles have behavior facts only. Unknown demographics and psychology are not added to inputs. Exact official prompts are compared with strict pre-rendered prompts before every generation. The check cannot establish that a model internally uses no stereotypes or pretrained knowledge.

The numeric endpoint is `p_redeem`, stored as a decimal string in the official free-text answer envelope to preserve zero through normalization. Raw JSON must have one answer, the correct instrument/question identities, and a valid probability in [0,1]. Missing, duplicate, confidence-only, truncated and malformed answers fail; there is no imputation or repair generation. At most one native generation is allowed per trial, six total; failures stop the run. Network auditing allows only loopback connections in the execution process. No .env is loaded and all proxy settings are removed.

The following is the reproduction sequence for the completed synthetic run, not authorization for additional calls. The six-call scope has already been used; agree a new scope before running the full generation command again. From the repository root:

```sh
uv sync --frozen
.venv/bin/python thesis/e0a/test_validation.py
.venv/bin/python thesis/e0a/run.py --preflight-only
git add thesis/e0a application/tasks/survey_thesis-e0a-synthetic
git commit -m "Add reproducible E0a synthetic official-agent check"
git push origin thesis-integration-3633d8d
.venv/bin/python thesis/e0a/run.py
.venv/bin/python thesis/e0a/verify.py thesis/e0a/runs/<timestamp>
```

Each full run must start from a clean committed tree. Runs use new timestamped local folders under ignored `thesis/e0a/runs/`: frozen config, generated persona YAML, manifest, dependency/runtime/model metadata, expected/actual prompts, upstream requests (without Authorization), native request/response bytes and headers, adapter response, official agent artifacts/events, strict parsing status, repetitions, failures and SHA-256 inventory. Local raw logs remain on disk; tracked reports include synthetic evidence, historical aggregate counts/distributions and artifact checksums, without real household identifiers or behavior details. Code/config versions are committed and pushed to the integration branch; the official fixed baseline is never moved.

Passing the six synthetic calls establishes only technical execution, input preservation, numeric output and logging for that configuration. The subsequent approved campaign-29 historical check passed all 60 calls and independent evidence verification. Together with the three deliverables, this completes E0a technical acceptance for the compatible-method path. It does not measure accuracy, validate probabilities or representative sampling, or authorize E1. Nineteen households returned zero and one returned 0.45 on all three repetitions; limited score discrimination is recorded in the historical report. E0b data/time/observation-completeness auditing remains the next stage.
