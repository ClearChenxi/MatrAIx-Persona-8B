# Thesis experiment restart, 2026-10-14

This branch starts at the fixed official MatrAIx commit
`3633d8dab149a9482a71b024418a49ae828cc941`. The active protocol is
`experiment-blueprint/README.md`, `实验执行手册.md`, and `REVIEW_FIXES.md`.

The E0a and E0b implementation files were copied from the previously verified
integration branch at `1e8778143c1979bcf12651fac440360656b85873`. Their
earlier run outputs, approval flags, prediction contracts, timelock policy,
model results, and freeze manifests were **not** adopted as restart evidence.
The E1 data builder was copied as source code only; its plan points to a
pending new E0b run. New executions require a clean, pushed code commit and
their own timestamped run directory and verification report.

The earlier integration branch remains intact for provenance. Its archived
method development record reports that the old evaluation process opened T
labels and fitted a calibration model using them. A newly trained model can be
restricted to D for fitting and R for calibration, but re-running the same T
cannot be described as the first unseen test. The prior access must appear in
the eventual methods and limitations. No old T predictions or labels are
permitted as input to this branch's model development.

Current state: **pre-execution preparation**. E0a, E0b, E1 data preparation,
method development, model calls, timelock sealing, T predictions, and E2 have
not been executed on this branch. The imported plan files deliberately have
`execution_approved: false` and must be reviewed and changed by a later,
separately backed-up commit before any real-data run.
