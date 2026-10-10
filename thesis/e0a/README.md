# E0a first local synthetic check

Scope approved on 2026-10-10: three invented household behavior profiles, each repeated twice, using existing local Ollama `qwen3.5:9b`. No real records or labels, cloud calls, purchases or model downloads.

The unchanged official `PersonaJsonSurvey` 1.0.0 class and `HostEnvironment` run directly. The job scheduler and CLI reward verifier are not invoked. This establishes an open-source official-code execution path, not access to a hosted MatrAIx account/API or use of MatrAIx-Persona-8B model weights. The `openai/` prefix only selects the official OpenAI-compatible client; the actual backend is Ollama/Qwen.

The thesis adapter preserves exact persona/task messages, saves both API request formats and the raw native response, and maps the official client's temperature 0.7 to a fixed local temperature 0. This deliberate integration difference is recorded in the manifest. Thinking is disabled; seed, context (8192), output limit (512), top-k, top-p and presence penalty are explicit in config.json. The native Ollama model metadata and full digest are captured before and after execution. A fixed seed does not guarantee repeatability across hardware/runtime versions; observed repetition differences are reported rather than forced to zero.

The household profiles have behavior facts only. Unknown demographics and psychology are not added to inputs. Exact official prompts are compared with strict pre-rendered prompts before every generation. The check cannot establish that a model internally uses no stereotypes or pretrained knowledge.

The numeric endpoint is `p_redeem`, stored as a decimal string in the official free-text answer envelope to preserve zero through normalization. Raw JSON must have one answer, the correct instrument/question identities, and a valid probability in [0,1]. Missing, duplicate, confidence-only, truncated and malformed answers fail; there is no imputation or repair generation. At most one native generation is allowed per trial, six total; failures stop the run. Network auditing allows only loopback connections in the execution process. No .env is loaded and all proxy settings are removed.

From the repository root:

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

Passing six calls establishes only technical execution, input preservation, numeric output and logging for this configuration. It does not measure accuracy, establish predictive ordering, validate representative cohort sampling, or complete E0a. Full E0a still requires the material/version index, final method-identity record, task-definition card, and an agreed small historical-development interface check without test-label access.
