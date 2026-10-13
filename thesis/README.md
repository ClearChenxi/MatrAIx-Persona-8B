# Thesis experiment

This directory contains the current Dunnhumby thesis experiment protocol,
audited prerequisites, and historical feasibility materials.

## Authoritative order

Use these documents in order:

1. [Research blueprint](experiment-blueprint/README.md): research questions,
   four evidence levels, experiment hierarchy, and conclusion boundaries.
2. [Execution manual](experiment-blueprint/实验执行手册.md): steps, inputs,
   outputs, gates, freeze order, and acceptance criteria.
3. [Review amendments](experiment-blueprint/REVIEW_FIXES.md): dated protocol
   corrections used together with the execution manual.
4. [E0a status](e0a/README.md), [E0b status](e0b/README.md), and
   [E1 data status](e1_data/README.md): completed evidence and current
   execution state.
5. **[E1a_v2 status](e1a_v2/README.md), [E1b_v2 status](e1b_v2/README.md),
   [E2_v2 status](e2_v2/README.md)**: current and future E1a / E1b / E2
   re-implementation, strictly isolated from the legacy `method_dev/`.

E1 is the parent experiment and does not produce a separate prediction
artifact. E1a covers predictive validity and comparison with traditional
methods; E1b covers the incremental value and mechanisms of Persona. The
fourth-level activity-person matching question remains deferred. E2 is a
planned application experiment testing whether a frozen P1 score adds value to
B3 under the same limited target-label budget; it does not create a fifth
research level.

## Current execution boundary

E0a, E0b, and the unified D/R/T inputs are complete. Formal T prediction
remains blocked at the time of writing.

**Active experiment directories**: `e0a/`, `e0b/`, `e1_data/`, `e1a_v2/`,
`e1b_v2/`, `e2_v2/`. The machine time-lock policy uses the canonical
`E1a / E1b / E2` names and rejects legacy `E1` as a prediction set.

## v2 restart — isolation from `method_dev/` (2026-10-13)

On 2026-10-13 the researcher initiated a full re-implementation of E1a / E1b
/ E2 because the legacy `thesis/method_dev/` directory implemented the LLM
methods (P1, L0, L1, L2, P2, P3) using raw `urllib.request` HTTP calls to
Ollama plus a single hand-written prompt string, while `e1a_freeze_manifest.md`
claimed the same methods used the unmodified MatrAIx `PersonaJsonSurvey` plus
`HostEnvironment`. That gap means the legacy predictions cannot enter the
thesis main result.

To prevent recurrence the directory layout was reorganized as follows:

- `e1a_v2/`, `e1b_v2/`, `e2_v2/` are the new self-contained experiment
  directories following the E0a / E0b naming rules (`<role>.py`,
  `*清单-<日期>.md`, `runs/<UTC>/`, etc.). They each ship their own
  `ISOLATION.md` and never import from `archive/method_dev/`.
- `archive/method_dev/` is the relocated legacy directory. It carries
  `DEPRECATED.md` and `ISOLATION.md`, and its contents must never be cited as
  evidence of current-paper results.
- The old `thesis/e1a_freeze_manifest.md` was moved into
  `thesis/e1a_v2/freeze-manifest.md` (renaming history preserved as a git
  rename). The freeze manifest still needs to be rewritten to align with the
  real PersonaJsonSurvey workflow.

The four baseline scripts (`run_baselines.py`, `run_logistic.py`,
`run_catboost.py`, `io_utils.py`) were copied verbatim from
`archive/method_dev/methods/` into `thesis/e1a_v2/methods/`, each with a
`MOVED FROM` header documenting the source path and the date of relocation.
Their `E1_RUN` path now resolves from the repository root, not from
`method_dev/`.

Audit command for ongoing compliance:

```bash
cd /Users/jiangchenxi/科研内容/代码仓库/MatrAIx-Persona-8B
grep -rn "method_dev" thesis/e1a_v2 thesis/e1b_v2 thesis/e2_v2 \
    --include="*.py" --include="*.md" \
    | grep -v "DEPRECATED" | grep -v "隔离" | grep -v "ISOLATION"
# Expected: empty
```

## Historical materials

[Feasibility materials](feasibility/README.md) preserve early dataset audits,
technical smoke tests, and the historical Hillstrom protocol. Their experiment
and method IDs are not valid for executing the current Dunnhumby experiment.

The [`archive/method_dev/`](archive/method_dev/DEPRECATED.md) directory holds
the relocated legacy LLM-method implementation from 2026-10-10 through
2026-10-13; see its `DEPRECATED.md` for the exact reason it was frozen.

Raw datasets, generated outputs, temporary files, caches, and local paper PDFs
are intentionally excluded from Git.
