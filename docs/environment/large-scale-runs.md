# Large-scale runs

Run a Playground evaluation over many personas. The result is still **one job**:
one name in Runs, one folder `jobs/<job_name>/`.

First single-persona walkthrough: [quickstart](../quickstart.md).

---

## Launch

Pick one surface. They all write the same `jobs/<job_name>/` tree.

| Surface | Use when |
|---------|----------|
| **Playground** | Interactive launch and inspection |
| **`POST /api/harbor/jobs`** | Automation |
| **`matraix run -c <job.yaml>`** | CLI that matches Playground (local, Modal, or GCP) |

A thousand-persona batch on this machine is the same generate + `matraix run -c`
path as the [quickstart](../quickstart.md) (no `--compute-family`; default
`local`). For Modal (or GKE):

```bash
uv run python application/scripts/generate_application_job.py \
  --task application/tasks/example-survey_product-feedback \
  --sample-size 1000 \
  --n-concurrent-trials 32 \
  --compute-family modal

uv run --extra modal matraix run --compute-family modal \
  -c configs/jobs/application-task-job-recipe/<generated>.yaml
```

`--compute-family modal|gcp` on the generator records the family in the sidecar.
`matraix run -c` then goes through HarborJobService (same as Playground).
Override with `matraix run -c <job.yaml> --compute-family local` to force this
machine.

One launch covers the whole cohort. Do not start a separate job per persona.
Give the job a **new** `job_name` if you re-run the same cohort — do not reuse
an empty or failed `jobs/<name>/`.

### Compute family

`plane` is who starts the job. `computeFamily` is where trials run.

```bash
# unset / local = this machine (default)
export MATRIX_COMPUTE_FAMILY=modal   # remote; or gcp when daily concurrency saturates
```

Or pass `"computeFamily"` on `POST /api/harbor/jobs`. Omit it to stay `local`.

| | Survey / chat | Web | Linux CUA / `force_docker` | macOS / iOS |
|--|---------------|-----|----------------------------|-------------|
| `local` | This machine | Docker on this machine | Docker on this machine | use.computer |
| `modal` | Modal Function | Harbor `type: modal` (Direct) | Docker-in-sandbox worker | use.computer |
| `gcp` | GKE host worker | Harbor `type: gke` | Harbor `type: gke` | use.computer |

macOS / iOS always use use.computer, even when `computeFamily` is `modal` or
`gcp`. Set `MATRIX_COMPUTE_FAMILY` or pass `computeFamily` on launch.

**Web on Modal (Direct).** This is the 1000-wide web path. Harbor on this
machine is the orchestrator; each persona is a Modal Sandbox. The recipe
writes `environment.type: modal` and **no** `dispatch`. One Harbor process
owns the whole job so every trial shares one `Image.from_dockerfile` (first
sandbox builds; the rest reuse). Keep `matraix run` up until Harbor and host
self-report finish — closing the laptop stops the orchestrator. You need a
Modal token on this machine (`modal setup`) and `uv run --extra modal`. You
do **not** deploy `modal_host_app` for this path.

Set Parallel / `--n-concurrent-trials` to the cohort size when you want all
sandboxes in flight. Persist copies `/app/output` while the sandbox is still
alive. If the task ships `input/self_report_schema.yaml`, HarborJobService
writes `user_feedback.json` on the host after each trial (and again at
job-end for anything the watcher missed). Leave interpreter and browser
paths out of `instruction.md`; the shared Playwright image already has them.

Smoke two personas on the **same** image and cohort before opening 1000.

**Survey / chat on Modal.** Modal Function workers. Deploy
`modal_host_app` once ([runtime.md](runtime.md)). The laptop can close after
spawn; restart the API to reattach. Chat still needs a sidecar URL Modal can
reach (`MATRIX_CHATBOT_PUBLIC_URL` or `MATRIX_CHATBOT_TUNNEL=auto`).

**`force_docker` / Linux CUA on Modal.** Still a Playground Docker sandbox
(`dispatch=modal_jobs`). That path is for CUA and explicit Docker, not the
default web cohort.

**GKE.** Web stays Harbor `type: gke` and **does** shard. Switch web here
when daily Modal concurrency stays saturated:
`MATRIX_COMPUTE_FAMILY=gcp` or `"computeFamily": "gcp"`.

