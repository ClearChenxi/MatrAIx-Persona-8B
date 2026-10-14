# Persona 1M-to-Dunnhumby matchability audit

This phase tests only whether predeclared Persona 1M attributes can be
defensibly paired with the already frozen Dunnhumby household X/S fields.
It does not assign a persona to a household, read any Persona 1M person row,
open T features or outcomes, call a model, or change P1. D/R feature presence
counts say only whether a proposed *proxy* can be computed; they do not
validate the proxy or establish that the Persona 1M attribute is true for a
Dunnhumby household.

The code requires the three small official release metadata files at the
dataset revision and SHA-256 values recorded in `plan.json`. Download only
`README.md`, `persona_codes.schema.json`, and `audit.json` from that pinned
Hugging Face dataset revision into a local ignored `source/` directory. Then:

```bash
.venv/bin/python -m thesis.persona1m_audit.audit \
  --metadata-dir thesis/persona1m_audit/source
```

The gate requires the audit code and plan to be clean and already pushed to
`origin/thesis-restart-20261014`. The local `runs/` report records the exact
code commit and input hashes. Original Persona 1M rows are intentionally not
downloaded, and no original or derived persona data is uploaded to GitHub.

The dataset card marks the release as non-commercial research-only and notes
that it is not a representative population sample. A separate design and
license review is required before using any persona records in a household-
level prediction condition.

The next, independently authorized phase audits the *complete* pinned release
without joining any Dunnhumby table. Download the ten Parquet shards listed
in the pinned official `manifest.json` into ignored `source/data/`; check
that each shard's size, SHA-256 and row count match. Only after this code and
the updated `plan.json` are clean and pushed, run:

```bash
.venv/bin/python -m thesis.persona1m_audit.coverage
```

This streams source type, packed attributes, missing bitmap, and overrides for
the five predeclared candidate attributes. It reports only source-level counts
and known category counts, plus pairwise/three-field/four-field shared coverage
for the four shopping-related attributes. It does not export any individual persona record,
read household data or labels, or call a model. Attribute coverage is a
feasibility measure, **not** evidence of household-persona matching quality.
For the agreed shopping-style/frugality pair it also counts records where both
values decode to known codebook categories, and reports only aggregate joint
category counts. Exact per-row overrides are not emitted or treated as known
categories for candidate weighting.
