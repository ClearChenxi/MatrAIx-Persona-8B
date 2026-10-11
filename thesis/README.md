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

E1 is the parent experiment and does not produce a separate prediction
artifact. E1a covers predictive validity and comparison with traditional
methods; E1b covers the incremental value and mechanisms of Persona. The
fourth-level activity-person matching question remains deferred. E2 is a
planned application experiment testing whether a frozen P1 score adds value to
B3 under the same limited target-label budget; it does not create a fifth
research level.

## Current execution boundary

E0a, E0b, and the unified D/R/T inputs are complete. Formal T prediction is
still blocked. The machine time-lock policy retains legacy `E1/E1a/E2` names
until the executor updates them to `E1a/E1b/E2`, runs the regression tests,
freezes all D/R-developed methods, and registers the prediction contract.

## Historical materials

[Feasibility materials](feasibility/README.md) preserve early dataset audits,
technical smoke tests, and the historical Hillstrom protocol. Their experiment
and method IDs are not valid for executing the current Dunnhumby experiment.
Raw datasets, generated outputs, temporary files, caches, and local paper PDFs
are intentionally excluded from Git.
