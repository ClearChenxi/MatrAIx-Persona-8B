# E0a 方法身份与接口检查

2026-10-10 后续状态：六次虚构家庭调用已完成；资料索引、方法身份记录、任务定义卡及历史接口检查代码已补齐。18 项输入/解析/路径测试通过，18 份背景资料版本核验一致，已有六次调用的 116 个证据文件通过只读复核。真实家庭输入尚未提取，历史模型调用尚未执行，E0a 整体验收仍待完成。

| 交付物 | 状态与入口 |
|---|---|
| 资料与版本索引 | [索引](资料与版本索引.md)、[18 份资料版本快照](materials.snapshot.json)已整理 |
| 方法身份记录 | [记录](方法身份记录.md)已整理；保守论文名称待用户审核 |
| 唯一主任务 | [任务定义卡](任务定义卡.md)已整理；正式 E1 冻结仍依赖后续审计 |
| 已有合成证据 | [验收报告](验收报告-20261010.md)；三户各两次，未使用真实标签 |
| 历史开发接口检查 | [具体方案](历史接口检查方案.md)、[待确认配置](history-plan.json)与本地代码已准备；待确认后执行 |
| 本轮准备验证 | [验证记录](准备验证记录-20261010.md)；零新增模型调用 |

在仓库根目录运行以下命令，只做合成测试、资料版本核验、方案展示和已有日志复核：

```sh
.venv/bin/python -m unittest discover -s thesis/e0a -p 'test_*.py' -v
.venv/bin/python thesis/e0a/inventory.py --check
.venv/bin/python thesis/e0a/history.py
.venv/bin/python thesis/e0a/run_history.py
.venv/bin/python thesis/e0a/verify.py thesis/e0a/runs/20261010T081210.118577Z
```

历史方案为活动 27、DAY153–236 的投放前行为、固定哈希选三户、每户两次、已有本地 Qwen 后端，最多六次、首错停止、不重试、不读取兑换表或人口表。目前 `execution_approved=false`；`--prepare-only` 和 `--execute` 均拒绝未确认方案。收到针对本轮范围的确认后，才登记确认来源、提交并推送，再从干净分支启动。`--prepare-only` 会读取真实输入但不调用模型；`--execute` 会重新准备输入并执行该轮调用，不复用可修改的旧准备包。

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

Each full run must start from a clean committed tree. Runs use new timestamped local folders under ignored `thesis/e0a/runs/`: frozen config, generated persona YAML, manifest, dependency/runtime/model metadata, expected/actual prompts, upstream requests (without Authorization), native request/response bytes and headers, adapter response, official agent artifacts/events, strict parsing status, repetitions, failures and SHA-256 inventory. Local raw logs remain on disk; tracked reports may include synthetic-only aggregate evidence and artifact checksums. Code/config versions are committed and pushed to the integration branch; the official fixed baseline is never moved.

Passing six calls establishes only technical execution, input preservation, numeric output and logging for this configuration. It does not measure accuracy, establish predictive ordering, validate representative cohort sampling, or complete E0a. The material/version index, draft method-identity record and task-definition card are now prepared. Full E0a still requires method-name review and execution/verification of the agreed small historical-development interface check without test-label access.
