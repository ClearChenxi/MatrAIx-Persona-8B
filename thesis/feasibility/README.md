# Persona-agent graduation-project feasibility

This workspace keeps source, schema, and technical feasibility notes for a
graduation project on generative user simulation.

## Authoritative experiment protocol

The current Dunnhumby experiment is defined by:

- [research blueprint](../experiment-blueprint/README.md),
- [execution manual](../experiment-blueprint/实验执行手册.md), and
- [review amendments](../experiment-blueprint/REVIEW_FIXES.md).

The current hierarchy has four research levels. E1 is the parent experiment;
E1a covers predictive validity and comparison with traditional methods, E1b
covers the incremental value and mechanisms of Persona, and the fourth-level
activity-person matching question remains deferred. E1 does not produce a
separate prediction artifact.

## Historical feasibility protocol

[EXPERIMENT_SPEC.md](EXPERIMENT_SPEC.md) is the 2026-10-08/09 historical
Hillstrom cold-start proposal. Its E1/E2/E3 and B/G method IDs are local to that
proposal and must not be used to execute the current Dunnhumby experiment.

## Confirmed research question

See `RESEARCH_QUESTION.md` for the earlier broad framing and its relationship to
the current four-level framework. The current dataset, outcome, comparison
protocol, and execution order are defined only by the authoritative documents
listed above.

Dataset facts are recorded in `DATASET_COMPARISON.md`,
`DATASET_SCHEMA_AUDIT.md`, `X5_DATASET_AUDIT.md`, and `DUNNHUMBY_AUDIT.md`.

## Data and prior tests

Raw datasets, local baselines, papers, and OpinionQA/local Qwen smoke tests are
retained as evidence of source availability or technical feasibility. These
historical tests are not results of the current Dunnhumby experiment.

Only source documents and the two small historical smoke-test scripts are
versioned here. Raw datasets, generated outputs, temporary files, caches, the
nested OpinionQA checkout, and local paper PDFs remain outside this repository.

Useful historical commands:

```bash
python3 pilot.py --limit 10
python3 local_persona_smoke.py --preset tiny
python3 local_persona_smoke.py --preset pilot
```

See `LOCAL_SMOKE_TEST.md` and `SMOKE_TEST_LOG.md` for their evidence boundaries.