`nConcurrentTrials` (Playground **Parallel**) is how many trials run at once.
Survey/chat pack up to `MATRIX_HOST_PACK_CONCURRENCY` (default `32`)
processes per Function / GKE host worker.
`MATRIX_SHARD_CONCURRENCY` (default `8`) is the max GKE / survey-chat
workers — it does not fan out Harbor `type: modal`.
`MATRIX_WEB_PACK_CONCURRENCY` applies to GKE web / docker-in-sandbox
workers, not Direct. `MATRIX_MAX_CONCURRENT_TRIALS` optionally caps Parallel.

Survey/chat on Modal Functions and GKE host workers publish **status** every
few seconds so the mosaic ticks; artifacts flush in batches (Modal) or when
the pod packs the tree (GKE). Direct web writes `jobs/<job_name>/` on this
machine as each sandbox finishes.

Optional knobs: `MATRIX_HOST_PACK_CONCURRENCY`, `MATRIX_SHARD_CONCURRENCY`,
`MATRIX_MAX_CONCURRENT_TRIALS`, `MATRIX_MODAL_LIVE_PUSH_SEC`,
`MATRIX_MODAL_LIVE_PULL_SEC`, `MATRIX_MODAL_ARTIFACT_FLUSH_TRIALS`,
`MATRIX_MODAL_ARTIFACT_FLUSH_SEC` ([runtime.md](runtime.md)).

---

## Personas

A **cohort** for launch is a directory of persona YAML files (or a pool path
plus `useEntirePool`).

| Batch size | How to pass them |
|------------|------------------|
| Small (about ≤100) | Optional `personaIds` |
| Large | `personaPool` = that directory, plus `useEntirePool` |

Sources:

1. **Task strategy** — most tasks ship `persona_strategy.json`. Sample in
   Playground, or run
   `generate_application_job.py --task application/tasks/<name>`.
   For a stratified sample, set ``sampling.allocation`` to ``perCell`` /
   ``proportional`` / ``equalTotal``. Per-cell uses ``sampling.perCell``;
   the other two use ``sampling.sampleSize`` as the total.
2. **Public Persona 1M** —
   [`MatrAIx2026/MatrAIx_Persona_1M_Public_Release`](https://huggingface.co/datasets/MatrAIx2026/MatrAIx_Persona_1M_Public_Release).
   Import it locally, then **Pull cohort** in Playground (writes a launch cache
   under `matraix-persona-1m/cohorts/`). See
   [Persona setup](../persona/README.md#setup-and-usage).
3. **Dev sample** — `persona/datasets/matraix-persona-dev-sample/` for small
   local batches.
4. **Saved dataset** — after a pull, **Save as dataset…** copies the YAML into
   `persona/datasets/<name>/` for reuse. Playground defaults sampling to **All**.

Path taxonomy (picker vs on-disk caches):  
[Playground pools & cohorts](../persona/README.md#playground-pools--cohorts).

Playground / API fields: `personaPool`, `useEntirePool`, `sampleSize`,
`nConcurrentTrials`. Reference: [playground-api.md](../application/playground-api.md).

Record the persona path (and the Hugging Face revision, if you used one) so the
batch is reproducible.

---

## Outputs

```text
jobs/<job_name>/
├── result.json
├── <task>__<trial>/
│   ├── agent/
│   ├── artifacts/app/output/   # task JSON; user_feedback.json if the task has a schema
│   ├── verifier/
│   └── result.json
└── job.log
```

Harbor finishing  N/N  is not the same as host self-report finishing. Wait for
`matraix run: status=completed` (or the Playground job to leave `running`)
before treating `user_feedback.json` as missing.

Keep `jobs/<job_name>/` and any sampled cohort directory if you need to
reproduce the run. Pulled launch caches sit next to the source dataset, for
example:

- `persona/datasets/matraix-persona-1m/cohorts/cohort-<digest>/`
- `persona/datasets/matraix-persona-dev-sample/cohorts/cohort-<digest>/`

Those caches (and `generated-persona-dev-*`, `saved-cohorts/`) are gitignored.

---

## Related

- [Runtime](runtime.md)
- [Quickstart](../quickstart.md)
- [Persona 1M](../persona/README.md#public-coreset-matraix-persona-1m)
- [Playground API](../application/playground-api.md)
